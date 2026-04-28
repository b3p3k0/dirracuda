"""
Dorkbook window.

Modeless singleton window with SMB/FTP/HTTP tabs for managing dork recipes
stored in the Dorkbook sidecar DB.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont
from pathlib import Path
from typing import Any, Optional

from experimental.dorkbook import store as dork_store
from experimental.dorkbook.models import (
    PROTOCOL_FTP,
    PROTOCOL_HTTP,
    PROTOCOL_SMB,
    PROTOCOLS,
    ROW_KIND_BUILTIN,
    DuplicateEntryError,
    ReadOnlyEntryError,
)
from gui.utils import safe_messagebox as messagebox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.session_flags import (
    DORKBOOK_DELETE_CONFIRM_MUTE_KEY,
    get_flag,
    set_flag,
)
from gui.utils.style import get_theme
from gui.components.scan_dork_editor_dialog import populate_discovery_dork_from_dorkbook

_WINDOW_INSTANCE = None

_WINDOW_SETTINGS_NAME = "dorkbook"
_SETTINGS_ACTIVE_TAB_KEY = "dorkbook.active_protocol_tab"
_DEFAULT_GEOMETRY = "1000x560"

_COL_HEADERS = {
    "nickname": "Nickname",
    "query": "Query",
    "notes": "Notes",
}
_COL_WIDTHS = {
    "nickname": 220,
    "query": 520,
    "notes": 240,
}
_COLS = ["nickname", "query", "notes"]


def _window_instance_is_live(instance) -> bool:
    if instance is None:
        return False
    try:
        return bool(instance.window.winfo_exists())
    except Exception:
        return False


def _resolve_initial_protocol(settings_manager: Any, default: str = PROTOCOL_SMB) -> str:
    """Resolve last active Dorkbook tab from GUI settings."""
    if settings_manager is None:
        return default
    try:
        value = str(settings_manager.get_setting(_SETTINGS_ACTIVE_TAB_KEY, default) or default).strip().upper()
    except Exception:
        value = default
    return value if value in PROTOCOLS else default


def _is_builtin_row(row: Optional[dict]) -> bool:
    if not row:
        return False
    return str(row.get("row_kind") or "").strip().lower() == ROW_KIND_BUILTIN


def _clipboard_payload_for_row(row: dict) -> str:
    """v1 copy semantics: query text only."""
    return str(row.get("query") or "")


def _normalize_scan_query_config_path(config_path: Optional[Any]) -> Optional[str]:
    raw = str(config_path or "").strip()
    if not raw:
        return None
    try:
        return str(Path(raw).expanduser())
    except Exception:
        return None


class _EntryEditorDialog:
    """Modal Add/Edit dialog for one Dorkbook entry."""

    def __init__(
        self,
        parent: tk.Widget,
        theme,
        *,
        title: str,
        nickname: str = "",
        query: str = "",
        notes: str = "",
    ) -> None:
        self.parent = parent
        self.theme = theme
        self.result: Optional[dict] = None

        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("760x360")
        self.dialog.resizable(True, True)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.theme.apply_to_widget(self.dialog, "main_window")

        outer = tk.Frame(self.dialog)
        self.theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        nickname_row = tk.Frame(outer)
        self.theme.apply_to_widget(nickname_row, "main_window")
        nickname_row.pack(fill=tk.X, pady=(0, 8))

        nickname_label = tk.Label(nickname_row, text="Nickname:", width=10, anchor="w")
        self.theme.apply_to_widget(nickname_label, "label")
        nickname_label.pack(side=tk.LEFT)

        self.nickname_var = tk.StringVar(value=nickname)
        nickname_entry = tk.Entry(nickname_row, textvariable=self.nickname_var)
        self.theme.apply_to_widget(nickname_entry, "entry")
        nickname_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        query_row = tk.Frame(outer)
        self.theme.apply_to_widget(query_row, "main_window")
        query_row.pack(fill=tk.X, pady=(0, 8))

        query_label = tk.Label(query_row, text="Query:", width=10, anchor="w")
        self.theme.apply_to_widget(query_label, "label")
        query_label.pack(side=tk.LEFT)

        self.query_var = tk.StringVar(value=query)
        query_entry = tk.Entry(query_row, textvariable=self.query_var)
        self.theme.apply_to_widget(query_entry, "entry")
        query_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        notes_label = tk.Label(outer, text="Notes:", anchor="w")
        self.theme.apply_to_widget(notes_label, "label")
        notes_label.pack(anchor="w", pady=(0, 4))

        self.notes_text = tk.Text(outer, height=8, wrap=tk.WORD)
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
        query_entry.focus_set()

    def _on_save(self) -> None:
        query = str(self.query_var.get() or "").strip()
        if not query:
            self.error_var.set("Query is required.")
            return
        self.result = {
            "nickname": str(self.nickname_var.get() or "").strip(),
            "query": query,
            "notes": str(self.notes_text.get("1.0", tk.END) or "").strip(),
        }
        self.dialog.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.dialog.destroy()

    def show(self) -> Optional[dict]:
        self.parent.wait_window(self.dialog)
        return self.result


class _DeleteConfirmDialog:
    """Delete confirmation dialog with session mute checkbox."""

    def __init__(self, parent: tk.Widget, theme, *, prompt_text: str) -> None:
        self.parent = parent
        self.theme = theme
        self.confirmed = False
        self.mute_until_restart = False

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Confirm Delete")
        self.dialog.geometry("520x200")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.theme.apply_to_widget(self.dialog, "main_window")

        outer = tk.Frame(self.dialog)
        self.theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        label = tk.Label(outer, text=prompt_text, justify="left", anchor="w", wraplength=490)
        self.theme.apply_to_widget(label, "label")
        label.pack(fill=tk.X, pady=(0, 12))

        self._mute_var = tk.BooleanVar(value=False)
        mute_cb = tk.Checkbutton(
            outer,
            text="Hide this message (until app restart)",
            variable=self._mute_var,
        )
        self.theme.apply_to_widget(mute_cb, "checkbox")
        mute_cb.pack(anchor="w", pady=(0, 12))

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
        self.mute_until_restart = bool(self._mute_var.get())
        self.dialog.destroy()

    def _on_cancel(self) -> None:
        self.confirmed = False
        self.mute_until_restart = False
        self.dialog.destroy()

    def show(self) -> tuple[bool, bool]:
        self.parent.wait_window(self.dialog)
        return self.confirmed, self.mute_until_restart


class DorkbookWindow:
    """Modeless Dorkbook recipe manager window."""

    def __init__(
        self,
        parent: tk.Widget,
        *,
        settings_manager=None,
        db_path: Optional[Path] = None,
        scan_query_config_path: Optional[str] = None,
    ) -> None:
        self.parent = parent
        self.settings_manager = settings_manager
        self.db_path = db_path
        self._scan_query_config_path = _normalize_scan_query_config_path(scan_query_config_path)
        self.theme = get_theme()

        self._tab_by_protocol: dict[str, dict] = {}
        self._protocol_by_tab_id: dict[str, str] = {}

        self._ensure_sidecar_ready()
        self.window = tk.Toplevel(parent)
        self.window.title("Dorkbook")
        self.window.geometry(_DEFAULT_GEOMETRY)
        self.window.minsize(760, 420)
        self.theme.apply_to_widget(self.window, "main_window")

        self._restore_window_state()
        self._build_ui()
        self._load_all_tabs()

        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self.window.bind("<Escape>", lambda _e: self._on_close())

    def _build_ui(self) -> None:
        outer = tk.Frame(self.window)
        self.theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        heading = tk.Label(
            outer,
            text="Dorkbook stores reusable dork recipes by protocol.",
            anchor="w",
            justify="left",
        )
        self.theme.apply_to_widget(heading, "label")
        heading.pack(fill=tk.X, pady=(0, 8))

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        for protocol in PROTOCOLS:
            self._build_protocol_tab(protocol)

        initial_protocol = _resolve_initial_protocol(self.settings_manager, PROTOCOL_SMB)
        tab = self._tab_by_protocol.get(initial_protocol)
        if tab is not None:
            self.notebook.select(tab["frame"])

    def _build_protocol_tab(self, protocol: str) -> None:
        frame = tk.Frame(self.notebook)
        self.theme.apply_to_widget(frame, "main_window")
        self.notebook.add(frame, text=protocol)

        row_by_iid: dict[str, dict] = {}

        search_row = tk.Frame(frame)
        self.theme.apply_to_widget(search_row, "main_window")
        search_row.pack(fill=tk.X, padx=8, pady=(8, 4))

        search_label = tk.Label(search_row, text="Search:", anchor="w")
        self.theme.apply_to_widget(search_label, "label")
        search_label.pack(side=tk.LEFT)

        search_var = tk.StringVar(value="")
        search_entry = tk.Entry(search_row, textvariable=search_var, width=40)
        self.theme.apply_to_widget(search_entry, "entry")
        search_entry.pack(side=tk.LEFT, padx=(6, 0))
        search_var.trace_add("write", lambda *_: self._load_entries(protocol))

        tree_frame = tk.Frame(frame)
        self.theme.apply_to_widget(tree_frame, "main_window")
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        tree = ttk.Treeview(
            tree_frame,
            columns=_COLS,
            show="headings",
            selectmode="browse",
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=tree.yview)

        for col in _COLS:
            tree.heading(col, text=_COL_HEADERS[col])
            tree.column(col, width=_COL_WIDTHS[col], minwidth=50, anchor="w")

        builtin_font = tkfont.Font(
            family=self.theme.fonts["body"][0],
            size=self.theme.fonts["body"][1],
            slant="italic",
        )
        tree.tag_configure("builtin", font=builtin_font)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        tree.bind("<<TreeviewSelect>>", lambda _e: self._on_selection_changed(protocol))
        tree.bind("<Button-3>", lambda e: self._on_right_click(protocol, e))
        tree.bind("<Double-1>", lambda e: self._on_tree_double_click(protocol, e))

        status_var = tk.StringVar(value="")
        status_label = tk.Label(frame, textvariable=status_var, anchor="w")
        self.theme.apply_to_widget(status_label, "label")
        status_label.pack(fill=tk.X, padx=8, pady=(0, 4))

        btn_row = tk.Frame(frame)
        self.theme.apply_to_widget(btn_row, "main_window")
        btn_row.pack(fill=tk.X, padx=8, pady=(0, 8))

        add_btn = tk.Button(btn_row, text="Add", command=lambda: self._on_add(protocol))
        self.theme.apply_to_widget(add_btn, "button_secondary")
        add_btn.pack(side=tk.LEFT, padx=(0, 6))

        copy_btn = tk.Button(btn_row, text="Copy", command=lambda: self._on_copy(protocol))
        self.theme.apply_to_widget(copy_btn, "button_secondary")
        copy_btn.pack(side=tk.LEFT, padx=(0, 6))
        copy_btn.configure(state=tk.DISABLED)

        use_btn = tk.Button(
            btn_row,
            text="Use in Discovery Dorks",
            command=lambda: self._on_use_in_discovery_dorks(protocol),
        )
        self.theme.apply_to_widget(use_btn, "button_secondary")
        use_btn.pack(side=tk.LEFT, padx=(0, 6))
        use_btn.configure(state=tk.DISABLED)

        edit_btn = tk.Button(btn_row, text="Edit", command=lambda: self._on_edit(protocol))
        self.theme.apply_to_widget(edit_btn, "button_secondary")
        delete_btn = tk.Button(btn_row, text="Delete", command=lambda: self._on_delete(protocol))
        self.theme.apply_to_widget(delete_btn, "button_secondary")

        context_menu = tk.Menu(self.window, tearoff=0)

        tab_info = {
            "protocol": protocol,
            "frame": frame,
            "search_var": search_var,
            "tree": tree,
            "row_by_iid": row_by_iid,
            "status_var": status_var,
            "copy_btn": copy_btn,
            "use_btn": use_btn,
            "edit_btn": edit_btn,
            "delete_btn": delete_btn,
            "edit_delete_visible": False,
            "context_menu": context_menu,
        }
        self._tab_by_protocol[protocol] = tab_info
        self._protocol_by_tab_id[str(frame)] = protocol
        self._hide_edit_delete_buttons(protocol)

    def _ensure_sidecar_ready(self) -> None:
        dork_store.init_db(self.db_path)

    def _open_store_connection(self):
        return dork_store.open_connection(self.db_path)

    def _load_all_tabs(self) -> None:
        for protocol in PROTOCOLS:
            self._load_entries(protocol)

    def _load_entries(self, protocol: str) -> None:
        tab = self._tab_by_protocol[protocol]
        tree = tab["tree"]
        search_text = str(tab["search_var"].get() or "")

        tree.delete(*tree.get_children())
        tab["row_by_iid"].clear()

        try:
            with self._open_store_connection() as conn:
                rows = dork_store.list_entries(conn, protocol, search_text=search_text)
        except Exception as exc:
            tab["status_var"].set(f"Load error: {exc}")
            self._set_action_visibility(protocol, None)
            return

        for row in rows:
            iid = str(row["entry_id"])
            tags = ("builtin",) if _is_builtin_row(row) else ()
            tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(row["nickname"], row["query"], row["notes"]),
                tags=tags,
            )
            tab["row_by_iid"][iid] = row

        if search_text.strip():
            tab["status_var"].set(f"{len(rows)} recipe(s) match search.")
        elif rows:
            tab["status_var"].set(f"{len(rows)} recipe(s).")
        else:
            tab["status_var"].set("No recipes yet. Use Add to create one.")

        self._set_action_visibility(protocol, self._selected_row(protocol))

    def _selected_row(self, protocol: str) -> Optional[dict]:
        tab = self._tab_by_protocol[protocol]
        selected = tab["tree"].selection()
        if not selected:
            return None
        iid = str(selected[0])
        return tab["row_by_iid"].get(iid)

    def _show_edit_delete_buttons(self, protocol: str) -> None:
        tab = self._tab_by_protocol[protocol]
        if tab["edit_delete_visible"]:
            return
        tab["edit_btn"].pack(side=tk.LEFT, padx=(0, 6))
        tab["delete_btn"].pack(side=tk.LEFT, padx=(0, 6))
        tab["edit_delete_visible"] = True

    def _hide_edit_delete_buttons(self, protocol: str) -> None:
        tab = self._tab_by_protocol[protocol]
        if not tab["edit_delete_visible"]:
            return
        tab["edit_btn"].pack_forget()
        tab["delete_btn"].pack_forget()
        tab["edit_delete_visible"] = False

    def _set_action_visibility(self, protocol: str, row: Optional[dict]) -> None:
        tab = self._tab_by_protocol[protocol]
        if row is None:
            tab["copy_btn"].configure(state=tk.DISABLED)
            tab["use_btn"].configure(state=tk.DISABLED)
            self._hide_edit_delete_buttons(protocol)
            return

        tab["copy_btn"].configure(state=tk.NORMAL)
        tab["use_btn"].configure(state=tk.NORMAL)
        if _is_builtin_row(row):
            self._hide_edit_delete_buttons(protocol)
        else:
            self._show_edit_delete_buttons(protocol)

    def _build_context_menu(self, protocol: str, row: Optional[dict]) -> None:
        tab = self._tab_by_protocol[protocol]
        menu = tab["context_menu"]
        menu.delete(0, tk.END)
        menu.add_command(label="Add", command=lambda: self._on_add(protocol))

        if row is None:
            return

        menu.add_command(label="Copy", command=lambda: self._on_copy(protocol))
        menu.add_command(label="Use in Discovery Dorks", command=lambda: self._on_use_in_discovery_dorks(protocol))
        if not _is_builtin_row(row):
            menu.add_command(label="Edit", command=lambda: self._on_edit(protocol))
            menu.add_command(label="Delete", command=lambda: self._on_delete(protocol))

    def _on_right_click(self, protocol: str, event) -> None:
        tab = self._tab_by_protocol[protocol]
        tree = tab["tree"]

        row_iid = tree.identify_row(event.y)
        if row_iid:
            tree.selection_set(row_iid)
            tree.focus(row_iid)
        else:
            tree.selection_remove(tree.selection())

        row = self._selected_row(protocol)
        self._set_action_visibility(protocol, row)
        self._build_context_menu(protocol, row)
        try:
            tab["context_menu"].tk_popup(event.x_root, event.y_root)
        finally:
            tab["context_menu"].grab_release()

    def _on_tree_double_click(self, protocol: str, event) -> None:
        tab = self._tab_by_protocol[protocol]
        tree = tab["tree"]
        row_iid = tree.identify_row(event.y)
        if not row_iid:
            return
        tree.selection_set(row_iid)
        tree.focus(row_iid)
        self._set_action_visibility(protocol, self._selected_row(protocol))
        self._on_use_in_discovery_dorks(protocol)

    def _show_entry_editor(
        self,
        *,
        title: str,
        nickname: str = "",
        query: str = "",
        notes: str = "",
    ) -> Optional[dict]:
        dialog = _EntryEditorDialog(
            self.window,
            self.theme,
            title=title,
            nickname=nickname,
            query=query,
            notes=notes,
        )
        return dialog.show()

    def _confirm_delete(self, row: dict) -> bool:
        if get_flag(DORKBOOK_DELETE_CONFIRM_MUTE_KEY, False):
            return True

        label = str(row.get("nickname") or "").strip() or str(row.get("query") or "").strip()
        dialog = _DeleteConfirmDialog(
            self.window,
            self.theme,
            prompt_text=(
                "Delete the selected Dorkbook recipe?\n\n"
                f"{label}"
            ),
        )
        confirmed, mute_until_restart = dialog.show()
        if confirmed and mute_until_restart:
            set_flag(DORKBOOK_DELETE_CONFIRM_MUTE_KEY, True)
        return confirmed

    def _on_selection_changed(self, protocol: str) -> None:
        self._set_action_visibility(protocol, self._selected_row(protocol))

    def _on_add(self, protocol: str) -> None:
        payload = self._show_entry_editor(title=f"Add {protocol} Dork")
        if payload is None:
            return

        try:
            with self._open_store_connection() as conn:
                dork_store.create_entry(
                    conn,
                    protocol=protocol,
                    nickname=payload.get("nickname"),
                    query=payload.get("query", ""),
                    notes=payload.get("notes"),
                )
                conn.commit()
        except DuplicateEntryError as exc:
            messagebox.showerror("Duplicate Dork", str(exc), parent=self.window)
            return
        except Exception as exc:
            messagebox.showerror("Add Failed", str(exc), parent=self.window)
            return

        self._load_entries(protocol)

    def _on_copy(self, protocol: str) -> None:
        row = self._selected_row(protocol)
        if row is None:
            return
        payload = _clipboard_payload_for_row(row)
        self.window.clipboard_clear()
        self.window.clipboard_append(payload)
        self._tab_by_protocol[protocol]["status_var"].set("Copied query to clipboard.")

    def update_scan_query_context(self, scan_query_config_path: Optional[str]) -> None:
        normalized = _normalize_scan_query_config_path(scan_query_config_path)
        if normalized:
            self._scan_query_config_path = normalized

    def _resolve_scan_query_config_path(self) -> Optional[str]:
        if self._scan_query_config_path:
            return self._scan_query_config_path
        if self.settings_manager is None:
            return None
        try:
            candidate = str(self.settings_manager.get_setting("backend.config_path", "") or "").strip()
        except Exception:
            candidate = ""
        if not candidate and hasattr(self.settings_manager, "get_smbseek_config_path"):
            try:
                candidate = str(self.settings_manager.get_smbseek_config_path() or "").strip()
            except Exception:
                candidate = ""
        return _normalize_scan_query_config_path(candidate)

    def _on_use_in_discovery_dorks(self, protocol: str) -> None:
        row = self._selected_row(protocol)
        if row is None:
            return
        query = str(row.get("query") or "").strip()
        if not query:
            messagebox.showwarning(
                "Cannot Use Dork",
                "Selected row has no query text.",
                parent=self.window,
            )
            return

        config_path = self._resolve_scan_query_config_path()
        if not config_path:
            messagebox.showwarning(
                "Discovery Dorks Context Missing",
                "No scan config context is available.\nOpen Start Scan -> Edit Queries first, then try again.",
                parent=self.window,
            )
            return

        try:
            populate_discovery_dork_from_dorkbook(
                parent=self.window,
                config_path=config_path,
                protocol=protocol,
                query=query,
                settings_manager=self.settings_manager,
            )
        except Exception as exc:
            messagebox.showerror(
                "Use Dork Failed",
                f"Could not populate Discovery Dorks editor:\n{exc}",
                parent=self.window,
            )
            return

        self._tab_by_protocol[protocol]["status_var"].set(
            f"Loaded {protocol} query into Discovery Dorks editor. Click Save there to persist."
        )

    def _on_edit(self, protocol: str) -> None:
        row = self._selected_row(protocol)
        if row is None:
            return
        if _is_builtin_row(row):
            return

        payload = self._show_entry_editor(
            title=f"Edit {protocol} Dork",
            nickname=str(row.get("nickname") or ""),
            query=str(row.get("query") or ""),
            notes=str(row.get("notes") or ""),
        )
        if payload is None:
            return

        try:
            with self._open_store_connection() as conn:
                dork_store.update_entry(
                    conn,
                    entry_id=int(row["entry_id"]),
                    nickname=payload.get("nickname"),
                    query=payload.get("query", ""),
                    notes=payload.get("notes"),
                )
                conn.commit()
        except DuplicateEntryError as exc:
            messagebox.showerror("Duplicate Dork", str(exc), parent=self.window)
            return
        except ReadOnlyEntryError as exc:
            messagebox.showerror("Edit Not Allowed", str(exc), parent=self.window)
            return
        except Exception as exc:
            messagebox.showerror("Edit Failed", str(exc), parent=self.window)
            return

        self._load_entries(protocol)

    def _on_delete(self, protocol: str) -> None:
        row = self._selected_row(protocol)
        if row is None:
            return
        if _is_builtin_row(row):
            return
        if not self._confirm_delete(row):
            return

        try:
            with self._open_store_connection() as conn:
                dork_store.delete_entry(conn, int(row["entry_id"]))
                conn.commit()
        except ReadOnlyEntryError as exc:
            messagebox.showerror("Delete Not Allowed", str(exc), parent=self.window)
            return
        except Exception as exc:
            messagebox.showerror("Delete Failed", str(exc), parent=self.window)
            return

        self._load_entries(protocol)

    def _on_tab_changed(self, _event=None) -> None:
        if self.settings_manager is None:
            return
        try:
            tab_id = str(self.notebook.select())
            protocol = self._protocol_by_tab_id.get(tab_id)
            if protocol in PROTOCOLS:
                self.settings_manager.set_setting(_SETTINGS_ACTIVE_TAB_KEY, protocol)
        except Exception:
            pass

    def _restore_window_state(self) -> None:
        if self.settings_manager is None:
            return
        try:
            geometry = str(
                self.settings_manager.get_window_setting(
                    _WINDOW_SETTINGS_NAME,
                    "geometry",
                    _DEFAULT_GEOMETRY,
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
                _WINDOW_SETTINGS_NAME,
                "geometry",
                self.window.geometry(),
            )
        except Exception:
            pass
        try:
            tab_id = str(self.notebook.select())
            protocol = self._protocol_by_tab_id.get(tab_id)
            if protocol in PROTOCOLS:
                self.settings_manager.set_setting(_SETTINGS_ACTIVE_TAB_KEY, protocol)
        except Exception:
            pass

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


def show_dorkbook_window(
    parent: tk.Widget,
    *,
    settings_manager=None,
    db_path: Optional[Path] = None,
    scan_query_config_path: Optional[str] = None,
) -> None:
    """Open singleton Dorkbook window or focus existing one."""
    global _WINDOW_INSTANCE
    if _window_instance_is_live(_WINDOW_INSTANCE):
        _WINDOW_INSTANCE.update_scan_query_context(scan_query_config_path)
        _WINDOW_INSTANCE.focus_window()
        return

    try:
        _WINDOW_INSTANCE = DorkbookWindow(
            parent,
            settings_manager=settings_manager,
            db_path=db_path,
            scan_query_config_path=scan_query_config_path,
        )
    except Exception as exc:
        _WINDOW_INSTANCE = None
        messagebox.showerror(
            "Dorkbook Unavailable",
            f"Could not open Dorkbook.\n\n{exc}",
            parent=parent,
        )
