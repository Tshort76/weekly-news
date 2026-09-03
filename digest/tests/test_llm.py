"""The provider edge: which model answers, how a request is shaped, and what
happens when the free tier says no."""

from __future__ import annotations

import pytest

from digest.config import Config, ModelsCfg
from digest.llm import (
    AnthropicBackend, Client, GeminiBackend, LLMError, extract_json,
    is_credentials_problem, is_transient, make_backend, retry_after_of, status_of,
)


# ------------------------------------------------------------ json extraction


def test_plain_json_parses():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_a_fenced_block_parses():
    assert extract_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]


def test_json_with_a_sentence_around_it_parses():
    assert extract_json('Here you go:\n[{"a": 1}]\nHope that helps.') == [{"a": 1}]


def test_prose_raises():
    with pytest.raises(LLMError):
        extract_json("I am afraid I cannot do that.")


# -------------------------------------------------------------- error reading


class FakeResponse:
    def __init__(self, headers: dict):
        self.headers = headers


class FakeStatusError(Exception):
    def __init__(self, status_code: int, retry_after: str | None = None):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.response = FakeResponse({"retry-after": retry_after} if retry_after else {})


def test_status_is_read_off_the_exception():
    assert status_of(FakeStatusError(429)) == 429
    assert status_of(ValueError("nope")) is None


def test_retry_after_is_read_when_the_server_sends_one():
    assert retry_after_of(FakeStatusError(429, "37")) == 37.0
    assert retry_after_of(FakeStatusError(429)) is None


def test_rate_limits_and_server_errors_are_transient_but_bad_requests_are_not():
    assert is_transient(FakeStatusError(429))
    assert is_transient(FakeStatusError(503))
    assert not is_transient(FakeStatusError(400))
    assert not is_transient(FakeStatusError(401))


def test_a_connection_error_is_transient_even_without_a_status():
    class APIConnectionError(Exception):
        pass

    assert is_transient(APIConnectionError())


def test_a_missing_or_bad_key_is_recognised_on_both_providers():
    assert is_credentials_problem(FakeStatusError(401))          # Anthropic
    assert is_credentials_problem(FakeStatusError(403))
    bad_key = FakeStatusError(400)
    bad_key.args = ("Error code: 400 - API_KEY_INVALID",)        # Gemini
    assert is_credentials_problem(bad_key)
    assert not is_credentials_problem(FakeStatusError(400))
    assert not is_credentials_problem(FakeStatusError(429))


# ------------------------------------------------------------------- backends


class RecordingGenaiClient:
    """Stands in for google.genai.Client."""

    def __init__(self, text="{}"):
        self.text = text
        self.calls: list[dict] = []
        self.interactions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("Interaction", (), {"output_text": self.text})()


def test_gemini_sends_seed_and_thinking_level_but_never_temperature():
    genai = RecordingGenaiClient()
    GeminiBackend(genai).generate(
        model="gemini-3.8-flash", prompt="p", system="s", max_tokens=99,
        opts={"seed": 7, "thinking_level": "low", "temperature": 0.0},
    )
    call = genai.calls[0]
    assert call["generation_config"] == {
        # 99 for the answer plus headroom, because thinking is drawn from the
        # same allowance — see THINKING_HEADROOM.
        "max_output_tokens": 99 + 1024, "seed": 7, "thinking_level": "low",
    }
    assert "temperature" not in str(call)
    assert call["system_instruction"] == "s"
    assert call["input"] == "p"


def test_gemini_asks_for_json_by_default():
    genai = RecordingGenaiClient()
    GeminiBackend(genai).generate(
        model="m", prompt="p", system=None, max_tokens=10, opts={}
    )
    assert genai.calls[0]["response_format"] == {
        "type": "text", "mime_type": "application/json"
    }


def test_gemini_raises_when_the_response_carries_no_text():
    genai = RecordingGenaiClient(text="")
    with pytest.raises(LLMError):
        GeminiBackend(genai).generate(
            model="m", prompt="p", system=None, max_tokens=10, opts={}
        )


