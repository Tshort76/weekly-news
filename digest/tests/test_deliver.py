import pytest

from digest import __main__ as cli
from digest.config import Config
from digest.deliver import copy_to_sync


def test_the_mp3_and_html_are_copied_into_the_sync_folder(tmp_path):
    mp3 = tmp_path / "digest-2026-10-05.mp3"
    mp3.write_bytes(b"ID3")
    html = tmp_path / "digest-2026-10-05.html"
    html.write_text("<html>")
    txt = tmp_path / "digest-2026-10-05.txt"
    txt.write_text("spoken")
    cfg = Config()
    cfg.run.sync_folder = tmp_path / "sync" / "WeeklyDigest"
    assert copy_to_sync([mp3, html, txt], cfg) == [
        cfg.run.sync_folder / mp3.name, cfg.run.sync_folder / html.name,
    ]
    assert (cfg.run.sync_folder / mp3.name).read_bytes() == b"ID3"
    assert not (cfg.run.sync_folder / txt.name).exists()


def test_no_sync_folder_copies_nothing(tmp_path):
    mp3 = tmp_path / "digest-2026-10-05.mp3"
    mp3.write_bytes(b"ID3")
    assert copy_to_sync([mp3], Config()) == []


def test_a_failed_copy_is_logged_and_the_rest_still_copy(tmp_path, caplog):
    html = tmp_path / "digest-2026-10-05.html"
    html.write_text("<html>")
    cfg = Config()
    cfg.run.sync_folder = tmp_path / "sync"
    assert copy_to_sync([tmp_path / "missing.mp3", html], cfg) == [cfg.run.sync_folder / html.name]
    assert "could not copy missing.mp3" in caplog.text


class Ran(Exception):
    pass


@pytest.mark.parametrize("where, extra, skips", [
    ("output", ["--scheduled"], True),
    # On 2026-10-30 the mp3 had been moved into the sync folder, and a
    # scheduled run that looked only in the output folder overwrote the week.
    ("sync", ["--scheduled"], True),
    (None, ["--scheduled"], False),
    ("sync", [], False),  # a hand run always runs
])
def test_a_scheduled_run_skips_only_a_week_whose_mp3_exists(tmp_path, monkeypatch, where, extra, skips):
    """A week run by hand already has its mp3; the timer must not redo it."""
    cfg = Config(state_dir=tmp_path)
    cfg.run.output_dir = tmp_path / "output"
    cfg.run.sync_folder = tmp_path / "sync"
    if where:
        folder = cfg.run.output_dir if where == "output" else cfg.run.sync_folder
        folder.mkdir()
        (folder / "digest-2026-10-30.mp3").write_bytes(b"ID3")
    monkeypatch.setattr(cli.config_module, "load", lambda: cfg)

    def ran(*args, **kwargs):
        raise Ran

    monkeypatch.setattr(cli.pipeline, "run", ran)
    monkeypatch.setattr("digest.schedule.notify", lambda *a, **k: True)
    argv = ["run", *extra, "--week", "2026-W44"]
    if skips:
        assert cli.main(argv) == 0
    else:
        with pytest.raises(Ran):
            cli.main(argv)
