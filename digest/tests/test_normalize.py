from digest.models import Item
from digest.normalize import (
    canonical_url, item_id, normalize, normalize_all, strip_html, truncate,
)

from .conftest import load_fixture, make_item


def test_strip_html_removes_tags_and_double_encoding():
    assert strip_html("<p>Reserves &amp;amp; prices</p>") == "Reserves & prices"


def test_strip_html_collapses_whitespace():
    assert strip_html("  a\n\n  b\t c ") == "a b c"


def test_canonical_url_drops_tracking_and_fragment():
    url = "https://EXAMPLE.com/a/?utm_source=rss&id=7&fbclid=x#top"
    assert canonical_url(url) == "https://example.com/a?id=7"


def test_canonical_url_keeps_meaningful_query():
    assert canonical_url("https://www.ft.com/world?format=rss") == (
        "https://www.ft.com/world?format=rss"
    )


def test_id_is_stable_across_tracking_variants():
    a = item_id("https://example.com/a?utm_source=rss")
    b = item_id("https://example.com/a/")
    assert a == b


def test_truncate_cuts_on_a_word_boundary():
    out = truncate("word " * 200, 400)
    assert len(out) <= 401 and out.endswith("…")


def test_normalize_recomputes_the_id_from_the_canonical_url():
    item = make_item(id="stale", url="https://example.com/a?utm_medium=feed")
    assert normalize(item).id == item_id("https://example.com/a")


def test_normalize_all_drops_untitled_items():
    items = [Item.from_dict(d) for d in load_fixture("raw_items.json")]
    out = normalize_all(items)
    assert len(out) == len(items) - 1
    assert all(i.title for i in out)


def test_normalize_all_truncates_long_blurbs():
    items = [Item.from_dict(d) for d in load_fixture("raw_items.json")]
    blurbs = [i.blurb for i in normalize_all(items)]
    assert max(len(b) for b in blurbs) <= 401
