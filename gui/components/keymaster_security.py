"""Desktop Keymaster security state, actions, and passphrase dialog."""

from __future__ import annotations

import tkinter as tk
from typing import Optional

from experimental.keymaster import store as km_store
from experimental.keymaster.models import (
    InvalidPassphraseError, KeymasterLockedError, KeymasterMetadataError,
    PassphraseAlreadyConfiguredError, PassphraseRequiredError,
)
from gui.utils import safe_messagebox as messagebox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.scrollable_body import ScrollableBody, flow_row, wrap_label


def security_error_message(exc: Exception) -> str:
    """Explain recovery without exposing database contents or raw exceptions."""
    if isinstance(exc, InvalidPassphraseError):
        return "Incorrect passphrase. Try again, or use Delete all keys and disable to start over."
    if isinstance(exc, PassphraseRequiredError):
        return "No passphrase is configured. Choose Set passphrase to finish setup."
    if isinstance(exc, KeymasterMetadataError):
        return "Security metadata is damaged or incomplete. Use Delete all keys and disable to start over."
    if isinstance(exc, PassphraseAlreadyConfiguredError):
        return "A previous passphrase is still stored. Use Delete all keys and disable before fresh setup."
    if isinstance(exc, KeymasterLockedError):
        return "This unlock session has expired. Unlock Keymaster again."
    if isinstance(exc, ValueError):
        return "The passphrase or encrypted data could not be processed. Check your input or reset Keymaster."
    return "Keymaster storage could not be accessed. Check the file's availability and permissions, then retry."


class PassphraseDialog:
    """One cancellable form; exact input, optional confirmation, explicit reveal."""

    def __init__(self, parent, theme, *, setup: bool):
        self.parent = parent
        self.setup = setup
        self.result: Optional[str] = None
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Set Keymaster passphrase" if setup else "Unlock Keymaster")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        theme.apply_to_widget(self.dialog, "main_window")
        self.dialog.geometry("500x330")
        self.dialog.minsize(400, 300)

        buttons = tk.Frame(self.dialog)
        theme.apply_to_widget(buttons, "main_window")
        buttons.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=10)
        submit = tk.Button(buttons, text="Set passphrase" if setup else "Unlock", command=self._submit)
        theme.apply_to_widget(submit, "button_primary")
        submit.pack(side=tk.LEFT)
        cancel = tk.Button(buttons, text="Cancel", command=self._cancel)
        theme.apply_to_widget(cancel, "button_secondary")
        cancel.pack(side=tk.LEFT)
        flow_row(buttons)

        body = ScrollableBody(self.dialog)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        form = body.content
        help_label = tk.Label(form, anchor="w", justify="left", text=(
            "Use your dedicated Keymaster passphrase. Spaces and capitalization count."
        ))
        theme.apply_to_widget(help_label, "label")
        help_label.pack(fill=tk.X, pady=(0, 8))
        self.passphrase = tk.StringVar(master=self.dialog)
        self.confirmation = tk.StringVar(master=self.dialog)
        self.entries = []
        fields = [("Passphrase", self.passphrase)]
        if setup:
            fields.append(("Confirm passphrase", self.confirmation))
        for text, variable in fields:
            label = tk.Label(form, text=text, anchor="w")
            theme.apply_to_widget(label, "label")
            label.pack(fill=tk.X)
            entry = tk.Entry(form, textvariable=variable, show="*")
            theme.apply_to_widget(entry, "entry")
            entry.pack(fill=tk.X, pady=(0, 8))
            self.entries.append(entry)
        self.visible = tk.BooleanVar(master=self.dialog, value=False)
        reveal = tk.Checkbutton(form, text="Show passphrase", variable=self.visible, command=self._toggle_visibility)
        theme.apply_to_widget(reveal, "checkbox")
        reveal.pack(anchor="w")
        self.error = tk.StringVar(master=self.dialog)
        error_label = tk.Label(form, textvariable=self.error, anchor="w", justify="left")
        theme.apply_to_widget(error_label, "label")
        error_label.configure(fg=theme.colors["error"])
        error_label.pack(fill=tk.X)
        self.dialog.bind("<Return>", lambda _event: self._submit())
        self.dialog.bind("<Escape>", lambda _event: self._cancel())
        self.dialog.protocol("WM_DELETE_WINDOW", self._cancel)
        self.dialog.bind("<FocusIn>", self._focus_entry, add="+")
        ensure_dialog_focus(self.dialog, parent)

    def _focus_entry(self, event):
        if event.widget is self.dialog:
            self.entries[0].focus_set()

    def _toggle_visibility(self):
        for entry in self.entries:
            entry.configure(show="" if self.visible.get() else "*")

    def _submit(self):
        value = self.passphrase.get()
        if not value or len(value.encode("utf-8")) > 1024:
            self.error.set("Enter a passphrase of 1–1024 UTF-8 bytes.")
            return
        if self.setup and value != self.confirmation.get():
            self.error.set("Passphrases do not match. Re-enter both fields.")
            return
        self.result = value
        self._close()

    def _cancel(self):
        self.result = None
        self._close()

    def _close(self):
        self.passphrase.set("")
        self.confirmation.set("")
        self.dialog.destroy()

    def show(self) -> Optional[str]:
        self.parent.wait_window(self.dialog)
        result, self.result = self.result, None
        return result


