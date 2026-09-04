"""Synthesis against recorded model responses — no network."""

from __future__ import annotations

import json

import pytest

from digest.config import Config, RunCfg
from digest.llm import extract_json
from digest.models import Cluster, Entry
from digest.synthesize import govern_length, synthesize, write_entry, write_frame

from .conftest import load_fixture, make_classified

RECORDED = load_fixture("synthesize_response.json")


class ScriptedClient:
    """Replays one saved response per call, in order."""

    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self.responses.pop(0) if self.responses else ""

    def complete_json(self, **kwargs):
        return extract_json(self.complete(**kwargs))


def _cluster(cid: str = "c1", n: int = 1) -> Cluster:
    return Cluster(
        cluster_id=cid,
        title="Japan rewrites its monetary framework",
        items=[
            make_classified(item={"url": f"https://e.com/{cid}-{i}"}, region="east_asia")
            for i in range(n)
        ],
        shared_mechanism="reserve quantity target replaces price target",
    )


def test_a_fenced_entry_response_still_parses():
    entry = write_entry(_cluster(), Config(), ScriptedClient(RECORDED["entry"]), [])
    assert entry is not None
    assert entry.headline.startswith("Japan's central bank")
    assert len(entry.questions) == 2
    assert entry.sources[0]["url"] == "https://e.com/c1-0"


def test_prior_coverage_is_handed_to_the_writer_when_the_mechanism_repeats():
    client = ScriptedClient(RECORDED["entry"])
    prior = [{
        "headline": "An older headline",
        "hook": "An older hook",
        "mechanism": "reserve quantity target replaces the price target",
    }]
    write_entry(_cluster(), Config(), client, prior)
    assert "An older hook" in client.calls[0]["prompt"]


def test_no_prior_note_when_nothing_matches():
    client = ScriptedClient(RECORDED["entry"])
    write_entry(_cluster(), Config(), client, [{"mechanism": "something else entirely"}])
    assert "covered in an earlier edition" not in client.calls[0]["prompt"]


def test_the_writer_is_told_explicitly_when_there_is_nothing_to_compare_to():
    """A blank {prior_coverage} slot reads, to a weaker model, as silence rather
    than as 'nothing to compare to' — and a model just told to say what changed
    will invent a comparison to fill the gap. The negative case has to be as
    explicit as the positive one."""
    client = ScriptedClient(RECORDED["entry"])
    write_entry(_cluster(), Config(), client, [])
    assert "No prior coverage" in client.calls[0]["prompt"]
    assert "fabricating the comparison" in client.calls[0]["prompt"]


def test_the_weak_model_rules_stay_out_of_the_hosted_writers_prompt():
    """These rules were measured against one local model's habits. The hosted
    writer does not have them, and two of the rules would cost it something it
    does it on purpose — so it never sees them."""
    client = ScriptedClient(RECORDED["entry"])
    write_entry(_cluster(), Config(), client, [])
    assert "Habits to avoid" not in client.calls[0]["prompt"]


@pytest.mark.parametrize(
    "provider, model, wanted",
    [
        ("ollama", "gemma3:27b", True),          # measured: these are its habits
        ("ollama", "some-new-local-model", True),  # unmeasured, so assume it needs them
        ("anthropic", "claude-sonnet-5", False),   # scored zero on every habit
    ],
)
def test_the_weak_model_rules_follow_the_writer_not_the_provider(provider, model, wanted):
    from digest.config import ModelsCfg

    client = ScriptedClient(RECORDED["entry"])
    cfg = Config(models=ModelsCfg(synthesize_provider=provider, synthesize=model))
    write_entry(_cluster(), cfg, client, [])
    assert ("Habits to avoid" in client.calls[0]["prompt"]) is wanted


def _payload(body: str, headline: str = "A headline about a change") -> str:
    return json.dumps(
        {"headline": headline, "body": body, "hook": "A hook.", "questions": []}
    )


