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
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("digest.discover")



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
        "Measured on 100 labelled headlines: agrees with the rubric on 70 of them, "
        "and errs toward letting an item in rather than dropping one. Thinking is "
        "switched off automatically — with it on this model returned empty answers "
        "and dropped every item that belonged.",
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
