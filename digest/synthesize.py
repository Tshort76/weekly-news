"""Two passes: an Entry per cluster, then the frame around them.

Every failure here degrades rather than aborts — a missing entry is skipped, a
missing frame falls back to fit order and stock wording, and either marks the
edition [PARTIAL].
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from rapidfuzz import fuzz

from .cluster import SAME_EVENT_THRESHOLD, theme_candidate
from .config import Config
from .llm import Client, LLMError
from .models import Classified, Cluster, Edition, Entry
from .normalize import strip_furniture

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


# Words that start a sentence or join a name without being part of one. A span
# is only evidence of invention when the capitalised words in it are.
STOP_CAPS = frozenset(
    """A An The This That These Those It Its He She They Their There Here When While
    Where What Which Who Whose Why How If Because Since After Before During Under
    Over Both Each Every Some Most Many Few More Less Other Another Such No Not Now
    Then Than But And Or So Yet For Nor As At By In On To Up Of Off Out From With
    Without Within Into Onto About Against Between Among Across Through Monday
    Tuesday Wednesday Thursday Friday Saturday Sunday January February March April
    May June July August September October November December""".split()
)

# Lowercase words that sit inside a proper name without breaking it, so that
# "Bureau of Industry and Security" is read as one span rather than three
# unremarkable single words.
NAME_JOINERS = frozenset("of and the for in on de du van der la le el bin al".split())

# Expansions the prompt itself demands. A source that says "US" gets "United
# States" back by instruction, and the guard must not read its own rule as a
# fabrication.
MANDATED_EXPANSIONS = frozenset(
    ["united states", "european union", "united kingdom", "united nations"]
)

_WORD = re.compile(r"[A-Za-z][A-Za-z.'’-]*")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_POSSESSIVE = re.compile(r"[’']s$")


def _capitalised_spans(text: str) -> list[str]:
    """Runs of capitalised words, joined across "of"/"and" and the like.

    A single capitalised word is not returned: "Parliament" on its own is how
    anyone writes about a parliament, while "Committee on Foreign Investment"
    is a claim about a specific body that either was in the stories or was not.
    """
    spans: list[str] = []
    for sentence in _SENTENCE.split(text):
        # A possessive is the same name wearing an apostrophe: "Bank of Japan's"
        # must match a source that says "Bank of Japan".
        tokens = [_POSSESSIVE.sub("", t).rstrip(".'’-") for t in _WORD.findall(sentence)]
        tokens = [t for t in tokens if t]
        run: list[str] = []
        pending: list[str] = []
        for n, tok in enumerate(tokens):
            capitalised = tok[0].isupper() and tok not in STOP_CAPS
            # A sentence's first word is capitalised by grammar, not by name.
            if n == 0 and capitalised and not run:
                nxt = tokens[1] if len(tokens) > 1 else ""
                if not (nxt and nxt[0].isupper() and nxt not in STOP_CAPS):
                    continue
            if capitalised:
                run.extend(pending)
                pending = []
                run.append(tok)
            elif run and tok.lower() in NAME_JOINERS:
                pending.append(tok)
            else:
                if len(run) > 1:
                    spans.append(" ".join(run))
                run, pending = [], []
        if len(run) > 1:
            spans.append(" ".join(run))
    return spans


def _initials(span: str) -> str:
    return "".join(w[0] for w in span.split() if w[0].isupper())


# What a fabricated detail is made of. Geography is not on this list on purpose:
# a writer that says Egypt is in North Africa is reasoning, not inventing, and a
# guard that has to know every sea and region is a gazetteer waiting to rot. A
# named body, law or treaty is different — either the stories named it or the
# model reached outside them for it.
INSTITUTION_WORDS = frozenset(
    """Act Administration Agency Agreement Alliance Assembly Association Authority
    Bank Board Bureau Cabinet Coalition Commission Committee Community Congress
    Convention Corporation Council Court Department Directorate Federation Fund
    Institute Ministry Office Organisation Organization Pact Parliament Partnership
    Programme Protocol Secretariat Service Society Treaty Tribunal Union""".split()
)

# Abbreviations whose expansion the prompt actively demands, where the initials
# of the expansion do not spell the abbreviation back.
ACRONYM_EXPANSIONS = {
    "G20": "group of twenty",
    "G7": "group of seven",
    "G8": "group of eight",
}


def novel_names(text: str, source: str) -> list[str]:
    """Institutions, laws and treaties in `text` that `source` never mentions.

    The writer is told that detail it knows from elsewhere counts as invented.
    Telling it so is not enough — gemma3 answered a one-line blurb about chip
    export controls with two agencies by name, neither of them anywhere in the
    stories, and then said what those agencies would now face. This is the same
    judgement made where the model cannot talk its way past it.

    Deliberately narrow. It asks only whether a named body appeared in the
    stories, which is a question with an answer, and leaves every other kind of
    invention to the prompt. A guard that drops good entries is worse than one
    that misses bad ones, because a dropped entry is news the briefing loses.
    """
    src = source.lower().replace("_", " ")
    src_acronyms = set(re.findall(r"\b[A-Z][A-Z0-9]{1,4}\b", source))
    allowed = {ACRONYM_EXPANSIONS[a] for a in src_acronyms if a in ACRONYM_EXPANSIONS}
    found: dict[str, None] = {}

    for span in _capitalised_spans(text):
        low = span.lower()
        if low in src or low in MANDATED_EXPANSIONS or low in allowed:
            continue
        if not any(w in INSTITUTION_WORDS for w in span.split()):
            continue
        # A source naming the acronym has named the thing: "IMF" in the stories
        # licenses "International Monetary Fund" in the entry.
        if _initials(span) in src_acronyms:
            continue
        missing = [w for w in span.split() if w[0].isupper() and w.lower() not in src]
        # One unfamiliar word beside familiar ones is a rephrasing. Two or more
        # is a name the stories do not contain.
        if len(missing) >= 2:
            found[span] = None

    # An acronym on its own is the compact form of the same offence. The ones the
    # prompt already governs are a style fault, caught elsewhere, not invention.
    for acronym in re.findall(r"\b[A-Z]{3,5}\b", text):
        if acronym not in src_acronyms and acronym.lower() not in src:
            found[acronym] = None

    return list(found)


def _render_cluster(cluster: Cluster) -> str:
    """The stories, and separately whatever else was found out about them.

    Evidence is kept in its own key rather than folded into the blurbs. The
    writer has to be able to tell what the story itself said from what some
    other outlet said about the same event, because only one of those is the
    source it is writing up.
    """
    payload: dict = {
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
    }
    own = [e for c in cluster.items for e in c.evidence if e.kind == "article"]
    others = [e for c in cluster.items for e in c.evidence if e.kind == "search"]
    if own:
        payload["full_text_of_the_story_itself"] = [e.text for e in own]
    if others:
        payload["what_other_outlets_reported"] = [e.text for e in others]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _sources(cluster: Cluster) -> list[dict]:
    out: list[dict] = []
    for c in cluster.items:
        out.append({"source": c.item.source, "url": c.item.url})
        out.extend({"source": c.item.source, "url": u} for u in c.item.also_in)
    seen: set[str] = set()
    return [s for s in out if not (s["url"] in seen or seen.add(s["url"]))]


INVENTION_NOTE = (
    "Your draft named these, and the stories above name none of them: {names}. "
    "Every one of them is something you know from elsewhere rather than something "
    "you were given, which is exactly what this brief forbids. Write the entry "
    "again without them. Do not substitute a different name, and where a sentence "
    "has nothing left once the name is gone, drop the sentence — a shorter entry "
    "that stays inside the stories is the point, not a cost."
)


def _spoken_text(payload: dict) -> str:
    return " ".join(
        str(x) for x in [
            payload.get("headline", ""), payload.get("body", ""),
            payload.get("hook", ""), *(payload.get("questions") or []),
        ]
    )


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _written_up(row: Classified) -> tuple[str, str] | None:
    """The article a person already wrote about this story, if we have one.

    Search snippets never count. They are other outlets glossing the event, not
    the story itself, and presenting them as the source would be a lie about
    where the words came from.
    """
    article = max(
        (e.text for e in row.evidence if e.kind == "article"), key=len, default=""
    )
    text = max([row.item.blurb, article], key=len)
    return (text, row.item.source) if text else None


def _long_enough(row: Classified, cfg: Config) -> bool:
    found = _written_up(row)
    return bool(found and len(found[0]) >= cfg.run.source_min_chars)


def partition_carried(
    selected: list[Classified], cfg: Config
) -> tuple[list[Classified], list[Classified]]:
    """Split the week into what we publish as written and what we hand the model.

    The order of these two steps decides how often a reporter's own words reach
    the page. Deciding after clustering meant a story a person had written in
    full got rewritten whenever the grouping happened to absorb it — nine of
    twelve one week, and which nine depended on how well a model grouped.

    So the carry decision comes first, with one exception. When two outlets
    covered the same event, combining them is the one thing the model does that
    no single article can, and publishing both verbatim would print the same
    news twice. Those go to clustering; everything else a person wrote goes
    straight to the page.
    """
    carried: list[Classified] = []
    for row in selected:
        if not _long_enough(row, cfg):
            continue
        covered_elsewhere = any(
            other.id != row.id
            and fuzz.token_set_ratio(row.item.title.lower(), other.item.title.lower())
            >= SAME_EVENT_THRESHOLD
            for other in selected
        )
        if not covered_elsewhere:
            carried.append(row)
    ids = {c.id for c in carried}
    return carried, [c for c in selected if c.id not in ids]


def carried_clusters(rows: list[Classified]) -> list[Cluster]:
    """One cluster per carried story, with ids that cannot collide with the
    model's own."""
    return [
        Cluster(cluster_id=f"s{n}", title=r.item.title, items=[r], shared_mechanism=r.mechanism)
        for n, r in enumerate(rows, 1)
    ]


