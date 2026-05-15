from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experimental.censys_discovery.query_builder import (
    PROTOCOL_EXTRA_FIELDS,
    build_query,
    get_extra_fields,
)


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


def test_get_extra_fields_http():
    fields = get_extra_fields("HTTP")
    assert "host.services.endpoints.http.headers" in fields
    assert "host.services.endpoints.http.body_hash_sha256" in fields


def test_get_extra_fields_ftp():
    fields = get_extra_fields("FTP")
    assert "host.services.ftp.banner" in fields
    assert "host.services.ftp.implicit_tls" in fields
    assert "host.services.ftp.status_code" in fields


def test_get_extra_fields_smb():
    fields = get_extra_fields("SMB")
    assert "host.services.software.product" in fields
    assert "host.services.software.version" in fields


def test_get_extra_fields_case_insensitive():
    assert get_extra_fields("http") == get_extra_fields("HTTP")


def test_get_extra_fields_unknown_protocol():
    result = get_extra_fields("GOPHER")
    assert result == []


def test_get_extra_fields_returns_copy():
    fields = get_extra_fields("HTTP")
    fields.append("mutated")
    assert "mutated" not in PROTOCOL_EXTRA_FIELDS["HTTP"]
