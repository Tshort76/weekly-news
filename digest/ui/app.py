"""The local web app: `digest open`.

Bound to 127.0.0.1 and nothing else. One person, one machine, no accounts, no
remote mode — the security model is that nothing is listening anywhere a second
person could reach.

The browser is the renderer because the edition is already an HTML page in this
project's own palette, so the app and the thing it produces look like one object
rather than two. Pages are server-rendered; the only JavaScript is the progress
stream, which is an `EventSource` and about sixty lines.

Everything long-running goes through `digest.jobs.Runner`, which is injected, so
the tests drive every route with a fake pipeline and never run a week.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import discover, jobs, pipeline
from ..config import ConfigError, load, paths
from ..config.schema import SCHEMA_VERSION, validate_config, validate_feeds
from ..config.write import dumps, dumps_feeds, write
from ..lens import presets, store
from ..state import State

HERE = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", 8765


def _config_or_none():
    try:
        return load()
    except (FileNotFoundError, ConfigError):
        return None


def _feeds() -> list[dict]:
    path = paths.feeds_file()
    if not path.exists():
        return []
    return validate_feeds(tomllib.loads(path.read_text(encoding="utf-8")))


def _save_feeds(feeds: list[dict]) -> None:
    from ..config.legacy import FEEDS_HEADER  # noqa: PLC0415

    write(paths.feeds_file(), dumps_feeds(feeds, FEEDS_HEADER))


def _real_run(**kwargs):
    """The actual week, with its own config and its own database handle.

    Opened here rather than passed in because this runs on the job thread and
    SQLite connections belong to one thread.
    """
    cfg = load()
    with State(cfg.db_path) as state:
        return pipeline.run(cfg, state, want_html=True, **kwargs)


def create_app(runner: jobs.Runner | None = None) -> FastAPI:
    app = FastAPI(title="Weekly Digest", docs_url=None, redoc_url=None)
    app.state.runner = runner or jobs.Runner(paths.data_dir(), _real_run)
    # The headlines currently being labelled. In memory rather than stored: they
    # are a working set for one sitting, and the labels themselves are what is
    # kept.
    app.state.sample = []
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=str(HERE / "templates"))

    def page(request: Request, name: str, **context) -> HTMLResponse:
        cfg = _config_or_none()
        context.setdefault("cfg", cfg)
        context.setdefault("lens_name", cfg.lens.name if cfg else "")
        context.setdefault("running", app.state.runner.busy)
        return templates.TemplateResponse(request, name, context)

    # ------------------------------------------------------------- this week

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        cfg = _config_or_none()
        if cfg is None:
            return RedirectResponse("/setup", status_code=303)
        with State(cfg.db_path) as state:
            runs = state.recent_runs(10)
            latest = state.load_edition(pipeline.iso_week())
        return page(
            request, "home.html", runs=runs, latest=latest,
            week=pipeline.iso_week(), job=app.state.runner.job,
        )

    @app.post("/run")
    def start_run(request: Request, dry: str = Form("")):
        try:
            app.state.runner.start(pipeline.iso_week(), dry_run=bool(dry), no_drive=True)
        except jobs.Busy as exc:
            return page(request, "home.html", error=str(exc), runs=[], latest=None,
                        week=pipeline.iso_week(), job=app.state.runner.job)
        return RedirectResponse("/", status_code=303)

    @app.post("/stop")
    def stop_run():
        app.state.runner.stop()
        return RedirectResponse("/", status_code=303)

    @app.get("/progress")
    def progress(last_event_id: int = 0):
        """The live stream. Resumes from the id the browser last saw."""
        job = app.state.runner.job
        if job is None:
            return JSONResponse({"status": "idle"})
        return StreamingResponse(
            jobs.stream(job, last_event_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---------------------------------------------------------------- review

    @app.get("/review/{week}", response_class=HTMLResponse)
    def review(request: Request, week: str):
        cfg = load()
        with State(cfg.db_path) as state:
            edition = state.load_edition(week)
            dropped = pipeline.audit(cfg, state, week) if state.load_classified(week) else []
        return page(request, "review.html", week=week, edition=edition, dropped=dropped)

    # ----------------------------------------------------------------- feeds

    @app.get("/feeds", response_class=HTMLResponse)
    def feeds_page(request: Request, checked: str = ""):
        return page(request, "feeds.html", feeds=_feeds(),
                    report=json.loads(checked) if checked else None)

    @app.post("/feeds/check", response_class=HTMLResponse)
    def check_feed(request: Request, url: str = Form(...)):
        from ..ingest import probe  # noqa: PLC0415

        report = probe(url)
        return page(request, "feeds.html", feeds=_feeds(), report=report, url=url)

    @app.post("/feeds/add")
    def add_feed(url: str = Form(...), name: str = Form(""), section: str = Form("other")):
        from ..ingest import probe  # noqa: PLC0415

        report = probe(url)
        feeds = _feeds()
        feeds.append({
            "name": name or report.name or url, "url": url, "section": section,
            "weight": 1.0, "enabled": True, "verified": report.checked_on,
        })
        _save_feeds(feeds)
        return RedirectResponse("/feeds", status_code=303)

    @app.post("/feeds/toggle")
    def toggle_feed(url: str = Form(...)):
        """Pause rather than delete. A quiet feed is usually off-lens, not broken."""
        feeds = _feeds()
        for feed in feeds:
            if feed["url"] == url:
                feed["enabled"] = not feed["enabled"]
        _save_feeds(feeds)
        return RedirectResponse("/feeds", status_code=303)

    @app.post("/feeds/remove")
    def remove_feed(url: str = Form(...)):
        _save_feeds([f for f in _feeds() if f["url"] != url])
        return RedirectResponse("/feeds", status_code=303)

    # -------------------------------------------------------------- settings

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request, saved: str = ""):
        cfg = load()
        found = discover.probe_ollama(cfg.models.ollama_host)
        return page(request, "settings.html", found=found, saved=bool(saved),
                    known=discover.KNOWN_MODELS)

    @app.post("/settings")
    def save_settings(
        classify: str = Form(...), synthesize: str = Form(...),
        provider: str = Form(...), minutes: int = Form(...),
        folder: str = Form(...), audio: str = Form(""), pdf: str = Form(""),
    ):
        raw = tomllib.loads(paths.config_file().read_text(encoding="utf-8"))
        raw.setdefault("models", {}).update(
            {"classify": classify, "synthesize": synthesize, "provider": provider}
        )
        raw.setdefault("output", {}).update(
            {"minutes": minutes, "folder": folder,
             "audio": bool(audio), "pdf": bool(pdf)}
        )
        validate_config(raw)  # raises before anything is written
        write(paths.config_file(), dumps(raw))
        return RedirectResponse("/settings?saved=1", status_code=303)

    @app.get("/schedule", response_class=HTMLResponse)
    def schedule_page(request: Request):
        from .. import schedule as scheduler  # noqa: PLC0415

        backend = scheduler.backend()
        return page(request, "schedule.html", backend=backend.name,
                    status=backend.status(), days=scheduler.WEEKDAYS)

    @app.post("/schedule")
    def set_schedule(day: str = Form("friday"), hour: int = Form(7), off: str = Form("")):
        from .. import schedule as scheduler  # noqa: PLC0415

        backend = scheduler.backend()
        if off:
            backend.remove()
        else:
            backend.install(day, hour)
        # The config keeps the same answer, so the Settings page and the actual
        # scheduler cannot drift apart.
        raw = tomllib.loads(paths.config_file().read_text(encoding="utf-8"))
        raw.setdefault("schedule", {}).update(
            {"enabled": not off, "day": day, "hour": hour}
        )
        validate_config(raw)
        write(paths.config_file(), dumps(raw))
        return RedirectResponse("/schedule", status_code=303)

    @app.post("/open-folder")
    def open_folder():
        """The default delivery: show the files where they already are."""
        import subprocess  # noqa: PLC0415
        import sys  # noqa: PLC0415

        folder = str(load().run.output_dir)
        opener = ("open" if sys.platform == "darwin"
                  else "explorer" if sys.platform.startswith("win") else "xdg-open")
        try:
            subprocess.Popen([opener, folder])
        except OSError:
            pass
        return RedirectResponse("/", status_code=303)

    # ----------------------------------------------------------------- setup

    @app.get("/setup", response_class=HTMLResponse)
    def setup(request: Request):
        found = discover.probe_ollama()
        memory = discover.total_memory_gb()
        return page(
            request, "setup.html",
            found=found, memory=memory,
            classify=discover.recommend("classify", found, memory),
            synthesize=discover.recommend("synthesize", found, memory),
            lenses=[(n, presets.load(n).name, presets.calibrated(n))
                    for n in presets.available()],
            legacy=_legacy_path(),
        )

    @app.post("/setup")
    def finish_setup(
        lens: str = Form(...), classify: str = Form(...), synthesize: str = Form(...),
        provider: str = Form("ollama"), minutes: int = Form(58),
        folder: str = Form("~/digests"),
    ):
        from ..init import _advanced_defaults  # noqa: PLC0415

        store.install_preset(lens)
        models = {"classify": {"model": classify, "provider": provider},
                  "synthesize": {"model": synthesize, "provider": provider}}
        config = {
            "schema_version": SCHEMA_VERSION,
            "models": {"provider": provider, "classify_provider": None,
                       "synthesize_provider": None,
                       "classify": classify, "synthesize": synthesize},
            "output": {"minutes": minutes, "folder": folder, "html": True,
                       "pdf": False, "audio": False},
            "schedule": {"enabled": False, "day": "friday", "hour": 7},
            "delivery": {"drive": {"enabled": False, "folder_id": "",
                                   "method": "oauth", "rclone_remote": ""}},
            "advanced": _advanced_defaults(models, "http://localhost:11434"),
        }
        validate_config(config)
        write(paths.config_file(), dumps(config))
        _save_feeds([dict(f, enabled=True) for f in presets.load(lens).feeds])
        return RedirectResponse("/", status_code=303)

    @app.post("/setup/import")
    def import_existing():
        from ..config import legacy  # noqa: PLC0415

        legacy.import_legacy()
        return RedirectResponse("/", status_code=303)

    # ------------------------------------------------------------------ lens

    @app.get("/lens", response_class=HTMLResponse)
    def lens_page(request: Request, saved: str = ""):
        stored = store.load()
        spec = stored.spec or presets.load(presets.DEFAULT)
        return page(request, "lens.html", spec=spec, stored=stored,
                    saved=bool(saved),
                    lenses=[(n, presets.load(n).name, presets.calibrated(n))
                            for n in presets.available()])

    @app.post("/lens")
    async def save_lens(request: Request):
        from .lensform import from_form  # noqa: PLC0415
        from ..lens.serialize import to_toml  # noqa: PLC0415

        form = await request.form()
        stored = store.load()
        spec = from_form(form, stored.spec or presets.load(presets.DEFAULT))
        store.save(spec, to_toml(spec))
        return RedirectResponse("/lens?saved=1", status_code=303)

    @app.post("/lens/use")
    def use_preset(name: str = Form(...)):
        store.install_preset(name)
        return RedirectResponse("/lens?saved=1", status_code=303)

    @app.get("/lens/diff", response_class=HTMLResponse)
    def lens_diff(request: Request):
        """What a hand edit changed, so the form can say before it overwrites."""
        import difflib  # noqa: PLC0415

        from ..lens.compile import compile_lens  # noqa: PLC0415

        stored = store.load()
        rebuilt = compile_lens(stored.spec) if stored.spec else ""
        diff = list(difflib.unified_diff(
            rebuilt.splitlines(), stored.markdown.splitlines(),
            "what the form would write", "your edited lens.md", lineterm="",
        ))
        return page(request, "diff.html", diff=diff)

    # ----------------------------------------------------------- calibration

    @app.get("/calibrate", response_class=HTMLResponse)
    def calibrate_page(request: Request):
        cfg = load()
        with State(cfg.db_path) as state:
            existing = {r["item_id"]: r for r in state.labels()}
        return page(request, "calibrate.html", labelled=existing,
                    items=app.state.sample or [], report=None)

    @app.post("/calibrate/sample")
    def draw_sample(request: Request):
        from ..ingest import sample  # noqa: PLC0415

        app.state.sample = sample(load(), 25)
        return RedirectResponse("/calibrate", status_code=303)

    @app.post("/calibrate/save")
    async def save_labels(request: Request):
        form = await request.form()
        cfg = load()
        wanted = {i.id: i for i in (app.state.sample or [])}
        rows = [
            {"item_id": item_id, "title": wanted[item_id].title,
             "blurb": wanted[item_id].blurb[:400],
             "source": wanted[item_id].source, "choice": choice}
            for item_id, choice in form.items()
            if item_id in wanted and choice in ("want", "maybe", "skip")
        ]
        with State(cfg.db_path) as state:
            state.save_labels(rows, pipeline.iso_week())
        return RedirectResponse("/calibrate/result", status_code=303)

    @app.get("/calibrate/result", response_class=HTMLResponse)
    def calibrate_result(request: Request):
        """Run the lens over what the user labelled and show the two disagreements."""
        from .. import calibrate as calibration  # noqa: PLC0415
        from ..classify import classify  # noqa: PLC0415
        from ..llm import Client  # noqa: PLC0415

        cfg = load()
        with State(cfg.db_path) as state:
            saved = state.labels()
        by_id = {r["item_id"]: r for r in saved}
        items = [i for i in (app.state.sample or []) if i.id in by_id]
        if not items:
            return page(request, "calibrate.html", labelled=by_id, items=[], report=None)
        labels = calibration.labels_from_choices(
            {r["item_id"]: r["choice"] for r in saved}
        )
        results = classify(items, cfg, Client(cfg))
        report = calibration.score(results, labels)
        return page(request, "result.html", report=report, total=len(items),
                    model=cfg.models.classify)

    @app.post("/calibrate/example")
    def add_to_lens(headline: str = Form(...), level: str = Form("1"),
                    note: str = Form("")):
        from .lensform import add_example  # noqa: PLC0415
        from ..lens.serialize import to_toml  # noqa: PLC0415

        stored = store.load()
        spec = add_example(stored.spec or presets.load(presets.DEFAULT),
                           level, headline, note)
        store.save(spec, to_toml(spec))
        return RedirectResponse("/lens?saved=1", status_code=303)

    @app.get("/about", response_class=HTMLResponse)
    def about(request: Request):
        return page(request, "about.html", config_dir=paths.config_dir(),
                    data_dir=paths.data_dir())

    return app


def _legacy_path():
    from ..config import legacy  # noqa: PLC0415

    return legacy.find_legacy_config()


def serve(host: str = HOST, port: int = PORT, open_browser: bool = True) -> int:
    import uvicorn  # noqa: PLC0415

    if open_browser:
        import threading  # noqa: PLC0415
        import webbrowser  # noqa: PLC0415

        threading.Timer(0.8, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    print(f"Weekly digest running at http://{host}:{port}/  (ctrl-c to stop)")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
    return 0
