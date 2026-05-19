"""C1 scaffold: import and app-factory tests."""


def test_webui_package_importable():
    import experimental.webui  # must not raise even without requirements-web.txt installed


def test_create_app_returns_fastapi_instance():
    from fastapi import FastAPI
    from experimental.webui.app import create_app

    assert isinstance(create_app(), FastAPI)


def test_health_route_registered():
    from fastapi.routing import APIRoute
    from experimental.webui.app import create_app

    app = create_app()
    paths = [r.path for r in app.routes if isinstance(r, APIRoute)]
    assert "/health" in paths


def test_health_route_payload():
    from experimental.webui.app import health

    assert health() == {"status": "ok"}


def test_debug_endpoints_not_registered():
    from experimental.webui.app import create_app

    app = create_app()
    paths = {getattr(route, "path", None) for route in app.routes}
    for disabled in ("/docs", "/redoc", "/openapi.json"):
        assert disabled not in paths, f"{disabled} must not be registered"


# --- Rate limiter startup behavior ---

import os
import pytest
from experimental.webui.config import AuthConfig, TLSConfig, WebUIConfig
from experimental.webui.rate_limiter import NullRateLimiter, RateLimiterInitError


def _locked_rl_path(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    return locked / "rl.db"


def test_create_app_fails_startup_remote_db_error(tmp_path):
    from experimental.webui.app import create_app
    cfg = WebUIConfig(
        bind_address="192.168.1.1",
        remote_enabled=True,
        allowed_cidrs=["192.168.1.0/24"],
        tls=TLSConfig(enabled=True),
    )
    rl_path = _locked_rl_path(tmp_path)
    try:
        with pytest.raises(RateLimiterInitError):
            create_app(cfg=cfg, rl_db_path=rl_path)
    finally:
        os.chmod(tmp_path / "locked", 0o700)


def test_create_app_degrades_localhost_db_error(tmp_path):
    from experimental.webui.app import create_app
    cfg = WebUIConfig(tls=TLSConfig(enabled=False), remote_enabled=False)
    rl_path = _locked_rl_path(tmp_path)
    try:
        app = create_app(cfg=cfg, rl_db_path=rl_path)
        assert isinstance(app.state.rate_limiter, NullRateLimiter)
    finally:
        os.chmod(tmp_path / "locked", 0o700)
