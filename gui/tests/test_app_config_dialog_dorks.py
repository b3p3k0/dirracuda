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


def test_validate_all_fields_skips_discovery_dork_fields():
    dlg = AppConfigDialog.__new__(AppConfigDialog)
    dlg.show_pry_controls = False
    visited = []
    dlg._validate_field = lambda field: visited.append(field)

    dlg._validate_all_fields()

    assert visited == ["smbseek", "database", "config", "api_key", "quarantine"]


def test_validate_all_fields_includes_wordlist_when_pry_controls_enabled():
    dlg = AppConfigDialog.__new__(AppConfigDialog)
    dlg.show_pry_controls = True
    visited = []
    dlg._validate_field = lambda field: visited.append(field)

    dlg._validate_all_fields()

    assert visited == ["smbseek", "database", "config", "api_key", "quarantine", "wordlist"]


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
        wordlist_path="/tmp/words.txt",
    )

    assert config_data["shodan"]["api_key"] == "NEW"
    assert config_data["shodan"]["query_components"]["base_query"] == "smb keep"
    assert config_data["ftp"]["shodan"]["query_components"]["base_query"] == "ftp keep"
    assert config_data["http"]["shodan"]["query_components"]["base_query"] == "http keep"
