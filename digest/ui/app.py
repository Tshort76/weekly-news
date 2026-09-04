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
            lenses=[(n, presets.load(n).name) for n in presets.available()],
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
