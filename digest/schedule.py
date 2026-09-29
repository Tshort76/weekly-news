"""Run the digest once a week without anyone remembering to.

On Linux and Windows: write the operating system's own file, hand it to the
operating system's own command, and never invent a daemon of our own. On a Mac
the SwiftBar menu-bar plugin is the scheduler, and nothing is written (see
`MenuBar`).

Every backend takes a `runner` for the command it would execute, so the tests
assert on the unit file and the argument list without any of them
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


class MenuBar(Backend):
    """On a Mac the SwiftBar plugin is the scheduler, so there is no file to write.

    A launch agent ran the digest and told nobody: a run that finished at 07:55
    on a Friday was noticed, if at all, the following week. The plugin replaced
    it on 2026-09-08, reading the day and hour from config.toml's [schedule]
    through `digest status --json`. This app went on writing a launch agent every
    time the Schedule page was saved, and one came back on 2026-09-25 and ran
    W39 beside the plugin. So saving a schedule here now only retires that old
    agent; recording the day and hour is the caller's job, as it always was.
    """

    name = "menu bar"

    def __init__(self, runner=None, agents: Path | None = None):
        super().__init__(runner)
        self.agents = agents or Path.home() / "Library" / "LaunchAgents"

    @property
    def leftover(self) -> Path:
        return self.agents / f"{LABEL}.plist"

    def install(self, day: str, hour: int) -> str:
        self.remove()
        return "the SwiftBar plugin runs it, reading the day and hour from config.toml"

    def remove(self) -> bool:
        """Retire a launch agent left by an older version. The plugin is untouched."""
        if not self.leftover.exists():
            return False
        self.run(["launchctl", "unload", str(self.leftover)])
        self.leftover.unlink()
        return True

    def status(self) -> Status:
        if self.leftover.exists():
            return Status(True, "an old launch agent is still installed beside the "
                          "menu-bar plugin; save the schedule to remove it", str(self.leftover))
        return Status(True, "run by the SwiftBar menu-bar plugin")


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


def record(enabled: bool, day: str | None = None, hour: int | None = None) -> None:
    """Write the schedule into config.toml, where the menu-bar plugin reads it.

    Both the Schedule page and `digest schedule` call this, so the Settings page,
    the plugin and any OS scheduler cannot drift apart. A checkout with no
    installed config has nowhere to put it and is left alone.
    """
    import tomllib  # noqa: PLC0415

    from .config import paths  # noqa: PLC0415
    from .config.schema import validate_config  # noqa: PLC0415
    from .config.write import dumps, write  # noqa: PLC0415

    target = paths.config_file()
    if not target.exists():
        return
    raw = tomllib.loads(target.read_text(encoding="utf-8"))
    given = {"day": day, "hour": hour}
    raw.setdefault("schedule", {}).update(
        {"enabled": enabled, **{k: v for k, v in given.items() if v is not None}}
    )
    validate_config(raw)
    write(target, dumps(raw))


def backend(runner=None, platform: str | None = None) -> Backend:
    platform = platform or sys.platform
    if platform == "darwin":
        return MenuBar(runner)
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
