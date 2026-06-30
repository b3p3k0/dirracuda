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
    USER_COLOR_KEYS,
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
    # Left category pane is unbuilt in headless tests; refresh_category_list
    # no-ops when _category_tree is None, so _refresh_after_mutation just drives
    # the mocked _refresh_table.
    tab._category_tree = None
    tab._active_category = mod._FACET_ALL
    tab._status_label = MagicMock()
    tab._refresh_table = MagicMock()
    tab._pattern_manager = None
    tab._pattern_manager_dirty = False
    tab._user_color_vars = {key: _DummyVar("") for key in USER_COLOR_KEYS}
    tab._neutral_bg = "#f5f5f5"
    tab._severity_swatches = {sev: MagicMock() for sev in Severity}
    tab._user_swatches = {key: MagicMock() for key in USER_COLOR_KEYS}
    return tab


def _color_vars(high="#ff4d4d", med="#ffa31a", low="#ffff80"):
    return {
        Severity.HIGH: _DummyVar(high),
        Severity.MED: _DummyVar(med),
        Severity.LOW: _DummyVar(low),
    }


def _user_color_vars(**values):
    return {key: _DummyVar(values.get(key, "")) for key in USER_COLOR_KEYS}


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
# Swatch face (C14) - pure, no Tk
# ---------------------------------------------------------------------------

def test_swatch_face_valid_color_shows_color_no_caption():
    tab = _bare_tab()
    assert tab._swatch_face("#ABCDEF", is_user=False) == ("#abcdef", "")
    assert tab._swatch_face("#abcdef", is_user=True) == ("#abcdef", "")


def test_swatch_face_empty_user_shows_none():
    tab = _bare_tab()
    assert tab._swatch_face("", is_user=True) == (tab._neutral_bg, "None")


def test_swatch_face_invalid_renders_defensively_without_raw_text():
    tab = _bare_tab()
    # Invalid internal value never leaks into the caption.
    bg, caption = tab._swatch_face("not-a-color", is_user=False)
    assert bg == tab._neutral_bg
    assert caption == "?"
    assert "not-a-color" not in caption
    # Empty severity (not allowed to clear) is also defensive, not blank-as-None.
    assert tab._swatch_face("", is_user=False) == (tab._neutral_bg, "?")


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

def _wire_save(tab, sm, *, colors=None, user_colors=None):
    tab._context = {"settings_manager": sm} if sm is not None else {}
    tab._ignore_case_var = _DummyVar(True)
    tab._run_after_probe_var = _DummyVar(False)
    tab._color_vars = colors or _color_vars()
    tab._user_color_vars = user_colors if user_colors is not None else _user_color_vars()
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
# User colors
# ---------------------------------------------------------------------------

def test_save_empty_user_colors_persists_blank(monkeypatch):
    monkeypatch.setattr(mod, "safe_messagebox", MagicMock())
    sm = MagicMock()
    sm.set_setting.return_value = True
    tab = _bare_tab()
    _wire_save(tab, sm)

    tab._on_save()

    _key, payload = sm.set_setting.call_args[0]
    assert payload["user_colors"] == {k: "" for k in USER_COLOR_KEYS}


def test_save_valid_user_color_round_trips(monkeypatch):
    monkeypatch.setattr(mod, "safe_messagebox", MagicMock())
    sm = MagicMock()
    sm.set_setting.return_value = True
    tab = _bare_tab()
    _wire_save(tab, sm, user_colors=_user_color_vars(user1="#ABCDEF"))

    tab._on_save()

    _key, payload = sm.set_setting.call_args[0]
    assert payload["user_colors"]["user1"] == "#abcdef"
    assert payload["user_colors"]["user2"] == ""


def test_save_rejects_invalid_user_color_without_writing(monkeypatch):
    fake_mb = MagicMock()
    monkeypatch.setattr(mod, "safe_messagebox", fake_mb)
    sm = MagicMock()
    tab = _bare_tab()
    _wire_save(tab, sm, user_colors=_user_color_vars(user2="nope"))

    tab._on_save()

    fake_mb.showerror.assert_called_once()
    sm.set_setting.assert_not_called()


# ---------------------------------------------------------------------------
# Color tag staging (C10)
# ---------------------------------------------------------------------------

def test_add_carries_color_tag(monkeypatch):
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    monkeypatch.setattr(
        tab,
        "_open_pattern_dialog",
        lambda existing=None: {
            "label": "Acme", "category": "Custom", "pattern": "*acme*",
            "severity": Severity.HIGH, "enabled": True, "color_tag": "user2",
        },
    )

    tab._on_add()

    assert tab._patterns[-1].color_tag == "user2"


def test_edit_updates_color_tag(monkeypatch):
    tab = _bare_tab()
    custom = SherlockPattern(
        key="custom_1", category="Custom", label="Old", pattern="*old*",
        severity=Severity.LOW, enabled=True, builtin=False, color_tag="user1",
    )
    tab._patterns = list(default_settings().patterns) + [custom]
    tab._tree.selection.return_value = ("custom_1",)
    monkeypatch.setattr(
        tab,
        "_open_pattern_dialog",
        lambda existing=None: {
            "label": "New", "category": "Custom", "pattern": "*new*",
            "severity": Severity.HIGH, "enabled": True, "color_tag": "user3",
        },
    )

    tab._on_edit()

    updated = next(p for p in tab._patterns if p.key == "custom_1")
    assert updated.color_tag == "user3"


def test_toggle_preserves_color_tag():
    tab = _bare_tab()
    custom = SherlockPattern(
        key="custom_1", category="Custom", label="x", pattern="*x*",
        severity=Severity.LOW, enabled=True, builtin=False, color_tag="user2",
    )
    tab._patterns = [custom]
    tab._tree.selection.return_value = ("custom_1",)

    tab._on_toggle()

    flipped = next(p for p in tab._patterns if p.key == "custom_1")
    assert flipped.enabled is False
    assert flipped.color_tag == "user2"


def test_refresh_table_renders_user_tag_cell():
    tab = SherlockTab.__new__(SherlockTab)
    tab._tree = MagicMock()
    tab._tree.get_children.return_value = ()
    custom = SherlockPattern(
        key="custom_1", category="Custom", label="x", pattern="*x*",
        severity=Severity.HIGH, enabled=True, builtin=False, color_tag="user2",
    )
    builtin = builtin_patterns()[0]
    tab._patterns = [custom, builtin]

    tab._refresh_table()

    rows = {c.kwargs["iid"]: c.kwargs["values"] for c in tab._tree.insert.call_args_list}
    # values: enabled, severity, user_tag, category, label, pattern, type
    assert rows["custom_1"][2] == "User2"
    assert rows[builtin.key][2] == ""


# ---------------------------------------------------------------------------
# Nested dialog parent resolver (C10)
# ---------------------------------------------------------------------------

def test_active_dialog_parent_prefers_open_manager():
    tab = _bare_tab()
    mgr = MagicMock()
    mgr.winfo_exists.return_value = True
    tab._pattern_manager = mgr
    assert tab._active_dialog_parent() is mgr


