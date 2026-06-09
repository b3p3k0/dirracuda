"""C8: Remote mode startup validation and allowlist enforcement tests."""

import pytest
from fastapi.testclient import TestClient

from experimental.webui.app import create_app
from experimental.webui.config import TLSConfig, WebUIConfig, WebUIConfigError, validate
import experimental.webui.server as server
from experimental.webui.server import _check_remote_tls


# --- Startup validation (config-level, via validate()) ---

def test_localhost_startup_allowed():
    validate(WebUIConfig())


def test_nonloopback_remote_disabled_rejected():
    cfg = WebUIConfig(
        bind_address="0.0.0.0",
        remote_enabled=False,
        allowed_cidrs=["0.0.0.0/0"],
        tls=TLSConfig(enabled=False, allow_insecure_remote=True),
    )
    with pytest.raises(WebUIConfigError, match="remote_enabled"):
        validate(cfg)


def test_nonloopback_empty_cidrs_rejected():
    cfg = WebUIConfig(
        bind_address="0.0.0.0",
        remote_enabled=True,
        allowed_cidrs=[],
        tls=TLSConfig(enabled=False, allow_insecure_remote=True),
    )
    with pytest.raises(WebUIConfigError, match="allowed_cidrs"):
        validate(cfg)


def test_nonloopback_tls_off_no_override_rejected():
    cfg = WebUIConfig(
        bind_address="0.0.0.0",
        remote_enabled=True,
        allowed_cidrs=["10.0.0.0/8"],
        tls=TLSConfig(enabled=False, allow_insecure_remote=False),
    )
    with pytest.raises(WebUIConfigError, match="allow_insecure_remote"):
        validate(cfg)


def test_explicit_insecure_override_allowed():
    cfg = WebUIConfig(
        bind_address="0.0.0.0",
        remote_enabled=True,
        allowed_cidrs=["10.0.0.0/8"],
        tls=TLSConfig(enabled=False, allow_insecure_remote=True),
    )
    validate(cfg)  # must not raise


def test_server_promotes_remote_loopback_without_host_override(monkeypatch):
    captured = {}
    cfg = WebUIConfig(
        bind_address="127.0.0.1",
        remote_enabled=True,
        allowed_cidrs=["10.0.0.0/8"],
        tls=TLSConfig(enabled=False, allow_insecure_remote=True),
    )
    monkeypatch.setattr(server, "create_app", lambda **kwargs: kwargs["cfg"])
    monkeypatch.setattr(server, "credential_exists", lambda: True)
    monkeypatch.setattr(
        server.uvicorn,
        "run",
        lambda app, **kwargs: captured.update(app=app, kwargs=kwargs),
    )

    server.run(cfg=cfg)

    assert captured["app"].bind_address == "0.0.0.0"
    assert captured["kwargs"]["host"] == "0.0.0.0"
    assert captured["kwargs"]["h11_max_incomplete_event_size"] == 16 * 1024
    assert captured["kwargs"]["limit_concurrency"] == 128
    assert captured["kwargs"]["backlog"] == 128
    assert captured["kwargs"]["server_header"] is False
    assert captured["kwargs"]["proxy_headers"] is False


def test_explicit_server_host_override_remains_authoritative(monkeypatch):
    captured = {}
    cfg = WebUIConfig(
        bind_address="127.0.0.1",
        remote_enabled=True,
        allowed_cidrs=["10.0.0.0/8"],
        tls=TLSConfig(enabled=False, allow_insecure_remote=True),
    )
    monkeypatch.setattr(server, "create_app", lambda **kwargs: kwargs["cfg"])
    monkeypatch.setattr(server, "credential_exists", lambda: True)
    monkeypatch.setattr(
        server.uvicorn,
        "run",
        lambda app, **kwargs: captured.update(app=app, kwargs=kwargs),
    )

    server.run(host="127.0.0.1", cfg=cfg)

    assert captured["app"].bind_address == "127.0.0.1"
    assert captured["kwargs"]["host"] == "127.0.0.1"


