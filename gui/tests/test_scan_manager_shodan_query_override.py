"""ScanManager per-protocol Shodan base_query override tests.

Each per-protocol scan worker injects a run-scoped Shodan ``base_query`` config
override when ``scan_options`` carries a non-blank query for that protocol,
using the existing ``_temporary_config_override`` plumbing. These tests assert
the override-to-config mapping by name for SMB/FTP/HTTP and confirm that absent
or blank queries inject nothing. Shodan is never invoked (the scan execution is
stubbed), so no network is hit.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock


def _make_manager():
    """Build a ScanManager with the scan execution fully stubbed."""
    from gui.utils.scan_manager import ScanManager

    manager = ScanManager.__new__(ScanManager)
    manager.backend_interface = MagicMock()
    manager.log_callback = None
    manager._update_progress = MagicMock()
    manager._execute_scan_with_options = MagicMock(return_value={"success": True})
    manager._process_scan_results = MagicMock()
    manager._handle_scan_error = MagicMock()
    manager._cleanup_scan = MagicMock()

    captured = {}

    @contextmanager
    def _fake_override(overrides):
        captured["overrides"] = overrides
        yield

    manager.backend_interface._temporary_config_override = _fake_override
    return manager, captured


def test_smb_scan_worker_injects_base_query():
    manager, captured = _make_manager()

    manager._scan_worker(
        {
            "country": "US",
            "smb_shodan_query": "smb authentication: disabled",
        }
    )

    assert captured["overrides"]["shodan"]["query_components"]["base_query"] == (
        "smb authentication: disabled"
    )


def test_ftp_scan_worker_injects_base_query():
    manager, captured = _make_manager()

    manager._ftp_scan_worker(
        {
            "country": "US",
            "ftp_shodan_query": 'port:21 "230 Login successful"',
        }
    )

    assert captured["overrides"]["ftp"]["shodan"]["query_components"]["base_query"] == (
        'port:21 "230 Login successful"'
    )


def test_http_scan_worker_injects_base_query():
    manager, captured = _make_manager()

    manager._http_scan_worker(
        {
            "country": "US",
            "http_shodan_query": 'http.title:"Index of /"',
        }
    )

    assert captured["overrides"]["http"]["shodan"]["query_components"]["base_query"] == (
        'http.title:"Index of /"'
    )


def test_absent_query_injects_no_base_query():
    """No query key for any protocol -> no base_query injected anywhere."""
    # SMB
    manager, captured = _make_manager()
    manager._scan_worker({"country": "US", "max_shodan_results": 1000})
    assert "query_components" not in captured["overrides"].get("shodan", {})

    # FTP
    manager, captured = _make_manager()
    manager._ftp_scan_worker({"country": "US", "max_shodan_results": 1000})
    assert "query_components" not in captured["overrides"].get("ftp", {}).get(
        "shodan", {}
    )

    # HTTP
    manager, captured = _make_manager()
    manager._http_scan_worker({"country": "US", "max_shodan_results": 1000})
    assert "query_components" not in captured["overrides"].get("http", {}).get(
        "shodan", {}
    )


def test_blank_query_injects_no_base_query():
    """A blank/empty query is falsy -> no base_query injected.

    A non-blank ``max_shodan_results`` is supplied so ``config_overrides`` is
    non-empty and the override context manager actually runs; otherwise the
    worker skips it and there is nothing to capture.
    """
    # SMB
    manager, captured = _make_manager()
    manager._scan_worker(
        {"country": "US", "max_shodan_results": 1000, "smb_shodan_query": ""}
    )
    assert "query_components" not in captured["overrides"].get("shodan", {})

    # FTP
    manager, captured = _make_manager()
    manager._ftp_scan_worker(
        {"country": "US", "max_shodan_results": 1000, "ftp_shodan_query": ""}
    )
    assert "query_components" not in captured["overrides"].get("ftp", {}).get(
        "shodan", {}
    )

    # HTTP
    manager, captured = _make_manager()
    manager._http_scan_worker(
        {"country": "US", "max_shodan_results": 1000, "http_shodan_query": ""}
    )
    assert "query_components" not in captured["overrides"].get("http", {}).get(
        "shodan", {}
    )


def test_base_query_does_not_disturb_api_key_or_max_results():
    """api_key_override and max_results overrides are unaffected by the query."""
    # SMB: api_key + max_results + query all supplied together.
    manager, captured = _make_manager()
    manager._scan_worker(
        {
            "country": "US",
            "api_key_override": "SMB_KEY",
            "max_shodan_results": 1000,
            "smb_shodan_query": "smb authentication: disabled",
        }
    )
    overrides = captured["overrides"]
    assert overrides["shodan"]["api_key"] == "SMB_KEY"
    assert overrides["shodan"]["query_limits"]["max_results"] == 1000
    assert overrides["shodan"]["query_components"]["base_query"] == (
        "smb authentication: disabled"
    )

    # FTP: api_key (global shodan path) + max_results (ftp.shodan) + query.
    manager, captured = _make_manager()
    manager._ftp_scan_worker(
        {
            "country": "US",
            "api_key_override": "FTP_KEY",
            "max_shodan_results": 500,
            "ftp_shodan_query": 'port:21 "230 Login successful"',
        }
    )
    overrides = captured["overrides"]
    assert overrides["shodan"]["api_key"] == "FTP_KEY"
    assert overrides["ftp"]["shodan"]["query_limits"]["max_results"] == 500
    assert overrides["ftp"]["shodan"]["query_components"]["base_query"] == (
        'port:21 "230 Login successful"'
    )

    # HTTP: api_key (global shodan path) + max_results (http.shodan) + query.
    manager, captured = _make_manager()
    manager._http_scan_worker(
        {
            "country": "US",
            "api_key_override": "HTTP_KEY",
            "max_shodan_results": 250,
            "http_shodan_query": 'http.title:"Index of /"',
        }
    )
    overrides = captured["overrides"]
    assert overrides["shodan"]["api_key"] == "HTTP_KEY"
    assert overrides["http"]["shodan"]["query_limits"]["max_results"] == 250
    assert overrides["http"]["shodan"]["query_components"]["base_query"] == (
        'http.title:"Index of /"'
    )
