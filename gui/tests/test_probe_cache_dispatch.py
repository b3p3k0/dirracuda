"""
Unit tests for gui.utils.probe_cache_dispatch.load_probe_result_for_host.

All tests use unittest.mock.patch — no real cache directories are touched.
"""

from unittest.mock import patch, MagicMock

import pytest

from gui.utils.probe_cache_dispatch import (
    load_probe_result_for_host,
    get_probe_snapshot_path_for_host,
    dispatch_probe_run,
)

_FAKE_SNAPSHOT = {"ip_address": "1.2.3.4", "shares": []}
_IP = "1.2.3.4"


# ---------------------------------------------------------------------------
# Protocol routing
# ---------------------------------------------------------------------------

def test_smb_explicit():
    with patch("gui.utils.probe_cache_dispatch.probe_cache") as m:
        m.load_probe_result.return_value = _FAKE_SNAPSHOT
        result = load_probe_result_for_host(_IP, "S")
    m.load_probe_result.assert_called_once_with(_IP)
    assert result is _FAKE_SNAPSHOT


def test_ftp_uppercase():
    with patch("gui.utils.probe_cache_dispatch.ftp_probe_cache") as m:
        m.load_ftp_probe_result.return_value = _FAKE_SNAPSHOT
        result = load_probe_result_for_host(_IP, "F")
    m.load_ftp_probe_result.assert_called_once_with(_IP)
    assert result is _FAKE_SNAPSHOT


def test_http_uppercase():
    with patch("gui.utils.probe_cache_dispatch.http_probe_cache") as m:
        m.load_http_probe_result.return_value = _FAKE_SNAPSHOT
        result = load_probe_result_for_host(_IP, "H")
    m.load_http_probe_result.assert_called_once_with(_IP)
    assert result is _FAKE_SNAPSHOT


# ---------------------------------------------------------------------------
# DB-first precedence and file-cache fallback
# ---------------------------------------------------------------------------

def test_db_first_snapshot_short_circuits_file_cache():
    class _FakeReader:
        def get_probe_snapshot_for_host(self, ip_address, host_type, *, port=None):
            assert ip_address == _IP
            assert host_type == "H"
            assert port == 8443
            return {"ip_address": ip_address, "entries": []}

    with patch("gui.utils.probe_cache_dispatch._get_cached_db_reader", return_value=_FakeReader()), \
         patch("gui.utils.probe_cache_dispatch.http_probe_cache") as http_m:
        result = load_probe_result_for_host(_IP, "H", port=8443)

    assert result == {"ip_address": _IP, "entries": []}
    http_m.load_http_probe_result.assert_not_called()


def test_db_miss_falls_back_to_protocol_cache():
    class _FakeReader:
        def get_probe_snapshot_for_host(self, *_args, **_kwargs):
            return None

    with patch("gui.utils.probe_cache_dispatch._get_cached_db_reader", return_value=_FakeReader()), \
         patch("gui.utils.probe_cache_dispatch.ftp_probe_cache") as ftp_m:
        ftp_m.load_ftp_probe_result.return_value = _FAKE_SNAPSHOT
        result = load_probe_result_for_host(_IP, "F")

    ftp_m.load_ftp_probe_result.assert_called_once_with(_IP)
    assert result is _FAKE_SNAPSHOT


def test_db_exception_falls_back_to_protocol_cache():
    class _FakeReader:
        def get_probe_snapshot_for_host(self, *_args, **_kwargs):
            raise RuntimeError("db unavailable")

    with patch("gui.utils.probe_cache_dispatch._get_cached_db_reader", return_value=_FakeReader()), \
         patch("gui.utils.probe_cache_dispatch.probe_cache") as smb_m:
        smb_m.load_probe_result.return_value = _FAKE_SNAPSHOT
        result = load_probe_result_for_host(_IP, "S")

    smb_m.load_probe_result.assert_called_once_with(_IP)
    assert result is _FAKE_SNAPSHOT


# ---------------------------------------------------------------------------
# Normalisation (lowercase input)
# ---------------------------------------------------------------------------

def test_ftp_lowercase():
    with patch("gui.utils.probe_cache_dispatch.ftp_probe_cache") as ftp_m, \
         patch("gui.utils.probe_cache_dispatch.probe_cache") as smb_m:
        ftp_m.load_ftp_probe_result.return_value = _FAKE_SNAPSHOT
        result = load_probe_result_for_host(_IP, "f")
    ftp_m.load_ftp_probe_result.assert_called_once_with(_IP)
    smb_m.load_probe_result.assert_not_called()
    assert result is _FAKE_SNAPSHOT


