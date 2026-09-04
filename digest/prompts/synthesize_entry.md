You write one item of a weekly briefing that is listened to, not read. The lens the
briefing is written through:

<rubric>
{rubric}
</rubric>

Write about this cluster of stories:

<cluster>
{cluster}
</cluster>

{prior_coverage}

Return a single JSON object and nothing else — no prose, no markdown fence:

{{
  "headline": "<=12 words, sentence case, no trailing period, factual, no puns, no colons-as-cleverness",
  "body": "2 to 5 sentences",
  "hook": "one sentence",
  "questions": ["0 to 2 open questions"]
}}

The body says: what changed structurally; why that matters; which larger system it
sits inside.

The note above tells you whether earlier coverage of this mechanism exists. Only when it
hands you an actual earlier headline and hook does one sentence say what is different
from what was said then — otherwise say nothing about prior coverage, other editions, or
what is "unlike" earlier reporting.

The hook is one sentence a listener could repeat in conversation: a concrete fact
plus the mechanism behind it. Never a take, never a prediction, never "some say" or
"analysts expect".

Questions are posed, not answered. They are what the item genuinely leaves open. Most
items get one question or none. Two is the ceiling, not the target, and an item with
nothing open gets an empty list. A question that would fit any item is filler: "how will
this affect X", "what are the implications for Y", "could this model spread to Z". A real
question names something specific the stories mention and says what about it is unknown.

How to write, because this is read aloud:
- Third person throughout. No "we", no "you".
- No forecasts, no adjectives of approval or disapproval, no opinion.
- No parentheticals, no dashes standing in for parentheses, no semicolons.
- Spell out an acronym the first time it appears. Country and bloc abbreviations are
  never used at all: write "United States", not "US" or "U.S.", "European Union", not
  "EU", and "artificial intelligence", not "AI", every time and in every field — the
  headline, hook and questions are read aloud too, and a speech engine reads "US" as
  the word "us".
- No URLs, no citations, no source names in the body — the appendix carries those.
- Numbers phrased the way a person says them: "about a third", "twelve billion
  dollars", "roughly one in five". No digits and no currency symbols anywhere, including
  the headline and hook: "seventeen billion dollars", never "$17 billion" or "17bn". A
  year is the one exception. A figure is the same figure in the headline, the body and
  the hook — never round or restate it differently in one of them.
- Only what you were given supports. Where the cluster carries the story's full
  text, or what other outlets reported, those count as given and you may use them —
  the other outlets are corroboration, not the source, so never name one in the
  body. Do not invent detail — and detail you know
  from elsewhere counts as invented here, even when it is true. If a law, an institution,
  a date, a statistic, a past event or a cause is not in the titles and blurbs, it does
  not go in the entry.
{writer_notes}
