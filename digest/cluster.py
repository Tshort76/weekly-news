"""Group the selected items. One call to the larger model; falls back to
singletons so a failure here costs ordering, not the edition."""

from __future__ import annotations

import json
import logging

from rapidfuzz import fuzz

from .config import Config
from .llm import Client, LLMError
from .models import Classified, Cluster

log = logging.getLogger("digest.cluster")

THEME_MIN_ITEMS = 3

# Two headlines about the same event still say many of the same words. Measured
# over a week's selected items: unrelated pairs peaked at 57 and sat at a median
# of 38, while genuine same-event pairs ran 70 and up. 60 clears the noise.
SAME_EVENT_THRESHOLD = 60


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


def _same_event_groups(members: list[Classified]) -> list[list[Classified]]:
    """Split members into runs that are visibly about the same event.

    Used only when the model grouped items without naming a mechanism. Two
    reports of one event share names and numbers, so their titles overlap;
    a topic folder's members have nothing in common but the folder.
    """
    groups: list[list[Classified]] = []
    for member in members:
        for group in groups:
            if any(
                fuzz.token_set_ratio(member.item.title.lower(), other.item.title.lower())
                >= SAME_EVENT_THRESHOLD
                for other in group
            ):
                group.append(member)
                break
        else:
            groups.append([member])
    return groups


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
        mechanism = mechanism.strip() if isinstance(mechanism, str) and mechanism.strip() else None
        title = str(group.get("title") or members[0].item.title)[:120]

        # A group of several items with no mechanism named is the failure this
        # guards against: asked to group by a shared mechanism, gemma3 returned
        # topic folders — "US Policy & Finance" holding Ethiopia's drone war
        # next to Silicon Valley philanthropy, mechanism null on every one. The
        # prompt allows a null mechanism only for items covering one event, and
        # that is a thing we can check ourselves.
        if len(members) > 1 and not mechanism:
            for part in _same_event_groups(members):
                if len(part) == len(members):
                    break  # genuinely one event after all
                log.info(
                    "splitting %r: %d items, no shared mechanism named", title, len(members)
                )
                clusters.append(
                    Cluster(
                        cluster_id=f"c{len(clusters) + 1}",
                        title=part[0].item.title[:120],
                        items=part,
                        shared_mechanism=part[0].mechanism if len(part) > 1 else None,
                    )
                )
            else:
                continue

        clusters.append(
            Cluster(
                cluster_id=str(group.get("cluster_id") or f"c{n}"),
                title=title,
                items=members,
                shared_mechanism=mechanism,
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
