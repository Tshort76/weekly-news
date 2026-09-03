"""One-off: synthesize the same selected/clustered items twice, once per
backend, so the two editions can be read side by side.

Reuses the classifications already stored for a week (the classify stage is
unchanged between backends and re-running it wastes quota) and reproduces the
exact cluster structure the earlier run actually used, via
`cluster_stage.singletons`, so only the synthesize stage differs between the
two outputs. Read-only against state: no save_edition, no mark_seen.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from digest import cluster as cluster_stage
from digest import config
from digest import emit as emit_stage
from digest import selection
from digest import synthesize as synth_stage
from digest.llm import Client
from digest.state import State


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", required=True)
    parser.add_argument("--provider", required=True, choices=["gemini", "anthropic", "ollama"])
    parser.add_argument("--model", required=True)
    parser.add_argument("--label", required=True, help="suffix for the output filenames")
    parser.add_argument(
        "--limit", type=int,
        help="write only the first N clusters. Selection and clustering are both "
             "deterministic, so the same N are the same entries every run — which is "
             "what makes a prompt A/B on a subset mean anything. Sixty entries on a "
             "local 27b model is over half an hour; ten is six minutes.",
    )
    args = parser.parse_args()

    cfg = config.load()
    cfg = dataclasses.replace(
        cfg,
        models=dataclasses.replace(
            cfg.models, synthesize_provider=args.provider, synthesize=args.model,
        ),
        run=dataclasses.replace(
            cfg.run, output_dir=cfg.run.output_dir / "compare",
        ),
    )

    with State(cfg.db_path) as state:
        classified = state.load_classified(args.week)
        if not classified:
            raise SystemExit(f"no classifications stored for {args.week}")

        selected, _ = selection.select(classified, cfg, state.prior_mechanisms(args.week))
        print(f"selected: {len(selected)} items (same selection as any other backend, pure fn)")

        # The Gemini acceptance run's clustering call failed and fell back to
        # singletons — reproduce that exactly so the entries line up 1:1
        # between the two editions and the comparison isolates synthesis.
        clusters = cluster_stage.singletons(selected)
        print(f"clusters: {len(clusters)} (singleton fallback, matching the Gemini run)")
        if args.limit:
            clusters = clusters[: args.limit]
            print(f"limited to the first {len(clusters)}")

        client = Client(cfg)
        edition = synth_stage.synthesize(
            clusters, cfg, client, args.week,
            prior_entries=state.prior_entries(args.week),
            degraded=False,
        )

    files = emit_stage.emit(edition, cfg, want_html=True, want_pdf=False)
    for kind, path in files.items():
        renamed = path.with_name(f"{path.stem}-{args.label}{path.suffix}")
        path.rename(renamed)
        print(f"{kind}  {renamed}")


if __name__ == "__main__":
    main()
