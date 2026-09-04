"""The imperative shell: run the stages in order and decide what gets recorded.

`--dry-run` still writes files and classifications — the acceptance run is
`run --dry-run --no-drive` followed by `audit`, so the audit needs the
classifications to be there. What dry-run withholds is the durable state: `seen`,
`editions` and `entries` stay untouched, so a dry run can be repeated and never
hides an item from next week.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import classify as classify_stage
from . import cluster as cluster_stage
from . import deliver as deliver_stage
from . import emit as emit_stage
from . import ground as ground_stage
from . import ingest as ingest_stage
from . import selection
from . import synthesize as synth_stage
from .config import Config
from .dedupe import dedupe
from .llm import Client
from .models import Dropped, Edition
from .normalize import normalize_all
from .state import State

log = logging.getLogger("digest.pipeline")


def iso_week(when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    year, week, _ = when.isocalendar()
    return f"{year}-W{week:02d}"


class Cancelled(RuntimeError):
    """Someone pressed stop. Not an error — the caller decides what to say."""


def _noop(*args, **kwargs) -> None:
    pass


@dataclass
class RunResult:
    week: str
    edition: Edition
    files: dict[str, Path] = field(default_factory=dict)
    dropped: list[Dropped] = field(default_factory=list)
    uploaded: bool = False
    fetched: int = 0
    kept_after_dedupe: int = 0
    selected: int = 0


def run(
    cfg: Config,
    state: State,
    *,
    week: str | None = None,
    want_html: bool = False,
    want_pdf: bool = False,
    want_audio: bool = False,
    dry_run: bool = False,
    no_drive: bool = False,
    client: Client | None = None,
    classify_only: bool = False,
    progress=None,
    cancel=None,
) -> RunResult:
    """Run a week.

    `progress(stage, detail)` is called at every stage boundary, and `cancel` is
    a threading.Event checked at the same points. Both default to doing nothing,
    so the CLI and every existing caller are unchanged — the UI is the only
    thing that passes them, and a stage never has to know which it is serving.
    """
    week = week or iso_week()
    client = client or Client(cfg)
    progress = progress or _noop

    def checkpoint(stage: str, **detail) -> None:
        progress(stage, detail)
        if cancel is not None and cancel.is_set():
            # Between stages only. Stopping mid-classification would leave a
            # partial batch that looks like a finished one.
            raise Cancelled(f"stopped after {stage}")

    checkpoint("start", week=week)
    items = ingest_stage.ingest(cfg)
    log.info("fetched %d items", len(items))
    items = normalize_all(items)
    checkpoint("fetch", fetched=len(items), feeds=len(cfg.sources))

    kept, dupes = dedupe(items, state.seen_ids())
    log.info("%d items after dedupe (%d dropped)", len(kept), len(dupes))

    checkpoint("dedupe", kept=len(kept), dropped=len(dupes))

    classified = classify_stage.classify(kept, cfg, client)
    state.save_classified(classified, week)
    checkpoint("classify", judged=len(classified))

    dropped = [
        Dropped(id=i.id, title=i.title, stage="dedupe", reason=reason)
        for i, reason in dupes
    ]

    if classify_only:
        return RunResult(
            week=week,
            edition=Edition(
                week=week, generated_at=datetime.now(timezone.utc),
                opening="", entries=[], closing_questions=[], quiet=True,
            ),
            dropped=dropped,
            fetched=len(items),
            kept_after_dedupe=len(kept),
        )

    selected, select_dropped = selection.select(
        classified, cfg, state.prior_mechanisms(week)
    )
    dropped.extend(select_dropped)
    log.info("%d items selected", len(selected))
    checkpoint("select", selected=len(selected), dropped=len(select_dropped))

    # Only the selected items, and only the thin ones among those. Re-saved so
    # the evidence is part of the week's record: an audit or a comparison run
    # rebuilds from these rows and has to see the same text the writer saw.
    selected = ground_stage.ground(selected, cfg)
    state.save_classified(selected, week)
    checkpoint("ground", grounded=sum(1 for r in selected if r.evidence))

    # Before clustering, not after: whether a reporter's own words reach the
    # page should not depend on how well a model grouped the week.
    carried, to_cluster = synth_stage.partition_carried(selected, cfg)
    log.info(
        "%d stories go in as their reporter wrote them, %d go to the model",
        len(carried), len(to_cluster),
    )
    checkpoint("partition", carried=len(carried), to_write=len(to_cluster))
    clusters, degraded = cluster_stage.cluster(to_cluster, cfg, client)
    clusters = synth_stage.carried_clusters(carried) + clusters
    edition = synth_stage.synthesize(
        clusters, cfg, client, week,
        prior_entries=state.prior_entries(week),
        degraded=degraded,
        progress=progress,
        cancel=cancel,
    )
    checkpoint("write", entries=len(edition.entries), words=edition.word_count)

    files = emit_stage.emit(edition, cfg, want_html=want_html, want_pdf=want_pdf)
    checkpoint("emit", files={k: str(v) for k, v in files.items()})

    if want_audio and not edition.quiet:
        from .audio import speak  # noqa: PLC0415

        mp3 = cfg.run.output_dir / f"{emit_stage.week_stem(week)}.mp3"
        try:
            files["mp3"] = speak(files["txt"], mp3, cfg)
        except Exception as exc:  # audio never blocks the edition
            log.error("audio generation failed: %s", exc)

    result = RunResult(
        week=week, edition=edition, files=files, dropped=dropped,
        fetched=len(items), kept_after_dedupe=len(kept), selected=len(selected),
    )

    if dry_run:
        log.info("dry run: state store not updated")
        return result

    # Only now, with the files on disk, is it safe to say we have seen these.
    # Every item fetched is marked, not just the dedupe winners: the fetch window
    # overlaps by a day, so a loser left unmarked would come back next week
    # looking new once its winner is filtered out by `seen`.
    state.save_edition(edition, files.get("txt"))
    state.mark_seen(items, week)

    if not no_drive:
        result.uploaded = deliver_stage.deliver(list(files.values()), cfg, state, week)
    return result


def render(cfg: Config, state: State, week: str, want_html: bool, want_pdf: bool) -> dict[str, Path]:
    edition = state.load_edition(week)
    if edition is None:
        raise SystemExit(f"no stored edition for {week}")
    return emit_stage.emit(edition, cfg, want_html=want_html, want_pdf=want_pdf)


def audit(cfg: Config, state: State, week: str) -> list[Dropped]:
    """Re-run selection over the stored classifications and report what it drops.

    Nothing extra is stored to make this work: the classifications are the record
    and `select` is pure, so the audit is just the same function run again.
    """
    classified = state.load_classified(week)
    if not classified:
        raise SystemExit(f"no classifications stored for {week} — run the week first")
    _, dropped = selection.select(classified, cfg, state.prior_mechanisms(week))
    return dropped
