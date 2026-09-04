"""Configuration: the runtime shape, and the three places it can come from.

`from digest.config import Config, RunCfg, load` works exactly as it did when
this was one module — that is deliberate, because every stage imports from here
and none of them should care that an installer now exists.

Three sources, tried in this order by `load()` with no argument:

1. The installed config — four validated files in `paths.config_dir()`.
2. A `digest.toml` in the working directory or at the old config path, which is
   what a git checkout has. Loaded directly, so a contributor's clone keeps
   working with no import step.
3. Neither, which is an error naming both places it looked.

An explicit path always means a `digest.toml`; that is what the scripts pass.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from ..models import Source
from . import legacy, paths
from .migrate import migrate
from .runtime import (
    DEFAULT_CONFIG_PATHS,
    STATE_DIR,
    Config,
    CredentialsCfg,
    DriveCfg,
    ModelsCfg,
    PdfCfg,
    RunCfg,
    TtsCfg,
    find_config,
)
from .runtime import load as load_toml
from .schema import ConfigError, validate_config, validate_feeds

__all__ = [
    "Config", "ConfigError", "CredentialsCfg", "DEFAULT_CONFIG_PATHS", "DriveCfg",
    "ModelsCfg", "PdfCfg", "RunCfg", "STATE_DIR", "TtsCfg", "find_config", "legacy",
    "load", "load_installed", "load_toml", "paths", "validate_config", "validate_feeds",
]

WORDS_PER_MINUTE = 145


def _read(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def load_installed() -> Config:
    """Build the runtime config from the four files an installed app writes."""
    config_path = paths.config_file()
    raw = migrate(_read(config_path), config_path)
    data = validate_config(raw)

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


def load(path: str | Path | None = None) -> Config:
    if path is not None:
        return load_toml(path)
    if paths.is_installed():
        return load_installed()
    if legacy.find_legacy_config() is not None:
        return load_toml(None)
    raise FileNotFoundError(
        "no configuration found. Run `digest init` to set one up, or `digest "
        f"import` if you have a digest.toml. Looked in {paths.config_dir()} and "
        "for a digest.toml in the working directory."
    )
