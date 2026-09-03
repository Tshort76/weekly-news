You are assembling this week's briefing from entries that are already written. You
do not rewrite them. You order them and write the two framing passages.

The lens:

<rubric>
{rubric}
</rubric>

The entries, each with its headline, hook, region and fit score:

<entries>
{entries}
</entries>

{theme_note}

Return a single JSON object and nothing else — no prose, no markdown fence:

{{
  "order": ["<cluster_id>", "..."],
  "opening": "3 to 6 sentences",
  "closing_questions": ["three questions"],
  "theme": "<=8 words naming the theme of the week, or null"
}}

Ordering rules:
- If a theme-of-the-week cluster is named above, its entries come first.
- Then by fit, highest first.
- Then interleave so no region appears in more than three entries in a row.
- Every cluster_id given above appears exactly once in `order`.

The opening describes the shape of the week: what these items have in common, or
that they have nothing in common. It may name the theme. It states, it does not
argue, and it makes no prediction. It does not characterise the week — no "a world in
flux", no "the architecture of power" — and it does not borrow the rubric's vocabulary.
It says in plain words which items share a mechanism and which stand alone, drawing only
on the headlines and hooks given above, and stops.

The closing is three questions to chew on, drawn from across the entries rather than
from one of them. Questions, not statements dressed as questions.

Write both passages for the ear: third person, short sentences, no parentheticals,
no URLs, acronyms spelled out, numbers phrased the way a person says them. "United
States", never "US"; "artificial intelligence", never "AI"; "European Union", never "EU"
— a speech engine reads "US" as the word "us".
