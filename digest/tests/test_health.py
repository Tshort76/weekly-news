"""The dashboard: the verdict rule, and that a run records itself either way.

The verdict rule is the one judgement on the page, so it is tested as a table
rather than as prose: amber and red are separated by whether the pipeline
recovered, not by how alarming the message reads.
"""

from __future__ import annotations

import pytest

from digest import health
from digest.state import State


def event(level="WARNING", kind="note", message="something", **extra):
    return {"level": level, "kind": kind, "message": message,
            "at": "2026-09-11T07:38:02+00:00", "subject": extra.get("subject"),
            "n": extra.get("n"), "ms": extra.get("ms"), "detail": extra.get("detail")}


def a_run(**over):
    run = {"week": "2026-W37", "started": "2026-09-11T07:00:00+00:00",
           "finished": "2026-09-11T07:51:00+00:00", "status": "ok",
           "fetched": 306, "selected": 60, "entries": 60, "words": 8397,
           "note": None, "dry": 0}
    run.update(over)
    return run


class Edition:
    def __init__(self, partial=False):
        self.partial = partial
        self.week = "2026-W37"


# ------------------------------------------------------------- the verdict


def test_a_clean_run_is_green():
    found = health.verdict(a_run(), [], Edition(), job_running=False)
    assert found.state == "green"
    assert found.headline == "All good"


def test_green_says_almost_nothing():
    """Three sentences of reassurance trains you to skip the banner."""
    found = health.verdict(a_run(), [], Edition(), job_running=False)
    assert found.summary == ""
    assert found.evidence == ""
    assert "8,397 words" in found.meta


def test_a_retry_that_succeeded_is_amber_not_red():
    """The line is whether the pipeline recovered, not how scary the word is."""
    events = [event(message="TimeoutError on attempt 1/5 for gemma3:27b, waiting 10s",
                    kind="call")]
    found = health.verdict(a_run(), events, Edition(), job_running=False)
    assert found.state == "amber"


def test_a_partial_edition_is_red_even_though_a_briefing_was_published():
    """Entries were dropped and nobody was told. That is the case for red."""
    found = health.verdict(a_run(status="partial"), [], Edition(partial=True),
                           job_running=False)
    assert found.state == "red"
    assert "[PARTIAL]" in found.summary


def test_an_error_is_red_and_carries_the_lines_that_led_to_it():
    events = [
        event(message="TimeoutError on attempt 4/5 for gemma3:27b, waiting 80s", kind="call"),
        event(level="ERROR", kind="call",
              message="gemma3:27b gave up after 5 attempts: [Errno 61] Connection refused"),
    ]
    found = health.verdict(a_run(status="failed"), events, Edition(), job_running=False)
    assert found.state == "red"
    assert "attempt 4/5" in found.evidence, "the warnings before the error are the diagnosis"
    assert "Connection refused" in found.evidence


def test_red_says_what_the_error_means_and_what_to_do():
    """A red banner that only repeats the error is a warning with a colour."""
    events = [event(level="ERROR", message="[Errno 61] Connection refused")]
    found = health.verdict(a_run(status="failed"), events, Edition(), job_running=False)
    assert "nothing was listening" in found.action
    assert "re-run" in found.action


def test_a_rate_limit_gets_different_advice_than_a_dead_server():
    events = [event(level="ERROR", message="RateLimitError: quota exhausted")]
    found = health.verdict(a_run(status="failed"), events, Edition(), job_running=False)
    assert "quota" in found.action and "local model" in found.action


def test_a_run_that_never_finished_is_red():
    """The state the whole recording layer exists to make visible."""
    run = a_run(status="running", finished=None)
    found = health.verdict(run, [event(kind="stage", subject="classify", level="INFO")],
                           Edition(), job_running=False)
    assert found.state == "red"
    assert "classify" in found.summary


def test_a_run_still_going_is_not_called_dead():
    run = a_run(status="running", finished=None)
    assert health.did_not_finish(run, job_running=True) is False


def test_a_feed_that_failed_three_runs_running_is_red():
    history = {"IEEE Spectrum": [{"n": 0, "failed": True}] * 3}
    found = health.verdict(a_run(), [], Edition(), job_running=False,
                           feed_history=history)
    assert found.state == "red"
    assert "IEEE Spectrum" in found.summary


def test_a_feed_that_failed_once_is_not():
    history = {"IEEE Spectrum": [{"n": 11, "failed": False},
                                 {"n": 0, "failed": True}]}
    found = health.verdict(a_run(), [], Edition(), job_running=False,
                           feed_history=history)
    assert found.state == "green"


