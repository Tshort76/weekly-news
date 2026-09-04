"""The editorial lens as data rather than prose.

`digest/prompts/rubric.md` is the product — the written test every headline is
judged against. It is thirty lines of hand-written markdown, and for one person
editing their own file that is exactly the right form for it to take.

It stops being the right form the moment someone else has to write one. A person
who has never written a prompt does not know that the examples are what makes it
work, or that "score 3 when the item is structurally important" classifies worse
than "a cartel forms or breaks". So the same content is also expressible as a
filled-in form, and `compile.py` turns the form back into the markdown.

The two representations are not equals. The file is the truth; a `LensSpec` is a
convenient way to write one. Nothing in the pipeline reads a spec — the stages
read the compiled markdown, exactly as they read the hand-written rubric today.

What is deliberately NOT here: the 0-to-3 scale, what novelty means, what a
mechanism is. Those are the same for every lens and were got right once, so the
compiler supplies them and the form never asks.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Example:
    """One concrete case at a fit level.

    `note` is the parenthetical that narrows a broad example — "a state gains or
    loses a capacity" means little until it says "(fiscal, military,
    administrative)". In the form this is the "why does this count" box.
    """

    text: str
    note: str = ""

    def rendered(self) -> str:
        return f"{self.text} ({self.note})" if self.note else self.text


@dataclass(frozen=True)
class Level:
    """A rung of the fit scale: what it means, and what it looks like.

    `lead` ending in a colon runs straight into the examples; ending in a full
    stop it gets an "Examples:" of its own. That is not decoration — it is the
    difference between "Interesting but mostly contest: an election result" and
    "A structural fact changed or was revealed. Examples: a central bank …".
    """

    lead: str
    examples: tuple[Example, ...] = ()
    unless: str = ""


@dataclass(frozen=True)
class Kinds:
    """The three-way sort, with fixed internal slots and lens-supplied words.

    `core` and `adjacent` are what the model is shown; the code downstream keys
    on the slot, never the word. This is load-bearing. The balance rule caps the
    adjacent slot at a share of the edition, and a local model rarely returns
    `neither` — it files off-lens items as adjacent instead — so that cap is
    quietly doing off-lens filtering as well as balance. A lens that renamed the
    idea rather than the word would lose that.
    """

    core: str
    core_gloss: str
    adjacent: str
    adjacent_gloss: str
    neither_gloss: str = "off-lens entirely"

    def words(self) -> tuple[str, str, str]:
        """What the model is shown, in the order the prompt lists them."""
        return (self.core, self.adjacent, "neither")

    def slot_for(self, word: str) -> str:
        """The model answers in the lens's vocabulary; the code stores a slot."""
        word = (word or "").strip().lower()
        if word in (self.core.lower(), "core"):
            return "core"
        if word in (self.adjacent.lower(), "adjacent"):
            return "adjacent"
        return "neither"

    def display(self, slot: str) -> str:
        return {"core": self.core, "adjacent": self.adjacent}.get(slot, "neither")


@dataclass(frozen=True)
class LensSpec:
    name: str
    about: str
    not_about: str
    fit3: Level
    fit2: Level
    fit1: Level
    never: tuple[str, ...]
    kinds: Kinds
    mechanism_examples: tuple[str, ...]
    high_interest: tuple[str, ...] = ()
    bias_extra: str = ""
    # Not compiled into the rubric — the rubric never lists them. They are the
    # enums `classify.md` offers the model and the names `emit.py` prints, and
    # they live here because a lens for one country wants different ones.
    regions: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    feeds: tuple[dict, ...] = field(default_factory=tuple)

    @staticmethod
    def from_toml(path: str | Path) -> "LensSpec":
        return LensSpec.from_dict(tomllib.loads(Path(path).read_text(encoding="utf-8")))

    @staticmethod
    def from_dict(raw: dict) -> "LensSpec":
        def examples(rows) -> tuple[Example, ...]:
            return tuple(Example(text=r["text"], note=r.get("note", "")) for r in rows or ())

        def level(key: str) -> Level:
            row = raw["fit"][key]
            return Level(
                lead=row["lead"],
                examples=examples(row.get("examples")),
                unless=row.get("unless", ""),
            )

        return LensSpec(
            name=raw["name"],
            about=raw["about"],
            not_about=raw["not_about"],
            fit3=level("3"),
            fit2=level("2"),
            fit1=level("1"),
            never=tuple(raw["fit"]["0"]["never"]),
            kinds=Kinds(**raw["kinds"]),
            mechanism_examples=tuple(raw.get("mechanism_examples", ())),
            high_interest=tuple(raw.get("bias", {}).get("high_interest", ())),
            bias_extra=raw.get("bias", {}).get("extra", ""),
            regions=tuple(raw.get("regions", ())),
            domains=tuple(raw.get("domains", ())),
            feeds=tuple(raw.get("feeds", ())),
        )
