"""Scheduler files, generated but never executed. Every platform, on any machine."""

from __future__ import annotations

import types

import pytest

from digest import schedule


class Recorder:
    """Stands in for subprocess: records the calls, runs none of them."""

    def __init__(self, stdout: str = "", returncode: int = 0):
        self.calls: list[tuple[list[str], str | None]] = []
        self.stdout, self.returncode = stdout, returncode

    def __call__(self, args, stdin=None):
        self.calls.append((args, stdin))
        return type("Done", (), {"stdout": self.stdout, "returncode": self.returncode})()


# ------------------------------------------------------------------- macOS


def test_a_mac_writes_no_launch_agent(tmp_path):
    """The menu-bar plugin is the scheduler. A launch agent beside it ran W39
    that nobody was told about."""
    runner = Recorder()
    schedule.MenuBar(runner, tmp_path).install("friday", 7)
    assert list(tmp_path.iterdir()) == []
    assert runner.calls == []


def test_saving_a_schedule_retires_an_old_launch_agent(tmp_path):
    runner = Recorder()
    backend = schedule.MenuBar(runner, tmp_path)
    backend.leftover.write_text("<plist/>", encoding="utf-8")
    assert "old launch agent" in backend.status().detail
    backend.install("friday", 7)
    assert not backend.leftover.exists()
    assert ["launchctl", "unload", str(backend.leftover)] in [c[0] for c in runner.calls]
    assert backend.status().detail == "run by the SwiftBar menu-bar plugin"


def test_the_schedule_is_recorded_where_the_plugin_reads_it(digest_home):
    """The plugin reads [schedule] from config.toml; turning it off keeps the hour."""
    import tomllib

    from digest.config import paths

    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text("", encoding="utf-8")
    schedule.record(True, "sunday", 18)
    schedule.record(False)
    saved = tomllib.loads(paths.config_file().read_text(encoding="utf-8"))["schedule"]
    assert saved == {"enabled": False, "day": "sunday", "hour": 18}


def test_a_run_from_a_scheduler_records_itself(tmp_path):
    """--scheduled is what puts a row in the runs table for the home screen."""
    assert "--scheduled" in schedule.command()


def test_the_scheduler_never_picks_up_a_stranger_named_digest(tmp_path, monkeypatch):
    """Homebrew's `nss` ships a `digest` too, and PATH order is not ours to fix.

    A scheduled job that runs the wrong binary fails once a week where nobody
    is watching, so the console script is located beside our own interpreter
    rather than searched for by name.
    """
    impostor = tmp_path / "elsewhere"
    impostor.mkdir()
    (impostor / "digest").write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", str(impostor))

    interpreter = tmp_path / "venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    monkeypatch.setattr(schedule.sys, "executable", str(interpreter))

    # No console script beside the interpreter: fall back to -m digest, never
    # to the impostor that PATH would have handed us.
    assert schedule.command()[:3] == [str(interpreter), "-m", "digest"]

    # One appears beside it: that is ours, and it wins.
    (interpreter.parent / "digest").touch()
    assert schedule.command()[0] == str(interpreter.parent / "digest")


# ------------------------------------------------------------------- Linux


def test_the_timer_survives_a_machine_that_was_asleep(tmp_path):
    _, timer = schedule.Systemd(Recorder(), tmp_path).render("friday", 7)
    assert "Persistent=true" in timer
    assert "OnCalendar=Fri 07:00" in timer


def test_the_unit_sets_path_for_the_same_reason_launchd_does(tmp_path):
    unit, _ = schedule.Systemd(Recorder(), tmp_path).render("friday", 7)
    assert "Environment=PATH=" in unit and "ExecStart=" in unit


def test_installing_a_timer_enables_it(tmp_path):
    runner = Recorder()
    schedule.Systemd(runner, tmp_path).install("friday", 7)
    assert (tmp_path / "digest.timer").exists()
    assert ["systemctl", "--user", "enable", "--now", "digest.timer"] in [
        c[0] for c in runner.calls
    ]


def test_cron_owns_one_line_and_leaves_the_rest_alone():
    runner = Recorder(stdout="0 3 * * * backup.sh\n")
    table = schedule.Cron(runner).install("friday", 7)
    assert "0 3 * * * backup.sh" in table
    assert table.count("# weekly-digest") == 1


def test_installing_cron_twice_does_not_leave_two_lines():
    existing = "0 7 * * 4 old  # weekly-digest\n0 3 * * * backup.sh\n"
    table = schedule.Cron(Recorder(stdout=existing)).install("friday", 7)
    assert table.count("# weekly-digest") == 1
    assert "* * 4 old" not in table


# ----------------------------------------------------------------- Windows


def test_the_scheduled_task_asks_for_the_right_day_and_time():
    arguments = schedule.Schtasks(Recorder()).arguments("friday", 7)
    assert arguments[:4] == ["schtasks", "/Create", "/F", "/TN"]
    assert "FRI" in arguments and "07:00" in arguments


def test_a_path_with_a_space_in_it_is_quoted_for_task_scheduler(monkeypatch):
    """Windows user directories have spaces in them more often than not."""
    monkeypatch.setattr(
        schedule, "command",
        lambda: [r"C:\Program Files\digest.exe", "run", "--scheduled"],
    )
    arguments = schedule.Schtasks(Recorder()).arguments("friday", 7)
    command = arguments[arguments.index("/TR") + 1]
    assert command == r'"C:\Program Files\digest.exe" run --scheduled'


@pytest.mark.parametrize(
    "platform, expected",
    [("darwin", "menu bar"), ("win32", "schtasks")],
)
def test_the_right_backend_for_the_platform(platform, expected):
    assert schedule.backend(Recorder(), platform).name == expected


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
