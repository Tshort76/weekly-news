"""The config the pipeline actually receives.

These dataclasses are the runtime shape and have not changed: every stage still
takes the same `Config` it took before there was an installer. What changed is
where the values come from — an installed app reads the four validated files in
`paths.config_dir()`, while a checkout can still hand `load()` a `digest.toml`.
Both paths end here, so no stage knows the difference.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Source
from . import paths as app_paths

DEFAULT_CONFIG_PATHS = [
    Path("digest.toml"),
    Path.home() / ".config" / "digest" / "digest.toml",
]

STATE_DIR = Path(os.environ.get("DIGEST_STATE_DIR", Path.home() / ".local/share/digest"))


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
class CredentialsCfg:
    """Optional overrides. Empty means use the defaults in digest.credentials:
    the environment variable, then ~/.config/digest/<provider>_key, then the
    macOS Keychain."""

    gemini_key_file: Path | None = None
    anthropic_key_file: Path | None = None

    def key_file(self, provider: str) -> Path | None:
        return self.gemini_key_file if provider == "gemini" else self.anthropic_key_file


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
    credentials: CredentialsCfg = field(default_factory=CredentialsCfg)
    sources: list[Source] = field(default_factory=list)
    prompts_dir: Path = Path(__file__).resolve().parent.parent / "prompts"
    state_dir: Path = STATE_DIR
    config_path: Path | None = None  # so a .env is looked for beside digest.toml
    # The editorial lens. `lens_path` is the user's copy once installed; without
    # one the packaged rubric is used, which is what a checkout does today.
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


def _expand(p: str | Path) -> Path:
    return Path(os.path.expanduser(str(p))).resolve()


def find_config(explicit: str | Path | None = None) -> Path:
    if explicit:
        p = _expand(explicit)
        if not p.exists():
            raise FileNotFoundError(f"config not found: {p}")
        return p
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(
        "no digest.toml found — looked in "
        + ", ".join(str(p) for p in DEFAULT_CONFIG_PATHS)
    )


def load(path: str | Path | None = None) -> Config:
    cfg_path = find_config(path)
    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))

    run = raw.get("run", {})
    models = raw.get("models", {})
    tts = raw.get("tts", {})
    drive = raw.get("drive", {})
    pdf = raw.get("pdf", {})
    creds = raw.get("credentials", {})

    cfg = Config(
        run=RunCfg(
            weekday=run.get("weekday", "friday"),
            max_words=int(run.get("max_words", 8500)),
            max_items=int(run.get("max_items", 60)),
            contest_share=float(run.get("contest_share", 0.20)),
            fetch_days=int(run.get("fetch_days", 8)),
            ground=bool(run.get("ground", True)),
            ground_min_chars=int(run.get("ground_min_chars", 500)),
            search_backend=run.get("search_backend", "duckduckgo"),
            source_min_chars=int(run.get("source_min_chars", 700)),
            source_max_words=int(run.get("source_max_words", 200)),
            output_dir=_expand(run.get("output_dir", "~/digests")),
        ),
        models=ModelsCfg(
            provider=models.get("provider", "gemini"),
            classify_provider=models.get("classify_provider"),
            synthesize_provider=models.get("synthesize_provider"),
            classify=models.get("classify", "gemini-3.8-flash"),
            synthesize=models.get("synthesize", "gemini-3.8-flash"),
            classify_batch_size=int(models.get("classify_batch_size", 25)),
            seed=models.get("seed", 7),
            classify_thinking=models.get("classify_thinking", "low"),
            synthesize_thinking=models.get("synthesize_thinking", "medium"),
            classify_temperature=models.get("classify_temperature", 0.0),
            synthesize_temperature=models.get("synthesize_temperature"),
            min_interval_seconds=float(models.get("min_interval_seconds", 4.0)),
            max_attempts=int(models.get("max_attempts", 5)),
            backoff_seconds=tuple(
                float(x) for x in models.get("backoff_seconds", [10, 20, 40, 60])
            ),
            max_backoff_seconds=float(models.get("max_backoff_seconds", 120.0)),
            ollama_host=models.get("ollama_host", "http://localhost:11434"),
            ollama_num_ctx=int(models.get("ollama_num_ctx", 32768)),
            ollama_think=models.get("ollama_think"),
            ollama_temperature=models.get("ollama_temperature"),
        ),
        tts=TtsCfg(
            enabled=bool(tts.get("enabled", False)),
            engine=tts.get("engine", "edge"),
            voice=tts.get("voice", "en-GB-RyanNeural"),
            offline=bool(tts.get("offline", False)),
            piper_model=tts.get("piper_model", ""),
            chunk_chars=int(tts.get("chunk_chars", 3000)),
        ),
        drive=DriveCfg(
            enabled=bool(drive.get("enabled", False)),
            folder_id=drive.get("folder_id", ""),
            method=drive.get("method", "oauth"),
            rclone_remote=drive.get("rclone_remote", ""),
            credentials_file=_expand(
                drive.get("credentials_file", "~/.config/digest/credentials.json")
            ),
            token_file=_expand(drive.get("token_file", "~/.config/digest/token.json")),
        ),
        pdf=PdfCfg(engine=pdf.get("engine", "html2pdf")),
        credentials=CredentialsCfg(
            gemini_key_file=_expand(creds["gemini_key_file"])
            if creds.get("gemini_key_file") else None,
            anthropic_key_file=_expand(creds["anthropic_key_file"])
            if creds.get("anthropic_key_file") else None,
        ),
        config_path=cfg_path,
        sources=[
            Source(
                name=s["name"],
                url=s["url"],
                section=s.get("section", "other"),
                weight=float(s.get("weight", 1.0)),
            )
            for s in raw.get("sources", [])
        ],
    )
    return cfg
