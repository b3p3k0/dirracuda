"""Route integration tests for Dorkbook endpoints (C33)."""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import experimental.dorkbook.store as dork_store
from experimental.webui.app import create_app
from experimental.webui.auth import set_password
from experimental.webui.config import TLSConfig, WebUIConfig
from experimental.dorkbook.models import ROW_KIND_BUILTIN, ROW_KIND_CUSTOM

_USERNAME = "dorkbook_tester"
_PASSWORD = "dorkbook-battery-staple"
_SENTINEL = "SECRET_PATH=/tmp/dorkbook-private.db"

_SMB_ROW = {
    "entry_id": 1,
    "protocol": "SMB",
    "nickname": "SMB anon",
    "query": "smb authentication: disabled",
    "notes": "",
    "row_kind": ROW_KIND_BUILTIN,
    "builtin_key": "builtin_smb_auth_disabled",
    "created_at": "2025-01-01T00:00:00",
    "updated_at": "2025-01-01T00:00:00",
}

_CUSTOM_ROW = {
    "entry_id": 42,
    "protocol": "HTTP",
    "nickname": "HTTP indexes",
    "query": 'http.title:"Index of /"',
    "notes": "open directory listing",
    "row_kind": ROW_KIND_CUSTOM,
    "builtin_key": None,
    "created_at": "2025-01-01T00:00:00",
    "updated_at": "2025-01-01T00:00:00",
}


def _raise_sentinel(exception_type=RuntimeError):
    def _raise(*_args, **_kwargs):
        raise exception_type(_SENTINEL)

    return _raise


def _assert_sanitized(response, caplog, status_code, payload):
    assert response.status_code == status_code
    assert response.json() == payload
    assert _SENTINEL not in response.text
    assert _SENTINEL not in caplog.text


@contextlib.contextmanager
def _fake_conn(get_entry_return=None):
    """Context manager yielding a mock connection, with get_entry pre-wired."""
    conn = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    yield conn


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
def app(creds, cfg_no_tls, main_config_path):
    return create_app(cfg=cfg_no_tls, creds_path=creds, main_config_path=main_config_path)


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def logged_in(client):
    r = client.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    return client


def _csrf(client):
    dash = client.get("/dashboard")
    assert dash.status_code == 200
    m = re.search(r'name="csrf-token" content="([^"]+)"', dash.text)
    assert m, "csrf-token meta tag not found"
    return m.group(1)


# ---------------------------------------------------------------------------
# GET /api/dorkbook/entries
# ---------------------------------------------------------------------------

def test_list_entries_redirects_unauthenticated(client):
    r = client.get("/api/dorkbook/entries")
    assert r.status_code == 303


def test_list_entries_returns_entries(logged_in, monkeypatch):
    monkeypatch.setattr(dork_store, "init_db", lambda *_a, **_kw: None)
    mock_conn = MagicMock()
    monkeypatch.setattr(
        dork_store,
        "open_connection",
        lambda *_a, **_kw: mock_conn,
    )
    monkeypatch.setattr(dork_store, "list_entries", lambda conn, protocol, search_text="", **kwargs: [_SMB_ROW])

    r = logged_in.get("/api/dorkbook/entries?protocol=SMB")
    assert r.status_code == 200
    data = r.json()
    assert "entries" in data
    assert data["entries"][0]["protocol"] == "SMB"


def test_list_entries_rejects_invalid_protocol(logged_in):
    r = logged_in.get("/api/dorkbook/entries?protocol=INVALID")
    assert r.status_code == 422


def test_list_entries_exception_is_sanitized(logged_in, monkeypatch, caplog):
    monkeypatch.setattr(dork_store, "init_db", _raise_sentinel())

    response = logged_in.get("/api/dorkbook/entries?protocol=SMB")

    _assert_sanitized(
        response,
        caplog,
        500,
        {"error": "dorkbook unavailable"},
    )
    assert "exception_class=RuntimeError" in caplog.text


# ---------------------------------------------------------------------------
# POST /api/dorkbook/entries
# ---------------------------------------------------------------------------

def test_create_entry_redirects_unauthenticated(client):
    r = client.post("/api/dorkbook/entries", json={"protocol": "SMB", "query": "smb authentication: disabled"})
    assert r.status_code == 303


def test_create_entry_requires_csrf(logged_in):
    r = logged_in.post(
        "/api/dorkbook/entries",
        json={"protocol": "SMB", "query": "smb authentication: disabled"},
    )
    assert r.status_code == 403


