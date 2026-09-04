"""Bring a checkout's `digest.toml` into an installed app's config directory.

Runs once, when the app starts and finds no `config.toml` but does find a
`digest.toml` — in the working directory, or at the old `~/.config/digest` path.

It copies rather than moves, and it never deletes the old file. Someone who has
been running this out of a git clone for months should be able to try the
installed version and still have their checkout work.

The database copy is the part that matters most and is easiest to miss. Every
headline the digest has ever seen is in `state.db`, and the whole point of that
table is that a story does not come back next week looking new. On macOS the data
directory changes under the new layout, so without this the first run after
upgrading would republish a chunk of last week.
"""

from __future__ import annotations

import logging
import shutil
import tomllib
from pathlib import Path

from . import paths
from .schema import SCHEMA_VERSION
from .write import dumps, dumps_feeds, write

log = logging.getLogger("digest.config")

WORDS_PER_MINUTE = 145

CONFIG_HEADER = """Plumbing for your weekly digest, written by the app.

The editorial lens — what this briefing is actually about — is lens.md beside
this file, and that is the one worth reading. Anything under [advanced] has a
measured right value behind it; change those only if you have a reason."""

FEEDS_HEADER = """Where the headlines come from.

`weight` breaks a tie when two feeds carry the same story: the higher weight
wins and the loser's link is kept as an "also in". `verified` is the last date
the app fetched this feed successfully."""


def find_legacy_config() -> Path | None:
    for candidate in (Path.cwd() / "digest.toml", paths.LEGACY_CONFIG / "digest.toml"):
        if candidate.exists():
            return candidate
    return None


def to_config(raw: dict) -> dict:
    """Map the flat digest.toml onto the four-file layout's config.toml."""
    run = raw.get("run", {})
    models = raw.get("models", {})
    tts = raw.get("tts", {})
    pdf = raw.get("pdf", {})
    drive = raw.get("drive", {})

    max_words = int(run.get("max_words", 8500))
    engine = pdf.get("engine", "html2pdf")

    return {
        "schema_version": SCHEMA_VERSION,
        "models": {
            "provider": models.get("provider", "ollama"),
            "classify_provider": models.get("classify_provider"),
            "synthesize_provider": models.get("synthesize_provider"),
            "classify": models.get("classify", "qwen3:30b"),
            "synthesize": models.get("synthesize", "gemma3:27b"),
        },
        "output": {
            # A length in minutes is the only form of this number anyone has an
            # opinion about. Words are what the governor actually enforces.
            "minutes": max(5, round(max_words / WORDS_PER_MINUTE)),
            "folder": str(run.get("output_dir", "~/digests")),
            "html": True,
            "pdf": False,
            "audio": bool(tts.get("enabled", False)),
        },
        "schedule": {
            "enabled": False,
            "day": run.get("weekday", "friday"),
            "hour": 7,
        },
        "delivery": {
            "drive": {
                "enabled": bool(drive.get("enabled", False)),
                "folder_id": drive.get("folder_id", ""),
                "method": drive.get("method", "oauth"),
                "rclone_remote": drive.get("rclone_remote", ""),
            }
        },
        "advanced": {
            "max_items": int(run.get("max_items", 60)),
            "contest_share": float(run.get("contest_share", 0.20)),
            "fetch_days": int(run.get("fetch_days", 8)),
            "ground": bool(run.get("ground", True)),
            "ground_min_chars": int(run.get("ground_min_chars", 500)),
            "search_backend": run.get("search_backend", "duckduckgo"),
            "source_min_chars": int(run.get("source_min_chars", 700)),
            "source_max_words": int(run.get("source_max_words", 200)),
            "classify_batch_size": int(models.get("classify_batch_size", 25)),
            "seed": models.get("seed", 7),
            "classify_thinking": models.get("classify_thinking", "low"),
            "synthesize_thinking": models.get("synthesize_thinking", "medium"),
            "classify_temperature": models.get("classify_temperature", 0.0),
            "synthesize_temperature": models.get("synthesize_temperature"),
            "min_interval_seconds": float(models.get("min_interval_seconds", 12.0)),
            "max_attempts": int(models.get("max_attempts", 5)),
            "max_backoff_seconds": float(models.get("max_backoff_seconds", 120.0)),
            "ollama_host": models.get("ollama_host", "http://localhost:11434"),
            "ollama_num_ctx": int(models.get("ollama_num_ctx", 32768)),
            "ollama_think": models.get("ollama_think", False),
            "ollama_temperature": models.get("ollama_temperature", 0.3),
            "voice": tts.get("voice", "en-GB-RyanNeural"),
            "tts_engine": tts.get("engine", "edge"),
            "tts_offline": bool(tts.get("offline", False)),
            "piper_model": tts.get("piper_model", ""),
            "chunk_chars": int(tts.get("chunk_chars", 3000)),
            # html2pdf is a script in the owner's ~/.local/bin. Nobody else has
            # it, so an imported config points at the browser finder instead.
            "pdf_engine": "weasyprint" if engine == "weasyprint" else "chrome",
        },
    }


def to_feeds(raw: dict) -> list[dict]:
    return [
        {
            "name": s["name"],
            "url": s["url"],
            "section": s.get("section", "other"),
            "weight": float(s.get("weight", 1.0)),
            "enabled": True,
        }
        for s in raw.get("sources", [])
    ]


def copy_state(source: Path, destination: Path) -> bool:
    """Copy the seen-headlines database and the logs. Never move them."""
    if source.resolve() == destination.resolve() or not source.exists():
        return False
    destination.mkdir(parents=True, exist_ok=True)
    copied = False
    for name in ("state.db", "state.db-wal", "state.db-shm"):
        src = source / name
        if src.exists() and not (destination / name).exists():
            shutil.copy2(src, destination / name)
            copied = True
    logs = source / "logs"
    if logs.is_dir() and not (destination / "logs").exists():
        shutil.copytree(logs, destination / "logs")
    return copied


def import_legacy(
    legacy_path: Path | None = None,
    lens_source: Path | None = None,
    lens_spec_source: Path | None = None,
) -> dict:
    """Write the four files from a digest.toml. Returns what it did."""
    legacy_path = legacy_path or find_legacy_config()
    if legacy_path is None:
        raise FileNotFoundError(
            "no digest.toml to import — looked in the working directory and "
            f"{paths.LEGACY_CONFIG}"
        )
    raw = tomllib.loads(Path(legacy_path).read_text(encoding="utf-8"))

    package = Path(__file__).resolve().parent.parent
    lens_source = lens_source or package / "prompts" / "rubric.md"
    lens_spec_source = lens_spec_source or package / "lenses" / "architecture-of-rule.toml"

    write(paths.config_file(), dumps(to_config(raw), CONFIG_HEADER))
    write(paths.feeds_file(), dumps_feeds(to_feeds(raw), FEEDS_HEADER))
    # The lens goes across as bytes, not as a recompile. Phase 0 measured that
    # rewrapping the same words moves the classifier by about ten points, so an
    # import must hand over exactly the file that was being used yesterday.
    write(paths.lens_file(), lens_source.read_text(encoding="utf-8"))
    write(paths.lens_spec_file(), lens_spec_source.read_text(encoding="utf-8"))

    moved_db = copy_state(paths.LEGACY_DATA, paths.data_dir())
    log.info("imported %s into %s", legacy_path, paths.config_dir())
    return {
        "from": str(legacy_path),
        "config_dir": str(paths.config_dir()),
        "data_dir": str(paths.data_dir()),
        "feeds": len(raw.get("sources", [])),
        "database_copied": moved_db,
    }
