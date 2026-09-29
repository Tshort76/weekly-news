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
