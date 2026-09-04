"""Get more text about a story than its feed entry carried.

Runs after selection, so the cost is paid only for the sixty items actually
being written up rather than the several hundred fetched.

Three tiers, cheapest first. Most feeds carry the article body and after the
blurb fix that is already enough. Where it is not, the story's own page usually
is. Where that is refused — The Economist answers 403 to everything, and it is
the spine of the source list — other outlets covering the same event are the
only remaining ground, and they are labelled as such all the way through.

Nothing here can fail the run. Every tier degrades to the tier below it and the
last one degrades to no evidence at all, which is exactly where the pipeline
was before this stage existed.
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
import urllib.parse
import urllib.request

from .config import Config
from .ingest import SSL_CONTEXT, fetch_bytes
from .models import Classified, Evidence

log = logging.getLogger("digest.ground")

_DROP_TAGS = re.compile(r"(?is)<(script|style|nav|header|footer|aside|form)[^>]*>.*?</\1>")
_PARA = re.compile(r"(?is)<p[^>]*>(.*?)</p>")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_SNIPPET = re.compile(r'(?is)class="result__snippet".*?>(.*?)</a>')


def _clean(fragment: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", fragment))).strip()


def article_text(url: str, timeout: int = 12) -> str:
    """The story's own page, reduced to its paragraphs.

    Deliberately naive. A paywall answers 403 and a consent wall answers a page
    with no paragraphs, and both come back as "not enough" through the same
    path, which is the only distinction this stage needs to make.
    """
    raw = fetch_bytes(url, timeout=timeout).decode("utf-8", "ignore")
    paragraphs = [_clean(p) for p in _PARA.findall(_DROP_TAGS.sub(" ", raw))]
    return _WS.sub(" ", " ".join(p for p in paragraphs if len(p) > 40)).strip()


def duckduckgo(query: str, limit: int = 4, timeout: int = 15) -> list[Evidence]:
    """The no-key HTML endpoint. Free, and best-effort in the literal sense.

    It is scraped markup with no agreement behind it, and it rate-limits hard:
    a dozen queries in a row earns an "anomaly" page with no results and a
    cooldown that outlasts several minutes of pacing. Fine for trying a handful,
    not something to build a weekly run on. Set search_backend = "brave" and
    supply a key for grounding that actually happens every week.
    """
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    page = fetch_bytes(url, timeout=timeout).decode("utf-8", "ignore")
    snippets = [_clean(s) for s in _SNIPPET.findall(page)]
    return [Evidence(kind="search", text=s) for s in snippets if len(s) > 60][:limit]


def brave(query: str, limit: int = 4, timeout: int = 15) -> list[Evidence]:
    """Brave's search API. Needs a key; the free tier is far above what a week
    of this costs, which is at most one query per selected item."""
    from .credentials import api_key, describe_sources  # noqa: PLC0415

    key = api_key("brave")
    if not key:
        raise LookupError(describe_sources("brave"))
    url = ("https://api.search.brave.com/res/v1/web/search?count=%d&q=%s"
           % (limit, urllib.parse.quote(query)))
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "X-Subscription-Token": key}
    )
    with urllib.request.urlopen(request, timeout=timeout, context=SSL_CONTEXT) as resp:
        payload = json.loads(resp.read().decode("utf-8", "ignore"))
    found = []
    for hit in payload.get("web", {}).get("results", [])[:limit]:
        text = _clean(hit.get("description", ""))
        if len(text) > 60:
            found.append(Evidence(kind="search", text=text,
                                  url=hit.get("url", ""), source=hit.get("profile", {}).get("name", "")))
    return found


SEARCH_BACKENDS = {"duckduckgo": duckduckgo, "brave": brave, "none": lambda *a, **k: []}


def search(query: str, cfg: Config | None = None, **kwargs) -> list[Evidence]:
    name = cfg.run.search_backend if cfg else "duckduckgo"
    backend = SEARCH_BACKENDS.get(name)
    if backend is None:
        raise LookupError(
            f"unknown search_backend {name!r} — pick one of {sorted(SEARCH_BACKENDS)}"
        )
    return backend(query, **kwargs)


def _gather(row: Classified, cfg: Config, searched_already: bool = False) -> list[Evidence]:
    if len(row.item.blurb) >= cfg.run.ground_min_chars:
        return []

    found: list[Evidence] = []
    try:
        text = article_text(row.item.url)
        if len(text) >= cfg.run.ground_min_chars:
            return [Evidence(kind="article", text=text[:6000],
                             url=row.item.url, source=row.item.source)]
    except Exception as exc:  # a paywall, a timeout, a consent wall — all the same here
        log.debug("no article text for %s (%s)", row.item.url, type(exc).__name__)

    try:
        # Spaced rather than burst: a rapid run of queries is what earns the
        # cooldown. Nothing waits before the first one, so a single lookup is
        # as fast as it ever was.
        if searched_already:
            time.sleep(SEARCH_PACE_SECONDS)
        found = search(row.item.title, cfg)
    except Exception as exc:
        log.warning("search failed for %r (%s)", row.item.title[:60], exc)
    return found


# A burst is what earns the cooldown, so the searches are spaced. Article
# fetches are not: they go to a different host each time.
SEARCH_PACE_SECONDS = 2.0


def ground(rows: list[Classified], cfg: Config) -> list[Classified]:
    """Attach evidence to the thin items. Returns the same rows, mutated."""
    if not cfg.run.ground:
        return rows

    thin = [r for r in rows if len(r.item.blurb) < cfg.run.ground_min_chars]
    log.info(
        "grounding %d of %d selected items (the rest have enough already)",
        len(thin), len(rows),
    )
    articles = searches = empty = 0
    for row in thin:
        row.evidence = _gather(row, cfg, searched_already=bool(searches))
        if not row.evidence:
            empty += 1
        elif row.evidence[0].kind == "article":
            articles += 1
        else:
            searches += 1
    log.info(
        "grounded: %d from the article itself, %d from other outlets, %d still thin",
        articles, searches, empty,
    )
    if thin and not articles and not searches:
        log.warning(
            "every grounding attempt came back empty — the search markup may have "
            "changed, or the network is refusing us"
        )
    return rows
