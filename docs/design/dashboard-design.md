# Operational dashboard — design

Written 2026-09-06 against the code as it stands. Companion: `dashboard-mockup.html`.
Design only; nothing here is implemented.

## What it is for

One page that answers *is this thing healthy, and if not, which part is wrong* —
for the last run first, and over the run history second. It is not the review
screen (which shows what was written and what was dropped, item by item) and it
is not analytics. The reader is one person, on one machine, looking at it either
right after a run or on a Friday morning when the scheduled run should have
happened.

The four things the owner asked to see, in the order they fail in practice:

1. **Sources** — which feeds contributed, which went quiet, which failed.
2. **Model calls** — counts, retries, rate limiting, time per stage, local vs hosted.
3. **The funnel** — fetched to published, and where each item died.
4. **Run history** — did it run, did it finish, how long, was it a dry run.

Plus the thing that was decided on 2026-09-06 (Q02): the system warns rather than
fails, so **warnings need a place to be seen**. Today they are lines in a log
file nobody opens.

## Where the page lives

One new route, `GET /health`, in `digest/ui/app.py`; one template
`health.html`; one link in `base.html`'s nav. Server-rendered from `state.db`,
`feeds.toml` and `config.toml`. No JavaScript beyond what the home page already
has (the `EventSource` stream, reused for the live stage strip while a run is in
progress). The home page's "Earlier weeks" table stays as it is; the dashboard
carries the fuller version.

## What is recorded today — the honest ledger

Three buckets, not two. The middle one changes the answer to "how much is new".

**Exists and is written.**

| Data | Where | What it supports |
| --- | --- | --- |
| Per-item verdicts, incl. `item.source`, `fit`, `kind`, `novelty`, `reason`, and `evidence[].kind` for selected items | `classified.json` (re-saved after `ground`) | Per-feed counts of what reached classification; fit/kind distribution; unjudged count (`reason LIKE 'classification failed:%'`); grounded 34/60 with article-vs-search split; per-source article-fetch refusals (Economist rows with no `article` evidence) |
| Drop-by-rule for the select stage | `pipeline.audit()` — pure replay of `select` over `classified` | Funnel: below threshold / saga / balance / over cap, with counts |
| `partial`, `quiet`, `entry_count`, `word_count`, `path` | `editions` | Output health; the `.txt` path the spoken check reads |
| `first_seen_week` | `seen` | Items new this week (non-dry runs only) |
| `enabled`, `verified`, `name`, `url`, `section` | `feeds.toml` | Feed table skeleton; paused feeds |
| Stage names and timestamps, per-entry progress | `jobs.Job.events` (in memory, 500-event ring) | Live stage strip and stage durations — for the run in progress only, gone on restart |
| Which provider serves which stage, pacing settings | `config.toml` `[models]`, `[advanced]` | The local/hosted label on the calls panel |
| Everything else — per-feed fetch counts, feed failures, retries, 429s, batch timings, grounding outcomes, all WARNING/ERROR lines | `~/Library/Application Support/Digest/logs/<week>.log`, unstructured text, CLI runs only | Nothing the dashboard should parse. Listed so it is clear the information is *emitted*, just not *kept* |

**Exists but unwired** — schema and code are there, nothing reaches them.
These are fixes, not new recording.

- `runs` is empty on the owner's machine. `start_run`/`finish_run` are called
  only under `run --scheduled` (`__main__.py:205`). The UI's `_real_run` never
  calls them, and neither does a plain `digest run`. A run that raises never
  reaches `finish_run`, so a failed scheduled run would sit at `status='running'`
  forever.
- UI runs write no log file. `_real_run` never calls `logging_setup.setup`, so
  `digest.*` loggers have no handlers in the web process; the `<week>.log` files
  that exist all came from the CLI. The W37 log on disk stops at batch 8/16 —
  that run was killed, and nothing anywhere says so.
- `runs` has no dry-run flag. Every run before 2026-09-06 was `--dry-run`, and
  history cannot tell them apart.

**Does not exist** — needs new recording.

