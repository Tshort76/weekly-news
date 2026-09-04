# Weekly News

A once-a-week, plain-text briefing of the world's notable events, filtered through a
single editorial lens:

> **The architecture of rule, not the contest for it.**
> How money, power, institutions and systems get *reorganised* — new rules, new
> constraints, new capacities, regime shifts in markets or states — rather than who is
> winning the fight over them.

It is written to be listened to. The primary output is prose for the ear, with a
sources appendix below it for the eye. It exists to replace a magazine subscription:
food for thought and conversational material, facts with a mechanism behind them,
never opinions.

Filtering is done by a language model reading a written rubric — never keyword
matching — and that rubric is a plain markdown file you are expected to edit. It is
the product; everything else is plumbing.

It reads only publicly served RSS feeds — headlines and blurbs, never article
bodies, never behind a paywall. See [Attribution](#attribution).

**What it does not do:** breaking news, daily cadence, full-text scraping of paywalled
articles, opinion, forecasting, horse-race politics, celebrity, or sport.

## Setup

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"          # add ",audio" / ",drive" as needed
```

### Where the API key goes

```bash
cp .env.example .env
$EDITOR .env                         # GEMINI_API_KEY=...
chmod 600 .env
python -m digest doctor              # confirms it without printing it
```

`.env` is gitignored and `.env.example` is the committed template. Note that
`.gitignore` only protects a directory that git is actually tracking — if you copy
this project somewhere new, `git init` before putting a key in it.

The key is looked up in this order, first hit winning:

1. `$GEMINI_API_KEY` in the real environment, so a one-off run can override
2. `GEMINI_API_KEY` in `.env` — beside `digest.toml` first, then the project root,
   then the working directory
3. `~/.config/digest/gemini_key`, or wherever `[credentials]` in `digest.toml` points
4. the macOS Keychain, service `digest-gemini` —
   `security add-generic-password -s digest-gemini -a "$USER" -w`

The real environment beating `.env` is the usual dotenv convention. The two options
below `.env` exist for cases where a key in the working tree is awkward: several
checkouts or worktrees of this project each need their own copy of `.env`, whereas
`~/.config` and the Keychain are shared by all of them and survive deleting the
directory. Use whichever suits you — the run reads all four the same way, and
`doctor` tells you which one it actually used.

Same four for `anthropic`, with `ANTHROPIC_API_KEY`, `anthropic_key`, and
`digest-anthropic`. A local Ollama provider needs no key at all, so a fully local
configuration never touches any of this.

**Do not put the key in the launchd plist.** That file is committed. It is also why
a key exported in `~/.zshrc` is not enough on its own: launchd starts with a bare
environment and never reads a shell profile, so an exported key works when you run
the command yourself and silently fails every Friday morning. All four options above
are read identically from both.

`python -m digest doctor` reports which providers each stage will use, whether the
key was found and where it came from, and whether each backend starts — printing
only the last four characters of the key, never the whole thing.

Get the key itself from [Google AI Studio](https://aistudio.google.com/apikey). It is free,
and separate from a Gemini Advanced subscription — a chat subscription grants no API
access. While you are there, open the
[rate-limit page](https://aistudio.google.com/rate-limit) and note your requests-per-minute
number; see *Rate limits* below for what to do with it.

Two things worth knowing about the free tier. Google may use free-tier prompts and
responses to improve their models — everything sent here is public RSS headlines and
one-line blurbs, so the exposure is small, but it is a fact rather than a footnote. And
the limits are per-account and not published, which is why the pacing below is
configurable rather than hard-coded.

To run on Claude instead, set `provider = "anthropic"` in `digest.toml` (the block is
written out in the comments there), `uv pip install -e ".[anthropic]"`, and export
`ANTHROPIC_API_KEY`. Measured cost on that path is about $0.60 a week.

## Running

```bash
python -m digest run                              # this week, txt + md, upload if enabled
python -m digest run --html --pdf --audio
python -m digest run --dry-run --no-drive         # write files, leave the state store alone
python -m digest classify-only --week 2026-W36    # fetch and filter, write no digest
python -m digest audit --week 2026-W36            # what was dropped, and why
python -m digest render --week 2026-W36 --html    # re-emit a stored edition
python -m digest speak --week 2026-W36            # audio from an existing .txt
python -m digest doctor                          # keys, providers and backends
```

Weekly on a Mac: edit the two paths and the key in
`scripts/io.digest.weekly.plist`, copy it to `~/Library/LaunchAgents/`, and
`launchctl load` it. On Linux use cron; on Windows, Task Scheduler.

## What a run does

```
ingest → normalize → dedupe → classify → select → cluster → synthesize → emit → deliver
```

Feeds come from `digest.toml`. Classification is a model judging title and blurb
against the rubric — never an article body, and never a keyword list. Selection applies
the fit threshold, the saga rule and the contest cap. Clustering and writing are two
further passes, so no single call has to hold the whole edition.

Which model does that work is a config choice, not a code one. Stages ask for
`"classify"` or `"synthesize"` and [`digest/llm.py`](digest/llm.py) maps that to a
provider and a model; nothing upstream of it knows who wrote the digest. Gemini and
Anthropic backends both ship. A measured week is 74 model calls: 12 to classify 286
items in batches, one to cluster, 56 to write entries, and one to frame them.

`seed` makes runs *usually* reproducible rather than guaranteed — it is a hint on this
API, not a promise. The real reproducibility mechanism is `audit`: every classification
is stored, so re-running the selection rules over a past week gives the same answer
regardless of what the model would say if asked again.

If a first real run ever fails with an unexpected request-shape error, the same SDK
still carries the older `client.models.generate_content` surface alongside
`client.interactions.create`, which is what [`digest/llm.py`](digest/llm.py) uses today.

## Running it locally, which is the default

Both stages run on local models through [Ollama](https://ollama.com). No key, no
spend, nothing leaves the machine:

```toml
[models]
provider = "ollama"
classify_provider = "ollama"
classify = "qwen3:30b"              # 286 headlines judged against the rubric
synthesize = "gemma3:27b"           # the writing
```

This started the other way round — filtering local, writing hosted, on the argument
that quality shows in the writing. Two things changed that. Google's free tier stopped
being able to finish a run: a week is about sixty calls and the budget now runs out
inside a couple of them, which produces a `[PARTIAL]` edition rather than a cheap one.
And most of the quality gap turned out to be fixable at this end rather than by paying
for a better model:

- The prompt says out loud what a weaker model gets wrong. Rules a hosted model already
  follows sit behind a `{writer_notes}` slot that only fills for a local one, so
  correcting one model's habits cannot cost the other anything.
- Sampling temperature is set. Unset, gemma3 runs at its Modelfile default of 1.0, and
  that alone accounted for a measurable slice of the difference.
- An entry naming an institution, law or treaty its own sources never mention is
  written again, and dropped if it does it twice. That was the one failure a reader
  could not catch, because the invented sentences are the fluent ones.
- Where a reporter already wrote the story, their words are published instead of a
  rewrite — so on a normal week the model writes about half the edition, and the half
  it does not write cannot be got wrong.

Set `provider = "gemini"` with `synthesize = "gemini-3.8-flash"` to put the writing
back on a hosted model. Nothing else needs to change, and with a paid key it works
properly.

### Fully local: what it costs you

A whole week written on `qwen3:30b` with no key at all produces a clean file. It
passes every mechanical criterion bar the odd unexpanded acronym: no URLs, no
markdown, no forecasting language, no parentheticals, inside the word ceiling.

The writing is the part that suffers, in three specific ways worth knowing before
you choose it. The opening becomes a list of headlines rather than a description of
the week's shape. Hooks tend to restate the body instead of adding a fact and its
mechanism. And where a blurb is thin, the model reaches for abstraction — "a
structural refinement in its operational framework" — instead of saying it has
little to go on. Where the blurb carries a hard number the entries are genuinely
good.

`scripts/eval_prose.py` writes the same three clusters with any set of models and
prints them together, flagging how much each hook merely repeats its body and which
abstraction words it leaned on:

```bash
python scripts/eval_prose.py qwen3:30b gemma3:27b
```

**Thinking cannot rescue this.** Any schema-constrained call with thinking on
returns an empty response on this model — synthesis included — so the ceiling above
is the ceiling.

### What the local models actually score

Measured against the labelled set on 2026-09-02, batches of 25, temperature 0. Of
those 25 items the rubric keeps 11:

| | qwen3:30b | qwen3-coder |
|---|---|---|
| exact fit | 19/25 (76%) | 13/25 (52%) |
| within one | 25/25 | 25/25 |
| kind correct | 13/25 (52%) | 22/25 (88%) |
| wrongly let in | 3 | 2 |
| **wrongly dropped** | **0** | **1** |
| time for 25 items | 43s | 43s |

`qwen3:30b` is the better classifier despite the worse `kind` score, because fit is
what drives selection and its errors all point the safe way: it lets a few extra
items through rather than dropping one that belonged. An extra entry is something
you skim past; a missing one you never know about.

Its `kind` weakness is specific and worth watching. It *rarely* returns `neither` —
none at all on the 25-item sample, and 25 out of 286 on a full week against 135
`contest` — so most off-lens items get filed as a contest. That only feeds the
contest balance rule, and on the sample every item that passed the fit threshold was
correctly called architecture, so nothing moved. If a real week starts dropping good
items to the balance rule, this is the reason, and the `KIND` section of
`prompts/classify.md` is the lever rather than the rubric.

On a full week — 286 items, twelve batches, about thirteen minutes on an M4 Pro
with nothing unjudged — `qwen3:30b` puts 40% of items at fit 2 or above, which is
the same proportion the hand-labelled sample has, and 6% at fit 3 against the
sample's 8%. `qwen3-coder` puts 57% above the threshold and 16% at fit 3.

The `max_items` cap binds either way and is meant to — it is the spec's hard limit
before clustering. The question is how much work the rubric did before the cap took
over: 113 candidates for 60 slots means the filtering was mostly editorial, 163
means the cap was doing the rubric's job.

**Reasoning models need `ollama_think = false`.** A thinking block in front of a
schema-constrained answer comes back empty rather than as an error, and every item
falls through unjudged. With thinking left on, `qwen3:30b` scored zero exact and
dropped all 11 items that belonged. Check `ollama show <model>` for `thinking` in
its capabilities.

Local models need the request constrained harder than hosted ones. Asked only for
JSON, a local model will answer a twenty-five-item batch with one object and stop;
given the array schema from `digest.classify.batch_schema` it returns all
twenty-five with the ids echoed correctly. That schema is passed to any backend
that can use it.

**Score a model before trusting it.** `digest/tests/fixtures/eval_labels.json`
holds twenty-five real items hand-labelled against the rubric, and:

```bash
python scripts/eval_rubric.py --provider ollama --model qwen3:30b --show
```

reports how close a model gets. The number that matters is not how often it hits
the exact fit score — it is `let in wrongly` and `dropped wrongly`, because those
are the items that do or do not reach your ears. Those labels are one considered
reading of the rubric; correct any you disagree with and every score moves with
them.

## Rate limits

None of this applies while both stages are local, which is the default. It matters
the moment you point a stage at a hosted model.

Google no longer publishes the free-tier numbers; they are per-account, and the only
authoritative source is your own dashboard at <https://ai.dev/rate-limit>. Check it
before your first real run, because the shape of the limit decides whether this works
on the free tier at all. Measured in September 2026 it did not: the budget ran out
within a call or two of starting, which is what moved the writing back off it.

There are two different limits and they need different handling.

A **per-minute** cap is what `min_interval_seconds` in `digest.toml` is for. It spaces
calls out — the shipped value of 12 seconds paces a run at 5 requests a minute. Lower
it if your dashboard says you have more room.

A **per-day** cap is not something pacing can solve. A full week costs roughly one
clustering call, one call per entry (up to 60), and one framing call — about 62 requests
against whatever your daily budget is. If that budget is smaller, most of the run cannot
succeed no matter how patiently it waits.

The awkward part is that the API reports both the same way: a 429 saying "please retry
in 55s", even when the budget it refers to renews tomorrow. So the run tells them apart
by experiment. It waits exactly as long as the server asks; if the very next call is
refused again, the window is not one that waiting will clear, and it stops calling that
provider for the rest of the run rather than spending another hour discovering the same
thing 60 more times. The edition still gets written, marked `[PARTIAL]`.

Short of that, a rate-limited call is simply retried — the server's own delay if it gave
one, capped at two minutes, up to five attempts — before the stage degrades. Raising
`synthesize_thinking` to `high` is the quickest way to start seeing limits, because
token caps count thinking tokens too.

If your daily budget is too small for a full week, the options are to move synthesis to
a local model (`synthesize_provider = "ollama"`, which is free and unlimited but slower),
to switch to a model with a larger free allowance, or to enable billing — measured cost
on the paid path is about $0.60 a week.

`--dry-run` still writes the files and the classifications, because `audit` reads the
classifications. What it withholds is the durable state: `seen`, `editions` and
`entries` are left untouched, so a dry run can be repeated and never hides an item
from next week's fetch. Items are marked seen only after the edition files are on
disk, so a crash mid-run costs nothing.

## Google Drive — the one-time setup

Delivery is off until you do this. Two ways; the first is the default.

**OAuth (default).** In the [Google Cloud console](https://console.cloud.google.com/), create a project, enable the Google Drive API, then create an OAuth client of type *Desktop app* and download its JSON to `~/.config/digest/credentials.json`. Open the Drive folder you want the digests in and copy the id out of its URL — the part after `/folders/`. Put that in `digest.toml` as `folder_id`, set `enabled = true`, then run `python -m digest run` once from a terminal where a browser can open: it walks the consent screen and caches a token at `~/.config/digest/token.json`. Every later run, scheduled ones included, uses the cached token and needs no browser.

**rclone (alternative).** If the OAuth flow is more trouble than it is worth: `brew install rclone`, `rclone config` to add a Drive remote, then set `method = "rclone"` and `rclone_remote = "gdrive:digests"` in `digest.toml`. `folder_id` is unused in this mode.

Either way, uploads are idempotent — re-running a week replaces that week's files rather than adding a second copy — and a failed upload never costs you the edition, because the local files are written first and the upload retries on the next run.

## Outputs

`~/digests/digest-2026-W36.{txt,md,html,pdf,mp3}`, plus the same files in the
configured Google Drive folder.

The `.txt` is the contract. Everything above the line of dashes is spoken prose — no
URLs, no markdown, no headers, acronyms spelled out, numbers phrased the way a person
says them. Below the dashes is a numbered sources appendix for the eye. The audio is
made from the part above the dashes only.

## State

SQLite at `~/.local/share/digest/state.db`: `seen` (never show an item twice),
`classified` (every verdict, so `audit` can explain any week), `editions` and `entries`
(saga detection and "since last week" diffs), `deliveries` (so an upload is idempotent).
Logs at `~/.local/share/digest/logs/<week>.log`.

## Failure behaviour

One dead feed warns and is skipped. Every feed dead aborts with a clear message. A
model call that fails after three attempts degrades rather than stopping: a failed
cluster call makes every item its own entry, a failed entry is skipped, a failed frame
falls back to fit order and stock wording. Any of those marks the edition `[PARTIAL]`
in its first line. A Drive failure leaves the local files in place and retries next run.

## Testing

```bash
python -m pytest digest/tests -q     # 134 tests, no network, no model calls
```

The classify and synthesize tests replay saved model responses from
`digest/tests/fixtures/`. Those responses are hand-authored to the right shape rather
than captured from a live call — replace them with real captures once you have run a
real week.

`scripts/check_spoken.py` checks a finished digest against the acceptance criteria:

```bash
python scripts/check_spoken.py ~/digests/digest-2026-W36.txt
```

It reads only the part above the line of dashes — the part that gets spoken — and
fails on URLs, markdown residue, unexpanded acronyms, forecasting language,
parentheticals and the word ceiling. It deliberately does not judge whether a hook
is a fact rather than a take, and says so, because that is the criterion a script
cannot check.

`scripts/eval_rubric.py` is the other half of testing, and the half that matters for
output quality: the unit tests prove the machinery works, the rubric eval proves the
model applies the lens. Re-run it whenever you edit the rubric or change models.

The provider layer is tested against fake SDK clients that record what would have gone
on the wire, so the request shape, the pacing, and the rate-limit handling are all
covered without a key. The shape itself was checked against the live endpoint: a
deliberately invalid key comes back as a server-side 400 carrying `API_KEY_INVALID`,
rather than a client-side validation error, which is how you can tell the request itself
was well-formed.

## Attribution

This project reads **publicly served RSS feeds** — headlines and the one-line blurbs
publishers put in those feeds. It never fetches, scrapes or stores article bodies, and
it never bypasses a paywall. Every generated entry links back to the original article,
and the `.txt` output carries a numbered sources appendix.

Headlines and blurbs remain the property of their publishers. The feeds configured by
default are:

- [The Economist](https://www.economist.com) — thirteen section feeds, the spine of the digest
- [Nikkei Asia](https://asia.nikkei.com)
- [Financial Times](https://www.ft.com) — world headlines
- [Semafor](https://www.semafor.com)

Change or remove any of them in `digest.toml`; nothing in the code assumes a
particular publisher. If you are a publisher and would rather not be in the default
list, open an issue and I will remove it.

The test fixtures in `digest/tests/fixtures/` contain a small sample of real feed
metadata — twenty-five headlines with their blurbs — kept so the rubric evaluation is
reproducible against real input rather than invented text.

Model providers, any one of which can run the whole thing:
[Google Gemini](https://ai.google.dev), [Anthropic Claude](https://www.anthropic.com),
and [Ollama](https://ollama.com) for local models. Built with
[feedparser](https://github.com/kurtmckee/feedparser),
[RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) and
[edge-tts](https://github.com/rany2/edge-tts).

## Licence

MIT — see [LICENSE](LICENSE).
