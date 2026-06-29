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
    tab._status_label = MagicMock()
    tab._refresh_table = MagicMock()
    tab._pattern_manager = None
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


# ---------------------------------------------------------------------------
# Real-Tk: pattern manager structure + nested Add/Edit grab restoration
# ---------------------------------------------------------------------------

import tkinter as tk  # noqa: E402


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
    for label in ("Add", "Edit", "Enable/Disable", "Delete", "Restore Built-ins", "Close"):
        assert label in captured["buttons"]
    assert captured["add_grabbed"] is True
    # Add dialog was transient to the manager, not the main window.
    assert captured["add_transient"] == captured["mgr_path"]
    assert captured["grab_after"] is not None


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
