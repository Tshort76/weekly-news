"""Threshold, saga and balance rules. Pure — named `selection` rather than
`select` so it can never shadow the stdlib module of that name."""

from __future__ import annotations

from collections.abc import Iterable

from rapidfuzz import fuzz

from .config import Config
from .models import Classified, Dropped

MECHANISM_THRESHOLD = 88


def _mechanism_seen(mechanism: str | None, prior: Iterable[str]) -> str | None:
    """Return the prior mechanism this one restates, if any."""
    if not mechanism:
        return None
    for p in prior:
        if fuzz.token_set_ratio(mechanism.lower(), p.lower()) >= MECHANISM_THRESHOLD:
            return p
    return None


def select(
    classified: list[Classified],
    cfg: Config,
    prior_mechanisms: Iterable[str] = (),
) -> tuple[list[Classified], list[Dropped]]:
    """Return (kept, dropped). `prior_mechanisms` comes from earlier editions;
    passing it in rather than reading the database keeps this function pure."""
    prior = list(prior_mechanisms)
    dropped: list[Dropped] = []

    def drop(c: Classified, reason: str) -> None:
        dropped.append(Dropped(id=c.id, title=c.item.title, stage="select", reason=reason))

    # 1 + 2: fit threshold, with a novelty exemption at fit 1.
    kept: list[Classified] = []
    for c in classified:
        if c.fit >= 2:
            kept.append(c)
        elif c.fit == 1 and c.novelty == 3:
            kept.append(c)
        else:
            drop(c, f"below threshold (fit={c.fit}, novelty={c.novelty})")

    # 3: saga rule. A low-novelty item repeating a mechanism we have already
    # covered is another episode, not news — unless it scores fit 3.
    survivors: list[Classified] = []
    for c in kept:
        echo = _mechanism_seen(c.mechanism, prior) if c.novelty <= 1 else None
        if echo and c.fit < 3:
            drop(c, f"saga: mechanism {echo!r} already covered, novelty={c.novelty}")
        else:
            survivors.append(c)
    kept = survivors

    # 4: balance rule. Contest items may not exceed cfg.contest_share of the
    # selected set. Dropping one shrinks the denominator too, so this iterates.
    contest = sorted(
        [c for c in kept if c.kind == "contest"], key=lambda c: (c.fit, c.novelty, c.id)
    )
    while contest and len(contest) > cfg.run.contest_share * len(kept):
        loser = contest.pop(0)
        kept.remove(loser)
        drop(
            loser,
            f"balance rule: contest items capped at {cfg.run.contest_share:.0%} of the set",
        )

    # 5: hard cap before clustering, best first.
    kept.sort(key=lambda c: (-c.rank, -c.novelty, c.id))
    if len(kept) > cfg.run.max_items:
        for c in kept[cfg.run.max_items :]:
            drop(c, f"over the {cfg.run.max_items}-item cap")
        kept = kept[: cfg.run.max_items]

    return kept, dropped


def contest_share(entries_kinds: list[str]) -> float:
    if not entries_kinds:
        return 0.0
    return sum(1 for k in entries_kinds if k == "contest") / len(entries_kinds)
