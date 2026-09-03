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


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

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
