from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from digest.config import Config, RunCfg
from digest.models import Classified, Item
from digest.normalize import item_id

FIXTURES = Path(__file__).parent / "fixtures"


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
        "kind": "architecture",
        "novelty": 3,
        "region": "global",
        "domain": "finance",
        "mechanism": "some mechanism",
        "reason": "because",
    }
    base.update(kwargs)
    return Classified(item=make_item(**item_kwargs), **base)
