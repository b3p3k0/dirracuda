"""
Keymaster window.

Modeless singleton window for managing reusable API keys.
"""

from __future__ import annotations

import json
import threading
import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from typing import Any, Optional

from experimental.keymaster import store as km_store
from experimental.keymaster.models import PROVIDER_SHODAN, DuplicateKeyError
from gui.utils import safe_messagebox as messagebox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import get_theme

_WINDOW_INSTANCE = None

_WINDOW_SETTINGS_NAME = "keymaster"
_DEFAULT_GEOMETRY = "860x480"
_AUTO_CHECK_SETTING_KEY = "keymaster.auto_check_query_credits"
MAX_BURST_CREDIT_CHECKS = 5

_COL_HEADERS = {
    "label": "Label",
    "key_preview": "Key Preview",
    "query_credits": "Query Credits",
    "notes": "Notes",
    "last_used_at": "Last Used",
}
_COL_WIDTHS = {
    "label": 180,
    "key_preview": 180,
    "query_credits": 110,
    "notes": 220,
    "last_used_at": 150,
}
_COLS = ["label", "key_preview", "query_credits", "notes", "last_used_at"]

_QUERY_CREDITS_NOT_CHECKED = "Not checked"
_QUERY_CREDITS_CHECKING = "Checking..."
_QUERY_CREDITS_INVALID = "Invalid key"
_QUERY_CREDITS_ERROR = "Error"

_QUERY_CREDIT_MAX_ATTEMPTS = 3
_QUERY_CREDIT_RETRY_DELAY_SECONDS = 0.85
_QUERY_CREDIT_INTER_REQUEST_DELAY_SECONDS = 0.35
_OVER_LIMIT_STARTUP_STATUS = (
    f"Auto check skipped: more than {MAX_BURST_CREDIT_CHECKS} saved keys. "
    "Use Recheck Selected."
)
_OVER_LIMIT_RECHECK_STATUS = (
    f"Recheck All is disabled when you have more than {MAX_BURST_CREDIT_CHECKS} saved keys. "
    "Use Recheck Selected."
)


def _classify_query_credit_error(exc: Exception) -> str:
    """Map API exceptions to a safe UI status string."""
    text = str(exc or "").strip().lower()
    invalid_markers = (
        "invalid api key",
        "invalid key",
        "invalid apikey",
        "api key is invalid",
        "unauthorized",
        "forbidden",
        "access denied",
    )
    if any(marker in text for marker in invalid_markers):
        return _QUERY_CREDITS_INVALID
    return _QUERY_CREDITS_ERROR


def _is_retryable_query_credit_error(exc: Exception) -> bool:
    """Identify transient API/network errors worth retrying."""
    text = str(exc or "").strip().lower()
    retry_markers = (
        "rate limit",
        "too many requests",
        "429",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "try again",
    )
    return any(marker in text for marker in retry_markers)


def _window_instance_is_live(instance) -> bool:
    if instance is None:
        return False
    try:
        return bool(instance.window.winfo_exists())
    except Exception:
        return False


def _mask_key(api_key: str) -> str:
    """Mask an API key for display.

    len > 8  : first4 + ('*' * (len-8)) + last4
    len <= 8 : '*' * max(4, len)  — never reveals full key
    """
    key = str(api_key or "")
    n = len(key)
    if n > 8:
        return key[:4] + ("*" * (n - 8)) + key[-4:]
    return "*" * max(4, n)