def _bank_cluster() -> Cluster:
    """A cluster whose stories mention an acronym but never spell it out."""
    return Cluster(
        cluster_id="c1",
        title="The IMF raises its forecast",
        items=[make_classified(
            item={"title": "The IMF raises its forecast",
                  "blurb": "The fund lifted its estimate for the year."},
            mechanism="forecast revision",
        )],
        shared_mechanism="forecast revision",
    )


def test_an_invented_institution_is_named_back_to_the_writer():
    """Told that outside detail counts as invented, gemma3 still answered a
    one-line blurb on chip controls with two agencies by name. Saying it again
    with the names in it is the only version the model cannot read past."""
    client = ScriptedClient(
        _payload("The Bureau of Industry and Security will now act."),
        _payload("The stories describe a change in export controls."),
    )
    entry = write_entry(_cluster(), Config(), client, [])
    assert len(client.calls) == 2
    assert "Bureau of Industry and Security" in client.calls[1]["prompt"]
    assert entry is not None and "Bureau" not in entry.body


def test_an_entry_that_keeps_inventing_is_dropped():
    """A shorter briefing beats a fluent false one. The caller marks the
    edition [PARTIAL], so the loss shows."""
    client = ScriptedClient(
        _payload("The Dodd-Frank Act applies here."),
        _payload("The Dodd-Frank Act still applies here."),
    )
    assert write_entry(_cluster(), Config(), client, []) is None
    assert len(client.calls) == 2


def test_spelling_out_an_acronym_the_stories_use_is_not_invention():
    """The prompt tells the writer to spell acronyms out. A guard that then
    calls the expansion invented would punish the rule it enforces."""
    client = ScriptedClient(
        _payload("The International Monetary Fund lifted its estimate.")
    )
    assert write_entry(_bank_cluster(), Config(), client, []) is not None
    assert len(client.calls) == 1


def test_a_clean_entry_is_written_once():
    client = ScriptedClient(RECORDED["entry"])
    assert write_entry(_cluster(), Config(), client, []) is not None
    assert len(client.calls) == 1


def test_a_possessive_is_the_same_name_wearing_an_apostrophe():
    """"Bank of Japan's" is the Bank of Japan. Reading the apostrophe as part of
    the name makes the guard drop entries about institutions the stories named."""
    from digest.synthesize import novel_names

    assert novel_names("The Bank of Japan’s decision held.", "Bank of Japan holds rates") == []


def test_geography_the_stories_imply_is_left_alone():
    """Saying Egypt is in North Africa is reasoning, not invention, and a guard
    that needs a gazetteer to tell the difference rots."""
    from digest.synthesize import novel_names

    assert novel_names("The jets flew over North Africa.", "Chinese jets over Egypt") == []


def test_a_failed_entry_is_skipped_and_marks_the_edition_partial():
    class Broken(ScriptedClient):
        def complete(self, **kwargs):
            from digest.llm import LLMError

            raise LLMError("boom")

    edition = synthesize([_cluster()], Config(), Broken(), "2026-W36")
    assert edition.quiet and edition.partial


def test_a_failed_frame_falls_back_to_fit_order_and_marks_partial():
    class OneShot(ScriptedClient):
        def complete(self, **kwargs):
            if "synthesize_frame" in kwargs["prompt"] or "closing_questions" in kwargs["prompt"]:
                from digest.llm import LLMError

                raise LLMError("boom")
            return RECORDED["entry"]

    edition = synthesize([_cluster()], Config(), OneShot(), "2026-W36")
    assert edition.partial and len(edition.entries) == 1
    assert edition.opening


def test_the_frame_reorders_entries_and_names_the_theme():
    entries = [
        Entry(cluster_id="c2", headline="B", body="", hook="", fit=2),
        Entry(cluster_id="c1", headline="A", body="", hook="", fit=3),
    ]
    opening, closing, ordered, theme, degraded = write_frame(
        entries, Config(), ScriptedClient(RECORDED["frame"]), _cluster("c1", n=3)
    )
    assert [e.cluster_id for e in ordered] == ["c1", "c2"]
    assert theme == "Rules that close off an option"
    assert len(closing) == 3 and opening and not degraded


