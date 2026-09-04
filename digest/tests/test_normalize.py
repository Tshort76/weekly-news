from digest.models import Item
from digest.normalize import (
    BLURB_LIMIT, canonical_url, item_id, normalize, normalize_all, strip_furniture,
    strip_html, truncate,
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


def test_normalize_all_caps_a_runaway_blurb():
    """The cap exists to stop a feed that dumps a whole page into one field,
    not to ration what the writer gets. It sat at 400 and was cutting real
    article text off mid-paragraph."""
    items = [Item.from_dict(d) for d in load_fixture("raw_items.json")]
    blurbs = [i.blurb for i in normalize_all(items)]
    assert max(len(b) for b in blurbs) <= BLURB_LIMIT + 1


def test_a_feed_that_gives_a_full_article_keeps_it():
    """Ars Technica's summary is 78 characters and its content field on the
    same entry is 1010. Keeping the shorter one is how a writer ends up
    describing a story it was barely told."""
    long_body = "The court ordered a change to the rules. " * 20
    item = Item.from_dict({**load_fixture("raw_items.json")[0], "blurb": long_body})
    assert len(normalize(item).blurb) > 400


def test_page_furniture_is_not_published_as_the_reporters_words():
    """Share buttons, photo credits and the read-the-article footer survive tag
    stripping because they are real text. Harmless in a blurb, not harmless
    once the words carry a named outlet's byline."""
    assert strip_furniture(
        "Post Email Whatsapp Copy link Share Tingshu Wang/Reuters "
        "Solar surpassed coal in China. It rose fast. Read full article Comments"
    ) == "Solar surpassed coal in China. It rose fast."


def test_a_cut_that_lands_mid_fragment_loses_the_fragment():
    text = "The court ruled against the agency today in a long opinion. Then a dangling"
    assert strip_furniture(text).endswith("opinion.")


def test_ordinary_prose_is_left_alone():
    assert strip_furniture("A normal sentence stays whole.") == "A normal sentence stays whole."