def test_server_startup_requires_credential(monkeypatch):
    cfg = WebUIConfig(tls=TLSConfig(enabled=False))
    monkeypatch.setattr(server, "credential_exists", lambda: False)
    monkeypatch.setattr(
        server.uvicorn,
        "run",
        lambda *_a, **_k: pytest.fail("uvicorn must not start without credentials"),
    )

    with pytest.raises(SystemExit) as exc:
        server.run(cfg=cfg)

    assert exc.value.code == 1


# --- Remote TLS cert/key check (server-level, via _check_remote_tls()) ---

def test_remote_tls_no_cert_rejected():
    cfg = WebUIConfig(
        bind_address="0.0.0.0",
        remote_enabled=True,
        allowed_cidrs=["10.0.0.0/8"],
        tls=TLSConfig(enabled=True, cert_file="", key_file=""),
    )
    err = _check_remote_tls(cfg, "0.0.0.0")
    assert err is not None
    assert "cert_file" in err or "key_file" in err


def test_remote_tls_missing_cert_file_rejected(tmp_path):
    key = tmp_path / "server.key"
    key.write_text("key")
    cfg = WebUIConfig(
        bind_address="0.0.0.0",
        remote_enabled=True,
        allowed_cidrs=["10.0.0.0/8"],
        tls=TLSConfig(enabled=True, cert_file="/nonexistent/cert.pem", key_file=str(key)),
    )
    err = _check_remote_tls(cfg, "0.0.0.0")
    assert err is not None
    assert "cert" in err.lower()


def test_loopback_tls_no_cert_ok():
    cfg = WebUIConfig(tls=TLSConfig(enabled=True, cert_file="", key_file=""))
    assert _check_remote_tls(cfg, "127.0.0.1") is None


def test_remote_tls_disabled_with_insecure_override_ok():
    cfg = WebUIConfig(
        bind_address="0.0.0.0",
        remote_enabled=True,
        allowed_cidrs=["10.0.0.0/8"],
        tls=TLSConfig(enabled=False, allow_insecure_remote=True),
    )
    assert _check_remote_tls(cfg, "0.0.0.0") is None


# --- Allowlist middleware (app-level) ---

def _with_client_ip(app, client_ip: str):
    """Wrap ASGI app so requests appear to come from client_ip."""
    async def _wrapper(scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope, client=(client_ip, 12345))
        await app(scope, receive, send)
    return _wrapper


def _remote_app(allowed_cidrs):
    """Remote-mode app with insecure override (no certs needed in tests)."""
    cfg = WebUIConfig(
        bind_address="0.0.0.0",
        remote_enabled=True,
        allowed_cidrs=allowed_cidrs,
        tls=TLSConfig(enabled=False, allow_insecure_remote=True),
    )
    return create_app(cfg=cfg)


def test_allowlist_blocks_disallowed_client():
    app = _remote_app(allowed_cidrs=["10.0.0.0/8"])
    client = TestClient(_with_client_ip(app, "192.168.1.5"), follow_redirects=False)
    assert client.get("/health", headers={"Host": "127.0.0.1"}).status_code == 403


def test_allowlist_permits_allowed_client():
    app = _remote_app(allowed_cidrs=["10.0.0.0/8"])
    client = TestClient(_with_client_ip(app, "10.0.1.5"), follow_redirects=False)
    assert client.get("/health", headers={"Host": "127.0.0.1"}).status_code == 200


def test_localhost_mode_skips_allowlist():
    cfg = WebUIConfig()  # remote_enabled=False, default allowlist
    app = create_app(cfg=cfg)
    # "203.0.113.1" is not in 127.0.0.1/32 or ::1/128 — but localhost mode skips the check
    client = TestClient(_with_client_ip(app, "203.0.113.1"), follow_redirects=False)
    assert client.get("/health", headers={"Host": "127.0.0.1"}).status_code == 200
