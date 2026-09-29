"""The config the pipeline actually receives.

`digest.config.load()` fills these from the files in `config/`; the tests build
them in code. No stage knows which.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..models import Source
from . import paths as app_paths


@dataclass
class RunCfg:
    weekday: str = "friday"
    max_words: int = 8500
    max_items: int = 60
    contest_share: float = 0.20
    fetch_days: int = 8
    output_dir: Path = Path.home() / "digests"
    # Grounding: a selected item whose feed entry carries less than this gets
    # its own page fetched, and failing that a search. 500 is about three
    # sentences — below that a writer is describing a story it was barely told.
    ground: bool = True
    ground_min_chars: int = 500
    # duckduckgo needs no key and rate-limits hard; brave needs a key and does
    # not. "none" turns searching off while leaving article fetching on.
    search_backend: str = "duckduckgo"
    # A story a person already wrote up at this length is carried in their
    # words instead of being rewritten. Capped so one long article cannot eat
    # the briefing; the appendix link carries the rest.
    source_min_chars: int = 700
    source_max_words: int = 200


@dataclass
class ModelsCfg:
    # `provider` is the default for both stages; either can override it, so the
    # filtering can run on a local model while the writing runs on a hosted one.
    provider: str = "gemini"  # gemini | anthropic | ollama
    classify_provider: str | None = None
    synthesize_provider: str | None = None
    classify: str = "gemini-3.8-flash"
    synthesize: str = "gemini-3.8-flash"
    classify_batch_size: int = 25

    # Gemini's interactions API takes no temperature; `seed` is the
    # reproducibility lever and `thinking_level` trades depth against tokens.
    seed: int | None = 7
    classify_thinking: str = "low"
    synthesize_thinking: str = "medium"

    # Used by the Anthropic and Ollama backends; the Gemini backend never reads
    # it, because that API surface has no temperature at all. Pinned at zero so
    # a classification run is reproducible.
    classify_temperature: float | None = 0.0
    synthesize_temperature: float | None = None

    # Free-tier accounts are capped on requests per minute and cannot read their
    # own limit from here, so calls are spaced out and a 429 is obeyed.
    min_interval_seconds: float = 4.0
    max_attempts: int = 5
    backoff_seconds: tuple[float, ...] = (10.0, 20.0, 40.0, 60.0)
    max_backoff_seconds: float = 120.0

    # Ollama only. A local model is not rate-limited, so pacing is off and the
    # context has to be large enough to hold the rubric plus a whole batch.
    ollama_host: str = "http://localhost:11434"
    ollama_num_ctx: int = 32768
    ollama_think: bool | None = None  # False disables a reasoning model's think block
    # Sampling temperature for a stage whose own `*_temperature` is unset. None
    # leaves the model's Modelfile default in force (1.0 for gemma3). Ollama-scoped
    # because the Anthropic backend forwards any stage temperature and Sonnet 5
    # rejects one, so the shared synthesize_temperature slot has to stay empty.
    ollama_temperature: float | None = None

    def provider_for(self, stage: str) -> str:
        override = self.classify_provider if stage == "classify" else self.synthesize_provider
        return override or self.provider


@dataclass
class TtsCfg:
    enabled: bool = False
    engine: str = "edge"  # edge | piper
    voice: str = "en-GB-RyanNeural"
    offline: bool = False
    piper_model: str = ""
    chunk_chars: int = 3000


@dataclass
class DriveCfg:
    enabled: bool = False
    folder_id: str = ""
    method: str = "oauth"  # oauth | rclone
    rclone_remote: str = ""
    credentials_file: Path = Path.home() / ".config/digest/credentials.json"
    token_file: Path = Path.home() / ".config/digest/token.json"


@dataclass
class PdfCfg:
    engine: str = "html2pdf"  # html2pdf | weasyprint


@dataclass
class Config:
    run: RunCfg = field(default_factory=RunCfg)
    models: ModelsCfg = field(default_factory=ModelsCfg)
    tts: TtsCfg = field(default_factory=TtsCfg)
    drive: DriveCfg = field(default_factory=DriveCfg)
    pdf: PdfCfg = field(default_factory=PdfCfg)
    sources: list[Source] = field(default_factory=list)
    prompts_dir: Path = Path(__file__).resolve().parent.parent / "prompts"
    state_dir: Path = field(default_factory=app_paths.data_dir)
    config_path: Path | None = None
    # The editorial lens. `lens_path` is config/lens.md; a Config built in code
    # (the tests) has none, and the packaged rubric is used.
    lens_path: Path | None = None
    lens_spec_path: Path | None = None
    title: str = ""

    @property
    def db_path(self) -> Path:
        return self.state_dir / "state.db"

    @property
    def log_dir(self) -> Path:
        return self.state_dir / "logs"

    def prompt(self, name: str) -> str:
        """One of the four machinery prompts, which ship with the package."""
        return (self.prompts_dir / name).read_text(encoding="utf-8")

    @property
    def lens_text(self) -> str:
        """The rubric, verbatim. The user's file wins over the packaged one."""
        if self.lens_path and self.lens_path.exists():
            return self.lens_path.read_text(encoding="utf-8")
        return self.prompt("rubric.md")

    @property
    def lens(self):
        """The lens as fields — the regions, domains and kind words the model is
        offered. Falls back to the shipped preset, whose lists are the ones that
        were hardcoded in `classify.md` before any of this existed."""
        cached = getattr(self, "_lens_cache", None)
        if cached is not None:
            return cached
        from ..lens.schema import LensSpec  # noqa: PLC0415

        path = self.lens_spec_path
        if path is None or not Path(path).exists():
            path = Path(__file__).resolve().parent.parent / "lenses" / "architecture-of-rule.toml"
        spec = LensSpec.from_toml(path)
        object.__setattr__(self, "_lens_cache", spec)
        return spec

