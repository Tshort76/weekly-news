from digest.dedupe import dedupe
from digest.models import Item
from digest.normalize import normalize_all

from .conftest import load_fixture, make_item


def _fixture_items():
    return normalize_all([Item.from_dict(d) for d in load_fixture("raw_items.json")])


def test_the_same_story_from_three_feeds_collapses_to_one():
    kept, dropped = dedupe(_fixture_items())
    titles = [k.title for k in kept]
    assert sum("Bank of Japan" in t for t in titles) == 1
    assert len(dropped) == 2


def test_the_highest_weight_source_wins_and_records_the_others():
    kept, _ = dedupe(_fixture_items())
    boj = next(k for k in kept if "Bank of Japan" in k.title)
    assert boj.source == "Economist — Finance and Economics"
    assert len(boj.also_in) == 2


def test_exact_url_duplicates_collapse():
    a = make_item(source="A", url="https://example.com/x?utm_source=rss", weight=1.0)
    b = make_item(source="B", url="https://example.com/x/", weight=0.5)
    kept, dropped = dedupe(normalize_all([a, b]))
    assert len(kept) == 1 and len(dropped) == 1
    assert "duplicate url" in dropped[0][1]


def test_seen_ids_are_dropped_before_anything_else():
    items = _fixture_items()
    kept, dropped = dedupe(items, seen_ids={items[0].id})
    assert items[0].id not in {k.id for k in kept}
    assert any("already seen" in reason for _, reason in dropped)


def test_unrelated_titles_survive():
    kept, _ = dedupe(_fixture_items())
    assert len(kept) == 3
