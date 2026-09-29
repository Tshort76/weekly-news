"""Where the config and the data live.

Config is versioned in the checkout's `config/` directory, so a worktree runs
with its own branch's lens and settings. Data — state.db and the logs — is kept
at one fixed place outside every checkout, so a run from any worktree sees the
same record of what has already been published.

`DIGEST_HOME` overrides both and puts them side by side. Every test uses it.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = Path.home() / "Library" / "Application Support" / "Digest"


def _home() -> Path | None:
    override = os.environ.get("DIGEST_HOME")
    return Path(override).expanduser() if override else None


def config_dir() -> Path:
    """Where config.toml, feeds.toml, lens.md and lens.toml live."""
    return _home() or REPO / "config"


def data_dir() -> Path:
    """Where state.db, logs and anything else that grows live."""
    return _home() or DATA


def config_file() -> Path:
    return config_dir() / "config.toml"


def feeds_file() -> Path:
    return config_dir() / "feeds.toml"


def lens_file() -> Path:
    return config_dir() / "lens.md"


def lens_spec_file() -> Path:
    return config_dir() / "lens.toml"
