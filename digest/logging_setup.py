"""Structured logging to a per-week file, a readable console stream, and — so a
run can say what happened to it — a row per event in the database.

The database sink is a `logging.Handler` rather than a callback threaded down
through the pipeline, and that is the whole reason it costs almost nothing. The
lines worth recording already exist at the sites that matter: `ingest` knows
what each feed gave, `llm.Client` knows about every retry, `ground` knows which
items stayed thin. None of those modules has a `State` handle or a progress
callback, and giving them one means changing `ingest()`, `fetch_source`,
`Client.__init__`, `ground()` and every test that builds them.

A handler needs none of that. Every WARNING and ERROR anywhere under the
`digest` logger becomes a row for free, which is what makes the decision to warn
rather than fail actually visible. A record that wants more structure carries
it: `extra={"event": {"kind": ..., "subject": ..., "n": ..., "ms": ...}}`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)-18s %(message)s"
CONSOLE_FORMAT = "%(levelname)-7s %(message)s"



class DatabaseHandler(logging.Handler):
    """Write records to `run_events`, on whatever thread logged them.

    Owns its own connection because the UI runs the pipeline on a job thread
    while the request thread is reading the same file, and a sqlite3 connection
    belongs to one thread. Failures here are swallowed: a dashboard that cannot
    record an event must never be the reason a run dies.
    """

    def __init__(self, db_path: Path, week: str):
        super().__init__(level=logging.DEBUG)
        self.db_path, self.week, self.started = Path(db_path), week, None
        self._local = threading.local()

    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=5)
            self._local.conn = conn
        return conn

    def emit(self, record: logging.LogRecord) -> None:
        event = getattr(record, "event", None)
        if self.started is None or (event is None and record.levelno < logging.WARNING):
            return
        event = event or {}
        detail = event.get("detail")
        try:
            self._conn().execute(
                """INSERT INTO run_events
                     (week, started, at, level, kind, subject, n, ms, message, detail)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (self.week, self.started,
                 datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                 record.levelname, event.get("kind", "note"), event.get("subject"),
                 event.get("n"), event.get("ms"), record.getMessage(),
                 json.dumps(detail) if detail is not None else None),
            )
            self._conn().commit()
        except Exception:  # noqa: BLE001 - never let recording break a run
            pass

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
        super().close()


def database_handler() -> DatabaseHandler | None:
    """The sink installed by `setup`, so `pipeline.run` can hand it `started`.

    `started` does not exist until `start_run` returns, and logging is set up
    before that, so the handler is created blind and told afterwards. Until it
    is told, it records nothing — which is right: those lines belong to no run.
    """
    for handler in logging.getLogger("digest").handlers:
        if isinstance(handler, DatabaseHandler):
            return handler
    return None


def setup(log_dir: Path, week: str, verbose: bool = False,
          db_path: Path | None = None) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{week}.log"

    root = logging.getLogger("digest")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    root.addHandler(console)

    if db_path is not None:
        root.addHandler(DatabaseHandler(db_path, week))

    return path
