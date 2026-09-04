# Implementation plan

Companion to `open-source-design.md`. Five phases, each shippable on its own and
testable without the network. Phase 1 is the smallest change that lets someone who is
not the owner install and run the digest without cloning the repository. The UI
comes second, the lens form third, because the lens form is the riskiest part and
phase 0 exists to find out early whether it can work at all.

Each phase lists the files it touches, the modules it adds, how it is tested, and what
"shipped" means. The test-suite promise — 199 tests, under a second, no network —
is kept by the rule in section 7.3 of the design: every new network edge is a
callable the tests replace, and an autouse fixture makes a stray socket call fail.

## Phase 0 — de-risk the lens compiler (two days, no release)

The whole authoring story rests on one claim: a rubric compiled from form fields
scores the same as the hand-written one. If that is false, the form design is wrong
and everything in phase 3 changes. So it is checked first, on the existing script,
before any packaging work.

**Build:**

- `digest/lens/schema.py` — a dataclass `LensSpec` with the fields from design
  section 2.2 (name, about, not_about, examples at fit 3/2/1 with reasons, the
  fit-1 exemption, never-list, regions, domains, kind display words).
- `digest/lens/compile.py` — `compile(spec) -> str`, producing markdown in the
  rubric's shape.
- `digest/lenses/architecture-of-rule.toml` — the current rubric decomposed into a
  `LensSpec` by hand.

**Test:**

- Unit: `compile(spec_from_toml)` produces markdown whose sections appear in the
  right order, and a round trip through the spec never loses an example.
- The de-risking measurement, run by hand and recorded in the phase-0 note:
  `scripts/eval_rubric.py --provider ollama --model qwen3:30b` with
  `cfg.prompts_dir` pointed at a directory holding the *compiled* rubric, against
  the same 25 labels. The bar is: the same "dropped wrongly" count (zero) and a
  "let in wrongly" count within one of the hand-written rubric's three. If the
  compiled rubric misses that bar, the compiler's wording is adjusted until it does,
  or the form is redesigned to keep more of the hand-written prose.

**Shipped means:** a recorded eval showing the compiled rubric matches the original,
and the decision to proceed with the form as designed.

**Risk it retires:** that a form can produce a rubric a model applies as well as a
person's prose.

## Phase 1 — installable, configurable from the terminal (two weeks)

The smallest thing that delivers real value: `uv tool install weekly-digest`, then
`digest init` in a terminal, then `digest run`. No browser. Someone else can run it
for their topic by picking a preset and pasting feed URLs.

**Files touched:**

- `pyproject.toml` — rename to `weekly-digest`, `digest` console script, extras
  `ollama` (empty, for the install line), `gemini`, `anthropic`, `audio`, `pdf`,
  `drive`, `dev`; `google-genai` moves to the `gemini` extra; package data adds
  `lenses/*`.
- `digest/config.py` → `digest/config/` package: `paths.py` (platformdirs and
  `DIGEST_HOME`), `schema.py` (pydantic models for the four files), `migrate.py`
  (numbered functions, `.bak` on write), `legacy.py` (the `digest.toml` importer
  from design section 3.4), `runtime.py` (builds the existing `Config` dataclass
  from the validated files so no stage changes). `Config.prompt("rubric.md")`
  becomes `Config.lens_text`.
- `digest/classify.py` and `digest/prompts/classify.md` — enums templated from the
  lens (`{regions}`, `{domains}`, `{kinds}`), `batch_schema(count, lens)`,
  `_coerce` maps display kinds to `core` / `adjacent` / `neither`.
- `digest/selection.py` — `"contest"` becomes `"adjacent"`.
- `digest/emit.py` — `REGION_NAMES` from the lens; title from the lens name.
- `digest/synthesize.py` — neutral `QUIET_WEEK` and `_fallback_frame` wording;
  `_writer_notes` keyed on a model tier.
- `digest/credentials.py` — `keyring` behind the same `resolve()` signature; the
  environment variable still wins; the old lookups move into `legacy.py`.
- `digest/state.py` — `PRAGMA user_version`, a migrations list, `runs` table.
- `digest/__main__.py` — `init`, `import`, `lens list|use|open`, `feeds
  add|check|list|remove`; `doctor` reports discovery.
- `scripts/eval_rubric.py` — thin wrapper over the new `digest/calibrate.py`.
- `scripts/io.digest.weekly.plist` — deleted in favour of the phase-4 generator;
  until then the README points at `digest schedule`, which prints the plist.

**New modules:**

- `digest/discover.py` — Ollama `/api/tags` and `/api/show`, memory check,
  `KNOWN_MODELS` with the README's measured numbers, `recommend()`.
