"""C3 — App Config owns the canonical HTTP TLS default."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.app_config_dialog import AppConfigDialog, _APP_CONFIG_RUNTIME_SECTIONS


def _bare_dialog() -> AppConfigDialog:
    dlg = AppConfigDialog.__new__(AppConfigDialog)
    dlg.http_tls_allow_insecure = True
    return dlg


def test_http_in_runtime_sections():
    assert "http" in _APP_CONFIG_RUNTIME_SECTIONS


def test_apply_runtime_settings_writes_tls_false():
    dlg = _bare_dialog()
    config_data: dict = {}
    dlg._apply_runtime_settings(
        config_data,
        api_key="",
        quarantine_path="~/.dirracuda/data/quarantine",
        http_tls_allow_insecure=False,
    )
    assert config_data["http"]["verification"]["allow_insecure_tls"] is False


def test_apply_runtime_settings_none_leaves_http_untouched():
    dlg = _bare_dialog()
    config_data: dict = {}
    dlg._apply_runtime_settings(
        config_data,
        api_key="",
        quarantine_path="~/.dirracuda/data/quarantine",
        http_tls_allow_insecure=None,
    )
    assert "http" not in config_data


def test_load_runtime_settings_uses_resolver(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gui.components.app_config_dialog.resolve_http_allow_insecure_tls",
        lambda config_path=None: False,
    )
    dlg = _bare_dialog()
    # Non-existent path: method returns early after the resolver-backed TLS load.
    dlg._load_runtime_settings_from_config(str(tmp_path / "missing.json"))
    assert dlg.http_tls_allow_insecure is False
