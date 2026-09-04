"""A LensSpec back to the TOML the form loads and saves.

Round-tripping matters more than prettiness: what the form writes has to load
again as the same lens, and `test_lens_form` holds it to that. Comments a person
left in their own lens.toml do not survive a form save — that file is the form's
copy, and `lens.md` is the one written for human eyes.
"""

from __future__ import annotations

from ..config.write import _scalar
from .schema import Example, LensSpec, Level


def _examples(rows: tuple[Example, ...]) -> list[str]:
    out = []
    for example in rows:
        note = f", note = {_scalar(example.note)}" if example.note else ""
        out.append(f"  {{ text = {_scalar(example.text)}{note} }},")
    return out


def _level(key: str, level: Level) -> list[str]:
    lines = [f"[fit.{key}]", f"lead = {_scalar(level.lead)}"]
    if level.examples:
        lines.append("examples = [")
        lines += _examples(level.examples)
        lines.append("]")
    if level.unless:
        lines.append(f"unless = {_scalar(level.unless)}")
    lines.append("")
    return lines


def to_toml(spec: LensSpec) -> str:
    lines = [
        f"name = {_scalar(spec.name)}",
        f"about = {_scalar(spec.about)}",
        f"not_about = {_scalar(spec.not_about)}",
        "",
        f"mechanism_examples = {_scalar(list(spec.mechanism_examples))}",
        f"regions = {_scalar(list(spec.regions))}",
        f"domains = {_scalar(list(spec.domains))}",
        "",
    ]
    lines += _level("3", spec.fit3)
    lines += _level("2", spec.fit2)
    lines += _level("1", spec.fit1)
    lines += ["[fit.0]", f"never = {_scalar(list(spec.never))}", ""]
    lines += [
        "[kinds]",
        f"core = {_scalar(spec.kinds.core)}",
        f"core_gloss = {_scalar(spec.kinds.core_gloss)}",
        f"adjacent = {_scalar(spec.kinds.adjacent)}",
        f"adjacent_gloss = {_scalar(spec.kinds.adjacent_gloss)}",
        f"neither_gloss = {_scalar(spec.kinds.neither_gloss)}",
        "",
        "[bias]",
        f"high_interest = {_scalar(list(spec.high_interest))}",
        f"extra = {_scalar(spec.bias_extra)}",
        "",
    ]
    if spec.feeds:
        for feed in spec.feeds:
            lines.append("[[feeds]]")
            lines += [f"{k} = {_scalar(v)}" for k, v in feed.items()]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
