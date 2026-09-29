"""Where an API key comes from: the environment, then the checkout's `.env`.

The menu-bar plugin starts runs with a bare environment and no shell profile,
so a key exported in `~/.zshrc` is not enough on its own. The `.env` beside the
code is gitignored and is read the same way however the run was started.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from .config.paths import REPO

log = logging.getLogger("digest.credentials")

ENV_VARS = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
            "brave": "BRAVE_SEARCH_API_KEY"}
DOTENV = REPO / ".env"


def parse_dotenv(text: str) -> dict[str, str]:
    """A deliberately small .env parser: KEY=value, one per line.

    Handles a leading `export`, surrounding single or double quotes, blank lines
    and whole-line comments. It does not handle multi-line values or variable
    interpolation, because an API key is one line and pretending otherwise would
    mean silently mis-reading a file someone hand-edited.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _from_dotenv(path: Path, var: str) -> str | None:
    if not path.is_file():
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        log.warning("%s is readable by others (mode %o); run: chmod 600 %s", path, mode, path)
    return parse_dotenv(path.read_text(encoding="utf-8")).get(var, "").strip() or None


def resolve(provider: str, dotenv: Path = DOTENV) -> tuple[str | None, str]:
    """Return (key, where it came from). A real environment variable beats .env,
    which is the dotenv convention and what makes a one-off override work."""
    env_var = ENV_VARS.get(provider)
    if not env_var:
        return None, "nowhere"
    if os.environ.get(env_var, "").strip():
        return os.environ[env_var].strip(), f"${env_var}"
    key = _from_dotenv(dotenv, env_var)
    if key:
        return key, f"{dotenv} ({env_var})"
    return None, "nowhere"


def api_key(provider: str) -> str | None:
    return resolve(provider)[0]


def describe_sources(provider: str, dotenv: Path = DOTENV) -> str:
    """What to tell someone whose key was not found anywhere."""
    env_var = ENV_VARS.get(provider, "the API key variable")
    return (
        f"no {provider} key found. Looked for ${env_var} in the environment, then "
        f"in {dotenv}.\n"
        f"Add one with:  printf '{env_var}=%s\\n' 'YOUR_KEY' >> {dotenv} && chmod 600 {dotenv}"
    )
