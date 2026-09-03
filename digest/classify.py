"""Batched classification against the rubric. Haiku sees title, blurb, source and
section — never an article body."""

from __future__ import annotations

import json
import logging

from .config import Config
from .llm import Client, LLMError
from .models import Classified, Item

log = logging.getLogger("digest.classify")

VALID_KINDS = {"architecture", "contest", "neither"}
VALID_REGIONS = {
    "east_asia", "south_asia", "europe", "uk", "us",
    "mena", "africa", "latam", "global",
}
VALID_DOMAINS = {
    "finance", "trade", "industry", "state", "tech",
    "energy", "demography", "security", "other",
}


def batch_schema(count: int) -> dict:
    """The exact shape a batch must come back in.

    Backends that can constrain generation use this; the others ignore it and
    fall back to the tolerant parser. It is what stops a local model answering a
    batch of twenty-five with a single object.
    """
    return {
        "type": "array",
        "minItems": count,
        "maxItems": count,
        "items": {
            "type": "object",
            "required": ["id", "fit", "kind", "novelty", "region", "domain", "mechanism", "reason"],
            "properties": {
                "id": {"type": "string"},
                "fit": {"type": "integer", "minimum": 0, "maximum": 3},
                "novelty": {"type": "integer", "minimum": 0, "maximum": 3},
                "kind": {"type": "string", "enum": sorted(VALID_KINDS)},
                "region": {"type": "string", "enum": sorted(VALID_REGIONS)},
                "domain": {"type": "string", "enum": sorted(VALID_DOMAINS)},
                "mechanism": {"type": ["string", "null"]},
                "reason": {"type": "string"},
            },
        },
    }


def _batches(items: list[Item], size: int) -> list[list[Item]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _render_items(batch: list[Item]) -> str:
    return "\n\n".join(
        json.dumps(
            {
                "id": it.id,
                "title": it.title,
                "blurb": it.blurb,
                "source": it.source,
                "section": it.section,
            },
            ensure_ascii=False,
        )
        for it in batch
    )


def _clamp(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _coerce(raw: dict, item: Item) -> Classified:
    kind = str(raw.get("kind", "neither")).strip().lower()
    region = str(raw.get("region", "global")).strip().lower()
    domain = str(raw.get("domain", "other")).strip().lower()
    mechanism = raw.get("mechanism")
    if isinstance(mechanism, str):
        mechanism = mechanism.strip() or None
        if mechanism and mechanism.lower() in {"null", "none", "n/a"}:
            mechanism = None
    else:
        mechanism = None
    return Classified(
        item=item,
        fit=_clamp(raw.get("fit"), 0, 3, 0),
        kind=kind if kind in VALID_KINDS else "neither",
        novelty=_clamp(raw.get("novelty"), 0, 3, 0),
        region=region if region in VALID_REGIONS else "global",
        domain=domain if domain in VALID_DOMAINS else "other",
        mechanism=mechanism,
        reason=str(raw.get("reason", ""))[:200],
    )


def _unjudged(item: Item, why: str) -> Classified:
    return Classified(
        item=item, fit=0, kind="neither", novelty=0,
        region="global", domain="other", mechanism=None,
        reason=f"classification failed: {why}",
    )


def classify_batch(batch: list[Item], cfg: Config, client: Client) -> list[Classified]:
    prompt = cfg.prompt("classify.md").format(
        rubric=cfg.prompt("rubric.md"),
        count=len(batch),
        items=_render_items(batch),
    )
    try:
        payload = client.complete_json(
            stage="classify",
            prompt=prompt,
            max_tokens=400 * len(batch) + 1000,
            schema=batch_schema(len(batch)),
        )
    except LLMError as exc:
        log.error("batch of %d failed: %s", len(batch), exc)
        return [_unjudged(it, str(exc)[:120]) for it in batch]

    if not isinstance(payload, list):
        payload = [payload]

    # Match on id. Position is only a fallback for a response that echoed no ids
    # at all — using it per-item would silently paste one item's verdict onto
    # another whenever the model skips one.
    by_id = {
        str(row.get("id")): row
        for row in payload
        if isinstance(row, dict) and row.get("id")
    }
    positional = not by_id and len(payload) == len(batch)

    out: list[Classified] = []
    for pos, item in enumerate(batch):
        row = payload[pos] if positional else by_id.get(item.id)
        if not isinstance(row, dict):
            row = None
        out.append(_coerce(row, item) if row else _unjudged(item, "missing from response"))
    return out


def classify(items: list[Item], cfg: Config, client: Client) -> list[Classified]:
    results: list[Classified] = []
    batches = _batches(items, cfg.models.classify_batch_size)
    for n, batch in enumerate(batches, 1):
        log.info("classifying batch %d/%d (%d items)", n, len(batches), len(batch))
        results.extend(classify_batch(batch, cfg, client))
    return results
