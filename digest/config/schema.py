"""What a valid config file may contain, and what to say when it does not.

The old loader read every key with `raw.get(name, default)` and cast it. That is
right for a file one person edits and wrong for a file a form writes: a typo is
silently a default, so `max_wrods = 4000` reads as 8500 and nothing says so, and
a wrong type is a traceback with no field name in it.

So each file gets a table of fields, and loading either returns a clean dict or
raises `ConfigError` carrying one line per problem, named by file and path:

    config.toml: models.provider — must be one of anthropic, gemini, ollama
    config.toml: output.minutes — expected a number, found "an hour"

Deliberately not pydantic. The design proposed it, and it would give the form its
schema for free, but it is the heaviest dependency in a tool whose entire selling
point is that installing it is one line. The validation here is shallow — types,
choices, ranges — and that is eighty lines of stdlib. Revisit if the form ever
needs something structural.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1

PROVIDERS = ("anthropic", "gemini", "ollama")
SEARCH_BACKENDS = ("brave", "duckduckgo", "none")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class ConfigError(ValueError):
    """One or more fields are wrong. `problems` is one line per field."""

    def __init__(self, filename: str, problems: list[str]):
        self.filename = filename
        self.problems = problems
        super().__init__("\n".join(f"{filename}: {p}" for p in problems))


@dataclass(frozen=True)
class Field:
    path: str
    kind: type
    default: Any = None
    choices: tuple = ()
    minimum: float | None = None
    maximum: float | None = None
    optional: bool = False  # None is allowed and meaningful


# Everything the app writes and a person may read. `advanced` holds the settings
# with a measured right value that no screen offers — see the design's "what a
# non-technical user should never see".
CONFIG_FIELDS = (
    Field("models.provider", str, "ollama", choices=PROVIDERS),
    Field("models.classify_provider", str, None, choices=PROVIDERS, optional=True),
    Field("models.synthesize_provider", str, None, choices=PROVIDERS, optional=True),
    Field("models.classify", str, "qwen3:30b"),
    Field("models.synthesize", str, "gemma3:27b"),

    Field("output.minutes", int, 58, minimum=5, maximum=240),
    Field("output.folder", str, "~/digests"),
    Field("output.html", bool, True),
    Field("output.pdf", bool, False),
    Field("output.audio", bool, False),

    Field("schedule.enabled", bool, False),
    Field("schedule.day", str, "friday", choices=WEEKDAYS),
    Field("schedule.hour", int, 7, minimum=0, maximum=23),

    Field("delivery.drive.enabled", bool, False),
    Field("delivery.drive.folder_id", str, ""),
    Field("delivery.drive.method", str, "oauth", choices=("oauth", "rclone")),
    Field("delivery.drive.rclone_remote", str, ""),

    Field("advanced.max_items", int, 60, minimum=1, maximum=500),
    Field("advanced.contest_share", float, 0.20, minimum=0.0, maximum=1.0),
    Field("advanced.fetch_days", int, 8, minimum=1, maximum=60),
    Field("advanced.ground", bool, True),
    Field("advanced.ground_min_chars", int, 500, minimum=0),
    Field("advanced.search_backend", str, "duckduckgo", choices=SEARCH_BACKENDS),
    Field("advanced.source_min_chars", int, 700, minimum=0),
    Field("advanced.source_max_words", int, 200, minimum=20),
    Field("advanced.classify_batch_size", int, 25, minimum=1, maximum=100),
    Field("advanced.seed", int, 7, optional=True),
    Field("advanced.classify_thinking", str, "low"),
    Field("advanced.synthesize_thinking", str, "medium"),
    Field("advanced.classify_temperature", float, 0.0, optional=True),
    Field("advanced.synthesize_temperature", float, None, optional=True),
    Field("advanced.min_interval_seconds", float, 12.0, minimum=0.0),
    Field("advanced.max_attempts", int, 5, minimum=1),
    Field("advanced.max_backoff_seconds", float, 120.0, minimum=0.0),
    Field("advanced.ollama_host", str, "http://localhost:11434"),
    Field("advanced.ollama_num_ctx", int, 32768, minimum=1024),
    Field("advanced.ollama_think", bool, False, optional=True),
    Field("advanced.ollama_temperature", float, 0.3, optional=True),
    Field("advanced.voice", str, "en-GB-RyanNeural"),
    Field("advanced.tts_engine", str, "edge", choices=("edge", "piper")),
    Field("advanced.tts_offline", bool, False),
    Field("advanced.piper_model", str, ""),
    Field("advanced.chunk_chars", int, 3000, minimum=200),
    Field("advanced.pdf_engine", str, "chrome", choices=("chrome", "weasyprint")),
)

FEED_FIELDS = (
    Field("name", str, ""),
    Field("url", str, ""),
    Field("section", str, "other"),
    Field("weight", float, 1.0, minimum=0.0, maximum=10.0),
    Field("enabled", bool, True),
    Field("verified", str, "", optional=True),
)


def _dig(data: dict, path: str) -> tuple[bool, Any]:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _put(data: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _check(field: Field, value: Any) -> tuple[Any, str | None]:
    if value is None:
        if field.optional:
            return None, None
        return None, f"{field.path} — must not be empty"
    # bool is a subclass of int, so an int field would happily accept `true`.
    if field.kind is bool:
        if not isinstance(value, bool):
            return None, f"{field.path} — expected true or false, found {value!r}"
        return value, None
    if isinstance(value, bool):
        return None, f"{field.path} — expected {field.kind.__name__}, found {value!r}"
    if field.kind is float and isinstance(value, int):
        value = float(value)
    if not isinstance(value, field.kind):
        return None, f"{field.path} — expected {field.kind.__name__}, found {value!r}"
    if field.choices and value not in field.choices:
        return None, f"{field.path} — must be one of {', '.join(map(str, field.choices))}"
    if field.minimum is not None and value < field.minimum:
        return None, f"{field.path} — must be at least {field.minimum}"
    if field.maximum is not None and value > field.maximum:
        return None, f"{field.path} — must be at most {field.maximum}"
    return value, None


def _known_paths(fields: tuple[Field, ...]) -> set[str]:
    return {f.path for f in fields}


def _walk(data: dict, prefix: str = "") -> list[str]:
    out = []
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.extend(_walk(value, f"{path}."))
        else:
            out.append(path)
    return out


def validate_config(raw: dict, filename: str = "config.toml") -> dict:
    """Return the filled-in config, or raise with every problem at once."""
    problems: list[str] = []
    out: dict = {"schema_version": raw.get("schema_version", SCHEMA_VERSION)}

    for field in CONFIG_FIELDS:
        present, value = _dig(raw, field.path)
        if not present:
            _put(out, field.path, field.default)
            continue
        checked, problem = _check(field, value)
        if problem:
            problems.append(problem)
        else:
            _put(out, field.path, checked)

    # A misspelled key is the failure this whole module exists for. Reported
    # rather than ignored, and reported by its full path so it is findable.
    known = _known_paths(CONFIG_FIELDS) | {"schema_version"}
    for path in _walk(raw):
        if path not in known:
            problems.append(f"{path} — not a setting the app knows")

    if problems:
        raise ConfigError(filename, sorted(problems))
    return out


def validate_feeds(raw: dict, filename: str = "feeds.toml") -> list[dict]:
    problems: list[str] = []
    feeds: list[dict] = []
    for n, entry in enumerate(raw.get("feed", []), 1):
        row: dict = {}
        for field in FEED_FIELDS:
            value = entry.get(field.path, field.default)
            checked, problem = _check(field, value)
            if problem:
                problems.append(f"feed {n}: {problem}")
            else:
                row[field.path] = checked
        if not row.get("url"):
            problems.append(f"feed {n}: url — must not be empty")
        if not row.get("name"):
            row["name"] = row.get("url", "")
        for key in entry:
            if key not in _known_paths(FEED_FIELDS):
                problems.append(f"feed {n}: {key} — not a setting the app knows")
        feeds.append(row)
    if problems:
        raise ConfigError(filename, problems)
    return feeds