class RecordingAnthropicClient:
    def __init__(self):
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        block = type("Block", (), {"type": "text", "text": "ok"})()
        return type("Msg", (), {"content": [block], "stop_reason": "end_turn"})()


def test_anthropic_sends_temperature_only_when_one_is_configured():
    client = RecordingAnthropicClient()
    backend = AnthropicBackend(client)
    backend.generate(model="m", prompt="p", system=None, max_tokens=10, opts={"temperature": 0.0})
    backend.generate(model="m", prompt="p", system=None, max_tokens=10, opts={"temperature": None})
    assert client.calls[0]["temperature"] == 0.0
    assert "temperature" not in client.calls[1]


def test_an_unknown_provider_is_named_in_the_error():
    with pytest.raises(LLMError, match="unknown provider"):
        make_backend("altavista")


# --------------------------------------------------------------------- client


class ScriptedBackend:
    name = "scripted"

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def fast(monkeypatch):
    """Record sleeps instead of taking them."""
    slept: list[float] = []
    monkeypatch.setattr("digest.llm.time.sleep", slept.append)
    return slept


def _cfg(**kwargs) -> Config:
    base = dict(min_interval_seconds=0.0, max_attempts=3, backoff_seconds=(10.0, 20.0))
    base.update(kwargs)
    return Config(models=ModelsCfg(**base))


def test_the_stage_decides_which_model_is_asked():
    cfg = _cfg(classify="flash", synthesize="pro")
    backend = ScriptedBackend("a", "b")
    client = Client(cfg, backend)
    client.complete(stage="classify", prompt="p", max_tokens=10)
    client.complete(stage="synthesize", prompt="p", max_tokens=10)
    assert [c["model"] for c in backend.calls] == ["flash", "pro"]


def test_the_stage_decides_the_thinking_level():
    cfg = _cfg(classify_thinking="low", synthesize_thinking="high")
    backend = ScriptedBackend("a", "b")
    client = Client(cfg, backend)
    client.complete(stage="classify", prompt="p", max_tokens=10)
    client.complete(stage="synthesize", prompt="p", max_tokens=10)
    assert [c["opts"]["thinking_level"] for c in backend.calls] == ["low", "high"]


def test_a_rate_limit_is_retried(fast):
    backend = ScriptedBackend(FakeStatusError(429), "ok")
    assert Client(_cfg(), backend).complete(stage="classify", prompt="p", max_tokens=10) == "ok"
    assert len(backend.calls) == 2
    assert fast == [10.0]


def test_the_server_retry_after_hint_beats_the_backoff_schedule(fast):
    backend = ScriptedBackend(FakeStatusError(429, retry_after="37"), "ok")
    Client(_cfg(), backend).complete(stage="classify", prompt="p", max_tokens=10)
    assert fast == [37.0]


def test_a_retry_after_longer_than_the_cap_is_clamped(fast):
    backend = ScriptedBackend(FakeStatusError(429, retry_after="9999"), "ok")
    cfg = _cfg(max_backoff_seconds=120.0)
    Client(cfg, backend).complete(stage="classify", prompt="p", max_tokens=10)
    assert fast == [120.0]


def test_a_bad_request_is_not_retried(fast):
    backend = ScriptedBackend(FakeStatusError(400))
    with pytest.raises(LLMError, match="rejected the request"):
        Client(_cfg(), backend).complete(stage="classify", prompt="p", max_tokens=10)
    assert len(backend.calls) == 1
    assert fast == []


def test_a_credentials_failure_says_so_rather_than_blaming_the_request(fast):
    backend = ScriptedBackend(FakeStatusError(401))
    with pytest.raises(LLMError, match="refused the credentials"):
        Client(_cfg(), backend).complete(stage="classify", prompt="p", max_tokens=10)
    assert len(backend.calls) == 1


def test_giving_up_names_the_model_and_the_last_error(fast):
    backend = ScriptedBackend(*[FakeStatusError(429)] * 3)
    with pytest.raises(LLMError, match="failed after 3 attempts"):
        Client(_cfg(classify="flash"), backend).complete(
            stage="classify", prompt="p", max_tokens=10
        )
    assert len(backend.calls) == 3


