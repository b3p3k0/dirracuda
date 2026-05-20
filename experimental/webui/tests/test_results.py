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
            indicator_matches INTEGER DEFAULT 0,
            extracted INTEGER DEFAULT 0,
            latest_snapshot_id INTEGER
        );
        CREATE TABLE ftp_probe_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            status TEXT,
            indicator_matches INTEGER DEFAULT 0,
            extracted INTEGER DEFAULT 0,
            accessible_dirs_count INTEGER DEFAULT 0,
            accessible_dirs_list TEXT,
            latest_snapshot_id INTEGER
        );
        CREATE TABLE http_probe_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            status TEXT,
            indicator_matches INTEGER DEFAULT 0,
            extracted INTEGER DEFAULT 0,
            accessible_dirs_count INTEGER DEFAULT 0,
            accessible_files_count INTEGER DEFAULT 0,
            accessible_dirs_list TEXT,
            latest_snapshot_id INTEGER
        );
        CREATE TABLE probe_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_snapshot_json TEXT NOT NULL
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

        INSERT INTO probe_snapshots (raw_snapshot_json)
        VALUES ('{"shares":[{"share":"Public","directories":[{"name":"pub","subdirectories":["docs"],"files":["docs/readme.txt","users.csv"]}],"root_files":["top.txt"]}]}');
        INSERT INTO probe_snapshots (raw_snapshot_json)
        VALUES ('{"shares":[{"share":"ftp_root","directories":[{"name":"pub","subdirectories":["docs"],"files":["docs/readme.txt","users.csv"]},{"name":"incoming","files":["drop.zip"]}]}]}');
        INSERT INTO probe_snapshots (raw_snapshot_json)
        VALUES ('{"shares":[{"share":"http_root","directories":[{"name":"/","subdirectories":["admin"],"files":["index.html","admin/panel.html"]}]}]}');

        INSERT INTO host_probe_cache (server_id, status, indicator_matches, extracted, latest_snapshot_id)
        VALUES (1, 'clean', 0, 0, 1);
        INSERT INTO ftp_probe_cache (
            server_id, status, indicator_matches, extracted, accessible_dirs_count, accessible_dirs_list, latest_snapshot_id
        )
        VALUES (1, 'issue', 4, 1, 2, 'pub,docs', 2);
        INSERT INTO ftp_access (
            server_id, accessible, auth_status, root_listing_available, root_entry_count, access_details, test_timestamp
        )
        VALUES (1, 1, 'anonymous', 1, 4, '["pub","docs"]', '2026-05-10T14:20:30');
        INSERT INTO http_probe_cache (
            server_id, status, indicator_matches, extracted, accessible_dirs_count, accessible_files_count, accessible_dirs_list, latest_snapshot_id
        )
        VALUES (1, 'unprobed', 0, 0, 1, 1, '/,/admin', 3);
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


def _csrf(client):
    dash = client.get("/dashboard")
    assert dash.status_code == 200
    m = re.search(r'name="csrf-token" content="([^"]+)"', dash.text)
    assert m, "csrf-token meta tag not found"
    return m.group(1)


def _post_toggle(client, payload, csrf=None, headers=None):
    req_headers = dict(headers or {})
    if csrf is not None:
        req_headers["X-CSRF-Token"] = csrf
    return client.post("/api/results/actions/toggle", json=payload, headers=req_headers)


def _fetch_int(db_path, sql, params):
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        conn.close()


def _fetch_probe(db_path, table, server_id):
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            f"SELECT COALESCE(status, 'unprobed'), COALESCE(indicator_matches, 0) "
            f"FROM {table} WHERE server_id = ? LIMIT 1",
            (server_id,),
        ).fetchone()
        if row is None:
            return ("unprobed", 0)
        return (str(row[0]).lower(), int(row[1]))
    finally:
        conn.close()


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
    assert '/static/results.js' in r.text


def test_results_page_renders_filter_controls(logged_in_client):
    r = logged_in_client.get("/results")
    assert r.status_code == 200
    assert 'id="search-filter"' in r.text
    assert "Search IP or shares" in r.text
    assert "Show Only Shares &gt; 0" in r.text
    assert "Favorites Only" in r.text
    assert "Hide Avoid" in r.text
    assert "country-filter" not in r.text
    assert "&country=" not in r.text


def test_invalid_protocol_rejected(logged_in_client):
    r = logged_in_client.get("/api/results/rdp")
    assert r.status_code == 422


def test_pagination_bounds_rejected(logged_in_client):
    assert logged_in_client.get("/api/results/all?page=0").status_code == 400
    assert logged_in_client.get("/api/results/all?page=10001").status_code == 400
    assert logged_in_client.get("/api/results/all?page_size=0").status_code == 400
    assert logged_in_client.get("/api/results/all?page_size=201").status_code == 400


def test_legacy_country_filter_rejected(logged_in_client):
    r = logged_in_client.get("/api/results/all?country=US")
    assert r.status_code == 400
    assert "country filter has been removed" in r.json()["error"]


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


