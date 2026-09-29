"""Where a key comes from, and what happens when it comes from nowhere."""

from __future__ import annotations

import pytest

from digest import credentials
from digest.credentials import resolve


@pytest.fixture(autouse=True)
def dotenv(monkeypatch, tmp_path):
    """The developer's own environment and real `.env` must not decide these tests."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    path = tmp_path / ".env"
    monkeypatch.setattr(credentials, "DOTENV", path)
    monkeypatch.setattr(credentials.resolve, "__defaults__", (path,))
    monkeypatch.setattr(credentials.describe_sources, "__defaults__", (path,))
    return path


@pytest.mark.parametrize(
    "env, file, expected",
    [
        ("from-env", "GEMINI_API_KEY=from-file\n", ("from-env", "$GEMINI_API_KEY")),
        ("   ", "GEMINI_API_KEY=from-file\n", ("from-file", "DOTENV")),
        (None, "OTHER=x\n", (None, "nowhere")),
        (None, None, (None, "nowhere")),
    ],
)
def test_the_environment_beats_the_dotenv(monkeypatch, dotenv, env, file, expected):
    if env is not None:
        monkeypatch.setenv("GEMINI_API_KEY", env)
    if file is not None:
        dotenv.write_text(file)
        dotenv.chmod(0o600)
    key, source = expected
    source = f"{dotenv} (GEMINI_API_KEY)" if source == "DOTENV" else source
    assert resolve("gemini") == (key, source)


def test_a_world_readable_dotenv_warns_but_still_works(dotenv, caplog):
    dotenv.write_text("GEMINI_API_KEY=k\n")
    dotenv.chmod(0o644)
    assert resolve("gemini")[0] == "k"
    assert "chmod 600" in caplog.text


def test_a_missing_key_stops_the_backend_and_names_the_dotenv(dotenv):
    from digest.config import Config
    from digest.llm import LLMError, make_backend

    with pytest.raises(LLMError, match="no gemini key found") as caught:
        make_backend("gemini", Config())
    assert str(dotenv) in str(caught.value)


def test_a_local_provider_needs_no_key():
    from digest.config import Config
    from digest.llm import make_backend

    assert make_backend("ollama", Config()).name == "ollama"


def test_doctor_reports_a_missing_key_and_exits_nonzero(capsys):
    from digest.__main__ import doctor
    from digest.config import Config, ModelsCfg

    cfg = Config(models=ModelsCfg(classify_provider="ollama", synthesize_provider="gemini"))
    assert doctor(cfg) == 1
    assert "NO KEY FOUND" in capsys.readouterr().out


def test_doctor_never_prints_the_whole_key(monkeypatch, capsys):
    from digest.__main__ import doctor
    from digest.config import Config, ModelsCfg

    monkeypatch.setenv("GEMINI_API_KEY", "sk-secret-abcd1234")
    monkeypatch.setattr(
        "digest.llm.make_backend",
        lambda provider, cfg=None: type("B", (), {"name": provider})(),
    )
    doctor(Config(models=ModelsCfg(classify_provider="ollama", synthesize_provider="gemini")))
    out = capsys.readouterr().out
    assert "1234" in out and "secret" not in out


def test_the_parser_handles_the_shapes_people_actually_write():
    from digest.credentials import parse_dotenv

    parsed = parse_dotenv(
        "# comment\n"
        "\n"
        "GEMINI_API_KEY=plain\n"
        'export ANTHROPIC_API_KEY="double"\n'
        "OTHER='single'\n"
        "SPACED = spaced \n"
        "not-a-pair\n"
    )
    assert parsed == {"GEMINI_API_KEY": "plain", "ANTHROPIC_API_KEY": "double",
                      "OTHER": "single", "SPACED": "spaced"}
