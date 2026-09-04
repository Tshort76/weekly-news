"""Turning form fields into a lens, and back.

Kept out of app.py because it is the fiddly half of phase 3 and the tests want
it without a web server. The shape it produces is the rubric's own shape — the
form is a form *for that shape*, not a general prompt editor.
"""

from __future__ import annotations

from ..lens.schema import Example, Kinds, LensSpec, Level


def _rows(form, prefix: str) -> tuple[Example, ...]:
    """Repeatable example rows arrive as parallel lists of text and note."""
    texts = form.getlist(f"{prefix}_text")
    notes = form.getlist(f"{prefix}_note")
    out = []
    for n, text in enumerate(texts):
        text = (text or "").strip()
        if text:
            note = (notes[n] if n < len(notes) else "").strip()
            out.append(Example(text=text, note=note))
    return tuple(out)


def _list(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


def from_form(form, previous: LensSpec) -> LensSpec:
    """Build a spec from posted fields, keeping what the form does not ask about.

    Regions, domains and the mechanism examples are carried through from the
    lens already in place rather than defaulted: they are the enums the whole
    pipeline is wired to, and silently resetting them because a form did not
    include a field would be the worst kind of data loss.
    """
    def value(name: str, fallback: str = "") -> str:
        return (form.get(name) or fallback).strip()

    return LensSpec(
        name=value("name", previous.name),
        about=value("about", previous.about),
        not_about=value("not_about", previous.not_about),
        fit3=Level(lead=value("fit3_lead", previous.fit3.lead),
                   examples=_rows(form, "fit3") or previous.fit3.examples),
        fit2=Level(lead=value("fit2_lead", previous.fit2.lead),
                   examples=_rows(form, "fit2") or previous.fit2.examples),
        fit1=Level(lead=value("fit1_lead", previous.fit1.lead),
                   examples=_rows(form, "fit1") or previous.fit1.examples,
                   unless=value("fit1_unless", previous.fit1.unless)),
        never=_list(value("never")) or previous.never,
        kinds=Kinds(
            core=value("kind_core", previous.kinds.core),
            core_gloss=value("kind_core_gloss", previous.kinds.core_gloss),
            adjacent=value("kind_adjacent", previous.kinds.adjacent),
            adjacent_gloss=value("kind_adjacent_gloss", previous.kinds.adjacent_gloss),
            neither_gloss=previous.kinds.neither_gloss,
        ),
        mechanism_examples=previous.mechanism_examples,
        high_interest=_list(value("high_interest")),
        bias_extra=value("bias_extra", previous.bias_extra),
        regions=previous.regions,
        domains=previous.domains,
        feeds=previous.feeds,
    )


def add_example(spec: LensSpec, level: str, text: str, note: str = "") -> LensSpec:
    """Put a headline into a fit level. This is what calibration's button does.

    A headline the user skipped that the lens would have kept goes in at fit 1,
    which is the "near the topic, usually skip" rung — the lens learns the shape
    of the thing rather than being told a rule about it.
    """
    from dataclasses import replace  # noqa: PLC0415

    field = {"3": "fit3", "2": "fit2", "1": "fit1"}[level]
    current: Level = getattr(spec, field)
    updated = Level(
        lead=current.lead,
        examples=current.examples + (Example(text=text.strip(), note=note.strip()),),
        unless=current.unless,
    )
    return replace(spec, **{field: updated})
