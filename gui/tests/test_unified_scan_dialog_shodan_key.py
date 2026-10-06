"""Card 6 (Design B) — sentinel status + type-to-override Shodan API-key field.

The single ``API key`` field is BOTH a non-secret status indicator and an
optional per-run override box. It NEVER contains or displays the stored key and
has no reveal control. Covers: sentinel token on open (never the real key),
plaintext-vs-masked modes, FocusIn/FocusOut transitions, run-scoped
``api_key_override`` wiring (only a real typed override counts), per-protocol
propagation, and the FocusIn status refresh that picks up a modeless Keymaster
Apply without ever exposing the key. Shodan is never invoked; no network.
"""

from __future__ import annotations

import json
import sys
import tkinter as tk
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import gui.components.unified_scan_dialog as unified_scan_dialog
import gui.components.unified_scan_layout as unified_scan_layout
from gui.components.unified_scan_dialog import (
    API_KEY_CONFIGURED,
    API_KEY_NOT_SET,
    UnifiedScanDialog,
)
from gui.components.dashboard_scan import build_protocol_scan_options


# ---------------------------------------------------------------------------
# Lightweight stubs (no Tk) for request-wiring / status-refresh unit tests
# ---------------------------------------------------------------------------

class _Var:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _DialogStub:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


def _make_request_dialog(tmp_path: Path) -> UnifiedScanDialog:
    """A Tk-free dialog carrying only what _build_scan_request reads (SMB selected)."""
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    dlg.shared_concurrency_var = _Var("10")
    dlg.shared_timeout_var = _Var("10")
    dlg.protocol_smb_var = _Var(True)
    dlg.protocol_ftp_var = _Var(False)
    dlg.protocol_http_var = _Var(False)
    dlg.smb_max_results_var = _Var("100")
    dlg.ftp_max_results_var = _Var("100")
    dlg.http_max_results_var = _Var("100")
    dlg.smb_shodan_query_var = _Var("smb base query")
    dlg.ftp_shodan_query_var = _Var("ftp base query")
    dlg.http_shodan_query_var = _Var("http base query")
    dlg.shodan_api_key_var = _Var("")
    dlg.country_var = _Var("")
    dlg.security_mode_var = _Var("cautious")
    dlg.verbose_var = _Var(False)
    dlg.bulk_probe_enabled_var = _Var(False)
    dlg.bulk_extract_enabled_var = _Var(False)
    dlg.skip_indicator_extract_var = _Var(True)
    dlg.allow_insecure_tls_var = _Var(True)
    dlg._settings_manager = None
    dlg.config_path = tmp_path / "config.json"
    dlg.theme = object()
    dlg.dialog = _DialogStub()
    dlg.result = None
    dlg._persist_dialog_state = lambda: None
    dlg._get_all_selected_countries = lambda _manual: ([], "")
    # NOTE: deliberately do NOT set _shodan_api_key_sentinel_active — the request
    # wiring must default (getattr -> True) to "no override" for stub harnesses.
    return dlg


# ---------------------------------------------------------------------------
# Real-Tk harness (mirrors test_unified_scan_dialog_layout.py)
# ---------------------------------------------------------------------------

class _TemplateStoreStub:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def list_templates(self):
        return []

    def load_template(self, _slug):
        return None

    def set_last_used(self, _slug) -> None:
        pass


class _SettingsStub:
    def __init__(self, overrides=None) -> None:
        self.values = dict(overrides or {})

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value) -> None:
        self.values[key] = value


def _write_config(tmp_path: Path, extra: dict | None = None) -> Path:
    config_path = tmp_path / "config.json"
    payload = {
        "discovery": {"max_concurrent_hosts": 10},
        "connection": {"timeout": 10},
    }
    if extra:
        payload.update(extra)
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


def _build_dialog(monkeypatch, tmp_path, extra_config=None, overrides=None):
    monkeypatch.setattr(unified_scan_dialog, "TemplateStore", _TemplateStoreStub)
    config_path = _write_config(tmp_path, extra_config)
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.geometry("1200x900+0+0")
    root.update_idletasks()
    dialog = unified_scan_dialog.UnifiedScanDialog(
        parent=root,
        config_path=str(config_path),
        scan_start_callback=lambda _request: None,
        settings_manager=_SettingsStub(overrides),
    )
    root.update()
    dialog.dialog.update()
    return root, dialog


def _destroy(root, dialog) -> None:
    try:
        dialog.dialog.destroy()
    finally:
        root.destroy()


