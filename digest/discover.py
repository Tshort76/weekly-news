"""What models this machine can actually run, and which one to suggest.

Three cheap questions, none of which the user should have to answer: is Ollama
running, what is pulled, and is there enough memory for the model we would
otherwise recommend.

`fetch` is injected so the tests hand this canned responses and never open a
socket. Everything here degrades to "no local models" rather than raising — a
machine with no Ollama is a supported machine, not an error.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("digest.discover")

GIGABYTE = 1024 ** 3


@dataclass(frozen=True)
class KnownModel:
    """What this project has actually measured about a model.

    `note` is shown to the user verbatim, so it says what was measured rather
    than how good the model is. Where nothing was measured it says so — the app
    never invents a score, and the calibration screen exists precisely so a user
    can find out what an untested model does on their own lens.
    """

    name: str
    roles: tuple[str, ...]
    tier: str  # small | large | hosted — decides the writer notes, not the price
    gigabytes: float
    measured: bool
    note: str
    recommended: bool = False


# The numbers come from the README and from this repository's own runs. Nothing
# here is an estimate.
KNOWN_MODELS: tuple[KnownModel, ...] = (
    KnownModel(
        "qwen3:30b", ("classify",), "large", 20.0, True,
        "Measured on 25 labelled headlines: dropped nothing that belonged, let in "
        "three or four. Thinking is switched off automatically — with it on this "
        "model returned empty answers and dropped every item that belonged.",
        recommended=True,
    ),
    KnownModel(
        "gemma3:27b", ("synthesize",), "small", 17.0, True,
        "The shipped writer. Roughly half of a week is published in the reporter's "
        "own words and never reaches it at all.",
        recommended=True,
    ),
    KnownModel(
        "qwen3-coder:30b", ("classify",), "large", 19.0, True,
        "Measured worse than qwen3:30b as a filter, and it dropped an item that "
        "belonged. Not recommended.",
    ),
    KnownModel(
        "claude-haiku-4-5", ("classify",), "hosted", 0.0, True,
        "Hosted. About sixty cents a week for filtering and writing together, "
        "with claude-sonnet-5.", recommended=True,
    ),
    KnownModel(
        "claude-sonnet-5", ("synthesize",), "hosted", 0.0, True,
        "Hosted, and the strongest writer measured here. Rejects a temperature "
        "setting, which the config already accounts for.", recommended=True,
    ),
    KnownModel(
        "gemini-3.8-flash", ("classify", "synthesize"), "hosted", 0.0, True,
        "Hosted. In September 2026 the free tier could not finish a week — the "
        "budget ran out within a couple of calls and the edition came out partial. "
        "A paid key works.",
    ),
)

BY_NAME = {m.name: m for m in KNOWN_MODELS}


@dataclass
class Ollama:
    """What we found. `reason` is shown to the user when nothing is available."""

    running: bool = False
    installed: bool = False
    models: list[dict] = field(default_factory=list)
    reason: str = ""

    def names(self) -> list[str]:
        return [m.get("name", "") for m in self.models]


def _urlopen_fetch(url: str, timeout: float = 3.0, payload: dict | None = None) -> bytes:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"} if data else {}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def ollama_installed() -> bool:
    """Distinguishes "not running" from "not installed", which need different advice."""
    if shutil.which("ollama"):
        return True
    return Path("/Applications/Ollama.app").exists()


def probe_ollama(host: str = "http://localhost:11434", fetch=None) -> Ollama:
    fetch = fetch or _urlopen_fetch
    try:
        payload = json.loads(fetch(f"{host}/api/tags"))
    except Exception as exc:  # refused, timed out, garbage — all one answer here
        installed = ollama_installed()
        return Ollama(
            running=False,
            installed=installed,
            reason=(
                "Ollama is installed but not running — start it and try again."
                if installed
                else "Ollama is not installed. Get it from ollama.com, or use a "
                     "hosted model instead."
            ),
        )
    models = payload.get("models", []) if isinstance(payload, dict) else []
    return Ollama(running=True, installed=True, models=models)


def capabilities(model: str, host: str = "http://localhost:11434", fetch=None) -> list[str]:
    fetch = fetch or _urlopen_fetch
    try:
        payload = json.loads(fetch(f"{host}/api/show", payload={"model": model}))
    except Exception:
        return []
    caps = payload.get("capabilities") if isinstance(payload, dict) else None
    return [str(c) for c in caps] if isinstance(caps, list) else []


def wants_thinking_off(model: str, host: str = "http://localhost:11434", fetch=None) -> bool:
    """A reasoning model must have its think block disabled for this workload.

    Measured on qwen3:30b: a thinking block in front of a schema-constrained
    answer comes back empty rather than as an error, so every item fell through
    as unjudged — 0 exact and all eleven items that belonged dropped. With it
    off, 76% exact and none dropped. The user never sees this setting.
    """
    return "thinking" in capabilities(model, host, fetch)


def total_memory_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GIGABYTE
    except (ValueError, OSError, AttributeError):
        pass
    try:
        import psutil  # noqa: PLC0415

        return psutil.virtual_memory().total / GIGABYTE
    except Exception:
        return 0.0


def fits(model: KnownModel, memory_gb: float) -> bool:
    """Resident size plus room for everything else the machine is doing."""
    return memory_gb <= 0 or model.gigabytes + 6 <= memory_gb


def recommend(stage: str, found: Ollama, memory_gb: float | None = None) -> dict:
    """What to suggest for one stage, and why, in the user's words.

    The order is deliberate and never invents a score: a measured model that is
    pulled and fits; else a measured model that fits and could be pulled; else
    the largest pulled model, labelled untested; else hosted.
    """
    memory_gb = total_memory_gb() if memory_gb is None else memory_gb
    pulled = set(found.names())
    candidates = [m for m in KNOWN_MODELS if stage in m.roles and m.tier != "hosted"]

    for model in candidates:
        if model.recommended and model.name in pulled and fits(model, memory_gb):
            return {"model": model.name, "provider": "ollama", "why": model.note,
                    "measured": True}
    for model in candidates:
        if model.recommended and fits(model, memory_gb):
            return {"model": model.name, "provider": "ollama", "pull": True,
                    "why": f"{model.note} About {model.gigabytes:.0f} GB to download.",
                    "measured": True}
    untested = sorted(pulled - {m.name for m in KNOWN_MODELS})
    if untested:
        return {
            "model": untested[0], "provider": "ollama", "measured": False,
            "why": "Not yet measured against a lens. Run the check after setup to "
                   "see what it does with your own headlines.",
        }
    hosted = next(m for m in KNOWN_MODELS if m.tier == "hosted" and m.recommended
                  and stage in m.roles)
    return {
        "model": hosted.name, "provider": "anthropic", "measured": True,
        "why": found.reason or (
            f"No local model here fits in {memory_gb:.0f} GB of memory."
        ) + " " + hosted.note,
    }


def writes_like_a_small_model(cfg) -> bool:
    """Whether the writer needs the extra rules a weaker model needs.

    Keyed on the model rather than on the provider, which is what it used to be.
    The measurement behind those rules was taken on gemma3:27b, and a hosted
    model scored zero on every habit they correct — so a strong model running
    locally should not get them, and a small hosted one should. The default is
    unchanged for every configuration this project has actually run.
    """
    local = cfg.models.provider_for("synthesize") == "ollama"
    known = BY_NAME.get(cfg.models.synthesize)
    # Only trust the name when it agrees with the provider. A config naming a
    # hosted model with provider = "ollama" is a mistake somewhere, and the
    # provider is the half that decides what actually gets called.
    if known is not None and (known.tier == "hosted") != local:
        return known.tier == "small"
    return local
