"""Read and write the user's lens: the markdown, the form's answers, and the
question of which of the two is currently the truth.

Both files exist because they serve different people. `lens.md` is what the
pipeline reads and what anyone with an editor can change; `lens.toml` is what
the form loads and saves. They can disagree, and the disagreement matters: a
hand edit that the form silently overwrites is somebody's afternoon lost.

So the spec records the hash of the markdown it last produced. If the file on
disk no longer hashes to that, a person has edited it, and the form must say so
before it saves over the top.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..config import paths
from .compile import compile_lens
from .schema import LensSpec


def digest_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class Stored:
    spec: LensSpec | None
    markdown: str
    hand_edited: bool
    recorded_hash: str = ""


def load(config_dir: Path | None = None) -> Stored:
    directory = config_dir or paths.config_dir()
    md_path, spec_path = directory / "lens.md", directory / "lens.toml"
    markdown = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    if not spec_path.exists():
        return Stored(spec=None, markdown=markdown, hand_edited=bool(markdown))
    raw = tomllib.loads(spec_path.read_text(encoding="utf-8"))
    recorded = str(raw.get("compiled_hash", ""))
    spec = LensSpec.from_dict(raw)
    return Stored(
        spec=spec,
        markdown=markdown,
        hand_edited=bool(markdown) and bool(recorded) and digest_of(markdown) != recorded,
        recorded_hash=recorded,
    )


def save(spec: LensSpec, spec_toml: str, config_dir: Path | None = None) -> str:
    """Compile the spec, write both files, and record what was written.

    `spec_toml` is the form's own serialisation, passed in rather than generated,
    so a comment a person left in their lens.toml survives a save.
    """
    from ..config.write import write  # noqa: PLC0415

    directory = config_dir or paths.config_dir()
    markdown = compile_lens(spec)
    write(directory / "lens.md", markdown)
    stamped = _stamp(spec_toml, digest_of(markdown))
    write(directory / "lens.toml", stamped)
    return markdown


def _stamp(spec_toml: str, value: str) -> str:
    lines = [ln for ln in spec_toml.splitlines() if not ln.startswith("compiled_hash")]
    # Above the first table, or a bare key would land inside it.
    for n, line in enumerate(lines):
        if line.startswith("["):
            lines.insert(n, f'compiled_hash = "{value}"')
            break
    else:
        lines.append(f'compiled_hash = "{value}"')
    return "\n".join(lines).rstrip() + "\n"


def install_preset(name: str, config_dir: Path | None = None) -> str:
    """Copy a shipped preset into the config directory.

    The markdown goes across as bytes rather than being recompiled. Phase 0
    measured that rewrapping the same words moves the classifier by about ten
    points, so an untouched preset must be the file that was scored.
    """
    from ..config.write import write  # noqa: PLC0415
    from . import presets  # noqa: PLC0415

    directory = config_dir or paths.config_dir()
    markdown = presets.markdown(name)
    write(directory / "lens.md", markdown)
    spec_toml = presets.spec_path(name).read_text(encoding="utf-8")
    write(directory / "lens.toml", _stamp(spec_toml, digest_of(markdown)))
    return markdown
