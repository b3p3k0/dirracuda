"""Route integration tests for SearXNG discovery endpoints (C31)."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from experimental.webui.app import create_app
from experimental.webui.auth import set_password
from experimental.webui.config import TLSConfig, WebUIConfig
from experimental.webui.shared_jobs import SharedJobQueue
from experimental.webui.tasks import CancelResult, ScanRequest, ScanTask, TaskStatus

_USERNAME = "searxng_tester"
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
        "instance_url": "http://searxng.example.com",
        "query": "inurl:index.of",
        "max_results": 10,
        "bulk_probe_enabled": False,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Page auth guard
# ---------------------------------------------------------------------------

def test_searxng_page_requires_auth(client):
    r = client.get("/scans/searxng")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


# ---------------------------------------------------------------------------
# /api/searxng/preflight
# ---------------------------------------------------------------------------

def test_searxng_preflight_requires_auth(client):
    r = client.post("/api/searxng/preflight", json={"instance_url": "http://example.com"})
    assert r.status_code == 303

def test_searxng_preflight_missing_csrf(logged_in_client):
    r = logged_in_client.post(
        "/api/searxng/preflight",
        json={"instance_url": "http://example.com"},
    )
    assert r.status_code == 403

def test_searxng_preflight_bad_origin(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/searxng/preflight",
        headers={"X-CSRF-Token": csrf, "origin": "http://attacker.com"},
        json={"instance_url": "http://example.com"},
    )
    assert r.status_code == 403

def test_searxng_preflight_ok(logged_in_client, monkeypatch):
    from experimental.se_dork.models import PreflightResult
    monkeypatch.setattr(
        "experimental.webui.app.run_searxng_preflight",
        lambda url: PreflightResult(ok=True, reason_code=None, message="Instance OK."),
    )
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/searxng/preflight",
        headers={"X-CSRF-Token": csrf},
        json={"instance_url": "http://searxng.example.com"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["message"] == "Instance OK."

def test_searxng_preflight_fail(logged_in_client, monkeypatch):
    from experimental.se_dork.models import PreflightResult, INSTANCE_UNREACHABLE
    monkeypatch.setattr(
        "experimental.webui.app.run_searxng_preflight",
        lambda url: PreflightResult(ok=False, reason_code=INSTANCE_UNREACHABLE, message="Cannot reach."),
    )
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/searxng/preflight",
        headers={"X-CSRF-Token": csrf},
        json={"instance_url": "http://dead.example.com"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert "Cannot reach" in data["message"]


# ---------------------------------------------------------------------------
# /api/searxng/run
# ---------------------------------------------------------------------------

def test_searxng_run_requires_auth(client):
    r = client.post("/api/searxng/run", json=_run_payload())
    assert r.status_code == 303

def test_searxng_run_missing_csrf(logged_in_client):
    r = logged_in_client.post("/api/searxng/run", json=_run_payload())
    assert r.status_code == 403

def test_searxng_run_bad_origin(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/searxng/run",
        headers={"X-CSRF-Token": csrf, "origin": "http://attacker.com"},
        json=_run_payload(),
    )
    assert r.status_code == 403

def test_searxng_run_queues_job(logged_in_client, monkeypatch):
    from experimental.se_dork.models import RunResult, RUN_STATUS_DONE
    fake_result = RunResult(
        run_id=1, fetched_count=5, deduped_count=3,
        status=RUN_STATUS_DONE, error=None, verified_count=3,
    )
    monkeypatch.setattr(
        "experimental.webui.app.run_dork_search",
        lambda opts: fake_result,
    )
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/searxng/run",
        headers={"X-CSRF-Token": csrf},
        json=_run_payload(),
    )
    assert r.status_code == 202
    data = r.json()
    assert "job_id" in data
    assert data["status"] in {"queued", "running", "done", "failed"}

