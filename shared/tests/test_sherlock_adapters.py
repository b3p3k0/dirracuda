"""Unit tests for the pure Sherlock path-entry adapters."""

import sqlite3

from shared.sherlock import path_entries_from_rows, path_entries_from_snapshot


def _paths(entries):
    return [e.display_path for e in entries]


# --- path_entries_from_rows (normalized probe_snapshot_entries shape) ---


def test_rows_join_share_and_path():
    rows = [
        {"share_name": "DOCS", "entry_kind": "file", "path": "reports/q1.xlsx", "parent_path": "reports"},
    ]
    entries = path_entries_from_rows(rows)
    assert _paths(entries) == ["DOCS/reports/q1.xlsx"]
    assert entries[0].container == "DOCS"
    assert entries[0].segments == ("DOCS", "reports", "q1.xlsx")


def test_rows_no_duplicated_directory_prefix():
    # path already includes parent; parent_path must NOT be prepended.
    rows = [
        {"share_name": "S", "entry_kind": "file", "path": "dir/sub/file.txt", "parent_path": "dir/sub"},
    ]
    entries = path_entries_from_rows(rows)
    assert _paths(entries) == ["S/dir/sub/file.txt"]


def test_rows_parent_path_fallback_when_path_blank():
    rows = [
        {"share_name": "S", "entry_kind": "directory", "path": "", "parent_path": "deep/dir"},
    ]
    entries = path_entries_from_rows(rows)
    assert _paths(entries) == ["S/deep/dir"]


def test_rows_skip_when_no_path_or_parent():
    rows = [{"share_name": "S", "entry_kind": "directory", "path": "", "parent_path": ""}]
    assert path_entries_from_rows(rows) == []


def test_rows_support_sqlite_row():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT 'DOCS' AS share_name, 'a/b.txt' AS path, 'a' AS parent_path"
    ).fetchone()
    entries = path_entries_from_rows([row])
    assert _paths(entries) == ["DOCS/a/b.txt"]
    conn.close()


def test_rows_backslash_normalized():
    rows = [{"share_name": "S", "path": "dir\\file.txt", "parent_path": ""}]
    assert _paths(path_entries_from_rows(rows)) == ["S/dir/file.txt"]


# --- path_entries_from_snapshot (raw snapshot dict shape) ---


def test_snapshot_nested_files_joined_under_directory():
    snapshot = {
        "shares": [
            {
                "share": "FILES",
                "root_files": ["readme.txt"],
                "directories": [
                    {
                        "name": "reports",
                        "subdirectories": ["q1"],
                        "files": ["q1/budget.xlsx"],
                    }
                ],
            }
        ]
    }
    paths = _paths(path_entries_from_snapshot(snapshot))
    assert "FILES/readme.txt" in paths
    assert "FILES/reports" in paths
    assert "FILES/reports/q1" in paths
    # nested file is share + dir_name + relative file, NOT share + relative file
    assert "FILES/reports/q1/budget.xlsx" in paths
    assert "FILES/q1/budget.xlsx" not in paths


def test_snapshot_share_name_key_styles():
    for key in ("share", "share_name", "name"):
        snapshot = {"shares": [{key: "MY", "root_files": ["a.txt"]}]}
        entries = path_entries_from_snapshot(snapshot)
        assert entries[0].container == "MY"
        assert entries[0].display_path == "MY/a.txt"


def test_snapshot_directory_as_bare_string():
    snapshot = {"shares": [{"share": "S", "directories": ["plaindir"]}]}
    assert _paths(path_entries_from_snapshot(snapshot)) == ["S/plaindir"]


def test_snapshot_directory_path_and_directory_keys():
    snapshot = {
        "shares": [
            {"share": "S", "directories": [{"path": "bypath"}, {"directory": "bydir"}]}
        ]
    }
    paths = _paths(path_entries_from_snapshot(snapshot))
    assert "S/bypath" in paths
    assert "S/bydir" in paths


def test_snapshot_tolerates_malformed():
    snapshot = {
        "shares": [
            "not-a-dict",
            {"share": "S", "root_files": [None, ""], "directories": [123, {"name": ""}]},
        ]
    }
    # Only valid entries survive; no exception.
    assert path_entries_from_snapshot(snapshot) == []


def test_snapshot_non_mapping_returns_empty():
    assert path_entries_from_snapshot(None) == []
    assert path_entries_from_snapshot([]) == []
