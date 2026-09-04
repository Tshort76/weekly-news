# Phase 0 — can a form write the lens?

The whole authoring story in `open-source-design.md` rests on one claim: a rubric
assembled from form fields is applied as well as one a person wrote. If that is
false, the form design is wrong and phase 3 changes. This is the check, run before
any packaging work.

**Verdict: proceed.** The compiled rubric passes the bar, and the risk the phase was
built to retire is retired. But the measurement turned up something the design did
not anticipate, and it changes one decision — see "What this actually proved".

## What was built

- `digest/lens/schema.py` — `LensSpec`, the lens as form fields.
- `digest/lens/compile.py` — `compile_lens(spec)`, spec back to markdown.
- `digest/lenses/architecture-of-rule.toml` — the current rubric decomposed by hand.
- `digest/tests/test_lens.py` — nine offline tests.
- `scripts/eval_rubric.py` grew `--prompts-dir`, so a compiled lens can be scored
  without touching the shipped one.

`digest/prompts/rubric.md` was not modified.

## Result 1 — the compiler reproduces the words exactly

`compile_lens` of the decomposed preset differs from the hand-written rubric in
**line-break positions only**. Every word, in every section, in the same order.

Byte-for-byte was not achievable and should never have been the target. The
hand-written rubric is wrapped by a person: line 3 breaks at 82 characters where a
uniform wrap of any width would have fitted the next word. Reproducing that would
mean storing the line breaks, which means storing the prose, which defeats the
point of having a form at all.

**This contradicts a phase-1 acceptance test.** `implementation-plan.md` says "the
compiled first preset equals the shipped `rubric.md` bytes". It cannot. The test in
`test_lens.py` compares whitespace-normalised text instead.

## Result 2 — the model is sensitive to the line breaks

qwen3:30b, 25 labelled items, the same 25 for every row. Three runs of each of the
first two rows; run-to-run variance is at most one item.

| Rubric | Exact fit | Kind correct | Let in wrongly | Dropped wrongly |
| --- | --- | --- | --- | --- |
| Hand-written (as shipped) | 19–21/25 | 13–14/25 | 3–4 | **0** |
| Hand-written, via `--prompts-dir` (control) | 19/25 | 13/25 | 3 | **0** |
| Compiled | 21/25 | 23/25 | 2 | **0** |
| Only the KIND block rewrapped | 21/25 | 14/25 | 2 | **0** |
| Only the two prose blocks rewrapped | 20/25 | 22/25 | 0 | **2** |

The bar was: dropped-wrongly stays at zero, let-in-wrongly within one of the
hand-written count. The compiled rubric clears both — zero dropped, and two let in
against the hand-written three or four.

The control row is the important one. It is the shipped rubric served through the
same new code path, and it scores exactly like the shipped rubric served the old
way. So the difference between rows is the rubric text, not the plumbing.

And the only difference in that text is where the lines wrap. Rewrapping three
paragraphs moved kind agreement from 13/25 to 23/25, reproducibly. Rewrapping a
different subset of the same three paragraphs — the "only the prose blocks" row —
**dropped two items that belonged**, which is the one failure the bar exists to
catch, and it did so while looking better on two other columns.

## What this actually proved

The stated risk is retired: a form can produce a rubric this model applies at least
as well as the hand-written one. Nothing found here argues against the form.

A larger risk was uncovered in its place. Formatting changes with no semantic
content move this model's judgements by about ten points on kind — the same
magnitude as differences the project has treated as signal when choosing models and
prompts. Two consequences:

**Twenty-five labels are too few to gate on.** A ten-point swing is two or three
items. The design already has the user labelling 25 during calibration, which is
right for calibration — it is a conversation, not a measurement. Using the same 25
as an acceptance gate for a code change is asking more of it than it can bear.
Before phase 1 templates the enums in `classify.md` (its own stated risk, checked
the same way), the labelled set should be enlarged. A hundred items from a real week
is a morning's work and it is the instrument every later decision leans on.

**A preset should ship the file, not the recipe.** The design has presets shipping
`lens.md` plus the `lens.toml` that compiles to it. Given the above, an untouched
preset should hand the pipeline the exact bytes that were measured — the
hand-written file — and compile only once the user has actually edited the form.
Otherwise every preset silently ships a rewrapped variant nobody scored. The spec
stays beside it as the thing the form loads and saves. This costs nothing and
removes a whole class of "why did my digest change" that nobody would ever trace to
line wrapping.

## Not investigated

Why the wrapping matters. It reproduces on this model at temperature 0, and the
KIND block's rewrap on its own does almost nothing while the prose blocks' does a
lot, so the naive story — that the model reads the KIND list better on one line —
is wrong. Worth knowing eventually. Not worth blocking phase 1, because the design
already treats every prompt as measured rather than reasoned about, and this is one
more reason to keep doing that.

## Reproducing

```
mkdir -p /tmp/compiled-prompts && cp digest/prompts/*.md /tmp/compiled-prompts/
python -c "from digest.lens.schema import LensSpec; from digest.lens.compile import compile_lens; \
import pathlib; pathlib.Path('/tmp/compiled-prompts/rubric.md').write_text( \
compile_lens(LensSpec.from_toml('digest/lenses/architecture-of-rule.toml')))"
python scripts/eval_rubric.py --provider ollama --model qwen3:30b
python scripts/eval_rubric.py --provider ollama --model qwen3:30b --prompts-dir /tmp/compiled-prompts
```

---

# Postscript: calibrating a second lens (2026-09-04)

`what-became-possible` was taken from written-but-unchecked to calibrated, which
was also the first real exercise of the check-the-lens loop. Two things came out
of it, one expected and one not.

**The lens works as written.** Thirty headlines drawn from its own five feeds,
labelled by the lens's author against its stated criteria: **26 of 30 agreement,
reproduced exactly on two consecutive runs.** Every one of the four headlines
labelled "definitely want" was scored fit 3. One false positive — a trade
publication's product-category explainer scored fit 2 — and three "maybe" items
dropped, all of them genuinely borderline (a spacecraft arriving before it has
measured anything; two reports of the same observed model behaviour).

**Every attempt to improve it made it worse.** This is the unexpected part, and
it is the phase-0 sensitivity finding showing up in a place where it costs
something.

| Change | Agreement | Let in wrongly | Dropped wrongly |
| --- | --- | --- | --- |
| As written (twice) | **26/30** | 1 | 3 |
| Two permissive fit-2 examples added | 25/30 | 1 | 4 |
| **One** never-list entry added | 21/30 | 0 | 9 |
| Both together | 22/30 | 0 | 8 |

The third row is the one to look at. A single line — "product-category
explainers from trade publications" — added to a never-list that already had
seven entries removed the false positive it was aimed at and took six real items
down with it. It did not act as a targeted rule about trade publications. It
acted as a **global severity dial.**

Two consequences worth carrying:

- **The "add as an example" button in calibration is more dangerous than it
  looks.** It is presented as teaching the lens one thing, and it can move the
  whole scale. The screen should show the effect on the rest of the labelled set
  after an addition, not just accept it — otherwise a user fixes one annoyance
  and quietly loses six stories a week they will never know about. Filed as a
  follow-up rather than fixed here, because it changes what the screen is.
- **Ship the version that was measured, even when a defect is visible in it.**
  The instinct to fix the one false positive was right and the fix was worse than
  the flaw. The preset ships as scored, with its one known false positive
  recorded here and in the labels file beside it.
