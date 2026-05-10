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
