"""Turn a filled-in form back into the rubric the pipeline reads.

The output has to be the same *shape* as the hand-written rubric, because
everything downstream is wired to that shape: `classify.md` asks for fit, kind,
novelty and mechanism; `selection.py` thresholds on fit and novelty and caps the
adjacent kind; `cluster.md` groups by mechanism.

It does not have to be the same *bytes*. The hand-written rubric is wrapped by a
person — line 3 breaks at 82 characters where any uniform wrap would have fitted
the next word — so reproducing it exactly would mean storing the line breaks,
which would mean storing the prose, which would defeat the point. What matters is
that a model scores the two the same, and that is measured rather than assumed:
see docs/design/phase-0-lens-compiler.md.
"""

from __future__ import annotations

import textwrap

from .schema import Example, LensSpec, Level

WIDTH = 88

# Sections the form never asks about, because they mean the same thing in every
# lens and the wording was got right once.
NOVELTY = (
    "NOVELTY 0–3: 3 = a new fact about the world; 0 = another episode of an ongoing "
    "story with nothing structurally new."
)


def _wrap(text: str, indent: str = "") -> str:
    return textwrap.fill(
        text, width=WIDTH, subsequent_indent=indent, break_long_words=False,
        break_on_hyphens=False,
    )


def _joined(examples: tuple[Example, ...]) -> str:
    """Semicolons only when a comma inside an example would be ambiguous.

    "a state gains or loses a capacity (fiscal, military, administrative)" in a
    comma-separated list reads as four items rather than one.
    """
    rendered = [e.rendered() for e in examples]
    separator = "; " if any("," in r for r in rendered) else ", "
    return separator.join(rendered)


def _level(score: int, level: Level) -> str:
    body = level.lead
    if level.examples:
        joiner = "" if body.endswith(":") else " Examples:"
        body = f"{body}{joiner} {_joined(level.examples)}"
    if level.unless:
        body = f"{body} — UNLESS {level.unless}"
    if not body.endswith("."):
        body += "."
    return _wrap(f"{score} — {body}", indent="    ")


def _kinds(spec: LensSpec) -> str:
    k = spec.kinds
    rows = [(k.core, k.core_gloss), (k.adjacent, k.adjacent_gloss), ("neither", k.neither_gloss)]
    pad = max(len(word) for word, _ in rows)
    lines = ["KIND:"]
    for word, gloss in rows:
        head = f"  {word.ljust(pad)} — "
        lines.append(_wrap(f"{head}{gloss}", indent=" " * len(head)))
    return "\n".join(lines)


def _mechanism(spec: LensSpec) -> str:
    quoted = ", ".join(f'"{e}"' for e in spec.mechanism_examples)
    tail = f", e.g. {quoted}" if quoted else ""
    return _wrap(
        f"MECHANISM: name the causal machinery in ≤12 words{tail}. "
        "Null if there is none."
    )


def _bias(spec: LensSpec) -> str:
    if not spec.high_interest and not spec.bias_extra:
        return ""
    parts = []
    if spec.high_interest:
        places = spec.high_interest
        listed = (
            f"{', '.join(places[:-1])}, and {places[-1]}" if len(places) > 2
            else " and ".join(places)
        )
        parts.append(
            f"{listed} are of high interest but do NOT inflate FIT for them; "
            "the lens is the only criterion."
        )
    if spec.bias_extra:
        parts.append(spec.bias_extra)
    parts.append("Ignore the source's own framing of importance.")
    return _wrap("Bias notes: " + " ".join(parts))


def compile_lens(spec: LensSpec) -> str:
    """Render a spec as the markdown the classify stage is handed verbatim."""
    listed = ", ".join(spec.never)
    fit0 = Level(lead=listed[:1].upper() + listed[1:])
    blocks = [
        f"LENS: {spec.name}.",
        _wrap(f"Score FIT 0–3 on whether the item describes {spec.about}."),
        "\n".join([
            _level(3, spec.fit3),
            _level(2, spec.fit2),
            _level(1, spec.fit1),
            _level(0, fit0),
        ]),
        _kinds(spec),
        _wrap(NOVELTY),
        _mechanism(spec),
        _bias(spec),
    ]
    return "\n\n".join(b for b in blocks if b) + "\n"
