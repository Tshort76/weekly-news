"""Batched classification against the rubric. Haiku sees title, blurb, source and
section — never an article body."""

from __future__ import annotations

import json
import logging

from .config import Config
from .llm import Client, LLMError
from .models import Classified, Item

log = logging.getLogger("digest.classify")

# The three fixed slots every lens sorts into. The words the model is shown are
# the lens's ("architecture" / "contest" here); the slot is what the code keys
# on, so the balance rule keeps working whatever a lens calls its own subject.
KIND_SLOTS = ("core", "adjacent", "neither")


def enum_line(words) -> str:
    """The literal the prompt shows: \"a\" | \"b\" | \"c\"."""
    return " | ".join(f'"{w}"' for w in words)


def batch_schema(count: int, lens=None) -> dict:
    """The exact shape a batch must come back in.

    Backends that can constrain generation use this; the others ignore it and
    fall back to the tolerant parser. It is what stops a local model answering a
    batch of twenty-five with a single object.
    """
    from .lens.presets import default_lens  # noqa: PLC0415

    lens = lens or default_lens()
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
                "kind": {"type": "string", "enum": sorted(lens.kinds.words())},
                "region": {"type": "string", "enum": sorted(lens.regions)},
                "domain": {"type": "string", "enum": sorted(lens.domains)},
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


def _coerce(raw: dict, item: Item, lens=None) -> Classified:
    from .lens.presets import default_lens  # noqa: PLC0415

    lens = lens or default_lens()
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
        kind=lens.kinds.slot_for(kind),
        novelty=_clamp(raw.get("novelty"), 0, 3, 0),
        region=region if region in lens.regions else lens.regions[-1],
        domain=domain if domain in lens.domains else lens.domains[-1],
        mechanism=mechanism,
        reason=str(raw.get("reason", ""))[:200],
    )


def _unjudged(item: Item, why: str, lens=None) -> Classified:
    from .lens.presets import default_lens  # noqa: PLC0415

    lens = lens or default_lens()
    return Classified(
        item=item, fit=0, kind="neither", novelty=0,
        region=lens.regions[-1], domain=lens.domains[-1], mechanism=None,
        reason=f"classification failed: {why}",
    )


def classify_batch(batch: list[Item], cfg: Config, client: Client) -> list[Classified]:
    lens = cfg.lens
    prompt = cfg.prompt("classify.md").format(
        rubric=cfg.lens_text,
        count=len(batch),
        items=_render_items(batch),
        kinds=enum_line(lens.kinds.words()),
        regions=enum_line(lens.regions),
        domains=enum_line(lens.domains),
    )
    try:
        payload = client.complete_json(
            stage="classify",
            prompt=prompt,
            max_tokens=400 * len(batch) + 1000,
            schema=batch_schema(len(batch), lens),
        )
    except LLMError as exc:
        log.error("batch of %d failed: %s", len(batch), exc)
        return [_unjudged(it, str(exc)[:120], lens) for it in batch]

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
        out.append(_coerce(row, item, lens) if row else _unjudged(item, "missing from response", lens))
    return out


def classify(items: list[Item], cfg: Config, client: Client) -> list[Classified]:
    results: list[Classified] = []
    batches = _batches(items, cfg.models.classify_batch_size)
    for n, batch in enumerate(batches, 1):
        log.info("classifying batch %d/%d (%d items)", n, len(batches), len(batch))
        results.extend(classify_batch(batch, cfg, client))
    return results
