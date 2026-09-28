# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A weekly briefing generator. It reads public RSS feeds, has a language model judge each headline against a written editorial **lens** (the rubric), and writes the selected stories up as prose meant to be *listened to*. The rubric is the product; the code is plumbing. It runs fully local by default (Ollama: `qwen3:30b` classifies, `gemma3:27b` writes), with Gemini and Anthropic as config-only alternatives.

## Commands

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"   # add ,audio / ,drive / ,ui / ,pdf as needed

python -m pytest -q                                     # whole suite: no network, no model calls
python -m pytest digest/tests/test_emit.py::test_name   # one test

python -m digest run --dry-run --no-drive     # a full week that writes files but records nothing
python -m digest audit --week 2026-W36        # what selection dropped, and why
python -m digest render --week 2026-W36 --html
python -m digest speak --week 2026-W36        # audio from an existing .txt
python -m digest doctor                       # keys, backends, feeds, paths; spends nothing

python scripts/check_spoken.py ~/digests/digest-<date>.txt   # lint the spoken part (warns, exits 0; --strict)
python scripts/eval_rubric.py                 # does the model apply the lens? re-run after any rubric/model change
```

There is no configured linter or formatter; the code carries `# noqa: PLC0415` markers for deliberate function-level imports.

## Architecture

**Pipeline** (`digest/pipeline.py` is the imperative shell; each stage is its own module):

```
ingest → normalize → dedupe → classify → select → ground → partition → cluster → synthesize → emit → audio → deliver
```

- `classify.py` batches headlines (title and blurb only, never keywords) through the lens prompt and gets fit, kind, novelty and mechanism back. Every verdict is stored, which is what makes `audit` reproducible when the model is not.
- `selection.py` is pure: fit threshold, the "saga" rule (a mechanism already covered in prior weeks is dropped unless it is new), and a cap on the adjacent/"contest" share.
- `ground.py` fetches more text only for selected items whose feed entry is thin, in three tiers: feed body, then the article page, then a search for other coverage.
- `synthesize.py` partitions out "carried" items (a reporter's own words are published instead of rewritten), then writes one Entry per cluster, then a frame (opening, order, closing questions). An entry that names an institution its sources never mention is rewritten, then dropped.
- **Failures degrade, never abort.** A failed cluster call makes each item its own entry, a failed entry is skipped, and a failed frame falls back to fit order. Any of these marks the edition `[PARTIAL]`. Audio, PDF and Drive failures never block the edition.

**The model edge** is `digest/llm.py`. Stages ask for `"classify"` or `"synthesize"`; config maps that to a provider and model. Nothing upstream knows which model ran. Prompts live in `digest/prompts/*.md`; a `{writer_notes}` slot carries rules that fill in only for local models.

**`Edition` (`digest/models.py`) is the single in-memory artifact** every output format derives from (`digest/emit.py`). The `.txt` is the contract: everything above the dashed `DIVIDER` is spoken prose (no URLs, no markdown, acronyms spelled out), and below it is a sources appendix. Audio is made only from the spoken part (`digest/audio.py`: edge-tts, piper fallback, chunks joined frame by frame with no ffmpeg).

**Lenses** (`digest/lens/`, presets in `digest/lenses/`): each lens is a `lens.md` (the rubric a person edits) plus `lens.toml` (the form's structured spec). `lens/compile.py` turns the form back into a rubric of the *shape* that `classify.md`, `selection.py` and `cluster.md` expect. `*.labels.json` are hand-labelled headlines used by the rubric eval.

**Config has three sources**, tried in order by `digest.config.load()`: the installed app's validated files in the platform config directory (`config/paths.py`, overridden by `DIGEST_HOME`, which every test uses); a checkout's `digest.toml` in the working directory; and legacy migration (`config/legacy.py` copies an old `digest.toml` and `state.db` into an installed layout and never deletes them). `config/schema.py` validates fields and names the file and path of every error.

**State** is SQLite (`digest/state.py`), keyed by ISO week so re-running a week overwrites. It holds `seen`, `classified`, `editions` and `entries` (used for saga detection and "since last week"), `deliveries` (idempotent Drive upload), and the `runs`/`run_events` that the dashboard (`health.py`) reads. A `--dry-run` still writes files and classifications but leaves `seen`, `editions` and `entries` untouched, so it can be repeated and never hides an item from next week.

**UI** (`digest/ui/`, `digest open`): a FastAPI app bound to 127.0.0.1 only, server-rendered with about sixty lines of JavaScript (an `EventSource` progress stream). Long runs go through `digest/jobs.py`, which keeps events in a ring buffer so a reconnecting tab replays what it missed.

**Scheduling** (`digest/schedule.py`) writes the OS scheduler's own file (launchd, systemd or Task Scheduler) with an explicit `PATH` and never an API key.

## Conventions that matter here

- **No pull requests.** Worktrees are fine, but finished work is merged into `main` directly (fast-forward) and pushed, and the worktree's branch is then deleted.

- **Keep dependencies minimal.** Core deps are deliberately short; anything only one provider or output needs is an extra. The installers (`install.sh`, `install.ps1`) target non-technical users on macOS, Linux and Windows, so don't add system requirements such as ffmpeg.
- **Output filenames use the Monday's date** (`digest-2026-09-21.txt`, via `emit.week_stem`); the ISO week (`2026-W39`) stays the key everywhere else.
- **Test fixtures in `digest/tests/fixtures/` are hand-authored model responses**, and providers are tested against fake SDK clients that record the request shape.
- **Model evals are noisy.** `seed` and `temperature` are pinned, but repeated runs can still differ. Run an eval at least three times before believing a small delta. If a prompt edit changes *nothing* at all, suspect the edit never reached the model.
- Comments and docstrings explain *why*, often with the measurement or incident behind a choice. Read them before changing a threshold or removing something that looks redundant.
- `docs/design/` holds the design history (`what-was-built.md` is the overview).
