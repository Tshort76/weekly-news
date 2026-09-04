"""The job runner, driven by a fake pipeline. No network, no real week."""

from __future__ import annotations

import json
import threading

import pytest

from digest.jobs import Busy, Runner, stream


def fake_run(events=("fetch", "classify", "write"), fail=False, block=None):
    def run(*, week, progress, cancel, **kwargs):
        for stage in events:
            if cancel.is_set():
                from digest.pipeline import Cancelled

                raise Cancelled(f"stopped after {stage}")
            progress(stage, {"week": week})
            if block is not None:
                block.wait(2)
        if fail:
            raise RuntimeError("the model fell over")
        return {"week": week, "entries": 3}
    return run


def wait_for(runner, timeout=2.0):
    runner.join(timeout)
    return runner.job


def test_progress_survives_the_page_being_closed(tmp_path):
    """The buffer is the record; the stream is only a view of it."""
    runner = Runner(tmp_path, fake_run())
    job = runner.start("2026-W36")
    wait_for(runner)
    assert [e.stage for e in job.since(0)] == ["fetch", "classify", "write"]


def test_a_reconnecting_page_gets_only_what_it_missed(tmp_path):
    runner = Runner(tmp_path, fake_run())
    job = runner.start("2026-W36")
    wait_for(runner)
    assert [e.stage for e in job.since(1)] == ["classify", "write"]


def test_a_crash_marks_the_run_failed_rather_than_hanging(tmp_path):
    runner = Runner(tmp_path, fake_run(fail=True))
    job = runner.start("2026-W36")
    wait_for(runner)
    assert job.status == "failed"
    assert "the model fell over" in job.error
    assert runner.busy is False


def test_a_second_run_while_one_is_going_is_refused(tmp_path):
    gate = threading.Event()
    runner = Runner(tmp_path, fake_run(block=gate))
    runner.start("2026-W36")
    with pytest.raises(Busy):
        runner.start("2026-W37")
    gate.set()
    wait_for(runner)


def test_stopping_ends_the_run_without_calling_it_a_failure(tmp_path):
    gate = threading.Event()
    runner = Runner(tmp_path, fake_run(block=gate))
    job = runner.start("2026-W36")
    assert runner.stop() is True
    gate.set()
    wait_for(runner)
    assert job.status == "cancelled" and job.error == ""


def test_the_lock_file_is_gone_once_the_run_ends(tmp_path):
    runner = Runner(tmp_path, fake_run())
    runner.start("2026-W36")
    wait_for(runner)
    assert not (tmp_path / "run.lock").exists()


def test_a_lock_left_by_a_dead_process_does_not_wedge_the_app(tmp_path):
    """A killed run must not make the app permanently refuse to start another."""
    (tmp_path / "run.lock").write_text(json.dumps({"pid": 999999999, "week": "2026-W01"}))
    runner = Runner(tmp_path, fake_run())
    runner.start("2026-W36")  # would raise Busy if the stale lock were obeyed
    wait_for(runner)


def test_the_stream_replays_then_closes(tmp_path):
    runner = Runner(tmp_path, fake_run())
    job = runner.start("2026-W36")
    wait_for(runner)
    chunks = list(stream(job, 0, keepalive=0.01))
    assert sum("event: progress" in c for c in chunks) == 3
    assert "event: done" in chunks[-1]
    assert '"status": "done"' in chunks[-1]


def test_a_finished_run_carries_its_result(tmp_path):
    runner = Runner(tmp_path, fake_run())
    job = runner.start("2026-W36")
    wait_for(runner)
    assert job.status == "done" and job.result["entries"] == 3
