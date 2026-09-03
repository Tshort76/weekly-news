"""CLI entry point.

    python -m digest run [--week 2026-W36] [--html] [--pdf] [--audio] [--no-drive] [--dry-run]
    python -m digest classify-only --week ...
    python -m digest audit --week ...
    python -m digest render --week ... --html --pdf
    python -m digest speak --week ...
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_module
from . import emit as emit_stage
from . import pipeline
from .logging_setup import setup as setup_logging
from .state import State


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="digest",
        description="Weekly world digest — the architecture of rule, not the contest for it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n", 2)[2],
    )
    parser.add_argument("--config", help="path to digest.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="fetch, filter, write and deliver a week")
    run.add_argument("--week", help="ISO week, e.g. 2026-W36 (default: this week)")
    run.add_argument("--html", action="store_true")
    run.add_argument("--pdf", action="store_true")
    run.add_argument("--audio", action="store_true")
    run.add_argument("--no-drive", action="store_true")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="write files and classifications but leave the state store untouched",
    )

    classify_only = sub.add_parser(
        "classify-only", help="fetch and classify, write no digest"
    )
    classify_only.add_argument("--week")

    audit = sub.add_parser("audit", help="print what was dropped from a week, and why")
    audit.add_argument("--week")

    render = sub.add_parser("render", help="re-emit a stored edition")
    render.add_argument("--week")
    render.add_argument("--html", action="store_true")
    render.add_argument("--pdf", action="store_true")

    speak = sub.add_parser("speak", help="make audio from an existing .txt")
    speak.add_argument("--week")

    sub.add_parser("doctor", help="check credentials, feeds and local models")

    return parser


def _report(result: pipeline.RunResult) -> None:
    edition = result.edition
    print(f"\nWeek {result.week}")
    print(f"  fetched      {result.fetched}")
    print(f"  after dedupe {result.kept_after_dedupe}")
    print(f"  selected     {result.selected}")
    print(f"  entries      {len(edition.entries)}")
    print(f"  words        {edition.word_count}")
    if edition.theme:
        print(f"  theme        {edition.theme}")
    if edition.partial:
        print("  PARTIAL      some items could not be written")
    if edition.quiet:
        print("  quiet week   nothing met the bar")
    for ext, path in sorted(result.files.items()):
        print(f"  {ext:<12} {path}")


def doctor(cfg) -> int:
    """Check everything a run needs, without spending anything or printing a key."""
    from .credentials import resolve  # noqa: PLC0415
    from .llm import Client, LLMError  # noqa: PLC0415

    problems = 0
    print("providers")
    for stage in ("classify", "synthesize"):
        provider = cfg.models.provider_for(stage)
        model = cfg.models.classify if stage == "classify" else cfg.models.synthesize
        print(f"  {stage:<11} {provider:<10} {model}")
        if provider == "ollama":
            continue
        key, source = resolve(
            provider, cfg.credentials.key_file(provider), cfg.config_path
        )
        if key:
            print(f"  {'':<11} key from {source} — {len(key)} characters ending {key[-4:]}")
        else:
            problems += 1
            print(f"  {'':<11} NO KEY FOUND")

    print("\nbackends")
    client = Client(cfg)
    for stage in ("classify", "synthesize"):
        try:
            print(f"  {stage:<11} {client.backend_for(stage).name} ready")
        except LLMError as exc:
            problems += 1
            print(f"  {stage:<11} FAILED\n{exc}")

    print("\nfeeds")
    print(f"  {len(cfg.sources)} configured; run `classify-only` to exercise them")

    print("\noutput")
    print(f"  digests   {cfg.run.output_dir}")
    print(f"  state     {cfg.db_path}")
    print(f"  drive     {'enabled' if cfg.drive.enabled else 'disabled'}")

    print("\n" + ("all good" if problems == 0 else f"{problems} problem(s) above"))
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config_module.load(args.config)
    week = getattr(args, "week", None) or pipeline.iso_week()
    log_path = setup_logging(cfg.log_dir, week, args.verbose)

    with State(cfg.db_path) as state:
        if args.command in {"run", "classify-only"}:
            result = pipeline.run(
                cfg,
                state,
                week=week,
                want_html=getattr(args, "html", False),
                want_pdf=getattr(args, "pdf", False),
                want_audio=getattr(args, "audio", False),
                dry_run=getattr(args, "dry_run", False),
                no_drive=getattr(args, "no_drive", False),
                classify_only=args.command == "classify-only",
            )
            if args.command == "classify-only":
                print(
                    f"\nWeek {week}: classified {result.kept_after_dedupe} items "
                    f"(fetched {result.fetched}). Run `audit --week {week}` to see the filter."
                )
            else:
                _report(result)
                if getattr(args, "no_drive", False):
                    print("  drive        skipped (--no-drive)")
                elif cfg.drive.enabled:
                    print(f"  drive        {'uploaded' if result.uploaded else 'FAILED, will retry next run'}")
            print(f"  log          {log_path}")
            return 0

        if args.command == "doctor":
            return doctor(cfg)

        if args.command == "audit":
            dropped = pipeline.audit(cfg, state, week)
            print(f"\nWeek {week}: {len(dropped)} items dropped by selection\n")
            for d in dropped:
                print(f"  {d.title}\n      {d.reason}\n")
            return 0

        if args.command == "render":
            files = pipeline.render(cfg, state, week, args.html, args.pdf)
            for ext, path in sorted(files.items()):
                print(f"  {ext:<5} {path}")
            return 0

        if args.command == "speak":
            from .audio import speak  # noqa: PLC0415

            txt = cfg.run.output_dir / f"{emit_stage.week_stem(week)}.txt"
            if not txt.exists():
                print(f"no digest text at {txt}", file=sys.stderr)
                return 1
            out = Path(str(txt).removesuffix(".txt") + ".mp3")
            speak(txt, out, cfg)
            print(f"  mp3   {out}")
            return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
