"""Page render and auth-protection tests for C6 / O3."""

import json
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
def config_path(tmp_path):
    return tmp_path / "webui.json"


@pytest.fixture
def client(creds, cfg_no_tls, config_path):
    app = create_app(cfg=cfg_no_tls, creds_path=creds, config_path=config_path)
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def logged_in(client):
    r = client.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    return client


def _csrf(logged_in):
    dash = logged_in.get("/dashboard")
    assert dash.status_code == 200
    m = re.search(r'name="csrf-token" content="([^"]+)"', dash.text)
    assert m, "csrf-token meta tag not found"
    return m.group(1)


# --- Unauthenticated access ---

def test_login_page_renders_unauthenticated(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Sign in" in r.text


def test_health_unprotected(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_dashboard_redirects_unauthenticated(client):
    r = client.get("/dashboard")
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_scans_redirects_unauthenticated(client):
    r = client.get("/scans")
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_results_redirects_unauthenticated(client):
    r = client.get("/results")
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_config_redirects_unauthenticated(client):
    r = client.get("/config")
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


# --- Authenticated page renders ---

def test_dashboard_renders_authenticated(logged_in):
    r = logged_in.get("/dashboard")
    assert r.status_code == 200
    assert "Dashboard" in r.text
    assert "127.0.0.1" in r.text
    assert "Shodan Balance:" in r.text
    assert 'id="shodan-balance-status"' in r.text
    assert 'id="shodan-balance-refresh"' in r.text
    assert 'id="prefs-consent-banner"' in r.text
    assert 'id="prefs-consent-yes"' in r.text
    assert 'id="prefs-consent-no"' in r.text
    assert '/static/prefs.js' in r.text
    assert '/static/dashboard.js' in r.text


def test_scans_renders_authenticated(logged_in):
    r = logged_in.get("/scans")
    assert r.status_code == 200
    assert "SMB" in r.text
    assert "Queue Scan" in r.text
    assert '/static/scans.js' in r.text


def test_results_renders_authenticated(logged_in):
    r = logged_in.get("/results")
    assert r.status_code == 200
    assert "Results" in r.text
    assert "Export DB" in r.text
    assert '/static/results.js' in r.text


def test_config_renders_authenticated(logged_in, cfg_no_tls):
    r = logged_in.get("/config")
    assert r.status_code == 200
    assert "Bind address" in r.text
    assert str(cfg_no_tls.port) in r.text
    assert "Browser Preference Storage" in r.text
    assert 'id="prefs-enable-btn"' in r.text
    assert 'id="prefs-disable-btn"' in r.text
    assert 'id="prefs-clear-btn"' in r.text


# --- Config POST ---

def test_config_post_requires_csrf(logged_in):
    payload = {
        "bind_address": "127.0.0.1",
        "port": 5480,
        "remote_enabled": False,
        "tls_enabled": False,
        "tls_allow_insecure_remote": False,
        "tls_cert": "",
        "tls_key": "",
        "allowed_cidrs": ["127.0.0.1/32"],
        "session_timeout_idle_min": 30,
        "session_timeout_absolute_hr": 8,
    }
    r = logged_in.post(
        "/config",
        json=payload,
        headers={"Origin": "http://testserver"},
    )
    assert r.status_code == 403


def _valid_config_payload():
    return {
        "bind_address": "127.0.0.1",
        "port": 5480,
        "remote_enabled": False,
        "tls_enabled": False,
        "tls_allow_insecure_remote": False,
        "tls_cert": "",
        "tls_key": "",
        "allowed_cidrs": ["127.0.0.1/32"],
        "session_timeout_idle_min": 30,
        "session_timeout_absolute_hr": 8,
    }


def test_config_post_saves_valid_config(logged_in, config_path):
    token = _csrf(logged_in)
    r = logged_in.post(
        "/config",
        json=_valid_config_payload(),
        headers={"Origin": "http://testserver", "X-CSRF-Token": token},
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is True
    assert config_path.exists()


def test_config_post_unit_conversion(logged_in, config_path):
    """idle_min*60 and abs_hr*3600 must be stored as seconds."""
    token = _csrf(logged_in)
    payload = _valid_config_payload()
    payload["session_timeout_idle_min"] = 30
    payload["session_timeout_absolute_hr"] = 8
    r = logged_in.post(
        "/config",
        json=payload,
        headers={"Origin": "http://testserver", "X-CSRF-Token": token},
    )
    assert r.status_code == 200
    saved = json.loads(config_path.read_text())
    assert saved["session_timeout_idle"] == 30 * 60
    assert saved["session_timeout_absolute"] == 8 * 3600


def test_config_post_preserves_enabled_flag(creds, config_path):
    """Saving config from the web form must not silently disable Web UI."""
    cfg_enabled = WebUIConfig(enabled=True, tls=TLSConfig(enabled=False))
    app = create_app(cfg=cfg_enabled, creds_path=creds, config_path=config_path)
    c = TestClient(app, follow_redirects=False)
    r_login = c.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r_login.status_code == 200

    token = _csrf(c)
    r = c.post(
        "/config",
        json=_valid_config_payload(),
        headers={"Origin": "http://testserver", "X-CSRF-Token": token},
    )
    assert r.status_code == 200

    saved = json.loads(config_path.read_text())
    assert saved["enabled"] is True


def test_config_post_rejects_invalid_bind(logged_in):
    token = _csrf(logged_in)
    payload = _valid_config_payload()
    payload["bind_address"] = "not-an-ip"
    r = logged_in.post(
        "/config",
        json=payload,
        headers={"Origin": "http://testserver", "X-CSRF-Token": token},
    )
    assert r.status_code == 400
    assert "error" in r.json()


def test_config_post_writes_to_tmp_path_not_home(logged_in, config_path, monkeypatch):
    """Confirm writes go to config_path, not ~/.dirracuda."""
    import pathlib
    home_cfg = pathlib.Path.home() / ".dirracuda" / "conf" / "webui.json"
    existed_before = home_cfg.exists()

    token = _csrf(logged_in)
    r = logged_in.post(
        "/config",
        json=_valid_config_payload(),
        headers={"Origin": "http://testserver", "X-CSRF-Token": token},
    )
    assert r.status_code == 200
    assert config_path.exists()
    # Home config must not have been created by this test
    if not existed_before:
        assert not home_cfg.exists()


# ---------------------------------------------------------------------------
# O3 — Security headers, CSP, inline-script/style elimination
# ---------------------------------------------------------------------------

_INLINE_SCRIPT_RE = re.compile(r'<script(?![^>]*\bsrc=)[^>]*>', re.IGNORECASE)
_INLINE_STYLE_RE = re.compile(r'\sstyle\s*=', re.IGNORECASE)


def _assert_no_inline_script(html):
    assert not _INLINE_SCRIPT_RE.search(html), (
        "Unexpected inline <script> tag (no src=): " + str(_INLINE_SCRIPT_RE.findall(html))
    )


def test_security_headers_on_html_route(logged_in):
    r = logged_in.get("/dashboard")
    assert r.status_code == 200
    h = {k.lower(): v for k, v in r.headers.items()}
    assert "content-security-policy" in h
    csp = h["content-security-policy"]
    assert "unsafe-inline" not in csp
    assert "script-src 'self'" in csp
    assert h.get("x-frame-options") == "DENY"
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("referrer-policy") == "no-referrer"
    assert "no-store" in h.get("cache-control", "")
    assert "strict-transport-security" not in h


def test_security_headers_on_json_api_route(logged_in):
    r = logged_in.get("/api/dashboard/shodan-balance")
    h = {k.lower(): v for k, v in r.headers.items()}
    assert "x-content-type-options" in h
    assert "no-store" in h.get("cache-control", "")


def test_cache_control_no_store_on_export_route(logged_in):
    token = _csrf(logged_in)
    r = logged_in.post(
        "/api/export",
        json={},
        headers={"Origin": "http://testserver", "X-CSRF-Token": token},
    )
    h = {k.lower(): v for k, v in r.headers.items()}
    assert "no-store" in h.get("cache-control", ""), (
        "Export API response must have Cache-Control: no-store"
    )


def test_no_inline_script_in_dashboard(logged_in):
    r = logged_in.get("/dashboard")
    _assert_no_inline_script(r.text)
    assert '/static/dashboard.js' in r.text


def test_no_inline_script_in_scans(logged_in):
    r = logged_in.get("/scans")
    _assert_no_inline_script(r.text)
    assert '/static/scans.js' in r.text


def test_no_inline_script_in_results(logged_in):
    r = logged_in.get("/results")
    _assert_no_inline_script(r.text)
    assert '/static/results.js' in r.text


def test_no_inline_script_in_config(logged_in):
    r = logged_in.get("/config")
    _assert_no_inline_script(r.text)
    assert '/static/config.js' in r.text


def test_no_inline_script_in_login(client):
    r = client.get("/login")
    _assert_no_inline_script(r.text)
    assert '/static/login.js' in r.text


def test_no_inline_style_attr_on_key_routes(logged_in, client):
    login_r = client.get("/login")
    assert login_r.status_code == 200
    assert not _INLINE_STYLE_RE.search(login_r.text), "Inline style= attr found on /login"
    for path in ["/dashboard", "/scans", "/results", "/config", "/account"]:
        r = logged_in.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        assert not _INLINE_STYLE_RE.search(r.text), f"Inline style= attr found on {path}"
