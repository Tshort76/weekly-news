"""Text to speech, through echo and edge-tts. There is no fallback voice: piper's
was bad enough that three editions read by it went unlistened, so a failure
means no mp3 and a desktop notification instead.

Only the part of the .txt above the divider is spoken — the sources appendix is
for the eye. This module decides what is said and where the chapters fall; echo
decides how it is spoken and how the file is built (docs/design/echo-as-library.md).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from .config import Config
from .emit import Segment, audio_title, spoken_part, spoken_segments
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


def _week_of(txt_path: Path) -> str:
    """The ISO week a `digest-<date>.txt` covers, for a .txt spoken with no
    stored edition; a name that is not a week stem is returned as it is."""
    try:
        y, w, _ = date.fromisoformat(txt_path.stem.removeprefix("digest-")).isocalendar()
    except ValueError:
        return txt_path.stem
    return f"{y}-W{w:02d}"


def speak(txt_path: Path, out_path: Path, cfg: Config, edition: Edition | None = None) -> Path:
    """Speak the .txt. Given its edition, the MP3 also gets a chapter per item."""
    from echo import Chapter, EchoError, speak_chapters  # noqa: PLC0415

    text = spoken_part(txt_path.read_text(encoding="utf-8"))
    if not text.strip():
        raise AudioError("nothing to speak")
    title = audio_title(edition.week if edition else _week_of(txt_path))
    chapters = [Chapter(s.title, s.text) for s in _segments(text, edition, title)]

    try:
        result = speak_chapters(
            chapters, out_path, title=title, engine="edge", voice=cfg.tts.voice,
            chunk_size=cfg.tts.chunk_chars,
        )
    except EchoError as exc:
        raise AudioError(str(exc)) from exc
    log.info("wrote %s, %d chapters", result.path, len(result.chapters))
    return Path(result.path)