- `digest/calibrate.py` — the scoring from `eval_rubric.py`, taking labels as an
  argument.
- `digest/lens/` — from phase 0, plus `load()` / `save()` for `lens.md` and
  `lens.toml` and the hand-edit hash check.
- `digest/lenses/` — the first preset, its default feeds, and at least two more
  presets written and checked against a small labelled set from their feeds.
- `digest/init.py` — the terminal wizard: detect, recommend, pick lens, paste
  feeds, write config. Every question has a default so pressing Enter through it
  produces a working install.
- `digest/pdf.py` — the Chrome/Chromium/Edge finder replacing the `html2pdf` call.

**Tests (all offline):**

- `test_config_schema.py` — every field validated, a typo produces a named error.
- `test_migrate.py` — a version-0 `digest.toml` (the one in the repo, read as a
  fixture) imports to the four files with the mapping in design 3.4; a version-N
  file migrates to N+1 and writes a `.bak`.
- `test_discover.py` — canned `/api/tags` and `/api/show` payloads through the
  injected fetch; a thinking-capable model gets `think=false`; a refused connection
  distinguishes "not running" from "not installed" via an injected `which`.
- `test_lens.py` — the compiled first preset equals the shipped `rubric.md` bytes;
  the hash check flags a hand edit.
- `test_classify.py` — extended: the schema enums follow the lens; an unknown region
  in a response clamps to the lens's default.
- `test_state.py` — extended: a fresh database is version 1; a version-1 database
  with `kind = "contest"` rows migrates to version 2 and `pipeline.audit` over it
  drops the same items, for the same reasons, as before the rename.
- `test_migrate.py` also covers the data move: a `state.db` at the legacy
  `~/.local/share/digest` path (under `tmp_path`) is copied into the new data
  directory and its `seen` ids survive.
- `conftest.py` — the autouse `socket.create_connection` guard.
- The existing 199 keep passing; the `cfg` fixture is built through `runtime.py`
  so the stages see the same dataclasses they see today.

**Shipped means:** a release on PyPI; a second machine (ideally Linux) runs
`uv tool install weekly-digest && digest init && digest run --dry-run` and produces
an edition from a preset other than the owner's.

**Risky parts:** the `classify.md` enum templating touches the prompt the measured
scores were taken on. Re-run `eval_rubric.py` on the first preset after the change
and require the same numbers. That is the one place phase 1 could silently make the
product worse.

## Phase 2 — the local web UI (three weeks)

The browser replaces the terminal wizard for setup and adds the two screens the
terminal cannot do well: watching a run and reviewing a week.

**Files touched:**

- `digest/pipeline.py` — `progress` callback and `cancel` event, both default
  no-ops.
- `digest/__main__.py` — `open` subcommand; `run --scheduled`.
- `pyproject.toml` — `ui` extra: fastapi, uvicorn, jinja2. htmx is vendored as a
  static file, not a dependency.

**New modules:**

- `digest/jobs.py` — one background thread, a lock so two runs cannot overlap, a
  ring buffer of progress events, an SSE generator.
- `digest/ui/` — `app.py` (FastAPI, bound to 127.0.0.1), `routes/` per screen,
  `templates/` (Jinja, house palette from `emit.STYLE` so the app and the edition
  look like one thing), `static/htmx.min.js`.
- Screens: setup wizard (steps 1–4 from the design; step 5 comes in phase 3), This
  week, Review, Feeds, Settings (models, outputs, schedule read-only until phase 4).

**Tests:**

- `test_jobs.py` — a fake pipeline function that emits three progress events; the
  buffer holds them; a second start while running is refused; cancel is honoured.
- `test_ui_*.py` — `TestClient` against every route with a `DIGEST_HOME` in
  `tmp_path` and the job runner replaced by the fake. Assert on rendered text, not
  markup. The feed-check route is tested with `fetch_bytes` stubbed to a fixture
  feed.
- No browser-driven tests in the suite. A short manual checklist lives in
  `docs/design/ui-checklist.md` and is run before a release.

**Shipped means:** `digest open` on a fresh machine walks setup, runs a week with
visible progress, and shows the edition with its audit.

**Risky parts:** a twenty-minute job inside a web process. Build `jobs.py` first,
with the fake pipeline, and prove: the browser tab can be closed and reopened
without losing progress; a crash in the job marks the run failed rather than hanging
the server; the CLI can still run while the server is up (it refuses with a clear
message if a run is in progress, via a lock file in the data directory).

## Phase 3 — lens authoring and calibration (three weeks)

