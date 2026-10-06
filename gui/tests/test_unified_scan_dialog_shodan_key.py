"""Card 4 — shared masked Shodan API-key row in the unified scan dialog.

Covers prefill via the non-raising accessor, mask-by-default + transient reveal,
run-scoped ``api_key_override`` request wiring, per-protocol propagation, and the
FocusIn reconcile that picks up a modeless Keymaster Apply. Shodan is never
invoked (dialog-level only); no network.
"""

from __future__ import annotations

import json
import sys
import tkinter as tk
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import gui.components.unified_scan_dialog as unified_scan_dialog
from gui.components.unified_scan_dialog import UnifiedScanDialog
from gui.components.dashboard_scan import build_protocol_scan_options


# ---------------------------------------------------------------------------
# Lightweight stubs (no Tk) for request-wiring / reconcile unit tests
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
# 1 — Prefill via the non-raising accessor
# ---------------------------------------------------------------------------

def test_prefill_populates_api_key_from_config(monkeypatch, tmp_path):
    root, dialog = _build_dialog(
        monkeypatch, tmp_path, extra_config={"shodan": {"api_key": "CONFIG_KEY"}}
    )
    try:
        assert dialog.shodan_api_key_var.get() == "CONFIG_KEY"
        assert dialog._shodan_api_key_default == "CONFIG_KEY"
    finally:
        _destroy(root, dialog)


def test_prefill_empty_when_no_key_and_builds_without_raising(monkeypatch, tmp_path):
    # No shodan section at all: get_shodan_api_key() would RAISE; the safe
    # accessor must not, and the dialog must build cleanly with a blank field.
    root, dialog = _build_dialog(monkeypatch, tmp_path)
    try:
        assert dialog.shodan_api_key_var.get() == ""
        assert dialog._shodan_api_key_default == ""
    finally:
        _destroy(root, dialog)


# ---------------------------------------------------------------------------
# 2 — Masked by default + transient reveal toggle
# ---------------------------------------------------------------------------

def test_api_key_entry_masked_by_default_and_reveal_toggles(monkeypatch, tmp_path):
    overrides = {"unified_scan_dialog.provider_shodan": True}
    root, dialog = _build_dialog(
        monkeypatch, tmp_path, extra_config={"shodan": {"api_key": "SECRET"}}, overrides=overrides
    )
    try:
        root.update()
        frame = dialog._shodan_opts_frame
        entry = frame._shodan_api_key_entry
        reveal = frame._shodan_api_key_reveal_button

        # Masked by default.
        assert str(entry.cget("show")) == "•"

        # Reveal -> visible; label flips to Hide.
        reveal.invoke()
        assert str(entry.cget("show")) == ""
        assert str(reveal.cget("text")) == "Hide"

        # Toggle back -> masked again; label flips to Show.
        reveal.invoke()
        assert str(entry.cget("show")) == "•"
        assert str(reveal.cget("text")) == "Show"
    finally:
        _destroy(root, dialog)


# ---------------------------------------------------------------------------
# 3 — Request wiring: non-blank sets api_key_override; blank omits it
# ---------------------------------------------------------------------------

def test_nonblank_inline_key_sets_api_key_override(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.persist_query_budget_state", lambda *_a, **_k: None
    )
    dlg = _make_request_dialog(tmp_path)
    dlg.shodan_api_key_var.set("  INLINE_KEY  ")  # stripped before submit

    request = dlg._build_scan_request()

    assert request["api_key_override"] == "INLINE_KEY"


def test_blank_inline_key_omits_api_key_override(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gui.components.unified_scan_dialog.persist_query_budget_state", lambda *_a, **_k: None
    )
    dlg = _make_request_dialog(tmp_path)
    dlg.shodan_api_key_var.set("   ")  # whitespace only -> treated as blank

    request = dlg._build_scan_request()

    assert "api_key_override" not in request


# ---------------------------------------------------------------------------
# 4 — Propagation into every per-protocol scan_options dict
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("protocol", ["smb", "ftp", "http"])
def test_api_key_override_propagates_into_protocol_options(protocol):
    common = {"api_key_override": "K"}
    assert build_protocol_scan_options(protocol, common)["api_key_override"] == "K"


@pytest.mark.parametrize("protocol", ["smb", "ftp", "http"])
def test_api_key_override_absent_defaults_to_none(protocol):
    assert build_protocol_scan_options(protocol, {})["api_key_override"] is None


# ---------------------------------------------------------------------------
# 5 — FocusIn reconcile picks up a modeless Keymaster Apply
# ---------------------------------------------------------------------------

def _make_reconcile_dialog(tmp_path: Path, stored_key: str, *, field: str, baseline: str):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"shodan": {"api_key": stored_key}}), encoding="utf-8")
    dlg = UnifiedScanDialog.__new__(UnifiedScanDialog)
    dlg.config_path = cfg
    dlg.shodan_api_key_var = _Var(field)
    dlg._shodan_api_key_default = baseline
    return dlg, cfg


def test_focusin_reconcile_snaps_untouched_field_to_new_key(tmp_path):
    # Field untouched (var == baseline). A Keymaster apply writes a new key.
    dlg, cfg = _make_reconcile_dialog(tmp_path, "OLD", field="OLD", baseline="OLD")
    cfg.write_text(json.dumps({"shodan": {"api_key": "APPLIED"}}), encoding="utf-8")

    dlg._refresh_shodan_api_key_field()

    assert dlg.shodan_api_key_var.get() == "APPLIED"
    assert dlg._shodan_api_key_default == "APPLIED"


def test_focusin_reconcile_preserves_in_progress_edit(tmp_path):
    # Field edited (var != baseline). A Keymaster apply must NOT clobber the edit.
    dlg, cfg = _make_reconcile_dialog(tmp_path, "OLD", field="MY_EDIT", baseline="OLD")
    cfg.write_text(json.dumps({"shodan": {"api_key": "APPLIED"}}), encoding="utf-8")

    dlg._refresh_shodan_api_key_field()

    assert dlg.shodan_api_key_var.get() == "MY_EDIT"
    # Baseline still advances so a later clear-to-baseline reconciles correctly.
    assert dlg._shodan_api_key_default == "APPLIED"
