# Weekly Digest as an installable application — design

This is a design for turning the weekly-news repository into an application other
people can install and run for their own topics, with their own feeds, on whatever
model they have. It is written against the code as it stands on 2026-09-04. Where the
code carries a docstring explaining a decision, this design keeps that decision unless
it says otherwise and says why.

The companion documents are `implementation-plan.md` (the phased proposal) and
`ui-mockup.html` (what the interface looks like).

## The short version

- **The lens is authored three ways at once, and they are the same thing.** A
  preset gives a whole rubric, the current one included verbatim. A form edits the
  rubric section by section, in the rubric's own fixed shape, asking for example
  headlines rather than abstractions. A calibration screen shows the user twenty-odd
  headlines from their own feeds, asks "would you want to read this?", and reports
  where the rubric disagrees with them. The markdown file stays on disk and stays
  editable, so nobody who can write a prompt is locked out.
- **Config moves out of the repository** into the platform's application-data
  directory, split into four files: `config.toml` (plumbing), `feeds.toml`,
  `lens.md` (the compiled rubric) and `lens.toml` (the form's answers). It carries a
  schema version and is migrated by numbered functions. The existing `digest.toml`
  is imported once and never read again.
- **Model setup detects rather than asks.** The app asks a local Ollama what models
  it has and which of them can think, ranks them against a short list of models with
  measured scores, and recommends one. If there is no Ollama, it recommends a hosted
  provider using the repository's own measurements and stores the key in the
  operating system's credential store.
- **Install is one line per platform** that puts a Python tool on the path with
  `uv`, after which `digest` opens a browser on a local web page. Native bundles are
  deferred; the user is already installing Ollama as a desktop app, so a one-line
  terminal install is the floor the environment already sets.
- **The UI is a local web app**, server-rendered Python with a little htmx, bound to
  `127.0.0.1`, sharing one core with the command line so the scheduled Friday job
  runs headless. It does setup, lens authoring, feeds, run-with-progress, review and
  schedule. It does not edit an edition's text and it does not override a
  classification.
- **The carried-words feature is not a knob.** The 700-character floor, the
  200-word ceiling and the "In X's own words" attribution stay fixed, because they
  are what makes publishing a reporter's paragraph defensible when the feeds are no
  longer the owner's.

## 1. What becomes configurable, and what stays fixed

The principle: a setting is exposed only when a reasonable user would set it
differently from another reasonable user *and* could tell whether they got it right.
Everything else is a constant in code, or is detected.

### Exposed, in the UI

| Setting | Today | In the app |
| --- | --- | --- |
| The editorial lens | `digest/prompts/rubric.md`, inside the package | `lens.md` in the config directory, authored by preset, form or file. Section 2. |
| Feeds | `[[sources]]` in `digest.toml` | `feeds.toml`, managed on the Feeds screen with add-time verification. Section 3.4. |
| Which model does which stage | `[models]` provider and model names | Detected and recommended; the user can pick from what was found. Section 4. |
| Hosted API key | four-way lookup, macOS Keychain | The OS credential store via `keyring`, entered once in the UI. Section 4.4. |
| Length | `max_words = 8500`, `max_items = 60` | One "how long" control shown in listening minutes (`check_spoken.py` already uses 145 words a minute: 8,500 words is about an hour). The two numbers are derived from it together. |
| Outputs | `--html --pdf --audio` flags, `[tts]`, `[pdf]` | Checkboxes: text, web page, PDF, audio. Voice picker when audio is on. |
| Delivery | `[drive]` | Off by default. "Open the folder" is the default delivery. Google Drive stays available as an *advanced* option with its own page, because the OAuth setup requires creating a Google Cloud project and no non-technical user should meet that on first run. |
| Schedule | a plist you edit by hand | Day and hour, installed for the platform by the app. Section 5.3. |
| Output folder | `output_dir` | A folder picker, defaulting to `~/Digests`. |

### Hidden, kept as defaults, editable only in `config.toml` under `[advanced]`

These are the settings whose right value was found by measurement in this
repository and whose wrong value produces a bad edition with no visible cause.
A user who wants them can open the file; the UI never shows them.

- `classify_batch_size = 25` — measured better than 5 or 10 on which items end up
  selected, per the comment in `digest.toml`.
- `seed`, `classify_temperature`, `ollama_temperature`, `classify_thinking`,
  `synthesize_thinking` — the temperature values in particular were measured
  (1.0 against 0.3 on gemma3 doubled the invented institutions).
- `min_interval_seconds`, `max_attempts`, `backoff_seconds`, `max_backoff_seconds`
  — the pacing and the quota breaker. Per-provider defaults ship in code (Gemini
  free tier 12 seconds, Anthropic 0), so the user never sets them.
- `ollama_num_ctx = 32768`.
- `ollama_think` — no longer a setting at all. It is detected: `ollama show` (the
  `/api/show` endpoint) lists `thinking` under capabilities, and the README says
  that is exactly when it must be false. The app reads that and sets it.
- `fetch_days = 8`, `contest_share = 0.20`, `ground`, `ground_min_chars = 500`,
  `search_backend`, `source_min_chars = 700`, `source_max_words = 200`.

### Fixed in code, not configurable at all

- The pipeline order and the degrade-rather-than-abort behaviour.
- The fit threshold (2, or 1 with novelty 3), the saga rule and the balance rule.
  These are the selection contract the `audit` command replays; a user who could
  change them between runs would break the promise that an audit reproduces a
  week.
- The carried-words attribution line and its caps.
- The spoken-text rules (no digits, no acronyms, no parentheticals). They are what
  the .txt contract means.
- Where state lives (SQLite in the platform data directory).
- The UI binds to `127.0.0.1` only. There is no remote mode and no login.

### What a non-technical user should never see

The words *temperature*, *seed*, *thinking*, *context window*, *batch*, *backoff*,
*token*, *schema*, *num_ctx*, *provider* as a noun, *rubric* (the UI says "lens"),
*classify* and *synthesize* as stage names (the UI says "filter" and "write"),
*TOML*, *plist*, *launchd*, *systemd*, *cron*, and any file path except the output
folder they chose.

## 2. Authoring the lens without being a prompt engineer

This is the hardest part of the design, so it gets the longest treatment.

### 2.1 What the rubric actually is

`digest/prompts/rubric.md` is thirty lines. Reading it against `classify.md`,
`classify.py`, `selection.py` and `synthesize.py`, the code depends on its *shape*
and not on its *content*. The shape is:

1. **A lens statement** — one line: "the architecture of rule, not the contest for it."
2. **A FIT scale, 0 to 3, with concrete examples at each level.** "A central bank
   adopts a new operating framework" is a 3; "an election result" is a 1 unless it
   explains a structural consequence.
3. **A three-way KIND**: the thing the lens is about (`architecture`), the adjacent
   thing it is not about (`contest`), and everything else (`neither`).
4. **NOVELTY 0 to 3**: new fact versus another episode.
5. **MECHANISM**: name the causal machinery in twelve words or null.
6. **Bias notes**: regions of interest that must not inflate the score.

Everything downstream is wired to that shape. `classify.md` asks for `fit`, `kind`,
`novelty`, `mechanism`, `region`, `domain`. `selection.py` thresholds on fit and
novelty, caps `kind == "contest"` at a share of the set, and uses mechanism for the
saga rule. `cluster.md` groups by mechanism. `synthesize_entry.md` tells the writer
to say "what changed structurally". `theme_candidate` needs a shared mechanism.

Two things follow. First, a lens for a different topic must produce the same shape,
so the form has to be a form *for that shape*. Second, the rubric works because its
examples are concrete. A rubric that said "score 3 when the item is structurally
important" would be a worse classifier than the one that says "a cartel forms or
breaks". So the form must elicit examples, not adjectives.

### 2.2 The three layers

**Layer 1 — presets.** A preset is a complete `lens.md` plus the `lens.toml` that
compiles to it, hand-written, shipped in the package under `digest/lenses/`. The
first preset is the current rubric, byte for byte. Five or six more ship with the
app, each written by a person and checked against a small labelled set from real
feeds, so a preset is never a template with the topic swapped in:

- Architecture of rule (the current one; world affairs through institutions and
  systems)
- Science and technology: what became possible
- Money and markets: plumbing, not prices
- A country or region, through its institutions (parameterised by a region list)
- Climate and energy: capacity, not targets
- Health and medicine: what changed in practice

A preset also ships a default feed list, because a lens without feeds that carry
the right kind of story is a lens that will have a quiet week every week.

Picking a preset is the whole of the first-run lens step. The form and calibration
are offered afterwards, not required.

**Layer 2 — the form.** The form has one field per rubric section, and the compiler
`digest/lens/compile.py` turns the answers into markdown of exactly the shape above.
The fields:

| Field | Prompt shown to the user | What it compiles to |
| --- | --- | --- |
| Name | "Give this lens a name" | The eyebrow in the UI and the `theme` line in outputs |
| The one line | "In one sentence, what is this briefing about — and what is it *not* about?" Two boxes: *about*, *not about*. | The LENS statement. The "not about" half becomes the KIND contrast. |
| What earns the top score | "Give three to six example headlines you would definitely want, and say in a few words why each one counts." | FIT 3 examples |
| What earns a middling score | "Give three to six headlines you'd probably want" | FIT 2 examples |
| What you'd usually skip | "Give three to six headlines that are near your topic but you'd usually skip, unless…" with an *unless* box | FIT 1 examples plus the exemption clause |
| What never belongs | "What kinds of story should never appear? (celebrity, sport, opinion, polls…)" — a checklist of the common ones plus free text | FIT 0 and KIND `neither` |
| Regions or places of interest | A checklist of the nine regions the code knows, with an option to rename and an "add a place" box | The bias note *and* the `region` enum for `classify.md` and `emit.REGION_NAMES` |
| Topics | A checklist of the nine domains with rename and add | The `domain` enum |
| Mechanism | Not asked. Always compiled in, with the preset's wording. | MECHANISM section |
| Novelty | Not asked. Always compiled in. | NOVELTY section |

The form deliberately does not ask what a 0-to-3 scale is, what novelty means, or
what a mechanism is. Those are the parts of the rubric that are the same for every
lens and were got right once.

The KIND section needs a word. The current rubric's `architecture` / `contest` pair
is specific to that lens. The compiled rubric keeps three kinds with *fixed internal
names* — `core`, `adjacent`, `neither` — and the lens supplies the display words
the model sees ("architecture" / "contest" for the first preset). `selection.py`'s
balance rule then caps `adjacent` rather than the literal string `contest`. This
matters for a reason the README records: qwen3:30b rarely returns `neither` and files
most off-lens items as `contest`, so the balance rule is quietly doing off-lens
filtering. Renaming the slot preserves that behaviour exactly; renaming the *idea*
would not.

**Layer 3 — the file.** `lens.md` sits in the config directory and any editor opens
it. The README already promises "a plain markdown file you are expected to edit",
and the design keeps the promise. The two representations coexist by a simple rule:
the form writes both `lens.toml` and `lens.md`; a hand edit to `lens.md` is detected
by a stored hash, and the form then shows a banner — "this lens was edited by hand;
opening the form will replace those edits" — with the diff, and a button to go
ahead. The file is the truth; the form is a convenient way to write it.

### 2.3 Calibration: showing what you mean instead of writing it

This is what makes the layers above work for someone who cannot judge a prompt by
reading it. It reframes `scripts/eval_rubric.py` as a screen in setup.

The script today scores a model against twenty-five hand-labelled items in
`digest/tests/fixtures/eval_labels.json`. Those labels were made against *this*
rubric; they cannot score a different lens. So the app builds the user their own set:

1. After feeds are added, the app fetches them (the same fetch the run does) and
   picks twenty-five headlines spread across sources.
2. The user sees each headline with its blurb and three buttons: *Want it*, *Maybe*,
   *Skip*. Nothing about scores, kinds or novelty. Want maps to fit 3, Maybe to fit
   2, Skip to fit 0 or 1 — the coarse label is enough, because the number that
   matters in `eval_rubric.py` is not exact fit but "let in wrongly" and "dropped
   wrongly", and the script's own docstring says so.
3. The app runs the classifier over the same twenty-five with the current lens and
   shows a two-column result: *You wanted these; the lens would drop them* and *The
   lens would keep these; you skipped them*. Each row has the model's twenty-word
   reason.
4. Two actions follow. *Add as an example* puts a mislabelled headline straight into
   the matching form field, which is how the rubric gets its concrete examples
   without anyone inventing them. *Re-check* runs again.

The labels are stored in the state database (a new `labels` table) keyed by item id,
so they persist across lens edits and model changes, and the screen can be reopened
any time as "How well does the lens match me?". Every model comparison in the app
uses the user's own labels once they exist, and the shipped twenty-five only until
then.

A caution the design carries forward from the README: the labels are "one considered
reading of the rubric, not gospel". The UI says the same thing in plainer words —
"these are your calls; change any you want".

### 2.4 What was considered and rejected

- **Free-text description → model writes the rubric.** Tempting, and easy to build:
  ask "what do you want to read?", have the configured model draft the rubric. It
  is rejected as the *primary* path because the output quality depends on the very
  model whose quality the user cannot judge, and a rubric drafted by gemma3 would
  carry the abstraction habits the WRITER_NOTES in `synthesize.py` exist to fight.
  It is kept as a *secondary* helper inside the form: a "suggest examples" button
  that fills the example fields from the user's own recent headlines, which the user
  then keeps or deletes. The model fills the form, never the file.
- **A knob-based lens** (sliders for "how much politics", "how much finance"). No.
  The rubric's own bias note says the lens is the only criterion and a region must
  not inflate a score. Sliders would reintroduce keyword weighting by another route.
- **Presets only.** Too rigid; the owner's stated aim is other topics and regions.

## 3. The configuration model

### 3.1 Where it lives

An installed app cannot keep its config in a repository checkout. The app uses the
`platformdirs` package to find the conventional directory:

| Platform | Config | Data (state.db, logs, labels) |
| --- | --- | --- |
| macOS | `~/Library/Application Support/Digest/` | same |
| Linux | `~/.config/digest/` | `~/.local/share/digest/` |
| Windows | `%APPDATA%\Digest\` | `%LOCALAPPDATA%\Digest\` |

`DIGEST_HOME` overrides both, which is what the tests use and what a power user with
two lenses uses.

Note that on Linux the config path is the one `config.py` already lists second in
`DEFAULT_CONFIG_PATHS`, and the data path is exactly today's `STATE_DIR`, so an
existing Linux install moves nothing.

### 3.2 The files

Four files rather than one, because they change for different reasons and are
edited by different hands:

- `config.toml` — plumbing: models, outputs, delivery, schedule, `[advanced]`.
  Written by the app; readable by a person.
- `feeds.toml` — the `[[feed]]` list. Same shape as today's `[[sources]]` with two
  additions: `enabled = true` and `verified = "2026-09-04"` (the last date the app
  fetched it successfully).
- `lens.md` — the compiled rubric. The only file a non-technical user might
  reasonably be told about.
- `lens.toml` — the form's answers, plus `compiled_hash` for the hand-edit check.

A fifth file, `presets/`, is not in the config directory; presets ship in the
package and are copied in when chosen.

### 3.3 Validation and migration

Today `config.py` reads with `raw.get(...)` and casts. A typo in a key is silently a
default; a wrong type is a traceback. For an app with a form on top, the config
needs to produce field-level errors. The design uses `pydantic` models for the four
files (v2, one dependency, and it also gives the UI its form schema). The existing
dataclasses `RunCfg`, `ModelsCfg` and the rest stay as the *runtime* config the
pipeline receives, built from the validated models, so nothing in the stages
changes. Validation errors are shown in the UI next to the field, and on the command
line as "config.toml: models.classify — must name a model Ollama has".

Every file carries `schema_version = N` at the top. `digest/config/migrate.py`
holds numbered functions `m001_...`, `m002_...`, each taking a dict and returning a
dict; loading runs the ones above the file's version and writes the file back with a
`.bak` beside it. Version 1 is the first app release. Version 0 is the legacy
`digest.toml`, and `m001` is the importer described next.

The SQLite store gets the same treatment: `PRAGMA user_version` and a list of
migration scripts, run at `State.__init__`. Today's `CREATE TABLE IF NOT EXISTS`
becomes migration 1, so an existing database is version 1 without change.

### 3.4 How `digest.toml` maps on

The importer runs once, when the app starts and finds no `config.toml` but does find
`digest.toml` in the working directory or at `~/.config/digest/digest.toml`. It does
not delete the old file.

| `digest.toml` | Becomes |
| --- | --- |
| `[run] max_words, max_items` | `config.toml [output] minutes` (words ÷ 145, rounded) plus `[advanced] max_items` |
| `[run] weekday` | `[schedule] day` |
| `[run] output_dir` | `[output] folder` |
| `[run] ground*, search_backend, source_*, fetch_days, contest_share` | `[advanced]` unchanged |
| `[models]` provider and model names | `[models]` unchanged, then verified against detection on first run |
| `[models]` everything else | `[advanced]` unchanged |
| `[tts]`, `[pdf]` | `[output] audio`, `[output] pdf`, `[advanced] voice`, `[advanced] pdf_engine` |
| `[drive]` | `[delivery.drive]` unchanged |
| `[credentials]` key files | Read once; if a key is found there or in `.env`, the importer offers to move it to the credential store |
| `[[sources]]` | `feeds.toml [[feed]]`, each with `enabled = true` and no `verified` date until the first fetch |
| `digest/prompts/rubric.md` | `lens.md`, with `lens.toml` set to the "architecture of rule" preset. Since the shipped preset is the same bytes, the hash check passes. |
| `~/.local/share/digest/state.db` and `logs/` | Copied, not moved, to the new data directory. On macOS that directory changes (section 3.1), and without this the first run after upgrading would treat every headline as new and show items a second time. On Linux the path is unchanged and nothing is copied. |

### 3.5 Feeds: adding and verifying

`ingest.fetch_source` already knows what a bad feed looks like — it warns when a feed
"parsed N entries but contributed none", which is how the Nikkei feed was found to
carry no dates. That check moves up to the moment a feed is added. Pasting a URL and
pressing *Check* does one fetch (through the same `fetch_bytes`) and reports:

- Did it parse, and how many entries.
- How many entries carry a date. Zero dates means every item falls out at the
  freshness cutoff, and the app says so in those words.
- The median blurb length. Under 500 characters means every selected item will need
  grounding; under 100 means the writer will be working from headlines alone. The
  app says which.
- How many entries `is_promotional` would drop.
- The five most recent headlines, so the user can see it is the feed they meant.

The feed is saved with `verified` set to today. A feed that fails on a scheduled run
three weeks running is shown with a warning on the Feeds screen; the app never
silently drops it.

The `weight` field stays in `feeds.toml` but is not in the UI. It exists to break
dedupe ties toward the more trusted source; a user with two feeds would not know how
to set it and a default of 1.0 is right.

Two things stay fixed from the existing code and are worth naming. The
`PROMOTIONAL_TITLE` pattern in `ingest.py` carries Economist and FT specific strings
(`FirstFT`, `The Economist asks`). Those stay as the generic filter's built-in list,
because they are harmless on other feeds, and no per-feed skip pattern is exposed.
And the app reads only what the feed serves plus, when grounding, the article's own
page; it never bypasses a paywall, and the Attribution section of the README becomes
the About screen.

## 4. Model setup

### 4.1 Discovery

On first run and on the Models screen, `digest/discover.py` does three cheap things:

1. `GET http://localhost:11434/api/tags` — is Ollama up, and what is pulled. A
   connection refusal means "not running or not installed"; the screen distinguishes
   the two by looking for the `ollama` binary on PATH (and the app bundle on macOS)
   and offers the right next step: "Start Ollama" or "Install Ollama from ollama.com".
2. `POST /api/show` per model — the `capabilities` list. A model listing `thinking`
   gets `think = false` on every request, per the README's measurement (thinking on
   scored zero and dropped all eleven items that belonged). The user never sees this.
