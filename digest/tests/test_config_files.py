"""The four-file config in config/: validation, writing, and loading."""

from __future__ import annotations

import tomllib

import pytest

from digest.config import load, paths
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


# ------------------------------------------------------------------- loading


def test_the_four_files_become_the_runtime_config(digest_home):
    from .conftest import write_config

    write_config(
        {"schedule": {"day": "thursday"}, "output": {"minutes": 59},
         "advanced": {"max_items": 42, "search_backend": "brave"},
         "models": {"classify": "qwen3:30b"}},
        [{"name": "A feed", "url": "https://example.com/rss", "section": "world",
          "weight": 0.9, "enabled": True}],
    )
    cfg = load()
    assert cfg.run.weekday == "thursday"
    assert cfg.run.max_items == 42
    assert cfg.run.search_backend == "brave"
    # 59 minutes at 145 words a minute: minutes is the number a person has an
    # opinion about, words is what the length governor counts.
    assert cfg.run.max_words == 59 * 145
    assert cfg.models.classify == "qwen3:30b"
    assert [(s.name, s.weight) for s in cfg.sources] == [("A feed", 0.9)]
    assert cfg.lens_path == paths.lens_file() and cfg.lens_path.exists()


def test_a_missing_config_says_where_it_looked(digest_home):
    with pytest.raises(FileNotFoundError) as caught:
        load()
    assert str(paths.config_file()) in str(caught.value)


def test_the_real_config_directory_is_the_checkout(monkeypatch):
    monkeypatch.delenv("DIGEST_HOME")
    assert paths.config_dir() == paths.REPO / "config"
    assert paths.data_dir() == paths.DATA


def test_feeds_written_by_the_app_are_read_back_by_it(digest_home):
    write(paths.feeds_file(), dumps_feeds([
        {"name": "One", "url": "https://a.example/rss", "section": "world",
         "weight": 1.0, "enabled": True},
        {"name": "Two", "url": "https://b.example/rss", "section": "world",
         "weight": 1.0, "enabled": False},
    ]))
    write(paths.config_file(), dumps(validate_config({})))
    # A disabled feed stays in the file and out of the run.
    assert [s.name for s in load().sources] == ["One"]
