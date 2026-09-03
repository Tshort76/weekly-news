"""Only the chunking is testable without a speech engine."""

from digest.audio import chunk_text
from digest.emit import DIVIDER, spoken_part


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
