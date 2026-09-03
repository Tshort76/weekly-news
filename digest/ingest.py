"""Fetch the feeds. The only network edge before the model calls.

feedparser has no timeout of its own, so the bytes are fetched here and parsed
from memory. One dead feed warns and is skipped; every feed dead aborts.
"""

from __future__ import annotations

import logging
import re
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import certifi
import feedparser

from .config import Config
from .models import Item, Source
from .normalize import item_id, canonical_url

log = logging.getLogger("digest.ingest")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 20

# A python.org interpreter on macOS ships no root certificates of its own, so
# every https fetch fails with CERTIFICATE_VERIFY_FAILED until one is supplied.
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class AllFeedsFailed(RuntimeError):
    pass


def fetch_bytes(url: str, timeout: int = TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
        return resp.read()


def _published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def _blurb(entry) -> str:
    for key in ("summary", "description", "subtitle"):
        value = entry.get(key)
        if value:
            return value
    content = entry.get("content") or []
    return content[0].get("value", "") if content else ""


# A newsletter or podcast trailer, which a news feed carries alongside the news.
# The title is the giveaway on some, the blurb on the rest.
PROMOTIONAL_TITLE = re.compile(
    r"\bnewsletter\b|\bDispatch:|^\s*FirstFT\b|\bis hiring\b|^The Economist asks\b",
    re.IGNORECASE,
)

# A blurb that introduces the writer instead of the event: "Gregg Carlstrom, our
# Middle East correspondent, on the reasons for the recent skirmishes". Every
# word of that is about who is talking, and nothing in it says what happened.
# "our" and the seniority words are load-bearing. A bare article would also
# match "the CEO is going after a Variety reporter", which is a story about a
# journalist rather than a trailer written by one.
BYLINE_BLURB = re.compile(
    r"\b(our|chief|senior|executive|deputy)\s+(\w+\s+){0,2}"
    r"(correspondent|editor|columnist|reporter)\b",
    re.IGNORECASE,
)


def is_promotional(title: str, blurb: str) -> bool:
    """A trailer for journalism rather than the journalism.

    These are the one input no prompt can survive. Handed "our Middle East
    correspondent, on the reasons for the recent skirmishes", a writer asked
    for what changed and why has been given a subject and no facts, and the
    fluent thing to do is supply some — gemma3 answered this exact item with a
    Red Sea shipping story assembled out of nothing. Dropping it here also
    keeps it out of the classifier, which was spending judgements on roughly a
    dozen of these a week.
    """
    return bool(PROMOTIONAL_TITLE.search(title) or BYLINE_BLURB.search(blurb))


def fetch_source(source: Source, cutoff: datetime) -> list[Item]:
    raw = fetch_bytes(source.url)
    parsed = feedparser.parse(raw)
    items: list[Item] = []
    undated = 0
    promotional = 0
    for entry in parsed.entries:
        url = entry.get("link") or ""
        if not url:
            continue
        published = _published(entry)
        if published is None:
            undated += 1
            continue
        if published < cutoff:
            continue
        title = entry.get("title", "").strip()
        blurb = _blurb(entry)
        if is_promotional(title, blurb):
            promotional += 1
            continue
        items.append(
            Item(
                id=item_id(url),
                source=source.name,
                section=source.section,
                title=title,
                blurb=blurb,
                url=canonical_url(url),
                published=published,
                weight=source.weight,
            )
        )
    if promotional:
        log.info("%s: skipped %d newsletter or podcast trailers", source.name, promotional)
    # A feed that parses fine but yields nothing looks identical to a healthy
    # quiet week in the log. Nikkei's RSS, for one, carries no dates at all, so
    # every entry falls out here and the source silently contributes zero.
    if not items and parsed.entries:
        log.warning(
            "%s parsed %d entries but contributed none (%d had no date) — "
            "check whether the feed still publishes what we need",
            source.name, len(parsed.entries), undated,
        )
    return items


def ingest(cfg: Config, now: datetime | None = None) -> list[Item]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=cfg.run.fetch_days)

    items: list[Item] = []
    failures = 0
    for source in cfg.sources:
        try:
            got = fetch_source(source, cutoff)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            failures += 1
            log.warning("feed failed, skipping: %s (%s)", source.name, exc)
            continue
        log.info("fetched %d items from %s", len(got), source.name)
        items.extend(got)

    if cfg.sources and failures == len(cfg.sources):
        raise AllFeedsFailed(
            f"all {failures} feeds failed — check the network before rerunning"
        )
    return items
