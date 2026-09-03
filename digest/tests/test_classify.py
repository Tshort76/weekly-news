"""Classification against a recorded model response — no network."""

from __future__ import annotations

from digest.classify import classify
from digest.config import Config
from digest.llm import extract_json
from digest.models import Item

from .conftest import load_fixture, make_item


class RecordedClient:
    """Stands in for digest.llm.Client, replaying a saved response."""

    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict] = []

    def complete(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self.text

    def complete_json(self, **kwargs):
        return extract_json(self.complete(**kwargs))


def _items() -> list[Item]:
    return [
        make_item(url="https://e.com/1", title="Bank of Japan rewrites its framework"),
        make_item(url="https://e.com/2", title="The polls tighten"),
        make_item(url="https://e.com/3", title="Indonesia imposes capital controls"),
    ]


def _client(items: list[Item]) -> RecordedClient:
    text = load_fixture("classify_response.json")["text"]
    for n, item in enumerate(items):
        text = text.replace(f"ID{n}", item.id)
    return RecordedClient(text)


def test_a_recorded_response_maps_onto_the_items_by_id():
    items = _items()
    out = classify(items, Config(), _client(items))
    assert [c.id for c in out] == [i.id for i in items]
    assert [c.fit for c in out] == [3, 0, 3]
    assert out[0].mechanism == "reserve quantity target replaces price target"
    assert out[1].mechanism is None


def test_the_prompt_never_carries_an_article_body():
    items = _items()
    client = _client(items)
    classify(items, Config(), client)
    prompt = client.calls[0]["prompt"]
    assert "<rubric>" in prompt
    for item in items:
        assert item.title in prompt
    assert "body" not in prompt.split("<items>")[1]


def test_a_response_missing_an_item_leaves_it_unjudged_rather_than_misaligned():
    items = _items()
    client = RecordedClient(
        f'[{{"id": "{items[2].id}", "fit": 3, "kind": "architecture", "novelty": 3, '
        '"region": "south_asia", "domain": "trade", "mechanism": "m", "reason": "r"}]'
    )
    out = classify(items, Config(), client)
    assert out[2].fit == 3
    assert [c.fit for c in out[:2]] == [0, 0]
    assert "classification failed" in out[0].reason or "missing" in out[0].reason


def test_a_failing_call_yields_fit_zero_for_the_whole_batch():
    class Broken(RecordedClient):
        def complete(self, **kwargs):
            from digest.llm import LLMError

            raise LLMError("boom")

    out = classify(_items(), Config(), Broken(""))
    assert all(c.fit == 0 and c.kind == "neither" for c in out)


def test_out_of_range_values_are_clamped():
    items = _items()[:1]
    client = RecordedClient(
        f'[{{"id": "{items[0].id}", "fit": 9, "kind": "vibes", "novelty": -1, '
        '"region": "mars", "domain": "sport", "mechanism": "  ", "reason": "r"}]'
    )
    out = classify(items, Config(), client)
    assert (out[0].fit, out[0].novelty) == (3, 0)
    assert out[0].kind == "neither" and out[0].region == "global"
    assert out[0].domain == "other" and out[0].mechanism is None
