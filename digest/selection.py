"""Threshold, saga and balance rules. Pure — named `selection` rather than
`select` so it can never shadow the stdlib module of that name."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from rapidfuzz import fuzz

from .config import Config
from .models import Classified, Dropped

MECHANISM_THRESHOLD = 88

log = logging.getLogger("digest.selection")


def gate_applies(c: Classified, gate) -> bool:
    """Is this lens's gate decisive for this item at all?"""
    return bool(gate) and c.region in gate.regions


def gated_out(c: Classified, gate) -> bool:
    """Does this lens's gate exclude this item?

    Only `False` drops. `None` — a lens with no gate, a backend that ignored the
    response schema, a row classified before the field existed — always passes,
    because `audit` re-runs this whole function over stored rows and a
    strict-on-missing rule would retroactively empty every past edition.
    """
    return gate_applies(c, gate) and c.gate is False


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

    # 3: the lens's gate, when it has one. A structural boolean rather than a
    # sentence in the rubric, because three measured placements of the same
    # sentence changed nothing at all — the model agreed with the rule in its
    # own `reason` field and kept the item regardless.
    gate = cfg.lens.gate
    if gate:
        survivors, unanswered = [], 0
        for c in kept:
            if gated_out(c, gate):
                drop(c, f"gate ({c.region}): answered no to {gate.question!r}")
                continue
            unanswered += gate_applies(c, gate) and c.gate is None
            survivors.append(c)
        kept = survivors
        if unanswered:
            # One line, not one per item: every `log.warning` becomes a row in
            # the health screen, and `audit` over a week stored before gates
            # existed would otherwise fill it with the same finding forty times.
            log.warning(
                "%d item(s) in %s were not asked the gate question — the rule "
                "could not apply to them", unanswered, ", ".join(gate.regions),
            )

    # 4: saga rule. A low-novelty item repeating a mechanism we have already
    # covered is another episode, not news — unless it scores fit 3.
    survivors: list[Classified] = []
    for c in kept:
        echo = _mechanism_seen(c.mechanism, prior) if c.novelty <= 1 else None
        if echo and c.fit < 3:
            drop(c, f"saga: mechanism {echo!r} already covered, novelty={c.novelty}")
        else:
            survivors.append(c)
    kept = survivors

    # 5: balance rule. Items in the ADJACENT slot — whatever this lens calls it,
    # "contest" in the original — may not exceed cfg.contest_share of the
    # selected set. Dropping one shrinks the denominator too, so this iterates.
    #
    # The slot does more than balance. A local model rarely answers "neither"
    # and files most off-lens items as adjacent instead, so this cap is quietly
    # the last off-lens filter as well.
    word = cfg.lens.kinds.adjacent
    contest = sorted(
        [c for c in kept if c.kind == "adjacent"], key=lambda c: (c.fit, c.novelty, c.id)
    )
    while contest and len(contest) > cfg.run.contest_share * len(kept):
        loser = contest.pop(0)
        kept.remove(loser)
        drop(
            loser,
            f"balance rule: {word} items capped at {cfg.run.contest_share:.0%} of the set",
        )

    # 6: hard cap before clustering, best first.
    kept.sort(key=lambda c: (-c.rank, -c.novelty, c.id))
    if len(kept) > cfg.run.max_items:
        for c in kept[cfg.run.max_items :]:
            drop(c, f"over the {cfg.run.max_items}-item cap")
        kept = kept[: cfg.run.max_items]

    return kept, dropped


def contest_share(entries_kinds: list[str]) -> float:
    if not entries_kinds:
        return 0.0
    return sum(1 for k in entries_kinds if k == "adjacent") / len(entries_kinds)