class _KeyEditorDialog:
    """Modal Add/Edit dialog for one Keymaster entry."""

    def __init__(
        self,
        parent: tk.Widget,
        theme,
        *,
        title: str,
        label: str = "",
        api_key: str = "",
        notes: str = "",
    ) -> None:
        self.parent = parent
        self.theme = theme
        self.result: Optional[dict] = None

        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("600x340")
        self.dialog.resizable(True, True)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.theme.apply_to_widget(self.dialog, "main_window")

        outer = tk.Frame(self.dialog)
        self.theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        label_row = tk.Frame(outer)
        self.theme.apply_to_widget(label_row, "main_window")
        label_row.pack(fill=tk.X, pady=(0, 8))

        label_lbl = tk.Label(label_row, text="Label:", width=10, anchor="w")
        self.theme.apply_to_widget(label_lbl, "label")
        label_lbl.pack(side=tk.LEFT)

        self.label_var = tk.StringVar(value=label)
        label_entry = tk.Entry(label_row, textvariable=self.label_var)
        self.theme.apply_to_widget(label_entry, "entry")
        label_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        key_row = tk.Frame(outer)
        self.theme.apply_to_widget(key_row, "main_window")
        key_row.pack(fill=tk.X, pady=(0, 8))

        key_lbl = tk.Label(key_row, text="API Key:", width=10, anchor="w")
        self.theme.apply_to_widget(key_lbl, "label")
        key_lbl.pack(side=tk.LEFT)

        self.key_var = tk.StringVar(value=api_key)
        key_entry = tk.Entry(key_row, textvariable=self.key_var, show="*")
        self.theme.apply_to_widget(key_entry, "entry")
        key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        notes_lbl = tk.Label(outer, text="Notes:", anchor="w")
        self.theme.apply_to_widget(notes_lbl, "label")
        notes_lbl.pack(anchor="w", pady=(0, 4))

        self.notes_text = tk.Text(outer, height=6, wrap=tk.WORD)
        self.theme.apply_to_widget(self.notes_text, "text")
        self.notes_text.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        if notes:
            self.notes_text.insert("1.0", notes)

        self.error_var = tk.StringVar(value="")
        error_label = tk.Label(outer, textvariable=self.error_var, anchor="w")
        self.theme.apply_to_widget(error_label, "label")
        error_label.configure(fg=self.theme.colors["error"])
        error_label.pack(fill=tk.X, pady=(0, 6))

        btn_row = tk.Frame(outer)
        self.theme.apply_to_widget(btn_row, "main_window")
        btn_row.pack(fill=tk.X)

        save_btn = tk.Button(btn_row, text="Save", command=self._on_save)
        self.theme.apply_to_widget(save_btn, "button_primary")
        save_btn.pack(side=tk.RIGHT)

        cancel_btn = tk.Button(btn_row, text="Cancel", command=self._on_cancel)
        self.theme.apply_to_widget(cancel_btn, "button_secondary")
        cancel_btn.pack(side=tk.RIGHT, padx=(0, 8))

        self.dialog.bind("<Escape>", lambda _e: self._on_cancel())
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel)
        ensure_dialog_focus(self.dialog, parent)
        label_entry.focus_set()

    def _on_save(self) -> None:
        label = str(self.label_var.get() or "").strip()
        api_key = str(self.key_var.get() or "").strip()
        if not label:
            self.error_var.set("Label is required.")
            return
        if not api_key:
            self.error_var.set("API Key is required.")
            return
        self.result = {
            "label": label,
            "api_key": api_key,
            "notes": str(self.notes_text.get("1.0", tk.END) or "").strip(),
        }
        self.dialog.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.dialog.destroy()

    def show(self) -> Optional[dict]:
        self.parent.wait_window(self.dialog)
        return self.result


class _SimpleDeleteConfirmDialog:
    """Simple delete confirmation — no mute option per SPEC Q4."""

    def __init__(self, parent: tk.Widget, theme, *, label_text: str) -> None:
        self.parent = parent
        self.theme = theme
        self.confirmed = False

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Confirm Delete")
        self.dialog.geometry("460x160")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.theme.apply_to_widget(self.dialog, "main_window")

        outer = tk.Frame(self.dialog)
        self.theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        lbl = tk.Label(
            outer,
            text=f"Delete selected key entry?\n\n\"{label_text}\"",
            justify="left",
            anchor="w",
            wraplength=430,
        )
        self.theme.apply_to_widget(lbl, "label")
        lbl.pack(fill=tk.X, pady=(0, 16))

        btn_row = tk.Frame(outer)
        self.theme.apply_to_widget(btn_row, "main_window")
        btn_row.pack(fill=tk.X)

        delete_btn = tk.Button(btn_row, text="Delete", command=self._on_confirm)
        self.theme.apply_to_widget(delete_btn, "button_danger")
        delete_btn.pack(side=tk.RIGHT)

        cancel_btn = tk.Button(btn_row, text="Cancel", command=self._on_cancel)
        self.theme.apply_to_widget(cancel_btn, "button_secondary")
        cancel_btn.pack(side=tk.RIGHT, padx=(0, 8))

        self.dialog.bind("<Escape>", lambda _e: self._on_cancel())
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel)
        ensure_dialog_focus(self.dialog, parent)

    def _on_confirm(self) -> None:
        self.confirmed = True
        self.dialog.destroy()

    def _on_cancel(self) -> None:
        self.confirmed = False
        self.dialog.destroy()

    def show(self) -> bool:
        self.parent.wait_window(self.dialog)
        return self.confirmed