- Per-feed outcome of a fetch (count, or the error). Only the log line.
- Per-call outcome of a model request (stage, backend, model, attempt, status,
  duration). Only the retry WARNING lines and the batch/entry INFO timestamps.
- Per-item grounding outcome for the *thin* items (article / search / none /
  blocked). The `evidence` column shows what succeeded; it cannot distinguish
  "had enough already" from "searched and found nothing", and the 403s are
  logged at DEBUG.
- Dedupe drops. `dupes` is discarded after its log line; only `fetched` minus
  the `classified` count implies it, and only per run.
- Warnings, as anything but log lines.
- Stage durations for finished runs.

**Caveat that would otherwise become a defect.** `classified` is keyed
`(id, week)`, so a week run twice *merges* item sets: W36 has 542 rows against
289 and 305 fetched in its two logged runs. Counts from `classified` are "items
ever classified for this week", not "this run". Per-run counts must come from
`runs` and the events table. `classified` is the audit record and is not
changed by this design.

## The minimum new recording

One table, one sink, two wiring fixes. Migration 3 → 4.

```sql
CREATE TABLE IF NOT EXISTS run_events (
    week    TEXT NOT NULL,
    started TEXT NOT NULL,          -- joins to runs(week, started)
    at      TEXT NOT NULL,
    level   TEXT NOT NULL,          -- INFO | WARNING | ERROR
    kind    TEXT NOT NULL,          -- stage | feed | call | ground | note
    subject TEXT,                   -- feed name, model, item id, stage name
    n       REAL,                   -- items fetched, attempt number, …
    ms      INTEGER,                -- duration where one exists
    message TEXT NOT NULL,          -- the log line as written
    detail  TEXT                    -- JSON for anything else
);
CREATE INDEX IF NOT EXISTS run_events_run ON run_events(week, started);
ALTER TABLE runs ADD COLUMN dry INTEGER NOT NULL DEFAULT 0;
```

**The sink is a `logging.Handler`, not a threaded callback.** The per-feed,
per-call and per-item lines already exist at the exact sites that matter
(`ingest.ingest`, `Client.complete`, `ground.ground`), in modules that have no
`State` handle and no `progress` callback. Threading one down means changing
`ingest()`, `fetch_source`, `Client.__init__`, `ground()` and every test that
builds them. A handler on the `digest` logger, installed beside the file handler
in `logging_setup.setup` with its own SQLite connection (it runs on the job
thread), needs no signature to change. It is created with `week` and is handed
`started` once `start_run` returns — `pipeline.run` sets it on the handler right
after `start_run`, since logging is set up before the run begins and `started`
does not exist until then. Rules:

- Every record at WARNING or above is written, `kind='note'` unless the record
  says otherwise. **This is what makes "warn instead of fail" visible with no
  further work** — every existing `log.warning` and `log.error` becomes a row.
- A record carrying `extra={"event": {"kind": ..., "subject": ..., "n": ...,
  "ms": ...}}` is written at any level with those fields.

Call sites that gain an `extra=` — six, each a one-line change to an existing
log call:

| Site | Kind | subject / n / ms |
| --- | --- | --- |
| `ingest.ingest` — `fetched %d items from %s` | `feed` | name / count / fetch time |
| `ingest.ingest` — `feed failed, skipping` | `feed` (WARNING) | name / 0 / — ; `detail` carries the exception class |
| `llm.Client.complete` — after a successful `generate` | `call` | model / attempt / elapsed; `detail` = `{stage, backend}` |
| `llm.Client.complete` — the existing retry WARNING | `call` (WARNING) | model / attempt / delay; `detail` = `{stage, backend, status}` |
| `ground.ground` — per thin item, a new DEBUG line | `ground` | item id / — / — ; `detail` = `{outcome: article\|search\|none\|blocked, source}` |
| `pipeline.checkpoint` — one new INFO line | `stage` | stage name / the count in `detail` / ms since the previous checkpoint |

`jobs.Job.record` stays as it is; the live strip keeps reading the ring buffer,
and the `stage` rows are what history reads after the run is over.

**The two wiring fixes**, no schema involved:

