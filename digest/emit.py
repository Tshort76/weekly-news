"""Every format derives from one in-memory Edition.

The .txt is the contract: everything above the line of dashes is what the
owner's text-to-speech reads, so it carries no urls, no markdown and no headers.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .config import Config
from .models import Edition

log = logging.getLogger("digest.emit")

DIVIDER = "-" * 60

REGION_NAMES = {
    "east_asia": "East Asia",
    "south_asia": "South Asia",
    "europe": "Europe",
    "uk": "Britain",
    "us": "the United States",
    "mena": "the Middle East",
    "africa": "Africa",
    "latam": "Latin America",
    "global": "the wider world",
}

STYLE = """
:root {
  --bg: #fbfaf7; --fg: #22282e; --muted: #5d6670; --faint: #8b939b;
  --rule: #dcdfd9; --accent: #0e6e63;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #171c21; --fg: #d9dee2; --muted: #97a1aa; --faint: #7c858e;
    --rule: #323a41; --accent: #4cbcab;
  }
}
:root[data-theme="dark"] {
  --bg: #171c21; --fg: #d9dee2; --muted: #97a1aa; --faint: #7c858e;
  --rule: #323a41; --accent: #4cbcab;
}
* { box-sizing: border-box; }
body {
  background: var(--bg); color: var(--fg); margin: 0;
  font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
  font-size: 18px; line-height: 1.65;
}
main { max-width: 70ch; margin: 0 auto; padding: 3rem 1.5rem 6rem; }
h1 {
  font-size: 1.9rem; line-height: 1.2; margin: 0 0 .3rem;
  letter-spacing: -0.01em;
}
h2 {
  font-size: 1.25rem; line-height: 1.3; margin: 2.6rem 0 .6rem;
  padding-top: 1.4rem; border-top: 1px solid var(--rule);
}
h3 { font-size: 1rem; margin: 2rem 0 .4rem; color: var(--muted);
     font-family: "Avenir Next", "Segoe UI", system-ui, sans-serif;
     text-transform: uppercase; letter-spacing: .08em; }
