"""Route integration tests for unified Web UI results endpoints (C17)."""

import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from experimental.webui.app import create_app
from experimental.webui.auth import set_password
from experimental.webui.config import TLSConfig, WebUIConfig

_USERNAME = "resultsuser"
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
def db_smb_only(tmp_path):
    """DB with only SMB protocol tables present."""
    db_path = tmp_path / "smb_only.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE smb_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL UNIQUE,
            country TEXT,
            country_code TEXT,
            status TEXT DEFAULT 'active',
            last_seen TEXT
        );
        INSERT INTO smb_servers (ip_address, country, country_code, status, last_seen)
        VALUES
            ('10.0.0.10', 'United States', 'US', 'active', '2026-05-10T14:22:00'),
            ('10.0.0.11', 'Germany', 'DE', 'active', '2026-05-10T14:20:00');
        """
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db_all_protocols(tmp_path):
    """DB with SMB/FTP/HTTP rows + user/probe metadata for desktop-parity row fields."""
    db_path = tmp_path / "all_protocols.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE smb_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL UNIQUE,
            country TEXT,
            country_code TEXT,
            status TEXT DEFAULT 'active',
            last_seen TEXT
        );
        CREATE TABLE ftp_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL UNIQUE,
            country TEXT,
            country_code TEXT,
            status TEXT DEFAULT 'active',
            last_seen TEXT
        );
        CREATE TABLE http_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL UNIQUE,
            country TEXT,
            country_code TEXT,
            status TEXT DEFAULT 'active',
            last_seen TEXT
        );

        CREATE TABLE share_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            share_name TEXT NOT NULL,
            accessible BOOLEAN NOT NULL DEFAULT 0
        );

        CREATE TABLE host_user_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            favorite INTEGER DEFAULT 0,
            avoid INTEGER DEFAULT 0
        );
        CREATE TABLE ftp_user_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            favorite INTEGER DEFAULT 0,
            avoid INTEGER DEFAULT 0
        );
        CREATE TABLE http_user_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            favorite INTEGER DEFAULT 0,
            avoid INTEGER DEFAULT 0
        );

        CREATE TABLE host_probe_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            status TEXT,
            extracted INTEGER DEFAULT 0
        );
        CREATE TABLE ftp_probe_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            status TEXT,
            extracted INTEGER DEFAULT 0,
            accessible_dirs_count INTEGER DEFAULT 0,
            accessible_dirs_list TEXT
        );
        CREATE TABLE http_probe_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            status TEXT,
            extracted INTEGER DEFAULT 0,
            accessible_dirs_count INTEGER DEFAULT 0,
            accessible_files_count INTEGER DEFAULT 0,
            accessible_dirs_list TEXT
        );
        CREATE TABLE ftp_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            accessible BOOLEAN DEFAULT 0,
            auth_status TEXT,
            root_listing_available BOOLEAN DEFAULT 0,
            root_entry_count INTEGER DEFAULT 0,
            error_message TEXT,
            access_details TEXT,
            test_timestamp TEXT
        );
        CREATE TABLE http_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            accessible BOOLEAN DEFAULT 0,
            status_code INTEGER DEFAULT 0,
            is_index_page BOOLEAN DEFAULT 0,
            dir_count INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0,
            tls_verified BOOLEAN DEFAULT 0,
            error_message TEXT,
            access_details TEXT,
            test_timestamp TEXT
        );

        INSERT INTO smb_servers (ip_address, country, country_code, status, last_seen)
        VALUES ('10.0.0.10', 'United States', 'US', 'active', '2026-05-10T14:22:00');
        INSERT INTO ftp_servers (ip_address, country, country_code, status, last_seen)
        VALUES ('10.0.0.20', 'Germany', 'DE', 'active', '2026-05-10T14:20:00');
        INSERT INTO http_servers (ip_address, country, country_code, status, last_seen)
        VALUES ('10.0.0.30', 'France', 'FR', 'active', '2026-05-10T14:19:00');

        INSERT INTO share_access (server_id, share_name, accessible)
        VALUES (1, 'Public', 1), (1, 'Private', 0);

        INSERT INTO host_user_flags (server_id, favorite, avoid) VALUES (1, 1, 0);
        INSERT INTO ftp_user_flags (server_id, favorite, avoid) VALUES (1, 0, 1);
        INSERT INTO http_user_flags (server_id, favorite, avoid) VALUES (1, 0, 0);

        INSERT INTO host_probe_cache (server_id, status, extracted)
        VALUES (1, 'clean', 0);
        INSERT INTO ftp_probe_cache (
            server_id, status, extracted, accessible_dirs_count, accessible_dirs_list
        )
        VALUES (1, 'issue', 1, 2, 'pub,docs');
        INSERT INTO ftp_access (
            server_id, accessible, auth_status, root_listing_available, root_entry_count, access_details, test_timestamp
        )
        VALUES (1, 1, 'anonymous', 1, 4, '["pub","docs"]', '2026-05-10T14:20:30');
        INSERT INTO http_probe_cache (
            server_id, status, extracted, accessible_dirs_count, accessible_files_count, accessible_dirs_list
        )
        VALUES (1, 'unprobed', 0, 1, 1, '/,/admin');
        INSERT INTO http_access (
            server_id, accessible, status_code, is_index_page, dir_count, file_count, tls_verified, access_details, test_timestamp
        )
        VALUES (1, 1, 200, 1, 5, 12, 0, '{"paths":["/","/admin"]}', '2026-05-10T14:19:30');
        """
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def client(creds, cfg_no_tls, db_smb_only):
    app = create_app(cfg=cfg_no_tls, creds_path=creds, db_path=db_smb_only)
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def logged_in_client(client):
    r = client.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    return client


