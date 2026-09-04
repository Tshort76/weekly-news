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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import certifi
import feedparser

from .config import Config
from .models import Item, Source
from .normalize import canonical_url, item_id, strip_html

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
    """The most text the feed is willing to give us about this story.

    Taking the first field that happened to be filled meant taking `summary`
    whenever it existed, and `summary` is the teaser. Ars Technica's was 78
    characters while the `content` field on the same entry held 1010 — a
    thousand characters of the actual article, already downloaded, thrown away
    in favour of the caption. The writer then had to describe a story it had
    barely been told, which is where invented detail comes from.
    """
    candidates = [entry.get(key) or "" for key in ("summary", "description", "subtitle")]
    candidates += [c.get("value", "") or "" for c in (entry.get("content") or [])]
    return max(candidates, key=len, default="")


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


@dataclass
class FeedReport:
    """What one fetch of a candidate feed found.

    The checks are the ones the pipeline already makes, moved to the moment a
    feed is added rather than left as a warning in a log nobody reads. Nikkei's
    RSS is why: it parses cleanly, carries no dates, and therefore contributed
    exactly nothing week after week.
    """

    url: str
    name: str = ""
    ok: bool = False
    entries: int = 0
    dated: int = 0
    recent: int = 0
    promotional: int = 0
    median_blurb: int = 0
    headlines: list[str] = field(default_factory=list)
    error: str = ""
    checked_on: str = ""

    @property
    def usable(self) -> bool:
        return self.ok and self.dated > 0

    def describe(self) -> str:
        if not self.ok:
            return f"Could not read that feed: {self.error}"
        lines = [f"{self.name or self.url}: {self.entries} entries."]
        if self.dated == 0:
            lines.append(
                "  None of them carry a date, so every item would fall out at the "
                "freshness cutoff and this feed would contribute nothing. That is "
                "the feed's own fault and there is nothing to configure."
            )
        else:
            lines.append(f"  {self.dated} carry a date; {self.recent} are from the last week.")
        if self.median_blurb and self.dated:
            if self.median_blurb < 100:
                lines.append(
                    f"  Blurbs run about {self.median_blurb} characters — headline "
                    "only. Every item from here will be looked up before it is written."
                )
            elif self.median_blurb < 500:
                lines.append(
                    f"  Blurbs run about {self.median_blurb} characters, so most items "
                    "will be looked up before they are written."
                )
            else:
                lines.append(
                    f"  Blurbs run about {self.median_blurb} characters — enough to "
                    "write from, and often the reporter's own words."
                )
        if self.promotional:
            lines.append(f"  {self.promotional} newsletter or podcast trailers would be skipped.")
        for headline in self.headlines:
            lines.append(f"    · {headline}")
        return "\n".join(lines)


def probe(url: str, now: datetime | None = None, days: int = 8) -> FeedReport:
    """Fetch a candidate feed once and say what it would contribute."""
    now = now or datetime.now(timezone.utc)
    report = FeedReport(url=url, checked_on=now.date().isoformat())
    try:
        raw = fetch_bytes(url)
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {exc}"
        return report
    parsed = feedparser.parse(raw)
    report.ok = True
    report.name = (parsed.feed.get("title") or "").strip() if parsed.feed else ""
    report.entries = len(parsed.entries)
    cutoff = now - timedelta(days=days)
    lengths = []
    for entry in parsed.entries:
        published = _published(entry)
        if published is None:
            continue
        report.dated += 1
        if published >= cutoff:
            report.recent += 1
        title = entry.get("title", "").strip()
        blurb = _blurb(entry)
        if is_promotional(title, blurb):
            report.promotional += 1
            continue
        lengths.append(len(strip_html(blurb)))
        if len(report.headlines) < 5:
            report.headlines.append(title)
    if lengths:
        lengths.sort()
        report.median_blurb = lengths[len(lengths) // 2]
    return report


def sample(cfg: Config, n: int = 25, now: datetime | None = None) -> list[Item]:
    """A spread of recent headlines for the user to sort.

    Spread across feeds rather than taken in order: the first twenty-five items
    of a fetch are whatever the busiest feed published this morning, and asking
    someone to calibrate their lens on one outlet's Tuesday teaches the lens
    about that outlet.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=cfg.run.fetch_days)
    by_source: dict[str, list[Item]] = {}
    for source in cfg.sources:
        try:
            by_source[source.name] = fetch_source(source, cutoff)
        except Exception as exc:
            log.warning("feed failed while sampling: %s (%s)", source.name, exc)

    picked: list[Item] = []
    round_number = 0
    while len(picked) < n and any(len(v) > round_number for v in by_source.values()):
        for items in by_source.values():
            if len(items) > round_number and len(picked) < n:
                picked.append(items[round_number])
        round_number += 1
    return picked


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
