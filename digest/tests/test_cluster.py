"""Clustering, and the guard against a model returning topic folders."""

from __future__ import annotations

import json

from digest.cluster import cluster, singletons, theme_candidate
from digest.config import Config
from digest.models import Cluster

from .conftest import make_classified


class Scripted:
    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, **kwargs):
        return self.payload

    def complete(self, **kwargs):
        return json.dumps(self.payload)


def _items(*titles):
    return [make_classified(item={"title": t, "url": f"https://e.com/{n}"})
            for n, t in enumerate(titles)]


def test_a_folder_with_no_mechanism_is_broken_back_into_singletons():
    """Asked to group by shared mechanism, gemma3 returned "US Policy & Finance"
    holding Ethiopia's drone war beside Silicon Valley philanthropy, with a null
    mechanism on every group. A folder is worse than no grouping: it tells the
    writer these belong together."""
    items = _items(
        "Ethiopia's undeclared drone war",
        "Coefficient Giving's CEO on Silicon Valley philanthropy",
        "Mexico is struggling to win over bond markets",
    )
    payload = [{"cluster_id": "c1", "title": "US Policy & Finance",
                "item_ids": [i.id for i in items], "shared_mechanism": None}]
    clusters, _ = cluster(items, Config(), Scripted(payload))
    assert len(clusters) == 3
    assert all(len(c.items) == 1 for c in clusters)


def test_one_event_reported_twice_survives_without_a_mechanism():
    """The prompt allows a null mechanism for items covering a single event, and
    two reports of one event share their names and numbers."""
    items = _items(
        "Nvidia to buy Hugging Face for nearly $13 billion",
        "Nvidia buys Hugging Face, the GitHub of AI, for $13 billion",
    )
    payload = [{"cluster_id": "c1", "title": "Nvidia buys Hugging Face",
                "item_ids": [i.id for i in items], "shared_mechanism": None}]
    clusters, _ = cluster(items, Config(), Scripted(payload))
    assert len(clusters) == 1 and len(clusters[0].items) == 2


def test_a_named_mechanism_is_taken_at_its_word():
    items = _items("Chevron doubles Venezuela output", "America secures Venezuelan oil")
    payload = [{"cluster_id": "c1", "title": "Venezuela oil",
                "item_ids": [i.id for i in items],
                "shared_mechanism": "US firm expands Venezuelan production"}]
    clusters, _ = cluster(items, Config(), Scripted(payload))
    assert len(clusters) == 1 and clusters[0].shared_mechanism


def test_an_item_the_model_forgot_still_reaches_the_edition():
    items = _items("One story", "Another story")
    payload = [{"cluster_id": "c1", "title": "One story",
                "item_ids": [items[0].id], "shared_mechanism": None}]
    clusters, _ = cluster(items, Config(), Scripted(payload))
    assert {c.items[0].item.title for c in clusters} == {"One story", "Another story"}


def test_a_broken_folder_cannot_become_the_theme_of_the_week():
    """The theme leads the edition, so a folder becoming one puts the worst
    grouping of the week at the top."""
    items = _items("Ethiopia drone war", "Silicon Valley philanthropy", "Mexico bond markets")
    payload = [{"cluster_id": "c1", "title": "US Policy & Finance",
                "item_ids": [i.id for i in items], "shared_mechanism": None}]
    clusters, _ = cluster(items, Config(), Scripted(payload))
    assert theme_candidate(clusters) is None


def test_singletons_reproduce_the_fallback_shape():
    items = _items("a", "b")
    assert [c.cluster_id for c in singletons(items)] == ["c1", "c2"]
