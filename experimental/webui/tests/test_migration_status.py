"""Tests for get_sidecar_migration_status and WebUI dashboard migration notice (C5)."""

from __future__ import annotations

import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from experimental.webui.app import create_app
from experimental.webui.auth import set_password
from experimental.webui.config import TLSConfig, WebUIConfig
import experimental.webui.db as _db

_USERNAME = "mig_tester"
_PASSWORD = "correct-horse-battery-staple"

_STATE_KEY = "db_unification.sidecar_prompt.state"
_SUMMARY_KEY = "db_unification.sidecar_prompt.last_summary"
_ERROR_KEY = "db_unification.sidecar_prompt.last_error"


def _make_db(tmp_path, state=None, summary=None, error=None):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE app_migration_state (key TEXT PRIMARY KEY, value TEXT)"
    )
    if state is not None:
        conn.execute(
            "INSERT INTO app_migration_state (key, value) VALUES (?, ?)",
            (_STATE_KEY, state),
        )
    if summary is not None:
        conn.execute(
            "INSERT INTO app_migration_state (key, value) VALUES (?, ?)",
            (_SUMMARY_KEY, summary),
        )
    if error is not None:
        conn.execute(
            "INSERT INTO app_migration_state (key, value) VALUES (?, ?)",
            (_ERROR_KEY, error),
        )
    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# Unit tests for get_sidecar_migration_status
# ---------------------------------------------------------------------------

def test_get_sidecar_migration_status_not_started(tmp_path):
    db = _make_db(tmp_path)
    result = _db.get_sidecar_migration_status(db)
    assert result == {"state": "not_started", "summary": None, "error": None}


def test_get_sidecar_migration_status_deferred(tmp_path):
    db = _make_db(tmp_path, state="deferred")
    result = _db.get_sidecar_migration_status(db)
    assert result["state"] == "deferred"


def test_get_sidecar_migration_status_completed_with_summary(tmp_path):
    db = _make_db(tmp_path, state="completed", summary='{"imported": 5}')
    result = _db.get_sidecar_migration_status(db)
    assert result["state"] == "completed"
    assert result["summary"] == '{"imported": 5}'


def test_get_sidecar_migration_status_unknown_on_error(tmp_path):
    bad_path = tmp_path / "nonexistent.db"
    result = _db.get_sidecar_migration_status(bad_path)
    assert result["state"] == "unknown"
    assert result["summary"] is None
    assert result["error"] is None


# ---------------------------------------------------------------------------
# Route tests for the migration notice card
# ---------------------------------------------------------------------------

@pytest.fixture
def creds(tmp_path):
    p = tmp_path / "creds.json"
    set_password(_USERNAME, _PASSWORD, path=p)
    return p


@pytest.fixture
def cfg_no_tls():
    return WebUIConfig(tls=TLSConfig(enabled=False))


def _logged_in_client(creds, cfg_no_tls, db_path):
    app = create_app(cfg=cfg_no_tls, creds_path=creds, db_path=str(db_path))
    client = TestClient(app, follow_redirects=False)
    r = client.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    return client


def test_dashboard_route_migration_notice_absent_when_not_started(tmp_path, creds, cfg_no_tls):
    db = _make_db(tmp_path)
    client = _logged_in_client(creds, cfg_no_tls, db)
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Migration Status" not in r.text


def test_dashboard_route_migration_notice_present_when_deferred(tmp_path, creds, cfg_no_tls):
    db = _make_db(tmp_path, state="deferred")
    client = _logged_in_client(creds, cfg_no_tls, db)
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Migration Status" in r.text
    assert "deferred" in r.text
