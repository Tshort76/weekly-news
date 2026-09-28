# Using echo as the text-to-speech library

**Status:** echo v0.3.0 meets all eleven requirements, and weekly-news uses it (the `audio` extra). The "Today" column below records where echo stood when this was written.

weekly-news made its own audio when this was written (`digest/audio.py`: edge-tts, piper as fallback, chapter marks written by hand). echo (`~/dev/echo`) already has the better design for this: engines behind one interface, a script made of titled chapters, retries and resumable synthesis. This document lists what echo needs to provide so weekly-news can hand it the spoken text and get back a chaptered audio file, with no knowledge of how the audio is made.

The dividing line: **weekly-news decides what is said and where the chapters fall. echo decides how it is spoken and how the file is built**, including which system tools it needs to do that.

## The call weekly-news wants to make

```python
from echo import speak_chapters, Chapter   # names illustrative

result = speak_chapters(
    [Chapter("Opening", text), Chapter("Europe: …", text), ...],
    out_path=Path("~/digests/digest-2026-09-21.mp3"),
    title="The weekly digest — 2026-W39",
    engine="edge", voice="en-GB-RyanNeural",
)
result.chapters   # [(title, start_ms, end_ms), ...]
```

## Requirements

"Today" says where echo stands now, checked against its code on 2026-09-27.

| # | Requirement | Today |
| --- | --- | --- |
| 1 | **One public entry point: titled chapters of plain text in, one audio file out.** echo splits each chapter's text to fit the engine's size limit itself. A chapter never shares a chunk with its neighbour, so each chapter mark lands exactly on its boundary. | Partly. `synthesize_script` and `assemble` are separate calls, and the chunking lives in `build_script`, which starts from a `Document`, not from chapters of text. |
| 2 | **Chapter marks in MP3**, not only M4B, readable by podcast players and VLC (ID3 `CHAP` frames plus a `CTOC` table of contents). | No. `assemble()` writes chapters for M4B only. MP3 is the format weekly-news delivers. |
| 3 | **Return each chapter's start and end time** to the caller, so the times can be reused (show notes, links in the HTML edition). | No public return value. `chapter_marks()` exists inside the assembly step. |
| 4 | **Configuration by argument, not by environment.** Engine, voice, speed, chunk size and bitrate are parameters with defaults. Importing echo reads no `.env` file and does not change `os.environ`. | No. `echo/constants.py` calls `load_dotenv()` at import and reads its settings from environment variables at import time. It would pick up any `.env` in weekly-news's working directory. |
| 5 | **Engines are chosen by name, and "is it usable here?" can be asked before any synthesis**, with a message saying what to install or set. A failure during synthesis raises a typed error, so the caller can retry on another engine. | Yes: `get_engine(name)`, `check_available()`, `EngineUnavailable`, `SynthesisError`. |
| 6 | **Installable as an ordinary dependency.** `pip install "echo-tts @ git+…@<tag>"` works. Dependencies are version ranges, not exact pins. The speech path does not pull in PDF, EPUB or HTML parsing libraries; those move to an extra. | No. The core dependencies are exact pins (`edge-tts==7.2.8`, …), and PyMuPDF, EbookLib and BeautifulSoup are in the core install. |
| 7 | **A pip install is a working install.** After installing echo with the chosen engine's extra, audio can be made on macOS, Linux and Windows with no further manual step. How echo joins and tags audio, and what it bundles to do so, is echo's concern. | No. Joining chunks needs an `ffmpeg` found on the machine (`assemble._ffmpeg`), which is a separate `brew install` today. weekly-news's installers target non-technical users, so it cannot ask for that step. |
| 8 | **The caller controls where temporary files go**, and they are removed when the call finishes (resume across calls can stay opt-in). Nothing is written beside the output file or into the working directory unless asked. | To check. `chunks_dir_for(output_path)` derives the chunk folder from the output path, which would put it in `~/digests`. |
| 9 | **Runs on Python 3.12 and later, including 3.14**, for the core and the edge engine. The local (mlx) engine states plainly which versions it supports. | Core yes (`>=3.11`). The mlx engine works only on 3.13 today; its `check_available` says so. |
| 10 | **Quiet by default and reports progress through a hook.** Logging goes through the standard `logging` module; nothing is printed to stdout, because the weekly run is often a scheduled job. An optional progress callback (chunks done / total) feeds the weekly-news web UI's progress stream. | To check. |
| 11 | **A small, documented public API with tagged versions**, so weekly-news can depend on a version range and a breaking change shows up as a version bump. | No tags or declared public API yet. |

Requirements 1, 2, 4, 6 and 7 block the switch. The rest can follow.

## Not required from echo

- Extraction, the text normalizers, Project Gutenberg, the GUI. weekly-news writes its own spoken text, already in the form it wants read.
- Any particular way of joining or tagging audio. That is covered by requirement 7 and left to echo.

## What changes in weekly-news

- `digest/audio.py` becomes a thin adapter: `emit.spoken_segments(edition)` becomes echo chapters, and one call makes the file. The hand-written ID3 tag and MP3 frame parsing are removed.
- `[tts]` in config maps directly to echo's engine and voice arguments.
- Piper is either dropped or added to echo as an engine. That decision is open; a good local model in echo would make piper unnecessary.
- The `audio` extra depends on `echo-tts` with the engine extras it needs.
