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
        lambda *_a, **_kw: contextlib.nullcontext(mock_conn),
    )
    monkeypatch.setattr(dork_store, "list_entries", lambda conn, protocol, search_text="": [_SMB_ROW])

    r = logged_in.get("/api/dorkbook/entries?protocol=SMB")
    assert r.status_code == 200
    data = r.json()
    assert "entries" in data
    assert data["entries"][0]["protocol"] == "SMB"


def test_list_entries_rejects_invalid_protocol(logged_in):
    r = logged_in.get("/api/dorkbook/entries?protocol=INVALID")
    assert r.status_code == 422


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
        lambda *_a, **_kw: contextlib.nullcontext(mock_conn),
    )
    monkeypatch.setattr(
        dork_store, "create_entry",
        lambda conn, protocol, nickname, query, notes: 99,
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


def test_create_entry_rejects_duplicate(logged_in, monkeypatch):
    from experimental.dorkbook.models import DuplicateEntryError
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", lambda *_a, **_kw: None)
    mock_conn = MagicMock()
    monkeypatch.setattr(
        dork_store,
        "open_connection",
        lambda *_a, **_kw: contextlib.nullcontext(mock_conn),
    )
    monkeypatch.setattr(
        dork_store, "create_entry",
        lambda *_a, **_kw: (_ for _ in ()).throw(DuplicateEntryError("already exists")),
    )

    r = logged_in.post(
        "/api/dorkbook/entries",
        json={"protocol": "SMB", "query": "smb authentication: disabled"},
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 409


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
        lambda *_a, **_kw: contextlib.nullcontext(mock_conn),
    )
    monkeypatch.setattr(dork_store, "get_entry", lambda conn, entry_id: _SMB_ROW)

    r = logged_in.delete(
        "/api/dorkbook/entries/1",
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 403


def test_delete_entry_succeeds(logged_in, monkeypatch):
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", lambda *_a, **_kw: None)
    mock_conn = MagicMock()
    monkeypatch.setattr(
        dork_store,
        "open_connection",
        lambda *_a, **_kw: contextlib.nullcontext(mock_conn),
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
        lambda *_a, **_kw: contextlib.nullcontext(mock_conn),
    )
    monkeypatch.setattr(dork_store, "get_entry", lambda conn, entry_id: None)

    r = logged_in.post(
        "/api/dorkbook/prefill",
        json={"entry_id": 999},
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 404


def test_prefill_writes_to_config(logged_in, monkeypatch, main_config_path):
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", lambda *_a, **_kw: None)
    mock_conn = MagicMock()
    monkeypatch.setattr(
        dork_store,
        "open_connection",
        lambda *_a, **_kw: contextlib.nullcontext(mock_conn),
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


def test_prefill_returns_404_when_config_missing(logged_in, app, monkeypatch, tmp_path):
    tok = _csrf(logged_in)
    monkeypatch.setattr(dork_store, "init_db", lambda *_a, **_kw: None)
    mock_conn = MagicMock()
    monkeypatch.setattr(
        dork_store,
        "open_connection",
        lambda *_a, **_kw: contextlib.nullcontext(mock_conn),
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
