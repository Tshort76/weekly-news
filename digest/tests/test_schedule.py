"""Scheduler files, generated but never executed. Every platform, on any machine."""

from __future__ import annotations

import wave

import pytest

from digest import schedule
from digest.audio import _concat, _strip_id3


class Recorder:
    """Stands in for subprocess: records the calls, runs none of them."""

    def __init__(self, stdout: str = "", returncode: int = 0):
        self.calls: list[tuple[list[str], str | None]] = []
        self.stdout, self.returncode = stdout, returncode

    def __call__(self, args, stdin=None):
        self.calls.append((args, stdin))
        return type("Done", (), {"stdout": self.stdout, "returncode": self.returncode})()


# ------------------------------------------------------------------- macOS


def test_the_launch_agent_fires_on_the_day_and_hour_asked_for(tmp_path):
    plist = schedule.Launchd(Recorder(), tmp_path, tmp_path).render("friday", 7)
    assert "<key>Weekday</key><integer>5</integer>" in plist
    assert "<key>Hour</key><integer>7</integer>" in plist


@pytest.mark.parametrize("day, number", [("monday", 1), ("friday", 5), ("sunday", 7)])
def test_launchd_weekday_numbers(day, number, tmp_path):
    plist = schedule.Launchd(Recorder(), tmp_path, tmp_path).render(day, 7)
    assert f"<key>Weekday</key><integer>{number}</integer>" in plist


def test_the_launch_agent_carries_no_api_key(tmp_path):
    """The file gets copied around; a key written into it is a key that leaks."""
    plist = schedule.Launchd(Recorder(), tmp_path, tmp_path).render("friday", 7)
    for word in ("API_KEY", "sk-", "token", "secret"):
        assert word not in plist


def test_the_launch_agent_sets_path_because_launchd_has_none(tmp_path):
    """Without it the browser is not found and the PDF fails quietly."""
    plist = schedule.Launchd(Recorder(), tmp_path, tmp_path).render("friday", 7)
    assert "<key>PATH</key>" in plist and "/usr/bin" in plist


def test_installing_writes_the_plist_and_loads_it(tmp_path):
    runner = Recorder()
    backend = schedule.Launchd(runner, tmp_path, tmp_path)
    backend.install("friday", 7)
    assert backend.path.exists()
    assert ["launchctl", "load", str(backend.path)] in [c[0] for c in runner.calls]


def test_removing_unloads_and_deletes(tmp_path):
    backend = schedule.Launchd(Recorder(), tmp_path, tmp_path)
    backend.install("friday", 7)
    assert backend.remove() is True
    assert not backend.path.exists()
    assert backend.remove() is False


def test_status_before_anything_is_installed(tmp_path):
    assert schedule.Launchd(Recorder(), tmp_path, tmp_path).status().installed is False


def test_a_run_from_a_scheduler_records_itself(tmp_path):
    """--scheduled is what puts a row in the runs table for the home screen."""
    assert "--scheduled" in schedule.command()


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
    [("darwin", "launchd"), ("win32", "schtasks")],
)
def test_the_right_backend_for_the_platform(platform, expected):
    assert schedule.backend(Recorder(), platform).name == expected


# ------------------------------------------------------------------- audio


def test_joining_mp3_chunks_keeps_one_tag_at_the_front(tmp_path):
    """A second ID3 tag mid-stream makes some players label the whole file wrong."""
    tag = b"ID3\x04\x00\x00\x00\x00\x00\x03abc"
    first, second = tmp_path / "a.mp3", tmp_path / "b.mp3"
    first.write_bytes(tag + b"FRAME-ONE")
    second.write_bytes(tag + b"FRAME-TWO")
    out = tmp_path / "out.mp3"
    _concat([first, second], out)
    assert out.read_bytes() == tag + b"FRAME-ONE" + b"FRAME-TWO"


def test_an_untagged_chunk_is_passed_through_whole():
    assert _strip_id3(b"\xff\xfbFRAME") == b"\xff\xfbFRAME"


def test_wav_chunks_are_joined_through_the_header_not_appended(tmp_path):
    """Piper writes WAV, and two WAV files appended is one WAV file plus noise."""
    parts = []
    for name in ("a", "b"):
        path = tmp_path / f"{name}.wav"
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(22050)
            handle.writeframes(b"\x00\x01" * 100)
        parts.append(path)
    out = tmp_path / "out.wav"
    _concat(parts, out)
    with wave.open(str(out), "rb") as handle:
        assert handle.getnframes() == 200