def test_http_lowercase():
    with patch("gui.utils.probe_cache_dispatch.http_probe_cache") as http_m, \
         patch("gui.utils.probe_cache_dispatch.probe_cache") as smb_m:
        http_m.load_http_probe_result.return_value = _FAKE_SNAPSHOT
        result = load_probe_result_for_host(_IP, "h")
    http_m.load_http_probe_result.assert_called_once_with(_IP)
    smb_m.load_probe_result.assert_not_called()
    assert result is _FAKE_SNAPSHOT


# ---------------------------------------------------------------------------
# Contract: unknown type falls back to SMB
# ---------------------------------------------------------------------------

def test_unknown_type_falls_back_to_smb():
    with patch("gui.utils.probe_cache_dispatch.probe_cache") as smb_m, \
         patch("gui.utils.probe_cache_dispatch.ftp_probe_cache") as ftp_m, \
         patch("gui.utils.probe_cache_dispatch.http_probe_cache") as http_m:
        smb_m.load_probe_result.return_value = _FAKE_SNAPSHOT
        result = load_probe_result_for_host(_IP, "X")
    smb_m.load_probe_result.assert_called_once_with(_IP)
    ftp_m.load_ftp_probe_result.assert_not_called()
    http_m.load_http_probe_result.assert_not_called()
    assert result is _FAKE_SNAPSHOT


# ---------------------------------------------------------------------------
# Contract: falsy ip returns None without calling any loader
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ip", ["", None])
def test_falsy_ip_returns_none(ip):
    with patch("gui.utils.probe_cache_dispatch.probe_cache") as smb_m, \
         patch("gui.utils.probe_cache_dispatch.ftp_probe_cache") as ftp_m, \
         patch("gui.utils.probe_cache_dispatch.http_probe_cache") as http_m:
        result = load_probe_result_for_host(ip, "S")
    assert result is None
    smb_m.load_probe_result.assert_not_called()
    ftp_m.load_ftp_probe_result.assert_not_called()
    http_m.load_http_probe_result.assert_not_called()


# ---------------------------------------------------------------------------
# Cache miss propagated
# ---------------------------------------------------------------------------

def test_cache_miss_propagated():
    with patch("gui.utils.probe_cache_dispatch.probe_cache") as m:
        m.load_probe_result.return_value = None
        result = load_probe_result_for_host(_IP, "S")
    assert result is None


# ---------------------------------------------------------------------------
# Contract: non-string host_type safe coercion
# ---------------------------------------------------------------------------

def test_non_string_type_safe_coercion():
    """int host_type must not raise; should fall back to SMB loader."""
    with patch("gui.utils.probe_cache_dispatch.probe_cache") as smb_m, \
         patch("gui.utils.probe_cache_dispatch.ftp_probe_cache") as ftp_m, \
         patch("gui.utils.probe_cache_dispatch.http_probe_cache") as http_m:
        smb_m.load_probe_result.return_value = _FAKE_SNAPSHOT
        result = load_probe_result_for_host(_IP, 1)
    smb_m.load_probe_result.assert_called_once_with(_IP)
    ftp_m.load_ftp_probe_result.assert_not_called()
    http_m.load_http_probe_result.assert_not_called()
    assert result is _FAKE_SNAPSHOT


# ---------------------------------------------------------------------------
# get_probe_snapshot_path_for_host
# ---------------------------------------------------------------------------

def test_snapshot_path_ftp_uppercase():
    with patch("gui.utils.probe_cache_dispatch.ftp_probe_cache") as ftp_m:
        ftp_m.get_ftp_cache_path.return_value = "/fake/path/1.2.3.4.json"
        result = get_probe_snapshot_path_for_host(_IP, "F")
    ftp_m.get_ftp_cache_path.assert_called_once_with(_IP)
    assert result == "/fake/path/1.2.3.4.json"


def test_snapshot_path_http_uppercase():
    with patch("gui.utils.probe_cache_dispatch.http_probe_cache") as http_m:
        http_m.get_http_cache_path.return_value = "/fake/path/1.2.3.4.json"
        result = get_probe_snapshot_path_for_host(_IP, "H")
    http_m.get_http_cache_path.assert_called_once_with(_IP)
    assert result == "/fake/path/1.2.3.4.json"