def test_active_dialog_parent_falls_back_when_manager_closed():
    tab = _bare_tab()
    toplevel = MagicMock()
    tab.frame.winfo_toplevel.return_value = toplevel
    # No manager.
    tab._pattern_manager = None
    assert tab._active_dialog_parent() is toplevel
    # Destroyed manager.
    mgr = MagicMock()
    mgr.winfo_exists.return_value = False
    tab._pattern_manager = mgr
    assert tab._active_dialog_parent() is toplevel


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


def test_edit_builtin_creates_new_custom(monkeypatch):
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    builtin = tab._patterns[0]
    before = len(tab._patterns)
    tab._tree.selection.return_value = (builtin.key,)
    monkeypatch.setattr(
        tab,
        "_open_pattern_dialog",
        lambda existing=None, prefill=None: {
            "label": builtin.label, "category": builtin.category,
            "pattern": builtin.pattern, "severity": builtin.severity,
            "enabled": True, "color_tag": "none",
        },
    )

    tab._on_edit()

    # The built-in row is untouched and a new custom was appended.
    assert any(p.key == builtin.key and p.builtin for p in tab._patterns)
    assert len(tab._patterns) == before + 1
    added = tab._patterns[-1]
    assert added.builtin is False
    assert added.key.startswith("custom_")
    assert added.key != builtin.key
    assert added.pattern == builtin.pattern


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


def test_delete_removes_builtin():
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    builtin_key = tab._patterns[0].key
    tab._tree.selection.return_value = (builtin_key,)

    tab._on_delete()

    assert all(p.key != builtin_key for p in tab._patterns)


def test_copy_builtin_creates_new_custom(monkeypatch):
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    builtin = tab._patterns[0]
    before = len(tab._patterns)
    tab._tree.selection.return_value = (builtin.key,)
    monkeypatch.setattr(
        tab,
        "_open_pattern_dialog",
        lambda existing=None, prefill=None: {
            "label": prefill.label, "category": prefill.category,
            "pattern": prefill.pattern, "severity": prefill.severity,
            "enabled": prefill.enabled, "color_tag": "none",
        },
    )

    tab._on_copy()

    assert any(p.key == builtin.key and p.builtin for p in tab._patterns)
    assert len(tab._patterns) == before + 1
    added = tab._patterns[-1]
    assert added.builtin is False
    assert added.key.startswith("custom_")
    assert added.pattern == builtin.pattern


def test_copy_custom_creates_new_custom(monkeypatch):
    tab = _bare_tab()
    custom = SherlockPattern(
        key="custom_1", category="Custom", label="x", pattern="*x*",
        severity=Severity.MED, enabled=True, builtin=False, color_tag="user1",
    )
    tab._patterns = [custom]
    tab._tree.selection.return_value = ("custom_1",)
    monkeypatch.setattr(
        tab,
        "_open_pattern_dialog",
        lambda existing=None, prefill=None: {
            "label": prefill.label, "category": prefill.category,
            "pattern": prefill.pattern, "severity": prefill.severity,
            "enabled": prefill.enabled, "color_tag": prefill.color_tag,
        },
    )

    tab._on_copy()

    assert len(tab._patterns) == 2
    added = tab._patterns[-1]
    assert added.builtin is False
    assert added.key != "custom_1"
    assert added.key.startswith("custom_")
    assert added.color_tag == "user1"


def test_copy_without_selection_sets_status():
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    before = len(tab._patterns)
    tab._tree.selection.return_value = ()

    tab._on_copy()

    assert len(tab._patterns) == before
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


def test_restore_builtins_readds_deleted_and_keeps_customs():
    tab = _bare_tab()
    full = builtin_patterns()
    deleted_key = full[0].key
    custom = SherlockPattern(
        key="custom_1", category="Custom", label="x", pattern="*x*",
        severity=Severity.LOW, enabled=True, builtin=False,
    )
    # Simulate a previously deleted built-in: drop it from the staged list.
    tab._patterns = [p for p in full if p.key != deleted_key] + [custom]

    tab._on_restore_builtins()

    assert any(p.key == deleted_key and p.builtin for p in tab._patterns)
    assert len([p for p in tab._patterns if p.builtin]) == len(full)
    assert any(p.key == "custom_1" for p in tab._patterns)


# ---------------------------------------------------------------------------
# C16.5: Pattern Manager dirty tracking + Save & Close / Close warning
# ---------------------------------------------------------------------------


def _dialog_result(**over):
    base = {
        "label": "Acme", "category": "Custom", "pattern": "*acme*",
        "severity": Severity.HIGH, "enabled": True, "color_tag": "none",
    }
    base.update(over)
    return base


def test_add_marks_dirty(monkeypatch):
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    monkeypatch.setattr(tab, "_open_pattern_dialog", lambda existing=None: _dialog_result())
    tab._on_add()
    assert tab._pattern_manager_dirty is True


def test_add_cancelled_leaves_clean(monkeypatch):
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    monkeypatch.setattr(tab, "_open_pattern_dialog", lambda existing=None: None)
    tab._on_add()
    assert tab._pattern_manager_dirty is False


def test_edit_custom_marks_dirty(monkeypatch):
    tab = _bare_tab()
    custom = SherlockPattern(
        key="custom_1", category="Custom", label="Old", pattern="*old*",
        severity=Severity.LOW, enabled=True, builtin=False,
    )
    tab._patterns = [custom]
    tab._tree.selection.return_value = ("custom_1",)
    monkeypatch.setattr(
        tab, "_open_pattern_dialog",
        lambda existing=None: _dialog_result(label="New", pattern="*new*"),
    )
    tab._on_edit()
    assert tab._pattern_manager_dirty is True


def test_copy_marks_dirty(monkeypatch):
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    tab._tree.selection.return_value = (tab._patterns[0].key,)
    monkeypatch.setattr(
        tab, "_open_pattern_dialog",
        lambda existing=None, prefill=None: _dialog_result(),
    )
    tab._on_copy()
    assert tab._pattern_manager_dirty is True


def test_delete_marks_dirty():
    tab = _bare_tab()
    custom = SherlockPattern(
        key="custom_1", category="Custom", label="x", pattern="*x*",
        severity=Severity.LOW, enabled=True, builtin=False,
    )
    tab._patterns = [custom]
    tab._tree.selection.return_value = ("custom_1",)
    tab._on_delete()
    assert tab._pattern_manager_dirty is True


def test_delete_without_selection_leaves_clean():
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    tab._tree.selection.return_value = ()
    tab._on_delete()
    assert tab._pattern_manager_dirty is False


def test_toggle_marks_dirty():
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    tab._tree.selection.return_value = (tab._patterns[0].key,)
    tab._on_toggle()
    assert tab._pattern_manager_dirty is True


def test_restore_builtins_marks_dirty():
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    tab._on_restore_builtins()
    assert tab._pattern_manager_dirty is True


def test_on_save_returns_true_and_clears_dirty(monkeypatch):
    monkeypatch.setattr(mod, "safe_messagebox", MagicMock())
    sm = MagicMock()
    sm.set_setting.return_value = True
    tab = _bare_tab()
    _wire_save(tab, sm)
    tab._pattern_manager_dirty = True

    assert tab._on_save() is True
    assert tab._pattern_manager_dirty is False


