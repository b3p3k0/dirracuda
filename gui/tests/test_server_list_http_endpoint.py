"""HTTP endpoint resolution tests for Server List copy and browse actions."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from gui.components.server_list_window import details
from gui.components.server_list_window.http_endpoint import (
    open_http_server_browser,
    resolve_http_endpoint,
    resolve_http_target,
)


def test_resolver_prefers_db_detail_and_preserves_saved_directory_path():
    db_reader = MagicMock()
    db_reader.get_http_server_detail.return_value = {
        "protocol_server_id": 55,
        "scheme": "https",
        "port": 443,
        "probe_host": "www.sellingyourscreenplay.com",
        "probe_path": "/wp-content/uploads/screenplay/scripts/",
    }

    endpoint = resolve_http_endpoint(
        "184.171.253.117",
        {
            "protocol_server_id": 55,
            "port": 80,
            "scheme": "http",
            "probe_host": "stale.example",
            "probe_path": "/stale/",
        },
        db_reader=db_reader,
    )

    assert endpoint.ip_address == "184.171.253.117"
    assert endpoint.port == 443
    assert endpoint.scheme == "https"
    assert endpoint.request_host == "www.sellingyourscreenplay.com"
    assert endpoint.initial_path == "/wp-content/uploads/screenplay/scripts/"
    assert endpoint.url == (
        "https://www.sellingyourscreenplay.com:443"
        "/wp-content/uploads/screenplay/scripts/"
    )


def test_resolver_uses_row_values_and_defaults_missing_path_to_root():
    endpoint = resolve_http_endpoint(
        "192.0.2.10",
        {"port": 8080, "scheme": "http", "probe_host": "files.example.test"},
    )

    assert endpoint.request_host == "files.example.test"
    assert endpoint.initial_path == "/"
    assert endpoint.url == "http://files.example.test:8080/"


def test_target_resolver_reads_nested_server_list_data_and_normalizes_path():
    endpoint = resolve_http_target(
        {
            "ip_address": "192.0.2.20",
            "host_type": "H",
            "data": {
                "port": 8443,
                "scheme": "https",
                "probe_host": "archive.example.test",
                "probe_path": "files/releases/?sort=name#top",
            },
        }
    )

    assert endpoint.initial_path == "/files/releases/"
    assert endpoint.url == "https://archive.example.test:8443/files/releases/"


def test_open_helper_preserves_ip_identity_and_passes_request_metadata():
    endpoint = resolve_http_endpoint(
        "184.171.253.117",
        {
            "port": 443,
            "scheme": "https",
            "probe_host": "www.sellingyourscreenplay.com",
            "probe_path": "/wp-content/uploads/screenplay/scripts/",
        },
    )

    with patch(
        "gui.components.unified_browser_window.open_ftp_http_browser"
    ) as open_browser:
        open_http_server_browser(
            endpoint,
            parent="parent",
            db_reader="reader",
            theme="theme",
        )

    open_browser.assert_called_once_with(
        "H",
        parent="parent",
        ip_address="184.171.253.117",
        port=443,
        scheme="https",
        request_host="www.sellingyourscreenplay.com",
        initial_path="/wp-content/uploads/screenplay/scripts/",
        banner=None,
        config_path=None,
        db_reader="reader",
        theme="theme",
        settings_manager=None,
    )


def test_details_browse_uses_same_endpoint_contract(monkeypatch):
    settings = MagicMock()
    settings.get_database_path.return_value = str(Path("/tmp/dirracuda.db"))
    settings.get_setting.return_value = "/tmp/config.json"
    db_reader = MagicMock()
    db_reader.get_http_server_detail.return_value = {
        "port": 443,
        "scheme": "https",
        "probe_host": "details.example.test",
        "probe_path": "/saved/path/",
    }
    opened = []

    monkeypatch.setattr(details, "DatabaseReader", lambda _path: db_reader)
    monkeypatch.setattr(
        details,
        "open_http_server_browser",
        lambda endpoint, **kwargs: opened.append((endpoint, kwargs)),
    )

    details._open_http_detail_browser(
        "parent",
        {
            "ip_address": "192.0.2.30",
            "host_type": "H",
            "protocol_server_id": 30,
            "port": 443,
        },
        "theme",
        settings_manager=settings,
    )

    endpoint, kwargs = opened[0]
    assert endpoint.request_host == "details.example.test"
    assert endpoint.initial_path == "/saved/path/"
    assert kwargs["parent"] == "parent"
    assert kwargs["db_reader"] is db_reader
