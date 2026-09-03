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
