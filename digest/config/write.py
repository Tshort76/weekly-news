"""Write TOML. The standard library reads it and does not write it.

Small on purpose: the app only ever writes the shapes its own schema defines —
nested tables of scalars, and one array of tables for the feeds. Anything with a
newline in it would need multi-line string handling, and nothing here has one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _table(name: str, table: dict) -> list[str]:
    scalars = {k: v for k, v in table.items() if not isinstance(v, dict)}
    lines = [f"[{name}]"] if name else []
    lines += [f"{k} = {_scalar(v)}" for k, v in scalars.items() if v is not None]
    lines.append("")
    for key, value in table.items():
        if isinstance(value, dict):
            lines += _table(f"{name}.{key}" if name else key, value)
    return lines


def dumps(data: dict, header: str = "") -> str:
    lines = [f"# {line}" if line else "#" for line in header.splitlines()] if header else []
    if lines:
        lines.append("")
    top = {k: v for k, v in data.items() if not isinstance(v, dict)}
    lines += [f"{k} = {_scalar(v)}" for k, v in top.items() if v is not None]
    if top:
        lines.append("")
    for key, value in data.items():
        if isinstance(value, dict):
            lines += _table(key, value)
    return "\n".join(lines).rstrip() + "\n"


def dumps_feeds(feeds: list[dict], header: str = "") -> str:
    lines = [f"# {line}" if line else "#" for line in header.splitlines()] if header else []
    if lines:
        lines.append("")
    lines.append(f"schema_version = {1}")
    for feed in feeds:
        lines.append("")
        lines.append("[[feed]]")
        for key, value in feed.items():
            if value is not None and value != "":
                lines.append(f"{key} = {_scalar(value)}")
    return "\n".join(lines) + "\n"


def write(path: Path, text: str) -> None:
    """Write, keeping one .bak of whatever was there.

    Config is the only thing standing between a user and a working install, and
    the app rewrites it whenever a form is saved. One generation of backup is
    cheap and turns a bad write from a reinstall into a rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_suffix(path.suffix + ".bak").write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    path.write_text(text, encoding="utf-8")
