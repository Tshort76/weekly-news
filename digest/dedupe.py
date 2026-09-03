"""Collapse the same story arriving from several feeds. Pure."""

from __future__ import annotations

from collections.abc import Iterable

from rapidfuzz import fuzz

from .models import Item

TITLE_THRESHOLD = 90


def dedupe(items: list[Item], seen_ids: Iterable[str] = ()) -> tuple[list[Item], list[tuple[Item, str]]]:
    """Return (kept, dropped) where dropped carries the reason.

    1. Exact canonical-url match: keep the first.
    2. Fuzzy title match across sources: keep the higher-weight source, record
       the loser's url on the winner's `also_in`.
    3. Anything already in the state store's `seen` table is dropped.
    """
    seen = set(seen_ids)
    dropped: list[tuple[Item, str]] = []

    by_url: dict[str, Item] = {}
    for it in items:
        if it.id in seen:
            dropped.append((it, "already seen in a prior edition"))
            continue
        if it.id in by_url:
            winner = by_url[it.id]
            if it.url not in winner.also_in and it.url != winner.url:
                winner.also_in.append(it.url)
            dropped.append((it, f"duplicate url of {winner.source}"))
            continue
        by_url[it.id] = it

    # Highest weight first, then earliest published, so the winner of a fuzzy
    # match is deterministic regardless of feed order.
    ordered = sorted(by_url.values(), key=lambda i: (-i.weight, i.published, i.id))

    kept: list[Item] = []
    for it in ordered:
        match = next(
            (
                k
                for k in kept
                if fuzz.token_set_ratio(k.title.lower(), it.title.lower()) >= TITLE_THRESHOLD
            ),
            None,
        )
        if match is None:
            kept.append(it)
            continue
        for url in [it.url, *it.also_in]:
            if url not in match.also_in and url != match.url:
                match.also_in.append(url)
        dropped.append((it, f"fuzzy title match with {match.source}: {match.title!r}"))

    kept.sort(key=lambda i: (i.published, i.id))
    return kept, dropped
