"""Model discovery and recommendation. Every fetch is injected; nothing dials out."""

from __future__ import annotations

import json

import pytest

from digest import discover
from digest.config import Config, ModelsCfg


def canned(tags: dict, show: dict | None = None):
    def fetch(url: str, timeout: float = 3.0, payload: dict | None = None) -> bytes:
        return json.dumps(show if "show" in url else tags).encode()
    return fetch


def refused(*args, **kwargs):
    raise ConnectionRefusedError("nothing listening on 11434")


def test_a_running_ollama_reports_what_is_pulled():
    found = discover.probe_ollama(fetch=canned({"models": [{"name": "qwen3:30b"}]}))
    assert found.running and found.names() == ["qwen3:30b"]


def test_not_running_and_not_installed_get_different_advice(monkeypatch):
    monkeypatch.setattr(discover, "ollama_installed", lambda: True)
    assert "start it" in discover.probe_ollama(fetch=refused).reason.lower()
    monkeypatch.setattr(discover, "ollama_installed", lambda: False)
    assert "ollama.com" in discover.probe_ollama(fetch=refused).reason


def test_a_reasoning_model_has_its_thinking_switched_off():
    """Measured: thinking on scored zero and dropped all eleven that belonged."""
    fetch = canned({}, {"capabilities": ["completion", "thinking"]})
    assert discover.wants_thinking_off("qwen3:30b", fetch=fetch) is True


def test_a_plain_model_is_left_alone():
    fetch = canned({}, {"capabilities": ["completion", "vision"]})
    assert discover.wants_thinking_off("gemma3:27b", fetch=fetch) is False


def test_an_unreachable_ollama_does_not_claim_a_model_thinks():
    assert discover.wants_thinking_off("anything", fetch=refused) is False


def test_a_measured_model_that_is_pulled_and_fits_is_the_recommendation():
    found = discover.probe_ollama(fetch=canned({"models": [{"name": "qwen3:30b"}]}))
    got = discover.recommend("classify", found, memory_gb=48)
    assert got["model"] == "qwen3:30b" and got["measured"] is True
    assert "pull" not in got


def test_a_measured_model_that_fits_but_is_missing_is_offered_with_its_size():
    found = discover.probe_ollama(fetch=canned({"models": []}))
    got = discover.recommend("classify", found, memory_gb=48)
    assert got["pull"] is True and "GB to download" in got["why"]


def test_a_small_machine_is_not_told_to_run_a_twenty_gigabyte_model():
    found = discover.probe_ollama(fetch=canned({"models": [{"name": "qwen3:30b"}]}))
    got = discover.recommend("classify", found, memory_gb=16)
    assert got["provider"] == "anthropic"


def test_an_unmeasured_local_model_is_offered_and_labelled_as_such():
    """The app never invents a score for a model nobody here has run."""
    found = discover.probe_ollama(fetch=canned({"models": [{"name": "llama4:8b"}]}))
    got = discover.recommend("classify", found, memory_gb=16)
    assert got["model"] == "llama4:8b"
    assert got["measured"] is False and "not yet measured" in got["why"].lower()


@pytest.mark.parametrize(
    "provider, model, wanted",
    [
        ("ollama", "gemma3:27b", True),
        ("ollama", "something-unmeasured", True),
        ("anthropic", "claude-sonnet-5", False),
        ("gemini", "gemini-3.8-flash", False),
    ],
)
def test_the_writer_notes_follow_the_model_with_the_provider_as_tiebreak(
    provider, model, wanted
):
    cfg = Config(models=ModelsCfg(provider=provider, synthesize=model))
    assert discover.writes_like_a_small_model(cfg) is wanted


def test_memory_is_reported_or_zero_never_a_guess():
    assert discover.total_memory_gb() >= 0
