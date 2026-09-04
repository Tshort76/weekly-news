"""The four-file config: validation, migration, and the import from a checkout."""

from __future__ import annotations

import sqlite3
import tomllib
from pathlib import Path

import pytest

from digest.config import legacy, load_installed, paths
from digest.config.schema import ConfigError, validate_config, validate_feeds
from digest.config.write import dumps, dumps_feeds, write


def test_a_misspelled_setting_is_reported_rather_than_ignored():
    """The whole reason this replaced raw.get(): max_wrods silently meant 8500."""
    with pytest.raises(ConfigError) as caught:
        validate_config({"output": {"minuets": 40}})
    assert "output.minuets — not a setting the app knows" in str(caught.value)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ({"models": {"provider": "openai"}}, "models.provider — must be one of"),
        ({"output": {"minutes": "an hour"}}, "output.minutes — expected int"),
        ({"output": {"minutes": 2}}, "output.minutes — must be at least 5"),
        ({"output": {"minutes": 900}}, "output.minutes — must be at most 240"),
        ({"schedule": {"day": "someday"}}, "schedule.day — must be one of"),
        ({"output": {"html": "yes"}}, "output.html — expected true or false"),
        ({"advanced": {"max_items": True}}, "advanced.max_items — expected int"),
    ],
)
def test_a_bad_value_is_named_by_its_path(raw, expected):
    with pytest.raises(ConfigError) as caught:
        validate_config(raw)
    assert expected in str(caught.value)


def test_every_problem_is_reported_at_once_not_one_per_run():
    with pytest.raises(ConfigError) as caught:
        validate_config({"models": {"provider": "openai"}, "schedule": {"hour": 99}})
    assert len(caught.value.problems) == 2


def test_an_empty_config_is_valid_and_fully_defaulted():
    filled = validate_config({})
    assert filled["models"]["provider"] == "ollama"
    assert filled["advanced"]["search_backend"] == "duckduckgo"


def test_a_feed_without_a_url_is_refused():
    with pytest.raises(ConfigError) as caught:
        validate_feeds({"feed": [{"name": "Nameless"}]})
    assert "feed 1: url — must not be empty" in str(caught.value)


def test_a_feed_keeps_its_weight_and_defaults_the_rest():
    feeds = validate_feeds({"feed": [{"url": "https://e.com/rss", "weight": 0.7}]})
    assert feeds[0]["weight"] == 0.7
    assert feeds[0]["enabled"] is True
    assert feeds[0]["name"] == "https://e.com/rss"


def test_writing_then_reading_a_config_round_trips():
    text = dumps({"schema_version": 1, "models": {"provider": "ollama"},
                  "delivery": {"drive": {"enabled": False}}})
    assert tomllib.loads(text)["delivery"]["drive"]["enabled"] is False


def test_a_rewrite_keeps_one_generation_of_backup(tmp_path):
    path = tmp_path / "config.toml"
    write(path, "first = 1\n")
    write(path, "second = 2\n")
    assert path.with_suffix(".toml.bak").read_text() == "first = 1\n"


# ------------------------------------------------------------------ importing


LEGACY = """
[run]
weekday = "thursday"
max_words = 8500
max_items = 42
output_dir = "~/elsewhere"
search_backend = "brave"

[models]
provider = "ollama"
classify = "qwen3:30b"
synthesize = "gemma3:27b"

[tts]
enabled = true

[pdf]
engine = "html2pdf"

[[sources]]
name = "A feed"
url = "https://example.com/rss"
section = "world"
weight = 0.9
"""


def test_a_checkout_config_becomes_the_four_files(digest_home, tmp_path):
    source = tmp_path / "digest.toml"
    source.write_text(LEGACY)
    report = legacy.import_legacy(source)

    assert report["feeds"] == 1
    assert paths.config_file().exists() and paths.feeds_file().exists()
    assert paths.lens_file().exists() and paths.lens_spec_file().exists()

    cfg = load_installed()
    assert cfg.run.weekday == "thursday"
    assert cfg.run.max_items == 42
    assert cfg.run.search_backend == "brave"
    assert cfg.models.classify == "qwen3:30b"
    assert cfg.tts.enabled is True
    assert [s.name for s in cfg.sources] == ["A feed"]
    assert cfg.sources[0].weight == 0.9


def test_a_length_in_words_becomes_a_length_in_minutes_and_back(digest_home, tmp_path):
    source = tmp_path / "digest.toml"
    source.write_text(LEGACY)
    legacy.import_legacy(source)
    # 8500 words is 59 minutes at 145 a minute, and 59 minutes is 8555 words.
    # The round trip is lossy by design: minutes is the number a person has an
    # opinion about, and a 55-word difference is not one.
    assert abs(load_installed().run.max_words - 8500) < 145


def test_html2pdf_does_not_survive_the_import(digest_home, tmp_path):
    """It is a script in one person's ~/.local/bin. An install cannot assume it."""
    source = tmp_path / "digest.toml"
    source.write_text(LEGACY)
    legacy.import_legacy(source)
    assert load_installed().pdf.engine == "chrome"


def test_the_lens_crosses_over_as_bytes_not_as_a_recompile(digest_home, tmp_path):
    """Phase 0: rewrapping the same words moves the classifier ten points."""
    source = tmp_path / "digest.toml"
    source.write_text(LEGACY)
    legacy.import_legacy(source)
    packaged = Path(__file__).resolve().parent.parent / "prompts" / "rubric.md"
    assert paths.lens_file().read_text() == packaged.read_text()


def test_what_we_have_already_seen_is_copied_to_the_new_data_directory(
    digest_home, tmp_path, monkeypatch
):
    """Without this, the first run after upgrading republishes last week."""
    old = tmp_path / "old-data"
    old.mkdir()
    conn = sqlite3.connect(old / "state.db")
    conn.execute("CREATE TABLE seen (id TEXT)")
    conn.execute("INSERT INTO seen VALUES ('abc')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(paths, "LEGACY_DATA", old)
    monkeypatch.setattr(legacy.paths, "LEGACY_DATA", old)

    source = tmp_path / "digest.toml"
    source.write_text(LEGACY)
    report = legacy.import_legacy(source)

    assert report["database_copied"] is True
    moved = sqlite3.connect(paths.data_dir() / "state.db")
    assert [r[0] for r in moved.execute("SELECT id FROM seen")] == ["abc"]
    moved.close()


def test_an_import_never_removes_the_file_it_read(digest_home, tmp_path):
    source = tmp_path / "digest.toml"
    source.write_text(LEGACY)
    legacy.import_legacy(source)
    assert source.exists()


def test_feeds_written_by_the_app_are_read_back_by_it(digest_home):
    write(paths.feeds_file(), dumps_feeds([
        {"name": "One", "url": "https://a.example/rss", "section": "world",
         "weight": 1.0, "enabled": True},
        {"name": "Two", "url": "https://b.example/rss", "section": "world",
         "weight": 1.0, "enabled": False},
    ]))
    write(paths.config_file(), dumps(validate_config({})))
    # A disabled feed stays in the file and out of the run.
    assert [s.name for s in load_installed().sources] == ["One"]
