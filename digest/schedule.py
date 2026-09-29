"""When the weekly run happens, and telling the desktop it is done.

The SwiftBar menu-bar plugin (`~/dev/swiftbar-plugins/weekly-digest.1h.py`) is
the scheduler. It reads the day and hour from config.toml's [schedule] through
`digest status --json` and starts the run itself. A launch agent did this job
until 2026-09-08 and told nobody when a run finished, which is why the plugin
replaced it; this module writes no scheduler file of any kind.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

log = logging.getLogger("digest.schedule")

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _default_runner(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, input=stdin, capture_output=True, text=True, check=False)


def record(day: str, hour: int) -> None:
    """Write the schedule into config.toml, where the menu-bar plugin reads it.

    The Schedule page calls this. Without a config.toml there is nowhere to put
    it, and nothing is written.
    """
    import tomllib  # noqa: PLC0415

    from .config import paths  # noqa: PLC0415
    from .config.schema import validate_config  # noqa: PLC0415
    from .config.write import dumps, write  # noqa: PLC0415

    target = paths.config_file()
    if not target.exists():
        return
    raw = tomllib.loads(target.read_text(encoding="utf-8"))
    raw.setdefault("schedule", {}).update({"day": day, "hour": hour})
    validate_config(raw)
    write(target, dumps(raw))


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