def test_on_save_invalid_color_returns_false_keeps_dirty(monkeypatch):
    monkeypatch.setattr(mod, "safe_messagebox", MagicMock())
    sm = MagicMock()
    tab = _bare_tab()
    _wire_save(tab, sm, colors=_color_vars(high="not-a-color"))
    tab._pattern_manager_dirty = True

    assert tab._on_save() is False
    assert tab._pattern_manager_dirty is True
    sm.set_setting.assert_not_called()


def test_on_save_set_setting_false_returns_false_keeps_dirty(monkeypatch):
    monkeypatch.setattr(mod, "safe_messagebox", MagicMock())
    sm = MagicMock()
    sm.set_setting.return_value = False
    tab = _bare_tab()
    _wire_save(tab, sm)
    tab._pattern_manager_dirty = True

    assert tab._on_save() is False
    assert tab._pattern_manager_dirty is True


def test_on_save_without_manager_returns_false(monkeypatch):
    monkeypatch.setattr(mod, "safe_messagebox", MagicMock())
    tab = _bare_tab()
    _wire_save(tab, None)
    assert tab._on_save() is False


def test_close_manager_clean_closes_without_prompt(monkeypatch):
    fake_mb = MagicMock()
    monkeypatch.setattr(mod, "safe_messagebox", fake_mb)
    tab = _bare_tab()
    dialog = MagicMock()
    tab._pattern_manager = dialog
    tab._pattern_manager_dirty = False

    tab._close_manager()

    fake_mb.askyesno.assert_not_called()
    dialog.destroy.assert_called_once()
    assert tab._pattern_manager is None


def test_close_manager_dirty_cancel_stays_open(monkeypatch):
    fake_mb = MagicMock()
    fake_mb.askyesno.return_value = False
    monkeypatch.setattr(mod, "safe_messagebox", fake_mb)
    tab = _bare_tab()
    dialog = MagicMock()
    tab._pattern_manager = dialog
    tab._pattern_manager_dirty = True

    tab._close_manager()

    fake_mb.askyesno.assert_called_once()
    dialog.destroy.assert_not_called()
    assert tab._pattern_manager is dialog


def test_close_manager_dirty_confirm_closes_but_flag_persists(monkeypatch):
    # Reviewer's hole: a confirmed unsaved close must NOT clear the dirty flag,
    # so reopening the manager and closing again warns until a successful save.
    fake_mb = MagicMock()
    fake_mb.askyesno.return_value = True
    monkeypatch.setattr(mod, "safe_messagebox", fake_mb)
    tab = _bare_tab()
    dialog = MagicMock()
    tab._pattern_manager = dialog
    tab._pattern_manager_dirty = True

    tab._close_manager()

    dialog.destroy.assert_called_once()
    assert tab._pattern_manager is None
    assert tab._pattern_manager_dirty is True

    # Simulate reopening the manager: the next close still prompts.
    dialog2 = MagicMock()
    tab._pattern_manager = dialog2
    fake_mb.askyesno.reset_mock()
    fake_mb.askyesno.return_value = False
    tab._close_manager()
    fake_mb.askyesno.assert_called_once()
    dialog2.destroy.assert_not_called()


def test_save_and_close_success_tears_down(monkeypatch):
    tab = _bare_tab()
    dialog = MagicMock()
    tab._pattern_manager = dialog
    monkeypatch.setattr(tab, "_on_save", lambda: True)

    tab._save_and_close_manager()

    dialog.destroy.assert_called_once()
    assert tab._pattern_manager is None


def test_save_and_close_failure_stays_open(monkeypatch):
    tab = _bare_tab()
    dialog = MagicMock()
    tab._pattern_manager = dialog
    monkeypatch.setattr(tab, "_on_save", lambda: False)

    tab._save_and_close_manager()

    dialog.destroy.assert_not_called()
    assert tab._pattern_manager is dialog


# ---------------------------------------------------------------------------
# Real-Tk: pattern manager structure + nested Add/Edit grab restoration
# ---------------------------------------------------------------------------

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402


@pytest.fixture(scope="module")
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for Tk")
    root.withdraw()
    yield root
    root.destroy()


def _button_texts(widget):
    out = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Button):
            out.append(child.cget("text"))
        out.extend(_button_texts(child))
    return out


def test_pattern_manager_structure_and_nested_grab(tk_root):
    tab = SherlockTab(tk_root, {})
    captured = {}

    def when_manager_open():
        mgr = tab._pattern_manager
        tree = tab._tree
        captured["mgr_path"] = str(mgr)
        captured["columns"] = tuple(tree["columns"])
        captured["headings"] = tuple(
            tree.heading(c, "text") for c in tree["columns"]
        )
        captured["buttons"] = _button_texts(mgr)

        def when_add_open():
            add = tk_root.grab_current()
            captured["add_grabbed"] = add is not None
            captured["add_transient"] = str(add.transient()) if add else None
            if add is not None:
                add.destroy()  # ends the nested wait_window

        tk_root.after(50, when_add_open)
        tab._on_add()  # blocks until the Add dialog closes
        # After the child closes, the manager must have re-grabbed.
        captured["grab_after"] = mgr.grab_current()
        mgr.destroy()  # ends the manager wait_window

    tk_root.after(50, when_manager_open)
    tab._open_pattern_manager()  # blocks until the manager closes

    assert "user_tag" in captured["columns"]
    assert "User Tag" in captured["headings"]
    for label in ("Add", "Edit", "Enable/Disable", "Delete", "Copy", "Restore Built-ins", "Close"):
        assert label in captured["buttons"]
    assert captured["add_grabbed"] is True
    # Add dialog was transient to the manager, not the main window.
    assert captured["add_transient"] == captured["mgr_path"]
    assert captured["grab_after"] is not None


def test_pattern_manager_save_and_close_button_order(tk_root):
    tab = SherlockTab(tk_root, {})
    captured = {}

    def when_open():
        mgr = tab._pattern_manager
        captured["buttons"] = _button_texts(mgr)
        mgr.destroy()  # ends the manager wait_window

    tk_root.after(50, when_open)
    tab._open_pattern_manager()  # blocks until the manager closes

    texts = captured["buttons"]
    assert "Save & Close" in texts
    # Save & Close sits between Restore Built-ins and Close.
    assert texts.index("Restore Built-ins") < texts.index("Save & Close") < texts.index("Close")


# ---------------------------------------------------------------------------
# Real-Tk: color swatches (C14)
# ---------------------------------------------------------------------------

def _all_widgets(widget):
    out = [widget]
    for child in widget.winfo_children():
        out.extend(_all_widgets(child))
    return out


def test_color_rows_have_no_dotdotdot_buttons_or_entries(tk_root):
    tab = SherlockTab(tk_root, {})
    assert "..." not in _button_texts(tab.frame)
    # No visible hex entries in the color rows.
    assert not any(isinstance(w, tk.Entry) for w in _all_widgets(tab.frame))


def test_severity_swatch_paints_initial_color(tk_root):
    tab = SherlockTab(tk_root, {})
    swatch = tab._severity_swatches[Severity.HIGH]
    assert str(swatch.cget("bg")).lower() == tab._color_vars[Severity.HIGH].get().lower()
    assert swatch.cget("text") == ""


