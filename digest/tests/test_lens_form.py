"""The lens form and the calibration screen. No model is ever called."""

from __future__ import annotations

import tomllib

import pytest

from digest.calibrate import Report, labels_from_choices, score
from digest.config import legacy, paths
from digest.jobs import Runner
from digest.lens import presets, store
from digest.lens.schema import LensSpec
from digest.lens.serialize import to_toml
from digest.models import Classified
from digest.ui.lensform import add_example, from_form

from .conftest import make_item

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from digest.ui.app import create_app  # noqa: E402

LEGACY = """
[models]
provider = "ollama"
[[sources]]
name = "A feed"
url = "https://example.com/rss"
"""


class Fields(dict):
    """The bits of a posted form these functions actually use."""

    def getlist(self, key):
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]

    def get(self, key, default=None):
        value = dict.get(self, key, default)
        return value[0] if isinstance(value, list) and value else value


@pytest.fixture
def installed(digest_home, tmp_path):
    (tmp_path / "digest.toml").write_text(LEGACY)
    legacy.import_legacy(tmp_path / "digest.toml")
    return digest_home


@pytest.fixture
def client(installed):
    return TestClient(create_app(Runner(paths.data_dir(), lambda **k: None)))


def test_a_lens_survives_the_round_trip_through_toml():
    spec = presets.load("architecture-of-rule")
    assert LensSpec.from_dict(tomllib.loads(to_toml(spec))) == spec


def test_the_form_keeps_the_enums_it_never_asked_about():
    """Regions and domains are what the whole pipeline is wired to."""
    previous = presets.load("architecture-of-rule")
    spec = from_form(Fields({"name": "Something else"}), previous)
    assert spec.name == "Something else"
    assert spec.regions == previous.regions
    assert spec.domains == previous.domains


def test_the_form_replaces_examples_rather_than_appending_to_them():
    previous = presets.load("architecture-of-rule")
    spec = from_form(
        Fields({"fit3_text": ["a new example", ""], "fit3_note": ["because", ""]}),
        previous,
    )
    assert [e.text for e in spec.fit3.examples] == ["a new example"]


def test_an_empty_example_row_is_not_saved_as_an_example():
    previous = presets.load("architecture-of-rule")
    spec = from_form(Fields({"fit3_text": ["", "  "], "fit3_note": ["", ""]}), previous)
    assert spec.fit3.examples == previous.fit3.examples  # unchanged, not emptied


def test_adding_an_example_lands_it_at_the_level_it_was_added_to():
    spec = presets.load("architecture-of-rule")
    updated = add_example(spec, "1", "Nepal flash floods", "a disaster, not a rule")
    assert updated.fit1.examples[-1].text == "Nepal flash floods"
    assert len(updated.fit3.examples) == len(spec.fit3.examples)


def flat(text: str) -> str:
    """The compiler wraps at 88 columns, so a phrase spans lines in the file."""
    return " ".join(text.split())


def test_posting_the_form_writes_both_files_and_they_agree(client):
    response = client.post("/lens", data={"name": "A different lens"},
                           follow_redirects=False)
    assert response.headers["location"] == "/lens?saved=1"
    assert "LENS: A different lens." in flat(paths.lens_file().read_text())
    # Saving is what clears the hand-edited flag: the hash is recorded with it.
    assert store.load().hand_edited is False


def test_a_hand_edit_is_flagged_before_the_form_can_overwrite_it(client):
    path = paths.lens_file()
    path.write_text(path.read_text() + "\nA rule I added myself.\n")
    body = client.get("/lens").text
    assert "edited" in body and "Show me the difference" in body


def test_the_difference_shows_the_hand_edited_line(client):
    path = paths.lens_file()
    path.write_text(path.read_text() + "\nA rule I added myself.\n")
    assert "+A rule I added myself." in client.get("/lens/diff").text


def test_choosing_a_preset_installs_the_measured_bytes(client):
    client.post("/lens/use", data={"name": "architecture-of-rule"})
    assert paths.lens_file().read_text() == presets.markdown("architecture-of-rule")


def test_the_calibration_screen_asks_before_it_fetches(client):
    body = client.get("/calibrate").text
    assert "Fetch 25 headlines" in body


def test_labelling_then_running_shows_both_disagreement_lists(client, monkeypatch):
    from digest import classify as classify_module
    from digest.ui import app as app_module

    items = [make_item(url=f"https://e.com/{n}", title=f"Story {n}") for n in range(3)]
    client.app.state.sample = items

    # The lens keeps story 0 and drops story 2; the user said the opposite.
    verdicts = {items[0].id: (3, 3), items[1].id: (3, 3), items[2].id: (0, 0)}

    def fake_classify(to_judge, cfg, client_):
        return [
            Classified(item=i, fit=verdicts[i.id][0], kind="core",
                       novelty=verdicts[i.id][1], region="global", domain="other",
                       mechanism=None, reason="")
            for i in to_judge
        ]

    monkeypatch.setattr(app_module, "load", app_module.load)
    monkeypatch.setattr(classify_module, "classify", fake_classify)
    monkeypatch.setattr("digest.ui.app.State", app_module.State)

    client.post("/calibrate/save", data={
        items[0].id: "skip", items[1].id: "want", items[2].id: "want",
    })
    body = client.get("/calibrate/result").text
    assert "Story 0" in body   # kept by the lens, skipped by the user
    assert "Story 2" in body   # wanted by the user, dropped by the lens


def test_adding_a_missed_headline_from_the_result_reaches_the_lens(client):
    client.post("/calibrate/example",
                data={"headline": "A story the lens missed", "level": "3"})
    assert "A story the lens missed" in flat(paths.lens_file().read_text())


def test_labels_outlive_a_lens_change(client, installed):
    from digest.state import State

    with State(paths.data_dir() / "state.db") as state:
        state.save_labels([{"item_id": "x", "title": "T", "choice": "want"}])
    client.post("/lens/use", data={"name": "architecture-of-rule"})
    with State(paths.data_dir() / "state.db") as state:
        assert len(state.labels()) == 1


# --------------------------------------------------------------- the scoring


def _judged(fits: list[tuple[str, int, int]]) -> list[Classified]:
    return [
        Classified(item=make_item(url=f"https://e.com/{name}", title=name),
                   fit=fit, kind="core", novelty=novelty, region="global",
                   domain="other", mechanism=None, reason="")
        for name, fit, novelty in fits
    ]


def test_the_report_counts_both_kinds_of_disagreement():
    judged = _judged([("kept", 3, 3), ("dropped", 0, 0)])
    labels = {judged[0].id: {"fit": 0, "novelty": 0},
              judged[1].id: {"fit": 3, "novelty": 3}}
    report = score(judged, labels)
    assert report.false_keeps == ["kept"] and report.false_drops == ["dropped"]
    assert report.agreement == 0


def test_want_maybe_skip_becomes_something_the_rubric_can_be_scored_against():
    labels = labels_from_choices({"a": "want", "b": "skip", "c": "nonsense"})
    assert labels["a"]["fit"] == 3 and labels["b"]["fit"] == 0
    assert "c" not in labels


def test_an_empty_report_does_not_divide_by_zero():
    assert Report().agreement == 0
