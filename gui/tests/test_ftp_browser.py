"""
Unit tests for shared.ftp_browser.FtpNavigator.

All tests mock ftplib.FTP so no live FTP server is required.
"""

import sys
import ftplib
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.ftp_browser import FtpNavigator, FtpCancelledError, FtpFileTooLargeError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_nav(**kwargs) -> FtpNavigator:
    """Return a FtpNavigator with a pre-wired mock FTP connection."""
    defaults = dict(
        connect_timeout=5.0,
        request_timeout=5.0,
        max_entries=100,
        max_depth=12,
        max_path_length=1024,
        max_file_bytes=26_214_400,
    )
    defaults.update(kwargs)
    nav = FtpNavigator(**defaults)
    mock_ftp = MagicMock(spec=ftplib.FTP)
    mock_ftp.voidcmd.return_value = "200 OK"
    nav._ftp = mock_ftp
    nav._host = "127.0.0.1"
    nav._port = 21
    return nav


# ---------------------------------------------------------------------------
# MLSD happy path
# ---------------------------------------------------------------------------

def test_list_dir_mlsd_success():
    nav = _make_nav()
    nav._ftp.mlsd.return_value = iter([
        ("file1.txt", {"type": "file", "size": "1234", "modify": "20240101120000"}),
        ("subdir", {"type": "dir", "modify": "20240101120000"}),
        (".", {"type": "cdir"}),
    ])

    result = nav.list_dir("/")

    assert len(result.entries) == 2
    names = {e.name for e in result.entries}
    assert names == {"file1.txt", "subdir"}
    file_entry = next(e for e in result.entries if e.name == "file1.txt")
    assert file_entry.is_dir is False
    assert file_entry.size == 1234
    dir_entry = next(e for e in result.entries if e.name == "subdir")
    assert dir_entry.is_dir is True
    assert dir_entry.size == 0
    assert result.truncated is False


# ---------------------------------------------------------------------------
# MLSD fallback to LIST on error_perm
# ---------------------------------------------------------------------------

def test_list_dir_mlsd_fallback_to_list():
    nav = _make_nav()
    nav._ftp.mlsd.side_effect = ftplib.error_perm("500 Unknown command")

    unix_line = "-rw-r--r-- 1 user group 512 Jan  1 12:00 readme.txt"

    def fake_retrlines(cmd, callback):
        callback(unix_line)

    nav._ftp.retrlines.side_effect = fake_retrlines

    result = nav.list_dir("/")

    assert len(result.entries) == 1
    assert result.entries[0].name == "readme.txt"
    assert result.entries[0].is_dir is False
    assert result.entries[0].size == 512


# ---------------------------------------------------------------------------
# Unix LIST line parser
# ---------------------------------------------------------------------------

def test_list_dir_unix_format():
    lines = [
        "-rw-r--r-- 1 user group 4096 Mar 15 09:30 document.pdf",
        "drwxr-xr-x 2 user group    0 Jan  1 00:00 backups",
        "-rwxr-xr-x 1 root root  8192 Dec 31 2023 script.sh",
    ]
    for line in lines:
        entry = FtpNavigator._parse_list_line(line)
        assert entry is not None, f"Failed to parse: {line}"

    entry_pdf = FtpNavigator._parse_list_line(lines[0])
    assert entry_pdf.name == "document.pdf"
    assert entry_pdf.is_dir is False
    assert entry_pdf.size == 4096

    entry_dir = FtpNavigator._parse_list_line(lines[1])
    assert entry_dir.name == "backups"
    assert entry_dir.is_dir is True
    assert entry_dir.size == 0

    entry_script = FtpNavigator._parse_list_line(lines[2])
    assert entry_script.name == "script.sh"
    assert entry_script.is_dir is False
    assert entry_script.size == 8192
    assert entry_script.modified_time is not None


# ---------------------------------------------------------------------------
# DOS/Windows LIST line parser
# ---------------------------------------------------------------------------

