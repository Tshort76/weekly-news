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


class SearchBlocked(RuntimeError):
    """The engine served a refusal page rather than results.

    Worth its own type because it is not the same as finding nothing. A run
    that grounds nothing because it was turned away needs a different answer
    from the reader than one where the searches genuinely came up empty.
    """

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
    """The no-key HTML endpoint. Free, and dependable if you are patient.

    It is scraped markup with no agreement behind it, and a burst earns an
    "anomaly" page with an address-level block that outlasts an hour. Spacing
    the queries out avoids that entirely: measured here, nineteen searches at
    SEARCH_PACE_SECONDS apart all returned results and none were refused.
    Weekly is exactly the shape of job that can afford the wait. Set
    search_backend = "brave" with a key if that ever stops holding.
    """
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    page = fetch_bytes(url, timeout=timeout).decode("utf-8", "ignore")
    if "anomaly" in page.lower() and "result__snippet" not in page:
        raise SearchBlocked(
            "DuckDuckGo served its anomaly page instead of results. This is an "
            "address-level block, not a per-request limit: once it starts, a "
            "single query an hour later is refused too, so there is no waiting "
            "it out inside a run. Pacing the queries prevents it; this run has "
            "stopped searching. Set search_backend = \"brave\" with a key, or "
            "\"none\" to stop asking."
        )
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


def _own_article(row: Classified) -> list[Evidence]:
    """The story's own page, when it is not behind a wall."""
    try:
        text = article_text(row.item.url)
    except Exception as exc:  # a paywall, a timeout, a consent wall — all one thing here
        log.debug("no article text for %s (%s)", row.item.url, type(exc).__name__)
        return []
    if len(text) < 500:
        return []
    return [Evidence(kind="article", text=text[:6000],
                     url=row.item.url, source=row.item.source)]


# Spaced rather than burst. This is a weekly job, so a generous gap costs
# nothing worth counting: forty queries at fifteen seconds is ten minutes of a
# run that already takes longer than that.
SEARCH_PACE_SECONDS = 15.0


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
    blocked = False

    for row in thin:
        found = _own_article(row)

        if not found and not blocked:
            try:
                if searches:
                    time.sleep(SEARCH_PACE_SECONDS)
                found = search(row.item.title, cfg)
            except SearchBlocked as exc:
                # Address-level, so every later query would be refused too.
                # Asking forty more times spends ten minutes learning the same
                # thing; the run carries on with what the articles gave it.
                log.warning("%s", exc)
                blocked = True
            except Exception as exc:
                log.warning("search failed for %r (%s)", row.item.title[:60], exc)

        row.evidence = found
        if not found:
            empty += 1
        elif found[0].kind == "article":
            articles += 1
        else:
            searches += 1

    log.info(
        "grounded: %d from the article itself, %d from other outlets, %d still thin",
        articles, searches, empty,
    )
    if blocked:
        log.warning(
            "%d items stayed thin because searching was refused — they are still "
            "written, from their headline and blurb alone", empty,
        )
    elif thin and not articles and not searches:
        log.warning(
            "every grounding attempt came back empty — the search markup may have "
            "changed, or the network is refusing us"
        )
    return rows
