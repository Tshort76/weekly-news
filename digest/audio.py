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


def _concat(parts: list[Path], out_path: Path) -> None:
    from pydub import AudioSegment  # noqa: PLC0415

    combined = AudioSegment.empty()
    silence = AudioSegment.silent(duration=400)
    for n, part in enumerate(parts):
        segment = AudioSegment.from_file(part)
        combined += segment if n == 0 else silence + segment
    combined.export(out_path, format="mp3")


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
