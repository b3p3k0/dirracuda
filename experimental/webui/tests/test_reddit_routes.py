"""Route integration tests for Reddit discovery endpoints (C32)."""

import json
import re
import types
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from experimental.webui.app import create_app
from experimental.webui.auth import set_password
from experimental.webui.config import TLSConfig, WebUIConfig
from experimental.webui.shared_jobs import SharedJobQueue
from experimental.webui.tasks import CancelResult, ScanRequest, ScanTask, TaskStatus

_USERNAME = "reddit_tester"
_PASSWORD = "correct-horse-battery-staple"


class FakeScanQueue:
    def __init__(self) -> None:
        self.tasks = {}

    def submit(self, request: ScanRequest) -> ScanTask:
        task = ScanTask(task_id=f"task-{len(self.tasks) + 1}", request=request)
        task.status = TaskStatus.QUEUED
        self.tasks[task.task_id] = task
        return task

    def get_task(self, task_id: str):
        return self.tasks.get(task_id)

    def cancel(self, task_id: str) -> CancelResult:
        return CancelResult.NOT_FOUND

    def queue_status(self) -> dict:
        return {"active": None, "queued": []}


@pytest.fixture
def creds(tmp_path):
    p = tmp_path / "creds.json"
    set_password(_USERNAME, _PASSWORD, path=p)
    return p


@pytest.fixture
def cfg_no_tls():
    return WebUIConfig(tls=TLSConfig(enabled=False))


@pytest.fixture
def main_config_path(tmp_path):
    p = tmp_path / "config.json"
    db_path = tmp_path / "main.db"
    p.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
    return p


@pytest.fixture
def app_and_queue(creds, cfg_no_tls, main_config_path):
    app = create_app(cfg=cfg_no_tls, creds_path=creds, main_config_path=main_config_path)
    fake = FakeScanQueue()
    try:
        app.state.shared_jobs.shutdown()
    except Exception:
        pass
    app.state.scan_queue = fake
    app.state.shared_jobs = SharedJobQueue(fake)
    return app, fake


@pytest.fixture
def client(app_and_queue):
    return TestClient(app_and_queue[0], follow_redirects=False)


@pytest.fixture
def logged_in_client(client):
    r = client.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    return client


def _csrf_from_dashboard(client):
    dash = client.get("/dashboard")
    assert dash.status_code == 200
    m = re.search(r'name="csrf-token" content="([^"]+)"', dash.text)
    assert m, "csrf-token meta tag not found"
    return m.group(1)


def _run_payload(**overrides):
    payload = {
        "mode": "feed",
        "sort": "new",
        "max_posts": 10,
        "max_pages": 1,
    }
    payload.update(overrides)
    return payload


def _fake_ingest_result():
    return types.SimpleNamespace(
        error=None,
        pages_fetched=1,
        posts_stored=5,
        posts_skipped=0,
        targets_stored=3,
        targets_deduped=0,
        probe_total=0,
        probe_clean=0,
        probe_issue=0,
        probe_unprobed=0,
        probe_skipped=0,
    )


# ---------------------------------------------------------------------------
# Page auth guard
# ---------------------------------------------------------------------------

def test_reddit_page_requires_auth(client):
    r = client.get("/scans/reddit")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


# ---------------------------------------------------------------------------
# /api/reddit/run
# ---------------------------------------------------------------------------

def test_reddit_run_requires_auth(client):
    r = client.post("/api/reddit/run", json=_run_payload())
    assert r.status_code == 303


def test_reddit_run_missing_csrf(logged_in_client):
    r = logged_in_client.post("/api/reddit/run", json=_run_payload())
    assert r.status_code == 403


def test_reddit_run_bad_origin(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/reddit/run",
        headers={"X-CSRF-Token": csrf, "origin": "http://attacker.com"},
        json=_run_payload(),
    )
    assert r.status_code == 403


def test_reddit_run_queues_job(logged_in_client, monkeypatch):
    monkeypatch.setattr(
        "experimental.webui.app.run_ingest",
        lambda opts: _fake_ingest_result(),
    )
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/reddit/run",
        headers={"X-CSRF-Token": csrf},
        json=_run_payload(),
    )
    assert r.status_code == 202
    data = r.json()
    assert "job_id" in data
    assert data["status"] in {"queued", "running", "done", "failed"}


def test_reddit_run_search_missing_query(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/reddit/run",
        headers={"X-CSRF-Token": csrf},
        json={"mode": "search", "sort": "new", "max_posts": 10, "max_pages": 1},
    )
    assert r.status_code == 422


def test_reddit_run_user_missing_username(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/reddit/run",
        headers={"X-CSRF-Token": csrf},
        json={"mode": "user", "sort": "new", "max_posts": 10, "max_pages": 1},
    )
    assert r.status_code == 422


