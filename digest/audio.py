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
from .emit import spoken_part

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


def speak(txt_path: Path, out_path: Path, cfg: Config) -> Path:
    text = spoken_part(txt_path.read_text(encoding="utf-8"))
    chunks = chunk_text(text, cfg.tts.chunk_chars)
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
    log.info("wrote %s from %d chunks", out_path, len(chunks))
    return out_path
