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


def test_reddit_run_completion_message_mentions_sidecar(
    logged_in_client, app_and_queue, monkeypatch
):
    """Runner closure must emit sidecar storage text in its final progress message."""
    app, _ = app_and_queue
    monkeypatch.setattr(
        "experimental.webui.app.run_ingest",
        lambda opts, db_path=None: _fake_ingest_result(),
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

        def set_progress(self, msg, pct):
            self.messages.append(msg)

        def set_metadata(self, **kw):
            pass

    job = _FakeJob()
    captured[0](job)

    completion = [m for m in job.messages if "complete" in m.lower()]
    assert completion, f"No completion message found in: {job.messages}"
    assert any(
        "sidecar" in m.lower() or "reddit_od.db" in m
        for m in completion
    ), f"Sidecar wording absent from completion message: {completion}"