def test_reddit_run_completion_message_does_not_mention_sidecar(
    logged_in_client, app_and_queue, monkeypatch
):
    """C10: runner completion message must not reference sidecar or manual promotion."""
    app, _ = app_and_queue
    monkeypatch.setattr(
        "experimental.webui.app.run_ingest",
        lambda opts, db_path=None: _fake_ingest_result(),
    )
    monkeypatch.setattr(
        "experimental.webui.app.sync_reddit_to_main_db",
        lambda keys, db_path=None: {"inserted": 0, "updated": 0, "skipped": 0, "processed": 0, "failed": 0, "cancelled": 0},
    )

    captured = []

    def _capture_runner(*, source, kind, label, runner, metadata=None, cancel_callback=None):
        captured.append(runner)
        return MagicMock(job_id="j1", status=MagicMock(value="queued"))

    monkeypatch.setattr(app.state.shared_jobs, "submit_external", _capture_runner)

    csrf = _csrf_from_dashboard(logged_in_client)
    logged_in_client.post(
        "/api/reddit/run",
        json=_run_payload(),
        headers={"X-CSRF-Token": csrf},
    )

    assert captured, "submit_external was not called — runner not captured"

    class _FakeJob:
        def __init__(self):
            self.messages = []
            self.metadata: dict = {}

        def set_progress(self, msg, pct):
            self.messages.append(msg)

        def set_metadata(self, **kw):
            self.metadata.update(kw)

    job = _FakeJob()
    captured[0](job)

    completion = [m for m in job.messages if "complete" in m.lower()]
    assert completion, f"No completion message found in: {job.messages}"
    assert not any("sidecar" in m.lower() or "reddit_od.db" in m for m in completion), \
        f"Stale sidecar wording in completion message: {completion}"


# ---------------------------------------------------------------------------
# C10 primary DB cutover tests
# ---------------------------------------------------------------------------

def _run_the_runner(app, monkeypatch, fake_run, fake_sync, logged_in_client):
    """Helper: capture and execute the runner closure via a fake submit_external."""
    monkeypatch.setattr("experimental.webui.app.run_ingest", fake_run)
    monkeypatch.setattr("experimental.webui.app.sync_reddit_to_main_db", fake_sync)

    captured = []

    def _capture(*, source, kind, label, runner, metadata=None, cancel_callback=None):
        captured.append(runner)
        return MagicMock(job_id="j1", status=MagicMock(value="queued"))

    monkeypatch.setattr(app.state.shared_jobs, "submit_external", _capture)
    csrf = _csrf_from_dashboard(logged_in_client)
    logged_in_client.post("/api/reddit/run", json=_run_payload(),
                          headers={"X-CSRF-Token": csrf})
    assert captured, "submit_external was not called"

    class _FakeJob:
        def __init__(self):
            self.meta: dict = {}

        def set_progress(self, msg, pct):
            pass

        def set_metadata(self, **kw):
            self.meta.update(kw)

    job = _FakeJob()
    captured[0](job)
    return job


def test_reddit_run_passes_primary_db_path(
    logged_in_client, app_and_queue, monkeypatch
):
    """C10: run_ingest must be called with db_path=app.state.db_path."""
    app, _ = app_and_queue
    captured_db: list = []

    def _fake_run(opts, db_path=None):
        captured_db.append(db_path)
        return _fake_ingest_result()

    _run_the_runner(
        app, monkeypatch,
        fake_run=_fake_run,
        fake_sync=lambda keys, db_path=None: {"inserted": 0, "updated": 0, "skipped": 0, "processed": 0, "failed": 0, "cancelled": 0},
        logged_in_client=logged_in_client,
    )

    assert len(captured_db) == 1
    assert captured_db[0] == app.state.db_path


def test_reddit_run_job_metadata_includes_sync_totals(
    logged_in_client, app_and_queue, monkeypatch
):
    """C10: job metadata must include sync_inserted, sync_updated, sync_skipped etc."""
    app, _ = app_and_queue

    job = _run_the_runner(
        app, monkeypatch,
        fake_run=lambda opts, db_path=None: _fake_ingest_result(),
        fake_sync=lambda keys, db_path=None: {
            "inserted": 2, "updated": 1, "skipped": 0,
            "processed": 3, "failed": 0, "cancelled": 0,
        },
        logged_in_client=logged_in_client,
    )

    assert job.meta.get("sync_inserted") == 2
    assert job.meta.get("sync_updated") == 1
    assert "sync_skipped" in job.meta


def test_reddit_run_replace_cache_true_does_not_call_wipe_all(
    logged_in_client, app_and_queue, monkeypatch
):
    """C10: even with replace_cache=True, wipe_all must not be called on primary DB."""
    import experimental.redseek.store as _store
    app, _ = app_and_queue
    wipe_all_called = []
    monkeypatch.setattr(_store, "wipe_all", lambda *a, **k: wipe_all_called.append(True))

    _run_the_runner(
        app, monkeypatch,
        fake_run=lambda opts, db_path=None: _fake_ingest_result(),
        fake_sync=lambda keys, db_path=None: {"inserted": 0, "updated": 0, "skipped": 0, "processed": 0, "failed": 0, "cancelled": 0},
        logged_in_client=logged_in_client,
    )

    assert not wipe_all_called, "wipe_all must not be called in WebUI primary-DB mode"


