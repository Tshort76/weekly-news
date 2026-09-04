"""Clean feed entries into a common shape. Pure."""

from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from .models import Item

# Enough to carry a feed's full article text where it gives one. The old 400
# cut Ars Technica and Semafor bodies off mid-paragraph, and a writer handed
# half a story finishes it from memory.
BLURB_LIMIT = 2000

# utm_*, plus the tracking params the wire services and newsletters attach.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_EXACT = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src",
    "source", "cmpid", "smid", "at_medium", "at_campaign", "sh", "s",
    "__twitter_impression", "utm", "ito", "CMP",
}

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(text: str) -> str:
    """Remove tags and resolve entities. Feed blurbs routinely carry both."""
    if not text:
        return ""
    # Unescape twice: some feeds double-encode (&amp;lt;p&amp;gt;).
    out = _TAG.sub(" ", html.unescape(html.unescape(text)))
    return _WS.sub(" ", out).strip()


def canonical_url(url: str) -> str:
    """Drop tracking params and the fragment; keep everything that identifies the page."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PREFIXES) and k not in _TRACKING_EXACT
    ]
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(kept), ""))


def item_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()


def truncate(text: str, limit: int = BLURB_LIMIT) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "…"


def normalize(item: Item) -> Item:
    """Return a cleaned copy. The id is recomputed from the canonical url."""
    url = canonical_url(item.url)
    return Item(
        id=item_id(url),
        source=item.source,
        section=item.section,
        title=_WS.sub(" ", strip_html(item.title)),
        blurb=truncate(strip_html(item.blurb)),
        url=url,
        published=item.published,
        weight=item.weight,
        also_in=list(item.also_in),
    )


def normalize_all(items: list[Item]) -> list[Item]:
    return [normalize(i) for i in items if canonical_url(i.url) and i.title.strip()]