# ---------------------------------------------------------------------------
# 1 — Sentinel status token on open; the stored key never enters the field
# ---------------------------------------------------------------------------

def test_open_with_key_shows_configured_sentinel_not_the_key(monkeypatch, tmp_path):
    root, dialog = _build_dialog(
        monkeypatch, tmp_path, extra_config={"shodan": {"api_key": "CONFIG_KEY"}}
    )
    try:
        value = dialog.shodan_api_key_var.get()
        assert value == API_KEY_CONFIGURED
        # The real key value is NOT present anywhere in the field/var.
        assert "CONFIG_KEY" not in value
        assert dialog._shodan_opts_frame._shodan_api_key_entry.get() == API_KEY_CONFIGURED
        assert "CONFIG_KEY" not in dialog._shodan_opts_frame._shodan_api_key_entry.get()
        assert dialog._shodan_api_key_sentinel_active is True
    finally:
        _destroy(root, dialog)


def test_open_without_key_shows_not_set_and_builds(monkeypatch, tmp_path):
    # No shodan section at all: get_shodan_api_key() would RAISE; the safe
    # presence check must not, and the dialog must build cleanly.
    root, dialog = _build_dialog(monkeypatch, tmp_path)
    try:
        assert dialog.shodan_api_key_var.get() == API_KEY_NOT_SET
        assert dialog._shodan_api_key_sentinel_active is True
    finally:
        _destroy(root, dialog)


# ---------------------------------------------------------------------------
# 2 — Sentinel is plaintext; EDIT mode is masked; no reveal control
# ---------------------------------------------------------------------------

def test_sentinel_plaintext_and_edit_mode_masked(monkeypatch, tmp_path):
    overrides = {"unified_scan_dialog.provider_shodan": True}
    root, dialog = _build_dialog(
        monkeypatch, tmp_path, extra_config={"shodan": {"api_key": "SECRET"}}, overrides=overrides
    )
    try:
        root.update()
        entry = dialog._shodan_opts_frame._shodan_api_key_entry

        # Sentinel display is plaintext (the token is non-secret).
        assert str(entry.cget("show")) == ""
        assert dialog.shodan_api_key_var.get() == API_KEY_CONFIGURED

        # Entering EDIT mode clears the token and masks the field.
        dialog._on_shodan_key_focus_in(entry)
        assert str(entry.cget("show")) == "*"
        assert dialog.shodan_api_key_var.get() == ""
        assert dialog._shodan_api_key_sentinel_active is False
    finally:
        _destroy(root, dialog)


# ---------------------------------------------------------------------------
# 3 — Request wiring: only a real typed override sets api_key_override
# ---------------------------------------------------------------------------

def test_sentinel_untouched_omits_api_key_override(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.persist_query_budget_state", lambda *_a, **_k: None
    )
    dlg = _make_request_dialog(tmp_path)
    # Stub never sets the flag -> treated as sentinel-active -> no override even
    # though a status token sits in the field.
    dlg.shodan_api_key_var.set(API_KEY_CONFIGURED)

    request = dlg._build_scan_request()

    assert "api_key_override" not in request


def test_typed_override_sets_api_key_override(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.persist_query_budget_state", lambda *_a, **_k: None
    )
    dlg = _make_request_dialog(tmp_path)
    dlg._shodan_api_key_sentinel_active = False
    dlg.shodan_api_key_var.set("  INLINE_KEY  ")  # stripped before submit

    request = dlg._build_scan_request()

    assert request["api_key_override"] == "INLINE_KEY"


def test_sentinel_token_value_in_edit_mode_omits_override(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.persist_query_budget_state", lambda *_a, **_k: None
    )
    dlg = _make_request_dialog(tmp_path)
    dlg._shodan_api_key_sentinel_active = False
    # A literal sentinel token must never be treated as a real override.
    dlg.shodan_api_key_var.set(API_KEY_NOT_SET)

    request = dlg._build_scan_request()

    assert "api_key_override" not in request


def test_blank_value_in_edit_mode_omits_override(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.persist_query_budget_state", lambda *_a, **_k: None
    )
    dlg = _make_request_dialog(tmp_path)
    dlg._shodan_api_key_sentinel_active = False
    dlg.shodan_api_key_var.set("   ")  # whitespace only -> treated as blank

    request = dlg._build_scan_request()

    assert "api_key_override" not in request


# ---------------------------------------------------------------------------
# 4 — FocusOut: empty reverts to sentinel; a typed value stays masked
# ---------------------------------------------------------------------------

