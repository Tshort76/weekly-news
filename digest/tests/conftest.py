from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest

from digest.config import Config, RunCfg
from digest.models import Classified, Item
from digest.normalize import item_id

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any test that opens a socket fails, here, with a message that says why.

    The suite is fast because every network edge is a function a test can
    replace. That was a convention, and a convention is one distracted afternoon
    away from a test that quietly dials out and takes eight seconds — which is
    exactly what happened when the grounding stage was added. This makes it a
    property instead, and it holds on a machine with no network at all.
    """

    def refuse(*args, **kwargs):
        raise AssertionError(
            "this test tried to open a network connection. Stub the edge it uses "
            "— fetch_bytes, urlopen, the Client, or cfg.run.ground — rather than "
            "letting the suite depend on someone else's uptime."
        )

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def digest_home(tmp_path, monkeypatch):
    """An install of one's own: config and data under tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DIGEST_HOME", str(home))
    return home


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def cfg(tmp_path) -> Config:
    # ground=False keeps the suite off the network. The grounding stage has its
    # own tests with every fetch stubbed; a pipeline test that quietly dialled
    # out would be slow, flaky and dependent on someone else's uptime.
    return Config(
        run=RunCfg(output_dir=tmp_path / "out", ground=False),
        state_dir=tmp_path / "state",
    )


def make_item(**kwargs) -> Item:
    base = {
        "id": "x",
        "source": "Economist — Business",
        "section": "business",
        "title": "A title",
        "blurb": "A blurb",
        "url": "https://example.com/a",
        "published": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "weight": 1.0,
    }
    base.update(kwargs)
    if "id" not in kwargs:
        base["id"] = item_id(base["url"])
    return Item(**base)


def make_classified(**kwargs) -> Classified:
    item_kwargs = kwargs.pop("item", {})
    base = {
        "fit": 3,
        "kind": "core",
        "novelty": 3,
        "region": "global",
        "domain": "finance",
        "mechanism": "some mechanism",
        "reason": "because",
    }
    base.update(kwargs)
    return Classified(item=make_item(**item_kwargs), **base)
