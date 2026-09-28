"""Only the chunking is testable without a speech engine."""

from digest.audio import _segments, chapter_tag, chunk_text, mp3_duration
from digest.emit import DIVIDER, render_txt, spoken_part


def test_chunks_stay_under_the_limit():
    text = "\n\n".join("word " * 100 for _ in range(20))
    chunks = chunk_text(text, 3000)
    assert chunks and all(len(c) <= 3000 for c in chunks)


def test_chunks_break_only_between_paragraphs():
    text = "\n\n".join(f"Paragraph {n}." for n in range(6))
    assert chunk_text(text, 20) == [f"Paragraph {n}." for n in range(6)]


def test_a_paragraph_longer_than_the_limit_is_not_split():
    long = "word " * 2000
    assert chunk_text(long, 100) == [long.strip()]


def test_audio_is_made_only_from_the_spoken_part():
    text = f"Spoken prose.\n\n{DIVIDER}\nSources\n\n1. a — b — https://e.com/1\n"
    assert "https://" not in spoken_part(text)
    assert chunk_text(spoken_part(text), 3000) == ["Spoken prose."]


def _frames(n: int) -> bytes:
    """n MPEG-2 layer III frames at 48kbps, 24kHz: what edge-tts writes."""
    header = bytes([0xFF, 0xF3, 0x64, 0xC4])  # 144 bytes, 24ms of audio each
    return (header + b"\0" * 140) * n


def test_duration_counts_frames_and_skips_a_leading_tag():
    tag = chapter_tag("t", [("a", 0, 1)])
    assert abs(mp3_duration(tag + _frames(250)) - 6.0) < 1e-9


def _read_chapters(tag: bytes) -> list[tuple[str, int, int]]:
    i, out = 10, []
    while i < len(tag):
        fid, size = tag[i:i + 4], int.from_bytes(tag[i + 4:i + 8], "big")
        body = tag[i + 10:i + 10 + size]
        if fid == b"CHAP":
            rest = body[body.index(b"\0") + 1:]
            start, end = int.from_bytes(rest[:4], "big"), int.from_bytes(rest[4:8], "big")
            sub = rest[16:]
            out.append((sub[11:].decode("utf-16"), start, end))
        i += 10 + size
    return out


def test_chapter_tag_round_trips():
    chapters = [("Opening", 0, 900), ("Europe: A safeguard — past its sunset", 900, 4000)]
    tag = chapter_tag("The weekly digest — 2026-W36", chapters)
    assert tag.startswith(b"ID3\x03") and b"CTOC" in tag
    assert _read_chapters(tag) == chapters


def test_segments_are_used_only_while_the_txt_still_matches(tmp_path):
    from digest.tests.test_emit import _edition

    edition = _edition()
    text = spoken_part(render_txt(edition))
    titles = [s.title for s in _segments(text, edition, "fallback")]
    assert titles[0] == "Opening" and titles[1].startswith("East Asia: ")
    assert [s.title for s in _segments(text + "\nEdited.", edition, "fallback")] == ["fallback"]