def test_the_frame_cannot_name_a_theme_when_no_cluster_qualifies():
    """Told there is no theme and to return null, gemma3 named one anyway — in
    the same edition whose own opening said the entries had nothing in common.
    Whether a theme exists is decided upstream; the model only names it."""
    entries = [Entry(cluster_id="c1", headline="A", body="", hook="", fit=3)]
    _, _, _, theme, _ = write_frame(
        entries, Config(), ScriptedClient(RECORDED["frame"]), None
    )
    assert theme is None


def test_an_entry_the_frame_forgot_is_still_published():
    entries = [
        Entry(cluster_id="c1", headline="A", body="", hook="", fit=3),
        Entry(cluster_id="c2", headline="B", body="", hook="", fit=2),
        Entry(cluster_id="c9", headline="C", body="", hook="", fit=1),
    ]
    _, _, ordered, _, _ = write_frame(
        entries, Config(), ScriptedClient(RECORDED["frame"]), None
    )
    assert {e.cluster_id for e in ordered} == {"c1", "c2", "c9"}


# ------------------------------------------------------------ length governor


def _entry(cid: str, fit: int, words: int, items: int = 1) -> Entry:
    return Entry(
        cluster_id=cid, headline="h", body="word " * words, hook="", fit=fit, item_count=items
    )


def test_the_governor_leaves_a_short_edition_alone():
    entries = [_entry("c1", 3, 50), _entry("c2", 2, 50)]
    assert govern_length(entries, 8500, None) == entries


def test_the_governor_drops_the_lowest_fit_singleton_first():
    entries = [_entry("c1", 3, 60), _entry("c2", 1, 60), _entry("c3", 2, 60)]
    kept = govern_length(entries, 130, None)
    assert [e.cluster_id for e in kept] == ["c1", "c3"]


def test_the_governor_keeps_multi_item_entries_and_the_theme():
    entries = [_entry("theme", 1, 200), _entry("multi", 1, 200, items=3), _entry("solo", 3, 200)]
    kept = govern_length(entries, 100, theme_id="theme")
    assert {e.cluster_id for e in kept} == {"theme", "multi"}


def test_the_governor_stops_rather_than_emptying_the_edition():
    entries = [_entry("multi", 3, 5000, items=4)]
    assert govern_length(entries, 100, None) == entries


def test_no_clusters_yields_the_quiet_week_note():
    edition = synthesize([], Config(run=RunCfg()), ScriptedClient(), "2026-W36")
    assert edition.quiet and edition.entries == [] and "Nothing this week" in edition.opening


def test_the_governor_reserves_room_for_the_frame():
    """Pass B adds an opening and three questions after the governor has run, so
    the ceiling it enforces has to sit below max_words."""
    from digest.config import Config as Cfg
    from digest.synthesize import FRAME_RESERVE_WORDS

    max_words = 1000
    clusters = [_cluster(f"c{n}") for n in range(30)]
    client = ScriptedClient(*([RECORDED["entry"]] * 30), RECORDED["frame"])
    edition = synthesize(
        clusters, Cfg(run=RunCfg(max_words=max_words)), client, "2026-W36"
    )
    entry_words = sum(e.word_count for e in edition.entries)
    assert entry_words <= max_words - FRAME_RESERVE_WORDS
    assert edition.word_count <= max_words


# ------------------------------------------------------- carrying source text


