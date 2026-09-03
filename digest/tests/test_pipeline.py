"""The shell's two contracts: dry-run does not touch durable state, and audit is
just `select` run again over the stored classifications."""

from __future__ import annotations

from datetime import datetime, timezone

import digest.pipeline as pipeline
from digest.llm import extract_json
from digest.models import Classified, Item
from digest.state import State

from .conftest import load_fixture

RECORDED = load_fixture("synthesize_response.json")


class FakeClient:
    """Answers whichever prompt it is handed, so a whole run needs no network."""

    def complete(self, **kwargs) -> str:
        prompt = kwargs["prompt"]
        if kwargs["stage"] == "classify":
            ids = _ids_in(prompt)
            return "[" + ",".join(
                f'{{"id": "{i}", "fit": 3, "kind": "architecture", "novelty": 3, '
                f'"region": "east_asia", "domain": "finance", "mechanism": "m{n}", "reason": "r"}}'
                for n, i in enumerate(ids)
            ) + "]"
        if "cluster_id" in prompt and "item_ids" in prompt:
            ids = _ids_in(prompt)
            return "[" + ",".join(
                f'{{"cluster_id": "c{n}", "title": "t{n}", "item_ids": ["{i}"], "shared_mechanism": null}}'
                for n, i in enumerate(ids)
            ) + "]"
        if "closing_questions" in prompt:
            return RECORDED["frame"]
        return RECORDED["entry"]

    def complete_json(self, **kwargs):
        return extract_json(self.complete(**kwargs))


def _ids_in(prompt: str) -> list[str]:
    import re

    return re.findall(r'"id": "([0-9a-f]{40})"', prompt)


def _patch_ingest(monkeypatch, items: list[Item]) -> None:
    monkeypatch.setattr(pipeline.ingest_stage, "ingest", lambda cfg, now=None: list(items))


TITLES = [
    "The Bank of Japan rewrites its operating framework",
    "Indonesia holds nickel export earnings onshore",
    "Brussels rolls a steel safeguard past its sunset",
]


def _items() -> list[Item]:
    return [
        Item(
            id="recomputed",
            source="Economist — Finance and Economics",
            section="finance-and-economics",
            title=title,
            blurb="A blurb with a mechanism in it.",
            url=f"https://www.economist.com/finance/{n}",
            published=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        for n, title in enumerate(TITLES)
    ]


def test_a_full_run_writes_the_files_and_records_the_week(cfg, tmp_path, monkeypatch):
    _patch_ingest(monkeypatch, _items())
    with State(tmp_path / "s.db") as st:
        result = pipeline.run(cfg, st, week="2026-W36", no_drive=True, client=FakeClient())
        assert result.files["txt"].exists() and result.files["md"].exists()
        assert st.load_edition("2026-W36") is not None
        assert len(st.seen_ids()) == 3


def test_a_dry_run_writes_files_and_classifications_but_no_durable_state(cfg, tmp_path, monkeypatch):
    _patch_ingest(monkeypatch, _items())
    with State(tmp_path / "s.db") as st:
        result = pipeline.run(
            cfg, st, week="2026-W36", dry_run=True, no_drive=True, client=FakeClient()
        )
        assert result.files["txt"].exists()
        assert st.load_classified("2026-W36"), "audit needs these"
        assert st.load_edition("2026-W36") is None
        assert st.seen_ids() == set()


def test_a_repeated_dry_run_sees_the_same_items(cfg, tmp_path, monkeypatch):
    _patch_ingest(monkeypatch, _items())
    with State(tmp_path / "s.db") as st:
        first = pipeline.run(cfg, st, week="2026-W36", dry_run=True, no_drive=True, client=FakeClient())
        second = pipeline.run(cfg, st, week="2026-W36", dry_run=True, no_drive=True, client=FakeClient())
        assert first.kept_after_dedupe == second.kept_after_dedupe == 3


def test_classify_only_writes_no_digest(cfg, tmp_path, monkeypatch):
    _patch_ingest(monkeypatch, _items())
    with State(tmp_path / "s.db") as st:
        result = pipeline.run(
            cfg, st, week="2026-W36", classify_only=True, no_drive=True, client=FakeClient()
        )
        assert result.files == {}
        assert len(st.load_classified("2026-W36")) == 3


def test_audit_reruns_selection_over_the_stored_classifications(cfg, tmp_path):
    rows = [Classified.from_dict(d) for d in load_fixture("classified_week.json")]
    with State(tmp_path / "s.db") as st:
        st.save_classified(rows, "2026-W36")
        dropped = pipeline.audit(cfg, st, "2026-W36")
    assert {d.id for d in dropped} >= {"b1", "c3"}
    assert all(d.reason for d in dropped)


def test_iso_week_formats_with_a_padded_week_number():
    assert pipeline.iso_week(datetime(2026, 1, 8, tzinfo=timezone.utc)) == "2026-W02"


def test_a_fuzzy_duplicate_does_not_resurface_the_following_week(cfg, tmp_path, monkeypatch):
    """The fetch window overlaps by a day. If only the dedupe winner were marked
    seen, the loser would come back next week looking like a new story."""
    winner = _items()[0]
    loser = Item(
        id="recomputed",
        source="FT — World",
        section="world",
        title=winner.title,
        blurb="The same story from another feed.",
        url="https://www.ft.com/content/boj",
        published=winner.published,
        weight=0.9,
    )
    _patch_ingest(monkeypatch, [winner, loser])
    with State(tmp_path / "s.db") as st:
        first = pipeline.run(cfg, st, week="2026-W36", no_drive=True, client=FakeClient())
        assert first.kept_after_dedupe == 1
        second = pipeline.run(cfg, st, week="2026-W37", no_drive=True, client=FakeClient())
        assert second.kept_after_dedupe == 0
        assert second.edition.quiet
