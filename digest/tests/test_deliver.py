from digest.config import Config
from digest.deliver import copy_audio


def test_the_mp3_is_copied_into_the_audio_folder(tmp_path):
    mp3 = tmp_path / "digest-2026-10-05.mp3"
    mp3.write_bytes(b"ID3")
    cfg = Config()
    cfg.run.audio_folder = tmp_path / "sync" / "WeeklyDigest"
    assert copy_audio(mp3, cfg) == cfg.run.audio_folder / mp3.name
    assert (cfg.run.audio_folder / mp3.name).read_bytes() == b"ID3"


def test_no_audio_folder_copies_nothing(tmp_path):
    mp3 = tmp_path / "digest-2026-10-05.mp3"
    mp3.write_bytes(b"ID3")
    assert copy_audio(mp3, Config()) is None


def test_a_failed_copy_is_logged_not_raised(tmp_path):
    cfg = Config()
    cfg.run.audio_folder = tmp_path / "sync"
    assert copy_audio(tmp_path / "missing.mp3", cfg) is None
