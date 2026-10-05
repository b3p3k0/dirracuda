"""Dashboard summary "Last Scan" and host counts span SMB, FTP, and HTTP."""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gui.utils import last_scan_window
from gui.utils.database_access import DatabaseReader


@pytest.fixture
def window(monkeypatch):
    state = {"value": None}
    monkeypatch.setattr(
        last_scan_window, "load_last_scan_window", lambda _db_path: state["value"]
    )
    return state


@pytest.fixture
def db_path(tmp_path, window):
    path = tmp_path / "summary.db"
    conn = sqlite3.connect(path)
    conn.executescript((ROOT / "tools" / "db_schema.sql").read_text())
    conn.close()
    return path


def _insert(db_path, table, ip, last_seen):
    conn = sqlite3.connect(db_path)
    conn.execute(
        f"INSERT INTO {table} (ip_address, last_seen) VALUES (?, ?)", (ip, last_seen)
    )
    conn.commit()
    conn.close()


def _last_scan(db_path):
    return DatabaseReader(db_path=str(db_path), cache_duration=0).get_dashboard_summary()["last_scan"]


def test_empty_db_reports_never(db_path):
    assert _last_scan(db_path) == "Never"


@pytest.mark.parametrize("table", ["ftp_servers", "http_servers"])
def test_non_smb_only_host_sets_last_scan(db_path, table):
    _insert(db_path, table, "198.51.100.7", "2026-10-05 09:30:00")
    assert _last_scan(db_path) == "2026-10-05 09:30:00"


def test_latest_timestamp_wins_across_protocols(db_path):
    _insert(db_path, "smb_servers", "198.51.100.1", "2026-10-01 08:00:00")
    _insert(db_path, "ftp_servers", "198.51.100.2", "2026-10-03T08:00:00")
    _insert(db_path, "http_servers", "198.51.100.3", "2026-10-05 08:00:00")
    assert _last_scan(db_path) == "2026-10-05 08:00:00"


def _window(end, *, cancelled=False):
    return {
        "start": datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
        "end": end,
        "providers": ["reddit"],
        "cancelled": cancelled,
    }


def test_zero_host_run_window_sets_last_scan(db_path, window):
    window["value"] = _window(datetime(2026, 10, 5, 9, 45, 7, tzinfo=timezone.utc))
    assert _last_scan(db_path) == "2026-10-05 09:45:07"


def test_window_later_than_hosts_wins(db_path, window):
    _insert(db_path, "smb_servers", "198.51.100.1", "2026-10-05 08:00:00")
    window["value"] = _window(datetime(2026, 10, 5, 9, 45, tzinfo=timezone.utc))
    assert _last_scan(db_path) == "2026-10-05 09:45:00"


def test_host_later_than_window_wins(db_path, window):
    _insert(db_path, "http_servers", "198.51.100.3", "2026-10-05 10:00:00")
    window["value"] = _window(datetime(2026, 10, 5, 9, 45, tzinfo=timezone.utc))
    assert _last_scan(db_path) == "2026-10-05 10:00:00"


def test_cancelled_window_is_ignored(db_path, window):
    _insert(db_path, "ftp_servers", "198.51.100.2", "2026-10-05 08:00:00")
    window["value"] = _window(
        datetime(2026, 10, 5, 9, 45, tzinfo=timezone.utc), cancelled=True
    )
    assert _last_scan(db_path) == "2026-10-05 08:00:00"


def _counts(db_path):
    summary = DatabaseReader(db_path=str(db_path), cache_duration=0).get_dashboard_summary()
    return summary["protocol_host_counts"]


def test_protocol_host_counts(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        INSERT INTO smb_servers (id, ip_address) VALUES (1, '198.51.100.1'), (2, '198.51.100.2');
        INSERT INTO smb_servers (id, ip_address, status) VALUES (3, '198.51.100.3', 'inactive');
        INSERT INTO share_access (server_id, session_id, share_name, accessible) VALUES
            (1, 0, 'a', 1), (1, 0, 'b', 1), (2, 0, 'c', 0), (3, 0, 'd', 1);
        INSERT INTO ftp_servers (id, ip_address) VALUES (1, '198.51.100.4'), (2, '198.51.100.5');
        INSERT INTO ftp_access (server_id, accessible) VALUES (1, 1), (1, 0), (2, 0), (2, 1);
        INSERT INTO http_servers (id, ip_address) VALUES (1, '198.51.100.6');
        INSERT INTO http_access (server_id, accessible) VALUES (1, 1);
    """)
    conn.commit()
    conn.close()
    # FTP host 1 lost access on its latest check; host 2 gained it.
    assert _counts(db_path) == {
        "smb": {"hosts": 2, "accessible": 1},
        "ftp": {"hosts": 2, "accessible": 1},
        "http": {"hosts": 1, "accessible": 1},
    }


def test_missing_protocol_tables_count_as_zero(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript("DROP TABLE http_access; DROP TABLE http_servers;")
    conn.close()
    assert _counts(db_path)["http"] == {"hosts": 0, "accessible": 0}
