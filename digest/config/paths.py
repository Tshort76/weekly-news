"""Where an installed app keeps its config and its data.

A checkout keeps both beside the code. An installed tool cannot: the package
directory is read-only, gets replaced on upgrade, and on Windows is not
somewhere a person can find. So config and data move to the conventional
per-platform directories.

`DIGEST_HOME` overrides both and puts them side by side. Every test uses it, and
so does anyone running two lenses out of one install.
"""

from __future__ import annotations

import os
from pathlib import Path

APP = "Digest"

# The two names the legacy layout used, kept so the importer can find an
# existing install. On Linux these are also the new paths, so a Linux user
# upgrading moves nothing.
LEGACY_CONFIG = Path.home() / ".config" / "digest"
LEGACY_DATA = Path.home() / ".local" / "share" / "digest"


def _platformdirs():
    try:
        import platformdirs  # noqa: PLC0415
    except ModuleNotFoundError:
        return None
    return platformdirs


def config_dir() -> Path:
    """Where config.toml, feeds.toml, lens.md and lens.toml live."""
    override = os.environ.get("DIGEST_HOME")
    if override:
        return Path(override).expanduser()
    dirs = _platformdirs()
    if dirs is None:
        return LEGACY_CONFIG
    return Path(dirs.user_config_dir(APP, appauthor=False))


def data_dir() -> Path:
    """Where state.db, logs and anything else that grows live."""
    override = os.environ.get("DIGEST_HOME")
    if override:
        return Path(override).expanduser()
    # Honoured before platformdirs: the scheduled job on the owner's machine
    # sets it, and a run that quietly used a different database would show
    # every headline a second time.
    legacy = os.environ.get("DIGEST_STATE_DIR")
    if legacy:
        return Path(legacy).expanduser()
    dirs = _platformdirs()
    if dirs is None:
        return LEGACY_DATA
    return Path(dirs.user_data_dir(APP, appauthor=False))


def config_file() -> Path:
    return config_dir() / "config.toml"


def feeds_file() -> Path:
    return config_dir() / "feeds.toml"


def lens_file() -> Path:
    return config_dir() / "lens.md"


def lens_spec_file() -> Path:
    return config_dir() / "lens.toml"


def is_installed() -> bool:
    """True once setup has written a config. The importer keys off this."""
    return config_file().exists()
