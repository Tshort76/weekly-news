#!/usr/bin/env python
"""Check a digest .txt against the acceptance criteria in the design spec.

    python scripts/check_spoken.py ~/digests/digest-2026-W36.txt

Everything above the line of dashes is read aloud, so it should carry no URLs, no
markdown, no unexpanded acronyms and no forecasting. These are the mechanical
criteria only — whether a hook is a fact rather than a take still needs a human,
and the script says so rather than pretending otherwise.

**Findings are warnings, and the exit code is 0.** This used to exit 1 on any
flag, and the flag was usually one unspelled acronym in an otherwise good
edition. A weekly job wired to treat non-zero as failure would then have called
a perfectly readable briefing broken every week, and a signal that cries wolf
weekly is a signal nobody reads. Nothing this script measures is worth throwing
away an edition over: an acronym is a thing to fix next week, not a reason to
publish nothing.

Exit 1 is available behind `--strict` for anyone who wants a gate. Exit 2 is
kept for the one honest failure — the check could not be run at all, because the
file is missing or holds no spoken part, which means something upstream is
actually broken rather than merely imperfect.
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


def check(path: Path, strict: bool = False) -> int:
    raw = path.read_text(encoding="utf-8")
    spoken = spoken_part(raw)
    if not spoken.strip():
        print(f"{path} has no spoken part — nothing to check.", file=sys.stderr)
        print("That is an upstream problem, not a wording one.", file=sys.stderr)
        return 2
    has_appendix = DIVIDER in raw
    words = len(spoken.split())
    spoken, quoted = split_by_author(spoken)

    # `findings` are things worth a human glance, never a reason to fail.
    findings: list[tuple[str, list[str]]] = []
    noted: list[tuple[str, list[str]]] = []

    urls = [line.strip() for line in spoken.splitlines() if URL.search(line)]
    if urls:
        findings.append(("URLs or bare domains in the spoken part", urls[:5]))

    md = [m.group(0).strip() for m in MARKDOWN.finditer(spoken)]
    if md:
        findings.append(("markdown residue", sorted(set(md))[:5]))

    acronyms = sorted({a for a in ACRONYM.findall(spoken) if a not in ACRONYM_OK})
    if acronyms:
        findings.append(("acronyms that may not be spelled out", acronyms[:8]))

    forecasts = [m.group(0) for m in FORECAST.finditer(spoken)]
    if forecasts:
        findings.append(("forecasting or attributed opinion", sorted(set(forecasts))[:5]))

    parens = [m.group(0)[:50] for m in PARENTHETICAL.finditer(spoken)]
    if parens:
        findings.append(("parentheticals, which do not read aloud", parens[:5]))

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
        findings.append((f"over the {MAX_WORDS}-word ceiling", [f"{words} words"]))

    print(f"file            {path}")
    print(f"spoken words    {words}  (ceiling {MAX_WORDS})")
    print(f"minutes aloud   about {words / 145:.0f} at 145 words a minute")
    print(f"sources appendix{'  present' if has_appendix else '  MISSING'}")
    if quoted:
        print(f"quoted aloud    {len(quoted.split())} words in the reporters' own wording")
    print()

    if not findings:
        print("Clean on every mechanical criterion.")
    else:
        print(f"{len(findings)} thing{'s' if len(findings) > 1 else ''} to look at.")
        for title, examples in findings:
            print(f"  [warn] {title}")
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

    if findings and strict:
        print()
        print("--strict: exiting 1 on the findings above.")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--strict", action="store_true",
        help="exit 1 if anything was flagged (default: warn and exit 0)",
    )
    args = parser.parse_args()
    if not args.path.exists():
        print(f"no such file: {args.path}", file=sys.stderr)
        return 2
    return check(args.path, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
