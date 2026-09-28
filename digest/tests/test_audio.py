"""What is spoken, in what chapters, by which engine. The speaking itself is echo's."""

from digest.audio import _piper_voice, _segments, engine_plan
from digest.config import Config
from digest.emit import DIVIDER, render_txt, spoken_part
from digest.tests.test_emit import _edition


def test_audio_is_made_only_from_the_spoken_part():
    text = f"Spoken prose.\n\n{DIVIDER}\nSources\n\n1. a — b — https://e.com/1\n"
    assert "https://" not in spoken_part(text)


def test_segments_are_used_only_while_the_txt_still_matches():
    edition = _edition()
    text = spoken_part(render_txt(edition))
    titles = [s.title for s in _segments(text, edition, "fallback")]
    assert titles[0] == "Opening" and titles[1].startswith("East Asia: ")
    assert [s.title for s in _segments(text + "\nEdited.", edition, "fallback")] == ["fallback"]


def test_edge_first_then_piper_unless_offline():
    cfg = Config()
    cfg.tts.voice = "en-GB-RyanNeural"
    assert engine_plan(cfg) == [("edge", "en-GB-RyanNeural"), ("piper", None)]
    cfg.tts.offline = True
    assert engine_plan(cfg) == [("piper", None)]


def test_an_old_piper_model_path_still_names_its_voice():
    assert _piper_voice("/models/en_GB-alan-medium.onnx") == "en_GB-alan-medium"
    assert _piper_voice("en_US-lessac-high") == "en_US-lessac-high"
    assert _piper_voice("") is None
