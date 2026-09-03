"""Structured logging to a per-week file, plus a readable console stream."""

from __future__ import annotations

import logging
from pathlib import Path

FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)-18s %(message)s"
CONSOLE_FORMAT = "%(levelname)-7s %(message)s"


def setup(log_dir: Path, week: str, verbose: bool = False) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{week}.log"

    root = logging.getLogger("digest")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    root.addHandler(console)

    return path
