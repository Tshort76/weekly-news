"""Grounding. Every network call is stubbed — this stage is about what happens
when the network says no, which is most of the time."""

from __future__ import annotations

import pytest

from digest import ground as g
from digest.config import Config, RunCfg
from digest.models import Evidence

from .conftest import make_classified


def _thin(blurb: str = "Ten words about a thing that happened somewhere.") -> list:
    return [make_classified(item={"title": "A judge ruled against the agency", "blurb": blurb})]


def test_an_item_the_feed_described_properly_is_left_alone(monkeypatch):
    """The cheapest tier is the one that needs no network at all."""
    monkeypatch.setattr(g, "article_text", lambda *a, **k: pytest.fail("should not fetch"))
    rows = _thin("x" * 600)
    assert g.ground(rows, Config()) == rows
    assert rows[0].evidence == []


def test_a_thin_item_gets_its_own_article(monkeypatch):
    monkeypatch.setattr(g, "article_text", lambda *a, **k: "The court said so. " * 60)
    monkeypatch.setattr(g, "search", lambda *a, **k: pytest.fail("article was enough"))
    rows = _thin()
    g.ground(rows, Config())
    assert [e.kind for e in rows[0].evidence] == ["article"]


def test_a_paywall_falls_through_to_other_outlets(monkeypatch):
    """The Economist answers 403 to every article and is the spine of the
    source list, so this path is the common one, not the exception."""
    def refuse(*a, **k):
        raise OSError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(g, "article_text", refuse)
    monkeypatch.setattr(g, "search", lambda *a, **k: [Evidence(kind="search", text="Another outlet said so.")])
    rows = _thin()
    g.ground(rows, Config())
    assert [e.kind for e in rows[0].evidence] == ["search"]


def test_a_page_that_answers_with_no_article_is_treated_as_a_refusal(monkeypatch):
    """A consent wall returns 200 and no paragraphs. Same outcome as a 403."""
    monkeypatch.setattr(g, "article_text", lambda *a, **k: "Accept cookies")
    monkeypatch.setattr(g, "search", lambda *a, **k: [Evidence(kind="search", text="Elsewhere.")])
    rows = _thin()
    g.ground(rows, Config())
    assert [e.kind for e in rows[0].evidence] == ["search"]


def test_the_run_survives_the_search_going_down(monkeypatch):
    """No tier of this may take the week's briefing down with it."""
    def broken(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(g, "article_text", broken)
    monkeypatch.setattr(g, "search", broken)
    rows = _thin()
    assert g.ground(rows, Config()) == rows
    assert rows[0].evidence == []


def test_grounding_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(g, "article_text", lambda *a, **k: pytest.fail("disabled"))
    rows = _thin()
    g.ground(rows, Config(run=RunCfg(ground=False)))
    assert rows[0].evidence == []


def test_evidence_survives_a_round_trip_through_storage():
    """The compare script and the audit both rebuild from the stored rows, so
    evidence that does not persist is evidence the guard will not see."""
    from digest.models import Classified

    row = make_classified()
    row.evidence = [Evidence(kind="search", text="Elsewhere.", url="u", source="s")]
    back = Classified.from_dict(row.to_dict())
    assert back.evidence == row.evidence


def test_snippets_are_parsed_out_of_the_search_page(monkeypatch):
    page = (b'<a class="result__snippet" href="x">A federal judge ruled that the '
            b'agency acted <b>unlawfully</b> and must reverse the decision.</a>'
            b'<a class="result__snippet" href="y">short</a>')
    monkeypatch.setattr(g, "fetch_bytes", lambda *a, **k: page)
    found = g.search("anything")
    assert len(found) == 1
    assert "unlawfully" in found[0].text and "<b>" not in found[0].text


def test_an_unknown_search_backend_says_so_rather_than_grounding_nothing():
    """A typo in the config must not look like a quiet week for the searcher."""
    from digest.config import Config, RunCfg

    with pytest.raises(LookupError, match="unknown search_backend"):
        g.search("q", Config(run=RunCfg(search_backend="gogole")))


def test_search_can_be_turned_off_while_article_fetching_stays_on(monkeypatch):
    from digest.config import Config, RunCfg

    monkeypatch.setattr(g, "article_text", lambda *a, **k: "Accept cookies")
    rows = _thin()
    g.ground(rows, Config(run=RunCfg(search_backend="none")))
    assert rows[0].evidence == []


def test_brave_reads_the_descriptions_out_of_the_payload(monkeypatch):
    import io
    import json as _json

    payload = {"web": {"results": [
        {"description": "A federal judge ruled the agency acted unlawfully and must reverse it.",
         "url": "https://x.test/a", "profile": {"name": "Example News"}},
        {"description": "too short", "url": "https://x.test/b"},
    ]}}
    monkeypatch.setattr("digest.credentials.api_key", lambda *a, **k: "k")
    monkeypatch.setattr(
        g.urllib.request, "urlopen",
        lambda *a, **k: io.BytesIO(_json.dumps(payload).encode()),
    )
    found = g.brave("anything")
    assert len(found) == 1
    assert found[0].source == "Example News" and "unlawfully" in found[0].text
