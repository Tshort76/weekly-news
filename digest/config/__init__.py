"""Configuration: the runtime shape, and the four files it is read from.

`paths.config_dir()` — the checkout's `config/` directory — holds config.toml,
feeds.toml, lens.md and lens.toml. `load()` validates them and builds the
`Config` every stage takes.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from ..models import Source
from . import paths
from .runtime import (
    Config,
    DriveCfg,
    ModelsCfg,
    PdfCfg,
    RunCfg,
    TtsCfg,
)
from .schema import ConfigError, validate_config, validate_feeds

__all__ = [
    "Config", "ConfigError", "DriveCfg", "ModelsCfg", "PdfCfg", "RunCfg", "TtsCfg",
    "load", "paths", "validate_config", "validate_feeds",
]

WORDS_PER_MINUTE = 145


def _read(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def load() -> Config:
    """Build the runtime config from the four files in `paths.config_dir()`."""
    config_path = paths.config_file()
    if not config_path.exists():
        raise FileNotFoundError(f"no config at {config_path}")
    data = validate_config(_read(config_path))

    feeds_path = paths.feeds_file()
    feeds = validate_feeds(_read(feeds_path)) if feeds_path.exists() else []

    models, output, schedule = data["models"], data["output"], data["schedule"]
    adv = data["advanced"]
    drive = data["delivery"]["drive"]

    return Config(
        run=RunCfg(
            weekday=schedule["day"],
            # Minutes is what the user chose; words is what the governor counts.
            max_words=int(output["minutes"] * WORDS_PER_MINUTE),
            max_items=adv["max_items"],
            contest_share=adv["contest_share"],
            fetch_days=adv["fetch_days"],
            output_dir=Path(output["folder"]).expanduser(),
            ground=adv["ground"],
            ground_min_chars=adv["ground_min_chars"],
            search_backend=adv["search_backend"],
            source_min_chars=adv["source_min_chars"],
            source_max_words=adv["source_max_words"],
        ),
        models=ModelsCfg(
            provider=models["provider"],
            classify_provider=models["classify_provider"],
            synthesize_provider=models["synthesize_provider"],
            classify=models["classify"],
            synthesize=models["synthesize"],
            classify_batch_size=adv["classify_batch_size"],
            seed=adv["seed"],
            classify_thinking=adv["classify_thinking"],
            synthesize_thinking=adv["synthesize_thinking"],
            classify_temperature=adv["classify_temperature"],
            synthesize_temperature=adv["synthesize_temperature"],
            min_interval_seconds=adv["min_interval_seconds"],
            max_attempts=adv["max_attempts"],
            max_backoff_seconds=adv["max_backoff_seconds"],
            ollama_host=adv["ollama_host"],
            ollama_num_ctx=adv["ollama_num_ctx"],
            ollama_think=adv["ollama_think"],
            ollama_temperature=adv["ollama_temperature"],
        ),
        tts=TtsCfg(
            enabled=output["audio"],
            engine=adv["tts_engine"],
            voice=adv["voice"],
            offline=adv["tts_offline"],
            piper_model=adv["piper_model"],
            chunk_chars=adv["chunk_chars"],
        ),
        drive=DriveCfg(
            enabled=drive["enabled"],
            folder_id=drive["folder_id"],
            method=drive["method"],
            rclone_remote=drive["rclone_remote"],
            credentials_file=paths.config_dir() / "credentials.json",
            token_file=paths.config_dir() / "token.json",
        ),
        pdf=PdfCfg(engine=adv["pdf_engine"]),
        sources=[
            Source(name=f["name"], url=f["url"], section=f["section"], weight=f["weight"])
            for f in feeds
            if f["enabled"]
        ],
        state_dir=paths.data_dir(),
        config_path=config_path,
        lens_path=paths.lens_file(),
        lens_spec_path=paths.lens_spec_file(),
    )

