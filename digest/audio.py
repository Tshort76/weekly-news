"""Text to speech. edge-tts online by default, piper offline as the fallback.

Only the part of the .txt above the divider is spoken — the sources appendix is
for the eye.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import Config
from .emit import Segment, spoken_part, spoken_segments
from .models import Edition

log = logging.getLogger("digest.audio")


class AudioError(RuntimeError):
    pass


def chunk_text(text: str, limit: int) -> list[str]:
    """Split at paragraph boundaries, never mid-sentence."""
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > limit and current:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def _edge_chunk(text: str, voice: str, path: Path) -> None:
    import edge_tts  # noqa: PLC0415

    await edge_tts.Communicate(text, voice).save(str(path))


def _synth_edge(chunks: list[str], cfg: Config, tmp: Path) -> list[Path]:
    async def run() -> list[Path]:
        paths = []
        for n, chunk in enumerate(chunks):
            part = tmp / f"part{n:03d}.mp3"
            await _edge_chunk(chunk, cfg.tts.voice, part)
            paths.append(part)
        return paths

    return asyncio.run(run())


def _synth_piper(chunks: list[str], cfg: Config, tmp: Path) -> list[Path]:
    if not shutil.which("piper"):
        raise AudioError("piper is not on PATH")
    if not cfg.tts.piper_model:
        raise AudioError("tts.piper_model is not set in digest.toml")
    paths = []
    for n, chunk in enumerate(chunks):
        part = tmp / f"part{n:03d}.wav"
        subprocess.run(
            ["piper", "--model", cfg.tts.piper_model, "--output_file", str(part)],
            input=chunk.encode("utf-8"), check=True, capture_output=True,
        )
        paths.append(part)
    return paths


def _strip_id3(data: bytes) -> bytes:
    """Drop a leading ID3v2 tag so the joined file has exactly one, at the front.

    Players tolerate a tag mid-stream, but some show the second one's metadata
    for the whole file, which puts chunk three's title on the finished briefing.
    """
    if not data.startswith(b"ID3") or len(data) < 10:
        return data
    # Syncsafe: seven bits per byte, the top bit always zero.
    size = 0
    for byte in data[6:10]:
        size = (size << 7) | (byte & 0x7F)
    return data[10 + size:]


# Layer III only: that is all edge-tts writes. Index 0 of each is "free", which
# no encoder here produces and the parser skips.
_BITRATES = {
    "mpeg1": (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    "mpeg2": (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_SAMPLE_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def mp3_duration(data: bytes) -> float:
    """Seconds of audio in an MP3, by walking its frame headers.

    Chapter marks need each chunk's length to the millisecond. A Xing header
    frame would count as one frame of silence, 24ms at most, which no chapter
    list can show.
    """
    data = _strip_id3(data)
    seconds = 0.0
    i = 0
    while i + 4 <= len(data):
        b1, b2 = data[i + 1], data[i + 2]
        version, layer = (b1 >> 3) & 3, (b1 >> 1) & 3
        br_index, sr_index = b2 >> 4, (b2 >> 2) & 3
        if (data[i] != 0xFF or b1 & 0xE0 != 0xE0 or version == 1 or layer != 1
                or br_index in (0, 15) or sr_index == 3):
            i += 1
            continue
        mpeg1 = version == 3
        bitrate = _BITRATES["mpeg1" if mpeg1 else "mpeg2"][br_index] * 1000
        rate = _SAMPLE_RATES[version][sr_index]
        samples = 1152 if mpeg1 else 576
        seconds += samples / rate
        i += samples // 8 * bitrate // rate + ((b2 >> 1) & 1)
    return seconds


def _id3_frame(frame_id: str, body: bytes) -> bytes:
    return frame_id.encode("ascii") + len(body).to_bytes(4, "big") + b"\0\0" + body


def _id3_text(frame_id: str, text: str) -> bytes:
    # Encoding 1 is UTF-16 with a byte-order mark, which every ID3v2.3 reader
    # takes and which carries a headline's dashes and accents intact.
    return _id3_frame(frame_id, b"\x01" + text.encode("utf-16"))


def chapter_tag(title: str, chapters: list[tuple[str, int, int]]) -> bytes:
    """An ID3v2.3 tag carrying a title and chapters as (title, start ms, end ms).

    CHAP frames plus one ordered, top-level CTOC is the layout podcast players
    read (Apple Podcasts, Overcast, Pocket Casts, VLC). The table of contents is
    flat on purpose: the ID3 chapter spec allows nesting, but players that honour
    it are rare, and a region prefix on each title does the grouping instead.
    """
    ids = [f"ch{n}".encode("ascii") for n in range(len(chapters))]
    toc = b"toc\0" + b"\x03" + bytes([len(ids)]) + b"".join(i + b"\0" for i in ids)
    frames = [_id3_text("TIT2", title), _id3_frame("CTOC", toc)]
    for chapter_id, (name, start, end) in zip(ids, chapters):
        times = start.to_bytes(4, "big") + end.to_bytes(4, "big") + b"\xff" * 8
        frames.append(_id3_frame("CHAP", chapter_id + b"\0" + times + _id3_text("TIT2", name)))
    payload = b"".join(frames)
    size = len(payload)
    syncsafe = bytes((size >> shift) & 0x7F for shift in (21, 14, 7, 0))
    return b"ID3\x03\x00\x00" + syncsafe + payload


def _concat(parts: list[Path], out_path: Path) -> None:
    """Join the chunks by appending their frames. No ffmpeg, no pydub.

    Every chunk comes from the same synthesiser at the same sample rate and
    bitrate, which is the case where this is safe and the only case that
    happens here. It replaces a pydub dependency that needed ffmpeg, which a
    non-technical user does not have and should not be asked to install for an
    optional MP3.

    The 400ms of silence that used to sit between chunks is gone with it. The
    pause now comes from the text: chunks split at paragraph boundaries, and a
    paragraph break is a pause the voice already makes.
    """
    if parts and parts[0].suffix.lower() == ".wav":
        return _concat_wav(parts, out_path)
    with out_path.open("wb") as out:
        for n, part in enumerate(parts):
            data = part.read_bytes()
            out.write(data if n == 0 else _strip_id3(data))


def _concat_wav(parts: list[Path], out_path: Path) -> None:
    """Piper writes WAV, which cannot simply be appended — it has a header."""
    import wave  # noqa: PLC0415

    with wave.open(str(parts[0]), "rb") as first:
        params = first.getparams()
    with wave.open(str(out_path), "wb") as out:
        out.setparams(params)
        for part in parts:
            with wave.open(str(part), "rb") as chunk:
                out.writeframes(chunk.readframes(chunk.getnframes()))


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


def speak(txt_path: Path, out_path: Path, cfg: Config, edition: Edition | None = None) -> Path:
    """Speak the .txt. Given its edition, the MP3 also gets a chapter per item."""
    text = spoken_part(txt_path.read_text(encoding="utf-8"))
    title = f"{edition.title} — {edition.week}" if edition else txt_path.stem
    segments = _segments(text, edition, title)
    # Chunked within a segment, never across one, so every chapter starts on a
    # chunk boundary and its start time is a sum of whole chunk lengths.
    owner: list[int] = []
    chunks: list[str] = []
    for n, segment in enumerate(segments):
        for chunk in chunk_text(segment.text, cfg.tts.chunk_chars):
            owner.append(n)
            chunks.append(chunk)
    if not chunks:
        raise AudioError("nothing to speak")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        use_piper = cfg.tts.offline or cfg.tts.engine == "piper"
        if not use_piper:
            try:
                parts = _synth_edge(chunks, cfg, tmp)
            except Exception as exc:
                log.warning("edge-tts failed (%s), falling back to piper", exc)
                use_piper = True
        if use_piper:
            parts = _synth_piper(chunks, cfg, tmp)

        _concat(parts, out_path)
        if len(segments) > 1 and parts[0].suffix.lower() == ".mp3":
            _add_chapters(out_path, title, segments, owner, parts)
    log.info("wrote %s from %d chunks", out_path, len(chunks))
    return out_path


def _add_chapters(
    out_path: Path, title: str, segments: list[Segment], owner: list[int], parts: list[Path],
) -> None:
    if len(segments) > 255:  # CTOC counts its entries in one byte
        log.warning("%d chapters is more than an MP3 can list; writing none", len(segments))
        return
    bounds = [[None, 0] for _ in segments]
    clock = 0
    for n, part in zip(owner, parts):
        if bounds[n][0] is None:
            bounds[n][0] = clock
        clock += round(mp3_duration(part.read_bytes()) * 1000)
        bounds[n][1] = clock
    chapters = [(s.title, start, end) for s, (start, end) in zip(segments, bounds)]
    out_path.write_bytes(chapter_tag(title, chapters) + out_path.read_bytes())
    log.info("marked %d chapters", len(chapters))
