"""Route integration tests for Web UI scan launch and polling endpoints."""

import re

import pytest
from fastapi.testclient import TestClient

from experimental.webui.app import create_app
from experimental.webui.auth import set_password
from experimental.webui.config import TLSConfig, WebUIConfig
from experimental.webui.tasks import CancelResult, ScanRequest, ScanTask, TaskStatus

_USERNAME = "scanuser"
_PASSWORD = "correct-horse-battery-staple"


class FakeScanQueue:
    def __init__(self) -> None:
        self.tasks = {}
        self.submitted = []
        self.cancel_result = CancelResult.OK

    def submit(self, request: ScanRequest) -> ScanTask:
        task = ScanTask(task_id=f"task-{len(self.tasks) + 1}", request=request)
        task.status = TaskStatus.QUEUED
        self.tasks[task.task_id] = task
        self.submitted.append(request)
        return task

    def get_task(self, task_id: str):
        return self.tasks.get(task_id)

    def cancel(self, task_id: str) -> CancelResult:
        if self.cancel_result == CancelResult.NOT_FOUND:
            return CancelResult.NOT_FOUND
        task = self.tasks.get(task_id)
        if task is None:
            return CancelResult.NOT_FOUND
        if self.cancel_result == CancelResult.TERMINAL:
            task.status = TaskStatus.DONE
            return CancelResult.TERMINAL
        task.status = TaskStatus.CANCELLED
        return CancelResult.OK

    def queue_status(self) -> dict:
        active = next(
            (t.to_dict() for t in self.tasks.values() if t.status == TaskStatus.RUNNING),
            None,
        )
        queued = [t.to_dict() for t in self.tasks.values() if t.status == TaskStatus.QUEUED]
        return {"active": active, "queued": queued}


@pytest.fixture
def creds(tmp_path):
    p = tmp_path / "creds.json"
    set_password(_USERNAME, _PASSWORD, path=p)
    return p


@pytest.fixture
def cfg_no_tls():
    return WebUIConfig(tls=TLSConfig(enabled=False))


@pytest.fixture
def app_and_queue(creds, cfg_no_tls):
    app = create_app(cfg=cfg_no_tls, creds_path=creds)
    fake = FakeScanQueue()
    app.state.scan_queue = fake
    return app, fake


@pytest.fixture
def fake_queue(app_and_queue):
    return app_and_queue[1]


@pytest.fixture
def client(app_and_queue):
    app, _ = app_and_queue
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def logged_in_client(client):
    r = client.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    return client


def _csrf_from_dashboard(client):
    dash = client.get("/dashboard")
    assert dash.status_code == 200
    m = re.search(r'name="csrf-token" content="([^"]+)"', dash.text)
    assert m, "csrf-token meta tag not found in dashboard"
    return m.group(1)


def _valid_payload(**overrides):
    payload = {
        "protocol": "smb",
        "countries": ["US"],
        "run_probe_after_scan": False,
        "filters": "",
    }
    payload.update(overrides)
    return payload


def test_scans_page_requires_auth(client):
    r = client.get("/scans")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_scans_page_authenticated(logged_in_client):
    r = logged_in_client.get("/scans")
    assert r.status_code == 200
    assert "Queue Scan" in r.text


def test_submit_requires_auth(client):
    r = client.post("/api/scans", json=_valid_payload())
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_submit_missing_csrf(logged_in_client):
    r = logged_in_client.post("/api/scans", json=_valid_payload())
    assert r.status_code == 403


def test_submit_bad_origin(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans",
        headers={"X-CSRF-Token": csrf, "origin": "http://attacker.com"},
        json=_valid_payload(),
    )
    assert r.status_code == 403


def test_submit_invalid_protocol(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans",
        headers={"X-CSRF-Token": csrf},
        json=_valid_payload(protocol="ssh"),
    )
    assert r.status_code == 422


def test_submit_invalid_country(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans",
        headers={"X-CSRF-Token": csrf},
        json=_valid_payload(countries=["ZZ"]),
    )
    assert r.status_code == 422


def test_submit_probe_on_ftp(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans",
        headers={"X-CSRF-Token": csrf},
        json=_valid_payload(protocol="ftp", run_probe_after_scan=True),
    )
    assert r.status_code == 422


def test_submit_probe_string_coercion_rejected(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans",
        headers={"X-CSRF-Token": csrf},
        json=_valid_payload(run_probe_after_scan="false"),
    )
    assert r.status_code == 422


def test_submit_valid_smb(logged_in_client, fake_queue):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans",
        headers={"X-CSRF-Token": csrf},
        json=_valid_payload(),
    )
    assert r.status_code == 202
    assert r.json()["task_id"] == "task-1"
    assert fake_queue.submitted[0].protocol == "smb"


def test_submit_valid_ftp(logged_in_client, fake_queue):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans",
        headers={"X-CSRF-Token": csrf},
        json=_valid_payload(protocol="ftp", countries=[]),
    )
    assert r.status_code == 202
    assert fake_queue.submitted[0].protocol == "ftp"


def test_submit_valid_http(logged_in_client, fake_queue):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans",
        headers={"X-CSRF-Token": csrf},
        json=_valid_payload(protocol="http", countries=[]),
    )
    assert r.status_code == 202
    assert fake_queue.submitted[0].protocol == "http"


def test_get_scan_requires_auth(client):
    r = client.get("/api/scans/task-1")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_get_scan_not_found(logged_in_client):
    r = logged_in_client.get("/api/scans/missing")
    assert r.status_code == 404


def test_get_scan_valid(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    submitted = logged_in_client.post(
        "/api/scans",
        headers={"X-CSRF-Token": csrf},
        json=_valid_payload(),
    )
    task_id = submitted.json()["task_id"]

    r = logged_in_client.get(f"/api/scans/{task_id}")

    assert r.status_code == 200
    assert r.json()["status"] == "queued"


def test_cancel_requires_auth(client):
    r = client.post("/api/scans/task-1/cancel")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_cancel_missing_csrf(logged_in_client, fake_queue):
    fake_queue.tasks["task-1"] = ScanTask(task_id="task-1", request=ScanRequest(protocol="smb"))
    r = logged_in_client.post("/api/scans/task-1/cancel", json={})
    assert r.status_code == 403


def test_cancel_not_found(logged_in_client, fake_queue):
    fake_queue.cancel_result = CancelResult.NOT_FOUND
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans/missing/cancel", headers={"X-CSRF-Token": csrf}, json={}
    )
    assert r.status_code == 404


def test_cancel_terminal_task(logged_in_client, fake_queue):
    fake_queue.cancel_result = CancelResult.TERMINAL
    fake_queue.tasks["task-1"] = ScanTask(task_id="task-1", request=ScanRequest(protocol="smb"))
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans/task-1/cancel", headers={"X-CSRF-Token": csrf}, json={}
    )
    assert r.status_code == 409
    assert r.json() == {"ok": False, "status": "done"}


def test_cancel_valid(logged_in_client, fake_queue):
    fake_queue.tasks["task-1"] = ScanTask(task_id="task-1", request=ScanRequest(protocol="smb"))
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans/task-1/cancel", headers={"X-CSRF-Token": csrf}, json={}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
