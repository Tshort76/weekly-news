#!/usr/bin/env python
"""Write the same entries with several models and print them side by side.

    python scripts/eval_prose.py qwen3:30b gemma3:27b
    python scripts/eval_prose.py --provider gemini gemini-3.8-flash

Classification can be scored against labels; prose cannot. This prints the same
clusters written by each model so the difference is visible, and flags the
mechanical faults that are checkable — a hook that merely restates the body, the
abstraction words a model reaches for when it has nothing concrete to say, and
acronyms left unexpanded.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from digest import config as cfgmod  # noqa: E402
from digest.llm import Client  # noqa: E402
from digest.models import Cluster, Classified, Item  # noqa: E402
from digest.synthesize import write_entry  # noqa: E402

# Three clusters chosen to differ in how much the blurb actually gives you: one
# with a hard number, one with a legal outcome, one that is mostly abstract. A
# model that writes well on the first and badly on the third is padding.
CLUSTERS = [
    ("China solar overtakes coal", "policy-driven renewable capacity build", [
        ("Solar overtakes coal as China's biggest source of power capacity",
         "Installed solar is now nearly a third of the total, up from almost nothing a decade ago.",
         "east_asia", "energy")]),
    ("Google keeps its advertising business", "remedy stops short of divestiture", [
        ("Judge rejects bid to break up Google's advertising arm",
         "The court ordered behavioural remedies instead of a forced sale of the ad exchange.",
         "us", "tech")]),
    ("China industrial policy", "state-directed credit allocation", [
        ("China leans on industrial policy to revive growth",
         "Officials point to state-directed investment in advanced manufacturing.",
         "east_asia", "industry")]),
]

ABSTRACTION = re.compile(
    r"\b(structural|structure|framework|landscape|ecosystem|paradigm|dynamic|"
    r"architecture of|operational|systemic|holistic)\b", re.IGNORECASE)
ACRONYM = re.compile(r"\b([A-Z]{2,5})\b")
ACRONYM_OK = {"US", "UK", "EU", "UN", "AI", "OK", "IT", "A", "I"}


def build(title: str, mechanism: str, stories: list) -> Cluster:
    items = []
    for n, (headline, blurb, region, domain) in enumerate(stories):
        items.append(Classified(
            item=Item(id=f"x{n}", source="Economist", section=domain, title=headline,
                      blurb=blurb, url=f"https://e.com/{n}",
                      published=__import__("datetime").datetime.now(
                          __import__("datetime").timezone.utc)),
            fit=3, kind="architecture", novelty=3, region=region, domain=domain,
            mechanism=mechanism, reason=""))
    return Cluster(cluster_id="c1", title=title, items=items, shared_mechanism=mechanism)


def overlap(a: str, b: str) -> float:
    """How much of the hook is just the body again."""
    wa = {w.lower().strip(".,") for w in a.split() if len(w) > 4}
    wb = {w.lower().strip(".,") for w in b.split() if len(w) > 4}
    return len(wa & wb) / max(len(wb), 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("models", nargs="+")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--config", default=str(ROOT / "digest.toml"))
    args = parser.parse_args()

    clusters = [build(*c) for c in CLUSTERS]

    for model in args.models:
        cfg = cfgmod.load(args.config)
        cfg.models.synthesize_provider = args.provider
        cfg.models.synthesize = model
        cfg.models.min_interval_seconds = 0.0
        client = Client(cfg)

        print("=" * 78)
        print(f"{args.provider} / {model}")
        print("=" * 78)
        started = time.time()
        for cluster in clusters:
            entry = write_entry(cluster, cfg, client, [])
            if entry is None:
                print(f"\n[{cluster.title}] FAILED to produce an entry\n")
                continue
            abstractions = sorted({m.group(0).lower() for m in ABSTRACTION.finditer(
                entry.body + " " + entry.hook)})
            acronyms = sorted({a for a in ACRONYM.findall(entry.body + " " + entry.hook)
                               if a not in ACRONYM_OK})
            print(f"\n[{cluster.title}]")
            print(f"  headline  {entry.headline}")
            print(f"  body      {entry.body}")
            print(f"  hook      {entry.hook}")
            for q in entry.questions:
                print(f"  question  {q}")
            print(f"  -- hook repeats {overlap(entry.hook, entry.body):.0%} of the body's"
                  f" content words; abstraction words {abstractions or 'none'};"
                  f" unexpanded {acronyms or 'none'}")
        print(f"\n  ({time.time() - started:.0f}s for {len(clusters)} entries)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
