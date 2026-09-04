"""SQLite store: what we have seen, what we classified, what we published.

Editions are keyed by ISO week, so re-running a week overwrites rather than
duplicating.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import Classified, Edition, Entry

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    first_seen_week TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS classified (
    id TEXT NOT NULL,
    week TEXT NOT NULL,
    fit INTEGER, kind TEXT, novelty INTEGER,
    region TEXT, domain TEXT, mechanism TEXT, reason TEXT,
    json TEXT NOT NULL,
    PRIMARY KEY (id, week)
);
CREATE TABLE IF NOT EXISTS editions (
    week TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    word_count INTEGER, entry_count INTEGER,
    theme TEXT, path TEXT,
    json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    week TEXT NOT NULL,
    cluster_title TEXT, mechanism TEXT, headline TEXT, hook TEXT
);
CREATE TABLE IF NOT EXISTS deliveries (
    week TEXT NOT NULL,
    filename TEXT NOT NULL,
    drive_file_id TEXT,
    uploaded_at TEXT,
    PRIMARY KEY (week, filename)
);
CREATE INDEX IF NOT EXISTS entries_week ON entries(week);
CREATE INDEX IF NOT EXISTS classified_week ON classified(week);
"""

# The database carries its own version in PRAGMA user_version. Everything above
# is version 1, so a store written before this existed is already at 1 once the
# script has run — nothing to migrate for an existing user.
SCHEMA_VERSION = 2

RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    week TEXT NOT NULL,
    started TEXT NOT NULL,
    finished TEXT,
    status TEXT NOT NULL,
    fetched INTEGER, selected INTEGER, entries INTEGER, words INTEGER,
    note TEXT,
    PRIMARY KEY (week, started)
);
"""


def _m002_kind_slots(conn) -> None:
    """Rewrite stored kinds from the first lens's words to the fixed slots.

    Load-bearing rather than cosmetic. `select` is pure and `audit` re-runs it
    over the stored classifications, so a past week has to keep giving the same
    answer. After the rename the balance rule looks for "adjacent"; every stored
    row says "contest", and an audit of an old week would quietly cap nothing.
    """
    conn.execute(RUNS)
    rows = conn.execute("SELECT id, week, kind, json FROM classified").fetchall()
    mapping = {"architecture": "core", "contest": "adjacent"}
    for row in rows:
        slot = mapping.get(row["kind"], row["kind"])
        blob = json.loads(row["json"])
        blob["kind"] = mapping.get(blob.get("kind"), blob.get("kind"))
        conn.execute(
            "UPDATE classified SET kind = ?, json = ? WHERE id = ? AND week = ?",
            (slot, json.dumps(blob), row["id"], row["week"]),
        )


MIGRATIONS = {1: _m002_kind_slots}


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA + RUNS)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        # A store that predates versioning has already had SCHEMA applied, so
        # it is at 1 by construction rather than at 0.
        version = version or 1
        while version < SCHEMA_VERSION:
            MIGRATIONS[version](self.conn)
            version += 1
        self.conn.execute(f"PRAGMA user_version = {version}")

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- runs

    def start_run(self, week: str) -> str:
        started = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO runs (week, started, status) VALUES (?,?,?)",
            (week, started, "running"),
        )
        self.conn.commit()
        return started

    def finish_run(self, week: str, started: str, status: str, **counts) -> None:
        self.conn.execute(
            """UPDATE runs SET finished = ?, status = ?, fetched = ?, selected = ?,
                               entries = ?, words = ?, note = ?
               WHERE week = ? AND started = ?""",
            (
                datetime.now(timezone.utc).isoformat(), status,
                counts.get("fetched"), counts.get("selected"), counts.get("entries"),
                counts.get("words"), counts.get("note"),
                week, started,
            ),
        )
        self.conn.commit()

    def recent_runs(self, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY started DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def __enter__(self) -> "State":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- seen

    def seen_ids(self) -> set[str]:
        return {r["id"] for r in self.conn.execute("SELECT id FROM seen")}

    def mark_seen(self, items, week: str) -> None:
        self.conn.executemany(
            "INSERT OR IGNORE INTO seen (id, url, title, first_seen_week) VALUES (?,?,?,?)",
            [(i.id, i.url, i.title, week) for i in items],
        )
        self.conn.commit()

    # ---------------------------------------------------------- classified

    def save_classified(self, rows: list[Classified], week: str) -> None:
        self.conn.executemany(
            """INSERT OR REPLACE INTO classified
               (id, week, fit, kind, novelty, region, domain, mechanism, reason, json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    c.id, week, c.fit, c.kind, c.novelty, c.region, c.domain,
                    c.mechanism, c.reason, json.dumps(c.to_dict()),
                )
                for c in rows
            ],
        )
        self.conn.commit()

    def load_classified(self, week: str) -> list[Classified]:
        rows = self.conn.execute(
            "SELECT json FROM classified WHERE week = ?", (week,)
        ).fetchall()
        return [Classified.from_dict(json.loads(r["json"])) for r in rows]

    # ------------------------------------------------------------ editions

    def save_edition(self, edition: Edition, path: Path | None = None) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO editions
               (week, generated_at, word_count, entry_count, theme, path, json)
               VALUES (?,?,?,?,?,?,?)""",
            (
                edition.week,
                edition.generated_at.isoformat(),
                edition.word_count,
                len(edition.entries),
                edition.theme,
                str(path) if path else None,
                json.dumps(edition.to_dict()),
            ),
        )
        self.conn.execute("DELETE FROM entries WHERE week = ?", (edition.week,))
        self.conn.executemany(
            "INSERT INTO entries (week, cluster_title, mechanism, headline, hook) VALUES (?,?,?,?,?)",
            [
                (edition.week, e.cluster_title, e.mechanism, e.headline, e.hook)
                for e in edition.entries
            ],
        )
        self.conn.commit()

    def load_edition(self, week: str) -> Edition | None:
        row = self.conn.execute(
            "SELECT json FROM editions WHERE week = ?", (week,)
        ).fetchone()
        return Edition.from_dict(json.loads(row["json"])) if row else None

    # ---------------------------------------------------- saga / diff data

    def prior_mechanisms(self, before_week: str, limit: int = 400) -> list[str]:
        rows = self.conn.execute(
            """SELECT DISTINCT mechanism FROM entries
               WHERE mechanism IS NOT NULL AND mechanism != '' AND week < ?
               ORDER BY week DESC LIMIT ?""",
            (before_week, limit),
        ).fetchall()
        return [r["mechanism"] for r in rows]

    def prior_entries(self, before_week: str, limit: int = 60) -> list[dict]:
        """Headline + hook + mechanism from recent editions, for 'since last week' diffs."""
        rows = self.conn.execute(
            """SELECT week, headline, hook, mechanism FROM entries
               WHERE week < ? ORDER BY week DESC LIMIT ?""",
            (before_week, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------- deliveries

    def record_delivery(self, week: str, filename: str, drive_file_id: str) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO deliveries (week, filename, drive_file_id, uploaded_at)
               VALUES (?,?,?,?)""",
            (week, filename, drive_file_id, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def delivery(self, week: str, filename: str) -> str | None:
        row = self.conn.execute(
            "SELECT drive_file_id FROM deliveries WHERE week = ? AND filename = ?",
            (week, filename),
        ).fetchone()
        return row["drive_file_id"] if row else None


@contextmanager
def open_state(path: Path):
    st = State(path)
    try:
        yield st
    finally:
        st.close()