def test_swatch_click_sets_var_and_repaints(tk_root, monkeypatch):
    tab = SherlockTab(tk_root, {})
    from tkinter import colorchooser

    monkeypatch.setattr(
        colorchooser, "askcolor", lambda *a, **kw: ((18, 52, 86), "#123456")
    )
    tab._pick_color(Severity.MED)

    assert tab._color_vars[Severity.MED].get() == "#123456"
    swatch = tab._severity_swatches[Severity.MED]
    assert str(swatch.cget("bg")).lower() == "#123456"
    assert swatch.cget("text") == ""


def test_swatch_click_cancel_leaves_var_unchanged(tk_root, monkeypatch):
    tab = SherlockTab(tk_root, {})
    from tkinter import colorchooser

    before = tab._color_vars[Severity.LOW].get()
    monkeypatch.setattr(colorchooser, "askcolor", lambda *a, **kw: (None, None))
    tab._pick_color(Severity.LOW)

    assert tab._color_vars[Severity.LOW].get() == before


def test_user_clear_resets_to_empty_and_shows_none(tk_root, monkeypatch):
    tab = SherlockTab(tk_root, {})
    from tkinter import colorchooser

    monkeypatch.setattr(
        colorchooser, "askcolor", lambda *a, **kw: ((171, 205, 239), "#abcdef")
    )
    tab._pick_user_color("user1")
    assert tab._user_color_vars["user1"].get() == "#abcdef"

    tab._clear_user_color("user1")
    assert tab._user_color_vars["user1"].get() == ""
    assert tab._user_swatches["user1"].cget("text") == "None"


def test_user_rows_have_clear_buttons(tk_root):
    tab = SherlockTab(tk_root, {})
    assert _button_texts(tab.frame).count("Clear") == len(USER_COLOR_KEYS)


# ---------------------------------------------------------------------------
# Real-Tk: Add/Edit Category combobox (C16)
# ---------------------------------------------------------------------------


def _category_combobox(widget):
    """Return the editable (state=normal) combobox; sev/tag are readonly."""
    for w in _all_widgets(widget):
        if isinstance(w, ttk.Combobox) and str(w.cget("state")) == "normal":
            return w
    return None


def _find_button(widget, text):
    for w in _all_widgets(widget):
        if isinstance(w, tk.Button) and w.cget("text") == text:
            return w
    return None


def _seed_pattern(category="Seed"):
    return SherlockPattern(
        key="src", category=category, label="L", pattern="*ok*",
        severity=Severity.MED, enabled=True, builtin=False,
    )


def test_category_combobox_lists_staged_categories(tk_root):
    tab = SherlockTab(tk_root, {})
    custom = SherlockPattern(
        key="custom_acme", category="Acme", label="A", pattern="*a*",
        severity=Severity.MED, enabled=True, builtin=False,
    )
    tab._patterns = list(default_settings().patterns) + [custom]
    captured = {}

    def when_open():
        dlg = None
        try:
            dlg = tk_root.grab_current()
            combo = _category_combobox(dlg)
            captured["state"] = str(combo.cget("state"))
            captured["values"] = tuple(combo.cget("values"))
        except Exception as exc:  # noqa: BLE001
            captured["error"] = exc
        finally:
            if dlg is not None and dlg.winfo_exists():
                dlg.destroy()

    tk_root.after(50, when_open)
    tab._open_pattern_dialog()

    assert "error" not in captured
    # Editable so analysts can type a brand-new category.
    assert captured["state"] == "normal"
    # Newly staged custom category appears without a main Save/reopen, alongside
    # built-in categories and the always-present "Custom".
    assert "Acme" in captured["values"]
    assert "Custom" in captured["values"]
    assert "Credentials" in captured["values"]


def test_pattern_dialog_grabs_after_fields_are_built(tk_root, monkeypatch):
    tk_root.deiconify()
    tk_root.update()
    tab = SherlockTab(tk_root, {})
    observed = {}
    original_grab_set = tk.Misc.grab_set

    def guarded_grab_set(widget):
        widgets = _all_widgets(widget)
        observed["entries"] = sum(isinstance(w, tk.Entry) for w in widgets)
        observed["comboboxes"] = sum(isinstance(w, ttk.Combobox) for w in widgets)
        if observed["entries"] < 2 or observed["comboboxes"] < 3:
            raise tk.TclError("grab failed before dialog fields were built")
        return original_grab_set(widget)

    def when_open():
        for child in tk_root.winfo_children():
            if isinstance(child, tk.Toplevel) and child.winfo_exists():
                child.destroy()
                break

    monkeypatch.setattr(tk.Misc, "grab_set", guarded_grab_set)
    try:
        tk_root.after(50, when_open)
        result = tab._open_pattern_dialog(prefill=_seed_pattern())
    finally:
        tk_root.withdraw()

    assert result is None
    assert observed["entries"] >= 2
    assert observed["comboboxes"] >= 3


def test_category_combobox_existing_selection_saved(tk_root):
    tab = SherlockTab(tk_root, {})
    tab._patterns = list(default_settings().patterns)
    captured = {}

    def when_open():
        dlg = None
        try:
            dlg = tk_root.grab_current()
            _category_combobox(dlg).set("Credentials")
            _find_button(dlg, "OK").invoke()  # destroys the dialog
        except Exception as exc:  # noqa: BLE001
            captured["error"] = exc
            if dlg is not None and dlg.winfo_exists():
                dlg.destroy()

    tk_root.after(50, when_open)
    result = tab._open_pattern_dialog(prefill=_seed_pattern())

    assert "error" not in captured
    assert result is not None
    assert result["category"] == "Credentials"


def test_category_combobox_typed_new_value_saved(tk_root):
    tab = SherlockTab(tk_root, {})
    tab._patterns = list(default_settings().patterns)
    captured = {}

    def when_open():
        dlg = None
        try:
            dlg = tk_root.grab_current()
            _category_combobox(dlg).set("Brand New Cat")
            _find_button(dlg, "OK").invoke()
        except Exception as exc:  # noqa: BLE001
            captured["error"] = exc
            if dlg is not None and dlg.winfo_exists():
                dlg.destroy()

    tk_root.after(50, when_open)
    result = tab._open_pattern_dialog(prefill=_seed_pattern())

    assert "error" not in captured
    assert result is not None
    assert result["category"] == "Brand New Cat"


def test_category_combobox_blank_saves_as_custom(tk_root):
    tab = SherlockTab(tk_root, {})
    tab._patterns = list(default_settings().patterns)
    captured = {}

    def when_open():
        dlg = None
        try:
            dlg = tk_root.grab_current()
            _category_combobox(dlg).set("   ")
            _find_button(dlg, "OK").invoke()
        except Exception as exc:  # noqa: BLE001
            captured["error"] = exc
            if dlg is not None and dlg.winfo_exists():
                dlg.destroy()

    tk_root.after(50, when_open)
    result = tab._open_pattern_dialog(prefill=_seed_pattern())

    assert "error" not in captured
    assert result is not None
    assert result["category"] == "Custom"


# ---------------------------------------------------------------------------
# C17: multi-select + row double-click
# ---------------------------------------------------------------------------