1. Every run writes `runs`: move `start_run` into `pipeline.run` (or a wrapper
   both callers share), set `dry`, and call `finish_run` from a `finally` with
   `ok | failed | cancelled` and the exception's last line in `note`.
2. The UI installs the same handlers the CLI does: `_real_run` calls
   `logging_setup.setup(cfg.log_dir, week)` before `pipeline.run`. Whether the
   *file* handler stays once events go to SQLite is an open question below.

Retention is not designed in. At roughly 400 rows a run and 52 runs a year the
table is under 25,000 rows; a `DELETE … WHERE week < ?` behind a "keep N
weeks" setting can come later if it ever matters.

## Panel 0 — the verdict banner

Added 2026-09-06 at the owner's request: *"flag things that need me to take
action at the top of the page."*

It is the only thing on the page that claims attention, and everything below it
is for after you have decided the answer is not "nothing". Without it the
dashboard is seven panels of numbers that all have to be read before you know
whether reading them was necessary.

**One banner, three states, and the state is computed rather than stored.**

| | When | What it says |
|---|---|---|
| **Green — All clear** | Neither of the below, and the last run finished | The run finished, and one line of what it produced |
| **Amber — Worth a look** | The pipeline hit something and worked around it | What happened, and explicitly that there is nothing to fix |
| **Red — Needs you** | The pipeline could not do it, and stopped or dropped something | The error, the evidence, and the command that fixes it |

**The line between amber and red is whether the pipeline recovered**, not how
alarming the message sounds. A timeout that succeeded on retry is amber even
though it logged a scary word. A `[PARTIAL]` edition is red even though the
briefing was still published, because entries were silently dropped and nobody
was told.

Red is any of: a run that never finished (`status='running'` with no live job),
an edition marked `[PARTIAL]`, credentials rejected, quota exhausted, the search
backend blocked for the rest of a run, a feed that has failed three consecutive
runs, or no run at all in a week the schedule was on. Amber is any WARNING that
resolved, an item left thin, a feed quiet once, or a spoken-check note. Green is
the absence of both.

**A red banner carries a debugging payload, and that is what distinguishes it
from a warning.** Four parts, all of them required:

1. **What stopped**, in a sentence, naming the stage and how far it got.
2. **The evidence** — the actual log lines, in a monospace block, including the
   warnings that led up to the error. `Connection refused` is a different
   problem from `timed out`, and the reader should not have to open a file to
   learn which one it was.
3. **What to do**, concretely. Not "check the model" but "nothing was listening
   on `http://localhost:11434` — Ollama was not running; nothing was marked
   seen, so a re-run starts from the same 306 headlines."
4. **Where to look** — the week, the stage, the log file path, links to the
   panels that carry the detail.

**Colour is never the only carrier.** Each state has its own word, its own
shape, and its own icon, so the banner reads the same to someone who cannot
distinguish the three colours, in a printout, or in either theme.

### Provenance

`exists` after the two wiring fixes, for everything except the run's own
warnings. `[PARTIAL]` and `quiet` are already on `editions`; a run that did not
finish is `runs.status` once every run writes it; the feed-failed-three-times
rule and the WARNING/ERROR list are queries over `run_events`. The banner
computes at page load and stores nothing — there is no banner state to go
stale, and no acknowledgement to forget to clear.

## The panels

Order on the page is the order the reader needs them: status, then what needs
attention, then the four subsystems, then history. Provenance is stated per
panel. **EXISTS** = readable today. **UNWIRED** = readable once the two fixes
land. **REQUIRES** = needs `run_events`.

### 1. Last run — the status strip (open by default)

Week, when it started, how long it took, `ok | failed | cancelled | did not
finish`, dry or live, and the four headline counts (fetched, selected, entries,
words). While a run is in progress this is the live stage strip from
`/progress`, stage by stage with elapsed time per stage.

- Status, counts, dry flag, duration: **UNWIRED** (`runs`, plus the `dry`
  column). "Did not finish" is `status='running'` with no live job.
