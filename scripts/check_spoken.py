#!/usr/bin/env python
"""Check a digest .txt against the acceptance criteria in the design spec.

    python scripts/check_spoken.py ~/digests/digest-2026-W36.txt

Everything above the line of dashes is read aloud, so it must carry no URLs, no
markdown, no unexpanded acronyms and no forecasting. These are the mechanical
criteria only — whether a hook is a fact rather than a take still needs a human,
and the script says so rather than pretending otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from digest.emit import DIVIDER, spoken_part  # noqa: E402

MAX_WORDS = 8500

URL = re.compile(r"https?://|www\.|\.com\b|\.org\b")
MARKDOWN = re.compile(r"(^#{1,6}\s)|(\*\*)|(^\s*[-*]\s)|(\[.+?\]\(.+?\))|(`)", re.MULTILINE)
# Two to five capitals, not at the start of a sentence, and not a known word.
ACRONYM = re.compile(r"\b([A-Z]{2,5})\b")
ACRONYM_OK = {"US", "UK", "EU", "UN", "AI", "OK", "IT", "A", "I"}
FORECAST = re.compile(
    r"\b(analysts?\s+(say|expect|predict)|some\s+say|is\s+expected\s+to|will\s+likely"
    r"|is\s+likely\s+to|forecasts?\s+that|observers\s+(say|note)|critics\s+say)\b",
    re.IGNORECASE,
)
PARENTHETICAL = re.compile(r"\([^)]{4,}\)")
# render_txt announces a carried entry, then prints the outlet's paragraph as a
# single line two lines below.
CARRIED_MARKER = re.compile(r"^In .+'s own words\.$")


def split_by_author(spoken: str) -> tuple[str, str]:
    """Separate what the briefing wrote from what it is quoting.

    Both are read aloud, so both matter — but only one of them is ours to fix.
    A semicolon in a reporter's paragraph is their house style, and reporting
    it as a defect in the briefing trains the reader to ignore the whole list.
    """
    lines = spoken.splitlines()
    ours: list[str] = []
    theirs: list[str] = []
    n = 0
    while n < len(lines):
        if CARRIED_MARKER.match(lines[n].strip()):
            # The headline two lines back is the outlet's too, so it moves
            # across with the paragraph rather than being judged as ours.
            while ours and not ours[-1].strip():
                ours.pop()
            if ours:
                theirs.append(ours.pop())
            theirs.extend(lines[n + 1: n + 3])
            n += 3
            continue
        ours.append(lines[n])
        n += 1
    return "\n".join(ours), "\n".join(theirs)


def check(path: Path) -> int:
    raw = path.read_text(encoding="utf-8")
    spoken = spoken_part(raw)
    has_appendix = DIVIDER in raw
    words = len(spoken.split())
    spoken, quoted = split_by_author(spoken)

    problems: list[tuple[str, list[str]]] = []
    noted: list[tuple[str, list[str]]] = []

    urls = [line.strip() for line in spoken.splitlines() if URL.search(line)]
    if urls:
        problems.append(("URLs or bare domains in the spoken part", urls[:5]))

    md = [m.group(0).strip() for m in MARKDOWN.finditer(spoken)]
    if md:
        problems.append(("markdown residue", sorted(set(md))[:5]))

    acronyms = sorted({a for a in ACRONYM.findall(spoken) if a not in ACRONYM_OK})
    if acronyms:
        problems.append(("acronyms that may not be spelled out", acronyms[:8]))

    forecasts = [m.group(0) for m in FORECAST.finditer(spoken)]
    if forecasts:
        problems.append(("forecasting or attributed opinion", sorted(set(forecasts))[:5]))

    parens = [m.group(0)[:50] for m in PARENTHETICAL.finditer(spoken)]
    if parens:
        problems.append(("parentheticals, which do not read aloud", parens[:5]))

    # The same rules over the quoted paragraphs, reported rather than failed.
    # Verbatim is the point: these are somebody else's sentences and the choice
    # to read them unaltered was deliberate.
    if quoted:
        quoted_acronyms = sorted({a for a in ACRONYM.findall(quoted) if a not in ACRONYM_OK})
        if quoted_acronyms:
            noted.append(("acronyms", quoted_acronyms[:8]))
        quoted_parens = [m.group(0)[:50] for m in PARENTHETICAL.finditer(quoted)]
        if quoted_parens:
            noted.append(("parentheticals", quoted_parens[:4]))
        quoted_digits = sorted(set(re.findall(r"\b\d[\d,.]*\b", quoted)))
        if quoted_digits:
            noted.append(("figures written as digits", quoted_digits[:8]))

    if words > MAX_WORDS:
        problems.append((f"over the {MAX_WORDS}-word ceiling", [f"{words} words"]))

    print(f"file            {path}")
    print(f"spoken words    {words}  (ceiling {MAX_WORDS})")
    print(f"minutes aloud   about {words / 145:.0f} at 145 words a minute")
    print(f"sources appendix{'  present' if has_appendix else '  MISSING'}")
    if quoted:
        print(f"quoted aloud    {len(quoted.split())} words in the reporters' own wording")
    print()

    if not problems:
        print("PASS on every mechanical criterion.")
    else:
        for title, examples in problems:
            print(f"  [!] {title}")
            for example in examples:
                print(f"        {example}")
    if noted:
        print()
        print("  In the quoted paragraphs, which are read verbatim by choice.")
        print("  Not defects in the briefing — the outlet wrote them this way.")
        for title, examples in noted:
            print(f"  [-] {title}")
            for example in examples:
                print(f"        {example}")
    print()
    print("Still needs your eyes: whether each hook is a fact plus a mechanism")
    print("rather than a take, and whether anything was invented that the")
    print("headlines did not support.")
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if not args.path.exists():
        print(f"no such file: {args.path}", file=sys.stderr)
        return 2
    return check(args.path)


if __name__ == "__main__":
    raise SystemExit(main())
