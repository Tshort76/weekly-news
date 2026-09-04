"""CLI entry point.

    python -m digest run [--week 2026-W36] [--html] [--pdf] [--audio] [--no-drive] [--dry-run]
    python -m digest classify-only --week ...
    python -m digest audit --week ...
    python -m digest render --week ... --html --pdf
    python -m digest speak --week ...

Setting up, once:

    digest init                     answer or press Enter through the questions
    digest import                   bring a digest.toml checkout into the app
    digest key set anthropic        store an API key in the system credential store
    digest lens list|use|show       the editorial lens: what gets in
    digest feeds add|check|list     where the headlines come from
    digest where                    print the config and data directories
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_module
from . import emit as emit_stage
from . import pipeline
from .logging_setup import setup as setup_logging


def schedule_days() -> tuple[str, ...]:
    from .schedule import WEEKDAYS  # noqa: PLC0415

    return WEEKDAYS
from .state import State


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="digest",
        description="A weekly briefing, filtered and written to your own editorial lens.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n", 2)[2],
    )
    parser.add_argument("--config", help="path to a digest.toml (a checkout, not an install)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="fetch, filter, write and deliver a week")
    run.add_argument("--week", help="ISO week, e.g. 2026-W36 (default: this week)")
    run.add_argument("--html", action="store_true")
    run.add_argument("--pdf", action="store_true")
    run.add_argument("--audio", action="store_true")
    run.add_argument("--no-drive", action="store_true")
    run.add_argument(
        "--scheduled", action="store_true",
        help="running from a timer: quieter output, and record the run",
    )
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

    open_cmd = sub.add_parser("open", help="open the app in your browser")
    open_cmd.add_argument("--port", type=int, default=8765)
    open_cmd.add_argument("--no-browser", action="store_true")

    sub.add_parser("init", help="set up the app, answering a few questions")
    imp = sub.add_parser("import", help="bring an existing digest.toml into the app")
    imp.add_argument("--from", dest="source", help="path to the digest.toml")
    sub.add_parser("where", help="print the config and data directories")

    sched = sub.add_parser("schedule", help="run the digest every week, automatically")
    sched.add_argument("action", choices=("on", "off", "status", "show"), nargs="?",
                       default="status")
    sched.add_argument("--day", default="", choices=("", *schedule_days()))
    sched.add_argument("--hour", type=int, default=None)

    key = sub.add_parser("key", help="store or forget an API key")
    key.add_argument("action", choices=("set", "show", "forget"))
    key.add_argument("provider", choices=("anthropic", "gemini", "brave"))

    lens = sub.add_parser("lens", help="the editorial lens: what gets into the briefing")
    lens.add_argument("action", choices=("list", "show", "use", "path"), nargs="?",
                      default="show")
    lens.add_argument("name", nargs="?")

    feeds = sub.add_parser("feeds", help="where the headlines come from")
    feeds.add_argument("action", choices=("list", "add", "check", "remove"), nargs="?",
                       default="list")
    feeds.add_argument("url", nargs="?")

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


NEEDS_NO_CONFIG = {"init", "import", "where", "open"}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command in NEEDS_NO_CONFIG:
        return _setup_command(args)

    try:
        cfg = config_module.load(args.config)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    except config_module.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.command == "schedule":
        return _schedule_command(args, cfg)

    if args.command in {"key", "lens", "feeds"}:
        return _admin_command(args, cfg)

    week = getattr(args, "week", None) or pipeline.iso_week()
    log_path = setup_logging(cfg.log_dir, week, args.verbose)

    with State(cfg.db_path) as state:
        if args.command in {"run", "classify-only"}:
            started = state.start_run(week) if getattr(args, "scheduled", False) else None
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
            if started:
                state.finish_run(
                    week, started, "ok",
                    fetched=result.fetched, selected=result.selected,
                    entries=len(result.edition.entries),
                    words=result.edition.word_count,
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


def _setup_command(args) -> int:
    from .config import legacy, paths  # noqa: PLC0415

    if args.command == "where":
        print(f"config  {paths.config_dir()}")
        print(f"data    {paths.data_dir()}")
        print(f"lens    {paths.lens_file()}")
        return 0

    if args.command == "open":
        from .ui.app import serve  # noqa: PLC0415

        return serve(port=args.port, open_browser=not args.no_browser)

    if args.command == "init":
        from .init import main as init_main  # noqa: PLC0415

        return init_main()

    try:
        report = legacy.import_legacy(Path(args.source) if args.source else None)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"imported {report['from']}")
    print(f"  config   {report['config_dir']}")
    print(f"  data     {report['data_dir']}")
    print(f"  feeds    {report['feeds']}")
    if report["database_copied"]:
        print("  copied the record of what you have already seen")
    return 0


def _schedule_command(args, cfg) -> int:
    from . import schedule as scheduler  # noqa: PLC0415

    backend = scheduler.backend()
    day = args.day or cfg.run.weekday
    hour = args.hour if args.hour is not None else 7

    if args.action == "show":
        if isinstance(backend, scheduler.Launchd):
            print(backend.render(day, hour))
        elif isinstance(backend, scheduler.Systemd):
            service, timer = backend.render(day, hour)
            print(service + "\n" + timer)
        else:
            print(" ".join(getattr(backend, "arguments", lambda *a: scheduler.command())(day, hour)))
        return 0

    if args.action == "on":
        where = backend.install(day, hour)
        print(f"scheduled for {day} at {hour:02d}:00 via {backend.name}")
        print(f"  {where}")
        return 0

    if args.action == "off":
        print("removed" if backend.remove() else "nothing was scheduled")
        return 0

    status = backend.status()
    print(f"{backend.name}: {'on' if status.installed else 'off'} — {status.detail}")
    if status.when:
        print(f"  {status.when}")
    return 0


def _admin_command(args, cfg) -> int:
    from . import credentials  # noqa: PLC0415
    from .config import paths  # noqa: PLC0415
    from .lens import presets, store  # noqa: PLC0415

    if args.command == "key":
        if args.action == "show":
            key, source = credentials.resolve(args.provider, config_path=cfg.config_path)
            print(f"{args.provider}: " + (f"…{key[-4:]} from {source}" if key else "not set"))
            return 0 if key else 1
        if args.action == "forget":
            print("forgotten" if credentials.forget(args.provider) else "nothing stored")
            return 0
        import getpass  # noqa: PLC0415

        value = getpass.getpass(f"{args.provider} API key (not shown): ").strip()
        if not value:
            print("nothing entered", file=sys.stderr)
            return 1
        print(f"stored in {credentials.store(args.provider, value)}")
        return 0

    if args.command == "lens":
        if args.action == "list":
            for name in presets.available():
                print(f"  {name:<24} {presets.load(name).name}")
            return 0
        if args.action == "path":
            print(paths.lens_file())
            return 0
        if args.action == "use":
            if not args.name:
                print("which preset? try `digest lens list`", file=sys.stderr)
                return 1
            store.install_preset(args.name)
            print(f"lens set to {args.name}; written to {paths.lens_file()}")
            return 0
        print(cfg.lens_text)
        return 0

    return _feeds_command(args, cfg)


def _feeds_command(args, cfg) -> int:
    from .config import paths  # noqa: PLC0415
    from .config.schema import validate_feeds  # noqa: PLC0415
    from .config.write import dumps_feeds, write  # noqa: PLC0415
    from .ingest import probe  # noqa: PLC0415

    path = paths.feeds_file()
    raw = {}
    if path.exists():
        import tomllib  # noqa: PLC0415

        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    feeds = validate_feeds(raw) if raw else []

    if args.action == "list":
        for feed in feeds:
            mark = " " if feed["enabled"] else "-"
            print(f" {mark} {feed['name']}\n      {feed['url']}")
        print(f"\n{len(feeds)} feeds in {path}")
        return 0

    if not args.url:
        print("which feed? pass a URL", file=sys.stderr)
        return 1

    if args.action == "remove":
        kept = [f for f in feeds if f["url"] != args.url]
        if len(kept) == len(feeds):
            print("no feed with that URL", file=sys.stderr)
            return 1
        write(path, dumps_feeds(kept))
        print(f"removed; {len(kept)} feeds left")
        return 0

    report = probe(args.url)
    print(report.describe())
    if args.action == "check" or not report.usable:
        return 0 if report.usable else 1

    feeds.append({
        "name": report.name, "url": args.url, "section": "other",
        "weight": 1.0, "enabled": True, "verified": report.checked_on,
    })
    write(path, dumps_feeds(feeds))
    print(f"added; {len(feeds)} feeds now")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