p { margin: 0 0 1rem; }
em { color: var(--accent); font-style: italic; }
a { color: var(--accent); text-decoration-color: var(--rule); }
ol, ul { padding-left: 1.4rem; }
hr { border: 0; border-top: 1px solid var(--rule); margin: 3rem 0; }
blockquote {
  margin: 0 0 1rem; padding-left: 1rem;
  border-left: 2px solid var(--rule); color: var(--muted);
}
.meta {
  font-family: "Avenir Next", "Segoe UI", system-ui, sans-serif;
  font-size: .8rem; letter-spacing: .06em; text-transform: uppercase;
  color: var(--faint); margin: 0 0 2.5rem;
}
@media print {
  body { background: #fff; color: #000; font-size: 11pt; }
  main { max-width: none; padding: 0; }
  a { color: #000; text-decoration: none; }
}
"""


def week_stem(week: str) -> str:
    return f"digest-{week}"


def _transition(previous_region: str | None, region: str) -> str:
    """A short spoken bridge between entries. Silent when the region has not moved."""
    if region == previous_region:
        return ""
    name = REGION_NAMES.get(region, "elsewhere")
    if previous_region is None:
        return f"First, {name}."
    return f"Next, {name}."


def render_txt(edition: Edition) -> str:
    """Spoken prose above the divider, an appendix of sources below it."""
    lines: list[str] = []
    if edition.partial:
        lines.append("[PARTIAL] This edition is incomplete. Some items could not be written.")
        lines.append("")

    lines.append(f"The weekly digest, week {edition.week}.")
    lines.append("")
    if edition.opening:
        lines.append(edition.opening)
        lines.append("")

    previous_region: str | None = None
    for entry in edition.entries:
        bridge = _transition(previous_region, entry.region)
        previous_region = entry.region
        if bridge:
            lines.append(bridge)
            lines.append("")
        lines.append(entry.headline.rstrip("."))
        lines.append("")
        if entry.provenance == "source" and entry.attribution:
            lines.append(f"In {entry.attribution}'s own words.")
            lines.append("")
        if entry.body:
            lines.append(entry.body)
            lines.append("")
        # A carried entry's hook is its own opening sentence, so reading it
        # again would just repeat the paragraph the listener just heard.
        if entry.hook and entry.provenance != "source":
            lines.append(entry.hook)
            lines.append("")
        for question in entry.questions:
            lines.append(question)
            lines.append("")

    if edition.closing_questions:
        lines.append("Three questions to chew on.")
        lines.append("")
        for question in edition.closing_questions:
            lines.append(question)
            lines.append("")

    lines.append("End of the digest.")
    lines.append("")
    lines.append(DIVIDER)
    lines.append("Sources")
    lines.append("")
    n = 0
    for entry in edition.entries:
        for source in entry.sources:
            n += 1
            lines.append(f"{n}. {entry.headline} — {source['source']} — {source['url']}")

    return "\n".join(lines).rstrip() + "\n"


def spoken_part(text: str) -> str:
    """Everything above the divider — what the audio is made from."""
    return text.split(DIVIDER)[0].rstrip() + "\n"


def render_md(edition: Edition) -> str:
    out: list[str] = [f"# The Weekly Digest — {edition.week}", ""]
    stamp = edition.generated_at.strftime("%-d %B %Y")
    n = len(edition.entries)
    facts = [f"Generated {stamp}", f"{n} item{'' if n == 1 else 's'}", f"{edition.word_count} words"]
    carried = sum(1 for e in edition.entries if e.provenance == "source")
    if carried:
        facts.append(f"{carried} carried in the reporter's own words")
    if edition.theme:
        facts.insert(0, f"Theme of the week: {edition.theme}")
    out += [f"*{'. '.join(facts)}.*", ""]
    if edition.partial:
        out += ["> **[PARTIAL]** This edition is incomplete. Some items could not be written.", ""]
    if edition.opening:
        out += [edition.opening, ""]

    for entry in edition.entries:
        out += [f"## {entry.headline}", ""]
        if entry.provenance == "source" and entry.attribution:
            out += [f"<small>**{entry.attribution}'s own words**, not a summary.</small>", ""]
        if entry.body:
            out += [entry.body, ""]
        if entry.hook and entry.provenance != "source":
            out += [f"*{entry.hook}*", ""]
        for question in entry.questions:
            out += [f"> {question}", ""]
        if entry.sources:
            links = ", ".join(f"[{s['source']}]({s['url']})" for s in entry.sources)
            out += [f"<small>{links}</small>", ""]

    if edition.closing_questions:
        out += ["## Three questions to chew on", ""]
        out += [f"{n}. {q}" for n, q in enumerate(edition.closing_questions, 1)]
        out += [""]
    return "\n".join(out).rstrip() + "\n"


def render_html(markdown_text: str, edition: Edition) -> str:
    import markdown as md_lib

    body = md_lib.markdown(markdown_text, extensions=["extra", "sane_lists"])
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>The Weekly Digest — {edition.week}</title>\n"
        f"<style>{STYLE}</style>\n</head>\n<body>\n<main>\n{body}\n</main>\n</body>\n</html>\n"
    )


def write_pdf(html_path: Path, pdf_path: Path, cfg: Config) -> bool:
    """html2pdf by default — a headless-Chrome wrapper already on the machine, so
    nothing has to be installed. weasyprint is opt-in via digest.toml."""
    if cfg.pdf.engine == "weasyprint":
        try:
            from weasyprint import HTML  # noqa: PLC0415

            HTML(filename=str(html_path)).write_pdf(str(pdf_path))
            return True
        except Exception as exc:
            log.warning("weasyprint failed (%s), falling back to html2pdf", exc)

    try:
        subprocess.run(
            ["html2pdf", str(html_path), "--out", str(pdf_path)],
            check=True, capture_output=True, timeout=180,
        )
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("pdf generation failed: %s", exc)
        return False


def emit(
    edition: Edition,
    cfg: Config,
    want_html: bool = False,
    want_pdf: bool = False,
) -> dict[str, Path]:
    """Write the formats and return {extension: path}. Re-running a week overwrites."""
    out_dir = cfg.run.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = week_stem(edition.week)
    written: dict[str, Path] = {}

    txt_path = out_dir / f"{stem}.txt"
    txt_path.write_text(render_txt(edition), encoding="utf-8")
    written["txt"] = txt_path

    markdown_text = render_md(edition)
    md_path = out_dir / f"{stem}.md"
    md_path.write_text(markdown_text, encoding="utf-8")
    written["md"] = md_path

    if want_html or want_pdf:
        html_path = out_dir / f"{stem}.html"
        html_path.write_text(render_html(markdown_text, edition), encoding="utf-8")
        written["html"] = html_path
        if want_pdf:
            pdf_path = out_dir / f"{stem}.pdf"
            if write_pdf(html_path, pdf_path, cfg):
                written["pdf"] = pdf_path

    return written
