from datetime import datetime, timezone

from digest.models import Edition, Entry
from digest.state import State

from .conftest import make_item


def _edition(week="2026-W36", mechanism="reserve quantity target") -> Edition:
    return Edition(
        week=week,
        generated_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
        opening="o",
        entries=[Entry(cluster_id="c1", headline="h", body="b", hook="k", mechanism=mechanism)],
        closing_questions=["q"],
        theme="t",
    )


def test_an_edition_round_trips(tmp_path):
    with State(tmp_path / "s.db") as st:
        st.save_edition(_edition())
        back = st.load_edition("2026-W36")
    assert back and back.entries[0].headline == "h" and back.theme == "t"


def test_rerunning_a_week_overwrites_rather_than_duplicating(tmp_path):
    with State(tmp_path / "s.db") as st:
        st.save_edition(_edition())
        st.save_edition(_edition())
        rows = st.conn.execute("SELECT COUNT(*) c FROM entries WHERE week = ?", ("2026-W36",))
        assert rows.fetchone()["c"] == 1


def test_prior_mechanisms_only_looks_backwards(tmp_path):
    with State(tmp_path / "s.db") as st:
        st.save_edition(_edition(week="2026-W35", mechanism="older mechanism"))
        st.save_edition(_edition(week="2026-W37", mechanism="future mechanism"))
        assert st.prior_mechanisms("2026-W36") == ["older mechanism"]


def test_seen_ids_survive_the_connection(tmp_path):
    db = tmp_path / "s.db"
    item = make_item(url="https://e.com/1")
    with State(db) as st:
        st.mark_seen([item], "2026-W36")
    with State(db) as st:
        assert item.id in st.seen_ids()


def test_a_delivery_is_recorded_per_file(tmp_path):
    with State(tmp_path / "s.db") as st:
        st.record_delivery("2026-W36", "digest-2026-W36.txt", "fileid1")
        assert st.delivery("2026-W36", "digest-2026-W36.txt") == "fileid1"
        assert st.delivery("2026-W36", "digest-2026-W36.md") is None


def test_the_entries_table_stores_the_cluster_title_not_its_id(tmp_path):
    edition = _edition()
    edition.entries[0].cluster_title = "Japan rewrites its monetary framework"
    with State(tmp_path / "s.db") as st:
        st.save_edition(edition)
        row = st.conn.execute(
            "SELECT cluster_title FROM entries WHERE week = ?", ("2026-W36",)
        ).fetchone()
    assert row["cluster_title"] == "Japan rewrites its monetary framework"
