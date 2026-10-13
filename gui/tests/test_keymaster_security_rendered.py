"""Real Tk state/forms, exercised on Xvfb with isolated stores."""

import json
import tkinter as tk
from types import SimpleNamespace

import pytest

from experimental.keymaster import store as km
from gui.components.keymaster_security import PassphraseDialog
from gui.components.keymaster_window import KeymasterWindow
from gui.utils.style import get_theme
from gui.tests.dialog_audit.inspect import measure


@pytest.fixture
def ui(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Requires Xvfb or a display")
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    # Prevent API activity; no window state/settings are persisted to live paths.
    monkeypatch.setattr(KeymasterWindow, "_schedule_startup_credit_refresh", lambda self: None)
    window = KeymasterWindow(root, db_path=tmp_path / "keymaster.db")
    root.update()
    try:
        yield root, window
        root.update()
        assert not errors
    finally:
        root.destroy()


def test_open_setup_cancel_delete_enable_reopen(ui, monkeypatch):
    root, window = ui
    assert window._security_status_var.get() == "Secure storage: Setup needed"
    assert window._security_action_btn.cget("text") == "Set passphrase…"
    assert len([w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]) == 1
    monkeypatch.setattr(window, "_ask_passphrase", lambda **kw: None)
    window._security_action_btn.invoke()
    assert window._security_state == "Setup needed"
    monkeypatch.setattr("gui.components.keymaster_security.messagebox.askyesno", lambda *a, **kw: True)
    window._reset_btn.invoke()
    assert window._security_state == "Off"
    assert window._security_action_btn.cget("text") == "Enable secure storage…"
    monkeypatch.setattr(window, "_ask_passphrase", lambda **kw: "  synthetic typed phrase  ")
    window._security_action_btn.invoke()
    assert window._security_state == "Unlocked"
    assert window._security_action_btn.cget("state") == "disabled"
    db_path = window.db_path
    window._on_close()
    reopened = KeymasterWindow(root, db_path=db_path)
    assert reopened._security_state == "Locked"
    assert reopened._security_action_btn.cget("text") == "Unlock…"
    with km.open_connection(db_path) as conn:
        km.unlock_session_keys(conn, "  synthetic typed phrase  ")


@pytest.mark.parametrize("state", ["Off", "Locked", "Unlocked", "Storage problem", "Setup needed"])
def test_security_action_reflows_when_state_text_changes(ui, state):
    root, window = ui
    window._security_state = state
    window._refresh_security_controls()
    root.update()
    button = window._security_action_btn
    assert button.winfo_width() >= button.winfo_reqwidth()
    assert not measure(window.window)["findings"]


@pytest.mark.parametrize("setup", [False, True])
def test_form_reveal_exact_input_focus_and_cancel(ui, setup):
    root, window = ui
    dialog = PassphraseDialog(window.window, get_theme(), setup=setup)
    root.update()
    assert dialog.dialog.grab_current() is dialog.dialog
    assert dialog.dialog.focus_get() is dialog.entries[0]
    assert len(dialog.entries) == (2 if setup else 1)
    assert all(entry.cget("show") == "*" for entry in dialog.entries)
    dialog.passphrase.set("  café Ω  ")
    dialog.confirmation.set("different")
    dialog.visible.set(True)
    dialog._toggle_visibility()
    assert all(entry.cget("show") == "" for entry in dialog.entries)
    dialog.visible.set(False)
    dialog._toggle_visibility()
    if setup:
        dialog._submit()
        assert dialog.dialog.winfo_exists()
        assert "do not match" in dialog.error.get()
    dialog.confirmation.set("  café Ω  ")
    dialog._submit()
    assert dialog.result == "  café Ω  "
    assert dialog.passphrase.get() == dialog.confirmation.get() == ""
    cancelled = PassphraseDialog(window.window, get_theme(), setup=setup)
    cancelled.passphrase.set("discard me")
    cancelled._cancel()
    assert cancelled.result is None
    assert cancelled.passphrase.get() == ""


@pytest.mark.parametrize("setup", [False, True])
def test_passphrase_waits_for_mapping_before_grab(ui, monkeypatch, setup):
    root, window = ui
    original_init = tk.Toplevel.__init__
    original_grab = tk.Toplevel.grab_set
    grabbed = []

    def delayed_mapping(dialog, *args, **kwargs):
        original_init(dialog, *args, **kwargs)
        dialog.withdraw()
        dialog.after(40, dialog.deiconify)

    def require_viewable(dialog):
        # Enforce the native failure reported by HI, independent of WM timing.
        if not dialog.winfo_viewable():
            raise tk.TclError("grab failed: window not viewable")
        assert dialog.winfo_children(), "The form must be built before grabbing"
        grabbed.append(dialog)
        return original_grab(dialog)

    monkeypatch.setattr(tk.Toplevel, "__init__", delayed_mapping)
    monkeypatch.setattr(tk.Toplevel, "grab_set", require_viewable)
    dialog = PassphraseDialog(window.window, get_theme(), setup=setup)
    root.update()
    assert grabbed == [dialog.dialog]
    assert all(entry.winfo_viewable() for entry in dialog.entries)
    assert dialog.dialog.focus_get() is dialog.entries[0]
    dialog._cancel()
    assert root.grab_current() is None


@pytest.mark.parametrize("outcome", ["unlock", "cancel", "wrong", "deleted"])
def test_reopen_two_keys_double_click_unlock_applies_selected_key(ui, monkeypatch, tmp_path, outcome):
    root, window = ui
    phrase = "synthetic reopen phrase"
    with km.open_connection(window.db_path) as conn:
        keys, _ = km.enable_secure_storage(conn, phrase)
        km.create_key(conn, "SHODAN", "First", "synthetic first", "", session_keys=keys)
        selected_id = km.create_key(conn, "SHODAN", "Second", "synthetic second", "", session_keys=keys)
    path = window.db_path
    window._on_close()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"shodan": {"api_key": "unchanged"}}))
    reopened = KeymasterWindow(root, db_path=path, config_path=str(config))
    root.update()
    assert reopened._security_state == "Locked"
    assert reopened._row_by_iid[str(selected_id)]["api_key"] == ""
    errors = []
    monkeypatch.setattr("gui.components.keymaster_security.messagebox.showerror", lambda *a, **kw: errors.append(a))
    original_show = PassphraseDialog.show

    def enter_passphrase(dialog):
        def respond():
            assert dialog.entries[0].winfo_viewable()
            if outcome == "cancel":
                dialog._cancel()
            else:
                if outcome == "deleted":
                    with km.open_connection(path) as conn:
                        km.delete_key(conn, selected_id)
                dialog.passphrase.set("wrong synthetic phrase" if outcome == "wrong" else phrase)
                dialog._submit()
        root.after(20, respond)
        return original_show(dialog)

    monkeypatch.setattr(PassphraseDialog, "show", enter_passphrase)
    _, y, _, height = reopened._tree.bbox(str(selected_id))
    reopened._on_tree_double_click(SimpleNamespace(y=y + height // 2))
    if outcome == "wrong":
        assert len(errors) == 1
        assert "Incorrect passphrase" in errors[0][1]
    else:
        assert not errors
    applied = json.loads(config.read_text())["shodan"]["api_key"]
    assert applied == ("synthetic second" if outcome == "unlock" else "unchanged")
    assert reopened._security_state == ("Locked" if outcome in {"cancel", "wrong"} else "Unlocked")
    assert root.grab_current() is None


def test_passphrase_can_map_with_withdrawn_parent(ui):
    root, window = ui
    window.window.withdraw()
    root.update()
    fallback_used = []
    def release_if_stuck():
        fallback_used.append(True)
        window.window.deiconify()
    timer = root.after(500, release_if_stuck)
    try:
        dialog = PassphraseDialog(window.window, get_theme(), setup=False)
        assert dialog.dialog.winfo_viewable()
        assert not fallback_used, "Dialog waited for its hidden parent to map"
        dialog._cancel()
    finally:
        root.after_cancel(timer)


def test_close_during_mapping_returns_cancel_without_orphan(ui, monkeypatch):
    root, window = ui
    original_init = tk.Toplevel.__init__
    def close_before_mapping(dialog, *args, **kwargs):
        original_init(dialog, *args, **kwargs)
        dialog.withdraw()
        dialog.after(20, dialog.destroy)
    monkeypatch.setattr(tk.Toplevel, "__init__", close_before_mapping)
    dialog = PassphraseDialog(window.window, get_theme(), setup=False)
    assert dialog.show() is None
    assert not dialog.dialog.winfo_exists()
    assert root.grab_current() is None


def test_grab_failure_destroys_dialog_instead_of_leaving_blank_window(ui, monkeypatch):
    root, window = ui
    before = window.window.winfo_children()
    def fail_grab(dialog):
        assert dialog.winfo_viewable()
        raise tk.TclError("injected grab failure")
    monkeypatch.setattr(tk.Toplevel, "grab_set", fail_grab)
    with pytest.raises(tk.TclError, match="injected grab failure"):
        PassphraseDialog(window.window, get_theme(), setup=False)
    assert window.window.winfo_children() == before
    assert root.grab_current() is None
