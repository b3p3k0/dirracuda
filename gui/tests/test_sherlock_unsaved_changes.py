"""Unsaved Sherlock edits must survive cancelled close attempts."""

import tkinter as tk
from unittest.mock import Mock

import pytest

from gui.components import experimental_features_dialog as shell
from gui.components.experimental_features import registry, sherlock_tab
from gui.utils import safe_messagebox
from shared.sherlock import Severity


@pytest.fixture
def root():
    window = tk.Tk()
    window.geometry("400x300+0+0")
    window.update()
    yield window
    window.destroy()


@pytest.fixture
def settings():
    return Mock(get_setting=Mock(return_value={}), set_setting=Mock(return_value=True))


def _widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from _widgets(child)


@pytest.mark.parametrize("field", ["severity", "user", "ignore_case", "after_probe", "patterns"])
def test_unsaved_edits_cancel_save_and_revert(root, settings, monkeypatch, field):
    tab = sherlock_tab.SherlockTab(root, {"settings_manager": settings})
    prompt = Mock(return_value=False)
    monkeypatch.setattr(safe_messagebox, "askyescancel", prompt)
    assert tab.confirm_close()
    prompt.assert_not_called()

    variables = {
        "severity": tab._color_vars[Severity.HIGH],
        "user": tab._user_color_vars["user1"],
        "ignore_case": tab._ignore_case_var,
        "after_probe": tab._run_after_probe_var,
    }
    if field == "patterns":
        tab._pattern_manager_dirty = True
    else:
        variable = variables[field]
        original = variable.get()
        changed = not original if isinstance(original, bool) else "#112233"
        variable.set(changed)
        assert not tab.confirm_close()
        variable.set(original)
        prompt.reset_mock()
        assert tab.confirm_close()  # edits reverted to the loaded value
        prompt.assert_not_called()
        variable.set(changed)

    assert not tab.confirm_close()
    settings.set_setting.assert_not_called()
    prompt.return_value = True
    assert tab.confirm_close()  # host may discard; confirmation does not save
    settings.set_setting.assert_not_called()
    settings.set_setting.return_value = False
    assert not tab._on_save()
    prompt.return_value = False
    assert not tab.confirm_close()  # failed save keeps the warning
    settings.set_setting.return_value = True
    assert tab._on_save()
    prompt.reset_mock()
    assert tab.confirm_close()
    prompt.assert_not_called()


@pytest.mark.parametrize("route", ["button", "wm", "Escape", "Control-w", "Return"])
def test_accessories_close_routes_keep_cancelled_edits(root, settings, monkeypatch, route):
    tabs = []

    def build(notebook, context):
        tab = sherlock_tab.SherlockTab(notebook, context)
        context["register_close_guard"](tab.confirm_close)
        notebook.add(tab.frame, text="Sherlock")
        tabs.append(tab)

    monkeypatch.setattr(registry, "build_all_tabs", build)
    monkeypatch.setattr(shell.ExperimentalFeaturesDialog, "_build_warning_section", lambda *args: None)
    prompt = Mock(return_value=False)
    monkeypatch.setattr(safe_messagebox, "askyescancel", prompt)
    shell.ExperimentalFeaturesDialog(root, {}, settings)
    tab = tabs[0]
    dialog = tab.frame.winfo_toplevel()
    tab._user_color_vars["user1"].set("#112233")

    def close():
        if route == "button":
            next(w for w in _widgets(dialog) if isinstance(w, tk.Button) and w.cget("text") == "Close").invoke()
        elif route == "wm":
            dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))
        else:
            dialog.focus_force()
            dialog.update()
            dialog.event_generate(f"<{route}>")
            root.update()

    close()
    assert dialog.winfo_exists()
    assert tab._user_color_vars["user1"].get() == "#112233"
    prompt.assert_called_once()
    settings.set_setting.assert_not_called()
    prompt.return_value = True
    close()
    assert not dialog.winfo_exists()
    settings.set_setting.assert_not_called()


def test_builder_registers_guard(root, settings):
    guards = []
    frame = sherlock_tab.build_sherlock_tab(root, {
        "settings_manager": settings, "register_close_guard": guards.append,
    })
    assert frame.winfo_exists()
    assert len(guards) == 1
    assert guards[0]()


def test_standalone_settings_window_guards_close(root, settings, monkeypatch):
    build = sherlock_tab.build_sherlock_tab
    prompt = Mock(side_effect=[False, True])
    monkeypatch.setattr(safe_messagebox, "askyescancel", prompt)
    survived_cancel = []

    def register_edit(parent, context):
        register = context["register_close_guard"]

        def edited(guard):
            guard.__self__._user_color_vars["user1"].set("#112233")
            register(guard)

        return build(parent, {**context, "register_close_guard": edited})

    monkeypatch.setattr(sherlock_tab, "build_sherlock_tab", register_edit)

    def close():
        window = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
        window.tk.call(window.protocol("WM_DELETE_WINDOW"))
        survived_cancel.append(bool(window.winfo_exists()))
        next(w for w in _widgets(window) if isinstance(w, tk.Button) and w.cget("text") == "Close").invoke()

    root.after(50, close)
    sherlock_tab.open_sherlock_settings_window(root, settings)
    assert survived_cancel == [True]
    assert prompt.call_count == 2
    settings.set_setting.assert_not_called()


@pytest.mark.parametrize("choice, accepted", [
    ("Yes", True), ("Cancel", False), ("wm", False),
    ("Escape", False), ("Return", False),
])
def test_yes_cancel_buttons_and_dismissal_restore_grab(root, choice, accepted):
    root.grab_set()
    observed = []

    def respond():
        dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
        buttons = {w.cget("text"): w for w in _widgets(dialog) if isinstance(w, tk.Button)}
        observed.append(set(buttons))
        if choice in buttons:
            buttons[choice].invoke()
        elif choice == "wm":
            dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))
        else:
            dialog.event_generate(f"<{choice}>")

    root.after(50, respond)
    assert safe_messagebox.askyescancel("Unsaved", "Discard changes?", parent=root) is accepted
    assert observed == [{"Yes", "Cancel"}]
    assert root.grab_current() == root
    root.grab_release()
