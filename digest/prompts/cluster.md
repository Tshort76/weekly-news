You are organizing the items that survived the filter for a weekly briefing.

Group items that describe the same event, or that are separate events sharing one
causal mechanism, into clusters. A cluster of one is fine and common — do not force
unrelated items together. Two items about the same country are not a cluster unless
they share an event or a mechanism.

A subject is not a mechanism, and this is the whole of the task. "China", "finance",
"technology", "trade and security" name what items are about; a mechanism is the
thing that is happening in more than one place at once — a state directing credit to
chosen industries, a court narrowing what a regulator may do, a currency peg being
defended by selling reserves. Groups like "US Policy & Finance", "Industry Specific
Trends" or "Demographic & Social Issues" are folders, not clusters, and a folder is
worse than no grouping at all because it tells the writer these items belong
together when they do not.

So the test is one sentence: name the mechanism in twelve words. If you cannot,
the items do not belong together, and each one is its own cluster of one. Most
weeks most items are clusters of one, and an answer that is mostly clusters of one
is the expected answer, not a failure to try.

<items>
{items}
</items>

Return a JSON array and nothing else — no prose, no markdown fence:

[
  {{
    "cluster_id": "c1",
    "title": "<=10 words naming what this cluster is about",
    "item_ids": ["<id>", "..."],
    "shared_mechanism": "<=12 words naming the mechanism; null ONLY when the items
       report one and the same event"
  }}
]

Every item id given above must appear in exactly one cluster.
