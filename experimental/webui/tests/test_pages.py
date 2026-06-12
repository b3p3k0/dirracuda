"""Page render and auth-protection tests for C6 / O3."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from experimental.webui.app import create_app
from experimental.webui.auth import set_password
from experimental.webui.config import TLSConfig, WebUIConfig, WebUIConfigError

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


def test_remote_plaintext_warning_is_visible_before_login(creds, tmp_path):
    cfg = WebUIConfig(
        bind_address="0.0.0.0",
        remote_enabled=True,
        allowed_cidrs=["127.0.0.1/32"],
        tls=TLSConfig(enabled=False, allow_insecure_remote=True),
    )
    app = create_app(
        cfg=cfg,
        creds_path=creds,
        db_path=tmp_path / "main.db",
        rl_db_path=tmp_path / "rate.db",
        main_config_path=tmp_path / "main.json",
    )
    client = TestClient(
        app,
        follow_redirects=False,
        client=("127.0.0.1", 50000),
    )

    response = client.get("/login", headers={"Host": "127.0.0.1"})

    assert response.status_code == 200
    assert "Remote HTTP (plaintext)" in response.text


def test_health_unprotected(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_dashboard_redirects_unauthenticated(client):
    r = client.get("/dashboard")
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_shodan_scans_redirects_unauthenticated(client):
    r = client.get("/scans/shodan")
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


def test_export_redirects_unauthenticated(client):
    r = client.get("/export")
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_scans_root_not_found(client):
    r = client.get("/scans")
    assert r.status_code == 404


def test_extras_root_not_found(client):
    r = client.get("/extras")
    assert r.status_code == 404


# --- Authenticated page renders ---

def test_dashboard_renders_authenticated(logged_in):
    r = logged_in.get("/dashboard")
    assert r.status_code == 200
    assert "Dashboard" in r.text
    assert "127.0.0.1" in r.text
    assert "Listening: http://127.0.0.1:2600" in r.text
    assert "Local URL: http://127.0.0.1:2600" in r.text
    assert "Shodan Balance:" in r.text
    assert 'id="shodan-balance-status"' in r.text
    assert 'id="shodan-balance-refresh"' in r.text
    assert 'id="prefs-consent-banner"' in r.text
    assert 'id="prefs-consent-yes"' in r.text
    assert 'id="prefs-consent-no"' in r.text
    assert '/static/prefs.js' in r.text
    assert '/static/dashboard.js' in r.text


def test_dashboard_nav_includes_scans_and_extras_groups(logged_in):
    r = logged_in.get("/dashboard")
    assert r.status_code == 200
    assert "/scans/shodan" in r.text
    assert "/scans/searxng" in r.text
    assert "/scans/reddit" in r.text
    assert "/extras/dorkbook" in r.text
    assert "/extras/keymaster" in r.text
    assert "<summary" in r.text


def test_scans_shodan_renders_authenticated(logged_in):
    r = logged_in.get("/scans/shodan")
    assert r.status_code == 200
    assert "Shodan Scans" in r.text
    assert "SMB" in r.text
    assert "Review Preflight" in r.text
    assert 'id="preflight-panel"' in r.text
    assert 'id="preflight-summary"' in r.text
    assert 'id="preflight-start-btn"' in r.text
    assert 'id="preflight-cancel-btn"' in r.text
    assert '/static/scans.js' in r.text


def test_scans_searxng_renders_authenticated(logged_in):
    r = logged_in.get("/scans/searxng")
    assert r.status_code == 200
    assert "SearXNG Discovery" in r.text
    assert 'id="run-btn"' in r.text
    assert 'id="probe-btn"' not in r.text
    assert 'id="promote-btn"' not in r.text
    assert "automatically paced to protect upstream engines" in r.text
    assert '/static/searxng.js' in r.text


def test_scans_reddit_renders_authenticated(logged_in):
    r = logged_in.get("/scans/reddit")
    assert r.status_code == 200
    assert "Reddit Discovery" in r.text
    assert 'id="run-btn"' in r.text
    assert 'id="probe-btn"' not in r.text
    assert 'id="promote-btn"' not in r.text
    assert '/static/reddit.js' in r.text


def test_results_renders_authenticated(logged_in):
    r = logged_in.get("/results")
    assert r.status_code == 200
    assert "Results" in r.text
    assert "Toggle Favorite" in r.text
    assert "Toggle Avoid" in r.text
    assert "Toggle Compromised" in r.text
    assert "Probe Selected" in r.text
    assert "Clear Selection" in r.text
    assert 'id="select-all-rows"' in r.text
    assert "<th>Probed</th>" in r.text
    assert "<th>Probe</th>" not in r.text
    assert 'colspan="12"' in r.text
    assert '/static/results.js' in r.text


def test_export_renders_authenticated(logged_in):
    r = logged_in.get("/export")
    assert r.status_code == 200
    assert "Export" in r.text
    assert "Export DB" in r.text
    assert '/static/export.js' in r.text


def test_extras_dorkbook_renders_authenticated(logged_in):
    r = logged_in.get("/extras/dorkbook")
    assert r.status_code == 200
    assert "Dorkbook" in r.text
    assert "dorkbook.js" in r.text


def test_no_inline_script_in_dorkbook(logged_in):
    import re as _re
    r = logged_in.get("/extras/dorkbook")
    assert r.status_code == 200
    assert not _re.search(r"<script[^>]*>[^<]+</script>", r.text)


def test_extras_keymaster_renders_authenticated(logged_in):
    r = logged_in.get("/extras/keymaster")
    assert r.status_code == 200
    assert "Keymaster" in r.text
    assert "desktop-only in this wave." in r.text
    assert 'src="/static/keymaster.js"' in r.text


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
        "port": 2600,
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
        "port": 2600,
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


def test_config_post_promotes_remote_loopback_and_returns_effective_bind(
    logged_in, config_path,
):
    token = _csrf(logged_in)
    payload = _valid_config_payload()
    payload.update({
        "remote_enabled": True,
        "tls_allow_insecure_remote": True,
        "allowed_cidrs": ["192.168.0.0/16"],
        "acknowledge_insecure_remote": True,
    })

    r = logged_in.post(
        "/config",
        json=payload,
        headers={"Origin": "http://testserver", "X-CSRF-Token": token},
    )

    assert r.status_code == 200
    assert r.json()["effective_bind_address"] == "0.0.0.0"
    assert json.loads(config_path.read_text())["bind_address"] == "0.0.0.0"


def test_config_post_requires_plaintext_transition_confirmation(logged_in):
    token = _csrf(logged_in)
    payload = _valid_config_payload()
    payload.update({
        "remote_enabled": True,
        "tls_allow_insecure_remote": True,
        "allowed_cidrs": ["192.168.0.0/16"],
    })

    response = logged_in.post(
        "/config",
        json=payload,
        headers={"Origin": "http://testserver", "X-CSRF-Token": token},
    )

    assert response.status_code == 409
    assert response.json()["confirmation_required"] == "insecure_remote"


def test_config_post_canonicalizes_and_returns_trusted_hosts(
    logged_in, config_path,
):
    token = _csrf(logged_in)
    payload = _valid_config_payload()
    payload["trusted_hosts"] = ["ScanBox.LAN.", "bücher.example"]

    response = logged_in.post(
        "/config",
        json=payload,
        headers={"Origin": "http://testserver", "X-CSRF-Token": token},
    )

    assert response.status_code == 200
    assert response.json()["trusted_hosts"] == [
        "scanbox.lan",
        "xn--bcher-kva.example",
    ]
    saved = json.loads(config_path.read_text())
    assert saved["trusted_hosts"] == response.json()["trusted_hosts"]


def test_config_javascript_promotes_remote_loopback(client):
    r = client.get("/static/config.js")
    assert r.status_code == 200
    assert "bindInput.value = '0.0.0.0'" in r.text
    assert "data.effective_bind_address" in r.text
    assert "window.confirm" in r.text
    assert "trusted_hosts" in r.text


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


def test_config_validation_error_is_sanitized(logged_in, monkeypatch, caplog):
    sentinel = "SECRET_PATH=/tmp/webui-private.json"

    def _raise(_cfg):
        raise WebUIConfigError(sentinel)

    monkeypatch.setattr("experimental.webui.app.validate", _raise)
    token = _csrf(logged_in)
    response = logged_in.post(
        "/config",
        json=_valid_config_payload(),
        headers={"Origin": "http://testserver", "X-CSRF-Token": token},
    )

    assert response.status_code == 400
    assert response.json() == {"error": "invalid Web UI configuration"}
    assert sentinel not in response.text
    assert sentinel not in caplog.text
    assert "exception_class=WebUIConfigError" in caplog.text


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
    r = logged_in.get("/scans/shodan")
    _assert_no_inline_script(r.text)
    assert '/static/scans.js' in r.text


def test_no_inline_script_in_searxng(logged_in):
    r = logged_in.get("/scans/searxng")
    _assert_no_inline_script(r.text)
    assert '/static/searxng.js' in r.text


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
    for path in [
        "/dashboard",
        "/scans/shodan",
        "/scans/searxng",
        "/scans/reddit",
        "/results",
        "/export",
        "/extras/dorkbook",
        "/extras/keymaster",
        "/config",
        "/account",
    ]:
        r = logged_in.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        assert not _INLINE_STYLE_RE.search(r.text), f"Inline style= attr found on {path}"