def _logged_in_client_for_db(creds, cfg_no_tls, db_path):
    app = create_app(cfg=cfg_no_tls, creds_path=creds, db_path=db_path)
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    return c


def test_results_page_requires_auth(client):
    r = client.get("/results")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_results_api_requires_auth(client):
    r = client.get("/api/results/all")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_results_page_defaults_to_all_and_autoload(logged_in_client):
    r = logged_in_client.get("/results")
    assert r.status_code == 200
    assert 'data-proto="all"' in r.text
    assert "proto-tab active" in r.text
    assert "loadResults();" in r.text
    assert "/api/results/details" in r.text
    assert "Show full details" in r.text
    assert "rows', '10'" in r.text or 'rows="10"' in r.text


def test_results_page_renders_filter_controls(logged_in_client):
    r = logged_in_client.get("/results")
    assert r.status_code == 200
    assert "Show Only Shares &gt; 0" in r.text
    assert "Favorites Only" in r.text
    assert "Hide Avoid" in r.text
    m = re.search(r"writeSection\('results', \{([^}]*)\}\);", r.text, re.S)
    assert m is not None
    body = m.group(1)
    assert "protocol" in body
    assert "shares_only" in body
    assert "favorites_only" in body
    assert "hide_avoid" in body
    assert "country" not in body


def test_invalid_protocol_rejected(logged_in_client):
    r = logged_in_client.get("/api/results/rdp")
    assert r.status_code == 422


def test_pagination_bounds_rejected(logged_in_client):
    assert logged_in_client.get("/api/results/all?page=0").status_code == 400
    assert logged_in_client.get("/api/results/all?page=10001").status_code == 400
    assert logged_in_client.get("/api/results/all?page_size=0").status_code == 400
    assert logged_in_client.get("/api/results/all?page_size=201").status_code == 400


def test_country_validation_rejected(logged_in_client):
    assert logged_in_client.get("/api/results/all?country=1A").status_code == 400
    assert logged_in_client.get("/api/results/all?country=USA").status_code == 400


def test_country_sql_injection_rejected(logged_in_client):
    r = logged_in_client.get("/api/results/all?country='; DROP TABLE smb_servers; --")
    assert r.status_code == 400


