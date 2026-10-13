"""Real Tk state/forms, exercised on Xvfb with isolated stores."""

import tkinter as tk

import pytest

from experimental.keymaster import store as km
from gui.components.keymaster_security import PassphraseDialog
from gui.components.keymaster_window import KeymasterWindow
from gui.utils.style import get_theme


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
