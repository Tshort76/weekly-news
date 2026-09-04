"""The terminal setup wizard: `digest init`.

Every question has a default, so pressing Enter the whole way through produces a
working install. That is the actual design constraint — someone who does not
know what a filter model is should still end up with something that runs, and
find out what the choices meant later, from the app.

It writes as it goes. Closing the terminal halfway leaves a config that is
further along than nothing, not a half-written file.

The browser version of this arrives in phase 2 and shares everything below the
prompting: discovery, recommendation, and the writers here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import discover
from .config import legacy, paths
from .config.schema import SCHEMA_VERSION
from .config.write import dumps, dumps_feeds, write
from .lens import presets, store

WORDS_PER_MINUTE = 145


def ask(question: str, default: str = "", choices: tuple = ()) -> str:
    shown = f" [{default}]" if default else ""
    while True:
        try:
            answer = input(f"{question}{shown} ").strip()
        except EOFError:  # piped input that ran out — take the defaults
            print()
            return default
        answer = answer or default
        if not choices or answer in choices:
            return answer
        print(f"  please answer one of: {', '.join(choices)}")


def ask_yes(question: str, default: bool = True) -> bool:
    return ask(question, "yes" if default else "no", ("yes", "no", "y", "n")).startswith("y")


def _choose_lens() -> str:
    names = presets.available()
    if len(names) == 1:
        only = presets.load(names[0])
        print(f"\nLens: {only.name}")
        print("  (more presets ship with later versions; you can edit yours any time)")
        return names[0]
    print("\nWhat should this briefing be about?")
    for n, name in enumerate(names, 1):
        print(f"  {n}. {presets.load(name).name}")
    pick = ask("Choose a number:", "1")
    try:
        return names[int(pick) - 1]
    except (ValueError, IndexError):
        return names[0]


def _choose_models(host: str) -> dict:
    found = discover.probe_ollama(host)
    memory = discover.total_memory_gb()
    print()
    if found.running:
        print(f"Ollama is running with {len(found.models)} model(s) pulled.")
    else:
        print(found.reason)
    if memory:
        print(f"This machine has about {memory:.0f} GB of memory.")

    picked = {}
    for stage, label in (("classify", "reading the headlines"), ("synthesize", "writing")):
        suggestion = discover.recommend(stage, found, memory)
        print(f"\nFor {label}: {suggestion['model']}")
        print(f"  {suggestion['why']}")
        if suggestion.get("pull"):
            print(f"  Not pulled yet — run: ollama pull {suggestion['model']}")
        model = ask(f"  Model for {label}:", suggestion["model"])
        provider = suggestion["provider"]
        if model != suggestion["model"]:
            known = discover.BY_NAME.get(model)
            provider = "anthropic" if known and known.tier == "hosted" else provider
        picked[stage] = {"model": model, "provider": provider}
    return picked


def _needs_key(provider: str) -> None:
    from . import credentials  # noqa: PLC0415

    key, source = credentials.resolve(provider)
    if key:
        print(f"  Using the {provider} key from {source}.")
        return
    print(f"\n  {provider} needs an API key.")
    print(f"  Set it later with:  digest key set {provider}")


def run(host: str = "http://localhost:11434") -> int:
    print("Setting up your weekly digest.\n")
    print(f"Config will live in {paths.config_dir()}")
    print(f"Data (what you have already seen) in {paths.data_dir()}")

    existing = legacy.find_legacy_config()
    if existing and not paths.is_installed():
        if ask_yes(f"\nFound {existing}. Import it?", True):
            report = legacy.import_legacy(existing)
            print(f"  Imported {report['feeds']} feeds into {report['config_dir']}.")
            if report["database_copied"]:
                print("  Copied the record of what you have already seen.")
            print("\nDone. Try:  digest run --dry-run")
            return 0

    lens_name = _choose_lens()
    spec = presets.load(lens_name)
    store.install_preset(lens_name)

    models = _choose_models(host)
    for stage in ("classify", "synthesize"):
        if models[stage]["provider"] != "ollama":
            _needs_key(models[stage]["provider"])

    print()
    minutes = ask("How long should the briefing be, in minutes of listening?", "58")
    folder = ask("Where should the files go?", "~/digests")

    providers = {models[s]["provider"] for s in models}
    config = {
        "schema_version": SCHEMA_VERSION,
        "models": {
            "provider": models["synthesize"]["provider"],
            "classify_provider": models["classify"]["provider"]
            if len(providers) > 1 else None,
            "synthesize_provider": None,
            "classify": models["classify"]["model"],
            "synthesize": models["synthesize"]["model"],
        },
        "output": {
            "minutes": int(minutes) if str(minutes).isdigit() else 58,
            "folder": folder, "html": True, "pdf": False, "audio": False,
        },
        "schedule": {"enabled": False, "day": "friday", "hour": 7},
        "delivery": {"drive": {"enabled": False, "folder_id": "",
                               "method": "oauth", "rclone_remote": ""}},
        "advanced": _advanced_defaults(models, host),
    }
    write(paths.config_file(), dumps(config, legacy.CONFIG_HEADER))
    feeds = [dict(f, enabled=True) for f in spec.feeds]
    write(paths.feeds_file(), dumps_feeds(feeds, legacy.FEEDS_HEADER))

    print(f"\nWritten. {len(feeds)} feeds, lens '{spec.name}'.")
    print("\nTry a run that changes nothing:\n  digest run --dry-run")
    return 0


def _advanced_defaults(models: dict, host: str) -> dict:
    """The settings with a measured right value that no screen offers."""
    classify_model = models["classify"]["model"]
    local = models["classify"]["provider"] == "ollama"
    # A reasoning model must have its think block off or every item comes back
    # unjudged. Asked of Ollama rather than guessed from the name.
    think_off = discover.wants_thinking_off(classify_model, host) if local else None
    return {
        "max_items": 60, "contest_share": 0.20, "fetch_days": 8,
        "ground": True, "ground_min_chars": 500, "search_backend": "duckduckgo",
        "source_min_chars": 700, "source_max_words": 200,
        "classify_batch_size": 25, "seed": 7,
        "classify_thinking": "low", "synthesize_thinking": "medium",
        "classify_temperature": 0.0, "synthesize_temperature": None,
        "min_interval_seconds": 0.0 if local else 12.0,
        "max_attempts": 5, "max_backoff_seconds": 120.0,
        "ollama_host": host, "ollama_num_ctx": 32768,
        "ollama_think": False if think_off else None,
        "ollama_temperature": 0.3,
        "voice": "en-GB-RyanNeural", "tts_engine": "edge", "tts_offline": False,
        "piper_model": "", "chunk_chars": 3000, "pdf_engine": "chrome",
    }


def main() -> int:
    try:
        return run()
    except KeyboardInterrupt:
        print("\nStopped. Nothing further was written.", file=sys.stderr)
        return 1