def test_snapshot_path_ftp_lowercase():
    with patch("gui.utils.probe_cache_dispatch.ftp_probe_cache") as ftp_m, \
         patch("gui.utils.probe_cache_dispatch.http_probe_cache") as http_m:
        ftp_m.get_ftp_cache_path.return_value = "/fake/path/1.2.3.4.json"
        result = get_probe_snapshot_path_for_host(_IP, "f")
    ftp_m.get_ftp_cache_path.assert_called_once_with(_IP)
    http_m.get_http_cache_path.assert_not_called()
    assert result == "/fake/path/1.2.3.4.json"


def test_snapshot_path_http_lowercase():
    with patch("gui.utils.probe_cache_dispatch.http_probe_cache") as http_m, \
         patch("gui.utils.probe_cache_dispatch.ftp_probe_cache") as ftp_m:
        http_m.get_http_cache_path.return_value = "/fake/path/1.2.3.4.json"
        result = get_probe_snapshot_path_for_host(_IP, "h")
    http_m.get_http_cache_path.assert_called_once_with(_IP)
    ftp_m.get_ftp_cache_path.assert_not_called()
    assert result == "/fake/path/1.2.3.4.json"


def test_snapshot_path_unknown_returns_none():
    with patch("gui.utils.probe_cache_dispatch.ftp_probe_cache") as ftp_m, \
         patch("gui.utils.probe_cache_dispatch.http_probe_cache") as http_m:
        result = get_probe_snapshot_path_for_host(_IP, "X")
    assert result is None
    ftp_m.get_ftp_cache_path.assert_not_called()
    http_m.get_http_cache_path.assert_not_called()


@pytest.mark.parametrize("ip", ["", None])
def test_snapshot_path_falsy_ip_returns_none(ip):
    with patch("gui.utils.probe_cache_dispatch.ftp_probe_cache") as ftp_m, \
         patch("gui.utils.probe_cache_dispatch.http_probe_cache") as http_m:
        result = get_probe_snapshot_path_for_host(ip, "F")
    assert result is None
    ftp_m.get_ftp_cache_path.assert_not_called()
    http_m.get_http_cache_path.assert_not_called()


# ---------------------------------------------------------------------------
# dispatch_probe_run
# ---------------------------------------------------------------------------

import threading as _threading
from unittest.mock import MagicMock as _MagicMock

_FAKE_FTP_SNAP = {"ip_address": _IP, "shares": [{"directories": [{"name": "pub"}], "root_files": []}]}
_FAKE_HTTP_SNAP = {"ip_address": _IP, "shares": [{"directories": [], "root_files": []}]}
_FAKE_SMB_SNAP = {"ip_address": _IP, "shares": [{"share_name": "docs"}]}


def test_dispatch_ftp_kwargs_and_cancel_event():
    """FTP: correct kwargs forwarded; max_entries = max_dirs*max_files; cancel_event passed."""
    cancel = _threading.Event()
    with patch("gui.utils.probe_cache_dispatch.ftp_probe_runner") as ftp_m:
        ftp_m.run_ftp_probe.return_value = _FAKE_FTP_SNAP
        result = dispatch_probe_run(
            _IP, "F",
            max_directories=3,
            max_files=5,
            timeout_seconds=10,
            cancel_event=cancel,
            port=2121,
        )
    assert result is _FAKE_FTP_SNAP
    kw = ftp_m.run_ftp_probe.call_args.kwargs
    assert kw["port"] == 2121
    assert kw["max_entries"] == 15  # max(1, 3*5)
    assert kw["max_directories"] == 3
    assert kw["max_files"] == 5
    assert kw["connect_timeout"] == 10
    assert kw["request_timeout"] == 10
    assert kw["cancel_event"] is cancel


def test_dispatch_http_explicit_scheme_skips_db_lookup():
    """HTTP with explicit scheme: db_reader.get_http_server_detail must not be called."""
    cancel = _threading.Event()
    db_mock = _MagicMock()
    with patch("gui.utils.probe_cache_dispatch.http_probe_runner") as http_m:
        http_m.run_http_probe.return_value = _FAKE_HTTP_SNAP
        dispatch_probe_run(
            _IP, "H",
            max_directories=3,
            max_files=5,
            timeout_seconds=10,
            cancel_event=cancel,
            port=8080,
            scheme="https",
            db_reader=db_mock,
        )
    db_mock.get_http_server_detail.assert_not_called()
    kw = http_m.run_http_probe.call_args.kwargs
    assert kw["port"] == 8080
    assert kw["scheme"] == "https"
    assert kw["request_host"] is None
    assert kw["start_path"] == "/"
    assert kw["allow_insecure_tls"] is True
    assert kw["cancel_event"] is cancel