def test_calls_are_paced_by_the_configured_interval(monkeypatch):
    slept: list[float] = []
    now = [1000.0]
    monkeypatch.setattr("digest.llm.time.monotonic", lambda: now[0])

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("digest.llm.time.sleep", sleep)

    backend = ScriptedBackend("a", "b", "c")
    client = Client(_cfg(min_interval_seconds=4.0), backend)
    for _ in range(3):
        client.complete(stage="classify", prompt="p", max_tokens=10)
    # The first call goes straight out; each one after it waits out the
    # remainder of the interval since the previous call returned.
    assert slept == [4.0, 4.0]


def test_malformed_json_is_retried_once_then_gives_up(fast):
    backend = ScriptedBackend("not json", '{"a": 1}')
    assert Client(_cfg(), backend).complete_json(
        stage="classify", prompt="p", max_tokens=10
    ) == {"a": 1}

    backend = ScriptedBackend("not json", "still not json")
    with pytest.raises(LLMError):
        Client(_cfg(), backend).complete_json(stage="classify", prompt="p", max_tokens=10)


# -------------------------------------------------------------------- ollama


class FakeUrlopen:
    """Captures the request body and replays a canned Ollama response."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.requests: list[dict] = []

    def __call__(self, request, timeout=None):
        import json as _json

        self.requests.append(_json.loads(request.data.decode()))
        body = _json.dumps(self.payload).encode()

        class Ctx:
            def __enter__(self_inner):
                import io

                return io.BytesIO(body)

            def __exit__(self_inner, *exc):
                return False

        return Ctx()


@pytest.fixture
def ollama(monkeypatch):
    def install(payload):
        fake = FakeUrlopen(payload)
        monkeypatch.setattr("urllib.request.urlopen", fake)
        return fake

    return install


def test_ollama_sends_the_schema_as_the_format(ollama):
    from digest.llm import OllamaBackend

    fake = ollama({"response": "[]"})
    schema = {"type": "array", "minItems": 3}
    OllamaBackend().generate(
        model="qwen3:30b", prompt="p", system=None, max_tokens=500,
        opts={"schema": schema, "temperature": 0.0, "seed": 7},
    )
    sent = fake.requests[0]
    assert sent["format"] == schema
    assert sent["options"]["temperature"] == 0.0
    assert sent["options"]["seed"] == 7
    assert sent["options"]["num_predict"] == 500
    assert sent["stream"] is False


def test_ollama_falls_back_to_plain_json_without_a_schema(ollama):
    from digest.llm import OllamaBackend

    fake = ollama({"response": "{}"})
    OllamaBackend().generate(model="m", prompt="p", system=None, max_tokens=10, opts={})
    assert fake.requests[0]["format"] == "json"


def test_ollama_reports_an_error_field_rather_than_returning_it(ollama):
    from digest.llm import OllamaBackend

    ollama({"error": "model not found"})
    with pytest.raises(LLMError, match="model not found"):
        OllamaBackend().generate(model="m", prompt="p", system=None, max_tokens=10, opts={})


def test_ollama_says_which_host_it_could_not_reach(monkeypatch):
    import urllib.error

    from digest.llm import OllamaBackend

    def boom(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(LLMError, match="ollama serve"):
        OllamaBackend(host="http://localhost:9999").generate(
            model="m", prompt="p", system=None, max_tokens=10, opts={}
        )


def test_an_empty_response_is_an_error_not_an_empty_digest(ollama):
    from digest.llm import OllamaBackend

    ollama({"response": "  ", "done_reason": "length"})
    with pytest.raises(LLMError, match="returned nothing"):
        OllamaBackend().generate(model="m", prompt="p", system=None, max_tokens=10, opts={})


def test_the_classify_schema_pins_the_batch_size():
    from digest.classify import batch_schema

    schema = batch_schema(25)
    assert schema["minItems"] == schema["maxItems"] == 25
    assert schema["items"]["properties"]["fit"] == {"type": "integer", "minimum": 0, "maximum": 3}
    assert "null" in schema["items"]["properties"]["mechanism"]["type"]


# ----------------------------------------------------- provider per stage


def test_each_stage_can_use_a_different_provider(monkeypatch):
    import digest.llm as llm

    made: list[str] = []

    def fake_make_backend(provider, cfg=None):
        made.append(provider)
        return type("B", (), {"name": provider, "generate": lambda self, **kw: "{}"})()

    monkeypatch.setattr(llm, "make_backend", fake_make_backend)
    client = Client(_cfg(classify_provider="ollama", synthesize_provider="gemini"))
    assert client.backend_for("classify").name == "ollama"
    assert client.backend_for("synthesize").name == "gemini"
    assert made == ["ollama", "gemini"]


def test_a_stage_without_an_override_falls_back_to_the_default_provider():
    cfg = _cfg(provider="gemini", classify_provider="ollama")
    assert cfg.models.provider_for("classify") == "ollama"
    assert cfg.models.provider_for("synthesize") == "gemini"


def test_a_local_backend_is_never_paced(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("digest.llm.time.sleep", slept.append)
    monkeypatch.setattr("digest.llm.time.monotonic", lambda: 1000.0)

    local = ScriptedBackend("a", "b", "c")
    local.name = "ollama"
    client = Client(_cfg(min_interval_seconds=4.0), local)
    for _ in range(3):
        client.complete(stage="classify", prompt="p", max_tokens=10)
    assert slept == []


def test_pacing_is_tracked_per_provider(monkeypatch):
    """A slow hosted call should not make the next hosted call wait twice."""
    slept: list[float] = []
    now = [1000.0]
    monkeypatch.setattr("digest.llm.time.monotonic", lambda: now[0])

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("digest.llm.time.sleep", sleep)
    hosted = ScriptedBackend("a", "b")
    hosted.name = "gemini"
    client = Client(_cfg(min_interval_seconds=4.0), hosted)
    client.complete(stage="classify", prompt="p", max_tokens=10)
    now[0] += 60  # a long call
    client.complete(stage="synthesize", prompt="p", max_tokens=10)
    assert slept == []  # 60s already elapsed, no need to wait


def test_ollama_omits_the_think_flag_unless_configured(ollama):
    from digest.llm import OllamaBackend

    fake = ollama({"response": "{}"})
    OllamaBackend().generate(model="m", prompt="p", system=None, max_tokens=10, opts={"think": None})
    assert "think" not in fake.requests[0]

    fake = ollama({"response": "{}"})
    OllamaBackend().generate(model="m", prompt="p", system=None, max_tokens=10, opts={"think": False})
    assert fake.requests[0]["think"] is False


def test_gemini_adds_headroom_so_thinking_does_not_eat_the_answer():
    """Thinking tokens come out of max_output_tokens, so a request sized to the
    answer alone comes back truncated with no error."""
    from digest.llm import THINKING_HEADROOM, GeminiBackend

    genai = RecordingGenaiClient(text="{}")
    GeminiBackend(genai).generate(
        model="m", prompt="p", system=None, max_tokens=4000,
        opts={"thinking_level": "medium"},
    )
    assert genai.calls[0]["generation_config"]["max_output_tokens"] == (
        4000 + THINKING_HEADROOM["medium"]
    )


def test_no_headroom_is_added_when_thinking_is_not_set():
    from digest.llm import GeminiBackend

    genai = RecordingGenaiClient(text="{}")
    GeminiBackend(genai).generate(model="m", prompt="p", system=None, max_tokens=500, opts={})
    assert genai.calls[0]["generation_config"]["max_output_tokens"] == 500


def test_the_retry_delay_is_read_from_the_message_when_there_is_no_header():
    """Gemini returns the delay only in the error text."""
    from digest.llm import retry_after_of

    exc = Exception(
        "Error code: 429 - Quota exceeded for metric: "
        "generate_content_free_tier_requests, limit: 5. Please retry in 46.16482456s."
    )
    assert retry_after_of(exc) == 46.16482456


def test_a_header_still_wins_over_the_message():
    from digest.llm import retry_after_of

    exc = FakeStatusError(429, retry_after="9")
    exc.args = ("Please retry in 46.1s",)
    assert retry_after_of(exc) == 9.0