def _custom(key, *, enabled=True, label="x", pattern="*x*"):
    return SherlockPattern(
        key=key, category="Custom", label=label, pattern=pattern,
        severity=Severity.LOW, enabled=enabled, builtin=False,
    )


@pytest.mark.gui_smoke
def test_pattern_table_uses_extended_selectmode():
    import tkinter as tk
    from gui.utils.style import get_theme

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    try:
        root.withdraw()
        tab = SherlockTab.__new__(SherlockTab)
        tab._theme = get_theme()
        tab._tree = None
        tab._build_table(root)
        assert str(tab._tree.cget("selectmode")) == "extended"
    finally:
        root.destroy()


def test_selected_patterns_returns_table_order():
    tab = _bare_tab()
    # Distinct labels -> three single-member value groups (iid == pattern key).
    a = _custom("custom_a", label="A")
    b = _custom("custom_b", label="B")
    c = _custom("custom_c", label="C")
    tab._patterns = [a, b, c]
    # Selection reported out of table order; helper restores table order.
    tab._tree.selection.return_value = ("custom_c", "custom_a")
    selected = tab._selected_patterns()
    assert [p.key for p in selected] == ["custom_a", "custom_c"]


def test_selected_patterns_empty_selection():
    tab = _bare_tab()
    tab._patterns = [_custom("custom_a")]
    tab._tree.selection.return_value = ()
    assert tab._selected_patterns() == []


def test_toggle_batch_flips_mixed_states_individually():
    tab = _bare_tab()
    # Distinct labels keep these as three separate single-member value groups.
    on = _custom("custom_on", enabled=True, label="On")
    off = _custom("custom_off", enabled=False, label="Off")
    untouched = _custom("custom_keep", enabled=True, label="Keep")
    tab._patterns = [on, off, untouched]
    tab._tree.selection.return_value = ("custom_on", "custom_off")

    tab._on_toggle()

    by_key = {p.key: p for p in tab._patterns}
    assert by_key["custom_on"].enabled is False
    assert by_key["custom_off"].enabled is True
    assert by_key["custom_keep"].enabled is True  # not selected, unchanged
    assert tab._pattern_manager_dirty is True
    tab._refresh_table.assert_called()
    tab._tree.selection_set.assert_called_once_with(["custom_on", "custom_off"])


def test_delete_batch_removes_builtin_and_custom():
    tab = _bare_tab()
    builtins = list(builtin_patterns())
    builtin_key = builtins[0].key
    custom = _custom("custom_1")
    tab._patterns = builtins + [custom]
    before = len(tab._patterns)
    tab._tree.selection.return_value = (builtin_key, "custom_1")

    tab._on_delete()

    keys = {p.key for p in tab._patterns}
    assert builtin_key not in keys
    assert "custom_1" not in keys
    assert len(tab._patterns) == before - 2
    assert tab._pattern_manager_dirty is True


def test_edit_multiple_selected_sets_status_no_change(monkeypatch):
    tab = _bare_tab()
    # Distinct labels -> two value groups, a genuine multi-group selection.
    tab._patterns = [_custom("custom_1", label="One"), _custom("custom_2", label="Two")]
    snapshot = list(tab._patterns)
    tab._tree.selection.return_value = ("custom_1", "custom_2")
    monkeypatch.setattr(
        tab, "_open_pattern_dialog",
        lambda *a, **k: pytest.fail("dialog must not open for multi-select Edit"),
    )

    tab._on_edit()

    assert tab._patterns == snapshot
    assert tab._pattern_manager_dirty is False
    tab._status_label.configure.assert_called()


def test_copy_multiple_selected_sets_status_no_change(monkeypatch):
    tab = _bare_tab()
    # Distinct labels -> two value groups, so this is a genuine multi-group select.
    tab._patterns = [_custom("custom_1", label="One"), _custom("custom_2", label="Two")]
    before = len(tab._patterns)
    tab._tree.selection.return_value = ("custom_1", "custom_2")
    monkeypatch.setattr(
        tab, "_open_pattern_dialog",
        lambda *a, **k: pytest.fail("dialog must not open for multi-select Copy"),
    )

    tab._on_copy()

    assert len(tab._patterns) == before
    assert tab._pattern_manager_dirty is False
    tab._status_label.configure.assert_called()


def test_double_click_custom_edits_in_place(monkeypatch):
    tab = _bare_tab()
    custom = SherlockPattern(
        key="custom_1", category="Custom", label="Old", pattern="*old*",
        severity=Severity.LOW, enabled=True, builtin=False,
    )
    tab._patterns = [custom]
    tab._tree.identify_row.return_value = "custom_1"
    tab._tree.selection.return_value = ("custom_1",)
    tab._tree.after_idle.side_effect = lambda cb: cb()
    monkeypatch.setattr(
        tab, "_open_pattern_dialog",
        lambda existing=None: _dialog_result(label="New", pattern="*new*"),
    )

    tab._on_row_double_click(types.SimpleNamespace(y=10))

    # Double-click selects the clicked row before routing to Edit.
    tab._tree.selection_set.assert_any_call("custom_1")
    updated = next(p for p in tab._patterns if p.key == "custom_1")
    assert updated.label == "New"
    assert updated.builtin is False
    assert tab._pattern_manager_dirty is True


def test_double_click_builtin_creates_copy(monkeypatch):
    tab = _bare_tab()
    tab._patterns = list(builtin_patterns())
    builtin = tab._patterns[0]
    before = len(tab._patterns)
    tab._tree.identify_row.return_value = builtin.key
    tab._tree.selection.return_value = (builtin.key,)
    tab._tree.after_idle.side_effect = lambda cb: cb()
    monkeypatch.setattr(
        tab, "_open_pattern_dialog",
        lambda existing=None, prefill=None: _dialog_result(
            label=prefill.label, category=prefill.category,
            pattern=prefill.pattern, severity=prefill.severity,
        ),
    )

    tab._on_row_double_click(types.SimpleNamespace(y=10))

    assert any(p.key == builtin.key and p.builtin for p in tab._patterns)
    assert len(tab._patterns) == before + 1
    assert tab._patterns[-1].builtin is False
    assert tab._pattern_manager_dirty is True


def test_double_click_empty_area_no_change(monkeypatch):
    tab = _bare_tab()
    tab._patterns = [_custom("custom_1")]
    snapshot = list(tab._patterns)
    tab._tree.identify_row.return_value = ""
    monkeypatch.setattr(
        tab, "_open_pattern_dialog",
        lambda *a, **k: pytest.fail("dialog must not open on empty double-click"),
    )

    tab._on_row_double_click(types.SimpleNamespace(y=999))

    assert tab._patterns == snapshot
    assert tab._pattern_manager_dirty is False
    tab._tree.selection_set.assert_not_called()


# ---------------------------------------------------------------------------
# C18 - Search and faceted filters
# ---------------------------------------------------------------------------

def _tagged(key, *, color_tag="none", category="Custom", severity=Severity.LOW,
            enabled=True, builtin=False, label="x", pattern="*x*"):
    return SherlockPattern(
        key=key, category=category, label=label, pattern=pattern,
        severity=severity, enabled=enabled, builtin=builtin, color_tag=color_tag,
    )


