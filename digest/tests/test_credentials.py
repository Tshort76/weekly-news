"""Where a key comes from, and what happens when it comes from nowhere."""

from __future__ import annotations

import pytest

from digest.credentials import api_key, describe_sources


@pytest.fixture(autouse=True)
def no_ambient_keys(monkeypatch):
    """The developer's own machine must not decide these tests.

    That means the environment, the Keychain, and — the one that actually bit —
    a real `.env` sitting in the checkout. Without the last of these the suite
    passes until someone puts their key in the repo, then fails with their key
    quoted in the diff.
    """
    from pathlib import Path

    from digest import credentials

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(credentials, "_from_keychain", lambda service: None)
    monkeypatch.setattr(
        credentials,
        "dotenv_paths",
        lambda config_path=None: (
            [Path(config_path).resolve().parent / ".env"] if config_path else []
        ),
    )


def test_the_environment_wins(monkeypatch, tmp_path):
    key_file = tmp_path / "k"
    key_file.write_text("from-file")
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert api_key("gemini", key_file) == "from-env"


def test_a_key_file_is_used_when_the_environment_is_empty(tmp_path):
    key_file = tmp_path / "k"
    key_file.write_text("  from-file\n")
    assert api_key("gemini", key_file) == "from-file"


def test_an_empty_environment_variable_does_not_shadow_the_file(monkeypatch, tmp_path):
    key_file = tmp_path / "k"
    key_file.write_text("from-file")
    monkeypatch.setenv("GEMINI_API_KEY", "   ")
    assert api_key("gemini", key_file) == "from-file"


def test_an_empty_key_file_is_not_a_key(tmp_path):
    key_file = tmp_path / "k"
    key_file.write_text("\n")
    assert api_key("gemini", key_file) is None


def test_the_keychain_is_the_last_resort(monkeypatch, tmp_path):
    monkeypatch.setattr("digest.credentials._from_keychain", lambda service: "from-keychain")
    assert api_key("gemini", tmp_path / "missing") == "from-keychain"


def test_a_world_readable_key_file_warns_but_still_works(tmp_path, caplog):
    key_file = tmp_path / "k"
    key_file.write_text("from-file")
    key_file.chmod(0o644)
    with caplog.at_level("WARNING"):
        assert api_key("gemini", key_file) == "from-file"
    assert "readable by others" in caplog.text


def test_nothing_anywhere_yields_none(tmp_path):
    assert api_key("gemini", tmp_path / "missing") is None


def test_the_failure_message_names_all_three_places_and_the_fix(tmp_path):
    message = describe_sources("gemini", tmp_path / "k")
    assert "$GEMINI_API_KEY" in message
    assert str(tmp_path / "k") in message
    assert "Keychain" in message
    assert "chmod 600" in message


def test_a_missing_key_stops_the_backend_with_that_message(tmp_path, monkeypatch):
    from digest.config import Config, CredentialsCfg
    from digest.llm import LLMError, make_backend

    cfg = Config(credentials=CredentialsCfg(gemini_key_file=tmp_path / "missing"))
    with pytest.raises(LLMError, match="no gemini key found"):
        make_backend("gemini", cfg)


def test_a_local_provider_needs_no_key(tmp_path):
    from digest.config import Config
    from digest.llm import make_backend

    assert make_backend("ollama", Config()).name == "ollama"


# ---------------------------------------------------------------- the doctor


def test_resolve_names_the_place_the_key_actually_came_from(monkeypatch, tmp_path):
    from digest.credentials import resolve

    key_file = tmp_path / "k"
    key_file.write_text("from-file")
    assert resolve("gemini", key_file) == ("from-file", str(key_file))

    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert resolve("gemini", key_file) == ("from-env", "$GEMINI_API_KEY")


def test_a_whitespace_env_var_is_not_reported_as_the_source(monkeypatch, tmp_path):
    """The bug this guards: doctor saying 'environment' while the run reads the file."""
    from digest.credentials import resolve

    key_file = tmp_path / "k"
    key_file.write_text("from-file")
    monkeypatch.setenv("GEMINI_API_KEY", "   ")
    key, source = resolve("gemini", key_file)
    assert key == "from-file"
    assert source == str(key_file)


