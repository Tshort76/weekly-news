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

**Three of the four presets are uncalibrated.** `what-became-possible`,
`plumbing-not-prices` and `capacity-not-targets` are written, have the right
shape, ship feeds, and have been scored against nothing. The design said a preset
should never be a template with the topic swapped in, and these are closer to
that than anyone would like. So the app labels them: `presets.calibrated()` is
true only when a measured `.md` ships beside the spec, which is the same file the
app installs, so the flag cannot drift from the fact. The lens picker and
`digest lens list` both say which is which.

Making one calibrated is a morning: point its feeds at a real week, label
twenty-five headlines on the check-the-lens screen, adjust, and save the scored
markdown beside the spec.

**Their feeds are unverified.** Feed URLs in the three new presets were written,
not fetched. `digest feeds check` and the Feeds screen fetch one and report what
it would contribute, which is where a wrong URL surfaces.

**Windows is untested.** Every path, console and scheduler assumption was made on
a Mac. `schedule.Schtasks` generates the right argument list, including quoting a
path with a space in it, and no one has run it on Windows. The plan budgeted two
days for this and expected surprises; that budget is unspent.

**No release.** Nothing is on PyPI, so the one-line installers name a package
that does not resolve yet, and there is no Homebrew formula or winget manifest —
both need a released tarball and its hash. `uv tool install` from a checkout
works today.

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
