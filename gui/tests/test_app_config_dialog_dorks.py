"""Tests for discovery dork config behavior in AppConfigDialog."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.app_config_dialog import AppConfigDialog


class _StringVar:
    def __init__(self, value: str = "") -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        self._value = value


def _bare_dialog() -> AppConfigDialog:
    dlg = AppConfigDialog.__new__(AppConfigDialog)
    dlg.api_key = ""
    dlg.wordlist_path = ""
    dlg.quarantine_path = "~/.dirracuda/quarantine"

    dlg.smb_dork = dlg.DORK_DEFAULTS["smb_dork"]
    dlg.ftp_dork = dlg.DORK_DEFAULTS["ftp_dork"]
    dlg.http_dork = dlg.DORK_DEFAULTS["http_dork"]
    dlg._open_dork_values = dlg.DORK_DEFAULTS.copy()

    dlg.quarantine_tmpfs_enabled = False
    dlg.quarantine_tmpfs_size_mb = 512
    dlg.clamav_enabled = False
    dlg.clamav_backend = "auto"
    dlg.clamav_timeout = 60
    dlg.clamav_extracted_root = "~/.dirracuda/extracted"
    dlg.clamav_known_bad_subdir = "known_bad"
    dlg.clamav_show_results = True
    dlg.clamav_auto_promote_clean = False

    dlg.smb_dork_var = None
    dlg.ftp_dork_var = None
    dlg.http_dork_var = None
    return dlg


def test_load_runtime_settings_reads_dork_config(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "shodan": {"query_components": {"base_query": "smb custom"}},
                "ftp": {"shodan": {"query_components": {"base_query": "ftp custom"}}},
                "http": {"shodan": {"query_components": {"base_query": "http custom"}}},
            }
        ),
        encoding="utf-8",
    )

    dlg = _bare_dialog()
    dlg._load_runtime_settings_from_config(str(cfg))

    assert dlg.smb_dork == "smb custom"
    assert dlg.ftp_dork == "ftp custom"
    assert dlg.http_dork == "http custom"
    assert dlg._open_dork_values["smb_dork"] == "smb custom"
    assert dlg._open_dork_values["ftp_dork"] == "ftp custom"
    assert dlg._open_dork_values["http_dork"] == "http custom"


def test_missing_dork_config_uses_defaults(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"shodan": {}}), encoding="utf-8")

    dlg = _bare_dialog()
    dlg._load_runtime_settings_from_config(str(cfg))

    assert dlg.smb_dork == dlg.DORK_DEFAULTS["smb_dork"]
    assert dlg.ftp_dork == dlg.DORK_DEFAULTS["ftp_dork"]
    assert dlg.http_dork == dlg.DORK_DEFAULTS["http_dork"]


def test_apply_runtime_settings_writes_dork_paths():
    dlg = _bare_dialog()
    out = {}

    dlg._apply_runtime_settings(
        out,
        api_key="API_KEY",
        quarantine_path="~/.dirracuda/quarantine",
        wordlist_path="",
        dork_settings={
            "smb_dork": "smb base",
            "ftp_dork": "ftp base",
            "http_dork": "http base",
        },
    )

    assert out["shodan"]["api_key"] == "API_KEY"
    assert out["shodan"]["query_components"]["base_query"] == "smb base"
    assert out["ftp"]["shodan"]["query_components"]["base_query"] == "ftp base"
    assert out["http"]["shodan"]["query_components"]["base_query"] == "http base"


def test_dork_default_and_reset_actions():
    dlg = _bare_dialog()
    dlg.smb_dork_var = _StringVar("smb changed")
    dlg._open_dork_values["smb_dork"] = "smb open"

    dlg._set_dork_default("smb_dork")
    assert dlg.smb_dork_var.get() == dlg.DORK_DEFAULTS["smb_dork"]

    dlg._reset_dork_to_open("smb_dork")
    assert dlg.smb_dork_var.get() == "smb open"


def test_validate_dork_query_rejects_blank():
    dlg = _bare_dialog()
    result = dlg._validate_dork_query("   ", "HTTP Base Query")

    assert result["valid"] is False
    assert "cannot be blank" in result["message"]
