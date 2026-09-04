"""The lenses that ship with the package.

A preset is a lens somebody wrote and checked against real headlines, not a
template with the topic swapped in. Each is a `.toml` spec plus, where one
exists, the exact `.md` that was measured — phase 0 found that recompiling the
same words with different line breaks moves the classifier by about ten points,
so a preset hands over the file that was scored rather than rebuilding it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .compile import compile_lens
from .schema import LensSpec

DIRECTORY = Path(__file__).resolve().parent.parent / "lenses"

# The lens this project was built around. Its regions, domains and kind words
# are the ones that were hardcoded in classify.md before lenses existed, so it
# is also the fallback whenever no lens has been chosen.
DEFAULT = "architecture-of-rule"


def available() -> list[str]:
    return sorted(p.stem for p in DIRECTORY.glob("*.toml"))


def spec_path(name: str) -> Path:
    path = DIRECTORY / f"{name}.toml"
    if not path.exists():
        raise LookupError(f"no preset named {name!r} — have {', '.join(available())}")
    return path


def load(name: str) -> LensSpec:
    return LensSpec.from_toml(spec_path(name))


def markdown(name: str) -> str:
    """The rubric text for a preset: the measured file if there is one."""
    measured = DIRECTORY / f"{name}.md"
    if measured.exists():
        return measured.read_text(encoding="utf-8")
    return compile_lens(load(name))


@lru_cache(maxsize=None)
def default_lens() -> LensSpec:
    return load(DEFAULT)