def test_all_protocol_results_mixed_rows_and_metadata(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get("/api/results/all?page=1&page_size=50")
    assert r.status_code == 200
    payload = r.json()

    assert payload["total_count"] == 3
    assert payload["total_pages"] == 1
    assert payload["page"] == 1
    assert payload["page_size"] == 50

    rows = payload["results"]
    assert [row["host_type"] for row in rows] == ["S", "F", "H"]
    for row in rows:
        assert set(row.keys()) == {
            "row_key",
            "protocol_server_id",
            "favorite",
            "avoid",
            "probe_status_emoji",
            "extract_status_emoji",
            "host_type",
            "ip_address",
            "shares",
            "accessible_shares_list",
            "denied_shares_count",
            "last_seen",
            "country",
        }
        assert row["row_key"]
        assert row["protocol_server_id"] > 0

    smb = rows[0]
    assert smb["row_key"] == "S:1"
    assert smb["favorite"] == "✔"
    assert smb["avoid"] == "○"
    assert smb["probe_status_emoji"] == "✔"
    assert smb["extract_status_emoji"] == "○"
    assert smb["shares"] == "📁 1"
    assert smb["accessible_shares_list"] == "Public"
    assert smb["denied_shares_count"] == 1
    assert smb["country"] == "United States"

    ftp = rows[1]
    assert ftp["row_key"] == "F:1"
    assert ftp["favorite"] == "○"
    assert ftp["avoid"] == "✖"
    assert ftp["probe_status_emoji"] == "✖"
    assert ftp["extract_status_emoji"] == "✔"
    assert ftp["shares"] == "📁 2"
    assert ftp["accessible_shares_list"] == "pub,docs"
    assert ftp["denied_shares_count"] == 0

    http = rows[2]
    assert http["row_key"] == "H:1"
    assert http["probe_status_emoji"] == "○"
    assert http["extract_status_emoji"] == "○"
    assert http["shares"] == "📁 2"
    assert http["accessible_shares_list"] == "/,/admin"
    assert http["denied_shares_count"] == 0


def test_protocol_filter_rows_match_host_type(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)

    smb_rows = c.get("/api/results/smb").json()["results"]
    assert len(smb_rows) == 1
    assert smb_rows[0]["host_type"] == "S"

    ftp_rows = c.get("/api/results/ftp").json()["results"]
    assert len(ftp_rows) == 1
    assert ftp_rows[0]["host_type"] == "F"

    http_rows = c.get("/api/results/http").json()["results"]
    assert len(http_rows) == 1
    assert http_rows[0]["host_type"] == "H"


def test_country_filter_on_all_protocols(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get("/api/results/all?country=DE")
    assert r.status_code == 200
    rows = r.json()["results"]
    assert len(rows) == 1
    assert rows[0]["host_type"] == "F"
    assert rows[0]["country"] == "Germany"


def test_country_filter_lowercase_normalized(logged_in_client):
    r = logged_in_client.get("/api/results/all?country=us")
    assert r.status_code == 200
    rows = r.json()["results"]
    assert len(rows) == 1
    assert rows[0]["country"] == "United States"


def test_shares_only_filter_excludes_zero_share_rows(logged_in_client):
    r = logged_in_client.get("/api/results/smb?shares_only=true")
    assert r.status_code == 200
    payload = r.json()
    assert payload["results"] == []
    assert payload["total_count"] == 0
    assert payload["total_pages"] == 1


def test_favorites_only_filter_returns_only_favorited_rows(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get("/api/results/all?favorites_only=true")
    assert r.status_code == 200
    payload = r.json()
    assert payload["total_count"] == 1
    assert payload["total_pages"] == 1
    assert len(payload["results"]) == 1
    assert payload["results"][0]["host_type"] == "S"
    assert payload["results"][0]["favorite"] == "✔"


def test_hide_avoid_filter_excludes_avoid_rows(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get("/api/results/all?hide_avoid=true")
    assert r.status_code == 200
    payload = r.json()
    assert payload["total_count"] == 2
    assert payload["total_pages"] == 1
    assert [row["host_type"] for row in payload["results"]] == ["S", "H"]
    assert all(row["avoid"] == "○" for row in payload["results"])


def test_filter_combination_applies_before_pagination(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get(
        "/api/results/all"
        "?shares_only=true&favorites_only=true&hide_avoid=true&page=1&page_size=1"
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["total_count"] == 1
    assert payload["total_pages"] == 1
    assert payload["page"] == 1
    assert payload["page_size"] == 1
    assert len(payload["results"]) == 1
    assert payload["results"][0]["host_type"] == "S"


def test_protocol_specific_filter_rows(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get("/api/results/ftp?hide_avoid=true")
    assert r.status_code == 200
    payload = r.json()
    assert payload["total_count"] == 0
    assert payload["total_pages"] == 1
    assert payload["results"] == []


def test_pagination_metadata_and_windowing(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get("/api/results/all?page=2&page_size=2")
    assert r.status_code == 200
    payload = r.json()
    assert payload["total_count"] == 3
    assert payload["total_pages"] == 2
    assert payload["page"] == 2
    assert payload["page_size"] == 2
    assert len(payload["results"]) == 1
    assert payload["results"][0]["host_type"] == "H"


def test_all_results_graceful_when_optional_tables_absent(logged_in_client):
    r = logged_in_client.get("/api/results/all")
    assert r.status_code == 200
    payload = r.json()
    assert payload["total_count"] == 2
    assert payload["total_pages"] == 1
    assert len(payload["results"]) == 2
    assert all(row["host_type"] == "S" for row in payload["results"])


def test_results_empty_when_db_absent(creds, cfg_no_tls, tmp_path):
    c = _logged_in_client_for_db(creds, cfg_no_tls, tmp_path / "missing.db")
    r = c.get("/api/results/all")
    assert r.status_code == 200
    payload = r.json()
    assert payload["results"] == []
    assert payload["total_count"] == 0
    assert payload["total_pages"] == 1


def test_result_details_smb_success(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get("/api/results/details?host_type=S&protocol_server_id=1")
    assert r.status_code == 200
    payload = r.json()
    assert payload["row_key"] == "S:1"
    assert payload["host_type"] == "S"
    assert payload["protocol"] == "SMB"
    assert payload["ip_address"] == "10.0.0.10"
    assert payload["overview"]["access_summary"] == "accessible=1, denied=1"
    assert "Protocol: SMB" in payload["full_details_text"]
    assert "Accessible Shares (1): Public" in payload["full_details_text"]


def test_result_details_ftp_success(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get("/api/results/details?host_type=F&protocol_server_id=1")
    assert r.status_code == 200
    payload = r.json()
    assert payload["row_key"] == "F:1"
    assert payload["host_type"] == "F"
    assert payload["protocol"] == "FTP"
    assert payload["overview"]["access_summary"] == "dirs=2, denied=0"
    assert "Protocol: FTP" in payload["full_details_text"]
    assert "Auth Status: anonymous" in payload["full_details_text"]


def test_result_details_http_success(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get("/api/results/details?host_type=H&protocol_server_id=1")
    assert r.status_code == 200
    payload = r.json()
    assert payload["row_key"] == "H:1"
    assert payload["host_type"] == "H"
    assert payload["protocol"] == "HTTP"
    assert payload["overview"]["access_summary"] == "dirs=1, files=1"
    assert "Protocol: HTTP" in payload["full_details_text"]
    assert "Status Code: 200" in payload["full_details_text"]


def test_result_details_invalid_params_rejected(logged_in_client):
    assert (
        logged_in_client.get("/api/results/details?host_type=Z&protocol_server_id=1").status_code
        == 400
    )
    assert (
        logged_in_client.get("/api/results/details?host_type=S&protocol_server_id=abc").status_code
        == 400
    )
    assert (
        logged_in_client.get("/api/results/details?host_type=S&protocol_server_id=0").status_code
        == 400
    )


def test_result_details_not_found_returns_404(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get("/api/results/details?host_type=S&protocol_server_id=99")
    assert r.status_code == 404


def test_result_details_schema_fallback_without_optional_tables(logged_in_client):
    r = logged_in_client.get("/api/results/details?host_type=S&protocol_server_id=1")
    assert r.status_code == 200
    payload = r.json()
    assert payload["host_type"] == "S"
    assert payload["overview"]["access_summary"] == "accessible=0, denied=0"
    assert "Protocol: SMB" in payload["full_details_text"]
