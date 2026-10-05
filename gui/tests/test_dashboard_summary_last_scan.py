"""Dashboard summary "Last Scan" spans SMB, FTP, and HTTP hosts."""

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
