"""What is spoken, in what chapters, by which engine. The speaking itself is echo's."""

from pathlib import Path

from digest.audio import _segments, _week_of
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


def test_a_txt_with_no_edition_is_titled_from_the_week_in_its_name():
    assert _week_of(Path("digest-2026-10-30.txt")) == "2026-W44"
    assert _week_of(Path("digest-2026-10-26.txt")) == "2026-W44"  # a Monday-named file from before
    assert _week_of(Path("notes.txt")) == "notes"
