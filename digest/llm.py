"""The model edge. One place that knows about providers, retries, pacing and
JSON extraction.

Stages ask for work by name — "classify" or "synthesize" — and the config decides
which provider and model answers. Nothing upstream of here knows which model
wrote the digest.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Protocol

log = logging.getLogger("digest.llm")

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> Any:
    """Parse a model response as JSON, tolerating a markdown fence around it.

    Still needed even when the provider is asked for JSON directly: a refusal or
    a truncated response arrives as prose, and the fenced block is the common
    failure mode when it does not.
    """
    stripped = _FENCE.sub("", text).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = stripped.find(opener), stripped.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"response was not JSON: {text[:300]!r}")


# --------------------------------------------------------------------- errors


def status_of(exc: Exception) -> int | None:
    """HTTP status behind an SDK exception, or None.

    Both SDKs expose `.status_code`; branching on that rather than on exception
    classes keeps this working when a provider reshuffles its class hierarchy,
    which google-genai has already done once — the classes its interactions API
    raises are not the ones exported from `google.genai.errors`.
    """
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


_RETRY_IN = re.compile(r"retry in ([\d.]+)\s*s", re.IGNORECASE)


def retry_after_of(exc: Exception) -> float | None:
    """The server's own instruction about when to come back, if it gave one.

    Checked in the header first, then in the message body: Gemini returns the
    delay only in the error text ("Please retry in 46.16s"), so a header-only
    reading would fall back to a guessed backoff on every free-tier 429.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    try:
        value = headers.get("retry-after")
    except AttributeError:
        value = None
    if value:
        try:
            return float(value)
        except (TypeError, ValueError):
            pass

    match = _RETRY_IN.search(str(exc))
    return float(match.group(1)) if match else None


def is_credentials_problem(exc: Exception) -> bool:
    """Distinguish a bad or missing key from an ordinary rejected request.

    Anthropic answers 401 and Gemini answers 400 with API_KEY_INVALID, and only
    the Gemini client raises at construction time — so without this the
    Anthropic path reports a missing key as "rejected the request", which is the
    first error a new owner would see and the least helpful one.
    """
    status = status_of(exc)
    if status in (401, 403):
        return True
    return status == 400 and "API_KEY_INVALID" in str(exc)


def is_rate_limited(exc: Exception) -> bool:
    return status_of(exc) == 429


def is_transient(exc: Exception) -> bool:
    status = status_of(exc)
    if status is not None:
        return status == 429 or status >= 500
    # Connection and timeout errors carry no status.
    name = type(exc).__name__
    return "Connection" in name or "Timeout" in name


# ------------------------------------------------------------------ backends


class Backend(Protocol):
    name: str

    def generate(
        self, *, model: str, prompt: str, system: str | None, max_tokens: int, opts: dict
    ) -> str: ...


def _json_schema(opts: dict) -> dict | None:
    schema = opts.get("schema")
    return schema if isinstance(schema, dict) else None


class AnthropicBackend:
    """Claude via the Anthropic API. See digest.credentials for where the key
    is looked up."""

    name = "anthropic"

    def __init__(self, client=None, api_key: str | None = None):
        if client is None:
            import anthropic  # noqa: PLC0415

            client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self._client = client

    def generate(self, *, model, prompt, system, max_tokens, opts) -> str:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        # Haiku 4.5 accepts sampling parameters; Sonnet 5 and the rest of the
        # current top tier reject them outright.
        if opts.get("temperature") is not None:
            kwargs["temperature"] = opts["temperature"]

        response = self._client.messages.create(**kwargs)
        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMError(f"{model} declined the request")
        return "".join(b.text for b in response.content if b.type == "text")


# Thinking tokens are drawn from the same allowance as the answer, so a request
# that asks for exactly as many tokens as the answer needs comes back truncated
# mid-sentence with no error. Measured: a 40-character JSON reply spent 122
# thinking tokens at `medium`. The backend adds this headroom on top of whatever
# the caller asked for.
THINKING_HEADROOM = {"minimal": 512, "low": 1024, "medium": 4096, "high": 8192}