def _reported(chars: int = 900, kind: str = "article") -> Cluster:
    from digest.models import Evidence

    item = make_classified(item={"title": "A judge struck the rule down", "blurb": "Short."})
    item.evidence = [Evidence(kind=kind, text="The court said so plainly. " * (chars // 27))]
    return Cluster(cluster_id="c1", title="t", items=[item], shared_mechanism="m")


def test_a_story_someone_already_wrote_is_not_rewritten():
    client = ScriptedClient(RECORDED["entry"])
    entry = write_entry(_reported(), Config(), client, [])
    assert client.calls == []
    assert entry.provenance == "source"
    assert entry.attribution == "Economist — Business"
    assert entry.headline == "A judge struck the rule down"


def test_a_search_snippet_is_not_somebody_writing_the_story_up():
    """Other outlets glossing the event are corroboration. Publishing them as
    the source would misstate where the words came from."""
    client = ScriptedClient(_payload("The court said so plainly."))
    entry = write_entry(_reported(kind="search"), Config(), client, [])
    assert entry.provenance == "written" and len(client.calls) == 1


def test_a_cluster_of_several_stories_is_still_synthesised():
    """No single outlet wrote the thing several stories have in common, so
    there is nothing to carry — splicing two articles is neither their words
    nor an honest summary."""
    from digest.models import Evidence

    cluster = _reported()
    second = make_classified(item={"url": "https://e.com/2"})
    second.evidence = [Evidence(kind="article", text="More text. " * 200)]
    cluster.items.append(second)
    client = ScriptedClient(_payload("The court said so plainly."))
    entry = write_entry(cluster, Config(), client, [])
    assert entry.provenance == "written" and len(client.calls) == 1


def test_a_thin_report_is_still_written_by_the_model():
    client = ScriptedClient(_payload("The court said so plainly."))
    entry = write_entry(_reported(chars=100), Config(), client, [])
    assert entry.provenance == "written" and len(client.calls) == 1


def test_a_carried_excerpt_stops_on_a_sentence_boundary():
    from digest.config import RunCfg
    from digest.synthesize import _excerpt

    entry = write_entry(_reported(), Config(run=RunCfg(source_max_words=20)), ScriptedClient(), [])
    assert entry.body.endswith(".")
    assert len(entry.body.split()) <= 25
    assert _excerpt("One two three. Four five six.", 3) == "One two three."


def test_the_reader_is_told_whose_words_they_are():
    from digest.emit import render_md, render_txt

    edition = synthesize([_reported()], Config(), ScriptedClient(), "2026-W36")
    assert "Economist — Business's own words" in render_md(edition)
    assert "In Economist — Business's own words." in render_txt(edition)
    assert "carried in the reporter's own words" in render_md(edition)


def test_a_lone_report_is_carried_without_waiting_for_clustering():
    """Deciding after grouping meant a story a person wrote in full got
    rewritten whenever the grouping happened to absorb it."""
    from digest.synthesize import partition_carried

    rows = [_reported().items[0]]
    carried, rest = partition_carried(rows, Config())
    assert len(carried) == 1 and rest == []


def test_two_outlets_on_one_event_go_to_the_model_instead():
    """Combining several accounts of one event is the one thing the model does
    that no single article can, and printing both verbatim prints it twice."""
    from digest.models import Evidence
    from digest.synthesize import partition_carried

    def report(title):
        row = make_classified(item={"title": title, "url": f"https://e.com/{title[:9]}"})
        row.evidence = [Evidence(kind="article", text="The deal closed today. " * 45)]
        return row

    rows = [
        report("Nvidia to buy Hugging Face for nearly $13 billion"),
        report("Nvidia buys Hugging Face, the GitHub of AI, for $13 billion"),
    ]
    carried, rest = partition_carried(rows, Config())
    assert carried == [] and len(rest) == 2


def test_a_thin_story_is_never_carried():
    from digest.synthesize import partition_carried

    rows = [make_classified(item={"blurb": "Short."})]
    carried, rest = partition_carried(rows, Config())
    assert carried == [] and len(rest) == 1


def test_carried_clusters_cannot_collide_with_the_models_ids():
    from digest.synthesize import carried_clusters

    ids = [c.cluster_id for c in carried_clusters(_reported().items)]
    assert ids == ["s1"] and not any(i.startswith("c") for i in ids)