def test_focus_out_empty_reverts_to_sentinel(monkeypatch, tmp_path):
    root, dialog = _build_dialog(
        monkeypatch, tmp_path, extra_config={"shodan": {"api_key": "SECRET"}}
    )
    try:
        entry = dialog._shodan_opts_frame._shodan_api_key_entry
        dialog._on_shodan_key_focus_in(entry)  # enter EDIT mode
        dialog.shodan_api_key_var.set("")       # user left it empty

        dialog._on_shodan_key_focus_out(entry)

        assert dialog._shodan_api_key_sentinel_active is True
        assert dialog.shodan_api_key_var.get() == API_KEY_CONFIGURED
        assert str(entry.cget("show")) == ""
    finally:
        _destroy(root, dialog)


def test_focus_out_with_typed_value_stays_masked_edit(monkeypatch, tmp_path):
    root, dialog = _build_dialog(monkeypatch, tmp_path)
    try:
        entry = dialog._shodan_opts_frame._shodan_api_key_entry
        dialog._on_shodan_key_focus_in(entry)  # enter EDIT mode
        dialog.shodan_api_key_var.set("TYPED_KEY")

        dialog._on_shodan_key_focus_out(entry)

        assert dialog._shodan_api_key_sentinel_active is False
        assert dialog.shodan_api_key_var.get() == "TYPED_KEY"
        assert str(entry.cget("show")) == "*"
    finally:
        _destroy(root, dialog)


# ---------------------------------------------------------------------------
# 5 — Status refresh after a modeless Keymaster Apply (never exposes the key)
# ---------------------------------------------------------------------------

def _make_refresh_dialog(tmp_path: Path, stored_key: str | None, *, field: str, sentinel: bool):
    cfg = tmp_path / "config.json"
    payload = {"shodan": {"api_key": stored_key}} if stored_key is not None else {}
    cfg.write_text(json.dumps(payload), encoding="utf-8")
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    dlg.config_path = cfg
    dlg.shodan_api_key_var = _Var(field)
    dlg._shodan_api_key_sentinel_active = sentinel
    return dlg, cfg


def test_refresh_flips_not_set_to_configured_without_exposing_key(tmp_path):
    # Start with no key -> "<not set>", sentinel active. Keymaster writes a key.
    dlg, cfg = _make_refresh_dialog(tmp_path, None, field=API_KEY_NOT_SET, sentinel=True)
    cfg.write_text(json.dumps({"shodan": {"api_key": "APPLIED"}}), encoding="utf-8")

    dlg._refresh_shodan_api_key_field()

    assert dlg.shodan_api_key_var.get() == API_KEY_CONFIGURED
    assert "APPLIED" not in dlg.shodan_api_key_var.get()
    assert dlg._shodan_api_key_sentinel_active is True


def test_refresh_preserves_in_progress_typed_override(tmp_path):
    # User typed an override (sentinel inactive). A Keymaster apply must NOT touch it.
    dlg, cfg = _make_refresh_dialog(tmp_path, "OLD", field="MY_EDIT", sentinel=False)
    cfg.write_text(json.dumps({"shodan": {"api_key": "APPLIED"}}), encoding="utf-8")

    dlg._refresh_shodan_api_key_field()

    assert dlg.shodan_api_key_var.get() == "MY_EDIT"
    assert dlg._shodan_api_key_sentinel_active is False


# ---------------------------------------------------------------------------
# 6 — There is NO reveal/Show control on the key row anymore
# ---------------------------------------------------------------------------

def test_no_reveal_control_exists(monkeypatch, tmp_path):
    root, dialog = _build_dialog(
        monkeypatch, tmp_path, extra_config={"shodan": {"api_key": "SECRET"}}
    )
    try:
        frame = dialog._shodan_opts_frame
        assert not hasattr(frame, "_shodan_api_key_reveal_button")
    finally:
        _destroy(root, dialog)
    # The reveal toggle helper is gone from the layout module entirely.
    assert not hasattr(unified_scan_layout, "_toggle_shodan_key_reveal")


# ---------------------------------------------------------------------------
# 7 — Propagation into every per-protocol scan_options dict (unchanged backend)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("protocol", ["smb", "ftp", "http"])
def test_api_key_override_propagates_into_protocol_options(protocol):
    common = {"api_key_override": "K"}
    assert build_protocol_scan_options(protocol, common)["api_key_override"] == "K"


@pytest.mark.parametrize("protocol", ["smb", "ftp", "http"])
def test_api_key_override_absent_defaults_to_none(protocol):
    assert build_protocol_scan_options(protocol, {})["api_key_override"] is None
