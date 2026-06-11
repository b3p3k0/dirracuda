"""Focused regression tests for SMBNavigator.download_file basename handling.

Card C8 - SMB Windows Basename Compatibility. norm_path is always a Windows-form
SMB path (backslash separators); the non-structured branch must extract the
basename with Windows semantics and reject empty/root-only basenames before any
filesystem mutation or SMB download.
"""

import pytest

from shared.smb_browser import SMBNavigator


class _StubConn:
    """Minimal impacket-connection stub recording the remote path passed to getFile."""

    def __init__(self, payload: bytes = b"contents"):
        self.payload = payload
        self.getfile_calls = []

    def setTimeout(self, _timeout):
        pass

    def getFile(self, share, remote_path, callback):
        self.getfile_calls.append((share, remote_path))
        callback(self.payload)


def _navigator(payload: bytes = b"contents"):
    nav = SMBNavigator()
    conn = _StubConn(payload)
    nav._conn = conn
    nav._share = "share"
    return nav, conn


def test_non_structured_uses_windows_basename(tmp_path):
    nav, conn = _navigator(b"hello")
    result = nav.download_file("\\folder\\file.txt", tmp_path, preserve_structure=False)

    assert result.saved_path.name == "file.txt"
    assert result.saved_path.parent == tmp_path
    assert result.saved_path.read_bytes() == b"hello"
    # Remote read still receives the original Windows-form path.
    assert conn.getfile_calls == [("share", "\\folder\\file.txt")]


def test_non_structured_deep_path_collapses(tmp_path):
    nav, _ = _navigator()
    result = nav.download_file("\\a\\b\\c.bin", tmp_path, preserve_structure=False)

    assert result.saved_path.name == "c.bin"
    assert result.saved_path.parent == tmp_path


def test_non_structured_rejects_root_only(tmp_path):
    nav, conn = _navigator()
    with pytest.raises(ValueError):
        nav.download_file("\\", tmp_path, preserve_structure=False)

    assert conn.getfile_calls == []
    assert list(tmp_path.iterdir()) == []


def test_non_structured_rejects_empty(tmp_path):
    nav, conn = _navigator()
    with pytest.raises(ValueError):
        nav.download_file("", tmp_path, preserve_structure=False)

    assert conn.getfile_calls == []


def test_non_structured_rejects_dotdot_basename(tmp_path):
    nav, conn = _navigator()
    with pytest.raises(ValueError):
        nav.download_file("\\folder\\..", tmp_path, preserve_structure=False)

    assert conn.getfile_calls == []


def test_rejection_before_filesystem_mutation(tmp_path):
    nav, conn = _navigator()
    dest_dir = tmp_path / "new"
    with pytest.raises(ValueError):
        nav.download_file("\\", dest_dir, preserve_structure=False)

    assert not dest_dir.exists()
    assert conn.getfile_calls == []


def test_structured_download_unchanged(tmp_path):
    nav, _ = _navigator(b"data")
    result = nav.download_file("\\a\\b\\c.bin", tmp_path, preserve_structure=True)

    assert result.saved_path == tmp_path / "a" / "b" / "c.bin"
    assert result.saved_path.read_bytes() == b"data"
