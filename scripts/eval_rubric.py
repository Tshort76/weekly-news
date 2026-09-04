#!/usr/bin/env python
"""Score a model's rubric judgement against hand-labelled items.

    python scripts/eval_rubric.py                      # whatever digest.toml says
    python scripts/eval_rubric.py --provider ollama --model qwen3:30b
    python scripts/eval_rubric.py --provider gemini --model gemini-3.8-flash

The headline number is `selection agreement`: of the items the labels say belong
in the digest, how many does this model also keep, and how much does it let in
that should have been dropped. Getting a fit score one off matters far less than
putting an ant-smuggling story in the briefing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from digest import config as cfgmod  # noqa: E402
from digest.classify import classify  # noqa: E402
from digest.llm import Client  # noqa: E402
from digest.models import Item  # noqa: E402

FIXTURES = ROOT / "digest" / "tests" / "fixtures"


def _plain(text: str) -> str:
    """Feeds mix curly and straight apostrophes; labels should not have to care."""
    return text.replace("\u2019", "'").replace("\u2018", "'")


def load_labelled() -> tuple[list[Item], dict[str, dict]]:
    items = [Item.from_dict(d) for d in json.loads((FIXTURES / "eval_items.json").read_text())]
    labels = json.loads((FIXTURES / "eval_labels.json").read_text())["labels"]
    by_id: dict[str, dict] = {}
    for label in labels:
        match = next(
            (i for i in items if _plain(i.title).startswith(_plain(label["title"]))), None
        )
        if match is None:
            raise SystemExit(f"no item matches label {label['title']!r}")
        by_id[match.id] = label
    return [i for i in items if i.id in by_id], by_id


def kept(fit: int, novelty: int) -> bool:
    """The selection rule, minus the saga and balance passes, which need history.

    Both sides of the comparison go through this, so the fit-1 novelty exemption
    is scored against a real labelled novelty rather than an assumed one.
    """
    return fit >= 2 or (fit == 1 and novelty == 3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--no-think", action="store_true",
                        help="disable a reasoning model's think block (Ollama)")
    parser.add_argument("--config", default=str(ROOT / "digest.toml"))
    parser.add_argument("--prompts-dir",
                        help="score a different rubric.md — a compiled lens, say")
    parser.add_argument("--show", action="store_true", help="print every item, not just the misses")
    args = parser.parse_args()

    cfg = cfgmod.load(args.config)
    if args.provider:
        cfg.models.provider = args.provider
    if args.model:
        cfg.models.classify = args.model
    if args.batch_size:
        cfg.models.classify_batch_size = args.batch_size
    cfg.models.min_interval_seconds = 0.0
    if args.prompts_dir:
        cfg.prompts_dir = Path(args.prompts_dir)
    if args.no_think:
        cfg.models.ollama_think = False

    items, labels = load_labelled()
    print(f"provider {cfg.models.provider}  model {cfg.models.classify}  items {len(items)}\n")

    started = time.time()
    results = classify(items, cfg, Client(cfg))
    elapsed = time.time() - started

    exact = off_by_one = over = under = kind_ok = novelty_ok = 0
    false_keeps: list[str] = []
    false_drops: list[str] = []
    rows = []

    for c in results:
        label = labels[c.id]
        delta = c.fit - label["fit"]
        exact += delta == 0
        off_by_one += abs(delta) == 1
        over += delta > 0
        under += delta < 0
        kind_ok += c.kind == label["kind"]
        novelty_ok += c.novelty == label["novelty"]

        want = kept(label["fit"], label["novelty"])
        got = kept(c.fit, c.novelty)
        if got and not want:
            false_keeps.append(c.item.title)
        if want and not got:
            false_drops.append(c.item.title)
        mark = "  " if delta == 0 else ("^^" if delta > 0 else "vv")
        rows.append(f"  {mark} said fit{c.fit} {c.kind:<12} | label fit{label['fit']} {label['kind']:<12} | {c.item.title[:52]}")

    n = len(results)
    print(f"time              {elapsed:.0f}s  ({elapsed / max(n, 1):.1f}s per item)")
    print(f"exact fit         {exact}/{n}  ({exact / n:.0%})")
    print(f"within one        {exact + off_by_one}/{n}  ({(exact + off_by_one) / n:.0%})")
    print(f"kind correct      {kind_ok}/{n}  ({kind_ok / n:.0%})")
    print(f"novelty exact     {novelty_ok}/{n}  ({novelty_ok / n:.0%})")
    print(f"over-scored       {over}/{n}     under-scored {under}/{n}")
    print()
    wanted = sum(1 for c in results if kept(labels[c.id]["fit"], labels[c.id]["novelty"]))
    print(f"the rubric keeps  {wanted}/{n} of these items")
    print(f"let in wrongly    {len(false_keeps)}  <- these would appear in the digest")
    for title in false_keeps:
        print(f"    {title[:70]}")
    print(f"dropped wrongly   {len(false_drops)}")
    for title in false_drops:
        print(f"    {title[:70]}")

    if args.show:
        print("\nevery item (^^ over-scored, vv under-scored):")
        print("\n".join(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
