from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experimental.censys_discovery.query_builder import build_query


def test_ftp_baseline_uses_nested_clause():
    assert "host.services:(protocol=FTP and port=21)" in build_query("FTP")


def test_http_baseline_uses_nested_clause():
    assert "host.services:(protocol=HTTP and port=80)" in build_query("HTTP")


def test_smb_baseline_uses_nested_clause():
    assert "host.services:(protocol=SMB and port=445)" in build_query("SMB")


def test_freshness_appended_when_query_hours_given():
    result = build_query("FTP", query_hours=24)
    assert 'scan_time > "now-24h"' in result


def test_no_freshness_when_query_hours_none():
    result = build_query("FTP")
    assert "scan_time" not in result


def test_query_hours_zero_produces_no_freshness():
    result = build_query("FTP", query_hours=0)
    assert "scan_time" not in result


def test_invalid_protocol_raises_value_error():
    with pytest.raises(ValueError):
        build_query("TELNET")


def test_protocol_is_case_insensitive():
    assert build_query("ftp") == build_query("FTP")


def test_non_nested_service_protocol_field_absent():
    result = build_query("SMB")
    assert "host.services.protocol" not in result


def test_non_nested_port_field_absent():
    result = build_query("SMB")
    assert "host.services.port" not in result


def test_freshness_is_outside_nested_clause():
    result = build_query("FTP", query_hours=72)
    nested_end = result.index(")")
    freshness_start = result.find("scan_time")
    assert freshness_start > nested_end