def test_repeated_warnings_collapse_to_one_row_with_a_count():
    """The W36 log had 60 identical rate-limit lines. That is one finding."""
    events = [event(message="RateLimitError, waiting 60s")] * 4
    found = health.verdict(a_run(), events, Edition(), job_running=False)
    rows = [f for f in found.findings if "RateLimit" in f.text]
    assert len(rows) == 1
    assert rows[0].detail == "×4"


# ------------------------------------------------- a run records itself


def _dies_at_fetch(monkeypatch, error):
    """Break the first stage, so the run dies with the stages barely started.

    Failing at fetch rather than at a model call is deliberate: with no feeds
    configured a run completes without ever calling a model, so a broken client
    proves nothing.
    """
    def boom(*args, **kwargs):
        raise error("boom")

    monkeypatch.setattr("digest.ingest.ingest", boom)


def test_every_run_writes_a_row_not_just_a_scheduled_one(digest_home, tmp_path, monkeypatch):
    """It used to be the caller's job and only --scheduled did it, so the table
    was empty on a machine where weeks were run by hand or from the browser."""
    from digest import pipeline
    from digest.config import Config

    _dies_at_fetch(monkeypatch, ZeroDivisionError)
    cfg = Config(state_dir=tmp_path)
    with State(cfg.db_path) as state:
        with pytest.raises(ZeroDivisionError):
            pipeline.run(cfg, state, week="2026-W37")
        run = state.recent_runs()[0]
    assert run["status"] == "failed"
    assert "ZeroDivisionError" in run["note"]
    assert run["finished"], "a run that died still has to be finished off"


def test_a_cancelled_run_says_cancelled(digest_home, tmp_path, monkeypatch):
    from digest import pipeline
    from digest.config import Config

    _dies_at_fetch(monkeypatch, pipeline.Cancelled)
    cfg = Config(state_dir=tmp_path)
    with State(cfg.db_path) as state:
        with pytest.raises(pipeline.Cancelled):
            pipeline.run(cfg, state, week="2026-W37")
        assert state.recent_runs()[0]["status"] == "cancelled"


def test_a_dry_run_is_marked_as_one(digest_home, tmp_path, monkeypatch):
    """Dry runs were the owner's entire history before 2026-09-06."""
    from digest import pipeline
    from digest.config import Config

    _dies_at_fetch(monkeypatch, ZeroDivisionError)
    cfg = Config(state_dir=tmp_path)
    with State(cfg.db_path) as state:
        with pytest.raises(ZeroDivisionError):
            pipeline.run(cfg, state, week="2026-W37", dry_run=True)
        assert state.recent_runs()[0]["dry"] == 1


def test_a_run_that_finishes_says_ok_and_carries_its_counts(digest_home, tmp_path, monkeypatch):
    from digest import pipeline
    from digest.config import Config

    cfg = Config(state_dir=tmp_path)
    with State(cfg.db_path) as state:
        pipeline.run(cfg, state, week="2026-W37")
        run = state.recent_runs()[0]
    assert run["status"] == "ok"
    assert run["fetched"] == 0  # no feeds configured, but the row is still written


# ------------------------------------------- the warnings actually land


def test_a_warning_anywhere_becomes_a_row_with_no_call_site_change(digest_home, tmp_path):
    """The whole argument for a handler rather than a threaded callback."""
    import logging

    from digest import logging_setup

    db = tmp_path / "state.db"
    with State(db) as state:
        started = state.start_run("2026-W37")
    logging_setup.setup(tmp_path / "logs", "2026-W37", db_path=db)
    try:
        logging_setup.database_handler().started = started
        logging.getLogger("digest.somewhere.new").warning("nobody wired this up")
    finally:
        logging.getLogger("digest").handlers.clear()

    with State(db) as state:
        rows = state.events("2026-W37", started, min_level="WARNING")
    assert [r["message"] for r in rows] == ["nobody wired this up"]


def test_ordinary_chatter_is_not_recorded(digest_home, tmp_path):
    """400 rows a run is fine; every DEBUG line of a 300-item fetch is not."""
    import logging

    from digest import logging_setup

    db = tmp_path / "state.db"
    with State(db) as state:
        started = state.start_run("2026-W37")
    logging_setup.setup(tmp_path / "logs", "2026-W37", db_path=db)
    try:
        logging_setup.database_handler().started = started
        logging.getLogger("digest.noise").info("just chatter")
    finally:
        logging.getLogger("digest").handlers.clear()

    with State(db) as state:
        assert state.events("2026-W37", started) == []