def _human_text(cluster: Cluster) -> tuple[str, str] | None:
    """A cluster of one story that a person already wrote up.

    Several stories sharing a mechanism never qualify: the point of that entry
    is the synthesis across them, which no one outlet wrote, and splicing two
    articles together would be neither their words nor an honest summary.
    """
    if len(cluster.items) != 1:
        return None
    return _written_up(cluster.items[0])


def _excerpt(text: str, max_words: int) -> str:
    """Cut on a sentence boundary, never mid-thought."""
    kept: list[str] = []
    used = 0
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        words = len(sentence.split())
        if kept and used + words > max_words:
            break
        kept.append(sentence)
        used += words
    return " ".join(kept).strip()


def carry_source(cluster: Cluster, cfg: Config) -> Entry | None:
    """Publish the outlet's own words rather than a rewrite of them.

    The model is only worth running where nobody has already done the work. If
    a person wrote the story up, their account is better than a paraphrase of
    it — and it costs no quota and cannot invent anything.
    """
    found = _human_text(cluster)
    if not found:
        return None
    text, outlet = found
    if len(text) < cfg.run.source_min_chars:
        return None
    body = _excerpt(strip_furniture(text), cfg.run.source_max_words)
    if not body:
        return None
    item = cluster.items[0]
    sentences = _SENTENCE_SPLIT.split(body)
    return Entry(
        cluster_id=cluster.cluster_id,
        cluster_title=cluster.title,
        headline=item.item.title.rstrip("."),
        body=body,
        hook=sentences[0] if sentences else "",
        questions=[],
        sources=_sources(cluster),
        fit=cluster.fit,
        region=cluster.region,
        mechanism=cluster.shared_mechanism or item.mechanism,
        item_count=1,
        provenance="source",
        attribution=outlet,
    )


