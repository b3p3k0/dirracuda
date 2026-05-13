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


# --- Rate limiter / lockout integration ---

from experimental.webui.config import AuthConfig
from experimental.webui.rate_limiter import NullRateLimiter


def _lockout_cfg():
    return WebUIConfig(tls=TLSConfig(enabled=False), auth=AuthConfig(lockout_threshold=3))


@pytest.fixture
def rl_db(tmp_path):
    return tmp_path / "rl.db"


@pytest.fixture
def lockout_client(creds, rl_db):
    app = create_app(cfg=_lockout_cfg(), creds_path=creds, rl_db_path=rl_db)
    return TestClient(app, follow_redirects=False)


def test_lockout_after_repeated_failures(lockout_client):
    for _ in range(3):
        r = lockout_client.post("/login", json={"username": _USERNAME, "password": "wrong"})
        assert r.status_code == 401
    # 4th attempt — still 401 (locked)
    r = lockout_client.post("/login", json={"username": _USERNAME, "password": "wrong"})
    assert r.status_code == 401


def test_locked_response_body_generic(lockout_client):
    wrong_body = lockout_client.post(
        "/login", json={"username": _USERNAME, "password": "wrong"}
    ).json()
    for _ in range(2):
        lockout_client.post("/login", json={"username": _USERNAME, "password": "wrong"})
    locked_body = lockout_client.post(
        "/login", json={"username": _USERNAME, "password": "wrong"}
    ).json()
    assert locked_body == wrong_body


def test_locked_no_retry_after_header(lockout_client):
    for _ in range(3):
        lockout_client.post("/login", json={"username": _USERNAME, "password": "wrong"})
    r = lockout_client.post("/login", json={"username": _USERNAME, "password": "wrong"})
    assert r.status_code == 401
    assert "retry-after" not in {k.lower() for k in r.headers}


def test_different_ip_not_locked(creds, rl_db):
    app = create_app(cfg=_lockout_cfg(), creds_path=creds, rl_db_path=rl_db)
    c1 = TestClient(app, follow_redirects=False)
    # Trigger lockout from default TestClient IP
    for _ in range(3):
        c1.post("/login", json={"username": _USERNAME, "password": "wrong"})

    # A second client with different headers — override client IP via custom transport
    # TestClient uses 'testclient' as host; simulate a second source via the rate limiter
    # directly: lockout is per (account, ip), so a new client at a different IP is unaffected.
    from experimental.webui.rate_limiter import RateLimiter
    from experimental.webui.config import AuthConfig
    rl = RateLimiter(rl_db, AuthConfig(lockout_threshold=3))
    locked_orig, _ = rl.check_locked(_USERNAME, "testclient")
    locked_other, _ = rl.check_locked(_USERNAME, "10.0.0.99")
    assert locked_orig
    assert not locked_other


def test_success_clears_lockout(creds, rl_db, monkeypatch):
    import time
    app = create_app(cfg=_lockout_cfg(), creds_path=creds, rl_db_path=rl_db)
    c = TestClient(app, follow_redirects=False)
    for _ in range(3):
        c.post("/login", json={"username": _USERNAME, "password": "wrong"})
    # Advance time past lockout
    future = time.time() + 400
    monkeypatch.setattr("experimental.webui.rate_limiter.time.time", lambda: future)
    r = c.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200


def test_localhost_mode_lockout_applies(creds, rl_db):
    cfg = WebUIConfig(
        tls=TLSConfig(enabled=False),
        remote_enabled=False,
        auth=AuthConfig(lockout_threshold=3),
    )
    app = create_app(cfg=cfg, creds_path=creds, rl_db_path=rl_db)
    c = TestClient(app, follow_redirects=False)
    for _ in range(3):
        c.post("/login", json={"username": _USERNAME, "password": "wrong"})
    r = c.post("/login", json={"username": _USERNAME, "password": "wrong"})
    assert r.status_code == 401


