"""Two passes: an Entry per cluster, then the frame around them.

Every failure here degrades rather than aborts — a missing entry is skipped, a
missing frame falls back to fit order and stock wording, and either marks the
edition [PARTIAL].
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from rapidfuzz import fuzz

from .cluster import theme_candidate
from .config import Config
from .llm import Client, LLMError
from .models import Cluster, Edition, Entry

log = logging.getLogger("digest.synthesize")

QUIET_WEEK = (
    "Nothing this week met the bar. No structural change was reported that "
    "the lens would count, so there is no briefing."
)
MECHANISM_ECHO_THRESHOLD = 88

# The governor runs before the frame is written, so the ceiling it enforces has
# to leave room for an opening and three closing questions.
FRAME_RESERVE_WORDS = 250


ENTRY_SCHEMA = {
    "type": "object",
    "required": ["headline", "body", "hook", "questions"],
    "properties": {
        "headline": {"type": "string"},
        "body": {"type": "string"},
        "hook": {"type": "string"},
        "questions": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
    },
}

FRAME_SCHEMA = {
    "type": "object",
    "required": ["order", "opening", "closing_questions", "theme"],
    "properties": {
        "order": {"type": "array", "items": {"type": "string"}},
        "opening": {"type": "string"},
        "closing_questions": {"type": "array", "items": {"type": "string"}},
        "theme": {"type": ["string", "null"]},
    },
}


NO_PRIOR_COVERAGE = (
    "No prior coverage of this mechanism exists. Do not write a sentence about what "
    "is different from earlier reporting, and do not mention 'previous coverage', "
    "'prior expectations', or anything being 'unlike' an earlier edition — there is "
    "no earlier edition to compare to. A model that invents one anyway is fabricating "
    "the comparison, which the rubric forbids."
)


# Habits a weaker writer has and a strong one does not. Measured on gemma3:27b
# against the same week Gemini wrote: eighty-one country abbreviations, fifty-eight
# filler sentences of the "operates within the broader system of" shape, forty
# hedges. Gemini scored zero on every one of them, so these rules would only be
# telling it not to do things it was not doing — and two of them actively cost it
# something, because it writes three-sentence bodies and reaches for an occasional
# intensifier on purpose. They are handed to a local model and to nothing else.
WRITER_NOTES = """
Habits to avoid, because they show up in writing that is generated rather than
composed:
- What changed, why it matters and the larger system are three things the body must
  cover, not three sentences to write in turn. The rubric's words are the lens for
  choosing what to say, not the vocabulary for saying it.
- Never "operates within", "sits within", "occurs within", "exists within", "the
  broader system", "represents a shift", "this signals" — whatever the subject of the
  sentence, these say nothing. Say what named thing the change acts on instead. Not
  "The expansion occurs within a centrally planned energy system" but "China's grid
  must now absorb a third of its capacity from a source that stops at night."
- No hedging: not "reportedly", "potentially", "appears to", "suggests", "could" or
  "may". If the stories state a thing, state it. If they do not, leave it out.
- No intensifiers: not "massive", "significant", "substantial", "rapid", "sweeping",
  "fundamentally" or "landmark". The number or the fact carries the weight on its own.
- Plain words a listener who has never seen the rubric can follow. "Operating
  parameters", "locus", "node", "calculus", "trajectory" and "dynamics" are analysis
  jargon, not briefing prose.
- The hook takes a cause only when the stories give one. When they state a fact but
  not its cause, the hook is the fact alone: a hook that explains is worth less than
  a hook that is true. It is in the present or past tense and contains no "will",
  "would", "could" or "may".
- A one-line blurb gets a two-sentence body. Length is not a target, and a body that
  says less than the stories do is better than one that says more.