def test_dispatch_http_scheme_none_resolves_from_db_reader():
    """HTTP with scheme=None: port/scheme resolved from db_reader."""
    cancel = _threading.Event()
    db_mock = _MagicMock()
    db_mock.get_http_server_detail.return_value = {
        "port": 443,
        "scheme": "https",
        "probe_host": "example.com",
        "probe_path": "/movies/",
    }
    with patch("gui.utils.probe_cache_dispatch.http_probe_runner") as http_m:
        http_m.run_http_probe.return_value = _FAKE_HTTP_SNAP
        dispatch_probe_run(
            _IP, "H",
            max_directories=2,
            max_files=4,
            timeout_seconds=5,
            cancel_event=cancel,
            db_reader=db_mock,
        )
    db_mock.get_http_server_detail.assert_called_once_with(_IP)
    kw = http_m.run_http_probe.call_args.kwargs
    assert kw["port"] == 443
    assert kw["scheme"] == "https"
    assert kw["request_host"] == "example.com"
    assert kw["start_path"] == "/movies/"


def test_dispatch_http_explicit_hints_forwarded():
    cancel = _threading.Event()
    db_mock = _MagicMock()
    with patch("gui.utils.probe_cache_dispatch.http_probe_runner") as http_m:
        http_m.run_http_probe.return_value = _FAKE_HTTP_SNAP
        dispatch_probe_run(
            _IP, "H",
            max_directories=3,
            max_files=5,
            timeout_seconds=10,
            cancel_event=cancel,
            port=8443,
            scheme="https",
            request_host="www.example.com",
            start_path="/movies/",
            db_reader=db_mock,
        )
    db_mock.get_http_server_detail.assert_not_called()
    kw = http_m.run_http_probe.call_args.kwargs
    assert kw["port"] == 8443
    assert kw["scheme"] == "https"
    assert kw["request_host"] == "www.example.com"
    assert kw["start_path"] == "/movies/"


def test_dispatch_smb_omitted_creds_not_forwarded():
    """SMB with omitted creds: run_probe must not receive username or password kwargs."""
    cancel = _threading.Event()
    with patch("gui.utils.probe_cache_dispatch.probe_runner") as pr_m:
        pr_m.run_probe.return_value = _FAKE_SMB_SNAP
        dispatch_probe_run(
            _IP, "S",
            max_directories=3,
            max_files=5,
            timeout_seconds=10,
            cancel_event=cancel,
            shares=["docs"],
        )
    kw = pr_m.run_probe.call_args.kwargs
    assert "username" not in kw, "username must be absent so probe_runner DEFAULT_USERNAME applies"
    assert "password" not in kw, "password must be absent so probe_runner DEFAULT_PASSWORD applies"


def test_dispatch_smb_explicit_creds_forwarded():
    """SMB with explicit credentials: run_probe receives them."""
    cancel = _threading.Event()
    with patch("gui.utils.probe_cache_dispatch.probe_runner") as pr_m:
        pr_m.run_probe.return_value = _FAKE_SMB_SNAP
        dispatch_probe_run(
            _IP, "S",
            max_directories=3,
            max_files=5,
            timeout_seconds=10,
            cancel_event=cancel,
            shares=["docs"],
            username="guest",
            password="",
        )
    kw = pr_m.run_probe.call_args.kwargs
    assert kw["username"] == "guest"
    assert kw["password"] == ""


def test_dispatch_smb_kwargs_forwarding():
    """SMB: allow_empty, enable_rce_analysis, db_accessor, cancel_event forwarded correctly."""
    cancel = _threading.Event()
    db_mock = _MagicMock()
    with patch("gui.utils.probe_cache_dispatch.probe_runner") as pr_m:
        pr_m.run_probe.return_value = _FAKE_SMB_SNAP
        dispatch_probe_run(
            _IP, "S",
            max_directories=3,
            max_files=5,
            timeout_seconds=10,
            cancel_event=cancel,
            shares=["share1"],
            enable_rce=True,
            allow_empty=True,
            db_reader=db_mock,
        )
    kw = pr_m.run_probe.call_args.kwargs
    assert kw["enable_rce_analysis"] is True
    assert kw["allow_empty"] is True
    assert kw["db_accessor"] is db_mock
    assert kw["cancel_event"] is cancel
