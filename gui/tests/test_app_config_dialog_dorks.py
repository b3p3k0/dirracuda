"""Tests for AppConfigDialog behavior after Discovery Dorks UI removal."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.app_config_dialog import AppConfigDialog


def test_required_fields_no_longer_include_discovery_dorks():
    assert "smb_dork" not in AppConfigDialog.REQUIRED_FIELDS
    assert "ftp_dork" not in AppConfigDialog.REQUIRED_FIELDS
    assert "http_dork" not in AppConfigDialog.REQUIRED_FIELDS


def test_experimental_fields_absent_from_validation_results():
    import inspect
    src = inspect.getsource(AppConfigDialog.__init__)
    for removed_key in (
        "se_dork_instance_url", "se_dork_query", "se_dork_max_results", "se_dork_probe_workers",
        "reddit_mode", "reddit_sort", "reddit_top_window", "reddit_query",
        "reddit_username", "reddit_max_posts",
    ):
        assert f'"{removed_key}"' not in src, (
            f"Removed key {removed_key!r} still found in AppConfigDialog.__init__"
        )


def test_validate_all_fields_skips_experimental_fields():
    dlg = AppConfigDialog.__new__(AppConfigDialog)
    visited = []
    dlg._validate_field = lambda field: visited.append(field)

    dlg._validate_all_fields()

    assert visited == ["smbseek", "database", "config", "api_key", "quarantine"]
    for removed in (
        "se_dork_instance_url", "se_dork_query", "se_dork_max_results", "se_dork_probe_workers",
        "reddit_mode", "reddit_sort", "reddit_top_window", "reddit_query",
        "reddit_username", "reddit_max_posts",
    ):
        assert removed not in visited


def test_apply_runtime_settings_preserves_existing_dork_keys():
    dlg = AppConfigDialog.__new__(AppConfigDialog)
    config_data = {
        "shodan": {
            "api_key": "OLD",
            "query_components": {"base_query": "smb keep"},
        },
        "ftp": {
            "shodan": {"query_components": {"base_query": "ftp keep"}},
        },
        "http": {
            "shodan": {"query_components": {"base_query": "http keep"}},
        },
    }

    dlg._apply_runtime_settings(
        config_data,
        api_key="NEW",
        quarantine_path="~/.dirracuda/quarantine",
    )

    assert config_data["shodan"]["api_key"] == "NEW"
    assert config_data["shodan"]["query_components"]["base_query"] == "smb keep"
    assert config_data["ftp"]["shodan"]["query_components"]["base_query"] == "ftp keep"
    assert config_data["http"]["shodan"]["query_components"]["base_query"] == "http keep"


def test_apply_runtime_settings_writes_censys_section():
    dlg = AppConfigDialog.__new__(AppConfigDialog)
    config_data = {}

    dlg._apply_runtime_settings(
        config_data,
        api_key="NEW",
        quarantine_path="~/.dirracuda/quarantine",
        censys_settings={
            "personal_access_token": "censys-pat",
            "organization_id": "11111111-2222-3333-4444-555555555555",
            "credit_profile": "search_enterprise",
            "defaults": {
                "max_pages": 20,
                "query_hours": 48,
                "page_size": 50,
                "ipv6_enabled": True,
            },
        },
    )

    assert config_data["censys"]["personal_access_token"] == "censys-pat"
    assert config_data["censys"]["organization_id"] == "11111111-2222-3333-4444-555555555555"
    assert config_data["censys"]["credit_profile"] == "search_enterprise"
    assert config_data["censys"]["defaults"]["max_pages"] == 20
    assert config_data["censys"]["defaults"]["query_hours"] == 48
    assert config_data["censys"]["defaults"]["page_size"] == 50
    assert config_data["censys"]["defaults"]["ipv6_enabled"] is True


def test_apply_runtime_settings_clamps_censys_defaults_and_profile():
    dlg = AppConfigDialog.__new__(AppConfigDialog)
    config_data = {}

    dlg._apply_runtime_settings(
        config_data,
        api_key="NEW",
        quarantine_path="~/.dirracuda/quarantine",
        censys_settings={
            "personal_access_token": "  ",
            "organization_id": "",
            "credit_profile": "invalid_profile",
            "defaults": {
                "max_pages": 9999,
                "query_hours": -1,
                "page_size": 0,
                "ipv6_enabled": "yes",
            },
        },
    )

    assert config_data["censys"]["personal_access_token"] == ""
    assert config_data["censys"]["organization_id"] == ""
    assert config_data["censys"]["credit_profile"] == "free_starter"
    assert config_data["censys"]["defaults"]["max_pages"] == 100
    assert config_data["censys"]["defaults"]["query_hours"] == 1
    assert config_data["censys"]["defaults"]["page_size"] == 1
    assert config_data["censys"]["defaults"]["ipv6_enabled"] is True
