"""
Unit tests for gui/utils/ftp_probe_cache.py and gui/utils/ftp_probe_runner.py.

All tests use tmp_path fixtures or mocks; no live FTP server or real cache
directory is touched.
"""

import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.utils.ftp_probe_cache import (
    save_ftp_probe_result,
    load_ftp_probe_result,
    clear_ftp_probe_result,
    get_ftp_cache_path,
    FTP_CACHE_DIR,
)
from gui.utils.ftp_probe_runner import run_ftp_probe
from gui.utils.probe_patterns import find_indicator_hits
from shared.smb_browser import Entry, ListResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_snapshot(ip: str = "1.2.3.4") -> dict:
    return {
        "ip_address": ip,
        "port": 21,
        "protocol": "ftp",
        "run_at": "2026-03-16T00:00:00+00:00",
        "limits": {"max_entries": 5000},
        "shares": [
            {
                "share": "ftp_root",
                "root_files": ["readme.txt", "data.csv"],
                "root_files_truncated": False,
                "directories": [
                    {"name": "pub", "files": ["file1.bin"], "files_truncated": False}
                ],
                "directories_truncated": False,
            }
        ],
        "errors": [],
    }


# ---------------------------------------------------------------------------
# Cache round-trip
# ---------------------------------------------------------------------------

def test_cache_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gui.utils.ftp_probe_cache.FTP_CACHE_DIR", tmp_path
    )
    ip = "192.168.1.1"
    snapshot = _minimal_snapshot(ip)

    save_ftp_probe_result(ip, snapshot)
    loaded = load_ftp_probe_result(ip)

    assert loaded is not None
    assert loaded["ip_address"] == ip
    assert loaded["protocol"] == "ftp"
    assert loaded["shares"][0]["share"] == "ftp_root"


# ---------------------------------------------------------------------------
# Cache clear
# ---------------------------------------------------------------------------

def test_cache_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gui.utils.ftp_probe_cache.FTP_CACHE_DIR", tmp_path
    )
    ip = "10.0.0.1"
    save_ftp_probe_result(ip, _minimal_snapshot(ip))

    # Confirm it exists before clear
    assert load_ftp_probe_result(ip) is not None

    clear_ftp_probe_result(ip)

    assert load_ftp_probe_result(ip) is None


# ---------------------------------------------------------------------------
# IP sanitization — no path traversal in filename
# ---------------------------------------------------------------------------

def test_cache_ip_sanitization(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gui.utils.ftp_probe_cache.FTP_CACHE_DIR", tmp_path
    )
    ip = "1.2.3.4"
    snapshot = _minimal_snapshot(ip)
    save_ftp_probe_result(ip, snapshot)

    cache_file = tmp_path / "1.2.3.4.json"
    assert cache_file.exists()

    # No path traversal characters in filename
    filename = cache_file.name
    assert ".." not in filename
    assert "/" not in filename
    assert "\\" not in filename


# ---------------------------------------------------------------------------
# run_ftp_probe — snapshot protocol field
# ---------------------------------------------------------------------------

def test_snapshot_protocol_field(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gui.utils.ftp_probe_cache.FTP_CACHE_DIR", tmp_path
    )

    root_entries = [
        Entry(name="file1.txt", is_dir=False, size=100, modified_time=None),
        Entry(name="docs", is_dir=True, size=0, modified_time=None),
        Entry(name="file2.txt", is_dir=False, size=200, modified_time=None),
    ]
    root_result = ListResult(entries=root_entries, truncated=False)
    subdir_result = ListResult(entries=[], truncated=False)

    mock_nav = MagicMock()
    mock_nav.connect.return_value = None
    mock_nav.list_dir.side_effect = [root_result, subdir_result]
    mock_nav.disconnect.return_value = None

    with patch("gui.utils.ftp_probe_runner.FtpNavigator", return_value=mock_nav):
        snapshot = run_ftp_probe("127.0.0.1", port=21)

    assert snapshot["protocol"] == "ftp"


# ---------------------------------------------------------------------------
# run_ftp_probe — share name is "ftp_root"
# ---------------------------------------------------------------------------

def test_snapshot_share_name(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gui.utils.ftp_probe_cache.FTP_CACHE_DIR", tmp_path
    )

    root_entries = [
        Entry(name="readme.txt", is_dir=False, size=512, modified_time=None),
    ]
    root_result = ListResult(entries=root_entries, truncated=False)

    mock_nav = MagicMock()
    mock_nav.connect.return_value = None
    mock_nav.list_dir.return_value = root_result
    mock_nav.disconnect.return_value = None

    with patch("gui.utils.ftp_probe_runner.FtpNavigator", return_value=mock_nav):
        snapshot = run_ftp_probe("10.0.0.1", port=21)

    assert len(snapshot["shares"]) == 1
    assert snapshot["shares"][0]["share"] == "ftp_root"


# ---------------------------------------------------------------------------
# probe_patterns compatibility — empty patterns → not suspicious
# ---------------------------------------------------------------------------

def test_snapshot_probe_patterns_compatible():
    snapshot = _minimal_snapshot()

    result = find_indicator_hits(snapshot, [])

    assert result["is_suspicious"] is False
    assert result["matches"] == []
