"""Run the digest once a week without anyone remembering to.

Three platforms, three schedulers, one idea: write the operating system's own
file, hand it to the operating system's own command, and never invent a daemon
of our own.

Every backend takes a `runner` for the command it would execute, so the tests
assert on the plist, the unit file and the argument list without any of them
being run. That is the whole reason this is testable on a Mac.

Two behaviours are carried over from the launchd plist that was in the
repository, and they are the two that were learned the hard way:

- **No API key in the file.** A scheduler file is a file on disk that gets
  copied around and pasted into issues. The key comes from the credential store
  at run time.
- **PATH has to be set explicitly.** A scheduled job starts with a bare
  environment and reads no shell profile, so without this the browser and the
  audio tools are simply not found — and the run half-succeeds, quietly, which
  is worse than failing.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("digest.schedule")

LABEL = "io.digest.weekly"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# launchd numbers Sunday 0; systemd and Task Scheduler want names.
LAUNCHD_WEEKDAY = {name: n for n, name in enumerate(WEEKDAYS, start=1)}
SCHTASKS_WEEKDAY = {name: name[:3].upper() for name in WEEKDAYS}

PATH_HINT = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"


def _default_runner(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, input=stdin, capture_output=True, text=True, check=False)


def command() -> list[str]:
    """How to invoke this app from a scheduler.

    Deliberately **not** `shutil.which("digest")`. `digest` is not a rare name —
    Homebrew's `nss` package installs one, and on a machine where that comes
    first on PATH the search returns an unrelated binary. Writing that into a
    launchd plist produces a weekly job that runs the wrong program and fails
    where nobody is looking, which is the worst place to be wrong.

    So the console script is taken from beside the interpreter that is running
    us, where an installed tool puts it, and the fallback is that same
    interpreter with `-m digest`. Both are this app by construction rather than
    by name, and both are absolute, because a scheduler has no PATH worth
    trusting.
    """
    here = Path(sys.executable).parent
    for name in ("digest", "digest.exe"):
        console = here / name
        if console.exists():
            return [str(console), "run", "--scheduled", "--html"]
    return [sys.executable, "-m", "digest", "run", "--scheduled", "--html"]


@dataclass
class Status:
    installed: bool
    detail: str = ""
    when: str = ""


class Backend:
    name = "none"

    def __init__(self, runner=None):
        self.run = runner or _default_runner

    def install(self, day: str, hour: int) -> str: ...
    def remove(self) -> bool: ...
    def status(self) -> Status: ...


# ------------------------------------------------------------------ macOS


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{arguments}
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>{weekday}</integer>
    <key>Hour</key><integer>{hour}</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <!-- No API key here on purpose: this file gets copied around, and a key
       written into it is a key that leaks. The run reads it from the system
       credential store instead.

       PATH is set because launchd starts with a bare environment and reads no
       shell profile. Without it the browser used for PDFs is not found and
       that part of the run fails quietly. -->
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>{path}</string>
{home}  </dict>
  <key>StandardOutPath</key><string>{log_out}</string>
  <key>StandardErrorPath</key><string>{log_err}</string>
</dict>
</plist>
"""


class Launchd(Backend):
    name = "launchd"

    def __init__(self, runner=None, agents: Path | None = None, logs: Path | None = None):
        super().__init__(runner)
        self.agents = agents or Path.home() / "Library" / "LaunchAgents"
        self.logs = logs or Path.home() / "Library" / "Logs"

    @property
    def path(self) -> Path:
        return self.agents / f"{LABEL}.plist"

    def render(self, day: str, hour: int) -> str:
        arguments = "\n".join(f"    <string>{a}</string>" for a in command())
        digest_home = os.environ.get("DIGEST_HOME", "")
        home = (
            f"    <key>DIGEST_HOME</key><string>{digest_home}</string>\n"
            if digest_home else ""
        )
        return PLIST.format(
            label=LABEL, arguments=arguments, weekday=LAUNCHD_WEEKDAY[day], hour=hour,
            path=f"{Path.home()}/.local/bin:{PATH_HINT}", home=home,
            log_out=self.logs / "digest.out.log", log_err=self.logs / "digest.err.log",
        )

    def install(self, day: str, hour: int) -> str:
        self.agents.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.render(day, hour), encoding="utf-8")
        self.run(["launchctl", "unload", str(self.path)])
        self.run(["launchctl", "load", str(self.path)])
        return str(self.path)

    def remove(self) -> bool:
        if not self.path.exists():
            return False
        self.run(["launchctl", "unload", str(self.path)])
        self.path.unlink()
        return True

    def status(self) -> Status:
        if not self.path.exists():
            return Status(False, "no launch agent installed")
        found = self.run(["launchctl", "list", LABEL])
        loaded = getattr(found, "returncode", 1) == 0
        return Status(True, "loaded" if loaded else "installed but not loaded",
                      str(self.path))


# ------------------------------------------------------------------ Linux


UNIT = """[Unit]
Description=Weekly digest

[Service]
Type=oneshot
Environment=PATH={path}
{home}ExecStart={exec_start}
"""

TIMER = """[Unit]
Description=Weekly digest

[Timer]
OnCalendar={day} {hour:02d}:00
# The machine is not always awake on a Friday morning. Persistent runs the job
# once at the next boot rather than skipping the week entirely.
Persistent=true

[Install]
WantedBy=timers.target
"""


