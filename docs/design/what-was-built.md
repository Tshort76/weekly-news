# What was built, and where it differs from the design

`open-source-design.md` and `implementation-plan.md` are the proposal. This is the
record of what exists, written after building all five phases, so that the next
person reads the plan knowing which parts of it survived contact.

Everything the plan called for is built except where noted below. The suite is
328 tests, runs in about a second, and now fails any test that opens a socket
rather than merely being expected not to.

## Where the build departed from the plan

**No pydantic.** The design proposed it for config validation and for giving the
form its schema. Validation here is types, choices and ranges — eighty lines of
stdlib in `config/schema.py` — against the heaviest dependency in a tool whose
selling point is a one-line install. The named field-level errors the design
wanted are there (`config.toml: models.provider — must be one of …`), including
for a misspelled key, which the old `raw.get()` loader silently read as a
default. Revisit if the form ever needs something structural.

**No htmx.** The design vendored it as a static file. Vendoring means shipping a
blob nobody in the repository can read, and what these pages actually need is an
`EventSource` and about sixty lines, which are in `digest/ui/static/app.js`. No
front-end build, no CDN, nothing to audit but the file itself.

**The package is still `weekly-news`.** The design proposed renaming it to
`weekly-digest`; the owner declined. The console script is `digest` either way,
and nothing else depended on it.

**Byte equality with `rubric.md` was never reachable.** Phase 1's acceptance test
said the compiled first preset should equal the shipped rubric's bytes. It
cannot: the rubric is wrapped by hand, and line 3 breaks where a uniform wrap of
any width would have fitted the next word. The test compares whitespace-
normalised text instead, and phase 0 recorded why that matters more than it
sounds.

**The `classify.md` risk was retired more cheaply than planned.** The plan said to
re-run `eval_rubric.py` after templating the enums and require the same numbers.
For the shipped lens the templated prompt renders to the same bytes it had
before, so the test compares the rendered prompt. That is stronger than a score —
the model sees literally nothing different — and it needs no Ollama, so it runs
in the ordinary suite.

## What is built but not measured

**All four presets are calibrated as of 2026-09-06.** Each was scored against
thirty headlines drawn from its own feeds and labelled by hand, and each ships
the measured markdown plus the labels it was scored against, so `calibrated()`
reads a fact rather than a claim.

Measuring the last two found that two of them shipped feeds that did not match
their own lenses — a money-plumbing lens reading general world news, a climate
lens reading general technology — which is exactly the "template with the topic
swapped in" the design forbids, and which nothing but drawing their own week
would have revealed. Both feed lists were replaced and every URL fetched before
it was added. `docs/design/phase-0-lens-compiler.md` has the numbers and the two
lens-wording defects the labelling turned up.

**Their feeds are verified as of 2026-09-06.** All eight URLs across
`plumbing-not-prices` and `capacity-not-targets` were fetched and all eight
answered. Two are worth knowing about before anyone relies on them: the four
Economist section feeds carry 56-character blurbs, so every item from them is
looked up before it is written, and Carbon Brief publishes about twelve items at
a time with only five in a typical week — a real feed, but a thin one on its own.

**Windows is untested.** Every path, console and scheduler assumption was made on
a Mac. `schedule.Schtasks` generates the right argument list, including quoting a
path with a space in it, and no one has run it on Windows. The plan budgeted two
days for this and expected surprises; that budget is unspent.

**Distribution is the git URL, not a package index.** The plan ended at a PyPI
release; the owner decided on 2026-09-06 to skip it for now. The installers and
the README install from `git+https://github.com/Tshort76/weekly-news`, which `uv`
handles exactly as it handles an index, so nothing about the install line is
worse for it — the repository is public and it is one command either way.

Two things this costs, both accepted. There is no Homebrew formula or winget
manifest, since both need a released tarball and its hash. And there is no
version to bump between installs, which means `uv` can serve a cached build:
`uv tool install --force` reinstalls what you already had and says nothing, so
an update is `--reinstall`, and `uv cache clean weekly-news` when even that looks
stale. That bit twice while migrating the owner's own setup, once with the
installed app silently a commit behind.

**The bundle was not built.** A signed, notarized macOS app was always
conditional on the terminal install line being what stops people, and nobody has
watched anybody try yet.

## What was found while building

Two bugs the tests caught rather than a user:

- **The importer never stamped a hash on the lens it wrote**, so the hand-edit
  check had nothing to compare against and would have silently overwritten
  somebody's edits. "No hash recorded" read exactly like "unchanged".
- **The kind rename needed a database migration.** Every stored row said
  `contest`; after the rename `select` looks for `adjacent`, so an `audit` of a
  past week would have quietly capped nothing. Migration 2 rewrites both the
  column and the JSON blob.

And one thing worth keeping in mind for any future prompt work, from phase 0:
this classifier moves about ten points on rubric line-wrapping alone, with no
word changed. That is why presets install the measured bytes rather than a
recompile, and why the labelled set is too small to gate a change on.
