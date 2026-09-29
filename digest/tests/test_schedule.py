"""Recording the schedule, and the notification a finished run posts."""

from __future__ import annotations

import types


from digest import schedule
from digest.config import load


# ------------------------------------------------------------------ recording


def test_the_schedule_is_recorded_where_the_plugin_reads_it(digest_home):
    from digest.health import _scheduled_at

    from .conftest import write_config

    write_config()
    schedule.record("sunday", 18)
    assert _scheduled_at(load()) == {"day": "sunday", "hour": 18}


# ------------------------------------------------ telling you it is finished


def test_a_notification_carries_the_title_and_the_body_on_a_mac():
    args = schedule.notify_arguments("Weekly digest ready", "digest-2026-09-07 — 60 items")
    assert args[:2] == ["osascript", "-e"]
    assert "digest-2026-09-07 — 60 items" in args[2]
    assert "Weekly digest ready" in args[2]


def test_a_quote_in_a_headline_cannot_become_applescript():
    args = schedule.notify_arguments("t", 'he said "no" \\ ever', platform="darwin")
    # Both escapes present, and the script still closes its own strings.
    assert '\\"no\\"' in args[2]
    assert "\\\\" in args[2]


def test_a_newline_is_flattened_because_a_notification_is_one_line():
    args = schedule.notify_arguments("t", "first\nsecond", platform="darwin")
    assert "first second" in args[2]


def test_windows_gets_no_notification_rather_than_a_broken_one():
    assert schedule.notify_arguments("t", "b", platform="win32") is None


def test_notify_swallows_a_broken_runner_because_a_run_matters_more():
    def explode(args):
        raise OSError("no osascript here")

    assert schedule.notify("t", "b", runner=explode, platform="darwin") is False


def test_notify_reports_true_when_the_command_ran():
    seen = []
    assert schedule.notify("t", "b", runner=seen.append, platform="darwin") is True
    assert seen and seen[0][0] == "osascript"


def _capture(monkeypatch):
    """Collect (title, body) instead of putting anything on screen."""
    sent = []
    monkeypatch.setattr(schedule, "notify", lambda t, b: sent.append((t, b)) or True)
    return sent


def test_a_finished_run_says_the_filename_the_count_and_the_folder(monkeypatch, tmp_path):
    from digest.__main__ import _notify_finished

    sent = _capture(monkeypatch)
    result = types.SimpleNamespace(
        files={"md": tmp_path / "digest-2026-09-07.md"},
        edition=types.SimpleNamespace(entries=[1, 2, 3]),
    )
    _notify_finished(result, "2026-W37", None)
    title, body = sent[0]
    assert title == "Weekly digest ready"
    assert "digest-2026-09-07" in body and "3 items" in body and str(tmp_path) in body


def test_a_failed_run_names_the_error_because_that_is_the_point(monkeypatch):
    from digest.__main__ import _notify_finished

    sent = _capture(monkeypatch)
    _notify_finished(None, "2026-W37", RuntimeError("ollama is not running"))
    title, body = sent[0]
    assert "failed" in title
    assert "RuntimeError: ollama is not running" in body


def test_a_quiet_week_says_so_rather_than_naming_a_file_that_says_nothing(monkeypatch):
    from digest.__main__ import _notify_finished

    sent = _capture(monkeypatch)
    _notify_finished(types.SimpleNamespace(files={}, edition=None), "2026-W37", None)
    assert "nothing met the bar" in sent[0][1]