def test_user_tag_facet_choices_none_only():
    assert mod._user_tag_facet_choices([_tagged("a")]) == ["None"]


def test_user_tag_facet_choices_present_in_canonical_order():
    pats = [_tagged("a", color_tag="user2"), _tagged("b"), _tagged("c", color_tag="user1")]
    assert mod._user_tag_facet_choices(pats) == ["None", "User1", "User2"]


def test_user_tag_facet_choices_all_tagged_omits_none():
    assert mod._user_tag_facet_choices([_tagged("a", color_tag="user1")]) == ["User1"]


def _sample(**over):
    base = dict(
        key="k", category="Credentials", label="Password files",
        pattern="*password*", severity=Severity.HIGH, enabled=True,
        builtin=True, color_tag="none",
    )
    base.update(over)
    return SherlockPattern(**base)


_ALL = mod._FACET_ALL


def _matches(pat, *, search="", category=_ALL, severity=_ALL, user_tag=_ALL, enabled=_ALL):
    return mod._pattern_matches_filters(
        pat, search=search, category=category, severity=severity,
        user_tag=user_tag, enabled=enabled,
    )


def test_filter_search_matches_each_field_case_insensitively():
    p = _sample(color_tag="user1", builtin=True)
    assert _matches(p, search="PASSWORD")          # label/pattern
    assert _matches(p, search="cred")              # category
    assert _matches(p, search="high")              # severity display name
    assert _matches(p, search="user1")             # user-tag label/token
    assert _matches(p, search="built")             # type Built-in
    assert _matches(_sample(builtin=False), search="custom")  # type Custom
    assert not _matches(p, search="nonexistent-token")


def test_filter_empty_search_and_all_facets_match_everything():
    assert _matches(_sample())
    assert _matches(_sample(enabled=False, color_tag="user3", builtin=False))


def test_filter_category_facet_exact_case_insensitive():
    p = _sample(category="Credentials")
    assert _matches(p, category="credentials")
    assert not _matches(p, category="Private keys")


def test_filter_severity_facet_exact():
    assert _matches(_sample(severity=Severity.HIGH), severity="HIGH")
    assert not _matches(_sample(severity=Severity.HIGH), severity="LOW")


def test_filter_user_tag_facet_exact():
    assert _matches(_sample(color_tag="none"), user_tag="None")
    assert _matches(_sample(color_tag="user2"), user_tag="User2")
    assert not _matches(_sample(color_tag="user2"), user_tag="User1")


def test_filter_enabled_facet_exact():
    assert _matches(_sample(enabled=True), enabled="Enabled")
    assert _matches(_sample(enabled=False), enabled="Disabled")
    assert not _matches(_sample(enabled=True), enabled="Disabled")


def test_filter_facets_are_anded():
    p = _sample(category="Credentials", severity=Severity.HIGH)
    assert _matches(p, category="Credentials", severity="HIGH")
    assert not _matches(p, category="Credentials", severity="LOW")


def test_visible_groups_no_filter_state_returns_all():
    tab = _bare_tab()
    pats = [_custom("a", label="A"), _custom("b", label="B")]
    tab._patterns = pats
    groups = tab._visible_groups()
    assert [m.key for g in groups for m in g.members] == ["a", "b"]


# --- filter-change / clear behavior (mocked tree) ---

def _filter_tab():
    tab = _bare_tab()
    tab._filter_search_var = _DummyVar("")
    # Category lives in tab._active_category (left pane), set by _bare_tab.
    tab._filter_severity_var = _DummyVar(_ALL)
    tab._filter_user_tag_var = _DummyVar(_ALL)
    tab._filter_enabled_var = _DummyVar(_ALL)
    return tab


def test_on_filter_change_clears_selection_and_refreshes():
    tab = _filter_tab()
    tab._tree.selection.return_value = ("custom_a",)
    tab._on_filter_change()
    tab._tree.selection_remove.assert_called_once_with("custom_a")
    tab._refresh_table.assert_called_once()


def test_on_filter_change_does_not_mark_dirty():
    tab = _filter_tab()
    tab._filter_severity_var.set("HIGH")
    tab._tree.selection.return_value = ()
    tab._on_filter_change()
    assert tab._pattern_manager_dirty is False


def test_clear_filters_resets_vars_and_single_refresh():
    tab = _filter_tab()
    tab._filter_search_var.set("x")
    tab._active_category = "Credentials"
    tab._filter_severity_var.set("HIGH")
    tab._filter_user_tag_var.set("User1")
    tab._filter_enabled_var.set("Disabled")
    tab._tree.selection.return_value = ()
    tab._on_clear_filters()
    assert tab._filter_search_var.get() == ""
    # Category (left pane) resets to "All Categories".
    assert tab._active_category == _ALL
    assert tab._filter_severity_var.get() == _ALL
    assert tab._filter_user_tag_var.get() == _ALL
    assert tab._filter_enabled_var.get() == _ALL
    tab._refresh_table.assert_called_once()


def test_toggle_restore_keeps_only_visible_keys_selected():
    # Enabled facet "Enabled": toggling a visible enabled row off makes it leave
    # the filtered view, so it must not be re-selected.
    tab = _filter_tab()
    tab._filter_enabled_var.set("Enabled")
    a = _custom("custom_a", enabled=True)
    b = _custom("custom_b", enabled=True)
    tab._patterns = [a, b]
    tab._tree.selection.return_value = ("custom_a",)
    tab._on_toggle()
    # custom_a is now disabled -> filtered out -> not selectable.
    tab._tree.selection_set.assert_called_once_with([])


# --- real-Tk: filter row presence and action scoping ---

def test_filter_row_widgets_present(tk_root):
    tab = SherlockTab(tk_root, {})
    captured = {}

    def when_open():
        mgr = tab._pattern_manager
        try:
            captured["buttons"] = _button_texts(mgr)
            captured["combos"] = [
                w for w in _all_widgets(mgr) if isinstance(w, ttk.Combobox)
            ]
            captured["entries"] = [
                w for w in _all_widgets(mgr) if isinstance(w, tk.Entry)
            ]
            # C23: Category is the left pane (a Treeview), not a facet combo.
            captured["has_widgets"] = (
                tab._filter_category_combo is None
                and tab._filter_user_tag_combo is not None
                and tab._category_tree is not None
            )
        finally:
            mgr.destroy()

    tk_root.after(50, when_open)
    tab._open_pattern_manager()

    assert "Clear" in captured["buttons"]
    # Severity, User Tag, Enabled facet comboboxes (Category moved to left pane).
    assert len(captured["combos"]) >= 3
    # Value search entry + category search entry.
    assert len(captured["entries"]) >= 2
    assert captured["has_widgets"]


