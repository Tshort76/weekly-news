#!/usr/bin/env python
"""Take a lens preset from written to measured.

Two steps, because the middle one is a person.

    python scripts/calibrate_preset.py sample <preset> --n 30 > headlines.json
    # ... someone labels each headline want / maybe / skip ...
    python scripts/calibrate_preset.py score <preset> labelled.json

`sample` draws headlines from the preset's *own* feeds, because a lens scored
against somebody else's week measures nothing. `score` runs the classifier with
that preset's lens and compares its keep/drop calls against the labels.

The labels are the instrument. A score with no labels beside it is a claim, and
`test_every_calibrated_preset_can_show_the_labels_it_was_scored_against` fails
the build for exactly that reason.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from digest import calibrate  # noqa: E402
from digest.classify import classify  # noqa: E402
from digest.config import Config  # noqa: E402
from digest.config import load as load_installed  # noqa: E402
from digest.ingest import sample as sample_feeds  # noqa: E402
from digest.lens import presets  # noqa: E402
from digest.lens.compile import compile_lens  # noqa: E402
from digest.llm import Client  # noqa: E402
from digest.models import Source  # noqa: E402


def _config_for(name: str, tmp: Path) -> Config:
    """The installed config, with this preset's feeds and lens swapped in.

    Built from the installed config rather than from `Config()` so the score is
    measured on the models actually in use. A bare `Config()` defaults to the
    hosted provider, which silently scored the first run of this script on
    Gemini rather than on the local model the app runs — a measurement of the
    wrong thing, and metered spend nobody asked for.
    """
    spec = presets.load(name)
    lens = tmp / f"{name}.md"
    lens.write_text(presets.markdown(name), encoding="utf-8")
    try:
        base = load_installed()
    except (FileNotFoundError, OSError, ValueError):
        base = Config()
        base.models.provider = "ollama"
    return dataclasses.replace(
        base,
        sources=[Source(name=f["name"], url=f["url"],
                        section=f.get("section", "other"), weight=f.get("weight", 1.0))
                 for f in spec.feeds],
        lens_path=lens,
        lens_spec_path=presets.spec_path(name),
    )


def do_sample(args) -> int:
    cfg = _config_for(args.preset, Path(args.tmp))
    items = sample_feeds(cfg, args.n)
    print(json.dumps({
        "preset": args.preset,
        "lens": presets.load(args.preset).name,
        "headlines": [
            {"id": i.id, "title": i.title, "source": i.source,
             "blurb": i.blurb[:400], "url": i.url}
            for i in items
        ],
    }, indent=2))
    return 0


def do_score(args) -> int:
    cfg = _config_for(args.preset, Path(args.tmp))
    payload = json.loads(Path(args.labels).read_text())
    rows = payload["labels"] if isinstance(payload, dict) else payload

    by_id = {r["id"]: r for r in rows if r.get("choice") in ("want", "maybe", "skip")}
    items = [i for i in _items_from(rows) if i.id in by_id]
    labels = calibrate.labels_from_choices({k: v["choice"] for k, v in by_id.items()})

    began = time.monotonic()
    results = classify(items, cfg, Client(cfg))
    report = calibrate.score(results, labels)
    report.seconds = time.monotonic() - began

    print(f"{args.preset}: {report.agreement} of {report.total} agree "
          f"({report.seconds:.0f}s, {cfg.models.classify})")
    print(f"  let in wrongly  {len(report.false_keeps)}")
    print(f"  dropped wrongly {len(report.false_drops)}")
    for title in report.false_keeps:
        print(f"    [in ] {title}")
    for title in report.false_drops:
        print(f"    [out] {title}")

    wanted_and_dropped = sum(
        1 for c in results
        if by_id.get(c.id, {}).get("choice") == "want" and not calibrate.kept(c.fit, c.novelty)
    )
    print(f"  wanted and dropped {wanted_and_dropped}   <- the number that matters")

    if args.save:
        _save(args.preset, report, rows, cfg, wanted_and_dropped, args.note)
        print(f"  saved digest/lenses/{args.preset}.md and .labels.json")
    return 0


def _items_from(rows: list[dict]):
    from datetime import datetime, timezone

    from digest.models import Item

    from digest.normalize import normalize_all

    items = [
        Item(id=r["id"], source=r.get("source", ""), section="other",
             title=r["title"], blurb=r.get("blurb", ""), url=r.get("url", ""),
             published=datetime.now(timezone.utc))
        for r in rows if r.get("choice") in ("want", "maybe", "skip")
    ]
    # Normalised, because the classifier reads normalised text in a real run and
    # a score taken on raw feed HTML measures a pipeline nobody runs.
    cleaned = normalize_all(items)
    keep = {i.id for i in items}
    for original, clean in zip(items, cleaned):
        clean.id = original.id  # normalize recomputes it from the url
    return [c for c in cleaned if c.id in keep]


def _save(name, report, rows, cfg, wanted_and_dropped: int, note: str) -> None:
    """Ship the bytes that were scored, not a recompile of the spec.

    Phase 0 measured that rewrapping the rubric with no word changed moves this
    classifier about ten points, so a preset that compiled its markdown at
    install time would hand the pipeline a variant nobody ever scored.
    """
    (presets.DIRECTORY / f"{name}.md").write_text(
        compile_lens(presets.load(name)), encoding="utf-8")
    (presets.DIRECTORY / f"{name}.labels.json").write_text(json.dumps({
        "_note": "The headlines this lens was scored against, and the score. "
                 "Drawn from this preset's own feeds. A score with no labels "
                 "beside it is a claim, not a measurement.",
        "model": cfg.models.classify,
        "measured": {
            "agreement": report.agreement, "of": report.total,
            "let_in_wrongly": len(report.false_keeps),
            "dropped_wrongly": len(report.false_drops),
            "wanted_and_dropped": wanted_and_dropped,
            "note": note,
        },
        "labels": [{"title": r["title"], "source": r.get("source", ""),
                    "choice": r["choice"], "why": r.get("why", "")}
                   for r in rows if r.get("choice") in ("want", "maybe", "skip")],
    }, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tmp", default="/tmp")
    sub = parser.add_subparsers(dest="command", required=True)

    draw = sub.add_parser("sample", help="draw headlines from the preset's own feeds")
    draw.add_argument("preset")
    draw.add_argument("--n", type=int, default=30)
    draw.set_defaults(func=do_sample)

    rate = sub.add_parser("score", help="score the lens against a labelled file")
    rate.add_argument("preset")
    rate.add_argument("labels")
    rate.add_argument("--save", action="store_true")
    rate.add_argument("--note", default="")
    rate.set_defaults(func=do_score)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
