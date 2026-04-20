"""Unit tests for shared discovery-dork config contract."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.discovery_dork_config import (
    DORK_DEFAULTS,
    apply_discovery_dorks,
    read_discovery_dorks,
    validate_discovery_dork,
)


def test_read_discovery_dorks_returns_defaults_when_paths_missing():
    out = read_discovery_dorks({"shodan": {}})
    assert out == DORK_DEFAULTS


def test_read_discovery_dorks_reads_existing_nested_paths():
    out = read_discovery_dorks(
        {
            "shodan": {"query_components": {"base_query": "smb custom"}},
            "ftp": {"shodan": {"query_components": {"base_query": "ftp custom"}}},
            "http": {"shodan": {"query_components": {"base_query": "http custom"}}},
        }
    )
    assert out["smb_dork"] == "smb custom"
    assert out["ftp_dork"] == "ftp custom"
    assert out["http_dork"] == "http custom"


def test_apply_discovery_dorks_writes_expected_nested_paths_only():
    cfg = {"shodan": {"api_key": "XYZ"}, "preserve_me": {"x": 1}}
    apply_discovery_dorks(
        cfg,
        {
            "smb_dork": "smb base",
            "ftp_dork": "ftp base",
            "http_dork": "http base",
        },
    )

    assert cfg["shodan"]["api_key"] == "XYZ"
    assert cfg["preserve_me"] == {"x": 1}
    assert cfg["shodan"]["query_components"]["base_query"] == "smb base"
    assert cfg["ftp"]["shodan"]["query_components"]["base_query"] == "ftp base"
    assert cfg["http"]["shodan"]["query_components"]["base_query"] == "http base"


def test_validate_discovery_dork_rejects_blank_queries():
    result = validate_discovery_dork("   ", "HTTP Base Query")
    assert result["valid"] is False
    assert "cannot be blank" in result["message"]