- Live stages: **EXISTS** (`jobs.Job.events` via `/progress`).
- Stage durations for a finished run: **REQUIRES** `run_events kind='stage'`.
  Until then, the strip shows stages without times for past runs.

### 2. Needs attention (open by default)

The warnings inbox. Every WARNING and ERROR from the last run, grouped by
source module (`ingest`, `llm`, `ground`, `synthesize`, `cluster`, `emit`,
`audio`, `deliver`), with the message as logged and a count where the same
message repeated (the W36 log has 60 `RateLimitError` lines; that is one row
with `×60`). Below it, the **spoken-text check** run over the edition's `.txt`
at page load — `check_spoken.check` refactored to return its problems rather
than print them — listed as warnings in the same style, because that is what
Q02 decided they are.

- Run warnings: **REQUIRES** `run_events` at `level >= WARNING` (zero call-site
  changes; the handler captures them).
- Spoken check: **EXISTS** — computed at request time from `editions.path`.
  Nothing stored; if the file is gone the panel says so.
- `[PARTIAL]` and quiet-week flags: **EXISTS** (`editions.json`).

### 3. Sources — the feed table

One row per feed in `feeds.toml`, enabled or not. Columns: feed, items this
run, the last four runs as a mini-strip (count, or a mark for failed / paused),
the four-run median, share of the fetch, last verified, and a state chip:
*ok*, *quiet* (contributed 0 while the fetch succeeded — the Nikkei case),
*failed* (the fetch raised — `feed failed, skipping`), *paused* (`enabled =
false`). A feed that fails three runs running is called out in Needs attention;
that rule lives in the query, not in a scheduler.

Two signals a single number hides, both worth a column: Semafor is 128 of 306
items this week and 184 of 289 in W36 — one feed is more than a third of what
the classifier reads — and the Economist section feeds are thirteen rows that
together are the spine but individually small, so a 3 from *International* is
normal and a 3 from *Finance and Economics* is not. The median column is what
makes "quiet" a comparison rather than a threshold.

- Feed list, `enabled`, `verified`: **EXISTS** (`feeds.toml`).
- Items per feed per week: **EXISTS, with a caveat** — `classified.json →
  item.source` gives what reached classification, per week, merged across runs
  of that week; it cannot tell "quiet" from "failed" (both are zero rows).
  Per-run counts and the failed/quiet distinction: **REQUIRES** `run_events
  kind='feed'`.

### 4. Model calls

One block per stage — *filter* (classify) and *write* (cluster, frame, entries)
— each showing provider and model (local or hosted), calls made, retries,
rate-limit hits, failures, median and total time, and for a hosted backend the
pacing interval and whether the quota breaker tripped (`Client._spent`). A
third line for the run as a whole: total model time against wall-clock time,
which is the number that says where the twenty minutes go.

Health signals that come from the verdicts rather than the calls: the number of
items that came back **unjudged** (a batch that failed or an item missing from
the response — every one of these is a fit-0 that never got read), and the
**kind distribution** (a local model that answers *neither* zero times is
filing off-lens items as *adjacent*, which the balance rule then has to catch).

- Provider, model, local/hosted, pacing: **EXISTS** (`config.toml`).
- Unjudged count, kind distribution: **EXISTS** (`classified`).
- Call counts, retries, 429s, timings, breaker: **REQUIRES** `run_events
  kind='call'`.

### 5. The funnel

Fetched → after dedupe → judged → selected → grounded → written → published, as
bars to one scale, each step labelled with what left and why. The select step
expands into its four rules with counts: below threshold, saga, balance rule,
over the cap. The saga rule shows its count *and* the size of the prior
mechanism list it was matched against, because until there are prior editions
that number is zero and the rule has never fired — the panel should say that
rather than show a bare 0. The write step shows carried-in-the-reporter's-words
against model-written, and entries dropped for invention or by the length
governor.

- Fetched, selected, entries, words per run: **UNWIRED** (`runs`).
- After dedupe / judged / unjudged: **EXISTS** (`classified` count, minus
  `classification failed` rows) — per week, merged across runs.