3. The machine's memory, from `os` / `psutil`, to say whether a 30-billion-parameter
   model is realistic. qwen3:30b is around twenty gigabytes resident; on a
   sixteen-gigabyte laptop the recommendation must not be it.

Discovery is injectable — it takes a `fetch` callable — so the tests hand it canned
`/api/tags` responses and never touch a socket.

### 4.2 Recommendation

A short table in code, `KNOWN_MODELS`, lists the models this project has measured
with the numbers from the README, and a small set it has not:

| Model | Role | Status |
| --- | --- | --- |
| qwen3:30b | filter | Measured: 19/25 exact, 0 dropped wrongly, 3 let in wrongly. Recommended filter where memory allows. |
| gemma3:27b | write | Measured against Gemini on the same week; the shipped default writer. |
| qwen3-coder | filter | Measured: 13/25 exact, 1 dropped wrongly. Not recommended. |
| Smaller Qwen and Gemma sizes | either | **Untested.** Offered with the label "not yet measured — run calibration first". |

The recommendation logic is: prefer a measured model that is pulled and fits in
memory; else a measured model that fits and can be pulled (with the download size
shown and a *Pull* button that streams `POST /api/pull` progress); else the largest
pulled model of a known family, marked untested; else no local option, and the
hosted path. The design does not invent scores for models nobody has measured. The
calibration screen is where a user finds out what an untested model does on their
own lens, and it is offered right after model choice for that reason.