def test_create_entry_requires_same_origin(logged_in):
    tok = _csrf(logged_in)
    r = logged_in.post(
        "/api/dorkbook/entries",
        json={"protocol": "SMB", "query": "smb authentication: disabled"},
        headers={"X-CSRF-Token": tok, "Origin": "http://attacker.example.com"},
    )
    assert r.status_code == 403


def test_create_entry_succeeds(logged_in, monkeypatch):
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", lambda *_a, **_kw: None)
    mock_conn = MagicMock()
    monkeypatch.setattr(
        dork_store,
        "open_connection",
        lambda *_a, **_kw: mock_conn,
    )
    monkeypatch.setattr(
        dork_store, "create_entry",
        lambda conn, protocol, nickname, query, notes, **kwargs: 99,
    )

    r = logged_in.post(
        "/api/dorkbook/entries",
        json={"protocol": "HTTP", "query": 'http.title:"Index of /"', "nickname": "test"},
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["entry_id"] == 99
    assert data["ok"] is True


def test_create_entry_rejects_duplicate(logged_in, monkeypatch, caplog):
    from experimental.dorkbook.models import DuplicateEntryError
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", lambda *_a, **_kw: None)
    mock_conn = MagicMock()
    monkeypatch.setattr(
        dork_store,
        "open_connection",
        lambda *_a, **_kw: mock_conn,
    )
    monkeypatch.setattr(
        dork_store, "create_entry",
        _raise_sentinel(DuplicateEntryError),
    )

    response = logged_in.post(
        "/api/dorkbook/entries",
        json={"protocol": "SMB", "query": "smb authentication: disabled"},
        headers={"X-CSRF-Token": tok},
    )
    _assert_sanitized(
        response,
        caplog,
        409,
        {"error": "query already exists"},
    )


def test_create_entry_exception_is_sanitized(logged_in, monkeypatch, caplog):
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", _raise_sentinel())

    response = logged_in.post(
        "/api/dorkbook/entries",
        json={"protocol": "SMB", "query": "smb authentication: disabled"},
        headers={"X-CSRF-Token": tok},
    )

    _assert_sanitized(
        response,
        caplog,
        500,
        {"error": "dorkbook operation failed"},
    )
    assert "exception_class=RuntimeError" in caplog.text


# ---------------------------------------------------------------------------
# DELETE /api/dorkbook/entries/{entry_id}
# ---------------------------------------------------------------------------

def test_delete_entry_requires_csrf(logged_in):
    r = logged_in.delete("/api/dorkbook/entries/42")
    assert r.status_code == 403


def test_delete_entry_rejects_builtin(logged_in, monkeypatch):
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", lambda *_a, **_kw: None)
    mock_conn = MagicMock()
    monkeypatch.setattr(
        dork_store,
        "open_connection",
        lambda *_a, **_kw: mock_conn,
    )
    monkeypatch.setattr(dork_store, "get_entry", lambda conn, entry_id: _SMB_ROW)

    r = logged_in.delete(
        "/api/dorkbook/entries/1",
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 403


def test_delete_entry_read_only_exception_is_sanitized(
    logged_in,
    monkeypatch,
    caplog,
):
    from experimental.dorkbook.models import ReadOnlyEntryError

    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", lambda *_a, **_kw: None)
    mock_conn = MagicMock()
    monkeypatch.setattr(
        dork_store,
        "open_connection",
        lambda *_a, **_kw: mock_conn,
    )
    monkeypatch.setattr(dork_store, "get_entry", lambda *_a, **_kw: _CUSTOM_ROW)
    monkeypatch.setattr(
        dork_store,
        "delete_entry",
        _raise_sentinel(ReadOnlyEntryError),
    )

    response = logged_in.delete(
        "/api/dorkbook/entries/42",
        headers={"X-CSRF-Token": tok},
    )

    _assert_sanitized(
        response,
        caplog,
        403,
        {"error": "built-in dorks are read-only"},
    )


def test_delete_entry_exception_is_sanitized(logged_in, monkeypatch, caplog):
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", _raise_sentinel())

    response = logged_in.delete(
        "/api/dorkbook/entries/42",
        headers={"X-CSRF-Token": tok},
    )

    _assert_sanitized(
        response,
        caplog,
        500,
        {"error": "dorkbook operation failed"},
    )
    assert "exception_class=RuntimeError" in caplog.text


def test_delete_entry_succeeds(logged_in, monkeypatch):
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", lambda *_a, **_kw: None)
    mock_conn = MagicMock()
    monkeypatch.setattr(
        dork_store,
        "open_connection",
        lambda *_a, **_kw: mock_conn,
    )
    monkeypatch.setattr(dork_store, "get_entry", lambda conn, entry_id: _CUSTOM_ROW)
    deleted = []
    monkeypatch.setattr(
        dork_store, "delete_entry",
        lambda conn, entry_id: deleted.append(entry_id) or True,
    )

    r = logged_in.delete(
        "/api/dorkbook/entries/42",
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert 42 in deleted


# ---------------------------------------------------------------------------
# POST /api/dorkbook/prefill
# ---------------------------------------------------------------------------

def test_prefill_requires_csrf(logged_in):
    r = logged_in.post("/api/dorkbook/prefill", json={"entry_id": 1})
    assert r.status_code == 403


def test_prefill_requires_same_origin(logged_in):
    tok = _csrf(logged_in)
    r = logged_in.post(
        "/api/dorkbook/prefill",
        json={"entry_id": 1},
        headers={"X-CSRF-Token": tok, "Origin": "http://attacker.example.com"},
    )
    assert r.status_code == 403


def test_prefill_returns_404_when_entry_missing(logged_in, monkeypatch):
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", lambda *_a, **_kw: None)
    mock_conn = MagicMock()
    monkeypatch.setattr(
        dork_store,
        "open_connection",
        lambda *_a, **_kw: mock_conn,
    )
    monkeypatch.setattr(dork_store, "get_entry", lambda conn, entry_id: None)

    r = logged_in.post(
        "/api/dorkbook/prefill",
        json={"entry_id": 999},
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 404


def test_prefill_store_exception_is_sanitized(logged_in, monkeypatch, caplog):
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", _raise_sentinel())

    response = logged_in.post(
        "/api/dorkbook/prefill",
        json={"entry_id": 42},
        headers={"X-CSRF-Token": tok},
    )

    _assert_sanitized(
        response,
        caplog,
        500,
        {"error": "dorkbook unavailable"},
    )
    assert "exception_class=RuntimeError" in caplog.text


def test_prefill_writes_to_config(logged_in, monkeypatch, main_config_path):
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", lambda *_a, **_kw: None)
    mock_conn = MagicMock()
    monkeypatch.setattr(
        dork_store,
        "open_connection",
        lambda *_a, **_kw: mock_conn,
    )
    monkeypatch.setattr(dork_store, "get_entry", lambda conn, entry_id: _CUSTOM_ROW)

    r = logged_in.post(
        "/api/dorkbook/prefill",
        json={"entry_id": 42},
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["protocol"] == "HTTP"

    written = json.loads(main_config_path.read_text(encoding="utf-8"))
    http_query = written.get("http", {}).get("shodan", {}).get("query_components", {}).get("base_query")
    assert http_query == 'http.title:"Index of /"'


def test_prefill_config_exception_is_sanitized(logged_in, monkeypatch, caplog):
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", lambda *_a, **_kw: None)
    mock_conn = MagicMock()
    monkeypatch.setattr(
        dork_store,
        "open_connection",
        lambda *_a, **_kw: mock_conn,
    )
    monkeypatch.setattr(dork_store, "get_entry", lambda *_a, **_kw: _CUSTOM_ROW)
    monkeypatch.setattr(
        "experimental.webui.dorkbook_routes.defaults.apply_default",
        _raise_sentinel(),
    )

    response = logged_in.post(
        "/api/dorkbook/prefill",
        json={"entry_id": 42},
        headers={"X-CSRF-Token": tok},
    )

    _assert_sanitized(
        response,
        caplog,
        500,
        {"error": "failed to update discovery configuration"},
    )
    assert "exception_class=RuntimeError" in caplog.text


def test_prefill_returns_404_when_config_missing(logged_in, app, monkeypatch, tmp_path):
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", lambda *_a, **_kw: None)
    mock_conn = MagicMock()
    monkeypatch.setattr(
        dork_store,
        "open_connection",
        lambda *_a, **_kw: mock_conn,
    )
    monkeypatch.setattr(dork_store, "get_entry", lambda conn, entry_id: _CUSTOM_ROW)

    original = app.state.main_config_path
    app.state.main_config_path = tmp_path / "does_not_exist.json"
    try:
        r = logged_in.post(
            "/api/dorkbook/prefill",
            json={"entry_id": 42},
            headers={"X-CSRF-Token": tok},
        )
        assert r.status_code == 404
    finally:
        app.state.main_config_path = original


@pytest.fixture
def real_dorkbook(app, tmp_path):
    """Keep route integration tests away from the user's sidecar and config."""
    app.state.dorkbook_db_path = tmp_path / "dorkbook.db"
    return app.state.dorkbook_db_path


def test_unified_library_groups_and_filters(logged_in, real_dorkbook):
    tok = _csrf(logged_in)
    query = 'intitle:"Index of /" "test epub"'
    created = logged_in.post("/api/dorkbook/entries", json={
        "provider": "self_hosted", "query": query, "nickname": "Test Books", "topic": "Test topic",
    }, headers={"X-CSRF-Token": tok})
    assert created.status_code == 201
    rows = logged_in.get("/api/dorkbook/entries").json()["entries"]
    assert {row["provider"] for row in rows} == {"shodan", "self_hosted"}
    filtered = logged_in.get("/api/dorkbook/entries", params={
        "provider": "self_hosted", "topic": "Test topic", "search": "Test Books",
    }).json()["entries"]
    assert len(filtered) == 1 and filtered[0]["query"] == query
    assert filtered[0]["protocol"] is None
    duplicate = logged_in.post("/api/dorkbook/entries", json={
        "provider": "self_hosted", "query": query,
    }, headers={"X-CSRF-Token": tok})
    assert duplicate.status_code == 409


@pytest.mark.parametrize("payload", [
    {"provider": "self_hosted", "protocol": "HTTP", "query": "query"},
    {"provider": "shodan", "query": "query"},
    {"provider": "searxng", "query": "query"},
    {"provider": "self_hosted", "query": " "},
    {"provider": "self_hosted", "query": "x" * 501},
    {"provider": "self_hosted", "query": "query", "topic": " "},
])
def test_invalid_destinations_rejected(logged_in, real_dorkbook, payload):
    assert logged_in.post("/api/dorkbook/entries", json=payload,
                          headers={"X-CSRF-Token": _csrf(logged_in)}).status_code == 422


@pytest.mark.parametrize("destination,provider,protocol", [
    ("shodan:SMB", "shodan", "SMB"), ("shodan:FTP", "shodan", "FTP"),
    ("shodan:HTTP", "shodan", "HTTP"), ("self_hosted", "self_hosted", None),
])
def test_apply_persists_only_its_destination(logged_in, real_dorkbook, main_config_path,
                                           destination, provider, protocol):
    headers = {"X-CSRF-Token": _csrf(logged_in)}
    before = logged_in.get("/api/dorkbook/defaults").json()["defaults"]
    query = "unique test query for " + destination
    created = logged_in.post("/api/dorkbook/entries", json={
        "provider": provider, "protocol": protocol, "query": query,
    }, headers=headers)
    entry_id = created.json()["entry_id"]
    response = logged_in.post("/api/dorkbook/apply", json={"entry_id": entry_id}, headers=headers)
    assert response.status_code == 200
    assert response.json()["destination"] == destination
    after = logged_in.get("/api/dorkbook/defaults").json()["defaults"]
    assert after == {**before, destination: query}
    # Removing the library row must not remove its saved default.
    assert logged_in.delete(f"/api/dorkbook/entries/{entry_id}", headers=headers).status_code == 200
    assert logged_in.get("/api/dorkbook/defaults").json()["defaults"] == after
    from experimental.dorkbook.defaults import read_defaults
    assert read_defaults(main_config_path) == after


def test_defaults_require_session_and_disable_cache(client, logged_in):
    response = logged_in.get("/api/dorkbook/defaults")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    client.cookies.clear()
    assert client.get("/api/dorkbook/defaults").status_code == 303
    assert client.post("/api/dorkbook/apply", json={"entry_id": 1}).status_code == 303


@pytest.mark.parametrize("headers", [{}, {"Origin": "http://attacker.example"}])
def test_apply_requires_csrf_and_origin(logged_in, headers):
    if headers:
        headers = {**headers, "X-CSRF-Token": _csrf(logged_in)}
    assert logged_in.post("/api/dorkbook/apply", json={"entry_id": 1}, headers=headers).status_code == 403


def test_defaults_failure_is_sanitized(logged_in, monkeypatch, caplog):
    monkeypatch.setattr("experimental.webui.dorkbook_routes.defaults.read_defaults", _raise_sentinel())
    _assert_sanitized(logged_in.get("/api/dorkbook/defaults"), caplog, 500,
                      {"error": "discovery configuration unavailable"})


def test_library_is_single_view_and_contextual_links(logged_in):
    page = logged_in.get("/extras/dorkbook")
    assert page.status_code == 200
    assert "Apply to Search" in page.text
    assert "dorkbook-tabs" not in page.text
    assert "recipe" not in page.text.lower()
    assert "provider=self_hosted" in logged_in.get("/scans/searxng").text
    assert "provider=shodan" in logged_in.get("/scans/shodan").text
