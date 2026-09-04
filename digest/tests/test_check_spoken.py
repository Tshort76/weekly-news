"""The acceptance checker has to actually catch what it claims to catch."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "check_spoken.py"

CLEAN = """The weekly digest, week 2026-W36.

This week the shape is monetary plumbing.

First, East Asia.

Japan targets reserve quantity, not price

The central bank changed what it steers, and the change looks permanent.

The interest rate is now an outcome of operations rather than the thing set.

End of the digest.

------------------------------------------------------------
Sources

1. Japan targets reserve quantity — Economist — https://e.com/1
"""


def run(tmp_path: Path, text: str) -> subprocess.CompletedProcess:
    path = tmp_path / "digest.txt"
    path.write_text(text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True
    )


def test_a_clean_digest_passes(tmp_path):
    result = run(tmp_path, CLEAN)
    assert result.returncode == 0, result.stdout
    assert "PASS on every mechanical criterion" in result.stdout


def test_the_sources_appendix_is_not_treated_as_spoken(tmp_path):
    """The appendix is full of URLs and must not fail the check."""
    result = run(tmp_path, CLEAN)
    assert "URLs" not in result.stdout


def test_a_url_above_the_divider_fails(tmp_path):
    result = run(tmp_path, CLEAN.replace("permanent.", "permanent, see https://e.com/x."))
    assert result.returncode == 1
    assert "URLs or bare domains" in result.stdout


def test_markdown_residue_fails(tmp_path):
    result = run(tmp_path, CLEAN.replace("Japan targets", "## Japan **targets**"))
    assert result.returncode == 1
    assert "markdown residue" in result.stdout


def test_an_unexpanded_acronym_is_flagged(tmp_path):
    result = run(tmp_path, CLEAN.replace("The central bank", "The BOJ and the ECB"))
    assert result.returncode == 1
    assert "acronyms" in result.stdout
    assert "BOJ" in result.stdout


def test_common_words_are_not_flagged_as_acronyms(tmp_path):
    result = run(tmp_path, CLEAN.replace("East Asia", "the US and the EU"))
    assert result.returncode == 0, result.stdout


def test_forecasting_language_fails(tmp_path):
    result = run(tmp_path, CLEAN.replace("looks permanent", "is expected to continue"))
    assert result.returncode == 1
    assert "forecasting" in result.stdout


def test_a_parenthetical_fails(tmp_path):
    result = run(tmp_path, CLEAN.replace("what it steers", "what it steers (the reserves)"))
    assert result.returncode == 1
    assert "parentheticals" in result.stdout


def test_a_reporters_wording_is_reported_but_does_not_fail_the_check(tmp_path):
    """Verbatim is the choice, so a semicolon in someone else's paragraph is
    their house style, not a defect in the briefing. Reporting it as one trains
    the reader to skip the whole list."""
    import subprocess
    import sys

    from digest.emit import DIVIDER

    path = tmp_path / "d.txt"
    path.write_text(
        "The Weekly Digest.\n\n"
        "CDC still counts two measles deaths\n\n"
        "In Ars Technica's own words.\n\n"
        "The CDC counted 2 deaths (a newborn and a child) after the NSA review.\n\n"
        "End of the digest.\n\n" + DIVIDER + "\nSources\n\n1. x\n"
    )
    out = subprocess.run(
        [sys.executable, "scripts/check_spoken.py", str(path)],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert out.returncode == 0, out.stdout
    assert "PASS on every mechanical criterion" in out.stdout
    assert "read verbatim by choice" in out.stdout
    assert "CDC" in out.stdout


def test_the_briefings_own_prose_is_still_held_to_the_rules(tmp_path):
    import subprocess
    import sys

    from digest.emit import DIVIDER

    path = tmp_path / "d.txt"
    path.write_text(
        "The Weekly Digest.\n\n"
        "A headline\n\n"
        "The CDC reviewed it (twice) this week.\n\n"
        "End of the digest.\n\n" + DIVIDER + "\nSources\n\n1. x\n"
    )
    out = subprocess.run(
        [sys.executable, "scripts/check_spoken.py", str(path)],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert out.returncode == 1
    assert "acronyms that may not be spelled out" in out.stdout
