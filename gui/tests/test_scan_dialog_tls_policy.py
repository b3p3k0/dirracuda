"""C3 — scan dialogs initialize TLS from the canonical default and no longer
persist the retired GUI TLS keys (transient per-run override only)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.unified_scan_dialog import UnifiedScanDialog
from gui.components.http_scan_dialog import HttpScanDialog


class _Var:
    def __init__(self, value=None):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _RecordingSettings:
    def __init__(self):
        self.saved = {}

    def get_setting(self, key, default=None):
        return default

    def set_setting(self, key, value, *args, **kwargs):
        self.saved[key] = value


# ---------------------------------------------------------------------------
# Unified scan dialog
# ---------------------------------------------------------------------------

def test_unified_init_tls_from_resolver_without_settings_manager(monkeypatch, tmp_path):
    """M1: _init_tls_policy_default runs even when _settings_manager is None."""
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.resolve_http_allow_insecure_tls",
        lambda config_path=None: False,
    )
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    dlg._settings_manager = None
    dlg.config_path = tmp_path / "config.json"
    dlg.allow_insecure_tls_var = _Var(True)
    dlg._init_tls_policy_default()
    assert dlg.allow_insecure_tls_var.get() is False


def test_unified_persist_omits_tls_key(monkeypatch):
    monkeypatch.setattr(
        "gui.components.scan_provider_options.persist_searxng_settings",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "gui.components.scan_provider_options.persist_reddit_settings",
        lambda *a, **k: None,
    )
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    sm = _RecordingSettings()
    dlg._settings_manager = sm
    for name in (
        "protocol_smb_var", "protocol_ftp_var", "protocol_http_var",
        "provider_shodan_var", "provider_searxng_var", "provider_reddit_var",
        "verbose_var", "bulk_probe_enabled_var", "bulk_extract_enabled_var",
        "skip_indicator_extract_var", "africa_var", "asia_var", "europe_var",
        "north_america_var", "oceania_var", "south_america_var",
    ):
        setattr(dlg, name, _Var(False))
    dlg.shared_concurrency_var = _Var("10")
    dlg.shared_timeout_var = _Var("10")
    dlg.country_var = _Var("US")
    dlg.security_mode_var = _Var("cautious")
    dlg.allow_insecure_tls_var = _Var(True)

    dlg._persist_dialog_state()

    # Ran to completion (security_mode written) but never persisted the retired key.
    assert "unified_scan_dialog.security_mode" in sm.saved
    assert "unified_scan_dialog.allow_insecure_tls" not in sm.saved


# ---------------------------------------------------------------------------
# HTTP scan dialog
# ---------------------------------------------------------------------------

def test_http_persist_omits_tls_key():
    dlg = HttpScanDialog.__new__(HttpScanDialog)
    sm = _RecordingSettings()
    dlg._settings_manager = sm
    dlg.discovery_concurrency_var = _Var("10")
    dlg.connect_timeout_var = _Var("5")
    dlg.request_timeout_var = _Var("15")
    dlg.api_key_var = _Var("")
    dlg.country_var = _Var("US")
    dlg.verbose_var = _Var(False)
    dlg.bulk_probe_enabled_var = _Var(False)
    dlg.allow_insecure_tls_var = _Var(True)
    for name in (
        "africa_var", "asia_var", "europe_var",
        "north_america_var", "oceania_var", "south_america_var",
    ):
        setattr(dlg, name, _Var(False))

    dlg._persist_dialog_state()

    assert "http_scan_dialog.verbose" in sm.saved
    assert "http_scan_dialog.allow_insecure_tls" not in sm.saved