class KeymasterSecurityMixin:
    """Security-only methods extracted from the modeless key manager."""

    def _build_security_controls(self, parent):
        panel = tk.Frame(parent)
        self.theme.apply_to_widget(panel, "main_window")
        panel.pack(fill=tk.X, pady=(0, 8))
        self._security_status_var = tk.StringVar(master=self.window)
        status = tk.Label(panel, textvariable=self._security_status_var, anchor="w", justify="left")
        self.theme.apply_to_widget(status, "label")
        status.pack(fill=tk.X)
        wrap_label(status, panel)
        actions = tk.Frame(panel)
        self._security_actions = actions
        self.theme.apply_to_widget(actions, "main_window")
        actions.pack(fill=tk.X, pady=(4, 0))
        self._security_action_btn = tk.Button(actions, command=self._on_security_action)
        self.theme.apply_to_widget(self._security_action_btn, "button_secondary")
        self._security_action_btn.pack(side=tk.LEFT)
        self._reset_btn = tk.Button(actions, text="Delete all keys and disable…", command=self._on_forgot_passphrase_reset)
        self.theme.apply_to_widget(self._reset_btn, "button_danger")
        self._reset_btn.pack(side=tk.LEFT)
        flow_row(actions)

    def _load_security_policy(self):
        try:
            with self._open_store_connection() as conn:
                self._secure_mode_enabled = km_store.secure_mode_enabled(conn)
                configured = km_store.passphrase_is_configured(conn)
            if not self._secure_mode_enabled:
                self._session_keys = None
                self._security_state = "Off"
            elif not configured:
                self._session_keys = None
                self._security_state = "Setup needed"
            elif self._session_keys:
                self._security_state = "Unlocked"
            else:
                self._security_state = "Locked"
        except Exception as exc:
            self._session_keys = None
            self._secure_mode_enabled = True  # Fail closed on unreadable policy.
            self._security_state = "Storage problem"
            self._status_var.set(security_error_message(exc))
        self._refresh_security_controls()

    def _initialize_security_session(self):
        # Opening only reads state; setup/unlock is always a deliberate action.
        self._session_keys = None
        self._load_security_policy()

    def _refresh_security_controls(self):
        if not hasattr(self, "_security_action_btn"):
            return
        state = self._security_state
        labels = {
            "Off": "Enable secure storage…", "Setup needed": "Set passphrase…",
            "Locked": "Unlock…", "Unlocked": "Unlocked", "Storage problem": "Retry storage",
        }
        self._security_status_var.set(f"Secure storage: {state}" + (
            " — new keys are saved as plaintext." if state == "Off" else ""
        ))
        self._security_action_btn.configure(text=labels[state], state=tk.DISABLED if state == "Unlocked" else tk.NORMAL)
        self._reset_btn.configure(state=tk.NORMAL)
        if hasattr(self, "_security_actions"):
            # flow_row must remeasure when text changes without a frame resize.
            self._security_actions.event_generate("<Configure>", when="tail")

    def _ask_passphrase(self, *, setup=False):
        return PassphraseDialog(self.window, self.theme, setup=setup).show()

    def _on_security_action(self):
        self._load_security_policy()
        if self._security_state in {"Off", "Setup needed"}:
            self._setup_initial_passphrase()
        elif self._security_state == "Locked":
            self._unlock_session()
        elif self._security_state in {"Unlocked", "Storage problem"}:
            return
        else:
            raise RuntimeError("Unknown Keymaster security state")

    def _security_action_failed(self, title, exc):
        self._session_keys = None
        self._load_security_policy()
        self._load_entries()
        message = security_error_message(exc)
        self._status_var.set(message)
        messagebox.showerror(title, message, parent=self.window)

    def _setup_initial_passphrase(self) -> bool:
        value = self._ask_passphrase(setup=True)
        if value is None:
            self._status_var.set("Passphrase setup cancelled.")
            return False
        try:
            with self._open_store_connection() as conn:
                keys, converted = km_store.enable_secure_storage(conn, value)
                conn.commit()
        except Exception as exc:
            self._security_action_failed("Setup failed", exc)
            return False
        self._session_keys = keys  # Publish only after a successful commit.
        self._load_security_policy()
        self._load_entries()
        self._status_var.set(f"Secure storage enabled. Encrypted {converted} existing key(s).")
        return True

    def _unlock_session(self) -> bool:
        self._load_security_policy()
        if self._security_state == "Off":
            return True
        if self._security_state == "Setup needed":
            return self._setup_initial_passphrase()
        if self._security_state == "Storage problem":
            return False
        value = self._ask_passphrase()
        if value is None:
            self._status_var.set("Unlock cancelled.")
            return False
        self._session_keys = None
        try:
            with self._open_store_connection() as conn:
                keys = km_store.unlock_session_keys(conn, value)
                km_store.migrate_legacy_plaintext_rows(conn, session_keys=keys)
                conn.commit()
        except Exception as exc:
            self._security_action_failed("Unlock failed", exc)
            return False
        self._session_keys = keys
        self._load_security_policy()
        self._load_entries()
        self._status_var.set("Keymaster unlocked.")
        return True

    def _ensure_unlocked_for_sensitive_action(self) -> bool:
        if not self._secure_mode_enabled:
            return True
        if self._session_keys:
            try:
                with self._open_store_connection() as conn:
                    km_store.validate_session_keys(conn, self._session_keys)
            except Exception as exc:
                self._security_action_failed("Unlock required", exc)
                return False
            return True
        return self._unlock_session()

    def _on_forgot_passphrase_reset(self):
        if not messagebox.askyesno(
            "Delete all keys and disable secure storage?",
            "Permanently delete ALL saved Keymaster keys, including Shodan keys and "
            "Analyst LLM tokens, and remove the Keymaster passphrase?\n\n"
            "Secure storage will stay off. New keys will be stored as plaintext until "
            "you enable it again.\n\n"
            "Credentials already applied elsewhere and backups remain. This action "
            "cannot be undone and does not securely erase disk contents.",
            parent=self.window, icon="warning", default=messagebox.NO,
        ):
            return
        try:
            with self._open_store_connection() as conn:
                km_store.destructive_reset(conn, secure_mode=False)
                conn.commit()
        except Exception as exc:
            message = security_error_message(exc)
            self._status_var.set(message)
            messagebox.showerror("Reset failed", message, parent=self.window)
            return
        self._session_keys = None
        self._row_by_iid.clear()
        self._query_credit_by_key_id.clear()
        self._credits_refresh_generation += 1
        self._credits_refresh_inflight = False
        self._load_security_policy()
        self._load_entries()
        self._set_recheck_state(inflight=False)
        self._status_var.set("All Keymaster keys deleted. Secure storage is off.")
