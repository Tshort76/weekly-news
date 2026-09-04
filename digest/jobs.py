"""One long job, running behind a web page that may not be open.

A week takes about twenty minutes. That is a strange thing to put inside a web
process, and getting it wrong in the obvious ways would make the UI worse than
the terminal it replaces. Three failures this is built to avoid:

- **Losing progress when the tab closes.** Events go into a ring buffer held by
  the job, not pushed at a listener. A page that reconnects replays what it
  missed and carries on, because the buffer is the record and the stream is only
  a view of it.
- **Hanging the server when the job dies.** The thread catches everything.
  A crashed run is a run with `status = "failed"` and the traceback in `error`,
  which is a thing the page can show.
- **Two runs at once.** One lock, held for the life of the job, plus a lock file
  in the data directory so a `digest run` in a terminal refuses rather than
  quietly writing the same week from two directions.

Nothing here imports the web framework. The UI reads this; this knows nothing
about the UI, which is what lets the tests drive it with a fake pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("digest.jobs")

BUFFER_EVENTS = 500
LOCK_NAME = "run.lock"


@dataclass
class Event:
    seq: int
    at: str
    stage: str
    detail: dict

    def as_dict(self) -> dict:
        return {"seq": self.seq, "at": self.at, "stage": self.stage, "detail": self.detail}


@dataclass
class Job:
    week: str
    status: str = "running"  # running | done | failed | cancelled
    started: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished: str = ""
    error: str = ""
    result: object = None
    events: deque = field(default_factory=lambda: deque(maxlen=BUFFER_EVENTS))
    cancel: threading.Event = field(default_factory=threading.Event)
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _changed: threading.Condition = field(init=False)

    def __post_init__(self):
        self._changed = threading.Condition(self._lock)

    @property
    def running(self) -> bool:
        return self.status == "running"

    def record(self, stage: str, detail: dict) -> None:
        with self._changed:
            self._seq += 1
            self.events.append(
                Event(self._seq, datetime.now(timezone.utc).isoformat(), stage, dict(detail))
            )
            self._changed.notify_all()

    def since(self, seq: int) -> list[Event]:
        """Everything after `seq`. A reconnecting page passes its last id."""
        with self._lock:
            return [e for e in self.events if e.seq > seq]

    def wait(self, seq: int, timeout: float = 15.0) -> list[Event]:
        """Block until there is something after `seq`, or the timeout expires.

        Returning empty on a timeout is deliberate: the caller sends a keepalive
        and asks again, so a proxy or a laptop lid does not silently kill the
        stream.
        """
        with self._changed:
            if not any(e.seq > seq for e in self.events) and self.running:
                self._changed.wait(timeout)
            return [e for e in self.events if e.seq > seq]

    def finish(self, status: str, error: str = "", result=None) -> None:
        with self._changed:
            self.status = status
            self.error = error
            self.result = result
            self.finished = datetime.now(timezone.utc).isoformat()
            self._changed.notify_all()


class Busy(RuntimeError):
    """A run is already in progress. Carries what is running."""


class Runner:
    """Owns the one job slot and the thread behind it."""

    def __init__(self, data_dir: Path | None = None, run=None):
        self.data_dir = Path(data_dir) if data_dir else None
        self._run = run  # injectable, so tests never touch the real pipeline
        self._job: Job | None = None
        self._guard = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def job(self) -> Job | None:
        return self._job

    @property
    def busy(self) -> bool:
        return self._job is not None and self._job.running

    # ------------------------------------------------------------- lock file

    def _lock_path(self) -> Path | None:
        return self.data_dir / LOCK_NAME if self.data_dir else None

    def _claim(self, week: str) -> None:
        """Tell a terminal `digest run` that the app is already running one.

        Advisory rather than enforced: a stale lock from a killed process would
        otherwise wedge the app, so a lock naming a process that is gone is
        cleared rather than obeyed.
        """
        path = self._lock_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                held = json.loads(path.read_text())
                if _process_alive(int(held.get("pid", -1))):
                    raise Busy(f"a run of {held.get('week')} is already in progress")
            except (ValueError, OSError):
                pass  # unreadable lock is a stale lock
        path.write_text(json.dumps({"pid": os.getpid(), "week": week}))

    def _release(self) -> None:
        path = self._lock_path()
        if path is not None and path.exists():
            path.unlink(missing_ok=True)

    # ----------------------------------------------------------------- start

    def start(self, week: str, **kwargs) -> Job:
        with self._guard:
            if self.busy:
                raise Busy(f"a run of {self._job.week} is already in progress")
            self._claim(week)
            job = Job(week=week)
            self._job = job

        def body() -> None:
            try:
                result = self._run(
                    week=week, progress=job.record, cancel=job.cancel, **kwargs
                )
            except Exception as exc:
                cancelled = type(exc).__name__ == "Cancelled"
                if not cancelled:
                    log.exception("run failed")
                job.finish(
                    "cancelled" if cancelled else "failed",
                    error="" if cancelled else traceback.format_exc(limit=6),
                )
            else:
                job.finish("done", result=result)
            finally:
                self._release()

        self._thread = threading.Thread(target=body, name=f"digest-run-{week}", daemon=True)
        self._thread.start()
        return job

    def stop(self) -> bool:
        if not self.busy:
            return False
        self._job.cancel.set()
        return True

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stream(job: Job, last_seq: int = 0, keepalive: float = 15.0):
    """Server-sent events for one job, resuming from `last_seq`.

    A generator rather than a callback so the web layer can hand it straight to
    a streaming response, and so the tests can drain it with a for loop.
    """
    seq = last_seq
    while True:
        events = job.wait(seq, keepalive)
        for event in events:
            seq = event.seq
            yield f"id: {seq}\nevent: progress\ndata: {json.dumps(event.as_dict())}\n\n"
        if not job.running and not events:
            payload = {"status": job.status, "error": job.error}
            yield f"event: done\ndata: {json.dumps(payload)}\n\n"
            return
        if not events:
            yield ": keepalive\n\n"
            time.sleep(0)