class KeymasterWindow:
    """Modeless Keymaster API key manager window."""

    def __init__(
        self,
        parent: tk.Widget,
        *,
        settings_manager=None,
        db_path: Optional[Path] = None,
        config_path: Optional[str] = None,
    ) -> None:
        self.parent = parent
        self.settings_manager = settings_manager
        self.db_path = db_path
        self._context_config_path = str(config_path or "").strip() or None
        self.theme = get_theme()

        self._row_by_iid: dict[str, dict] = {}
        self._context_menu: Optional[tk.Menu] = None
        self._query_credit_by_key_id: dict[int, str] = {}
        self._credits_refresh_inflight = False
        self._credits_refresh_generation = 0
        self._total_saved_keys = 0

        self._ensure_sidecar_ready()
        self.window = tk.Toplevel(parent)
        self.window.title("Keymaster")
        self.window.geometry(_DEFAULT_GEOMETRY)
        self.window.minsize(640, 360)
        self.theme.apply_to_widget(self.window, "main_window")

        self._restore_window_state()
        self._build_ui()
        self._load_entries()
        self._schedule_startup_credit_refresh()

        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self.window.bind("<Escape>", lambda _e: self._on_close())

    # ------------------------------------------------------------------
    # Sidecar helpers
    # ------------------------------------------------------------------

    def _ensure_sidecar_ready(self) -> None:
        km_store.init_db(self.db_path)

    def _open_store_connection(self):
        return km_store.open_connection(self.db_path)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = tk.Frame(self.window)
        self.theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        heading = tk.Label(
            outer,
            text="Manage reusable API keys for rapid testing key rotation.",
            anchor="w",
            justify="left",
        )
        self.theme.apply_to_widget(heading, "label")
        heading.pack(fill=tk.X, pady=(0, 8))

        search_row = tk.Frame(outer)
        self.theme.apply_to_widget(search_row, "main_window")
        search_row.pack(fill=tk.X, pady=(0, 6))

        search_lbl = tk.Label(search_row, text="Search:", anchor="w")
        self.theme.apply_to_widget(search_lbl, "label")
        search_lbl.pack(side=tk.LEFT)

        self._search_var = tk.StringVar(value="")
        search_entry = tk.Entry(search_row, textvariable=self._search_var, width=40)
        self.theme.apply_to_widget(search_entry, "entry")
        search_entry.pack(side=tk.LEFT, padx=(6, 0))
        self._search_var.trace_add("write", lambda *_: self._load_entries())

        self._auto_check_var = tk.BooleanVar(value=self._read_auto_check_setting())
        auto_check_cb = tk.Checkbutton(
            search_row,
            text="Auto check",
            variable=self._auto_check_var,
        )
        self.theme.apply_to_widget(auto_check_cb, "checkbox")
        auto_check_cb.pack(side=tk.RIGHT)
        self._auto_check_var.trace_add("write", lambda *_: self._on_auto_check_toggled())

        tree_frame = tk.Frame(outer)
        self.theme.apply_to_widget(tree_frame, "main_window")
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 4))

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        self._tree = ttk.Treeview(
            tree_frame,
            columns=_COLS,
            show="headings",
            selectmode="browse",
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self._tree.yview)

        for col in _COLS:
            self._tree.heading(col, text=_COL_HEADERS[col])
            self._tree.column(col, width=_COL_WIDTHS[col], minwidth=50, anchor="w")

        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._tree.bind("<<TreeviewSelect>>", lambda _e: self._on_selection_changed())
        self._tree.bind("<Button-3>", self._on_right_click)
        self._tree.bind("<Double-1>", self._on_tree_double_click)

        self._status_var = tk.StringVar(value="")
        status_lbl = tk.Label(outer, textvariable=self._status_var, anchor="w")
        self.theme.apply_to_widget(status_lbl, "label")
        status_lbl.pack(fill=tk.X, pady=(0, 4))

        btn_row = tk.Frame(outer)
        self.theme.apply_to_widget(btn_row, "main_window")
        btn_row.pack(fill=tk.X)

        add_btn = tk.Button(btn_row, text="Add", command=self._on_add)
        self.theme.apply_to_widget(add_btn, "button_secondary")
        add_btn.pack(side=tk.LEFT, padx=(0, 6))

        self._recheck_btn = tk.Button(btn_row, text="Recheck All", command=self._on_recheck_all)
        self.theme.apply_to_widget(self._recheck_btn, "button_secondary")
        self._recheck_btn.pack(side=tk.LEFT, padx=(0, 6))

        self._recheck_selected_btn = tk.Button(
            btn_row,
            text="Recheck Selected",
            command=self._on_recheck_selected,
        )
        self.theme.apply_to_widget(self._recheck_selected_btn, "button_secondary")
        self._recheck_selected_btn.configure(state=tk.DISABLED)
        self._recheck_selected_btn.pack(side=tk.LEFT, padx=(0, 6))

        self._apply_btn = tk.Button(btn_row, text="Apply", command=self._apply_selected_key)
        self.theme.apply_to_widget(self._apply_btn, "button_primary")
        self._apply_btn.configure(state=tk.DISABLED)
        self._apply_btn.pack(side=tk.LEFT, padx=(0, 6))

        self._edit_btn = tk.Button(btn_row, text="Edit", command=self._on_edit)
        self.theme.apply_to_widget(self._edit_btn, "button_secondary")
        self._edit_btn.configure(state=tk.DISABLED)
        self._edit_btn.pack(side=tk.LEFT, padx=(0, 6))

        self._delete_btn = tk.Button(btn_row, text="Delete", command=self._on_delete)
        self.theme.apply_to_widget(self._delete_btn, "button_secondary")
        self._delete_btn.configure(state=tk.DISABLED)
        self._delete_btn.pack(side=tk.LEFT)

        self._context_menu = tk.Menu(self.window, tearoff=0)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_entries(self) -> None:
        search_text = str(self._search_var.get() or "")
        self._tree.delete(*self._tree.get_children())
        self._row_by_iid.clear()

        try:
            with self._open_store_connection() as conn:
                rows = km_store.list_keys(conn, PROVIDER_SHODAN, search_text=search_text)
                if search_text.strip():
                    all_rows = km_store.list_keys(conn, PROVIDER_SHODAN, search_text="")
                    self._total_saved_keys = len(all_rows)
                else:
                    self._total_saved_keys = len(rows)
        except Exception as exc:
            self._status_var.set(f"Load error: {exc}")
            self._total_saved_keys = 0
            self._set_action_state(None)
            return

        for row in rows:
            iid = str(row["key_id"])
            key_id = int(row["key_id"])
            preview = _mask_key(row["api_key"])
            last_used = str(row["last_used_at"] or "")
            credits = self._query_credit_by_key_id.get(
                key_id,
                _QUERY_CREDITS_NOT_CHECKED,
            )
            self._tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(row["label"], preview, credits, row["notes"], last_used),
            )
            self._row_by_iid[iid] = row

        if search_text.strip():
            self._status_var.set(f"{len(rows)} key(s) match search.")
        elif rows:
            self._status_var.set(f"{len(rows)} key(s).")
        else:
            self._status_var.set("No keys yet. Use Add to create one.")

        self._set_action_state(self._selected_row())

    def _selected_row(self) -> Optional[dict]:
        selected = self._tree.selection()
        if not selected:
            return None
        iid = str(selected[0])
        return self._row_by_iid.get(iid)

    def _set_action_state(self, row: Optional[dict]) -> None:
        state = tk.NORMAL if row is not None else tk.DISABLED
        self._apply_btn.configure(state=state)
        self._edit_btn.configure(state=state)
        self._delete_btn.configure(state=state)
        self._set_recheck_state(inflight=self._credits_refresh_inflight)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _build_context_menu(self, row: Optional[dict]) -> None:
        self._context_menu.delete(0, tk.END)
        self._context_menu.add_command(label="Add", command=self._on_add)
        recheck_all_state = (
            tk.NORMAL
            if (not self._credits_refresh_inflight and not self._is_over_burst_limit())
            else tk.DISABLED
        )
        self._context_menu.add_command(
            label="Recheck All",
            command=self._on_recheck_all,
            state=recheck_all_state,
        )
        if row is not None:
            selected_state = tk.NORMAL if not self._credits_refresh_inflight else tk.DISABLED
            self._context_menu.add_command(
                label="Recheck Selected",
                command=self._on_recheck_selected,
                state=selected_state,
            )
            self._context_menu.add_command(label="Apply", command=self._apply_selected_key)
            self._context_menu.add_command(label="Edit", command=self._on_edit)
            self._context_menu.add_command(label="Delete", command=self._on_delete)

    def _on_right_click(self, event) -> None:
        row_iid = self._tree.identify_row(event.y)
        if row_iid:
            self._tree.selection_set(row_iid)
            self._tree.focus(row_iid)
        else:
            self._tree.selection_remove(self._tree.selection())

        row = self._selected_row()
        self._set_action_state(row)
        self._build_context_menu(row)
        try:
            self._context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._context_menu.grab_release()

    def _on_tree_double_click(self, event) -> None:
        row_iid = self._tree.identify_row(event.y)
        if not row_iid:
            return
        self._tree.selection_set(row_iid)
        self._tree.focus(row_iid)
        self._set_action_state(self._selected_row())
        self._apply_selected_key()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        self._set_action_state(self._selected_row())

    # ------------------------------------------------------------------
    # CRUD actions
    # ------------------------------------------------------------------

    def _on_add(self) -> None:
        dialog = _KeyEditorDialog(self.window, self.theme, title="Add API Key")
        payload = dialog.show()
        if payload is None:
            return
        try:
            with self._open_store_connection() as conn:
                km_store.create_key(
                    conn,
                    PROVIDER_SHODAN,
                    payload["label"],
                    payload["api_key"],
                    payload.get("notes"),
                )
                conn.commit()
        except DuplicateKeyError as exc:
            messagebox.showerror("Duplicate Key", str(exc), parent=self.window)
            return
        except Exception as exc:
            messagebox.showerror("Add Failed", str(exc), parent=self.window)
            return
        self._load_entries()

    def _on_edit(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        dialog = _KeyEditorDialog(
            self.window,
            self.theme,
            title="Edit API Key",
            label=str(row.get("label") or ""),
            api_key=str(row.get("api_key") or ""),
            notes=str(row.get("notes") or ""),
        )
        payload = dialog.show()
        if payload is None:
            return
        try:
            with self._open_store_connection() as conn:
                km_store.update_key(
                    conn,
                    int(row["key_id"]),
                    payload["label"],
                    payload["api_key"],
                    payload.get("notes"),
                )
                conn.commit()
        except DuplicateKeyError as exc:
            messagebox.showerror("Duplicate Key", str(exc), parent=self.window)
            return
        except Exception as exc:
            messagebox.showerror("Edit Failed", str(exc), parent=self.window)
            return
        self._load_entries()

    def _on_delete(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        label_text = str(row.get("label") or "").strip()
        dialog = _SimpleDeleteConfirmDialog(
            self.window,
            self.theme,
            label_text=label_text,
        )
        if not dialog.show():
            return
        try:
            with self._open_store_connection() as conn:
                km_store.delete_key(conn, int(row["key_id"]))
                conn.commit()
        except Exception as exc:
            messagebox.showerror("Delete Failed", str(exc), parent=self.window)
            return
        self._load_entries()

    def _on_recheck_all(self) -> None:
        self._start_query_credits_refresh(user_initiated=True, startup=False)

    def _on_recheck_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            self._status_var.set("Select a key to recheck.")
            return
        try:
            key_id = int(row["key_id"])
        except Exception:
            self._status_var.set("Select a key to recheck.")
            return
        self._start_query_credits_refresh(
            user_initiated=True,
            only_key_ids={key_id},
        )

    def _schedule_startup_credit_refresh(self) -> None:
        """Queue one startup refresh without blocking initial render."""
        try:
            self.window.after(
                25,
                self._run_startup_credit_refresh,
            )
        except Exception:
            pass

    def _run_startup_credit_refresh(self) -> None:
        if not self._is_auto_check_enabled():
            self._status_var.set("Auto check is off.")
            self._set_recheck_state(inflight=False)
            return
        self._start_query_credits_refresh(user_initiated=False, startup=True)

    def _read_auto_check_setting(self) -> bool:
        if self.settings_manager is None:
            return True
        try:
            return bool(
                self.settings_manager.get_setting(
                    _AUTO_CHECK_SETTING_KEY,
                    True,
                )
            )
        except Exception:
            return True

    def _is_auto_check_enabled(self) -> bool:
        try:
            return bool(self._auto_check_var.get())
        except Exception:
            return True

    def _on_auto_check_toggled(self) -> None:
        enabled = self._is_auto_check_enabled()
        if self.settings_manager is not None:
            try:
                self.settings_manager.set_setting(_AUTO_CHECK_SETTING_KEY, enabled)
            except Exception:
                pass
        self._status_var.set("Auto check enabled." if enabled else "Auto check disabled.")
        self._set_recheck_state(inflight=self._credits_refresh_inflight)

    def _is_over_burst_limit(self) -> bool:
        return int(self._total_saved_keys) > MAX_BURST_CREDIT_CHECKS

    def _guard_burst_credit_check(self, *, total_keys: int, startup: bool) -> bool:
        if int(total_keys) <= MAX_BURST_CREDIT_CHECKS:
            return True
        self._status_var.set(_OVER_LIMIT_STARTUP_STATUS if startup else _OVER_LIMIT_RECHECK_STATUS)
        self._set_recheck_state(inflight=False)
        return False

    def _set_recheck_state(self, *, inflight: bool) -> None:
        recheck_all_enabled = (not inflight) and (not self._is_over_burst_limit())
        state = tk.NORMAL if recheck_all_enabled else tk.DISABLED
        try:
            self._recheck_btn.configure(state=state)
        except Exception:
            pass
        try:
            selected_row = self._selected_row()
        except Exception:
            selected_row = None
        selected_state = tk.NORMAL if (not inflight and selected_row is not None) else tk.DISABLED
        try:
            self._recheck_selected_btn.configure(state=selected_state)
        except Exception:
            pass

    def _rows_for_credit_refresh(self, *, only_key_ids: Optional[set[int]] = None) -> list[dict]:
        """Load all keys for provider, independent of search/filter state."""
        with self._open_store_connection() as conn:
            rows = km_store.list_keys(conn, PROVIDER_SHODAN, search_text="")
        if only_key_ids:
            filtered = []
            for row in rows:
                try:
                    key_id = int(row.get("key_id"))
                except Exception:
                    continue
                if key_id in only_key_ids:
                    filtered.append(row)
            return filtered
        return rows

    def _start_query_credits_refresh(
        self,
        *,
        user_initiated: bool,
        only_key_ids: Optional[set[int]] = None,
        startup: bool = False,
    ) -> None:
        if self._credits_refresh_inflight:
            if user_initiated:
                self._status_var.set("Query credit check already in progress.")
            return

        try:
            rows = self._rows_for_credit_refresh(only_key_ids=only_key_ids)
        except Exception:
            if user_initiated:
                self._status_var.set("Query credit check failed.")
            return

        if not rows:
            if user_initiated:
                self._status_var.set("No keys to check.")
            return

        if only_key_ids is None and not self._guard_burst_credit_check(
            total_keys=len(rows),
            startup=startup,
        ):
            return

        self._credits_refresh_inflight = True
        self._set_recheck_state(inflight=True)
        self._credits_refresh_generation += 1
        refresh_id = self._credits_refresh_generation

        for row in rows:
            try:
                key_id = int(row["key_id"])
            except Exception:
                continue
            self._query_credit_by_key_id[key_id] = _QUERY_CREDITS_CHECKING

        self._load_entries()
        if len(rows) == 1:
            self._status_var.set("Checking query credits for selected key...")
        else:
            self._status_var.set("Checking query credits...")

        worker = threading.Thread(
            target=self._run_query_credit_refresh_worker,
            args=(refresh_id, rows),
            daemon=True,
            name="keymaster-credit-refresh",
        )
        worker.start()

    def _run_query_credit_refresh_worker(self, refresh_id: int, rows: list[dict]) -> None:
        results: dict[int, str] = {}
        for index, row in enumerate(rows):
            try:
                key_id = int(row.get("key_id"))
            except Exception:
                continue

            api_key = str(row.get("api_key") or "").strip()
            if not api_key:
                results[key_id] = _QUERY_CREDITS_INVALID
                continue

            results[key_id] = self._fetch_query_credit_display(api_key)
            if index < (len(rows) - 1):
                time.sleep(_QUERY_CREDIT_INTER_REQUEST_DELAY_SECONDS)

        try:
            self.window.after(
                0,
                lambda: self._finish_query_credit_refresh(refresh_id, results),
            )
        except Exception:
            # Window likely closed; safe to drop worker result.
            pass

    def _fetch_query_credit_display(self, api_key: str) -> str:
        try:
            import shodan
        except Exception:
            return _QUERY_CREDITS_ERROR

        last_exc: Optional[Exception] = None
        for attempt in range(1, _QUERY_CREDIT_MAX_ATTEMPTS + 1):
            try:
                info = shodan.Shodan(api_key).info()
                break
            except Exception as exc:
                last_exc = exc
                if (
                    attempt < _QUERY_CREDIT_MAX_ATTEMPTS
                    and _is_retryable_query_credit_error(exc)
                ):
                    time.sleep(_QUERY_CREDIT_RETRY_DELAY_SECONDS)
                    continue
                return _classify_query_credit_error(exc)
        else:
            if last_exc is None:
                return _QUERY_CREDITS_ERROR
            return _classify_query_credit_error(last_exc)

        if not isinstance(info, dict):
            return _QUERY_CREDITS_ERROR

        credits = info.get("query_credits")
        if isinstance(credits, bool):
            return _QUERY_CREDITS_ERROR
        if isinstance(credits, int):
            return str(credits)
        if isinstance(credits, float):
            return str(int(credits))
        if isinstance(credits, str) and credits.strip():
            return credits.strip()
        return _QUERY_CREDITS_ERROR

    def _finish_query_credit_refresh(self, refresh_id: int, results: dict[int, str]) -> None:
        if refresh_id != self._credits_refresh_generation:
            return

        self._credits_refresh_inflight = False
        self._set_recheck_state(inflight=False)

        for key_id, display in results.items():
            self._query_credit_by_key_id[int(key_id)] = str(display or _QUERY_CREDITS_ERROR)

        try:
            if not bool(self.window.winfo_exists()):
                return
        except Exception:
            return

        self._load_entries()
        self._status_var.set(f"Updated query credits for {len(results)} key(s).")

    # ------------------------------------------------------------------
    # Apply (stub — full implementation in C3)
    # ------------------------------------------------------------------

    def _resolve_active_config_path(self) -> Optional[Path]:
        candidate = self._context_config_path
        if not candidate and self.settings_manager:
            try:
                candidate = self.settings_manager.get_setting("backend.config_path", None)
            except Exception:
                candidate = None
            if not candidate and hasattr(self.settings_manager, "get_smbseek_config_path"):
                try:
                    candidate = self.settings_manager.get_smbseek_config_path()
                except Exception:
                    candidate = None
        if not candidate:
            return None
        try:
            return Path(str(candidate)).expanduser().resolve()
        except Exception:
            return None

    def _apply_selected_key(self) -> None:
        row = self._selected_row()
        if row is None:
            messagebox.showwarning("No Selection", "Select a key to apply.", parent=self.window)
            return

        config_path = self._resolve_active_config_path()
        if config_path is None:
            messagebox.showerror(
                "No Config",
                "No active config file found. Open the app config to set a config path first.",
                parent=self.window,
            )
            return

        try:
            if config_path.exists():
                raw = config_path.read_text(encoding="utf-8")
                try:
                    data = json.loads(raw)
                except Exception:
                    messagebox.showerror(
                        "Invalid Config",
                        f"Config file contains invalid JSON — cannot write safely.\n{config_path}",
                        parent=self.window,
                    )
                    return
                if not isinstance(data, dict):
                    messagebox.showerror(
                        "Invalid Config",
                        f"Config file root is not a JSON object — cannot write safely.\n{config_path}",
                        parent=self.window,
                    )
                    return
            else:
                data = {}

            shodan_cfg = data.get("shodan")
            if not isinstance(shodan_cfg, dict):
                shodan_cfg = {}
                data["shodan"] = shodan_cfg
            shodan_cfg["api_key"] = row["api_key"]

            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            messagebox.showerror("Apply Failed", str(exc), parent=self.window)
            return

        try:
            with self._open_store_connection() as conn:
                km_store.touch_last_used(conn, int(row["key_id"]))
                conn.commit()
        except Exception:
            pass

        self._load_entries()
        self._status_var.set(f"Applied \"{row['label']}\".")

    # ------------------------------------------------------------------
    # Window lifecycle
    # ------------------------------------------------------------------

    def _restore_window_state(self) -> None:
        if self.settings_manager is None:
            return
        try:
            geometry = str(
                self.settings_manager.get_window_setting(
                    _WINDOW_SETTINGS_NAME, "geometry", _DEFAULT_GEOMETRY
                )
                or _DEFAULT_GEOMETRY
            )
            if geometry:
                self.window.geometry(geometry)
        except Exception:
            pass

    def _save_window_state(self) -> None:
        if self.settings_manager is None:
            return
        try:
            self.settings_manager.set_window_setting(
                _WINDOW_SETTINGS_NAME, "geometry", self.window.geometry()
            )
        except Exception:
            pass

    def update_config_context(self, config_path: Optional[str]) -> None:
        """Refresh the active config path used by apply operations."""
        normalized = str(config_path or "").strip()
        if normalized:
            self._context_config_path = normalized

    def focus_window(self) -> None:
        try:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
        except Exception:
            pass

    def _on_close(self) -> None:
        global _WINDOW_INSTANCE
        self._save_window_state()
        try:
            self.window.destroy()
        finally:
            if _WINDOW_INSTANCE is self:
                _WINDOW_INSTANCE = None


def show_keymaster_window(
    parent: tk.Widget,
    *,
    settings_manager=None,
    db_path: Optional[Path] = None,
    config_path: Optional[str] = None,
) -> None:
    """Open singleton Keymaster window or focus existing one."""
    global _WINDOW_INSTANCE
    if _window_instance_is_live(_WINDOW_INSTANCE):
        _WINDOW_INSTANCE.update_config_context(config_path)
        _WINDOW_INSTANCE.focus_window()
        return

    try:
        _WINDOW_INSTANCE = KeymasterWindow(
            parent,
            settings_manager=settings_manager,
            db_path=db_path,
            config_path=config_path,
        )
    except Exception as exc:
        _WINDOW_INSTANCE = None
        messagebox.showerror(
            "Keymaster Unavailable",
            f"Could not open Keymaster.\n\n{exc}",
            parent=parent,
        )