class GeminiBackend:
    """Gemini via the Google AI API. Needs GEMINI_API_KEY.

    This surface has no `temperature` — `seed` is the reproducibility lever, and
    `thinking_level` is the depth-versus-tokens one. Asking for
    `application/json` up front removes most of the fenced-JSON failures.
    """

    name = "gemini"

    def __init__(self, client=None, api_key: str | None = None):
        if client is None:
            from google import genai  # noqa: PLC0415

            client = genai.Client(api_key=api_key) if api_key else genai.Client()
        self._client = client

    def generate(self, *, model, prompt, system, max_tokens, opts) -> str:
        level = opts.get("thinking_level")
        budget = max_tokens + THINKING_HEADROOM.get(level, 0)
        generation_config: dict[str, Any] = {"max_output_tokens": budget}
        if opts.get("seed") is not None:
            generation_config["seed"] = opts["seed"]
        if level:
            generation_config["thinking_level"] = level

        kwargs: dict[str, Any] = {
            "model": model,
            "input": prompt,
            "generation_config": generation_config,
        }
        if system:
            kwargs["system_instruction"] = system
        if opts.get("json", True):
            # A schema is accepted by this API too, but that path has not been
            # exercised against the live endpoint, and asking for the JSON mime
            # type alone has been enough — `extract_json` covers the rest.
            kwargs["response_format"] = {"type": "text", "mime_type": "application/json"}

        interaction = self._client.interactions.create(**kwargs)
        text = getattr(interaction, "output_text", None)
        if not text:
            raise LLMError(f"{model} returned no text (finish reason may be a block)")
        return text