def test_facet_value_lists_refresh_after_mutation(tk_root):
    tab = SherlockTab(tk_root, {})
    tab._patterns = [_tagged("custom_a", category="Alpha")]
    captured = {}

    def _category_rows():
        tree = tab._category_tree
        return [tree.set(iid, "category") for iid in tree.get_children()]

    def when_open():
        try:
            captured["tags_before"] = list(tab._filter_user_tag_combo["values"])
            captured["cats_before"] = _category_rows()
            tab._patterns.append(
                _tagged("custom_b", category="Beta", color_tag="user1")
            )
            # A staged add refreshes both panes (left counts + right groups) and
            # the User Tag facet list.
            tab._refresh_after_mutation()
            captured["tags_after"] = list(tab._filter_user_tag_combo["values"])
            captured["cats_after"] = _category_rows()
        finally:
            tab._pattern_manager.destroy()

    tk_root.after(50, when_open)
    tab._open_pattern_manager()

    assert "User1" not in captured["tags_before"]
    assert "User1" in captured["tags_after"]
    assert "Beta" not in captured["cats_before"]
    assert "Beta" in captured["cats_after"]


def test_delete_under_active_filter_only_touches_visible_selection(tk_root):
    tab = SherlockTab(tk_root, {})
    a = _tagged("custom_a", category="Custom")
    b = _tagged("custom_b", category="Other")
    tab._patterns = [a, b]
    captured = {}

    def when_open():
        try:
            # Select the "Custom" category in the left pane (display-only).
            tab._active_category = "Custom"
            tab._on_filter_change()
            captured["visible_iids"] = tab._tree.get_children()
            tab._tree.selection_set("custom_a")
            tab._on_delete()
            captured["after"] = list(tab._patterns)
        finally:
            tab._pattern_manager.destroy()

    tk_root.after(50, when_open)
    tab._open_pattern_manager()

    # Only custom_a (visible + selected) was rendered, then deleted.
    assert captured["visible_iids"] == ("custom_a",)
    keys = [p.key for p in captured["after"]]
    assert "custom_a" not in keys
    # The filtered-out row is byte-for-byte unchanged.
    assert any(p == b for p in captured["after"])


def test_enable_disable_under_active_filter_only_touches_visible_selection(tk_root):
    tab = SherlockTab(tk_root, {})
    a = _tagged("custom_a", category="Custom", enabled=True)
    b = _tagged("custom_b", category="Other", enabled=True)
    tab._patterns = [a, b]
    captured = {}

    def when_open():
        try:
            tab._active_category = "Custom"
            tab._on_filter_change()
            tab._tree.selection_set("custom_a")
            tab._on_toggle()
            captured["after"] = list(tab._patterns)
        finally:
            tab._pattern_manager.destroy()

    tk_root.after(50, when_open)
    tab._open_pattern_manager()

    by_key = {p.key: p for p in captured["after"]}
    assert by_key["custom_a"].enabled is False   # visible selection flipped
    assert by_key["custom_b"] == b               # filtered-out row unchanged


# ---------------------------------------------------------------------------
# Export (C19)
# ---------------------------------------------------------------------------

def _seed_export_tab():
    tab = _bare_tab()
    tab._patterns = list(default_settings().patterns)
    tab._pattern_manager = MagicMock()
    return tab


def test_export_writes_full_catalog_json(monkeypatch, tmp_path):
    tab = _seed_export_tab()
    out = tmp_path / "patterns.json"
    monkeypatch.setattr(
        mod.filedialog, "asksaveasfilename", lambda **kw: str(out)
    )
    before = list(tab._patterns)

    tab._on_export()

    import json as _json

    text = out.read_text(encoding="utf-8")
    assert text.endswith("\n")
    data = _json.loads(text)
    assert data["schema"] == "dirracuda.sherlock.patterns"
    assert data["schema_version"] == 1
    assert data["count"] == len(before)
    assert len(data["patterns"]) == len(before)
    # Read-only: staged list and manager untouched.
    assert tab._patterns == before
    assert tab._pattern_manager_dirty is False
    assert tab._pattern_manager is not None
    assert tab._status_label.configure.called


def test_export_ignores_filters_and_writes_all_rows(monkeypatch, tmp_path):
    tab = _seed_export_tab()
    # Filter state that would hide everything is irrelevant to export.
    tab._filter_search_var = _DummyVar("zzz-no-match")
    tab._active_category = "Nonexistent"
    out = tmp_path / "all.json"
    monkeypatch.setattr(
        mod.filedialog, "asksaveasfilename", lambda **kw: str(out)
    )

    tab._on_export()

    import json as _json

    data = _json.loads(out.read_text(encoding="utf-8"))
    assert data["count"] == len(tab._patterns)


def test_export_cancel_writes_nothing(monkeypatch, tmp_path):
    tab = _seed_export_tab()
    fake_mb = MagicMock()
    monkeypatch.setattr(mod, "safe_messagebox", fake_mb)
    monkeypatch.setattr(mod.filedialog, "asksaveasfilename", lambda **kw: "")
    before = list(tab._patterns)

    tab._on_export()

    assert tab._patterns == before
    assert tab._pattern_manager_dirty is False
    fake_mb.showerror.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_export_write_error_reports_and_does_not_mutate(monkeypatch, tmp_path):
    tab = _seed_export_tab()
    fake_mb = MagicMock()
    monkeypatch.setattr(mod, "safe_messagebox", fake_mb)
    # Path under a directory that does not exist -> open() raises OSError.
    bad = tmp_path / "missing_dir" / "out.json"
    monkeypatch.setattr(
        mod.filedialog, "asksaveasfilename", lambda **kw: str(bad)
    )
    before = list(tab._patterns)

    tab._on_export()

    fake_mb.showerror.assert_called_once()
    assert tab._patterns == before
    assert not bad.exists()


# ---------------------------------------------------------------------------
# C23 - Two-pane (grouped value rows + category index)
# ---------------------------------------------------------------------------

from gui.components.experimental_features import sherlock_pattern_manager as pm  # noqa: E402


def test_grouped_row_selection_maps_to_all_members():
    tab = _bare_tab()
    # Same label/category/severity/tag -> one value group with two members.
    m1 = _tagged("custom_1", label="Tax", pattern="*w2*")
    m2 = _tagged("custom_2", label="Tax", pattern="*w4*")
    other = _tagged("custom_3", label="Other", pattern="*x*")
    tab._patterns = [m1, m2, other]
    # The group's iid is its first member's key.
    tab._tree.selection.return_value = ("custom_1",)

    groups = tab._selected_groups()
    assert len(groups) == 1
    assert {member.key for member in groups[0].members} == {"custom_1", "custom_2"}
    assert {p.key for p in tab._selected_patterns()} == {"custom_1", "custom_2"}


def test_toggle_grouped_row_flips_all_members():
    tab = _bare_tab()
    tab._patterns = [
        _tagged("custom_1", label="Tax", pattern="*w2*", enabled=True),
        _tagged("custom_2", label="Tax", pattern="*w4*", enabled=True),
    ]
    tab._tree.selection.return_value = ("custom_1",)

    tab._on_toggle()

    assert all(not p.enabled for p in tab._patterns)
    assert tab._pattern_manager_dirty is True


def test_delete_grouped_row_removes_all_members():
    tab = _bare_tab()
    tab._patterns = [
        _tagged("custom_1", label="Tax", pattern="*w2*"),
        _tagged("custom_2", label="Tax", pattern="*w4*"),
        _tagged("custom_3", label="Keep", pattern="*k*"),
    ]
    tab._tree.selection.return_value = ("custom_1",)

    tab._on_delete()

    assert [p.key for p in tab._patterns] == ["custom_3"]
    assert tab._pattern_manager_dirty is True


