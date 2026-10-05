"""Dashboard summary "Last Scan" spans SMB, FTP, and HTTP hosts."""

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gui.utils.database_access import DatabaseReader


@pytest.fixture
def db_path(tmp_path):
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