def test_list_dir_dos_format():
    lines = [
        "01-15-2024  10:30AM               12345 report.docx",
        "02-28-2024  03:45PM       <DIR>          Archive",
        "12-01-2023  09:00AM                   0 empty.txt",
    ]
    for line in lines:
        entry = FtpNavigator._parse_list_line(line)
        assert entry is not None, f"Failed to parse DOS line: {line}"

    entry_file = FtpNavigator._parse_list_line(lines[0])
    assert entry_file.name == "report.docx"
    assert entry_file.is_dir is False
    assert entry_file.size == 12345

    entry_dir = FtpNavigator._parse_list_line(lines[1])
    assert entry_dir.name == "Archive"
    assert entry_dir.is_dir is True
    assert entry_dir.size == 0

    entry_empty = FtpNavigator._parse_list_line(lines[2])
    assert entry_empty.name == "empty.txt"
    assert entry_empty.size == 0


# ---------------------------------------------------------------------------
# max_entries truncation
# ---------------------------------------------------------------------------

def test_list_dir_truncation():
    max_entries = 3
    nav = _make_nav(max_entries=max_entries)

    # MLSD returns 4 entries (N+1 where N = max_entries)
    nav._ftp.mlsd.return_value = iter([
        (f"file{i}.txt", {"type": "file", "size": str(i * 100)})
        for i in range(max_entries + 1)
    ])

    result = nav.list_dir("/")

    assert len(result.entries) == max_entries
    assert result.truncated is True


# ---------------------------------------------------------------------------
# max_depth enforcement
# ---------------------------------------------------------------------------

def test_enforce_limits_depth():
    nav = _make_nav(max_depth=3)
    deep_path = "/a/b/c/d/e"   # depth 5

    with pytest.raises(ValueError, match="max_depth"):
        nav._enforce_limits(deep_path)


# ---------------------------------------------------------------------------
# max_path_length enforcement
# ---------------------------------------------------------------------------

def test_enforce_limits_path_length():
    nav = _make_nav(max_path_length=50)
    long_path = "/" + "x" * 51   # length 52 > 50

    with pytest.raises(ValueError, match="max_path_length"):
        nav._enforce_limits(long_path)


# ---------------------------------------------------------------------------
# _normalize_path
# ---------------------------------------------------------------------------

def test_normalize_path_root():
    nav = FtpNavigator()
    assert nav._normalize_path("/") == "/"


def test_normalize_path_trailing_slash():
    nav = FtpNavigator()
    assert nav._normalize_path("/foo/bar/") == "/foo/bar"


# ---------------------------------------------------------------------------
# download_file — file too large
# ---------------------------------------------------------------------------

def test_download_file_too_large(tmp_path):
    nav = _make_nav(max_file_bytes=1000)

    # SIZE response > limit
    nav._ftp.voidcmd.return_value = "200 OK"
    nav._ftp.sendcmd.return_value = f"213 99999"

    with pytest.raises(FtpFileTooLargeError):
        nav.download_file("/big.iso", tmp_path)


# ---------------------------------------------------------------------------
# download_file — cancel clears connection
# ---------------------------------------------------------------------------

def test_download_cancel_clears_connection(tmp_path):
    nav = _make_nav()

    # SIZE returns small value (not too large)
    nav._ftp.sendcmd.return_value = "213 100"
    nav._ftp.voidcmd.return_value = "200 OK"

    def fake_retrbinary(cmd, callback, blocksize=None):
        nav.cancel()                     # set cancel_event
        callback(b"partial data")        # triggers FtpCancelledError in callback

    nav._ftp.retrbinary.side_effect = fake_retrbinary

    with pytest.raises(FtpCancelledError):
        nav.download_file("/file.txt", tmp_path)

    assert nav._ftp is None, "_ftp must be None after cancel"


# ---------------------------------------------------------------------------
# _ensure_connected — NOOP failure triggers reconnect
# ---------------------------------------------------------------------------

def test_ensure_connected_reconnects():
    nav = FtpNavigator()

    # Inject a mock ftp that fails on NOOP
    mock_ftp = MagicMock(spec=ftplib.FTP)
    mock_ftp.voidcmd.side_effect = EOFError("connection lost")
    nav._ftp = mock_ftp
    nav._host = "127.0.0.1"
    nav._port = 21

    reconnect_calls = []

    def fake_connect(host, port=21):
        reconnect_calls.append((host, port))
        new_mock = MagicMock(spec=ftplib.FTP)
        new_mock.voidcmd.return_value = "200 OK"
        new_mock.sock = MagicMock()
        nav._ftp = new_mock

    with patch.object(nav, "connect", side_effect=fake_connect):
        nav._ensure_connected()

    assert len(reconnect_calls) == 1
    assert reconnect_calls[0] == ("127.0.0.1", 21)