def test_search_by_ip_substring_on_all_protocols(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get("/api/results/all?search=10.0.0.20")
    assert r.status_code == 200
    rows = r.json()["results"]
    assert len(rows) == 1
    assert rows[0]["host_type"] == "F"
    assert rows[0]["ip_address"] == "10.0.0.20"


def test_search_by_accessible_share_substring_case_insensitive(
    creds, cfg_no_tls, db_all_protocols
):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get("/api/results/all?search=PUB")
    assert r.status_code == 200
    rows = r.json()["results"]
    assert len(rows) == 2
    assert [row["host_type"] for row in rows] == ["S", "F"]
    assert rows[0]["accessible_shares_list"] == "Public"
    assert rows[1]["accessible_shares_list"] == "pub,docs"


def test_search_no_match_returns_empty_with_metadata(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get("/api/results/all?search=does-not-exist")
    assert r.status_code == 200
    payload = r.json()
    assert payload["results"] == []
    assert payload["total_count"] == 0
    assert payload["total_pages"] == 1
    assert payload["page"] == 1
    assert payload["page_size"] == 50


def test_search_protocol_scoped_filters_by_protocol_and_search(
    creds, cfg_no_tls, db_all_protocols
):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = c.get("/api/results/smb?search=10.0.0.2")
    assert r.status_code == 200
    rows = r.json()["results"]
    assert rows == []


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
    assert "Probe Snapshot Tree (stored):" in payload["full_details_text"]
    assert "Share: Public" in payload["full_details_text"]
    assert "📁 docs/" in payload["full_details_text"]


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
    assert "Probe Snapshot Tree (stored):" in payload["full_details_text"]
    assert "Share: ftp_root" in payload["full_details_text"]
    assert "📁 incoming/" in payload["full_details_text"]
    assert "Auth Status: anonymous" in payload["full_details_text"]
    assert "Access Details:" not in payload["full_details_text"]


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
    assert "Probe Snapshot Tree (stored):" in payload["full_details_text"]
    assert "Share: http_root" in payload["full_details_text"]
    assert "Status Code: 200" in payload["full_details_text"]
    assert "Access Details:" not in payload["full_details_text"]


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


def test_results_toggle_actions_requires_auth(client):
    r = _post_toggle(
        client,
        {
            "action": "favorite",
            "targets": [{"host_type": "S", "protocol_server_id": 1, "row_key": "S:1"}],
        },
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_results_toggle_actions_missing_csrf_returns_403(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    r = _post_toggle(
        c,
        {
            "action": "favorite",
            "targets": [{"host_type": "S", "protocol_server_id": 1, "row_key": "S:1"}],
        },
    )
    assert r.status_code == 403


def test_results_toggle_actions_bad_origin_returns_403(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    csrf = _csrf(c)
    r = _post_toggle(
        c,
        {
            "action": "favorite",
            "targets": [{"host_type": "S", "protocol_server_id": 1, "row_key": "S:1"}],
        },
        csrf=csrf,
        headers={"Origin": "http://attacker.com"},
    )
    assert r.status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"action": "bogus", "targets": [{"host_type": "S", "protocol_server_id": 1}]},
        {"action": "favorite", "targets": []},
        {"action": "favorite", "targets": [{"host_type": "X", "protocol_server_id": 1}]},
        {"action": "favorite", "targets": [{"host_type": "S", "protocol_server_id": 0}]},
        {
            "action": "favorite",
            "targets": [{"host_type": "S", "protocol_server_id": 1, "extra": "x"}],
        },
        {
            "action": "favorite",
            "targets": [{"host_type": "S", "protocol_server_id": 1}] * 201,
        },
    ],
)
def test_results_toggle_actions_invalid_payload_returns_400(
    creds, cfg_no_tls, db_all_protocols, payload
):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    csrf = _csrf(c)
    r = _post_toggle(c, payload, csrf=csrf)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid payload"


def test_results_toggle_actions_invalid_json_returns_400(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    csrf = _csrf(c)
    r = c.post(
        "/api/results/actions/toggle",
        content="{not-json",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid payload"


@pytest.mark.parametrize(
    "action,host_type,table,column,expected",
    [
        ("favorite", "S", "host_user_flags", "favorite", 0),
        ("favorite", "F", "ftp_user_flags", "favorite", 1),
        ("favorite", "H", "http_user_flags", "favorite", 1),
        ("avoid", "S", "host_user_flags", "avoid", 1),
        ("avoid", "F", "ftp_user_flags", "avoid", 0),
        ("avoid", "H", "http_user_flags", "avoid", 1),
    ],
)
def test_results_toggle_single_row_per_protocol(
    creds, cfg_no_tls, db_all_protocols, action, host_type, table, column, expected
):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    csrf = _csrf(c)
    r = _post_toggle(
        c,
        {
            "action": action,
            "targets": [
                {
                    "host_type": host_type,
                    "protocol_server_id": 1,
                    "row_key": f"{host_type}:1",
                }
            ],
        },
        csrf=csrf,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["updated"] == 1
    assert payload["failed"] == 0
    assert _fetch_int(
        db_all_protocols,
        f"SELECT COALESCE({column}, 0) FROM {table} WHERE server_id = ?",
        (1,),
    ) == expected


def test_results_toggle_favorite_bulk_s_f_h(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    csrf = _csrf(c)
    r = _post_toggle(
        c,
        {
            "action": "favorite",
            "targets": [
                {"host_type": "S", "protocol_server_id": 1, "row_key": "S:1"},
                {"host_type": "F", "protocol_server_id": 1, "row_key": "F:1"},
                {"host_type": "H", "protocol_server_id": 1, "row_key": "H:1"},
            ],
        },
        csrf=csrf,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["updated"] == 3
    assert payload["failed"] == 0
    assert _fetch_int(
        db_all_protocols,
        "SELECT COALESCE(favorite, 0) FROM host_user_flags WHERE server_id = ?",
        (1,),
    ) == 0
    assert _fetch_int(
        db_all_protocols,
        "SELECT COALESCE(favorite, 0) FROM ftp_user_flags WHERE server_id = ?",
        (1,),
    ) == 1
    assert _fetch_int(
        db_all_protocols,
        "SELECT COALESCE(favorite, 0) FROM http_user_flags WHERE server_id = ?",
        (1,),
    ) == 1


def test_results_toggle_avoid_bulk_s_f_h(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    csrf = _csrf(c)
    r = _post_toggle(
        c,
        {
            "action": "avoid",
            "targets": [
                {"host_type": "S", "protocol_server_id": 1, "row_key": "S:1"},
                {"host_type": "F", "protocol_server_id": 1, "row_key": "F:1"},
                {"host_type": "H", "protocol_server_id": 1, "row_key": "H:1"},
            ],
        },
        csrf=csrf,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["updated"] == 3
    assert payload["failed"] == 0
    assert _fetch_int(
        db_all_protocols,
        "SELECT COALESCE(avoid, 0) FROM host_user_flags WHERE server_id = ?",
        (1,),
    ) == 1
    assert _fetch_int(
        db_all_protocols,
        "SELECT COALESCE(avoid, 0) FROM ftp_user_flags WHERE server_id = ?",
        (1,),
    ) == 0
    assert _fetch_int(
        db_all_protocols,
        "SELECT COALESCE(avoid, 0) FROM http_user_flags WHERE server_id = ?",
        (1,),
    ) == 1


def test_results_toggle_compromised_bulk_desktop_parity(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    csrf = _csrf(c)
    r = _post_toggle(
        c,
        {
            "action": "compromised",
            "targets": [
                {"host_type": "S", "protocol_server_id": 1, "row_key": "S:1"},
                {"host_type": "F", "protocol_server_id": 1, "row_key": "F:1"},
                {"host_type": "H", "protocol_server_id": 1, "row_key": "H:1"},
            ],
        },
        csrf=csrf,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["updated"] == 3
    assert payload["failed"] == 0
    assert _fetch_probe(db_all_protocols, "host_probe_cache", 1) == ("issue", 1)
    assert _fetch_probe(db_all_protocols, "ftp_probe_cache", 1) == ("clean", 0)
    assert _fetch_probe(db_all_protocols, "http_probe_cache", 1) == ("issue", 1)


def test_results_toggle_partial_success_for_missing_target(creds, cfg_no_tls, db_all_protocols):
    c = _logged_in_client_for_db(creds, cfg_no_tls, db_all_protocols)
    csrf = _csrf(c)
    r = _post_toggle(
        c,
        {
            "action": "favorite",
            "targets": [
                {"host_type": "S", "protocol_server_id": 1, "row_key": "S:1"},
                {"host_type": "S", "protocol_server_id": 99, "row_key": "S:99"},
            ],
        },
        csrf=csrf,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["updated"] == 1
    assert payload["failed"] == 1
    assert len(payload["results"]) == 2
    failures = [item for item in payload["results"] if item.get("ok") is False]
    assert len(failures) == 1
    assert "target not found" in failures[0].get("error", "")


def test_results_toggle_schema_fallback_per_target_error(creds, cfg_no_tls, tmp_path):
    db_path = tmp_path / "toggle_schema_missing_cols.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE smb_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL UNIQUE
        );
        INSERT INTO smb_servers (ip_address) VALUES ('10.9.0.1');

        CREATE TABLE host_user_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            favorite INTEGER DEFAULT 0
        );
        """
    )
    conn.commit()
    conn.close()

    c = _logged_in_client_for_db(creds, cfg_no_tls, db_path)
    csrf = _csrf(c)
    r = _post_toggle(
        c,
        {
            "action": "avoid",
            "targets": [{"host_type": "S", "protocol_server_id": 1, "row_key": "S:1"}],
        },
        csrf=csrf,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["updated"] == 0
    assert payload["failed"] == 1
    assert len(payload["results"]) == 1
    assert payload["results"][0]["ok"] is False
    assert "missing required table/column" in payload["results"][0]["error"]
