"""Where an API key comes from, in priority order.

The scheduled job is the reason this exists. launchd starts with a bare
environment and never reads a shell profile, so the obvious fix is to put the key
in the plist — but the plist lives in the repository, and a key in a tracked file
is a key that gets committed. A file outside the repo, or the macOS Keychain,
avoids that without making the interactive case any harder.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path

log = logging.getLogger("digest.credentials")

ENV_VARS = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
            "brave": "BRAVE_SEARCH_API_KEY"}
DOTENV_NAME = ".env"
DEFAULT_KEY_FILES = {
    "gemini": Path.home() / ".config/digest/gemini_key",
    "anthropic": Path.home() / ".config/digest/anthropic_key",
    "brave": Path.home() / ".config/digest/brave_key",
}
KEYCHAIN_SERVICES = {"gemini": "digest-gemini", "anthropic": "digest-anthropic",
                     "brave": "digest-brave"}


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


def _from_dotenv(paths: list[Path], var: str) -> tuple[str | None, Path | None]:
    for path in paths:
        if not path.is_file():
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            log.warning(
                "%s is readable by others (mode %o); run: chmod 600 %s", path, mode, path
            )
        value = parse_dotenv(path.read_text(encoding="utf-8")).get(var, "").strip()
        if value:
            return value, path
    return None, None


def dotenv_paths(config_path: Path | None = None) -> list[Path]:
    """Where a .env may live: beside digest.toml first, then the package's own
    directory, then the working directory. Duplicates removed, order kept."""
    candidates: list[Path] = []
    if config_path:
        candidates.append(Path(config_path).resolve().parent / DOTENV_NAME)
    candidates.append(Path(__file__).resolve().parent.parent / DOTENV_NAME)
    candidates.append(Path.cwd() / DOTENV_NAME)
    seen: set[Path] = set()
    return [p for p in candidates if not (p in seen or seen.add(p))]


def _from_file(path: Path) -> str | None:
    if not path.exists():
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        log.warning(
            "%s is readable by others (mode %o); run: chmod 600 %s", path, mode, path
        )
    key = path.read_text(encoding="utf-8").strip()
    return key or None


def _from_keychain(service: str) -> str | None:
    """macOS only, and silent everywhere else."""
    if not shutil.which("security"):
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def resolve(
    provider: str,
    key_file: Path | None = None,
    config_path: Path | None = None,
) -> tuple[str | None, str]:
    """Return (key, where it came from): the real environment, then a .env file,
    then a key file, then the Keychain.

    A real environment variable beats .env, which is the dotenv convention and
    what makes a one-off override work. Callers that report the source use this
    rather than re-deriving it, so what `doctor` prints and what a run actually
    reads can never disagree.
    """
    env_var = ENV_VARS.get(provider)
    if env_var and os.environ.get(env_var, "").strip():
        return os.environ[env_var].strip(), f"${env_var}"

    if env_var:
        key, found_in = _from_dotenv(dotenv_paths(config_path), env_var)
        if key:
            return key, f"{found_in} ({env_var})"

    path = key_file or DEFAULT_KEY_FILES.get(provider)
    if path:
        expanded = Path(os.path.expanduser(str(path)))
        key = _from_file(expanded)
        if key:
            return key, str(expanded)

    service = KEYCHAIN_SERVICES.get(provider)
    if service:
        key = _from_keychain(service)
        if key:
            return key, f"the Keychain, service {service!r}"
    return None, "nowhere"


def api_key(
    provider: str, key_file: Path | None = None, config_path: Path | None = None
) -> str | None:
    return resolve(provider, key_file, config_path)[0]


def describe_sources(
    provider: str, key_file: Path | None = None, config_path: Path | None = None
) -> str:
    """What to tell someone whose key was not found anywhere."""
    env_var = ENV_VARS.get(provider, "the API key variable")
    path = key_file or DEFAULT_KEY_FILES.get(provider)
    service = KEYCHAIN_SERVICES.get(provider)
    candidates = dotenv_paths(config_path)
    dotenv = candidates[0] if candidates else Path.cwd() / DOTENV_NAME
    return (
        f"no {provider} key found. Looked at, in order:\n"
        f"  1. ${env_var} in the environment\n"
        f"  2. {env_var} in {dotenv}\n"
        f"  3. {path}\n"
        f"  4. the macOS Keychain, service {service!r}\n"
        f"Set one with:  printf '{env_var}=%s\\n' 'YOUR_KEY' > {dotenv} && chmod 600 {dotenv}"
    )