- Drop by rule: **EXISTS** (`pipeline.audit`).
- Prior-mechanism count: **EXISTS** (`state.prior_mechanisms`).
- Carried vs written, entries: **EXISTS** (`editions.json → entries[].provenance`).
- Dedupe drops as a number: **UNWIRED** (`runs.fetched` minus `classified`
  count for that run — only correct for the first run of a week).
- Invention drops, governor drops, per run: **REQUIRES** (`run_events` WARNING
  and INFO rows from `synthesize`; the ERROR is captured free, the governor's
  INFO line needs `extra=`).

### 6. Grounding

Of the selected set: how many had enough text already, how many were thin, and
of the thin ones how many got the article itself, other outlets' coverage, or
nothing. Then the same split **per source**, because that is where the
Economist story lives: every Economist item is thin (57-character blurbs) and
every article fetch is refused (403), so they all go to search — 15 seconds
apiece. A line for the search backend: which one, how many queries, the pacing,
and whether it was blocked (the `SearchBlocked` warning, which stops all further
searching for the run).

- Grounded / article / search per selected item, per source: **EXISTS**
  (`classified.json → evidence[].kind`, selected rows only).
- Thin-but-nothing-found vs had-enough-already: **REQUIRES** `run_events
  kind='ground'` (today both are "no evidence"; the threshold in
  `ground_min_chars` against `blurb` length recovers most of it at query time,
  and the panel can use that until the events exist).
- Search blocked, search failed, queries made, pacing: blocked and failed are
  WARNING lines → **REQUIRES** `run_events` (captured free); queries made is
  `count(evidence.kind='search')` → **EXISTS**.

### 7. Run history

One row per run, newest first: week, started, dry or live, status, duration,
fetched / selected / entries / words, and the warning count. A run that never
finished renders as *did not finish* with the stage it reached, which is the
last `stage` event. Two or more runs of one week sit as separate rows. A
sparkline column over fetched and entries is cheap once the rows exist, and
useless before six or so runs; it is drawn but not the point.

- Rows: **UNWIRED** (`runs` + `dry`).
- Duration, warning count, stage reached: **REQUIRES** `run_events`.
- Dry-run identification for past weeks: not recoverable; the `dry` column
  starts empty and the history is honest about it.

## Left out, and why

- **Token counts and cost.** Ollama is unmetered, and the hosted path is
  optional; a cost panel would be empty on the owner's configuration and a
  temptation to instrument the SDK responses for the rare case.
- **A time-series store, charts over months, alerting.** One person, one
  machine, one run a week. SQLite rows and a page that is opened on Friday is
  the whole mechanism; anything that pushes rather than waits to be read is
  infrastructure the design was told not to build.
- **The per-item dropped table.** The review page has it, from the same
  `audit()` call. The funnel links there.
- **Calibration scores.** They are about the lens, not the run's health, and
  the check-the-lens screen owns them.
- **Parsing the log files.** Every panel above that could be built from
  `<week>.log` was instead marked REQUIRES, because a regex over prose is a
  panel that breaks the first time a message is reworded, and the handler makes
  the same information structured for six one-line changes.
- **Acknowledging warnings.** One `acknowledged_at` column would do it, but it
  turns a report into a workflow. Left as an open question.

## Open questions for the owner

1. **Should UI runs write a log file at all once events go to SQLite?** The
   file handler and the SQLite handler are independent; keeping both costs
   nothing and keeps `grep` working. Recommendation: keep both.
2. **Do dry runs appear in history by default?** They are the whole history
   before 2026-09-06. Recommendation: show them, marked *dry*, with a filter to
   hide them.
3. **Retention for `run_events`.** Recommendation: none until it matters.
4. **Should warnings be acknowledgeable?** Recommendation: no — the panel shows
   the last run, and the last run changes every week.
5. **Does the dashboard replace the home page's "Earlier weeks" table, or sit
   beside it?** Recommendation: beside it; the home page stays the place to
   press *Run*, and the dashboard is where to look when the result is odd.
6. **Is `GET /health` the right name?** `/status` reads as the run's status;
   `/health` reads as the system's, which is the question the page answers.
