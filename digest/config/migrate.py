"""Move a config file forward when the app's idea of its shape changes.

Numbered functions, each taking the raw dict and returning the next version's
dict. Loading runs every migration above the file's own version and writes the
result back, keeping a `.bak`.

Version 0 is the legacy `digest.toml` from the repository checkout; `legacy.py`
converts it and is not written as an `m00N` here, because it reads a different
file with a different name and has to move a database as well.

Version 1 is the first release. There is nothing above it yet, and that is
correct — this module exists so that the first time there is, an installed user
does not have to hand-edit anything.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from .schema import SCHEMA_VERSION
from .write import dumps, write

log = logging.getLogger("digest.config")

# {version a file is at: function taking it to the next version}
MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


def register(from_version: int):
    def decorate(fn):
        MIGRATIONS[from_version] = fn
        return fn
    return decorate


def migrate(raw: dict, path: Path | None = None) -> dict:
    """Bring `raw` up to SCHEMA_VERSION, writing it back if it moved."""
    version = int(raw.get("schema_version", SCHEMA_VERSION))
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"{path or 'config'} was written by a newer version of the app "
            f"(schema {version}, this app knows {SCHEMA_VERSION}) — upgrade rather "
            "than editing it by hand"
        )
    moved = False
    while version < SCHEMA_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise ValueError(f"no migration from schema {version} to {version + 1}")
        log.info("migrating %s from schema %d to %d", path or "config", version, version + 1)
        raw = step(raw)
        version += 1
        raw["schema_version"] = version
        moved = True
    if moved and path is not None:
        write(path, dumps(raw))
    return raw
