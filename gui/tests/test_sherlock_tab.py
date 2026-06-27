"""Headless unit tests for the Sherlock Accessories tab.

Uses __new__ to bypass Tk construction (no display required); widgets and Tk
vars are replaced with mocks/dummies, mirroring test_se_dork_tab.py.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Lightweight impacket stub so GUI modules import cleanly in headless test env.
if "impacket" not in sys.modules:
    _imod = types.ModuleType("impacket")
    _ismb = types.ModuleType("impacket.smb")
    _ismb.SMB2_DIALECT_002 = object()
    _iconn = types.ModuleType("impacket.smbconnection")
    _iconn.SMBConnection = object

    class _SessionError(Exception):
        pass

    _iconn.SessionError = _SessionError
    _imod.smb = _ismb
    sys.modules["impacket"] = _imod
    sys.modules["impacket.smb"] = _ismb
    sys.modules["impacket.smbconnection"] = _iconn

from gui.components.experimental_features import sherlock_tab as mod
from gui.components.experimental_features.sherlock_tab import (
    SherlockTab,
    validate_pattern_fields,
)
from shared.sherlock import (
    DEFAULT_COLORS,
    SHERLOCK_SETTINGS_KEY,
    Severity,
    SherlockPattern,
    builtin_patterns,
    default_settings,
)


class _DummyVar:
    def __init__(self, value=None):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


def _bare_tab(context=None) -> SherlockTab:
    tab = SherlockTab.__new__(SherlockTab)
    tab._context = context or {}
    tab.frame = MagicMock()
    tab._tree = MagicMock()
    tab._tree.selection.return_value = ()
    tab._status_label = MagicMock()
    tab._refresh_table = MagicMock()
    return tab


def _color_vars(high="#ff4d4d", med="#ffa31a", low="#ffff80"):
    return {
        Severity.HIGH: _DummyVar(high),
        Severity.MED: _DummyVar(med),
        Severity.LOW: _DummyVar(low),
    }


# ---------------------------------------------------------------------------
# Pure validator
# ---------------------------------------------------------------------------

def test_validate_pattern_fields():
    assert validate_pattern_fields("*x*") == (True, "")
    ok, msg = validate_pattern_fields("   ")
    assert ok is False and msg
    assert validate_pattern_fields("")[0] is False
    assert validate_pattern_fields(None)[0] is False


# ---------------------------------------------------------------------------
# _load_settings
# ---------------------------------------------------------------------------

def test_load_settings_defaults_without_manager():
    tab = _bare_tab({})
    settings = tab._load_settings()
    assert [p.key for p in settings.patterns] == [
        p.key for p in default_settings().patterns
    ]


def test_load_settings_uses_manager_value():
    sm = MagicMock()
    sm.get_setting.return_value = {"ignore_case": False, "builtin_disabled": [
        builtin_patterns()[0].key
    ]}
    tab = _bare_tab({"settings_manager": sm})
    settings = tab._load_settings()
    assert settings.ignore_case is False
    by_key = {p.key: p for p in settings.patterns}
    assert by_key[builtin_patterns()[0].key].enabled is False
    sm.get_setting.assert_called_once_with(SHERLOCK_SETTINGS_KEY, {})


def test_load_settings_survives_manager_exception():
    sm = MagicMock()
    sm.get_setting.side_effect = RuntimeError("boom")
    tab = _bare_tab({"settings_manager": sm})
    settings = tab._load_settings()
    assert settings.colors == DEFAULT_COLORS


# ---------------------------------------------------------------------------
# _on_save
# ---------------------------------------------------------------------------

def _wire_save(tab, sm, *, colors=None):
    tab._context = {"settings_manager": sm} if sm is not None else {}
    tab._ignore_case_var = _DummyVar(True)
    tab._run_after_probe_var = _DummyVar(False)
    tab._color_vars = colors or _color_vars()
    tab._patterns = list(default_settings().patterns)


def test_save_persists_serialized_dict_and_reports_saved(monkeypatch):
    monkeypatch.setattr(mod, "safe_messagebox", MagicMock())
    sm = MagicMock()
    sm.set_setting.return_value = True
    tab = _bare_tab()
    _wire_save(tab, sm)

    tab._on_save()

    sm.set_setting.assert_called_once()
    key, payload = sm.set_setting.call_args[0]
    assert key == SHERLOCK_SETTINGS_KEY
    assert payload["colors"]["high"] == "#ff4d4d"
    assert payload["ignore_case"] is True
    tab._status_label.configure.assert_called_with(text="Saved.")


def test_save_reports_failure_when_set_setting_false(monkeypatch):
    monkeypatch.setattr(mod, "safe_messagebox", MagicMock())
    sm = MagicMock()
    sm.set_setting.return_value = False
    tab = _bare_tab()
    _wire_save(tab, sm)

    tab._on_save()

    final = tab._status_label.configure.call_args[1].get("text", "")
    assert "fail" in final.lower()
    assert final != "Saved."


def test_save_rejects_invalid_color_without_writing(monkeypatch):
    fake_mb = MagicMock()
    monkeypatch.setattr(mod, "safe_messagebox", fake_mb)
    sm = MagicMock()
    tab = _bare_tab()
    _wire_save(tab, sm, colors=_color_vars(high="not-a-color"))

    tab._on_save()

    fake_mb.showerror.assert_called_once()
    sm.set_setting.assert_not_called()


def test_save_noop_without_manager(monkeypatch):
    monkeypatch.setattr(mod, "safe_messagebox", MagicMock())
    tab = _bare_tab()
    _wire_save(tab, None)
    tab._on_save()  # must not raise
    final = tab._status_label.configure.call_args[1].get("text", "")
    assert "not saved" in final.lower()


# ---------------------------------------------------------------------------
# Pattern table actions
# ---------------------------------------------------------------------------

def test_toggle_flips_selected_pattern():
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    target = tab._patterns[0]
    tab._tree.selection.return_value = (target.key,)

    tab._on_toggle()

    flipped = next(p for p in tab._patterns if p.key == target.key)
    assert flipped.enabled is (not target.enabled)
    tab._refresh_table.assert_called()


def test_toggle_without_selection_sets_status():
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    tab._tree.selection.return_value = ()
    tab._on_toggle()
    tab._status_label.configure.assert_called()
    tab._refresh_table.assert_not_called()


def test_add_appends_custom_pattern(monkeypatch):
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    before = len(tab._patterns)
    monkeypatch.setattr(
        tab,
        "_open_pattern_dialog",
        lambda existing=None: {
            "label": "Acme",
            "category": "Custom",
            "pattern": "*acme*",
            "severity": Severity.HIGH,
            "enabled": True,
        },
    )

    tab._on_add()

    assert len(tab._patterns) == before + 1
    added = tab._patterns[-1]
    assert added.builtin is False
    assert added.pattern == "*acme*"
    assert added.key.startswith("custom_")


def test_add_cancelled_does_nothing(monkeypatch):
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    before = len(tab._patterns)
    monkeypatch.setattr(tab, "_open_pattern_dialog", lambda existing=None: None)
    tab._on_add()
    assert len(tab._patterns) == before


def test_edit_mutates_custom_pattern(monkeypatch):
    tab = _bare_tab()
    custom = SherlockPattern(
        key="custom_1", category="Custom", label="Old", pattern="*old*",
        severity=Severity.LOW, enabled=True, builtin=False,
    )
    tab._patterns = list(default_settings().patterns) + [custom]
    tab._tree.selection.return_value = ("custom_1",)
    monkeypatch.setattr(
        tab,
        "_open_pattern_dialog",
        lambda existing=None: {
            "label": "New", "category": "Custom", "pattern": "*new*",
            "severity": Severity.HIGH, "enabled": False,
        },
    )

    tab._on_edit()

    updated = next(p for p in tab._patterns if p.key == "custom_1")
    assert updated.label == "New"
    assert updated.pattern == "*new*"
    assert updated.severity is Severity.HIGH
    assert updated.enabled is False


def test_edit_builtin_is_readonly(monkeypatch):
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    builtin_key = tab._patterns[0].key
    tab._tree.selection.return_value = (builtin_key,)
    called = []
    monkeypatch.setattr(tab, "_open_pattern_dialog", lambda existing=None: called.append(1))

    tab._on_edit()

    assert called == []
    tab._status_label.configure.assert_called()


def test_delete_removes_custom_only():
    tab = _bare_tab()
    custom = SherlockPattern(
        key="custom_1", category="Custom", label="x", pattern="*x*",
        severity=Severity.LOW, enabled=True, builtin=False,
    )
    tab._patterns = list(default_settings().patterns) + [custom]
    tab._tree.selection.return_value = ("custom_1",)

    tab._on_delete()

    assert all(p.key != "custom_1" for p in tab._patterns)


def test_delete_builtin_blocked():
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    builtin_key = tab._patterns[0].key
    tab._tree.selection.return_value = (builtin_key,)

    tab._on_delete()

    assert any(p.key == builtin_key for p in tab._patterns)
    tab._status_label.configure.assert_called()


def test_restore_builtins_reenables_all_and_keeps_customs():
    tab = _bare_tab()
    disabled_builtins = [
        SherlockPattern(
            key=p.key, category=p.category, label=p.label, pattern=p.pattern,
            severity=p.severity, enabled=False, builtin=True,
        )
        for p in builtin_patterns()
    ]
    custom = SherlockPattern(
        key="custom_1", category="Custom", label="x", pattern="*x*",
        severity=Severity.LOW, enabled=True, builtin=False,
    )
    tab._patterns = disabled_builtins + [custom]

    tab._on_restore_builtins()

    assert all(p.enabled for p in tab._patterns if p.builtin)
    assert any(p.key == "custom_1" for p in tab._patterns)
