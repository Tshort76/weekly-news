"""Synthesis against recorded model responses — no network."""

from __future__ import annotations

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


def test_a_local_writer_is_given_the_weak_model_rules():
    from digest.config import ModelsCfg

    client = ScriptedClient(RECORDED["entry"])
    cfg = Config(models=ModelsCfg(synthesize_provider="ollama"))
    write_entry(_cluster(), cfg, client, [])
    assert "Habits to avoid" in client.calls[0]["prompt"]


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
