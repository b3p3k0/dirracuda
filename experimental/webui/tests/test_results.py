"""Route integration tests for Web UI results endpoints (C5)."""

import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from experimental.webui.app import create_app
from experimental.webui.auth import set_password
from experimental.webui.config import TLSConfig, WebUIConfig

_USERNAME = "resultsuser"
_PASSWORD = "correct-horse-battery-staple"


# ---------------------------------------------------------------------------
# Fixtures — credentials and config
# ---------------------------------------------------------------------------


@pytest.fixture
def creds(tmp_path):
    p = tmp_path / "creds.json"
    set_password(_USERNAME, _PASSWORD, path=p)
    return p


@pytest.fixture
def cfg_no_tls():
    return WebUIConfig(tls=TLSConfig(enabled=False))


# ---------------------------------------------------------------------------
# Fixtures — databases
# ---------------------------------------------------------------------------


@pytest.fixture
def db_smb_only(tmp_path):
    """DB with smb_servers table only (no share_access)."""
    db_path = tmp_path / "smb.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE smb_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL UNIQUE,
            country TEXT,
            country_code TEXT,
            auth_method TEXT,
            status TEXT DEFAULT 'active',
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            scan_count INTEGER DEFAULT 1
        );
        INSERT INTO smb_servers (ip_address, country, country_code, auth_method)
        VALUES ('10.0.0.1', 'United States', 'US', 'ntlm');
        INSERT INTO smb_servers (ip_address, country, country_code, auth_method)
        VALUES ('10.0.0.2', 'Germany', 'DE', 'guest');
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db_with_shares(tmp_path):
    """DB with smb_servers + share_access (including share_comment)."""
    db_path = tmp_path / "shares.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE smb_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL UNIQUE,
            country TEXT,
            country_code TEXT,
            auth_method TEXT,
            status TEXT DEFAULT 'active',
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            scan_count INTEGER DEFAULT 1
        );
        CREATE TABLE share_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            share_name TEXT NOT NULL,
            accessible BOOLEAN NOT NULL DEFAULT 0,
            permissions TEXT,
            share_comment TEXT
        );
        INSERT INTO smb_servers (ip_address, country, country_code) VALUES ('10.0.0.1', 'US', 'US');
        INSERT INTO smb_servers (ip_address, country, country_code) VALUES ('10.0.0.2', 'DE', 'DE');
        INSERT INTO share_access (server_id, share_name, accessible, share_comment)
            VALUES (1, 'Public', 1, 'Public share');
        INSERT INTO share_access (server_id, share_name, accessible)
            VALUES (1, 'Private', 0);
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db_ftp_with_probe(tmp_path):
    """DB with ftp_servers + ftp_probe_cache (accessible_dirs_count populated)."""
    db_path = tmp_path / "ftp.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE ftp_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL UNIQUE,
            country TEXT,
            country_code TEXT,
            port INTEGER DEFAULT 21,
            anon_accessible BOOLEAN DEFAULT 0,
            status TEXT DEFAULT 'active',
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            scan_count INTEGER DEFAULT 1
        );
        CREATE TABLE ftp_probe_cache (
            server_id INTEGER PRIMARY KEY,
            accessible_dirs_count INTEGER DEFAULT 0
        );
        INSERT INTO ftp_servers (ip_address, country, country_code, port, anon_accessible)
            VALUES ('10.1.0.1', 'US', 'US', 21, 1);
        INSERT INTO ftp_probe_cache (server_id, accessible_dirs_count) VALUES (1, 3);
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db_ftp_probe_no_col(tmp_path):
    """DB with ftp_probe_cache but accessible_dirs_count column missing."""
    db_path = tmp_path / "ftp_partial.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE ftp_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL UNIQUE,
            country TEXT,
            country_code TEXT,
            port INTEGER DEFAULT 21,
            anon_accessible BOOLEAN DEFAULT 0,
            status TEXT DEFAULT 'active',
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            scan_count INTEGER DEFAULT 1
        );
        CREATE TABLE ftp_probe_cache (
            server_id INTEGER PRIMARY KEY
        );
        INSERT INTO ftp_servers (ip_address, country, country_code, port)
            VALUES ('10.1.0.2', 'US', 'US', 21);
        INSERT INTO ftp_probe_cache (server_id) VALUES (1);
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db_http_with_access(tmp_path):
    """DB with http_servers + http_access (all summary columns present)."""
    db_path = tmp_path / "http.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE http_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            country TEXT,
            country_code TEXT,
            port INTEGER DEFAULT 80,
            scheme TEXT DEFAULT 'http',
            title TEXT,
            status TEXT DEFAULT 'active',
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            scan_count INTEGER DEFAULT 1,
            UNIQUE(ip_address, port)
        );
        CREATE TABLE http_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            accessible BOOLEAN DEFAULT 1,
            is_index_page BOOLEAN DEFAULT 0,
            dir_count INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0
        );
        INSERT INTO http_servers (ip_address, country, country_code, port, scheme)
            VALUES ('10.2.0.1', 'US', 'US', 8080, 'http');
        INSERT INTO http_access (server_id, accessible, is_index_page, dir_count, file_count)
            VALUES (1, 1, 1, 5, 12);
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db_http_access_partial(tmp_path):
    """DB with http_access table that has only id and server_id (no summary columns)."""
    db_path = tmp_path / "http_partial.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE http_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            country TEXT,
            country_code TEXT,
            port INTEGER DEFAULT 80,
            scheme TEXT DEFAULT 'http',
            title TEXT,
            status TEXT DEFAULT 'active',
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            scan_count INTEGER DEFAULT 1,
            UNIQUE(ip_address, port)
        );
        CREATE TABLE http_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL
        );
        INSERT INTO http_servers (ip_address, country, country_code, port, scheme)
            VALUES ('10.2.0.2', 'US', 'US', 80, 'http');
        INSERT INTO http_access (server_id) VALUES (1);
    """)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Fixtures — test clients
# ---------------------------------------------------------------------------


@pytest.fixture
def client(creds, cfg_no_tls, db_smb_only):
    app = create_app(cfg=cfg_no_tls, creds_path=creds, db_path=db_smb_only)
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def logged_in_client(client):
    r = client.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    return client


def _csrf(client):
    dash = client.get("/dashboard")
    assert dash.status_code == 200
    m = re.search(r'name="csrf-token" content="([^"]+)"', dash.text)
    assert m, "csrf-token meta tag not found"
    return m.group(1)


def _logged_in(creds, cfg_no_tls, db_path):
    app = create_app(cfg=cfg_no_tls, creds_path=creds, db_path=db_path)
    c = TestClient(app, follow_redirects=False)
    c.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    return c


# ---------------------------------------------------------------------------
# Auth protection
# ---------------------------------------------------------------------------


def test_results_page_requires_auth(client):
    r = client.get("/results")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_api_results_requires_auth(client):
    r = client.get("/api/results/smb")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_results_page_authenticated(logged_in_client):
    r = logged_in_client.get("/results")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Pagination bounds
# ---------------------------------------------------------------------------


def test_page_zero_rejected(logged_in_client):
    r = logged_in_client.get("/api/results/smb?page=0")
    assert r.status_code == 400


def test_page_over_max_rejected(logged_in_client):
    r = logged_in_client.get("/api/results/smb?page=10001")
    assert r.status_code == 400


def test_page_size_zero_rejected(logged_in_client):
    r = logged_in_client.get("/api/results/smb?page_size=0")
    assert r.status_code == 400


def test_page_size_over_max_rejected(logged_in_client):
    r = logged_in_client.get("/api/results/smb?page_size=201")
    assert r.status_code == 400


def test_valid_pagination(logged_in_client):
    r = logged_in_client.get("/api/results/smb?page=1&page_size=10")
    assert r.status_code == 200
    data = r.json()
    assert "results" in data
    assert data["page"] == 1
    assert data["page_size"] == 10


# ---------------------------------------------------------------------------
# Country filter
# ---------------------------------------------------------------------------


def test_country_invalid_chars_rejected(logged_in_client):
    r = logged_in_client.get("/api/results/smb?country=1A")
    assert r.status_code == 400


def test_country_too_long_rejected(logged_in_client):
    r = logged_in_client.get("/api/results/smb?country=USA")
    assert r.status_code == 400


def test_country_valid_two_letter(logged_in_client):
    r = logged_in_client.get("/api/results/smb?country=US")
    assert r.status_code == 200
    data = r.json()
    for row in data["results"]:
        assert row["country_code"] == "US"


def test_country_lowercase_normalized(logged_in_client):
    r = logged_in_client.get("/api/results/smb?country=us")
    assert r.status_code == 200
    data = r.json()
    for row in data["results"]:
        assert row["country_code"] == "US"


# ---------------------------------------------------------------------------
# Invalid protocol
# ---------------------------------------------------------------------------


def test_invalid_protocol_rejected(logged_in_client):
    r = logged_in_client.get("/api/results/rdp")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# SMB results content
# ---------------------------------------------------------------------------


def test_smb_results_returned(logged_in_client):
    r = logged_in_client.get("/api/results/smb")
    assert r.status_code == 200
    data = r.json()
    assert len(data["results"]) == 2
    for row in data["results"]:
        assert "ip" in row
        assert row["copy_str"].startswith("smb://")
        assert row["accessible_shares"] == 0


def test_smb_results_empty_when_table_absent(creds, cfg_no_tls, tmp_path):
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    conn.commit()
    conn.close()
    c = _logged_in(creds, cfg_no_tls, db_path)
    r = c.get("/api/results/smb")
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_smb_results_empty_when_db_absent(creds, cfg_no_tls, tmp_path):
    c = _logged_in(creds, cfg_no_tls, tmp_path / "missing.db")
    r = c.get("/api/results/smb")
    assert r.status_code == 200
    assert r.json()["results"] == []


# ---------------------------------------------------------------------------
# Share summaries
# ---------------------------------------------------------------------------


def test_share_summary_present_when_share_access_exists(creds, cfg_no_tls, db_with_shares):
    c = _logged_in(creds, cfg_no_tls, db_with_shares)
    r = c.get("/api/results/smb")
    assert r.status_code == 200
    rows = r.json()["results"]
    row = next(x for x in rows if x["ip"] == "10.0.0.1")
    assert row["accessible_shares"] == 1
    assert "Public" in row["share_names"]


def test_share_fallback_when_share_access_absent(logged_in_client):
    r = logged_in_client.get("/api/results/smb")
    assert r.status_code == 200
    for row in r.json()["results"]:
        assert row["accessible_shares"] == 0


# ---------------------------------------------------------------------------
# SQL injection prevention
# ---------------------------------------------------------------------------


def test_country_sql_injection_rejected(logged_in_client):
    r = logged_in_client.get("/api/results/smb?country='; DROP TABLE smb_servers; --")
    assert r.status_code == 400


def test_country_filter_is_parameterized(creds, cfg_no_tls, db_smb_only):
    c = _logged_in(creds, cfg_no_tls, db_smb_only)
    r = c.get("/api/results/smb?country=DE")
    assert r.status_code == 200
    rows = r.json()["results"]
    assert len(rows) == 1
    assert rows[0]["country_code"] == "DE"


# ---------------------------------------------------------------------------
# FTP / HTTP — empty table (no match)
# ---------------------------------------------------------------------------


def test_ftp_results_empty_without_table(logged_in_client):
    r = logged_in_client.get("/api/results/ftp")
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_http_results_empty_without_table(logged_in_client):
    r = logged_in_client.get("/api/results/http")
    assert r.status_code == 200
    assert r.json()["results"] == []


# ---------------------------------------------------------------------------
# FTP / HTTP — positive summary paths
# ---------------------------------------------------------------------------


def test_ftp_accessible_dirs_present(creds, cfg_no_tls, db_ftp_with_probe):
    c = _logged_in(creds, cfg_no_tls, db_ftp_with_probe)
    r = c.get("/api/results/ftp")
    assert r.status_code == 200
    rows = r.json()["results"]
    assert len(rows) == 1
    assert rows[0]["accessible_dirs"] == 3
    assert rows[0]["copy_str"] == "ftp://10.1.0.1:21"


def test_http_summary_fields_present(creds, cfg_no_tls, db_http_with_access):
    c = _logged_in(creds, cfg_no_tls, db_http_with_access)
    r = c.get("/api/results/http")
    assert r.status_code == 200
    rows = r.json()["results"]
    assert len(rows) == 1
    row = rows[0]
    assert row["dir_count"] == 5
    assert row["file_count"] == 12
    assert row["is_index_page"] is True
    assert row["last_accessible"] is True
    assert row["copy_str"] == "http://10.2.0.1:8080"


# ---------------------------------------------------------------------------
# Missing-column fallback
# ---------------------------------------------------------------------------


def test_ftp_probe_cache_missing_dirs_column(creds, cfg_no_tls, db_ftp_probe_no_col):
    c = _logged_in(creds, cfg_no_tls, db_ftp_probe_no_col)
    r = c.get("/api/results/ftp")
    assert r.status_code == 200
    rows = r.json()["results"]
    assert len(rows) == 1
    assert rows[0]["accessible_dirs"] == 0


def test_http_access_missing_dir_columns(creds, cfg_no_tls, db_http_access_partial):
    c = _logged_in(creds, cfg_no_tls, db_http_access_partial)
    r = c.get("/api/results/http")
    assert r.status_code == 200
    rows = r.json()["results"]
    assert len(rows) == 1
    row = rows[0]
    assert row["dir_count"] == 0
    assert row["file_count"] == 0
    assert row["is_index_page"] is False
    assert row["last_accessible"] is False
