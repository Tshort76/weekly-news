"""The .txt is the contract: it has to read aloud cleanly."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from digest.emit import DIVIDER, emit, render_md, render_txt, spoken_part
from digest.models import Edition, Entry


def _edition(**kwargs) -> Edition:
    base = dict(
        week="2026-W36",
        generated_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
        opening="This week the shape is monetary plumbing.",
        entries=[
            Entry(
                cluster_id="c1",
                headline="Japan targets reserve quantity, not price",
                body="The central bank changed what it steers.",
                hook="The interest rate is now an outcome of operations rather than the thing set.",
                questions=["What happens to transmission?"],
                sources=[{"source": "Economist — Finance", "url": "https://e.com/1"}],
                fit=3,
                region="east_asia",
            ),
            Entry(
                cluster_id="c2",
                headline="Brussels rolls a steel safeguard past its sunset",
                body="The quota was extended.",
                hook="A safeguard with no end date is a tariff by another name.",
                sources=[{"source": "Economist — Europe", "url": "https://e.com/2"}],
                fit=2,
                region="europe",
            ),
        ],
        closing_questions=["One?", "Two?", "Three?"],
        theme="Rules that close off an option",
    )
    base.update(kwargs)
    return Edition(**base)


def test_the_spoken_part_carries_no_urls_or_markdown():
    spoken = spoken_part(render_txt(_edition()))
    assert "http" not in spoken
    assert not re.search(r"[*_#`\[\]]", spoken)


def test_the_sources_appendix_sits_below_the_divider():
    text = render_txt(_edition())
    above, below = text.split(DIVIDER)
    assert "https://e.com/1" in below and "https://e.com/1" not in above
    assert below.count("https://") == 2


def test_the_appendix_numbers_every_source():
    below = render_txt(_edition()).split(DIVIDER)[1]
    assert "1. Japan targets reserve quantity" in below
    assert "2. Brussels rolls a steel safeguard" in below


def test_a_spoken_transition_announces_each_new_region():
    spoken = spoken_part(render_txt(_edition()))
    assert "First, East Asia." in spoken
    assert "Next, Europe." in spoken


def test_no_transition_when_the_region_has_not_moved():
    edition = _edition()
    edition.entries[1].region = "east_asia"
    spoken = spoken_part(render_txt(edition))
    assert spoken.count("East Asia") == 1
    assert "Next," not in spoken


def test_a_partial_edition_says_so_in_the_first_line():
    text = render_txt(_edition(partial=True))
    assert text.splitlines()[0].startswith("[PARTIAL]")


def test_a_quiet_week_is_two_lines_of_prose_and_no_entries():
    edition = _edition(entries=[], closing_questions=[], quiet=True, opening="Nothing met the bar.")
    spoken = spoken_part(render_txt(edition))
    assert "Nothing met the bar." in spoken
    assert "First," not in spoken


def test_markdown_keeps_the_links_and_italicises_the_hook():
    md = render_md(_edition())
    assert "[Economist — Finance](https://e.com/1)" in md
    assert "*The interest rate is now an outcome" in md
    assert "Theme of the week: Rules that close off an option" in md


def test_emit_writes_txt_and_md_always_and_overwrites_on_a_rerun(cfg):
    first = emit(_edition(), cfg)
    assert set(first) == {"txt", "md"}
    second = emit(_edition(), cfg)
    assert second == first
    assert len(list(cfg.run.output_dir.iterdir())) == 2


def test_emit_writes_html_on_request(cfg):
    files = emit(_edition(), cfg, want_html=True)
    html = files["html"].read_text(encoding="utf-8")
    assert "<title>The Weekly Digest — 2026-W36</title>" in html
    assert "prefers-color-scheme: dark" in html
    assert '[data-theme="dark"]' in html
    assert "<script" not in html
    assert "http://" not in html.split("<main>")[0]  # nothing loaded from outside