### 4.3 No local model

When Ollama is absent or the machine is too small, setup says so plainly and offers a
hosted provider. The choice between them uses the repository's own measurements
rather than a preference:

- **Gemini.** The README records that in September 2026 the free tier "stopped being
  able to finish a run: a week is about sixty calls and the budget now runs out
  inside a couple of them", producing `[PARTIAL]` editions. A paid key works.
- **Anthropic.** Measured at about sixty cents a week with `claude-haiku-4-5` filtering
  and `claude-sonnet-5` writing, no pacing needed.

So the hosted default is Anthropic, with the measured cost shown, and Gemini offered
with a note that a free key will probably not finish a run. Both stay supported, and
the per-stage split stays: a common shape will be a small local filter with a hosted
writer, or the reverse, exactly as `ModelsCfg.provider_for` allows today.

The `google-genai` SDK is currently a hard dependency while `anthropic` is optional,
so an Ollama-only user installs Google's client. Both become extras (`digest[gemini]`,
`digest[anthropic]`), and the package installs the one the user picks during setup.

### 4.4 Keys

`credentials.py` looks in the environment, then `.env`, then a key file, then the
macOS Keychain. That order exists for the launchd case and it is right for a repo;
for an app it has two problems: it is macOS-only for the secure store, and a
non-technical user will not create `~/.config/digest/gemini_key` with mode 600.

