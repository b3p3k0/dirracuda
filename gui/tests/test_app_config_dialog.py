"""
Tests for AppConfigDialog baseline dork controls.

Requires a display for tkinter StringVar creation (run under xvfb-run -a).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class _MainConfigStub:
    def __init__(self, config_path: Path, smbseek_path: Path, db_path: Path):
        self._config_path = config_path
        self._smbseek_path = smbseek_path
        self._db_path = db_path
        self.config = {}

    def get_smbseek_path(self):
        return self._smbseek_path

    def get_config_path(self):
        return self._config_path

    def get_database_path(self):
        return self._db_path


@pytest.fixture(scope="module")
def tk_root():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def _make_dialog(tk_root, config_path: Path):
    from gui.components.app_config_dialog import AppConfigDialog

    main_config = _MainConfigStub(
        config_path=config_path,
        smbseek_path=config_path.parent.parent,
        db_path=config_path.parent.parent / "smbseek.db",
    )
    with patch.object(AppConfigDialog, "_create_dialog"):
        return AppConfigDialog(parent=tk_root, main_config=main_config)


def test_loads_dorks_from_runtime_config(tmp_path, tk_root):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "shodan": {"query_components": {"base_query": "smb custom"}},
                "ftp": {"shodan": {"query_components": {"base_query": "ftp custom"}}},
                "http": {"shodan": {"query_components": {"base_query": "http custom"}}},
            }
        ),
        encoding="utf-8",
    )

    dialog = _make_dialog(tk_root, config_path)

    assert dialog.smb_dork == "smb custom"
    assert dialog.ftp_dork == "ftp custom"
    assert dialog.http_dork == "http custom"
    assert dialog._open_dork_values["smb_dork"] == "smb custom"
    assert dialog._open_dork_values["ftp_dork"] == "ftp custom"
    assert dialog._open_dork_values["http_dork"] == "http custom"


def test_missing_dork_keys_use_defaults(tmp_path, tk_root):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"shodan": {}}), encoding="utf-8")

    dialog = _make_dialog(tk_root, config_path)

    assert dialog.smb_dork == dialog.DORK_DEFAULTS["smb_dork"]
    assert dialog.ftp_dork == dialog.DORK_DEFAULTS["ftp_dork"]
    assert dialog.http_dork == dialog.DORK_DEFAULTS["http_dork"]


def test_dork_default_and_reset_are_per_row(tmp_path, tk_root):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "shodan": {"query_components": {"base_query": "smb custom"}},
                "ftp": {"shodan": {"query_components": {"base_query": "ftp custom"}}},
                "http": {"shodan": {"query_components": {"base_query": "http custom"}}},
            }
        ),
        encoding="utf-8",
    )

    dialog = _make_dialog(tk_root, config_path)
    smb_var = dialog._field_var("smb_dork")
    ftp_var = dialog._field_var("ftp_dork")

    smb_var.set("smb changed")
    ftp_var.set("ftp changed")

    dialog._set_dork_default("smb_dork")
    assert smb_var.get() == dialog.DORK_DEFAULTS["smb_dork"]
    assert ftp_var.get() == "ftp changed"

    dialog._reset_dork_to_open("smb_dork")
    assert smb_var.get() == "smb custom"


def test_blank_dork_query_fails_validation(tmp_path, tk_root):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    dialog = _make_dialog(tk_root, config_path)

    dialog._field_var("http_dork").set("   ")
    dialog._validate_field("http_dork")

    assert dialog.validation_results["http_dork"]["valid"] is False
    assert "cannot be blank" in dialog.validation_results["http_dork"]["message"]


def test_apply_runtime_settings_writes_dork_paths(tmp_path, tk_root):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    dialog = _make_dialog(tk_root, config_path)

    payload = {}
    dialog._apply_runtime_settings(
        payload,
        "API_KEY",
        "/tmp/quarantine",
        "/tmp/wordlist.txt",
        "smb base",
        "ftp base",
        "http base",
    )

    assert payload["shodan"]["api_key"] == "API_KEY"
    assert payload["shodan"]["query_components"]["base_query"] == "smb base"
    assert payload["ftp"]["shodan"]["query_components"]["base_query"] == "ftp base"
    assert payload["http"]["shodan"]["query_components"]["base_query"] == "http base"
