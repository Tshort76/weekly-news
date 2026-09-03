"""Group the selected items. One call to the larger model; falls back to
singletons so a failure here costs ordering, not the edition."""

from __future__ import annotations

import json
import logging

from .config import Config
from .llm import Client, LLMError
from .models import Classified, Cluster

log = logging.getLogger("digest.cluster")

THEME_MIN_ITEMS = 3


def cluster_schema() -> dict:
    """The shape a clustering response must take. Local models need the array
    pinned or they answer with a single group and stop."""
    return {
        "type": "array",
        "minItems": 1,
        "items": {
            "type": "object",
            "required": ["cluster_id", "title", "item_ids", "shared_mechanism"],
            "properties": {
                "cluster_id": {"type": "string"},
                "title": {"type": "string"},
                "item_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "shared_mechanism": {"type": ["string", "null"]},
            },
        },
    }


def singletons(selected: list[Classified]) -> list[Cluster]:
    return [
        Cluster(cluster_id=f"c{n}", title=c.item.title, items=[c], shared_mechanism=c.mechanism)
        for n, c in enumerate(selected, 1)
    ]


def _render(selected: list[Classified]) -> str:
    return "\n".join(
        json.dumps(
            {
                "id": c.id,
                "title": c.item.title,
                "blurb": c.item.blurb,
                "region": c.region,
                "domain": c.domain,
                "mechanism": c.mechanism,
            },
            ensure_ascii=False,
        )
        for c in selected
    )


def cluster(selected: list[Classified], cfg: Config, client: Client) -> tuple[list[Cluster], bool]:
    """Return (clusters, degraded). `degraded` marks the edition partial."""
    if not selected:
        return [], False

    prompt = cfg.prompt("cluster.md").format(items=_render(selected))
    try:
        payload = client.complete_json(
            stage="synthesize", prompt=prompt, max_tokens=16000, schema=cluster_schema()
        )
    except LLMError as exc:
        log.error("clustering failed, treating every item as its own cluster: %s", exc)
        return singletons(selected), True

    by_id = {c.id: c for c in selected}
    used: set[str] = set()
    clusters: list[Cluster] = []
    for n, group in enumerate(payload if isinstance(payload, list) else [], 1):
        if not isinstance(group, dict):
            continue
        members = [
            by_id[i] for i in group.get("item_ids", [])
            if i in by_id and i not in used
        ]
        if not members:
            continue
        used.update(m.id for m in members)
        mechanism = group.get("shared_mechanism")
        clusters.append(
            Cluster(
                cluster_id=str(group.get("cluster_id") or f"c{n}"),
                title=str(group.get("title") or members[0].item.title)[:120],
                items=members,
                shared_mechanism=mechanism.strip() if isinstance(mechanism, str) and mechanism.strip() else None,
            )
        )

    # Anything the model forgot still gets into the edition.
    leftover = [c for c in selected if c.id not in used]
    for n, c in enumerate(leftover, len(clusters) + 1):
        log.warning("item missing from clustering, adding as a singleton: %s", c.item.title)
        clusters.append(
            Cluster(cluster_id=f"c{n}", title=c.item.title, items=[c], shared_mechanism=c.mechanism)
        )

    return clusters, False


def theme_candidate(clusters: list[Cluster]) -> Cluster | None:
    """The one cluster allowed to lead the edition: at least three items sharing
    a mechanism, best fit first."""
    candidates = [
        c for c in clusters
        if len(c.items) >= THEME_MIN_ITEMS and c.shared_mechanism
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c.fit, len(c.items)))
