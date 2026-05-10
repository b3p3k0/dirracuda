"""Route integration tests for login, logout, and dashboard."""

import re

import pytest
from fastapi.testclient import TestClient

from experimental.webui.app import create_app
from experimental.webui.auth import set_password
from experimental.webui.config import TLSConfig, WebUIConfig

_USERNAME = "testuser"
_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def creds(tmp_path):
    p = tmp_path / "creds.json"
    set_password(_USERNAME, _PASSWORD, path=p)
    return p


@pytest.fixture
def cfg_no_tls():
    return WebUIConfig(tls=TLSConfig(enabled=False))


@pytest.fixture
def client(creds, cfg_no_tls):
    app = create_app(cfg=cfg_no_tls, creds_path=creds)
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


# --- Unprotected routes ---

def test_health_unprotected(client):
    assert client.get("/health").status_code == 200


def test_root_redirects_to_dashboard(client):
    r = client.get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"


def test_login_page_accessible(client):
    assert client.get("/login").status_code == 200


# --- Login ---

def test_login_success_returns_200_and_sets_cookie(client):
    r = client.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    assert "dirracuda-session" in r.cookies


def test_login_wrong_password_returns_401(client):
    r = client.post("/login", json={"username": _USERNAME, "password": "wrong"})
    assert r.status_code == 401
    assert "dirracuda-session" not in r.cookies


def test_login_mismatched_origin_returns_403(client):
    r = client.post(
        "/login",
        json={"username": _USERNAME, "password": _PASSWORD},
        headers={"origin": "http://attacker.com"},
    )
    assert r.status_code == 403


def test_login_cookie_flags_tls_disabled(client):
    r = client.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    sc = r.headers.get("set-cookie", "")
    assert "dirracuda-session=" in sc
    assert "HttpOnly" in sc
    assert "samesite=strict" in sc.lower()
    assert "Path=/" in sc
    assert "secure" not in sc.lower()


def test_login_cookie_name_and_secure_flag_tls_enabled(creds):
    cfg = WebUIConfig(tls=TLSConfig(enabled=True))
    app = create_app(cfg=cfg, creds_path=creds)
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    sc = r.headers.get("set-cookie", "")
    assert "__Host-dirracuda-session=" in sc
    assert "secure" in sc.lower()
    assert "HttpOnly" in sc


# --- Dashboard ---

def test_dashboard_requires_auth(client):
    r = client.get("/dashboard")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_dashboard_accessible_with_valid_session(logged_in_client):
    r = logged_in_client.get("/dashboard")
    assert r.status_code == 200
    assert _USERNAME in r.text


# --- Logout ---

def test_logout_clears_session(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post("/logout", headers={"X-CSRF-Token": csrf}, json={})
    assert r.status_code == 200
    r2 = logged_in_client.get("/dashboard")
    assert r2.status_code == 303
    assert r2.headers["location"] == "/login"


def test_logout_missing_csrf_returns_403(logged_in_client):
    r = logged_in_client.post("/logout", json={})
    assert r.status_code == 403


def test_logout_mismatched_origin_returns_403(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    r = logged_in_client.post(
        "/logout",
        headers={"X-CSRF-Token": csrf, "origin": "http://attacker.com"},
        json={},
    )
    assert r.status_code == 403


def test_logout_without_session_is_boring(client):
    # No login -- logout clears cookie and returns 200 without revealing session state.
    r = client.post("/logout", json={})
    assert r.status_code == 200
    sc = r.headers.get("set-cookie", "")
    assert "dirracuda-session=" in sc
    assert "max-age=0" in sc.lower()
    assert "HttpOnly" in sc
    assert "samesite=strict" in sc.lower()
    assert "Path=/" in sc
    assert "secure" not in sc.lower()


def test_logout_clears_cookie_tls_enabled(creds):
    cfg = WebUIConfig(tls=TLSConfig(enabled=True))
    app = create_app(cfg=cfg, creds_path=creds)
    # Use HTTPS base URL so httpx sends the Secure-flagged __Host- cookie.
    c = TestClient(app, follow_redirects=False, base_url="https://testserver")
    r_login = c.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r_login.status_code == 200
    dash = c.get("/dashboard")
    assert dash.status_code == 200
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', dash.text).group(1)
    r = c.post("/logout", headers={"X-CSRF-Token": csrf}, json={})
    assert r.status_code == 200
    sc = r.headers.get("set-cookie", "")
    assert "__Host-dirracuda-session=" in sc
    assert "max-age=0" in sc.lower()
    assert "secure" in sc.lower()
    assert "HttpOnly" in sc
    assert "samesite=strict" in sc.lower()
    assert "Path=/" in sc


def test_session_not_reused_after_logout(logged_in_client):
    csrf = _csrf_from_dashboard(logged_in_client)
    logged_in_client.post("/logout", headers={"X-CSRF-Token": csrf}, json={})
    r = logged_in_client.get("/dashboard")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