def write_entry(
    cluster: Cluster, cfg: Config, client: Client, prior_entries: list[dict]
) -> Entry | None:
    carried = carry_source(cluster, cfg)
    if carried is not None:
        log.info(
            "carrying %s's own words for %s — no model call",
            carried.attribution, cluster.cluster_id,
        )
        return carried

    rendered = _render_cluster(cluster)
    prior = _prior_note(cluster, prior_entries)
    prompt = cfg.prompt("synthesize_entry.md").format(
        rubric=cfg.prompt("rubric.md"),
        cluster=rendered,
        prior_coverage=prior,
        writer_notes=_writer_notes(cfg),
    )
    evidence = " ".join(e.text for c in cluster.items for e in c.evidence)
    source_text = f"{rendered}\n{prior}\n{evidence}"

    payload = None
    for attempt in range(2):
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

        invented = novel_names(_spoken_text(payload), source_text)
        if not invented:
            break
        if attempt == 0:
            log.warning(
                "cluster %s named %s, which the stories do not — asking again",
                cluster.cluster_id, ", ".join(invented),
            )
            prompt = f"{prompt}\n\n{INVENTION_NOTE.format(names=', '.join(invented))}"
        else:
            # Better a shorter briefing than a fluent false one. The edition is
            # marked [PARTIAL] by the caller, so the loss is visible.
            log.error(
                "dropping cluster %s: still names %s after being told not to",
                cluster.cluster_id, ", ".join(invented),
            )
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
        log.info("entry %d/%d: %s", n, len(clusters), c.title)
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
