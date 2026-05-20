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


class FakeShodanBalanceService:
    def __init__(self) -> None:
        self.payload = {"state": "ok", "query_credits": 10, "cached": False}
        self.calls = []

    def get_balance(self, *, force=False):
        self.calls.append(bool(force))
        return dict(self.payload)


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
    fake_balance = FakeShodanBalanceService()
    app.state.shodan_balance_service = fake_balance
    return app, fake, fake_balance


@pytest.fixture
def fake_queue(app_and_queue):
    return app_and_queue[1]


@pytest.fixture
def fake_balance_service(app_and_queue):
    return app_and_queue[2]


@pytest.fixture
def client(app_and_queue):
    app = app_and_queue[0]
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
        "max_shodan_results": 100,
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
    assert "Review Preflight" in r.text


def test_preflight_requires_auth(client):
    r = client.post(
        "/api/scans/preflight",
        json={"protocols": ["smb"], "max_shodan_results": 100},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_preflight_missing_csrf(logged_in_client):
    r = logged_in_client.post(
        "/api/scans/preflight",
        json={"protocols": ["smb"], "max_shodan_results": 100},
    )
    assert r.status_code == 403


def test_preflight_bad_origin(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans/preflight",
        headers={"X-CSRF-Token": csrf, "origin": "http://attacker.com"},
        json={"protocols": ["smb"], "max_shodan_results": 100},
    )
    assert r.status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"protocols": [], "max_shodan_results": 100},
        {"protocols": ["smb", "smb"], "max_shodan_results": 100},
        {"protocols": ["ssh"], "max_shodan_results": 100},
        {"protocols": ["smb"], "max_shodan_results": 0},
        {"protocols": ["smb"], "max_shodan_results": 100, "extra": "x"},
    ],
)
def test_preflight_invalid_payload_returns_400(logged_in_client, payload):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans/preflight",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid payload"


def test_preflight_estimates_single_protocol_and_post_balance(
    logged_in_client, fake_balance_service
):
    fake_balance_service.payload = {"state": "ok", "query_credits": 11, "cached": False}
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans/preflight",
        headers={"X-CSRF-Token": csrf},
        json={"protocols": ["smb"], "max_shodan_results": 100},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["estimated_query_credits_total"] == 1
    assert payload["estimated_query_credits_by_protocol"] == {"smb": 1}
    assert payload["estimated_post_scan_balance"] == 10
    assert payload["balance"]["state"] == "ok"
    assert payload["dashboard_url"] == "https://developer.shodan.io/dashboard"
    assert fake_balance_service.calls[-1] is True


def test_preflight_estimates_multi_protocol(logged_in_client, fake_balance_service):
    fake_balance_service.payload = {"state": "ok", "query_credits": 20, "cached": False}
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans/preflight",
        headers={"X-CSRF-Token": csrf},
        json={"protocols": ["smb", "ftp", "http"], "max_shodan_results": 250},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["estimated_query_credits_total"] == 9
    assert payload["estimated_query_credits_by_protocol"] == {"smb": 3, "ftp": 3, "http": 3}
    assert payload["estimated_post_scan_balance"] == 11


def test_preflight_no_key_suppresses_post_balance(logged_in_client, fake_balance_service):
    fake_balance_service.payload = {"state": "no_key", "cached": False}
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans/preflight",
        headers={"X-CSRF-Token": csrf},
        json={"protocols": ["ftp"], "max_shodan_results": 1000},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["estimated_query_credits_total"] == 10
    assert payload["estimated_post_scan_balance"] is None
    assert payload["balance"]["state"] == "no_key"


def test_preflight_unavailable_suppresses_post_balance(logged_in_client, fake_balance_service):
    fake_balance_service.payload = {"state": "unavailable", "reason": "network", "cached": False}
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans/preflight",
        headers={"X-CSRF-Token": csrf},
        json={"protocols": ["http"], "max_shodan_results": 100},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["estimated_query_credits_total"] == 1
    assert payload["estimated_post_scan_balance"] is None
    assert payload["balance"]["state"] == "unavailable"


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


def test_submit_invalid_max_shodan_results(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans",
        headers={"X-CSRF-Token": csrf},
        json=_valid_payload(max_shodan_results=0),
    )
    assert r.status_code == 422


def test_submit_probe_on_ftp(logged_in_client, fake_queue):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans",
        headers={"X-CSRF-Token": csrf},
        json=_valid_payload(protocol="ftp", run_probe_after_scan=True),
    )
    assert r.status_code == 202
    assert fake_queue.submitted[0].protocol == "ftp"
    assert fake_queue.submitted[0].run_probe_after_scan is True


def test_submit_probe_on_http(logged_in_client, fake_queue):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans",
        headers={"X-CSRF-Token": csrf},
        json=_valid_payload(protocol="http", run_probe_after_scan=True),
    )
    assert r.status_code == 202
    assert fake_queue.submitted[0].protocol == "http"
    assert fake_queue.submitted[0].run_probe_after_scan is True


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
    assert fake_queue.submitted[0].max_shodan_results == 100


def test_submit_valid_custom_max_results(logged_in_client, fake_queue):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/api/scans",
        headers={"X-CSRF-Token": csrf},
        json=_valid_payload(max_shodan_results=500),
    )
    assert r.status_code == 202
    assert fake_queue.submitted[0].max_shodan_results == 500


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


def test_get_scans_queue_requires_auth(client):
    r = client.get("/api/scans")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_get_scans_queue_snapshot(logged_in_client, fake_queue):
    running = ScanTask(task_id="task-running", request=ScanRequest(protocol="smb"))
    running.status = TaskStatus.RUNNING
    queued = ScanTask(task_id="task-queued", request=ScanRequest(protocol="ftp"))
    queued.status = TaskStatus.QUEUED
    fake_queue.tasks[running.task_id] = running
    fake_queue.tasks[queued.task_id] = queued

    r = logged_in_client.get("/api/scans")

    assert r.status_code == 200
    payload = r.json()
    assert payload["active"]["task_id"] == "task-running"
    assert len(payload["queued"]) == 1
    assert payload["queued"][0]["task_id"] == "task-queued"


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
