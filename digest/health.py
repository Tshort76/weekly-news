"""What the dashboard shows: one verdict, then the panels behind it.

Everything here is computed at request time and stored nowhere. There is no
banner state to go stale and no acknowledgement anybody has to remember to
clear — the page is a view over `runs`, `run_events` and the files a run wrote,
and if the answer changes the page changes with it.

The one judgement in this module is the line between amber and red, and it is
deliberately not "how alarming does the message sound". It is **whether the
pipeline recovered**. A timeout that succeeded on the retry is amber even though
it logged a frightening word; a `[PARTIAL]` edition is red even though a
briefing was still published, because entries were dropped and nobody was told.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

# A feed that fails this many runs in a row has stopped working rather than had
# a bad morning, and that is a thing to go and fix.
FEED_FAILURE_RUN = 3


@dataclass
class Finding:
    """One thing worth saying, at one of the three severities."""

    level: str  # info | warn | stop
    text: str
    detail: str = ""
    where: str = ""


@dataclass
class Verdict:
    state: str = "green"  # green | amber | red
    headline: str = "All good"
    summary: str = ""
    evidence: str = ""
    action: str = ""
    meta: str = ""
    findings: list[Finding] = field(default_factory=list)


def _age(started: str | None) -> str:
    if not started:
        return ""
    try:
        when = datetime.fromisoformat(started)
    except ValueError:
        return started
    return when.astimezone().strftime("%a %-d %b %H:%M")


def _duration(run: dict) -> str:
    if not run.get("finished") or not run.get("started"):
        return ""
    try:
        span = datetime.fromisoformat(run["finished"]) - datetime.fromisoformat(run["started"])
    except ValueError:
        return ""
    minutes, seconds = divmod(int(span.total_seconds()), 60)
    return f"{minutes} m" if minutes else f"{seconds} s"


def did_not_finish(run: dict | None, job_running: bool) -> bool:
    """A row still saying "running" with nothing actually running.

    This is the state the whole recording layer exists to make visible: before
    it, a run that was killed left an unstructured log file that stopped
    mid-sentence and nothing else at all.
    """
    return bool(run) and run.get("status") == "running" and not job_running


def failing_feeds(history: dict[str, list[dict]], runs: int = FEED_FAILURE_RUN) -> list[str]:
    """Feeds whose last `runs` attempts all raised. Not "quiet" — failed."""
    out = []
    for name, entries in history.items():
        recent = entries[-runs:]
        if len(recent) == runs and all(e["failed"] for e in recent):
            out.append(name)
    return sorted(out)


def _evidence_lines(events: list[dict], limit: int = 6) -> str:
    """The log lines that led to the error, formatted as they were written.

    Warnings are included, not just the error: the retries before a give-up are
    what say whether the model was slow or was never there, and those are
    different problems with different fixes.
    """
    lines = []
    for event in events[-limit:]:
        at = event["at"][11:19] if len(event.get("at", "")) > 19 else ""
        lines.append(f"{at}  {event['level']:<7}  {event['kind']:<6}  {event['message']}")
    return "\n".join(lines)


def verdict(run: dict | None, events: list[dict], edition, job_running: bool,
            feed_history: dict[str, list[dict]] | None = None) -> Verdict:
    """The banner. Red first, then amber, then green."""
    errors = [e for e in events if e["level"] in ("ERROR", "CRITICAL")]
    warnings = [e for e in events if e["level"] == "WARNING"]
    broken = failing_feeds(feed_history or {})

    meta = ""
    if run:
        meta = f"{run['week']} · {'started' if run.get('status') == 'running' else 'finished'} {_age(run.get('finished') or run.get('started'))}"

    # ---------------------------------------------------------------- red
    if run is None:
        return Verdict("amber", "No run recorded yet",
                       summary="Nothing has run since the app started keeping records. "
                               "Run a week and this page fills in.")

    if did_not_finish(run, job_running):
        stages = [e for e in events if e["kind"] == "stage"]
        reached = stages[-1]["subject"] if stages else "the start"
        return Verdict(
            "red", "Needs you",
            summary=f"The {run['week']} run started and never finished. It got as "
                    f"far as {reached} and nothing recorded why it stopped.",
            evidence=_evidence_lines(errors + warnings) if (errors or warnings) else "",
            action="Nothing was marked seen, so re-running the week starts from the "
                   "same headlines and loses nothing.",
            meta=meta, findings=_findings(events, edition, broken),
        )

    if errors or run.get("status") in ("failed", "partial"):
        first = errors[0] if errors else None
        partial = getattr(edition, "partial", False)
        return Verdict(
            "red", "Needs you",
            summary=(
                f"The {run['week']} run published a briefing marked [PARTIAL] — "
                "entries were dropped."
                if partial else
                f"The {run['week']} run failed. {run.get('note') or ''}".strip()
            ),
            evidence=_evidence_lines(warnings + errors),
            action=_advice(first, run),
            meta=meta, findings=_findings(events, edition, broken),
        )

    if broken:
        return Verdict(
            "red", "Needs you",
            summary=f"{_and_list(broken)} has failed the last {FEED_FAILURE_RUN} runs. "
                    "That is a feed that has stopped working, not a quiet week.",
            action="Check the URL on the Feeds page; a moved or retired feed fails "
                   "silently every week otherwise.",
            meta=meta, findings=_findings(events, edition, broken),
        )

    # -------------------------------------------------------------- amber
    if warnings:
        return Verdict(
            "amber", "Worth a look",
            summary=_amber_summary(warnings, events),
            meta=meta, findings=_findings(events, edition, broken),
        )

    # -------------------------------------------------------------- green
    words = run.get("words") or 0
    return Verdict(
        "green", "All good",
        meta=f"{run['week']} · finished {_age(run.get('finished'))} · "
             f"{run.get('entries') or 0} entries · {words:,} words",
        findings=_findings(events, edition, broken),
    )


def _and_list(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _amber_summary(warnings: list[dict], events: list[dict]) -> str:
    thin = [e for e in events if e["kind"] == "ground" and _outcome(e) in ("none", "blocked")]
    parts = []
    if thin:
        parts.append(
            f"<b>{len(thin)} stories were written from a headline alone</b>"
        )
    others = len(warnings)
    if others:
        parts.append(f"{others} warning{'s' if others > 1 else ''} the run handled itself")
    return "Nothing to fix. " + ", and ".join(parts) + "." if parts else "Nothing to fix."


def _outcome(event: dict) -> str:
    try:
        return (json.loads(event["detail"]) or {}).get("outcome", "")
    except (TypeError, ValueError):
        return ""


def _advice(error: dict | None, run: dict) -> str:
    """Say what the error means and what to do, not just that it happened."""
    text = (error or {}).get("message", "") + " " + (run.get("note") or "")
    if "Connection refused" in text or "ConnectionError" in text:
        return ("<b>Connection refused</b> means nothing was listening — the model "
                "server was not running, rather than being slow. Start it and re-run "
                "the week; nothing was marked seen, so it starts from the same headlines.")
    if "RateLimit" in text or "quota" in text.lower() or "429" in text:
        return ("The hosted provider stopped answering. Either wait for the quota to "
                "reset or switch that stage to a local model on the Settings page, "
                "then re-run the week.")
    if "credential" in text.lower() or "api key" in text.lower() or "401" in text:
        return "The API key was rejected. Set it again with <code>digest key set</code>."
    return ("Nothing was marked seen, so re-running the week starts from the same "
            "headlines and loses nothing.")


def _findings(events: list[dict], edition, broken: list[str]) -> list[Finding]:
    """The Needs-attention list: every warning and error, repeats collapsed."""
    seen: dict[tuple, Finding] = {}
    order: list[tuple] = []
    for event in events:
        if event["level"] not in ("WARNING", "ERROR", "CRITICAL"):
            continue
        key = (event["level"], event["kind"], event["message"])
        if key in seen:
            seen[key].detail = _bump(seen[key].detail)
            continue
        seen[key] = Finding(
            level="stop" if event["level"] != "WARNING" else "warn",
            text=event["message"],
            where=event["kind"],
        )
        order.append(key)
    findings = [seen[k] for k in order]
    if getattr(edition, "partial", False):
        findings.insert(0, Finding("stop", "The edition is marked [PARTIAL].",
                                   "Entries were dropped from the briefing.", "emit"))
    for name in broken:
        findings.insert(0, Finding("stop", f"{name} has failed {FEED_FAILURE_RUN} runs running.",
                                   "Check the URL on the Feeds page.", "ingest"))
    return findings


def _bump(detail: str) -> str:
    if detail.startswith("×"):
        return f"×{int(detail[1:]) + 1}"
    return "×2"


def funnel(run: dict, classified: list, selected: int, entries: int) -> list[dict]:
    """The filtering steps, all on one scale, largest first."""
    fetched = run.get("fetched") or 0
    steps = [
        ("fetched", fetched, f"{run.get('feeds') or ''}".strip()),
        ("judged", len(classified), ""),
        ("selected", selected, ""),
        ("published", entries, f"{run.get('words') or 0:,} words"),
    ]
    top = max((n for _, n, _ in steps), default=1) or 1
    return [{"label": label, "n": n, "pct": round(100 * n / top, 1), "note": note}
            for label, n, note in steps]


def stage_times(events: list[dict]) -> list[dict]:
    """Per-stage elapsed time, from the stage events the pipeline writes."""
    return [
        {"name": e["subject"], "ms": e["ms"] or 0,
         "detail": _brief(e.get("detail"))}
        for e in events if e["kind"] == "stage"
    ]


def _brief(detail: str | None) -> str:
    try:
        data = json.loads(detail) if detail else {}
    except ValueError:
        return ""
    return " · ".join(f"{k} {v}" for k, v in data.items() if not isinstance(v, dict))


def calls(events: list[dict]) -> dict[str, dict]:
    """Model calls per stage: how many, how many retried, how long."""
    out: dict[str, dict] = {}
    for event in events:
        if event["kind"] != "call":
            continue
        try:
            detail = json.loads(event["detail"] or "{}")
        except ValueError:
            detail = {}
        stage = detail.get("stage", "?")
        row = out.setdefault(stage, {"model": event["subject"], "backend": detail.get("backend", ""),
                                     "calls": 0, "retries": 0, "ms": 0})
        if event["level"] == "WARNING":
            row["retries"] += 1
        else:
            row["calls"] += 1
            row["ms"] += event["ms"] or 0
    return out


def grounding(events: list[dict]) -> dict[str, int]:
    counts = {"article": 0, "search": 0, "none": 0, "blocked": 0}
    for event in events:
        if event["kind"] == "ground":
            outcome = _outcome(event)
            if outcome in counts:
                counts[outcome] += 1
    return counts


def feeds_this_run(events: list[dict]) -> dict[str, dict]:
    out = {}
    for event in events:
        if event["kind"] == "feed":
            out[event["subject"]] = {
                "n": int(event["n"] or 0),
                "failed": event["level"] in ("WARNING", "ERROR"),
                "ms": event["ms"] or 0,
            }
    return out


# ------------------------------------------------------- the machine-readable one


def snapshot(cfg, state) -> dict:
    """Everything a status display needs, in one JSON-able dict.

    This exists so a menu-bar plugin — or anything else outside the app — can
    show how the digest is doing without reimplementing `verdict`. A second
    implementation of "is this healthy" drifts from this one, and the drift
    shows up as two things that disagree about the same run for no reason a
    reader can see.
    """
    from . import emit as emit_stage  # noqa: PLC0415
    from . import pipeline  # noqa: PLC0415

    run = state.last_run()
    events = state.events(run["week"], run["started"]) if run else []
    history = state.feed_history()
    edition = state.load_edition(run["week"]) if run else None
    # A run marked `running` whose process is gone reads as running from here.
    # `verdict` already calls that out via did_not_finish, so it stays its job.
    running = bool(run and run.get("status") == "running")
    found = verdict(run, events, edition, running, history)

    this_week = pipeline.iso_week()
    done = next((r for r in state.recent_runs(20)
                 if r["week"] == this_week and r["status"] in ("ok", "partial")), None)

    files = _files_for(cfg, emit_stage.week_stem(run["week"] if run else this_week))

    return {
        "verdict": {"state": found.state, "headline": found.headline,
                    "summary": found.summary, "meta": found.meta},
        "findings": [{"text": f.text, "detail": f.detail} for f in found.findings][:6],
        "run": {
            "week": run["week"], "status": run["status"],
            "started": run.get("started"), "finished": run.get("finished"),
            "entries": run.get("entries"), "fetched": run.get("fetched"),
            "note": run.get("note") or "",
        } if run else None,
        "running": running,
        "this_week": this_week,
        "completed_this_week": done is not None,
        "failing_feeds": failing_feeds(history),
        "files": files,
        "folder": str(cfg.run.output_dir),
        "schedule": _scheduled_at(cfg),
    }


def _scheduled_at(cfg) -> dict:
    """When the weekly run is meant to happen, from the config file.

    Day and hour live in config.toml's [schedule] and never made it onto
    Config, because until now only `digest schedule` read them and it read the
    file itself. Read rather than raise: a status display that cannot find a
    schedule should say Friday at seven and carry on.
    """
    import tomllib  # noqa: PLC0415

    section: dict = {}
    if cfg.config_path:  # a Config built in code carries no file at all
        try:
            with open(cfg.config_path, "rb") as handle:
                section = tomllib.load(handle).get("schedule", {})
        except (OSError, ValueError, TypeError):
            section = {}
    try:
        hour = int(section.get("hour", 7))
    except (TypeError, ValueError):
        hour = 7
    return {"day": str(section.get("day", "friday")), "hour": hour}


def _files_for(cfg, stem: str) -> dict[str, str]:
    """The edition's files, falling back to the most recent one on disk.

    The run store and the output folder can disagree: a fresh install has
    editions written before it started recording runs, and a week can be
    written and then re-run. Showing the newest thing that actually exists
    beats showing nothing and being technically right about which week.
    """
    folder = cfg.run.output_dir
    suffixes = ("md", "txt", "html", "mp3", "pdf")
    found = {s: str(folder / f"{stem}.{s}") for s in suffixes
             if (folder / f"{stem}.{s}").exists()}
    if found:
        return found
    latest = max(folder.glob("digest-*.md"), key=lambda p: p.name, default=None)
    if latest is None:
        return {}
    return {s: str(latest.with_suffix(f".{s}")) for s in suffixes
            if latest.with_suffix(f".{s}").exists()}
