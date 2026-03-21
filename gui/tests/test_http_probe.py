"""
Unit tests for gui/utils/http_probe_cache.py and gui/utils/http_probe_runner.py.

Pattern after test_ftp_probe.py. All tests use tmp_path fixtures or mocks;
no live HTTP server or real cache directory is touched.
"""

import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.utils.http_probe_cache import (
    save_http_probe_result,
    load_http_probe_result,
    clear_http_probe_result,
    get_http_cache_path,
    HTTP_CACHE_DIR,
)
from gui.utils.http_probe_runner import run_http_probe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_snapshot(ip: str = "1.2.3.4") -> dict:
    return {
        "ip_address": ip,
        "port": 80,
        "scheme": "http",
        "protocol": "http",
        "run_at": "2026-03-19T00:00:00+00:00",
        "limits": {"max_entries": 5000, "max_directories": 5000, "max_files": 5000, "timeout_seconds": 15},
        "shares": [
            {
                "share": "http_root",
                "root_files": ["index.html", "robots.txt"],
                "root_files_truncated": False,
                "directories": [
                    {
                        "name": "pub",
                        "subdirectories": ["incoming"],
                        "subdirectories_truncated": False,
                        "files": ["file1.bin"],
                        "files_truncated": False,
                    }
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
    monkeypatch.setattr("gui.utils.http_probe_cache.HTTP_CACHE_DIR", tmp_path)
    ip = "192.168.1.1"
    snapshot = _minimal_snapshot(ip)

    save_http_probe_result(ip, snapshot)
    loaded = load_http_probe_result(ip)

    assert loaded is not None
    assert loaded["ip_address"] == ip
    assert loaded["protocol"] == "http"
    assert loaded["shares"][0]["share"] == "http_root"


# ---------------------------------------------------------------------------
# Cache clear
# ---------------------------------------------------------------------------

def test_cache_clear(tmp_path, monkeypatch):
    monkeypatch.setattr("gui.utils.http_probe_cache.HTTP_CACHE_DIR", tmp_path)
    ip = "10.0.0.1"
    save_http_probe_result(ip, _minimal_snapshot(ip))

    assert load_http_probe_result(ip) is not None

    clear_http_probe_result(ip)

    assert load_http_probe_result(ip) is None


# ---------------------------------------------------------------------------
# IP sanitization — no path traversal in filename
# ---------------------------------------------------------------------------

def test_cache_ip_sanitization(tmp_path, monkeypatch):
    monkeypatch.setattr("gui.utils.http_probe_cache.HTTP_CACHE_DIR", tmp_path)
    ip = "1.2.3.4"
    save_http_probe_result(ip, _minimal_snapshot(ip))

    cache_file = tmp_path / "1.2.3.4.json"
    assert cache_file.exists()

    filename = cache_file.name
    assert ".." not in filename
    assert "/" not in filename
    assert "\\" not in filename


# ---------------------------------------------------------------------------
# run_http_probe — protocol and scheme fields
# ---------------------------------------------------------------------------

def test_snapshot_protocol_and_scheme_fields(tmp_path, monkeypatch):
    monkeypatch.setattr("gui.utils.http_probe_cache.HTTP_CACHE_DIR", tmp_path)

    with patch("gui.utils.http_probe_runner.try_http_request", return_value=(200, b"<body>", False, None)), \
         patch("gui.utils.http_probe_runner.validate_index_page", return_value=True), \
         patch("gui.utils.http_probe_runner._parse_dir_entries", return_value=([], [])):
        snapshot = run_http_probe("127.0.0.1", port=80, scheme="http")

    assert snapshot["protocol"] == "http"
    assert snapshot["scheme"] == "http"


# ---------------------------------------------------------------------------
# run_http_probe — root fetch failure recorded in errors
# ---------------------------------------------------------------------------

def test_root_fetch_failure_recorded_in_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("gui.utils.http_probe_cache.HTTP_CACHE_DIR", tmp_path)

    with patch("gui.utils.http_probe_runner.try_http_request", return_value=(0, b"", False, "connection refused")):
        snapshot = run_http_probe("10.0.0.99", port=80)

    assert len(snapshot["errors"]) > 0
    for err in snapshot["errors"]:
        assert isinstance(err, dict)
        assert "share" in err


# ---------------------------------------------------------------------------
# run_http_probe — errors are dicts, not plain strings (HTTP-specific)
# ---------------------------------------------------------------------------

def test_errors_are_dicts_not_strings(tmp_path, monkeypatch):
    monkeypatch.setattr("gui.utils.http_probe_cache.HTTP_CACHE_DIR", tmp_path)

    with patch("gui.utils.http_probe_runner.try_http_request", return_value=(0, b"", False, "timeout")):
        snapshot = run_http_probe("10.0.0.50", port=80)

    for err in snapshot["errors"]:
        assert isinstance(err, dict), f"Expected dict, got {type(err)}: {err!r}"


# ---------------------------------------------------------------------------
# run_http_probe — subdirectory and file limits respected
# ---------------------------------------------------------------------------

def test_directory_listing_limits_subdirs_and_files_independently(tmp_path, monkeypatch):
    monkeypatch.setattr("gui.utils.http_probe_cache.HTTP_CACHE_DIR", tmp_path)

    # Root parse returns one directory
    root_dirs = ["/pub/"]
    root_files = []

    # Subdir parse returns 3 dirs and 3 files (above both limits of 2)
    sub_dirs = ["/a/", "/b/", "/c/"]
    sub_files = ["/pub/f1.txt", "/pub/f2.txt", "/pub/f3.txt"]

    parse_side_effects = [
        (root_dirs, root_files),   # root "/"
        (sub_dirs, sub_files),     # subdir "/pub/"
    ]

    with patch("gui.utils.http_probe_runner.try_http_request", return_value=(200, b"<body>", False, None)), \
         patch("gui.utils.http_probe_runner.validate_index_page", return_value=True), \
         patch("gui.utils.http_probe_runner._parse_dir_entries", side_effect=parse_side_effects):
        snapshot = run_http_probe(
            "10.0.0.9",
            port=80,
            max_directories=2,
            max_files=2,
        )

    directory = snapshot["shares"][0]["directories"][0]
    assert len(directory["subdirectories"]) == 2
    assert directory["subdirectories_truncated"] is True
    assert len(directory["files"]) == 2
    assert directory["files_truncated"] is True
