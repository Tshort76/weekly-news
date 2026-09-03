#!/usr/bin/env python
"""Count the mechanical faults in a finished edition, entry by entry.

    python scripts/score_prose.py ~/digests/compare/digest-2026-W36-gemma3-v2.md
    python scripts/score_prose.py A.md --only B.md      # score only the entries B also has
    python scripts/score_prose.py A.md --week 2026-W36  # also flag detail the blurbs lack

`check_spoken.py` is the acceptance gate for a whole edition. This is the finer
instrument for comparing two runs of the same week: it splits the edition into
entries and counts, per entry, the habits that separate a strong writer from a
weak one on this prompt — digits where words were asked for, rubric vocabulary
leaking into the prose, a hook that forecasts or merely restates the headline,
filler questions, hedges, and (given the week's stored classifications) years
and named things in the body that appear nowhere in the source titles or blurbs,
which is the only mechanical proxy for invented background this has.

Entries are matched between editions by source URL, so `--only` compares like
with like when one run is partial.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ENTRY_HEAD = re.compile(r"^## (?!Three questions)(.+)$", re.MULTILINE)
LINK = re.compile(r"\[(.+?)\]\((.+?)\)")

DIGITS = re.compile(r"(?<![\w-])(?!(?:19|20)\d\d\b)\d[\d,.]*")
YEAR = re.compile(r"\b(?:19|20)\d\d\b")
SYMBOLS = re.compile(r"[$£€¥%]|\b\d+bn\b")
TEMPLATE = re.compile(
    r"\b(operates? within|sits? within|exists? within|occurs? within|"
    r"within (the |a )?(broader|larger|wider|existing|global|international) "
    r"(system|context|framework|architecture|structure)|the broader system|"
    r"this (represents|signals|reflects|highlights|demonstrates|indicates) a)\b",
    re.IGNORECASE,
)
RUBRIC_WORDS = re.compile(
    r"\b(structural(ly)?|structure|architecture|capacit(y|ies)|mechanism|"
    r"cost of coordination|dynamics?|landscape|framework|ecosystem|paradigm|"
    r"incentive structure|balance of power|node)\b",
    re.IGNORECASE,
)
HEDGE = re.compile(
    r"\b(reportedly|potentially|potential|suggests?|appears?( to)?|seems?( to)?|"
    r"analysis suggests|reports suggest|may|might|could)\b",
    re.IGNORECASE,
)
FORECAST = re.compile(
    r"\b(will|would|could|may|might|poised|set to|is expected|are expected|likely)\b",
    re.IGNORECASE,
)
MECHANISM_LINK = re.compile(r"\b(because|as|after|since|by|so that|which)\b", re.IGNORECASE)
INTENSIFIER = re.compile(
    r"\b(massive|significant(ly)?|fundamental(ly)?|rapid(ly)?|existential|sweeping|"
    r"dramatic(ally)?|unprecedented|major|substantial(ly)?|novel|uniquely|crucial|"
    r"landmark|blockbuster|huge|remarkable|extraordinary)\b",
    re.IGNORECASE,
)
ACRONYM = re.compile(r"\b([A-Z]{2,5})\b")
ACRONYM_OK = {"US", "UK", "EU", "UN", "AI", "OK", "IT", "A", "I"}
SPOKEN_ABBREV = re.compile(r"\b(US|U\.S\.|AI|EU|UK|UN)\b")
PARENTHETICAL = re.compile(r"\([^)]{2,}\)")
DASH = re.compile(r"\s[—–-]{1,2}\s|—")
PERSON = re.compile(r"\b(we|our|us|you|your|I)\b")
SOURCE_NAME = re.compile(
    r"\b(The Economist|Economist|Financial Times|Semafor|Ars Technica|IEEE Spectrum|"
    r"South China Morning Post)\b"
)
GENERIC_Q = re.compile(
    r"^(how (will|might|does|do|could) this|could this (model|approach|trend)|"
    r"what (are|is) the (implications|impact|long-term)|will (this|other)|"
    r"how (will|might) other)",
    re.IGNORECASE,
)
SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
STOP_CAPS = {
    "The", "This", "These", "That", "Those", "It", "Its", "A", "An", "In", "By", "As",
    "For", "With", "Under", "After", "Should", "If", "When", "While", "Recent", "Rising",
    "Several", "More", "Fewer", "Space", "Officials", "Governments", "Disruption",
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
    # Geography and its derivatives are not invented detail; a writer may place a
    # story on the map. Institutions, laws, people and events are what we want.
    "United", "States", "European", "Union", "Kingdom", "Nations", "Nation", "North",
    "South", "East", "West", "Eastern", "Western", "Central", "Northern", "Southern",
    "Asia", "Europe", "Africa", "America", "Americas", "Gulf", "Middle", "Persian",
    "Atlantic", "Pacific", "Mediterranean", "Organization", "Treaty", "Group", "Twenty",
    "Beijing", "Washington", "Moscow", "Canberra", "Brussels", "Tokyo", "Kyiv", "Tehran",
    "Silicon", "Valley", "Wall", "Street", "Congress", "Parliament", "Nordic",
}
DEMONYM = re.compile(r"^[A-Z][a-z]+(an|ese|ish|ian|ic|i)s?$")


@dataclass
class Entry:
    headline: str
    body: str
    hook: str
    questions: list[str]
    urls: list[str] = field(default_factory=list)


def parse(path: Path) -> list[Entry]:
    text = path.read_text(encoding="utf-8")
    heads = list(ENTRY_HEAD.finditer(text))
    entries = []
    for n, m in enumerate(heads):
        end = heads[n + 1].start() if n + 1 < len(heads) else len(text)
        block = text[m.end():end]
        body, hook, questions, urls = "", "", [], []
        for para in [p.strip() for p in block.split("\n\n") if p.strip()]:
            if para.startswith("## Three questions"):
                break
            if para.startswith("<small>"):
                urls += [u for _, u in LINK.findall(para)]
            elif para.startswith("> "):
                questions.append(para[2:].strip())
            elif para.startswith("*") and para.endswith("*"):
                hook = para.strip("*").strip()
            elif not body:
                body = para
        entries.append(Entry(m.group(1).strip(), body, hook, questions, urls))
    return entries


def words(s: str) -> set[str]:
    return {w.lower().strip(".,'’\"") for w in s.split() if len(w) > 4}


def overlap(a: str, b: str) -> float:
    wa, wb = words(a), words(b)
    return len(wa & wb) / max(len(wb), 1)


def sentences(s: str) -> int:
    return len([x for x in SENTENCE_END.split(s) if x.strip()])


def title_case(h: str) -> bool:
    ws = [w for w in h.split() if len(w) > 3 and w.isalpha()]
    return len(ws) >= 3 and sum(w[0].isupper() for w in ws) / len(ws) >= 0.7


def novel_terms(entry: Entry, source: str) -> tuple[list[str], list[str]]:
    """Years and capitalised names in the body that the source text never mentions."""
    src = source.lower()
    years = sorted({y for y in YEAR.findall(entry.body) if y not in src})
    names: set[str] = set()
    for sent in re.split(r"(?<=[.!?])\s+", entry.body):
        toks = sent.split()
        for tok in toks[1:]:  # skip sentence-initial capitals
            t = tok.strip(".,;:'’\"()")
            if (t and t[0].isupper() and t.isalpha() and t not in STOP_CAPS and len(t) > 2
                    and not DEMONYM.match(t)):
                if t.lower() not in src and t.lower() not in entry.headline.lower():
                    names.add(t)
    return years, sorted(names)


def score(entries: list[Entry], sources: dict[str, str] | None) -> Counter:
    c: Counter = Counter()
    c["entries"] = len(entries)
    for e in entries:
        spoken = " ".join([e.headline, e.body, e.hook, *e.questions])
        c["words"] += len(e.body.split())
        c["digits (not years)"] += len(DIGITS.findall(spoken))
        c["currency/percent symbols"] += len(SYMBOLS.findall(spoken))
        c["template phrases"] += len(TEMPLATE.findall(e.body + " " + e.hook))
        c["rubric vocabulary"] += len(RUBRIC_WORDS.findall(e.body + " " + e.hook))
        c["hedges"] += len(HEDGE.findall(e.body + " " + e.hook))
        c["intensifiers"] += len(INTENSIFIER.findall(spoken))
        c["unexpanded acronyms"] += len({a for a in ACRONYM.findall(spoken) if a not in ACRONYM_OK})
        c["US/AI/EU-style abbreviations"] += len(SPOKEN_ABBREV.findall(spoken))
        c["parentheticals"] += len(PARENTHETICAL.findall(spoken))
        c["semicolons"] += spoken.count(";")
        c["dashes"] += len(DASH.findall(spoken))
        c["first/second person"] += len(PERSON.findall(e.body + " " + e.hook))
        c["source names in body"] += len(SOURCE_NAME.findall(e.body))
        c["hooks with forecast verbs"] += bool(FORECAST.search(e.hook))
        c["hooks with no causal link"] += not MECHANISM_LINK.search(e.hook)
        c["hooks restating the headline"] += overlap(e.hook, e.headline) >= 0.5
        c["hook/body overlap %"] += round(100 * overlap(e.hook, e.body))
        n = sentences(e.body)
        c["bodies outside 2-5 sentences"] += not (2 <= n <= 5)
        c["questions"] += len(e.questions)
        c["entries with two questions"] += len(e.questions) == 2
        c["entries with no question"] += len(e.questions) == 0
        c["generic questions"] += sum(bool(GENERIC_Q.match(q)) for q in e.questions)
        c["title-case headlines"] += title_case(e.headline)
        c["headlines ending in a period"] += e.headline.endswith(".")
        if sources is not None:
            src = " ".join(sources.get(u, "") for u in e.urls)
            if src:
                years, names = novel_terms(e, src)
                c["novel years (not in source)"] += len(years)
                c["novel names (not in source)"] += len(names)
                c["entries with novel names"] += bool(names)
    return c


def load_sources(week: str) -> dict[str, str]:
    from digest import config  # noqa: PLC0415
    from digest.state import State  # noqa: PLC0415

    cfg = config.load()
    with State(cfg.db_path) as state:
        rows = state.load_classified(week)
    out = {}
    for r in rows:
        text = " ".join([r.item.title, r.item.blurb or "", r.mechanism or ""])
        out[r.item.url] = text
        for u in r.item.also_in:
            out[u] = text
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path)
    parser.add_argument("--only", type=Path, help="score only entries whose sources this edition also has")
    parser.add_argument("--week", help="load that week's classifications to flag detail the sources lack")
    parser.add_argument("--show", action="store_true", help="print the offending text per entry")
    args = parser.parse_args()

    entries = parse(args.path)
    if args.only:
        keep = {u for e in parse(args.only) for u in e.urls}
        entries = [e for e in entries if set(e.urls) & keep]
    sources = load_sources(args.week) if args.week else None

    if args.show:
        for e in entries:
            flags = []
            if DIGITS.findall(" ".join([e.headline, e.body, e.hook])):
                flags.append("digits=" + ",".join(DIGITS.findall(" ".join([e.headline, e.body, e.hook]))))
            t = TEMPLATE.findall(e.body + " " + e.hook)
            if t:
                flags.append("template=" + ",".join(m[0] for m in t))
            if FORECAST.search(e.hook):
                flags.append("hook-forecast=" + FORECAST.search(e.hook).group(0))
            if overlap(e.hook, e.headline) >= 0.5:
                flags.append("hook-restates-headline")
            if sources is not None:
                src = " ".join(sources.get(u, "") for u in e.urls)
                years, names = novel_terms(e, src)
                if years or names:
                    flags.append(f"novel={years + names}")
            print(f"- {e.headline}\n    {' | '.join(flags) or 'clean'}")
        print()

    c = score(entries, sources)
    n = max(c["entries"], 1)
    print(f"file     {args.path}")
    print(f"entries  {c['entries']}   avg body words {c['words'] / n:.0f}   "
          f"avg hook/body overlap {c['hook/body overlap %'] / n:.0f}%")
    for key in [
        "digits (not years)", "currency/percent symbols", "unexpanded acronyms",
        "US/AI/EU-style abbreviations", "template phrases", "rubric vocabulary", "hedges",
        "intensifiers", "hooks with forecast verbs", "hooks with no causal link",
        "hooks restating the headline", "bodies outside 2-5 sentences", "questions",
        "entries with two questions", "entries with no question", "generic questions",
        "parentheticals", "semicolons", "dashes", "first/second person", "source names in body",
        "title-case headlines", "headlines ending in a period",
        "novel years (not in source)", "novel names (not in source)", "entries with novel names",
    ]:
        if key.startswith("novel") and sources is None:
            continue
        print(f"  {key:<34} {c[key]:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