The app uses the `keyring` package, which wraps the macOS Keychain, the Windows
Credential Locker and the Linux Secret Service behind one call. The key is typed once
into a password field in the UI and stored under service `digest`, account
`<provider>`. On a headless Linux with no Secret Service, `keyring` falls back to an
encrypted file it manages. The environment variable still wins when set, which keeps
the one-off override that the README describes and the scheduled job unaffected.
`.env` and the key files are read by the importer once, offered for migration, and
then not consulted again.

`doctor` keeps its current behaviour of printing only the last four characters.

### 4.5 Whether `eval_rubric.py` belongs in setup

Yes, but not as it stands. As a script it compares a model to the owner's labels
against the owner's rubric. In the app it becomes two screens that share one engine
(`digest/calibrate.py`, a move of the script's scoring into the package):

- *Does the lens match me?* — section 2.3. The user labels; the lens is scored.
- *Which model should filter?* — the same labels, run through each candidate model,
  reported as "dropped wrongly / let in wrongly / time for 25 items". Offered after
  model choice with a single *Compare* button, because a filter run over twenty-five
  items is under a minute on a local model and it is the only honest answer to "is
  the smaller model good enough for me?".

It is optional on first run — a user who picks a preset and a measured model can
skip straight to running a week — and it is prominent afterwards.

## 5. Packaging, distribution and scheduling

### 5.1 The recommended path

**A PyPI package installed as a tool with `uv`, from a one-line bootstrap per
platform.**

macOS and Linux:

```
curl -LsSf https://digest.example/install.sh | sh
```

Windows (PowerShell):

```
irm https://digest.example/install.ps1 | iex
```

The script installs `uv` if it is missing (uv's own installer), then runs
`uv tool install weekly-digest[ollama]` and finally `digest open`, which starts the
local server and opens the browser on the setup wizard. From then on the user has a
`digest` command on their path and a menu-bar-free app they reach at
`http://127.0.0.1:8765`.

For whom is this easy, and why. It is easy for anyone who has already installed
Ollama, which is the population this app is for: Ollama's own quick start is a
terminal command, so "paste one line" is not a new demand. It is easy for a Windows
user in the same sense, because the PowerShell one-liner is the same shape as uv's
own. It is *not* easy for someone who has never opened a terminal, and the design
says so rather than pretending — that user is served by the native bundle in a later
phase, if demand appears.

### 5.2 The alternatives, and why not

- **Native bundles (PyInstaller, Briefcase).** The best experience for the
  never-seen-a-terminal user and the most work: three build pipelines, an Apple
  Developer certificate and notarization (without which macOS shows "cannot be
  opened"), a Windows code-signing certificate (without which SmartScreen blocks the
  download), and a bundle north of a hundred megabytes. The core of this app is a
  scheduled job that has to run without the bundle open, which means a bundled app
  still has to install a launch agent — so the bundle solves the first five minutes
  and none of the rest. Deferred to a phase that only runs if the uv path is the
  thing stopping people.
- **Docker.** Worst of all for a non-technical user: Docker Desktop is a heavier
  install than anything here, Ollama on the host is reachable from a container only
  with an extra flag, the browser has to be opened by hand, and scheduling means
  another container. Fine for a home-server enthusiast, and a `Dockerfile` can exist
  for them, but not the recommended path.
- **`pip install` into a venv the user makes.** This is today's README. It is what
  the owner wants to stop asking of people.
- **Homebrew / winget / apt.** Good second-tier channels once a release exists;
  each wants a maintained formula and gives nothing over the uv line on day one.

### 5.3 The weekly schedule

`digest schedule install --day friday --hour 7` and the matching UI panel write the
platform's native job. The scheduled command is the absolute path uv gave the tool
(`~/.local/bin/digest` or `%USERPROFILE%\.local\bin\digest.exe`) with `run
--scheduled`, which behaves as today's launchd invocation does — no browser, log to
the data directory, deliver if configured.

- **macOS: launchd.** A plist at `~/Library/LaunchAgents/io.digest.weekly.plist`,
  generated rather than copied, so the paths are right. The existing plist's two
  hard-won comments carry over as behaviour: no key in the file (the key is in the
  Keychain), and `PATH` set explicitly because launchd starts with a bare
  environment. The job runs at the next opportunity if the machine was asleep at the
  hour, which launchd does for `StartCalendarInterval` on wake.
- **Linux: a systemd user timer**, with `Persistent=true` so a missed hour runs on
  next login. One caveat the app states: user timers run only while the user is
  logged in unless `loginctl enable-linger` has been run, and the app prints that
  command rather than running it, since it needs the user's decision. Where systemd
  is absent, a `crontab` line as the fallback.
- **Windows: Task Scheduler** via `schtasks /create`, weekly, with "run task as
  soon as possible after a scheduled start is missed" set.

`digest schedule status` and the UI panel read the job back and show the next run
time, so "is it actually going to run on Friday?" has an answer. The app never edits
a schedule it did not install.

## 6. The user interface

### 6.1 Stack

**A local web application: FastAPI serving Jinja-rendered HTML with htmx for the
parts that update in place, no build step, no Node, no JavaScript framework.**
`digest open` starts uvicorn on `127.0.0.1:8765` and opens the default browser. The
same process runs the pipeline in a background thread and streams progress over a
server-sent-events endpoint that htmx subscribes to.

Why this over the alternatives, weighed on what a non-technical person has to
install and on what the app has to do:

- **Textual (a terminal UI).** Installs with the package and needs nothing else,
  which is attractive. Rejected because a terminal is the thing the target user is
  being spared, a twenty-minute run with a progress bar in a terminal window is
  exactly the experience the owner has now, and the output of this app is a document
  — reading an edition in a TUI is worse than reading it in the browser that already
  renders the HTML the app emits.
- **A desktop toolkit (PySide6 / Qt, or Toga via Briefcase).** Native windows and
  menus. Rejected for install weight and packaging: the PySide6 wheel alone is over
  a hundred megabytes on every platform, and Toga's maturity is uneven outside
  macOS. Both force the native-bundle question immediately, and neither renders the
  emitted HTML edition better than a browser does.
- **Streamlit or NiceGUI.** Both give a browser UI fast. Streamlit re-runs the whole
  script on every interaction, which is the wrong model for a job that runs for
  twenty minutes and must not be restarted by a click; NiceGUI is sound but brings a
  Vue-based frontend stack and its own server, which is more moving parts than a
  page of htmx for the same result.
- **FastAPI + htmx** wins on: nothing to install beyond Python packages; the user's
  browser is the renderer; the edition the app emits is already an HTML page in the
  house style of `emit.py`, so the review screen is that page with a sidebar; the
  server is testable with `TestClient` and no network; and the run engine lives in
  one process that the CLI and the scheduler share.

The dependency cost is FastAPI, uvicorn, Jinja2 and a single htmx file vendored into
the package (no CDN, so the UI works offline exactly as the local pipeline does).

### 6.2 What the UI does

Six screens. The mockup shows the first four flows in full.

1. **Setup** (first run only, resumable): welcome → models found → pick a lens →
   add feeds → check the lens (optional) → done. Each step writes config as it goes,
   so closing the browser halfway loses nothing.
2. **This week** (home): the last edition's summary, the next scheduled run, a
   *Run now* button, and while a run is in progress the stage-by-stage progress with
   counts — "fetched 286 · 241 new · filtered 113 · 60 selected · grounding 38 ·
   writing 31 of 44". The counts come from the `RunResult` fields that exist today
   plus a progress callback (section 7).
3. **Review a week**: the edition as rendered by `emit.render_html`, with a side
   panel that is the `audit` command as a table — every item that was fetched, its
   fit and kind, and if it was dropped, at which stage and the reason string
   `selection.py` already writes. Entries carried in a reporter's own words are
   marked, as they are in the .md output. Buttons: re-render as PDF, make audio, open
   the folder, upload.
4. **Lens**: preset picker, the form, the calibration screen, and a *Open the file*
   link.
5. **Feeds**: the list with last-verified dates and last-run counts, add with check,
   enable/disable, remove.
6. **Settings**: models (with discovery and compare), outputs, delivery (Drive under
   "Advanced"), schedule, and an About page carrying the attribution text.

### 6.3 What the UI does not do

- **It does not edit an edition's text.** The app produces a briefing; it is not a
  document editor. A user who wants different words changes the lens and reruns.
- **It does not let the user override a classification.** "Keep this one anyway"
  would be a natural button and it is deliberately absent, because the stored
  classifications and the pure `select` function are what make `audit` reproduce a
  week. The right response to "the filter dropped something I wanted" is the
  calibration screen, which turns that item into a labelled example and improves
  the lens for every future week.
- **It does not run daily**, and has no "run for the last three days" control.
- **It does not show or edit the other four prompts** (`classify.md`, `cluster.md`,
  the two `synthesize_*.md`). They are the machinery, and they stay in the package.
- **It does not serve on the network, and has no accounts.**
- **It does not manage Ollama** beyond starting it if the binary is found and
  pulling a model on request. Installing Ollama is a link to ollama.com.

## 7. What has to change in the existing code

Honestly scoped, module by module. The stages themselves — `normalize`, `dedupe`,
`selection`, `cluster`, `ground`, `synthesize`'s guards and the carry logic — are
nearly untouched; the work is at the edges.

### 7.1 Things that fight the design, by file

- **`digest/config.py`.** `prompts_dir` points inside the package, so the rubric is
  not user-editable once installed. `DEFAULT_CONFIG_PATHS` looks at the working
  directory. The loader has no validation and no versioning. Replaced by a
  `digest/config/` package: `paths.py` (platformdirs), `schema.py` (pydantic),
  `migrate.py`, `legacy.py` (the importer), `runtime.py` (builds today's
  dataclasses). `Config.prompt("rubric.md")` becomes `Config.lens_text`; the four
  machinery prompts keep loading from the package.
- **`digest/prompts/classify.md`** hardcodes the `region`, `domain` and `kind` enums
  as literal JSON in the prompt text, and **`digest/classify.py`** duplicates them in
  `VALID_KINDS`, `VALID_REGIONS`, `VALID_DOMAINS` and `batch_schema`. Both become
  templated from the lens: the prompt gets `{regions}`, `{domains}`, `{kinds}`
  slots, and `batch_schema(count, lens)` builds the enums from the same source.
  `_coerce` clamps to the lens's lists.
- **`digest/selection.py`** compares `c.kind == "contest"`. Becomes `== "adjacent"`
  (the fixed internal slot), with the lens mapping display words to slots when the
  response is coerced.
- **`digest/emit.py`** — `REGION_NAMES` for the spoken "Next, East Asia" bridges is
  the region taxonomy again; it comes from the lens. The title "The weekly digest"
  becomes the lens name.
- **`digest/synthesize.py`** — `QUIET_WEEK` ("No structural change was reported that
  the lens would count") and `_fallback_frame` ("ordered by how much structure each
  one moves") are written in the first lens's vocabulary. Both become neutral
  wording ("Nothing this week met the bar the lens sets"). `_writer_notes` keys the
  weak-model rules on `provider == "ollama"`; that becomes a `tier` on the model
  entry in `KNOWN_MODELS` (`local` / `hosted`), so a small hosted model could get
  the notes and a strong local one could skip them, without changing the measured
  default.
- **`digest/prompts/synthesize_frame.md`** — "no region appears in more than three
  entries in a row" assumes regions exist; kept, since every lens has a region list,
  even if it is one entry.
- **`digest/credentials.py`** — macOS-only secure store, `.env` search relative to
  the config path. Replaced by `keyring` with the environment override kept; the old
  lookups survive only inside the legacy importer.
- **`digest/llm.py`** — no discovery. Gains nothing itself; `digest/discover.py` is
  new and reads Ollama's `/api/tags` and `/api/show`. `OllamaBackend` gains a
  `pull(model, progress)` for the UI. `make_backend` reads the key from the new
  credential module.
- **`digest/pipeline.py`** — `run()` is synchronous, has no progress hook and no way
  to cancel. It gains an optional `progress: Callable[[Stage, dict], None]` called
  at each stage boundary and once per entry written (the `log.info("entry %d/%d")`
  line already marks the spot), and a `cancel: threading.Event` checked at the same
  points. Both default to no-ops, so the CLI and every existing test are unchanged.
  A new `digest/jobs.py` owns the background thread, the single-run lock, and the
  event stream the UI reads.
- **`digest/state.py`** — no schema version. Gains `PRAGMA user_version`, a
  migrations list, a `labels` table for calibration, and a `runs` table (started,
  finished, status, counts) so the home screen can show history without parsing logs.
  One migration is load-bearing: every stored `classified` row (the `kind` column and
  the `json` blob) says `contest`, and after the slot rename `select` would apply the
  balance rule to nothing on a past week. Migration 2 rewrites stored kinds through
  the lens's display-word-to-slot map, so `audit` of an old week gives the same
  answer under the new code.
- **`digest/emit.py` `write_pdf`** — defaults to `html2pdf`, which is a script in the
  owner's `~/.local/bin` that nobody else has. Replaced by `digest/pdf.py`, which
  looks for a Chrome, Chromium or Edge binary in the usual places on each platform
  and runs it headless with `--print-to-pdf`, and reports "install Chrome or Edge to
  make PDFs" when none is found. WeasyPrint stays an extra for those who have it.
  The owner's environment note about `html2pdf` is about *this machine*; the app has
  to carry its own browser finder.
- **`digest/audio.py`** — needs `pydub`, which needs `ffmpeg`, which a non-technical
  user does not have. edge-tts writes MP3 chunks; the concatenation becomes a plain
  byte-append of the MP3 frames (the chunks are the same codec and bitrate), with
  the 400-millisecond silence replaced by a pause in the text. `ffmpeg` is then not
  required. Piper stays an advanced option.
- **`digest/deliver.py`** — unchanged in code, demoted in the UI. Gains one more
  method later if asked for (email via SMTP), not now.
- **`digest/__main__.py`** — grows `open`, `init`, `schedule`, `import`, `lens`
  subcommands; `doctor` learns to report discovery.
- **`digest/ingest.py`** — gains `probe(url) -> FeedReport` for add-time
  verification, built from the same `fetch_source` path. `PROMOTIONAL_TITLE` stays.
- **`scripts/eval_rubric.py`** — its scoring moves to `digest/calibrate.py`; the
  script becomes a thin wrapper that calls it with the shipped labels, so the
  README's command keeps working.
- **`scripts/score_prose.py`** — `SOURCE_NAME` hardcodes the current publishers and
  `RUBRIC_WORDS` the current lens's vocabulary. It is a developer tool and stays a
  script, but both lists should be read from the lens and the feed list when it is
  next touched. Not in scope for the app.
- **`pyproject.toml`** — the description carries the lens ("the architecture of rule,
  not the contest for it"), `google-genai` is a hard dependency, and the package
  name `weekly-news` is the repository's, not the tool's. Becomes `weekly-digest`
  with a `digest` console script, extras for each provider and for `ui`, `audio`,
  `pdf`, `drive`, and the prompts and presets as package data.
- **`scripts/io.digest.weekly.plist`** — becomes a template rendered by
  `digest/schedule.py`, not a file to copy.

### 7.2 Things that stay exactly as they are

Everything with a measured reason behind it: the batch size, the temperatures, the
thinking-off rule, the schema-not-just-JSON rule for Ollama, the quota breaker, the
DuckDuckGo pacing, the carry-before-cluster order, the invention guard and its
narrowness, the promotional-trailer filter, the dedupe marking of losers as seen. The
design read each docstring and found nothing it wanted to argue with; the changes
above are about *where* things are configured, not *what* the pipeline does.

### 7.3 Keeping the tests offline

The suite is fast because every network edge is a function that tests replace:
`fetch_bytes`, `urllib.request.urlopen`, the `Client`, and `ground=False` in the
`cfg` fixture. The new edges follow the same rule — `discover` takes a fetch
callable, `probe` uses `fetch_bytes`, `schedule` takes a runner for `launchctl` /
`systemctl` / `schtasks`, the UI is exercised through `TestClient` with a fake job
runner. And one addition makes the rule a property rather than a convention: an
autouse fixture in `conftest.py` that replaces `socket.create_connection` with a
function that raises. Any test that reaches the network then fails immediately with
a clear message, on every machine, including CI without network access.
