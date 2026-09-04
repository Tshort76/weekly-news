"""Score a model's judgement against labels a person wrote.

This was `scripts/eval_rubric.py`, and it is the only instrument in the project
that can say whether a change to the judging made things better or worse. It
moves into the package because it stops being a developer's script the moment a
user can pick their own model: "is the smaller one good enough for me?" has no
honest answer except running it on their own headlines.

Two callers, one engine. The script keeps working with the labels shipped in the
test fixtures; the app passes the user's own.

The headline number is not accuracy. Getting a fit score one off matters far less
than putting an ant-smuggling story in the briefing, so the report leads with
what was let in wrongly and what was dropped wrongly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .models import Classified, Item

FIXTURES = Path(__file__).resolve().parent / "tests" / "fixtures"


def plain(text: str) -> str:
    """Feeds mix curly and straight apostrophes; labels should not have to care."""
    return text.replace("’", "'").replace("‘", "'")


def kept(fit: int, novelty: int) -> bool:
    """The selection rule, minus the saga and balance passes, which need history.

    Both sides of a comparison go through this, so the fit-1 novelty exemption
    is scored against a real labelled novelty rather than an assumed one.
    """
    return fit >= 2 or (fit == 1 and novelty == 3)


@dataclass
class Report:
    total: int = 0
    exact: int = 0
    within_one: int = 0
    kind_ok: int = 0
    novelty_ok: int = 0
    over: int = 0
    under: int = 0
    wanted: int = 0
    false_keeps: list[str] = field(default_factory=list)
    false_drops: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def agreement(self) -> int:
        """Items where the lens and the person reached the same keep/drop call."""
        return self.total - len(self.false_keeps) - len(self.false_drops)


def score(results: list[Classified], labels: dict[str, dict]) -> Report:
    report = Report(total=len(results))
    for c in results:
        label = labels.get(c.id)
        if label is None:
            continue
        delta = c.fit - label["fit"]
        report.exact += delta == 0
        report.within_one += abs(delta) <= 1
        report.over += delta > 0
        report.under += delta < 0
        if "kind" in label:
            report.kind_ok += c.kind == label["kind"]
        if "novelty" in label:
            report.novelty_ok += c.novelty == label["novelty"]

        want = kept(label["fit"], label.get("novelty", 0))
        got = kept(c.fit, c.novelty)
        report.wanted += want
        if got and not want:
            report.false_keeps.append(c.item.title)
        if want and not got:
            report.false_drops.append(c.item.title)
    return report


def shipped_labels() -> tuple[list[Item], dict[str, dict]]:
    """The 25 hand-labelled items in the test fixtures.

    Small enough that it calibrates rather than proves: phase 0 measured a
    ten-point swing on rubric formatting alone, which at 25 items is two or
    three stories. Good for a conversation about what someone means, weak as an
    acceptance gate.
    """
    items = [Item.from_dict(d) for d in json.loads((FIXTURES / "eval_items.json").read_text())]
    labels = json.loads((FIXTURES / "eval_labels.json").read_text())["labels"]
    by_id: dict[str, dict] = {}
    for label in labels:
        match = next(
            (i for i in items if plain(i.title).startswith(plain(label["title"]))), None
        )
        if match is None:
            raise LookupError(f"no item matches label {label['title']!r}")
        by_id[match.id] = label
    return [i for i in items if i.id in by_id], by_id


# Want / Maybe / Skip is what the app asks for; a fit is what the rubric speaks.
# The mapping is deliberately coarse — a person sorting their own headlines is
# not scoring them, and pretending otherwise would make the screen feel like an
# exam.
CHOICES = {"want": {"fit": 3, "novelty": 3}, "maybe": {"fit": 2, "novelty": 2},
           "skip": {"fit": 0, "novelty": 0}}


def compare(items, labels: dict[str, dict], models: list[tuple[str, str]], cfg) -> list[dict]:
    """Score each candidate model over the same items and labels.

    The only honest answer to "is the smaller model good enough for me?", and a
    minute's work on a local model for twenty-five items. Every row says what it
    got wrong in both directions, because that is the number that matters and an
    accuracy percentage hides it.
    """
    import copy  # noqa: PLC0415
    import time  # noqa: PLC0415

    from .classify import classify  # noqa: PLC0415
    from .llm import Client  # noqa: PLC0415

    rows = []
    for provider, model in models:
        trial = copy.deepcopy(cfg)
        trial.models.provider = provider
        trial.models.classify_provider = provider
        trial.models.classify = model
        started = time.time()
        try:
            results = classify(list(items), trial, Client(trial))
        except Exception as exc:
            rows.append({"model": model, "provider": provider, "error": str(exc)[:200]})
            continue
        report = score(results, labels)
        report.seconds = time.time() - started
        rows.append({"model": model, "provider": provider, "report": report})
    return rows


def labels_from_choices(choices: dict[str, str]) -> dict[str, dict]:
    return {
        item_id: dict(CHOICES[choice], title="")
        for item_id, choice in choices.items()
        if choice in CHOICES
    }
