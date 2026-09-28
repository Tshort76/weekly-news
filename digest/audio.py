"""Text to speech, through echo. edge-tts online by default, piper offline as the fallback.

Only the part of the .txt above the divider is spoken — the sources appendix is
for the eye. This module decides what is said and where the chapters fall; echo
decides how it is spoken and how the file is built (docs/design/echo-as-library.md).
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import Config
from .emit import Segment, spoken_part, spoken_segments
from .models import Edition

log = logging.getLogger("digest.audio")


class AudioError(RuntimeError):
    pass


def _segments(text: str, edition: Edition | None, fallback_title: str) -> list[Segment]:
    """The edition's segments, provided they are still what the .txt says.

    Someone may have edited the .txt by hand before re-running `speak`. The file
    is what gets spoken, so when the two disagree the chapters are dropped
    rather than placed against prose that is no longer there.
    """
    if edition is not None:
        segments = spoken_segments(edition)
        if "\n\n".join(s.text for s in segments).rstrip() == text.rstrip():
            return segments
        log.warning("the .txt no longer matches the stored edition; writing no chapters")
    return [Segment(fallback_title, text)]


def _piper_voice(model: str) -> str | None:
    """`piper_model` names a piper voice, e.g. en_GB-alan-medium.

    It used to be the path to a model file for the piper command line. A path
    still works as far as naming the voice goes — echo fetches that voice into
    its own cache — but the file itself is not read.
    """
    if not model:
        return None  # echo's default voice
    return Path(model).name.removesuffix(".onnx") if model.endswith(".onnx") else model


def engine_plan(cfg: Config) -> list[tuple[str, str | None]]:
    """(engine, voice) pairs in the order to try them."""
    piper = ("piper", _piper_voice(cfg.tts.piper_model))
    if cfg.tts.offline or cfg.tts.engine == "piper":
        return [piper]
    return [("edge", cfg.tts.voice), piper]


def speak(txt_path: Path, out_path: Path, cfg: Config, edition: Edition | None = None) -> Path:
    """Speak the .txt. Given its edition, the MP3 also gets a chapter per item."""
    from echo import Chapter, EchoError, speak_chapters  # noqa: PLC0415

    text = spoken_part(txt_path.read_text(encoding="utf-8"))
    if not text.strip():
        raise AudioError("nothing to speak")
    title = f"{edition.title} — {edition.week}" if edition else txt_path.stem
    chapters = [Chapter(s.title, s.text) for s in _segments(text, edition, title)]

    failures = []
    for engine, voice in engine_plan(cfg):
        try:
            result = speak_chapters(
                chapters, out_path, title=title, engine=engine, voice=voice,
                chunk_size=cfg.tts.chunk_chars,
            )
        except EchoError as exc:
            log.warning("%s could not make the audio (%s)", engine, exc)
            failures.append(f"{engine}: {exc}")
            continue
        log.info("wrote %s with %s, %d chapters", result.path, engine, len(result.chapters))
        return Path(result.path)
    raise AudioError("; ".join(failures))
