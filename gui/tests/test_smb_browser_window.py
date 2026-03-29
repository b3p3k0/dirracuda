"""
Unit tests for _extract_smb_banner (gui/components/unified_browser_window.py).

Imports only unified_browser_window — avoids the shared.smb_browser / impacket
dep chain that file_browser_window pulls in.
"""
import json
import pytest

from gui.components.unified_browser_window import _extract_smb_banner


# ---------------------------------------------------------------------------
# Null / empty inputs
# ---------------------------------------------------------------------------


def test_none_returns_empty():
    assert _extract_smb_banner(None) == ""


def test_empty_string_returns_empty():
    assert _extract_smb_banner("") == ""


def test_invalid_json_returns_empty():
    assert _extract_smb_banner("{not valid json") == ""


def test_non_dict_json_returns_empty():
    assert _extract_smb_banner(json.dumps([1, 2, 3])) == ""


def test_empty_dict_returns_empty():
    assert _extract_smb_banner(json.dumps({})) == ""


# ---------------------------------------------------------------------------
# SMB port preferred
# ---------------------------------------------------------------------------


def test_port_445_service_preferred():
    d = {
        "data": [
            {"port": 80, "data": "HTTP/1.1 200 OK"},
            {"port": 445, "data": "SMBv2 banner text"},
        ]
    }
    assert _extract_smb_banner(json.dumps(d)) == "SMBv2 banner text"


def test_port_139_service_preferred():
    d = {
        "data": [
            {"port": 22, "data": "SSH banner"},
            {"port": 139, "data": "NetBIOS banner"},
        ]
    }
    assert _extract_smb_banner(json.dumps(d)) == "NetBIOS banner"


# ---------------------------------------------------------------------------
# Non-SMB service fallback
# ---------------------------------------------------------------------------


def test_non_smb_service_used_when_no_smb_port():
    d = {"data": [{"port": 80, "data": "HTTP banner here"}]}
    assert _extract_smb_banner(json.dumps(d)) == "HTTP banner here"


# ---------------------------------------------------------------------------
# Org / ISP / hostnames fallback
# ---------------------------------------------------------------------------


def test_org_fallback_when_no_service_data():
    d = {"org": "ACME Corp"}
    assert _extract_smb_banner(json.dumps(d)) == "ACME Corp"


def test_isp_fallback_after_org_missing():
    d = {"isp": "CheapISP"}
    assert _extract_smb_banner(json.dumps(d)) == "CheapISP"


def test_hostname_fallback():
    d = {"hostnames": ["host.example.com", "alias.example.com"]}
    assert _extract_smb_banner(json.dumps(d)) == "host.example.com"


def test_all_fields_empty_returns_empty():
    d = {"data": [], "org": "", "isp": None, "hostnames": []}
    assert _extract_smb_banner(json.dumps(d)) == ""


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


def test_service_data_truncated_to_500():
    long_banner = "x" * 600
    d = {"data": [{"port": 445, "data": long_banner}]}
    result = _extract_smb_banner(json.dumps(d))
    assert len(result) == 500
    assert result == "x" * 500


# ---------------------------------------------------------------------------
# Whitespace stripping
# ---------------------------------------------------------------------------


def test_service_data_stripped():
    d = {"data": [{"port": 445, "data": "  banner with spaces  "}]}
    assert _extract_smb_banner(json.dumps(d)) == "banner with spaces"


def test_service_data_whitespace_only_skipped():
    d = {
        "data": [{"port": 445, "data": "   "}],
        "org": "Fallback Org",
    }
    assert _extract_smb_banner(json.dumps(d)) == "Fallback Org"


# ---------------------------------------------------------------------------
# Accepts dict directly (not just JSON string)
# ---------------------------------------------------------------------------


def test_accepts_dict_input():
    d = {"org": "DictOrg"}
    assert _extract_smb_banner(d) == "DictOrg"