def test_doctor_reports_a_missing_key_and_exits_nonzero(tmp_path, capsys):
    from digest.__main__ import doctor
    from digest.config import Config, CredentialsCfg, ModelsCfg

    cfg = Config(
        models=ModelsCfg(classify_provider="ollama", synthesize_provider="gemini"),
        credentials=CredentialsCfg(gemini_key_file=tmp_path / "missing"),
    )
    assert doctor(cfg) == 1
    assert "NO KEY FOUND" in capsys.readouterr().out


def test_doctor_never_prints_the_whole_key(tmp_path, monkeypatch, capsys):
    from digest.config import Config, CredentialsCfg, ModelsCfg

    key_file = tmp_path / "k"
    key_file.write_text("sk-secret-abcd1234")
    key_file.chmod(0o600)
    cfg = Config(
        models=ModelsCfg(classify_provider="ollama", synthesize_provider="gemini"),
        credentials=CredentialsCfg(gemini_key_file=key_file),
    )
    monkeypatch.setattr(
        "digest.llm.make_backend",
        lambda provider, cfg=None: type("B", (), {"name": provider})(),
    )
    from digest.__main__ import doctor

    doctor(cfg)
    out = capsys.readouterr().out
    assert "1234" in out
    assert "sk-secret-abcd1234" not in out
    assert "secret" not in out


# ------------------------------------------------------------------- .env


def test_a_dotenv_beside_the_config_supplies_the_key(tmp_path):
    from digest.credentials import resolve

    (tmp_path / "digest.toml").write_text("")
    dotenv = tmp_path / ".env"
    dotenv.write_text("GEMINI_API_KEY=from-dotenv\n")
    dotenv.chmod(0o600)
    key, source = resolve("gemini", tmp_path / "missing", tmp_path / "digest.toml")
    assert key == "from-dotenv"
    assert str(dotenv) in source


def test_the_real_environment_beats_a_dotenv(monkeypatch, tmp_path):
    """The dotenv convention, and what makes a one-off override work."""
    from digest.credentials import resolve

    (tmp_path / ".env").write_text("GEMINI_API_KEY=from-dotenv\n")
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    key, source = resolve("gemini", None, tmp_path / "digest.toml")
    assert key == "from-env"
    assert source == "$GEMINI_API_KEY"


def test_a_dotenv_beats_the_key_file(tmp_path):
    from digest.credentials import resolve

    (tmp_path / ".env").write_text("GEMINI_API_KEY=from-dotenv\n")
    key_file = tmp_path / "k"
    key_file.write_text("from-key-file")
    key, _ = resolve("gemini", key_file, tmp_path / "digest.toml")
    assert key == "from-dotenv"


def test_a_dotenv_without_our_variable_falls_through(tmp_path):
    from digest.credentials import resolve

    (tmp_path / ".env").write_text("SOMETHING_ELSE=x\nGEMINI_API_KEY=\n")
    key_file = tmp_path / "k"
    key_file.write_text("from-key-file")
    key, _ = resolve("gemini", key_file, tmp_path / "digest.toml")
    assert key == "from-key-file"


def test_a_world_readable_dotenv_warns(tmp_path, caplog):
    from digest.credentials import resolve

    dotenv = tmp_path / ".env"
    dotenv.write_text("GEMINI_API_KEY=from-dotenv\n")
    dotenv.chmod(0o644)
    with caplog.at_level("WARNING"):
        resolve("gemini", None, tmp_path / "digest.toml")
    assert "readable by others" in caplog.text


def test_dotenv_paths_prefers_the_config_directory_then_the_repo_then_the_cwd(monkeypatch):
    """Checked directly, since the fixture above stubs it out everywhere else."""
    from pathlib import Path

    from digest import credentials

    monkeypatch.undo()
    paths = credentials.dotenv_paths(Path("/somewhere/digest.toml"))
    assert paths[0] == Path("/somewhere/.env")
    assert len(paths) == len(set(paths)), "duplicates should be collapsed"
    assert all(p.name == ".env" for p in paths)


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
    assert parsed["GEMINI_API_KEY"] == "plain"
    assert parsed["ANTHROPIC_API_KEY"] == "double"
    assert parsed["OTHER"] == "single"
    assert parsed["SPACED"] == "spaced"
    assert "not-a-pair" not in parsed


def test_the_failure_message_names_the_dotenv_first(tmp_path):
    from digest.credentials import describe_sources

    message = describe_sources("gemini", tmp_path / "k", tmp_path / "digest.toml")
    assert str(tmp_path / ".env") in message
    assert "chmod 600" in message