The form and the calibration screen from design section 2. Depends on phase 0's
result and phase 2's UI shell.

**Files touched:**

- `digest/state.py` — `labels` table (item id, week fetched, label, labelled_at).
- `digest/ingest.py` — `sample(cfg, n=25) -> list[Item]` reusing `fetch_source`,
  spread across feeds.
- `digest/calibrate.py` — score against user labels; a `compare(models)` that runs
  the filter on each and returns the table.

**New:**

- `digest/ui/routes/lens.py` — preset picker, the form (one field per rubric
  section, example headlines as repeatable rows), the hand-edit banner with diff,
  "suggest examples" (fills fields from recent headlines via the configured model;
  never writes the file).
- `digest/ui/routes/calibrate.py` — the Want / Maybe / Skip screen, the two-column
  disagreement view, "add as example", "compare models".

**Tests:**

- `test_lens_form.py` — posting the form writes `lens.toml` and `lens.md`, and the
  compiled file round-trips; a hand edit shows the banner and is not overwritten
  until confirmed.
- `test_calibrate.py` — with a `FakeClient` that returns fixed fits, the
  disagreement lists are computed correctly; labels persist across a lens change;
  "add as example" lands the headline in the right field.
- The sampling route stubs `fetch_bytes`.

**Shipped means:** a user picks a preset, labels twenty-five of their own headlines,
sees where the lens disagrees, adds two examples, re-checks, and the disagreement
count falls.

**Risky parts:** whether adding examples actually moves the classifier. This is the
second half of the phase-0 risk and is measured the same way — on the owner's lens
and labels first, then on a second preset. If two added examples do not change
the outcome on a local model, the "add as example" action is misleading and should be
replaced with a plainer "the lens and you disagree on N; edit the lens" message.

## Phase 4 — scheduling, delivery, cross-platform (two weeks)

**New:**

- `digest/schedule.py` — `install`, `remove`, `status` for launchd, systemd user
  timers with cron fallback, and Task Scheduler. Each backend takes a `run` callable
  for its shell command so tests assert on the generated plist, unit file or
  `schtasks` arguments without executing them.
- The Settings → Schedule panel.
- The one-line installers (`install.sh`, `install.ps1`) hosted from the release.
- `digest/audio.py` — MP3 frame concatenation replacing `pydub`; ffmpeg no longer
  required.
- Delivery: "open the folder" as the default; Drive under Advanced with the
  existing code.

**Tests:**

- `test_schedule.py` — generated plist matches the behaviour comments carried from
  `scripts/io.digest.weekly.plist` (no key, explicit PATH, weekday and hour); the
  systemd unit has `Persistent=true`; the schtasks argument list is correct; status
  parses each backend's canned output.
- `test_audio.py` — extended: two fixture MP3 chunks concatenate to a file that
  decodes to the sum of their lengths (a tiny fixture pair checked in).
- Windows is exercised by hand on a real machine before the release; the plan
  budgets two days for it and expects path and console-encoding surprises.

**Shipped means:** on each of the three platforms, "Schedule: Friday 07:00" in the
UI produces a job that runs and writes the week's files while the browser is closed.

## Phase 5 — polish and, if demanded, native bundles (open-ended)

- Two or three more presets, each with feeds and a labelled set.
- Homebrew formula and winget manifest for the installed tool.
- A Briefcase bundle for macOS only, signed and notarized, only if the uv install
  line is what stops people in practice. The bundle would wrap the same server and
  install the same launch agent.
- Email delivery via SMTP if asked for.

## What is built first to de-risk, in order

1. The lens compiler round trip (phase 0) — because if it fails, the product story
   changes.
2. The `classify.md` enum templating, measured against the shipped labels — because
   it is the one phase-1 change that could degrade the current product.
3. `jobs.py` with a fake pipeline — because a job runner that loses progress or
   hangs the server would make the UI worse than the terminal.
4. A Windows dry run of phase 1, early, on a real machine — because every path,
   console and scheduler assumption in the code was made on a Mac.

## Keeping the suite fast and offline

- No test imports `fastapi.testclient` except the UI tests, and those construct the
  app with the job runner replaced.
- The autouse socket guard in `conftest.py` fails any test that opens a connection.
- Discovery, probing, scheduling and pulling all take their I/O as a parameter.
- Fixtures grow by: one legacy `digest.toml` (a copy of the repo's), one RSS feed
  document, two short MP3 frames, canned Ollama `/api/tags` and `/api/show` JSON,
  and one `LensSpec` TOML per preset. All small, all checked in.
- Target after phase 4: around 320 tests, still under two seconds.
