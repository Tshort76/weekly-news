You are organizing the items that survived the filter for a weekly briefing.

Group items that describe the same event, or that are separate events sharing one
causal mechanism, into clusters. A cluster of one is fine and common — do not force
unrelated items together. Two items about the same country are not a cluster unless
they share an event or a mechanism.

<items>
{items}
</items>

Return a JSON array and nothing else — no prose, no markdown fence:

[
  {{
    "cluster_id": "c1",
    "title": "<=10 words naming what this cluster is about",
    "item_ids": ["<id>", "..."],
    "shared_mechanism": "<=12 words, or null if the items only share an event"
  }}
]

Every item id given above must appear in exactly one cluster.