class OllamaBackend:
    """A model served by a local Ollama daemon. No key, no metering, no network.

    A JSON schema is not optional here the way it is elsewhere. Asked only for
    `format: "json"`, a local model will answer a twenty-five-item batch with one
    object and stop; given the array schema it returns all twenty-five. The
    schema comes from the calling stage, which is the only thing that knows the
    shape it asked for.
    """

    name = "ollama"

    def __init__(self, host: str = "http://localhost:11434", num_ctx: int = 32768, timeout: int = 900):
        self.host = host.rstrip("/")
        self.num_ctx = num_ctx
        self.timeout = timeout

    def generate(self, *, model, prompt, system, max_tokens, opts) -> str:
        import urllib.error  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        options: dict[str, Any] = {
            "num_ctx": opts.get("num_ctx") or self.num_ctx,
            "num_predict": max_tokens,
        }
        # Local models take sampling parameters, so the classify stage really can
        # be pinned rather than merely seeded.
        if opts.get("temperature") is not None:
            options["temperature"] = opts["temperature"]
        if opts.get("seed") is not None:
            options["seed"] = opts["seed"]

        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": _json_schema(opts) or "json",
            "options": options,
        }
        # Reasoning models emit a thinking block before the answer, which a
        # schema-constrained request has nowhere to put — the result is an empty
        # or truncated response rather than an error. Sent only when configured,
        # because a model without the capability rejects the field.
        if opts.get("think") is not None:
            body["think"] = opts["think"]
        if system:
            body["system"] = system

        request = urllib.request.Request(
            f"{self.host}/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.URLError as exc:
            raise LLMError(
                f"could not reach Ollama at {self.host} — is `ollama serve` running? ({exc})"
            ) from exc

        if payload.get("error"):
            raise LLMError(f"ollama: {payload['error']}")
        text = payload.get("response", "")
        if not text.strip():
            raise LLMError(f"{model} returned nothing (done_reason={payload.get('done_reason')})")
        return text


BACKENDS = {"anthropic": AnthropicBackend, "gemini": GeminiBackend, "ollama": OllamaBackend}


def make_backend(provider: str, cfg=None) -> Backend:
    if provider not in BACKENDS:
        raise LLMError(f"unknown provider {provider!r}; expected one of {sorted(BACKENDS)}")

    kwargs: dict[str, Any] = {}
    if provider == "ollama":
        if cfg is not None:
            kwargs = {"host": cfg.models.ollama_host, "num_ctx": cfg.models.ollama_num_ctx}
    else:
        from .credentials import api_key, describe_sources  # noqa: PLC0415

        key_file = cfg.credentials.key_file(provider) if cfg is not None else None
        config_path = cfg.config_path if cfg is not None else None
        key = api_key(provider, key_file, config_path)
        if not key:
            raise LLMError(describe_sources(provider, key_file, config_path))
        kwargs = {"api_key": key}

    try:
        return BACKENDS[provider](**kwargs)
    except Exception as exc:
        raise LLMError(f"could not start the {provider} client: {exc}") from exc


# -------------------------------------------------------------------- client


class Client:
    """Retries, pacing, and the stage-to-model mapping.

    Each stage gets its own backend, so the filtering can run on a local model
    while the writing runs on a hosted one. Free-tier accounts are limited by
    requests per minute and cannot see their own limit from here, so hosted calls
    are spaced by `cfg.models.min_interval_seconds` and a 429 is obeyed rather
    than hammered. A local backend is not metered and is never paced.
    """

    def __init__(self, cfg, backend: Backend | None = None):
        self.cfg = cfg
        self._override = backend
        self._backends: dict[str, Backend] = {}
        self._last_call: dict[str, float] = {}
        # Backends whose quota is spent for this run. Gemini says "retry in 55s"
        # for a per-day budget exactly as it does for a per-minute one, so the
        # only way to tell them apart is to honour the wait and see whether the
        # next call is still refused. Once that happens the remaining calls
        # cannot succeed either, and attempting them costs an hour of backoff
        # in a job that runs unattended. Recorded per backend, never retried.
        self._spent: dict[str, str] = {}

    def backend_for(self, stage: str) -> Backend:
        """One backend per provider, built on first use so an unused provider
        never has to have a key."""
        if self._override is not None:
            return self._override
        provider = self.cfg.models.provider_for(stage)
        if provider not in self._backends:
            self._backends[provider] = make_backend(provider, self.cfg)
        return self._backends[provider]

    # -- stage wiring

    def model_for(self, stage: str) -> str:
        models = self.cfg.models
        return models.classify if stage == "classify" else models.synthesize

    def opts_for(self, stage: str) -> dict:
        models = self.cfg.models
        if stage == "classify":
            return {
                "temperature": models.classify_temperature,
                "seed": models.seed,
                "thinking_level": models.classify_thinking,
                "think": models.ollama_think,
            }
        return {
            "temperature": models.synthesize_temperature,
            "seed": models.seed,
            "thinking_level": models.synthesize_thinking,
            "think": models.ollama_think,
        }

    # -- pacing

    def _wait_turn(self, backend: Backend) -> None:
        interval = self.cfg.models.min_interval_seconds
        if interval <= 0 or backend.name == "ollama":
            return
        elapsed = time.monotonic() - self._last_call.get(backend.name, 0.0)
        if elapsed < interval:
            time.sleep(interval - elapsed)

    def _backoff(self, attempt: int, exc: Exception) -> float:
        hinted = retry_after_of(exc)
        if hinted is not None:
            return min(hinted, self.cfg.models.max_backoff_seconds)
        schedule = self.cfg.models.backoff_seconds
        return schedule[min(attempt, len(schedule) - 1)]

    # -- calls

    def complete(
        self,
        *,
        stage: str,
        prompt: str,
        max_tokens: int,
        system: str | None = None,
        schema: dict | None = None,
    ) -> str:
        model = self.model_for(stage)
        backend = self.backend_for(stage)
        opts = self.opts_for(stage)
        if schema:
            opts = {**opts, "schema": schema}
        attempts = self.cfg.models.max_attempts
        last: Exception | None = None
        spent = self._spent.get(backend.name)
        if spent is not None:
            raise LLMError(spent)
        # Set to the delay once we have slept one the server itself asked for.
        honoured_delay: float | None = None

        for attempt in range(attempts):
            self._wait_turn(backend)
            try:
                text = backend.generate(
                    model=model, prompt=prompt, system=system,
                    max_tokens=max_tokens, opts=opts,
                )
            except LLMError:
                raise
            except Exception as exc:
                self._last_call[backend.name] = time.monotonic()
                if is_credentials_problem(exc):
                    raise LLMError(
                        f"the {backend.name} API refused the credentials — "
                        "is the API key set and valid?"
                    ) from exc
                if not is_transient(exc):
                    raise LLMError(f"{model} rejected the request: {exc}") from exc
                last = exc
                if is_rate_limited(exc) and honoured_delay is not None:
                    # We waited exactly as long as the server asked and it is
                    # still refusing, so this is not a per-minute window that
                    # waiting will clear. Stop the whole run's calls to this
                    # backend rather than burning the same wait on every one.
                    self._spent[backend.name] = (
                        f"{backend.name} quota is exhausted for now — waited the "
                        f"{honoured_delay:.0f}s it asked for and the next call was "
                        f"refused again. Remaining {backend.name} calls are being skipped; "
                        "check your limits at https://ai.dev/rate-limit"
                    )
                    log.error("%s", self._spent[backend.name])
                    raise LLMError(self._spent[backend.name]) from exc
                if attempt == attempts - 1:
                    break
                hinted = retry_after_of(exc)
                delay = self._backoff(attempt, exc)
                # Only a hint we followed in full tells us anything; a hint the
                # cap trimmed means we came back early and a 429 proves nothing.
                honoured_delay = delay if (hinted is not None and delay >= hinted) else None
                log.warning(
                    "%s on attempt %d/%d for %s, waiting %.0fs",
                    type(exc).__name__, attempt + 1, attempts, model, delay,
                )
                time.sleep(delay)
                continue

            self._last_call[backend.name] = time.monotonic()
            return text

        raise LLMError(f"{model} failed after {attempts} attempts: {last}")

    def complete_json(self, **kwargs) -> Any:
        """As `complete`, but retries once more when the response is not JSON."""
        text = self.complete(**kwargs)
        try:
            return extract_json(text)
        except LLMError:
            log.warning("malformed JSON, retrying once")
            return extract_json(self.complete(**kwargs))