def test_health_route_includes_rate_limiter_ok(creds, rl_db):
    app = create_app(cfg=_lockout_cfg(), creds_path=creds, rl_db_path=rl_db)
    c = TestClient(app, follow_redirects=False)
    data = c.get("/health").json()
    assert data["status"] == "ok"
    assert data["rate_limiter"] == "ok"


def test_health_route_degraded_null_limiter(creds):
    app = create_app(cfg=_lockout_cfg(), creds_path=creds)
    app.state.rate_limiter = NullRateLimiter()
    c = TestClient(app, follow_redirects=False)
    data = c.get("/health").json()
    assert data["status"] == "ok"
    assert data["rate_limiter"] == "error"


# --- Runtime DB error: fail-closed (remote) and degrade (localhost) ---

from experimental.webui.rate_limiter import RateLimiterRuntimeError


class _CheckLockedBrokenRL:
    """Rate limiter stub that raises RateLimiterRuntimeError on check_locked."""

    def check_locked(self, account, ip):
        raise RateLimiterRuntimeError("simulated disk error")

    def record_failure(self, account, ip):
        pass

    def record_success(self, account):
        pass

    def health_check(self):
        return "error"


class _RecordFailureBrokenRL:
    """Rate limiter stub: check_locked OK, record_failure raises."""

    def check_locked(self, account, ip):
        return False, 0

    def record_failure(self, account, ip):
        raise RateLimiterRuntimeError("simulated disk error")

    def record_success(self, account):
        pass

    def health_check(self):
        return "error"


def test_remote_rl_check_locked_runtime_error_returns_503(creds, rl_db):
    # Create app with remote_enabled=False so the middleware closure keeps that state
    # and TestClient's non-IP host passes the allowlist check. Then *reassign*
    # app.state.cfg (not mutate the original object) so the login handler — which
    # reads request.app.state.cfg — sees remote_enabled=True and fails closed.
    cfg = WebUIConfig(tls=TLSConfig(enabled=False))
    app = create_app(cfg=cfg, creds_path=creds, rl_db_path=rl_db)
    app.state.cfg = WebUIConfig(tls=TLSConfig(enabled=False), remote_enabled=True)
    app.state.rate_limiter = _CheckLockedBrokenRL()
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 503


def test_localhost_rl_check_locked_runtime_error_degrades(creds, rl_db):
    cfg = WebUIConfig(tls=TLSConfig(enabled=False), remote_enabled=False)
    app = create_app(cfg=cfg, creds_path=creds, rl_db_path=rl_db)
    app.state.rate_limiter = _CheckLockedBrokenRL()
    c = TestClient(app, follow_redirects=False)
    # Correct password: degraded mode skips lockout check and allows login.
    r = c.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200


def test_remote_rl_record_failure_runtime_error_returns_503(creds, rl_db):
    cfg = WebUIConfig(tls=TLSConfig(enabled=False))
    app = create_app(cfg=cfg, creds_path=creds, rl_db_path=rl_db)
    app.state.cfg = WebUIConfig(tls=TLSConfig(enabled=False), remote_enabled=True)
    app.state.rate_limiter = _RecordFailureBrokenRL()
    c = TestClient(app, follow_redirects=False)
    # Wrong password triggers record_failure; remote mode must fail closed.
    r = c.post("/login", json={"username": _USERNAME, "password": "wrong"})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# O2 — /account page and /api/auth/change-password endpoint
# ---------------------------------------------------------------------------

def _csrf_token(lc):
    """Extract CSRF token from the account page of an already-logged-in client."""
    r = lc.get("/account")
    assert r.status_code == 200
    m = re.search(r'name="csrf-token" content="([^"]+)"', r.text)
    assert m, "csrf-token meta tag not found"
    return m.group(1)


def test_account_page_requires_auth(client):
    r = client.get("/account")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_account_page_accessible(logged_in_client):
    r = logged_in_client.get("/account")
    assert r.status_code == 200