"""


def _writer_notes(cfg: Config) -> str:
    """The weak-model rules, and only when a weak model is writing.

    The prompt file is shared with the hosted writer, which is the production
    path and cannot be regression-tested on demand — its quota is a day long.
    Rules that fix a local model's habits therefore go in behind a slot rather
    than into the prompt every backend sees.
    """
    return WRITER_NOTES if cfg.models.provider_for("synthesize") == "ollama" else ""


def _prior_note(cluster: Cluster, prior_entries: list[dict]) -> str:
    """If we covered this mechanism before, hand the writer what was said then.

    Returns an explicit sentinel rather than an empty string in the negative
    case: a blank {prior_coverage} slot reads, to a weaker model, as silence
    rather than as "nothing to compare to", and a model that has just been told
    to "say what is different" will invent a comparison to fill the gap. gemma3
    did this in 29 of 60 entries on a week with zero prior editions — Gemini
    happened not to, but nothing here relied on it not to.
    """
    if not cluster.shared_mechanism and not any(c.mechanism for c in cluster.items):
        return NO_PRIOR_COVERAGE
    mechanisms = [cluster.shared_mechanism, *(c.mechanism for c in cluster.items)]
    for prior in prior_entries:
        pm = prior.get("mechanism")
        if not pm:
            continue
        if any(
            m and fuzz.token_set_ratio(m.lower(), pm.lower()) >= MECHANISM_ECHO_THRESHOLD
            for m in mechanisms
        ):
            return (
                "This mechanism was covered in an earlier edition. What was said then:\n"
                f"  headline: {prior.get('headline')}\n"
                f"  hook: {prior.get('hook')}\n"
                "Say in one sentence what is different now. Do not repeat that ground."
            )
    return NO_PRIOR_COVERAGE


def _render_cluster(cluster: Cluster) -> str:
    return json.dumps(
        {
            "title": cluster.title,
            "shared_mechanism": cluster.shared_mechanism,
            "stories": [
                {
                    "title": c.item.title,
                    "blurb": c.item.blurb,
                    "source": c.item.source,
                    "region": c.region,
                    "domain": c.domain,
                    "mechanism": c.mechanism,
                }
                for c in cluster.items
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _sources(cluster: Cluster) -> list[dict]:
    out: list[dict] = []
    for c in cluster.items:
        out.append({"source": c.item.source, "url": c.item.url})
        out.extend({"source": c.item.source, "url": u} for u in c.item.also_in)
    seen: set[str] = set()
    return [s for s in out if not (s["url"] in seen or seen.add(s["url"]))]


def write_entry(
    cluster: Cluster, cfg: Config, client: Client, prior_entries: list[dict]
) -> Entry | None:
    prompt = cfg.prompt("synthesize_entry.md").format(
        rubric=cfg.prompt("rubric.md"),
        cluster=_render_cluster(cluster),
        prior_coverage=_prior_note(cluster, prior_entries),
        writer_notes=_writer_notes(cfg),
    )
    try:
        payload = client.complete_json(
            stage="synthesize", prompt=prompt, max_tokens=4000, schema=ENTRY_SCHEMA
        )
    except LLMError as exc:
        log.error("entry failed for cluster %s: %s", cluster.cluster_id, exc)
        return None

    if not isinstance(payload, dict) or not payload.get("headline"):
        log.error("entry for cluster %s came back unusable", cluster.cluster_id)
        return None

    questions = payload.get("questions") or []
    return Entry(
        cluster_id=cluster.cluster_id,
        cluster_title=cluster.title,
        headline=str(payload["headline"]).strip(),
        body=str(payload.get("body", "")).strip(),
        hook=str(payload.get("hook", "")).strip(),
        questions=[str(q).strip() for q in questions if str(q).strip()][:2],
        sources=_sources(cluster),
        fit=cluster.fit,
        region=cluster.region,
        mechanism=cluster.shared_mechanism or cluster.items[0].mechanism,
        item_count=len(cluster.items),
    )


def govern_length(entries: list[Entry], max_words: int, theme_id: str | None) -> list[Entry]:
    """Drop the lowest-fit singletons until the edition fits the ceiling.

    Multi-item entries and the theme entry are kept: they are the ones carrying
    a mechanism several stories share.
    """
    kept = list(entries)
    while sum(e.word_count for e in kept) > max_words:
        droppable = [
            e for e in kept if e.item_count == 1 and e.cluster_id != theme_id
        ]
        if not droppable:
            break
        loser = min(droppable, key=lambda e: (e.fit, -e.word_count))
        kept.remove(loser)
        log.info("length governor dropped %r (fit %d)", loser.headline, loser.fit)
    return kept


def _fallback_frame(entries: list[Entry]) -> tuple[str, list[str], list[Entry]]:
    ordered = sorted(entries, key=lambda e: (-e.fit, e.cluster_id))
    opening = (
        f"This week's briefing runs to {len(ordered)} items. "
        "They are ordered by how much structure each one moves."
    )
    closing = [q for e in ordered for q in e.questions][:3]
    return opening, closing, ordered


def write_frame(
    entries: list[Entry], cfg: Config, client: Client, theme: Cluster | None
) -> tuple[str, list[str], list[Entry], str | None, bool]:
    """Return (opening, closing_questions, ordered_entries, theme_name, degraded)."""
    if not entries:
        return "", [], [], None, False

    rendered = "\n".join(
        json.dumps(
            {
                "cluster_id": e.cluster_id,
                "headline": e.headline,
                "hook": e.hook,
                "region": e.region,
                "fit": e.fit,
            },
            ensure_ascii=False,
        )
        for e in entries
    )
    theme_note = (
        f"The theme-of-the-week cluster is {theme.cluster_id}, titled {theme.title!r}, "
        f"sharing the mechanism {theme.shared_mechanism!r}. Its entry leads."
        if theme
        else "No cluster qualifies as a theme of the week. Set `theme` to null."
    )
    prompt = cfg.prompt("synthesize_frame.md").format(
        rubric=cfg.prompt("rubric.md"), entries=rendered, theme_note=theme_note
    )
    try:
        payload = client.complete_json(
            stage="synthesize", prompt=prompt, max_tokens=4000, schema=FRAME_SCHEMA
        )
    except LLMError as exc:
        log.error("framing failed, falling back to fit order: %s", exc)
        opening, closing, ordered = _fallback_frame(entries)
        return opening, closing, ordered, None, True

    by_id = {e.cluster_id: e for e in entries}
    ordered = [by_id[i] for i in payload.get("order", []) if i in by_id]
    ordered += [e for e in entries if e not in ordered]

    # The prompt says "set theme to null" when no cluster qualifies, and the
    # schema allows a string there too. gemma3 named one anyway, in the same
    # edition whose opening said the entries had nothing in common. Whether a
    # theme exists was decided upstream by theme_candidate; the model only
    # gets to name it.
    theme_name = payload.get("theme") if theme is not None else None
    if isinstance(theme_name, str):
        theme_name = theme_name.strip() or None
    else:
        theme_name = None

    closing = [str(q).strip() for q in payload.get("closing_questions", []) if str(q).strip()]
    opening = str(payload.get("opening", "")).strip()
    if not opening:
        opening, closing_fallback, _ = _fallback_frame(entries)
        closing = closing or closing_fallback
    return opening, closing[:3], ordered, theme_name, False


def synthesize(
    clusters: list[Cluster],
    cfg: Config,
    client: Client,
    week: str,
    prior_entries: list[dict] | None = None,
    degraded: bool = False,
) -> Edition:
    prior_entries = prior_entries or []
    now = datetime.now(timezone.utc)

    if not clusters:
        return Edition(
            week=week, generated_at=now, opening=QUIET_WEEK, entries=[],
            closing_questions=[], quiet=True,
        )

    theme = theme_candidate(clusters)

    entries: list[Entry] = []
    for n, c in enumerate(clusters, 1):
        log.info("writing entry %d/%d: %s", n, len(clusters), c.title)
        entry = write_entry(c, cfg, client, prior_entries)
        if entry is None:
            degraded = True
            continue
        entries.append(entry)

    if not entries:
        return Edition(
            week=week, generated_at=now, opening=QUIET_WEEK, entries=[],
            closing_questions=[], quiet=True, partial=degraded,
        )

    entries = govern_length(
        entries,
        cfg.run.max_words - FRAME_RESERVE_WORDS,
        theme.cluster_id if theme else None,
    )
    opening, closing, ordered, theme_name, frame_degraded = write_frame(
        entries, cfg, client, theme
    )

    return Edition(
        week=week,
        generated_at=now,
        opening=opening,
        entries=ordered,
        closing_questions=closing,
        theme=theme_name,
        partial=degraded or frame_degraded,
    )
