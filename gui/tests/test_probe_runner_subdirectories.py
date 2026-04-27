"""Unit tests for SMB probe directory sampling behavior."""

from pathlib import Path
from unittest.mock import MagicMock
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.utils import probe_runner


def test_probe_share_lists_subdirectories_and_files_with_independent_limits(monkeypatch):
    fake_conn = MagicMock()
    fake_conn.login.return_value = None
    fake_conn.logoff.return_value = None

    monkeypatch.setattr(probe_runner, "_connect", lambda *_args, **_kwargs: fake_conn)

    def _fake_list_entries(_conn, _share, pattern):
        if pattern == "*":
            return [
                {"name": "pub", "is_directory": True},
                {"name": "root.txt", "is_directory": False},
            ]
        if pattern == "pub\\*":
            return [
                {"name": "incoming", "is_directory": True},
                {"name": "archive", "is_directory": True},
                {"name": "staging", "is_directory": True},
                {"name": "note1.txt", "is_directory": False},
                {"name": "note2.txt", "is_directory": False},
                {"name": "note3.txt", "is_directory": False},
            ]
        return []

    monkeypatch.setattr(probe_runner, "_list_entries", _fake_list_entries)

    result = probe_runner._probe_share(
        "10.1.1.5",
        "public",
        max_directories=1,
        max_files=2,
        timeout_seconds=5,
        max_depth=1,
        username="guest",
        password="",
    )

    directory = result["directories"][0]
    assert directory["subdirectories"] == ["incoming", "archive"]
    assert directory["subdirectories_truncated"] is True
    assert directory["files"] == ["note1.txt", "note2.txt"]
    assert directory["files_truncated"] is True


def test_probe_share_depth_three_adds_nested_relative_paths(monkeypatch):
    fake_conn = MagicMock()
    fake_conn.login.return_value = None
    fake_conn.logoff.return_value = None
    monkeypatch.setattr(probe_runner, "_connect", lambda *_args, **_kwargs: fake_conn)

    def _fake_list_entries(_conn, _share, pattern):
        if pattern == "*":
            return [{"name": "pub", "is_directory": True}]
        if pattern == "pub\\*":
            return [
                {"name": "incoming", "is_directory": True},
                {"name": "root.txt", "is_directory": False},
            ]
        if pattern == "pub\\incoming\\*":
            return [
                {"name": "archive", "is_directory": True},
                {"name": "deep.txt", "is_directory": False},
            ]
        if pattern == "pub\\incoming\\archive\\*":
            return [{"name": "leaf.txt", "is_directory": False}]
        return []

    monkeypatch.setattr(probe_runner, "_list_entries", _fake_list_entries)

    result = probe_runner._probe_share(
        "10.1.1.5",
        "public",
        max_directories=2,
        max_files=5,
        timeout_seconds=5,
        max_depth=3,
        username="guest",
        password="",
    )

    directory = result["directories"][0]
    assert "incoming" in directory["subdirectories"]
    assert "incoming/archive" in directory["subdirectories"]
    assert "root.txt" in directory["files"]
    assert "incoming/deep.txt" in directory["files"]
    assert "incoming/archive/leaf.txt" in directory["files"]