def test_edit_grouped_multimember_is_guarded(monkeypatch):
    tab = _bare_tab()
    tab._patterns = [
        _tagged("custom_1", label="Tax", pattern="*w2*"),
        _tagged("custom_2", label="Tax", pattern="*w4*"),
    ]
    snapshot = list(tab._patterns)
    tab._tree.selection.return_value = ("custom_1",)
    monkeypatch.setattr(
        tab, "_open_pattern_dialog",
        lambda *a, **k: pytest.fail("dialog must not open for multi-member edit"),
    )

    tab._on_edit()

    assert tab._patterns == snapshot
    assert tab._pattern_manager_dirty is False
    tab._status_label.configure.assert_called()


def test_copy_grouped_multimember_duplicates_all_members(monkeypatch):
    tab = _bare_tab()
    tab._patterns = [
        _tagged("custom_1", label="Tax", pattern="*w2*"),
        _tagged("custom_2", label="Tax", pattern="*w4*"),
    ]
    tab._tree.selection.return_value = ("custom_1",)
    monkeypatch.setattr(
        tab, "_open_pattern_dialog",
        lambda *a, **k: pytest.fail("multi-member copy must not open the dialog"),
    )

    tab._on_copy()

    assert len(tab._patterns) == 4
    assert sorted(p.pattern for p in tab._patterns) == ["*w2*", "*w2*", "*w4*", "*w4*"]
    assert all(not p.builtin for p in tab._patterns)
    assert tab._pattern_manager_dirty is True


def test_visible_groups_combines_active_category_and_search():
    tab = _filter_tab()
    a = _tagged("custom_a", label="Alpha", category="Custom")
    b = _tagged("custom_b", label="Beta", category="Custom")
    other = _tagged("custom_c", label="Alpha", category="Other")
    tab._patterns = [a, b, other]
    tab._active_category = "Custom"
    tab._filter_search_var.set("alpha")

    groups = tab._visible_groups()

    assert [g.label for g in groups] == ["Alpha"]
    assert {m.key for g in groups for m in g.members} == {"custom_a"}


def test_hidden_rows_are_not_actionable():
    tab = _filter_tab()
    visible = _tagged("custom_v", label="Vis", category="Custom")
    hidden = _tagged("custom_h", label="Hid", category="Other")
    tab._patterns = [visible, hidden]
    tab._active_category = "Custom"
    # custom_h is filtered out of the active category; selecting it is inert.
    tab._tree.selection.return_value = ("custom_h",)

    assert tab._selected_groups() == []
    tab._on_delete()

    assert any(p.key == "custom_h" for p in tab._patterns)
    assert tab._pattern_manager_dirty is False


def test_delete_last_group_of_active_category_resets_to_all():
    tab = _bare_tab()
    only = _tagged("custom_1", label="Solo", category="Lonely")
    keep = _tagged("custom_2", label="Keep", category="Custom")
    tab._patterns = [only, keep]
    tab._active_category = "Lonely"
    tab._tree.selection.return_value = ("custom_1",)

    tab._on_delete()

    # The "Lonely" category no longer exists -> active category falls back to All.
    assert tab._active_category == _ALL


def test_category_pane_lists_all_categories_with_grouped_counts(tk_root):
    tab = SherlockTab(tk_root, {})
    tab._patterns = [
        _tagged("c1", label="Tax", category="Finance", pattern="*w2*"),
        _tagged("c2", label="Tax", category="Finance", pattern="*w4*"),  # same group
        _tagged("c3", label="Keys", category="Secrets", pattern="*key*"),
    ]
    captured = {}

    def when_open():
        try:
            tab._refresh_category_list()
            tree = tab._category_tree
            captured["rows"] = {
                iid: (tree.set(iid, "category"), tree.set(iid, "count"))
                for iid in tree.get_children()
            }
        finally:
            tab._pattern_manager.destroy()

    tk_root.after(50, when_open)
    tab._open_pattern_manager()

    rows = captured["rows"]
    # Counts are grouped value-row counts: Finance has 2 patterns but 1 group.
    assert rows[pm._ALL_ROW_IID] == ("All Categories", "2")
    assert rows["cat::Finance"] == ("Finance", "1")
    assert rows["cat::Secrets"] == ("Secrets", "1")


def test_category_search_filters_left_list(tk_root):
    tab = SherlockTab(tk_root, {})
    tab._patterns = [
        _tagged("c1", label="A", category="Finance", pattern="*a*"),
        _tagged("c2", label="B", category="Secrets", pattern="*b*"),
    ]
    captured = {}

    def when_open():
        try:
            tab._category_search_var.set("fin")
            tree = tab._category_tree
            captured["cats"] = [
                tree.set(iid, "category") for iid in tree.get_children()
            ]
        finally:
            tab._pattern_manager.destroy()

    tk_root.after(50, when_open)
    tab._open_pattern_manager()

    cats = captured["cats"]
    assert "All Categories" in cats  # always present
    assert "Finance" in cats
    assert "Secrets" not in cats


def test_selecting_category_filters_right_pane_and_all_restores(tk_root):
    tab = SherlockTab(tk_root, {})
    tab._patterns = [
        _tagged("c1", label="A", category="Finance", pattern="*a*"),
        _tagged("c2", label="B", category="Secrets", pattern="*b*"),
    ]
    captured = {}

    def when_open():
        try:
            tab._category_tree.selection_set("cat::Finance")
            tab._on_category_select()
            captured["after_select"] = tab._tree.get_children()
            tab._category_tree.selection_set(pm._ALL_ROW_IID)
            tab._on_category_select()
            captured["after_all"] = tab._tree.get_children()
        finally:
            tab._pattern_manager.destroy()

    tk_root.after(50, when_open)
    tab._open_pattern_manager()

    assert captured["after_select"] == ("c1",)
    assert set(captured["after_all"]) == {"c1", "c2"}


def test_export_writes_all_groups_not_only_visible(tk_root, monkeypatch, tmp_path):
    tab = SherlockTab(tk_root, {})
    tab._patterns = [
        _tagged("c1", label="A", category="Finance", pattern="*a*"),
        _tagged("c2", label="B", category="Secrets", pattern="*b*"),
    ]
    out = tmp_path / "all.json"
    monkeypatch.setattr(
        mod.filedialog, "asksaveasfilename", lambda **kw: str(out)
    )
    captured = {}

    def when_open():
        try:
            # Narrow the right pane to one category, then export.
            tab._active_category = "Finance"
            tab._refresh_table()
            captured["visible"] = tab._tree.get_children()
            tab._on_export()
        finally:
            tab._pattern_manager.destroy()

    tk_root.after(50, when_open)
    tab._open_pattern_manager()

    import json as _json

    data = _json.loads(out.read_text(encoding="utf-8"))
    assert captured["visible"] == ("c1",)  # right pane filtered to Finance
    # Export still writes the full staged catalog (both patterns).
    assert data["count"] == 2
    assert {row["pattern"] for row in data["patterns"]} == {"*a*", "*b*"}