def test_change_password_requires_auth(client):
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": _PASSWORD, "new_password": "newpassword123456"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_change_password_missing_csrf(logged_in_client):
    r = logged_in_client.post(
        "/api/auth/change-password",
        json={"current_password": _PASSWORD, "new_password": "newpassword123456"},
    )
    assert r.status_code == 403


def test_change_password_wrong_origin(logged_in_client):
    csrf = _csrf_token(logged_in_client)
    r = logged_in_client.post(
        "/api/auth/change-password",
        json={"current_password": _PASSWORD, "new_password": "newpassword123456"},
        headers={"X-CSRF-Token": csrf, "origin": "http://attacker.com"},
    )
    assert r.status_code == 403


def test_change_password_wrong_current(logged_in_client):
    csrf = _csrf_token(logged_in_client)
    r = logged_in_client.post(
        "/api/auth/change-password",
        json={"current_password": "wrong-password", "new_password": "newpassword123456"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 401
    assert "error" in r.json()


def test_change_password_too_short(logged_in_client):
    csrf = _csrf_token(logged_in_client)
    r = logged_in_client.post(
        "/api/auth/change-password",
        json={"current_password": _PASSWORD, "new_password": "tooshort"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert "15" in r.json().get("error", "")


def test_change_password_common_password(logged_in_client, monkeypatch):
    import experimental.webui.auth as auth_module
    csrf = _csrf_token(logged_in_client)
    monkeypatch.setattr(auth_module, "_BLOCKLIST", frozenset({"blockedtestpassword123"}))
    r = logged_in_client.post(
        "/api/auth/change-password",
        json={"current_password": _PASSWORD, "new_password": "blockedtestpassword123"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert "common" in r.json().get("error", "").lower()


def test_change_password_blocklist_unavailable(logged_in_client, monkeypatch):
    import experimental.webui.auth as auth_module
    csrf = _csrf_token(logged_in_client)
    monkeypatch.setattr(auth_module, "_BLOCKLIST", None)
    r = logged_in_client.post(
        "/api/auth/change-password",
        json={"current_password": _PASSWORD, "new_password": "newpassword123456"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 503


def test_change_password_success(logged_in_client, creds):
    from experimental.webui.auth import verify_password
    csrf = _csrf_token(logged_in_client)
    new_pw = "newpassword-xk7-battery"
    r = logged_in_client.post(
        "/api/auth/change-password",
        json={"current_password": _PASSWORD, "new_password": new_pw},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True
    assert verify_password(_USERNAME, new_pw, creds) is True
    assert verify_password(_USERNAME, _PASSWORD, creds) is False


# ---------------------------------------------------------------------------
# O3 — HSTS and security headers
# ---------------------------------------------------------------------------

import re as _re

_INLINE_SCRIPT_RE = _re.compile(r'<script(?![^>]*\bsrc=)[^>]*>', _re.IGNORECASE)


def test_hsts_present_on_https_response(creds):
    cfg = WebUIConfig(tls=TLSConfig(enabled=True))
    app = create_app(cfg=cfg, creds_path=creds)
    c = TestClient(app, follow_redirects=False, base_url="https://testserver")
    r = c.get("/login")
    assert "strict-transport-security" in {k.lower() for k in r.headers}


def test_hsts_absent_on_http_response(client):
    r = client.get("/login")
    assert "strict-transport-security" not in {k.lower() for k in r.headers}


def test_security_headers_on_login_page(client):
    r = client.get("/login")
    h = {k.lower(): v for k, v in r.headers.items()}
    assert "x-frame-options" in h
    assert "x-content-type-options" in h
    assert "content-security-policy" in h
    assert "unsafe-inline" not in h["content-security-policy"]
    assert not _INLINE_SCRIPT_RE.search(r.text), (
        "Unexpected inline <script> on /login: " + str(_INLINE_SCRIPT_RE.findall(r.text))
    )
