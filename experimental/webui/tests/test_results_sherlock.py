"""Read-only Sherlock display tests for the Web UI results API (C6)."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

import experimental.webui.sherlock_view as sherlock_view
from experimental.webui.app import create_app
from experimental.webui.auth import set_password
from experimental.webui.config import TLSConfig, WebUIConfig

_USERNAME = "sherlockuser"
_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def creds(tmp_path):
    p = tmp_path / "creds.json"
    set_password(_USERNAME, _PASSWORD, path=p)
    return p


@pytest.fixture
def cfg_no_tls():
    return WebUIConfig(tls=TLSConfig(enabled=False))


def _base_schema(conn):
    conn.executescript(
        """
        CREATE TABLE smb_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL UNIQUE,
            country TEXT, country_code TEXT,
            status TEXT DEFAULT 'active', last_seen TEXT
        );
        CREATE TABLE host_probe_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            status TEXT, extracted INTEGER DEFAULT 0,
            latest_snapshot_id INTEGER
        );
        """
    )


def _sherlock_schema(conn):
    conn.executescript(
        """
        CREATE TABLE sherlock_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_type TEXT NOT NULL,
            protocol_server_id INTEGER NOT NULL,
            ip_address TEXT, port INTEGER,
            snapshot_id INTEGER,
            highest_severity TEXT,
            total_hit_count INTEGER DEFAULT 0,
            detail_count INTEGER DEFAULT 0,
            truncated INTEGER DEFAULT 0,
            scanned_at TEXT, updated_at TEXT
        );
        CREATE TABLE sherlock_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            result_id INTEGER NOT NULL,
            severity TEXT, category TEXT, label TEXT,
            pattern TEXT, display_path TEXT, created_at TEXT
        );
        """
    )


@pytest.fixture
def db_with_sherlock(tmp_path):
    """SMB hosts covering fresh / stale / zero-hit / malformed / unscanned."""
    db_path = tmp_path / "sherlock.db"
    conn = sqlite3.connect(str(db_path))
    _base_schema(conn)
    _sherlock_schema(conn)
    conn.executescript(
        """
        INSERT INTO smb_servers (ip_address, country, country_code, status, last_seen) VALUES
            ('10.0.0.1', 'US', 'US', 'active', '2026-05-10T14:25:00'),  -- fresh finding
            ('10.0.0.2', 'US', 'US', 'active', '2026-05-10T14:24:00'),  -- stale
            ('10.0.0.3', 'US', 'US', 'active', '2026-05-10T14:23:00'),  -- fresh zero-hit
            ('10.0.0.4', 'US', 'US', 'active', '2026-05-10T14:22:00'),  -- malformed severity
            ('10.0.0.5', 'US', 'US', 'active', '2026-05-10T14:21:00');  -- unscanned

        INSERT INTO host_probe_cache (server_id, status, latest_snapshot_id) VALUES
            (1, 'clean', 5),
            (2, 'clean', 9),
            (3, 'clean', 7),
            (4, 'clean', 8),
            (5, 'clean', 1);

        INSERT INTO sherlock_results
            (host_type, protocol_server_id, snapshot_id, highest_severity, total_hit_count, detail_count, truncated)
        VALUES
            ('S', 1, 5, 'high', 3, 2, 1),   -- fresh, recognized, >0  -> badge
            ('S', 2, 4, 'high', 2, 1, 0),   -- snapshot 4 != latest 9 -> stale
            ('S', 3, 7, 'low', 0, 0, 0),    -- fresh but zero hits    -> blank
            ('S', 4, 8, 'bogus', 2, 0, 0);  -- malformed severity     -> blank

        INSERT INTO sherlock_hits (result_id, severity, category, label, pattern, display_path) VALUES
            (1, 'high', 'Credentials', 'Password files', '*password*', 'Public/passwords.txt'),
            (1, 'med', 'Finance', 'Payroll', '*payroll*', 'Public/payroll.xlsx'),
            (2, 'high', 'Credentials', 'Secrets', '*secret*', 'Old/secret.txt');
        """
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db_no_sherlock(tmp_path):
    """SMB host present but Sherlock tables absent (legacy DB)."""
    db_path = tmp_path / "no_sherlock.db"
    conn = sqlite3.connect(str(db_path))
    _base_schema(conn)
    conn.execute(
        "INSERT INTO smb_servers (ip_address, country, country_code, status, last_seen) "
        "VALUES ('10.0.0.9', 'US', 'US', 'active', '2026-05-10T14:00:00')"
    )
    conn.commit()
    conn.close()
    return db_path


def _client(creds, cfg_no_tls, db_path):
    app = create_app(cfg=cfg_no_tls, creds_path=creds, db_path=db_path)
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    return app, c


def _rows_by_key(payload):
    return {row["row_key"]: row for row in payload["results"]}


# --- list badge: quiet contract ---

def test_results_list_badge_only_for_fresh_nonzero_recognized(creds, cfg_no_tls, db_with_sherlock):
    _, c = _client(creds, cfg_no_tls, db_with_sherlock)
    rows = _rows_by_key(c.get("/api/results/all").json())

    assert rows["S:1"]["sherlock_risk"] == {"text": "HIGH 3", "color": "#ff4d4d"}
    # stale / zero-hit / malformed / unscanned all stay blank
    for key in ("S:2", "S:3", "S:4", "S:5"):
        assert rows[key]["sherlock_risk"] is None


def test_results_list_no_sherlock_tables_degrades_to_blank(creds, cfg_no_tls, db_no_sherlock):
    _, c = _client(creds, cfg_no_tls, db_no_sherlock)
    resp = c.get("/api/results/all")
    assert resp.status_code == 200
    rows = _rows_by_key(resp.json())
    assert rows["S:1"]["sherlock_risk"] is None


def test_results_detail_partial_hits_table_degrades_to_none(tmp_path):
    db_path = tmp_path / "partial_hits.db"
    conn = sqlite3.connect(str(db_path))
    _base_schema(conn)
    conn.executescript(
        """
        CREATE TABLE sherlock_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_type TEXT NOT NULL,
            protocol_server_id INTEGER NOT NULL,
            ip_address TEXT, port INTEGER,
            snapshot_id INTEGER,
            highest_severity TEXT,
            total_hit_count INTEGER DEFAULT 0,
            detail_count INTEGER DEFAULT 0,
            truncated INTEGER DEFAULT 0,
            scanned_at TEXT, updated_at TEXT
        );
        CREATE TABLE sherlock_hits (
            result_id INTEGER NOT NULL,
            severity TEXT, category TEXT, label TEXT,
            pattern TEXT, display_path TEXT, created_at TEXT
        );
        INSERT INTO smb_servers (ip_address, status, last_seen)
            VALUES ('10.0.0.10', 'active', '2026-05-10T14:00:00');
        INSERT INTO host_probe_cache (server_id, status, latest_snapshot_id)
            VALUES (1, 'clean', 5);
        INSERT INTO sherlock_results
            (host_type, protocol_server_id, snapshot_id, highest_severity, total_hit_count)
            VALUES ('S', 1, 5, 'high', 1);
        """
    )
    conn.commit()
    conn.close()

    assert sherlock_view.get_sherlock_detail(db_path, "S", 1) is None


# --- detail: explanatory ---

def test_results_detail_fresh_finding_has_capped_hits(creds, cfg_no_tls, db_with_sherlock):
    _, c = _client(creds, cfg_no_tls, db_with_sherlock)
    payload = c.get("/api/results/details?host_type=S&protocol_server_id=1").json()
    sher = payload["sherlock"]
    assert sher["stale"] is False
    assert sher["text"] == "HIGH 3"
    assert sher["count"] == 3
    assert sher["truncated"] is True
    assert len(sher["hits"]) == 2
    assert sher["hits"][0]["display_path"] == "Public/passwords.txt"
    assert sher["color"] == "#ff4d4d"


def test_results_detail_stale_flagged(creds, cfg_no_tls, db_with_sherlock):
    _, c = _client(creds, cfg_no_tls, db_with_sherlock)
    sher = c.get("/api/results/details?host_type=S&protocol_server_id=2").json()["sherlock"]
    assert sher["stale"] is True


def test_results_detail_zero_hits(creds, cfg_no_tls, db_with_sherlock):
    _, c = _client(creds, cfg_no_tls, db_with_sherlock)
    sher = c.get("/api/results/details?host_type=S&protocol_server_id=3").json()["sherlock"]
    assert sher["stale"] is False
    assert sher["count"] == 0


def test_results_detail_unscanned_is_none(creds, cfg_no_tls, db_with_sherlock):
    _, c = _client(creds, cfg_no_tls, db_with_sherlock)
    sher = c.get("/api/results/details?host_type=S&protocol_server_id=5").json()["sherlock"]
    assert sher is None


# --- no-mutation / no action route guard ---

def test_no_sherlock_route_exists(creds, cfg_no_tls, db_with_sherlock):
    app, _ = _client(creds, cfg_no_tls, db_with_sherlock)
    for route in app.routes:
        assert "sherlock" not in getattr(route, "path", "").lower()


def test_no_sherlock_action_endpoint_post_rejected(creds, cfg_no_tls, db_with_sherlock):
    _, c = _client(creds, cfg_no_tls, db_with_sherlock)
    # There is no Sherlock scan/edit endpoint; such a POST must not be routed.
    r = c.post("/api/results/actions/sherlock", json={"targets": ["S:1"]})
    assert r.status_code in (404, 405)


# --- colors sourced from the sherlock.json shard (injectable, no real shard) ---

def test_badge_colors_from_injected_shard(db_with_sherlock):
    class _FakeStore:
        def load_module_prefs(self, name):
            assert name == "sherlock"
            return {"colors": {"high": "#010203", "med": "#0a0b0c", "low": "#111213"}}

    badge_map = sherlock_view.get_sherlock_badge_map(db_with_sherlock, config_store=_FakeStore())
    assert badge_map["S:1"] == {"text": "HIGH 3", "color": "#010203"}


def test_badge_colors_default_on_shard_failure(db_with_sherlock):
    class _BadStore:
        def load_module_prefs(self, name):
            raise RuntimeError("shard unreadable")

    badge_map = sherlock_view.get_sherlock_badge_map(db_with_sherlock, config_store=_BadStore())
    assert badge_map["S:1"]["color"] == "#ff4d4d"
