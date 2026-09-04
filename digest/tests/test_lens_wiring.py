"""What the lens now drives: the classifier's prompt, the stored kinds, the files."""

from __future__ import annotations

import json
import sqlite3

import pytest

from digest.classify import classify_batch, enum_line
from digest.config import Config
from digest.lens import presets, store
from digest.lens.compile import compile_lens
from digest.state import SCHEMA_VERSION, State

from .conftest import make_item

# The three lines that used to be literals in classify.md. Templating them is the
# one phase-1 change that could quietly make the product worse, so this is
# checked by comparing the rendered prompt rather than by scoring a model: if the
# text is identical, there is nothing left to measure.
BEFORE_TEMPLATING = (
    '  "kind": "architecture" | "contest" | "neither",',
    '  "region": "east_asia" | "south_asia" | "europe" | "uk" | "us" | "mena" | '
    '"africa" | "latam" | "global",',
    '  "domain": "finance" | "trade" | "industry" | "state" | "tech" | "energy" | '
    '"demography" | "security" | "other",',
)


class Recorder:
    def __init__(self):
        self.prompt = ""
        self.schema = None

    def complete_json(self, *, stage, prompt, max_tokens, schema=None):
        self.prompt, self.schema = prompt, schema
        return []


def test_the_rendered_prompt_is_what_it_was_before_the_enums_were_templated():
    client = Recorder()
    classify_batch([make_item()], Config(), client)
    for line in BEFORE_TEMPLATING:
        assert line in client.prompt


def test_the_response_schema_offers_the_lens_own_words():
    client = Recorder()
    classify_batch([make_item()], Config(), client)
    properties = client.schema["items"]["properties"]
    assert properties["kind"]["enum"] == ["architecture", "contest", "neither"]
    assert "east_asia" in properties["region"]["enum"]


def test_the_prompt_carries_the_lens_not_the_packaged_rubric(tmp_path):
    mine = tmp_path / "lens.md"
    mine.write_text("LENS: whatever I say it is.\n")
    client = Recorder()
    classify_batch([make_item()], Config(lens_path=mine), client)
    assert "whatever I say it is" in client.prompt


def test_enum_line_quotes_and_separates():
    assert enum_line(("a", "b")) == '"a" | "b"'


# ------------------------------------------------------------------- storage


def test_a_preset_is_installed_as_the_bytes_that_were_measured(tmp_path):
    """Recompiling would rewrap it, and phase 0 measured what that costs."""
    store.install_preset("architecture-of-rule", tmp_path)
    packaged = presets.markdown("architecture-of-rule")
    assert (tmp_path / "lens.md").read_text() == packaged


def test_an_untouched_lens_does_not_look_hand_edited(tmp_path):
    store.install_preset("architecture-of-rule", tmp_path)
    assert store.load(tmp_path).hand_edited is False


def test_a_hand_edit_is_noticed_so_the_form_can_warn_before_it_overwrites(tmp_path):
    store.install_preset("architecture-of-rule", tmp_path)
    path = tmp_path / "lens.md"
    path.write_text(path.read_text() + "\nAnd one more rule I added myself.\n")
    stored = store.load(tmp_path)
    assert stored.hand_edited is True
    assert stored.spec is not None  # the form can still be shown, with a banner


def test_saving_the_form_records_the_hash_of_what_it_wrote(tmp_path):
    spec = presets.load("architecture-of-rule")
    toml = presets.spec_path("architecture-of-rule").read_text()
    store.save(spec, toml, tmp_path)
    assert store.load(tmp_path).hand_edited is False


# ----------------------------------------------------------------- database


def test_a_fresh_database_is_at_the_current_schema_version(tmp_path):
    with State(tmp_path / "state.db") as state:
        assert state.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_a_stored_week_keeps_its_meaning_after_the_kind_slots_were_renamed(tmp_path):
    """`audit` re-runs selection over stored rows, so an old week must still cap."""
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """CREATE TABLE classified (id TEXT NOT NULL, week TEXT NOT NULL,
           fit INTEGER, kind TEXT, novelty INTEGER, region TEXT, domain TEXT,
           mechanism TEXT, reason TEXT, json TEXT NOT NULL, PRIMARY KEY (id, week));"""
    )
    conn.execute(
        "INSERT INTO classified VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("x", "2026-W01", 2, "contest", 1, "us", "state", "m", "r",
         json.dumps({"kind": "contest", "fit": 2})),
    )
    conn.commit()
    conn.close()

    with State(path) as state:
        row = state.conn.execute("SELECT kind, json FROM classified").fetchone()
        assert row["kind"] == "adjacent"
        assert json.loads(row["json"])["kind"] == "adjacent"


def test_a_run_is_recorded_so_the_home_screen_needs_no_log_parsing(tmp_path):
    with State(tmp_path / "state.db") as state:
        started = state.start_run("2026-W36")
        state.finish_run("2026-W36", started, "ok", fetched=286, entries=44)
        run = state.recent_runs()[0]
        assert run["status"] == "ok" and run["fetched"] == 286


# --------------------------------------------------------------- the presets


@pytest.mark.parametrize("name", presets.available())
def test_every_preset_loads_and_compiles_to_the_rubric_shape(name):
    out = compile_lens(presets.load(name))
    for head in ("LENS:", "Score FIT", "3 — ", "0 — ", "KIND:", "NOVELTY", "MECHANISM:"):
        assert head in out


@pytest.mark.parametrize("name", presets.available())
def test_every_preset_ships_feeds_that_carry_its_kind_of_story(name):
    """A lens with no feeds has a quiet week every week."""
    assert presets.load(name).feeds


@pytest.mark.parametrize("name", presets.available())
def test_every_preset_names_the_enums_the_pipeline_is_wired_to(name):
    spec = presets.load(name)
    assert len(spec.regions) >= 2 and len(spec.domains) >= 2
    assert spec.kinds.core and spec.kinds.adjacent


def test_only_a_preset_with_a_measured_rubric_is_called_calibrated():
    """The flag reads the file the app installs, so it cannot drift from it."""
    assert presets.calibrated("architecture-of-rule") is True
    uncalibrated = [n for n in presets.available() if not presets.calibrated(n)]
    assert uncalibrated  # the honest state today, and the app says so


@pytest.mark.parametrize(
    "name", [n for n in presets.available() if presets.calibrated(n)]
)
def test_a_calibrated_preset_says_the_same_thing_as_its_spec(name):
    """Catches a spec edited after the measurement, leaving a stale scored file."""
    measured = presets.markdown(name)
    assert " ".join(compile_lens(presets.load(name)).split()) == " ".join(measured.split())


def test_every_calibrated_preset_can_show_the_labels_it_was_scored_against():
    """A score nobody can reproduce is a claim, not a measurement.

    The original's labelled set predates presets and lives in the test fixtures,
    where `eval_rubric.py` and the README both still point at it; a preset added
    since ships its labels beside its spec.
    """
    import json

    from digest.calibrate import shipped_labels

    for name in presets.available():
        if not presets.calibrated(name):
            continue
        sidecar = presets.DIRECTORY / f"{name}.labels.json"
        if sidecar.exists():
            payload = json.loads(sidecar.read_text())
            assert payload["labels"]
            assert payload["measured"]["of"] == len(payload["labels"])
        else:
            assert name == presets.DEFAULT
            assert shipped_labels()[1]
