"""Every route, through TestClient, with a fake pipeline. No week is ever run.

Assertions are on rendered text rather than markup: a test that pins a class name
fails when the page is restyled and passes when the page says the wrong thing,
which is exactly backwards.
"""

from __future__ import annotations

import json
import threading

import pytest

from digest.config import legacy, paths
from digest.jobs import Runner

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from digest.ui.app import create_app  # noqa: E402

LEGACY = """
[run]
output_dir = "~/digests"
[models]
provider = "ollama"
classify = "qwen3:30b"
synthesize = "gemma3:27b"
[[sources]]
name = "A feed"
url = "https://example.com/rss"
"""


def fake_pipeline(events=("fetch", "classify"), block=None, fail=False):
    def run(*, week, progress, cancel, **kwargs):
        for stage in events:
            progress(stage, {"week": week})
            if block is not None:
                block.wait(2)
        if fail:
            raise RuntimeError("nope")
        return {"week": week}
    return run


@pytest.fixture
def installed(digest_home, tmp_path):
    source = tmp_path / "digest.toml"
    source.write_text(LEGACY)
    legacy.import_legacy(source)
    return digest_home


@pytest.fixture
def client(installed):
    runner = Runner(paths.data_dir(), fake_pipeline())
    app = create_app(runner)
    app.state.test_runner = runner
    return TestClient(app)


def test_a_machine_with_no_config_is_sent_to_setup(digest_home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no digest.toml here either
    client = TestClient(create_app(Runner(digest_home, fake_pipeline())))
    assert client.get("/", follow_redirects=False).headers["location"] == "/setup"


def test_setup_offers_to_import_an_existing_checkout(digest_home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "digest.toml").write_text(LEGACY)
    client = TestClient(create_app(Runner(digest_home, fake_pipeline())))
    body = client.get("/setup").text
    assert "Found an existing setup" in body and "digest.toml" in body


def test_setup_writes_a_working_config(digest_home):
    client = TestClient(create_app(Runner(digest_home, fake_pipeline())))
    response = client.post("/setup", data={
        "lens": "architecture-of-rule", "classify": "qwen3:30b",
        "synthesize": "gemma3:27b", "provider": "ollama",
        "minutes": "58", "folder": "~/digests",
    }, follow_redirects=False)
    assert response.headers["location"] == "/"
    assert paths.config_file().exists()
    assert paths.lens_file().read_text().startswith("LENS:")
    # A preset brings its feeds, or the first week is quiet for no good reason.
    assert paths.feeds_file().read_text().count("[[feed]]") == 19


def test_the_home_page_names_the_lens_rather_than_the_app(client):
    assert "architecture of rule" in client.get("/").text


def test_starting_a_run_shows_progress_and_a_way_to_stop_it(client):
    client.post("/run", data={"dry": "1"})
    client.app.state.test_runner.join(2)
    body = client.get("/").text
    assert "Stop after this entry" in body or "Run this week" in body


def test_a_second_run_is_refused_while_one_is_going(installed):
    gate = threading.Event()
    runner = Runner(paths.data_dir(), fake_pipeline(block=gate))
    client = TestClient(create_app(runner))
    client.post("/run")
    assert "already in progress" in client.post("/run").text
    gate.set()
    runner.join(2)


def test_the_progress_stream_replays_what_a_reconnecting_page_missed(installed):
    runner = Runner(paths.data_dir(), fake_pipeline())
    client = TestClient(create_app(runner))
    client.post("/run")
    runner.join(2)
    body = client.get("/progress").text
    assert body.count("event: progress") == 2
    assert "event: done" in body


def test_a_browsers_automatic_retry_resumes_from_its_header(installed):
    """EventSource cannot add a query parameter to a retry it issues itself."""
    runner = Runner(paths.data_dir(), fake_pipeline())
    client = TestClient(create_app(runner))
    client.post("/run")
    runner.join(2)
    body = client.get("/progress", headers={"Last-Event-ID": "1"}).text
    assert body.count("event: progress") == 1


def test_a_junk_resume_header_replays_rather_than_failing(installed):
    runner = Runner(paths.data_dir(), fake_pipeline())
    client = TestClient(create_app(runner))
    client.post("/run")
    runner.join(2)
    body = client.get("/progress", headers={"Last-Event-ID": ""}).text
    assert body.count("event: progress") == 2


def test_the_stream_says_idle_before_anything_has_run(client):
    assert client.get("/progress").json() == {"status": "idle"}


def test_checking_a_feed_reports_what_it_would_contribute(client, monkeypatch):
    from digest import ingest

    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>A Feed</title>
    <item><title>One</title><link>https://e.com/1</link>
    <pubDate>Wed, 02 Sep 2026 10:00:00 GMT</pubDate>
    <description>A long enough description to be worth something at all.</description>
    </item></channel></rss>"""
    monkeypatch.setattr(ingest, "fetch_bytes", lambda url, timeout=15: feed)
    body = client.post("/feeds/check", data={"url": "https://e.com/rss"}).text
    assert "A Feed" in body and "1 entries" in body


def test_a_feed_that_cannot_be_read_is_reported_not_added(client, monkeypatch):
    from digest import ingest

    def boom(url, timeout=15):
        raise OSError("no such host")

    monkeypatch.setattr(ingest, "fetch_bytes", boom)
    body = client.post("/feeds/check", data={"url": "https://nope/rss"}).text
    assert "Could not read that feed" in body
    assert "Add this feed" not in body


def test_pausing_a_feed_keeps_it_in_the_file(client):
    client.post("/feeds/toggle", data={"url": "https://example.com/rss"})
    assert "https://example.com/rss" in paths.feeds_file().read_text()
    assert "enabled = false" in paths.feeds_file().read_text()


def test_removing_a_feed_takes_it_out(client):
    client.post("/feeds/remove", data={"url": "https://example.com/rss"})
    assert "https://example.com/rss" not in paths.feeds_file().read_text()


def test_settings_saves_and_reloads(client):
    client.post("/settings", data={
        "classify": "qwen3:30b", "synthesize": "gemma3:27b", "provider": "ollama",
        "minutes": "30", "folder": "~/elsewhere", "audio": "1", "pdf": "",
    })
    body = client.get("/settings?saved=1").text
    assert "Saved." in body
    assert "elsewhere" in body  # the folder is shown expanded, as the run sees it


def test_a_bad_setting_is_refused_before_anything_is_written(client):
    before = paths.config_file().read_text()
    with pytest.raises(Exception):
        client.post("/settings", data={
            "classify": "x", "synthesize": "y", "provider": "openai",
            "minutes": "30", "folder": "~/e",
        })
    assert paths.config_file().read_text() == before


@pytest.mark.parametrize("knob", ["temperature", "batch", "seed", "num_ctx", "think"])
def test_settings_offers_no_field_for_a_measured_knob(client, knob):
    """Each has a right answer behind it; a form invites a wrong one."""
    body = client.get("/settings").text
    assert f'name="{knob}"' not in body


def test_review_of_a_week_that_was_never_run_says_so(client):
    assert "No edition stored" in client.get("/review/2026-W01").text


def test_about_says_where_the_files_are(client):
    body = client.get("/about").text
    assert str(paths.config_dir()) in body and "never bypasses a paywall" in body