class Systemd(Backend):
    name = "systemd"

    def __init__(self, runner=None, units: Path | None = None):
        super().__init__(runner)
        self.units = units or Path.home() / ".config" / "systemd" / "user"

    def render(self, day: str, hour: int) -> tuple[str, str]:
        digest_home = os.environ.get("DIGEST_HOME", "")
        home = f"Environment=DIGEST_HOME={digest_home}\n" if digest_home else ""
        service = UNIT.format(
            path=f"{Path.home()}/.local/bin:{PATH_HINT}", home=home,
            exec_start=" ".join(command()),
        )
        return service, TIMER.format(day=day.capitalize()[:3], hour=hour)

    def install(self, day: str, hour: int) -> str:
        self.units.mkdir(parents=True, exist_ok=True)
        service, timer = self.render(day, hour)
        (self.units / "digest.service").write_text(service, encoding="utf-8")
        (self.units / "digest.timer").write_text(timer, encoding="utf-8")
        self.run(["systemctl", "--user", "daemon-reload"])
        self.run(["systemctl", "--user", "enable", "--now", "digest.timer"])
        return str(self.units / "digest.timer")

    def remove(self) -> bool:
        timer = self.units / "digest.timer"
        if not timer.exists():
            return False
        self.run(["systemctl", "--user", "disable", "--now", "digest.timer"])
        timer.unlink()
        (self.units / "digest.service").unlink(missing_ok=True)
        return True

    def status(self) -> Status:
        timer = self.units / "digest.timer"
        if not timer.exists():
            return Status(False, "no timer installed")
        found = self.run(["systemctl", "--user", "list-timers", "digest.timer"])
        return Status(True, getattr(found, "stdout", "").strip() or "installed",
                      str(timer))


class Cron(Backend):
    """The fallback where there is no systemd — a container, or a minimal box.

    Deliberately simple: the app owns exactly one line, marked, and rewrites the
    table around it. Anything else in a user's crontab is left alone.
    """

    name = "cron"
    MARK = "# weekly-digest"

    def line(self, day: str, hour: int) -> str:
        return f"0 {hour} * * {WEEKDAYS.index(day)} {' '.join(command())}  {self.MARK}"

    def _table(self) -> list[str]:
        found = self.run(["crontab", "-l"])
        text = getattr(found, "stdout", "") or ""
        return [ln for ln in text.splitlines() if self.MARK not in ln]

    def install(self, day: str, hour: int) -> str:
        table = "\n".join([*self._table(), self.line(day, hour)]) + "\n"
        self.run(["crontab", "-"], table)
        return table

    def remove(self) -> bool:
        kept = self._table()
        if not kept and not self.status().installed:
            return False
        self.run(["crontab", "-"], "\n".join(kept) + "\n" if kept else "\n")
        return True

    def status(self) -> Status:
        found = self.run(["crontab", "-l"])
        text = getattr(found, "stdout", "") or ""
        installed = self.MARK in text
        return Status(installed, "in crontab" if installed else "not in crontab")


# ---------------------------------------------------------------- Windows


class Schtasks(Backend):
    name = "schtasks"

    def arguments(self, day: str, hour: int) -> list[str]:
        return [
            "schtasks", "/Create", "/F", "/TN", "WeeklyDigest",
            "/SC", "WEEKLY", "/D", SCHTASKS_WEEKDAY[day],
            "/ST", f"{hour:02d}:00",
            "/TR", " ".join(f'"{part}"' if " " in part else part for part in command()),
        ]

    def install(self, day: str, hour: int) -> str:
        self.run(self.arguments(day, hour))
        return "WeeklyDigest"

    def remove(self) -> bool:
        found = self.run(["schtasks", "/Delete", "/TN", "WeeklyDigest", "/F"])
        return getattr(found, "returncode", 1) == 0

    def status(self) -> Status:
        found = self.run(["schtasks", "/Query", "/TN", "WeeklyDigest"])
        if getattr(found, "returncode", 1) != 0:
            return Status(False, "no scheduled task")
        return Status(True, (getattr(found, "stdout", "") or "").strip().splitlines()[-1])


def backend(runner=None, platform: str | None = None) -> Backend:
    platform = platform or sys.platform
    if platform == "darwin":
        return Launchd(runner)
    if platform.startswith("win"):
        return Schtasks(runner)
    if shutil.which("systemctl"):
        return Systemd(runner)
    return Cron(runner)


# --------------------------------------------------------------- telling you


def _applescript_string(text: str) -> str:
    """Quote a value for AppleScript, which knows only \\" and \\\\ as escapes.

    A headline is arbitrary text off the internet, and a stray quote in one
    would otherwise turn the rest of the notification into AppleScript. Newlines
    become spaces because a notification body is one line whatever you send it.
    """
    flat = " ".join(text.split())
    return '"' + flat.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify_arguments(title: str, body: str, platform: str | None = None) -> list[str] | None:
    """The command that puts a desktop notification on screen, or None.

    None is not a failure: it is a machine with nothing to notify (Windows,
    or a Linux box without notify-send). The caller treats it as a quiet no-op.
    """
    platform = platform or sys.platform
    if platform == "darwin":
        script = (
            f"display notification {_applescript_string(body)} "
            f"with title {_applescript_string(title)}"
        )
        return ["osascript", "-e", script]
    if not platform.startswith("win") and shutil.which("notify-send"):
        return ["notify-send", title, body]
    return None


def notify(title: str, body: str, runner=None, platform: str | None = None) -> bool:
    """Tell the desktop a scheduled run is done. Never raises, ever.

    A scheduled run has nobody watching it, which is the whole reason this
    exists — but it is also why a failure here must not take the run with it.
    The edition is already on disk by the time this is called; a notification
    that does not appear is a nuisance, and an exception at this point would
    turn a finished run into a failed one.
    """
    arguments = notify_arguments(title, body, platform)
    if arguments is None:
        return False
    try:
        (runner or _default_runner)(arguments)
        return True
    except Exception as exc:  # noqa: BLE001 - a notification is never worth a run
        log.debug("could not post a notification: %s", exc)
        return False
