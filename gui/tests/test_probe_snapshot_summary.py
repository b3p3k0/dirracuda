"""Unit tests for probe snapshot summary helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.utils.probe_snapshot_summary import (
    LOOSE_FILES_DISPLAY_TOKEN,
    summarize_probe_snapshot,
)


def test_summary_adds_loose_files_marker_for_root_files_only():
    snapshot = {
        "shares": [
            {
                "share": "ftp_root",
                "directories": [],
                "root_files": ["readme.txt", "dump.sql"],
            }
        ]
    }

    summary = summarize_probe_snapshot(snapshot)

    assert summary["directory_names"] == []
    assert summary["display_entries"] == [LOOSE_FILES_DISPLAY_TOKEN]
    assert summary["has_loose_root_files"] is True
    assert summary["total_file_count"] == 2


def test_summary_preserves_directory_order_then_appends_marker():
    snapshot = {
        "shares": [
            {
                "share": "http_root",
                "directories": [{"name": "pub"}, {"name": "incoming"}],
                "root_files": ["index.html"],
            }
        ]
    }

    summary = summarize_probe_snapshot(snapshot)

    assert summary["directory_names"] == ["pub", "incoming"]
    assert summary["display_entries"] == ["pub", "incoming", LOOSE_FILES_DISPLAY_TOKEN]


def test_summary_omits_marker_when_no_root_files():
    snapshot = {
        "shares": [
            {
                "share": "http_root",
                "directories": [{"name": "pub", "files": ["a.txt", "b.txt"]}],
                "root_files": [],
            }
        ]
    }

    summary = summarize_probe_snapshot(snapshot)

    assert summary["display_entries"] == ["pub"]
    assert summary["has_loose_root_files"] is False
    assert summary["root_file_count"] == 0
    assert summary["nested_file_count"] == 2
