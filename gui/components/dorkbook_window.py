"""One provider-grouped Dorkbook library shared by all desktop launch surfaces."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import ttk, font as tkfont

from experimental.dorkbook import store as dork_store
from experimental.dorkbook.defaults import apply_default, read_defaults
from experimental.dorkbook.models import PROTOCOLS, ROW_KIND_BUILTIN
from gui.components.dorkbook_events import broadcast_applied
from gui.utils import safe_messagebox as messagebox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.session_flags import DORKBOOK_DELETE_CONFIRM_MUTE_KEY, get_flag, set_flag
from gui.utils.style import get_theme

_WINDOW_INSTANCE = None
_WINDOW_SETTINGS_NAME = "dorkbook"
_DEFAULT_GEOMETRY = "1120x720"
PROVIDER_LABELS = {"shodan": "Shodan", "self_hosted": "Self-hosted Search"}


def _window_instance_is_live(instance) -> bool:
    try:
        return instance is not None and bool(instance.window.winfo_exists())
    except Exception:
        return False


def _is_builtin_row(row) -> bool:
    return bool(row) and row.get("row_kind") == ROW_KIND_BUILTIN


def _clipboard_payload_for_row(row) -> str:
    return str(row.get("query") or "")


def _normalize_scan_query_config_path(config_path) -> Optional[str]:
    return str(Path(config_path).expanduser()) if config_path else None


def _destination(row) -> str:
    return "self_hosted" if row["provider"] == "self_hosted" else "shodan:" + row["protocol"]


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
        provider: str = "shodan",
        protocol: Optional[str] = "SMB",
        topic: str = "General",
        editing: bool = False,
    ) -> None:
        self.parent = parent
        self.theme = theme
        self.result: Optional[dict] = None

        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("780x440")
        self.dialog.resizable(True, True)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.theme.apply_to_widget(self.dialog, "main_window")

        outer = tk.Frame(self.dialog)
        self.theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        metadata = tk.Frame(outer)
        self.theme.apply_to_widget(metadata, "main_window")
        metadata.pack(fill=tk.X, pady=(0, 8))
        self.provider_var = tk.StringVar(value=PROVIDER_LABELS[provider])
        self.protocol_var = tk.StringVar(value=protocol or "HTTP")
        self.topic_var = tk.StringVar(value=topic)
        for label, variable, values in (
            ("Provider", self.provider_var, tuple(PROVIDER_LABELS.values())),
            ("Protocol", self.protocol_var, PROTOCOLS),
            ("Topic", self.topic_var, None),
        ):
            widget = tk.Label(metadata, text=label + ":")
            self.theme.apply_to_widget(widget, "label")
            widget.pack(side=tk.LEFT, padx=(0, 5))
            entry = ttk.Combobox(metadata, textvariable=variable, values=values or (), width=20)
            entry.configure(state="disabled" if editing and values else "readonly" if values else "normal")
            entry.pack(side=tk.LEFT, padx=(0, 10))
            if label == "Protocol":
                self.protocol_entry = entry
        self._editing = editing
        self.provider_var.trace_add("write", lambda *_: self._sync_protocol())
        self._sync_protocol()

        nickname_row = tk.Frame(outer)
        self.theme.apply_to_widget(nickname_row, "main_window")
        nickname_row.pack(fill=tk.X, pady=(0, 8))

        nickname_label = tk.Label(nickname_row, text="Name:", width=10, anchor="w")
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
        query_entry.focus_set()
        ensure_dialog_focus(self.dialog, parent)

    def _sync_protocol(self) -> None:
        self.protocol_entry.configure(
            state="disabled" if self._editing or self.provider_var.get() == PROVIDER_LABELS["self_hosted"] else "readonly"
        )

    def _on_save(self) -> None:
        query = str(self.query_var.get() or "").strip()
        if not query:
            self.error_var.set("Query is required.")
            return
        provider = next(key for key, value in PROVIDER_LABELS.items() if value == self.provider_var.get())
        self.result = {
            "provider": provider,
            "protocol": self.protocol_var.get() if provider == "shodan" else None,
            "topic": self.topic_var.get().strip() or "General",
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
    """Modeless singleton with explicit persistence and selection-only preview."""

    def __init__(self, parent, *, settings_manager=None, db_path=None,
                 scan_query_config_path=None, focus_provider=None):
        self.parent = parent
        self.settings_manager = settings_manager
        self.db_path = db_path
        self._scan_query_config_path = _normalize_scan_query_config_path(scan_query_config_path)
        self.theme = get_theme()
        self.rows = {}
        self.defaults = {}
        self._refresh_pending = None
        dork_store.init_db(db_path)
        self.window = tk.Toplevel(parent._root())
        self.window.title("Dorkbook")
        self.window.geometry(_DEFAULT_GEOMETRY)
        self.window.minsize(850, 560)
        self.theme.apply_to_widget(self.window, "main_window")
        self._restore_window_state()
        self._build_ui()
        self._load_entries()
        if focus_provider:
            self.focus_provider(focus_provider)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self.window.bind("<Escape>", lambda _event: self._on_close())
        self.window.bind("<FocusIn>", self._on_focus)
        self.window.bind("<<DorkbookApplied>>", lambda _event: self._load_entries())

    def _frame(self, parent):
        frame = tk.Frame(parent)
        self.theme.apply_to_widget(frame, "main_window")
        return frame

    def _label(self, parent, **kwargs):
        label = tk.Label(parent, **kwargs)
        self.theme.apply_to_widget(label, "label")
        return label

    def _build_ui(self):
        outer = self._frame(self.window)
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        heading = self._frame(outer)
        heading.pack(fill=tk.X)
        self._label(heading, text="Explore a dork, adapt its query, then apply it to your next search.", anchor="w").pack(side=tk.LEFT)
        jumps = self._frame(heading)
        jumps.pack(side=tk.RIGHT)
        self._label(jumps, text="Jump to:").pack(side=tk.LEFT, padx=(10, 4))
        for provider, label in PROVIDER_LABELS.items():
            ttk.Button(jumps, text=label, padding=(4, 0),
                       command=lambda key=provider: self.focus_provider(key, clear_filters=False)).pack(side=tk.LEFT, padx=2)
        filters = self._frame(outer)
        filters.pack(fill=tk.X, pady=10)
        self._label(filters, text="Find Dork:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar(value="")
        search = tk.Entry(filters, textvariable=self.search_var)
        self.theme.apply_to_widget(search, "entry")
        search.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 20))
        self._label(filters, text="Topic:").pack(side=tk.LEFT)
        self.topic_var = tk.StringVar(value="All topics")
        self.topic_combo = ttk.Combobox(filters, textvariable=self.topic_var, state="readonly", width=20)
        self.topic_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.search_var.trace_add("write", self._schedule_reload)
        self.topic_var.trace_add("write", self._schedule_reload)
        table = self._frame(outer)
        table.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(table, columns=("protocol", "topic", "query", "default"), selectmode="browse", height=14)
        self.tree.heading("#0", text="Provider / Dork")
        self.tree.column("#0", width=250, minwidth=160)
        for name, title, width in (("protocol", "Protocol", 70), ("topic", "Topic", 130),
                                   ("query", "Query preview", 420), ("default", "Default", 70)):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, minwidth=60, stretch=name == "query")
        self._builtin_font = tkfont.Font(family=self.theme.fonts["body"][0], size=self.theme.fonts["body"][1], slant="italic")
        self.tree.tag_configure("builtin", font=self._builtin_font)
        scrollbar = ttk.Scrollbar(table, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_selection_changed)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self._label(outer, text="Selected Dork", anchor="w").pack(fill=tk.X, pady=(10, 3))
        self.preview = tk.Text(outer, height=6, wrap=tk.WORD)
        self.theme.apply_to_widget(self.preview, "text")
        self.preview.pack(fill=tk.X)
        self.preview.configure(state=tk.DISABLED)
        self.status_var = tk.StringVar(value="")
        self._label(outer, textvariable=self.status_var, anchor="w").pack(fill=tk.X, pady=6)
        actions = self._frame(outer)
        actions.pack(fill=tk.X)
        self.buttons = {}
        for label, command in (("Add Dork", self._on_add), ("Edit Dork", self._on_edit),
                               ("Delete Dork", self._on_delete), ("Copy Query", self._on_copy),
                               ("Apply to Search", self._on_apply)):
            button = tk.Button(actions, text=label, command=command)
            if label == "Apply to Search":
                self.theme.apply_to_widget(button, "button_primary")
            else:
                self.theme.apply_to_widget(button, "button_secondary")
            button.pack(side=tk.RIGHT if label == "Apply to Search" else tk.LEFT, padx=(0, 8))
            self.buttons[label] = button
        self.context_menu = tk.Menu(self.window, tearoff=0)

    def _schedule_reload(self, *_args):
        if self._refresh_pending is not None:
            self.window.after_cancel(self._refresh_pending)
        self._refresh_pending = self.window.after(150, self._load_entries)

    def _load_entries(self):
        if self._refresh_pending is not None:
            self.window.after_cancel(self._refresh_pending)
        self._refresh_pending = None
        selected = self.tree.selection()
        opened = {key: self.tree.item(key, "open") for key in PROVIDER_LABELS if self.tree.exists(key)}
        try:
            with closing(dork_store.open_connection(self.db_path)) as conn:
                all_rows = dork_store.list_entries(conn, provider=None)
                rows = dork_store.list_entries(conn, provider=None, search_text=self.search_var.get(),
                                              topic=None if self.topic_var.get() == "All topics" else self.topic_var.get())
        except Exception as exc:
            self.status_var.set(f"Load failed: {exc}")
            return
        default_error = ""
        try:
            legacy_query = self.settings_manager.get_setting("unified_scan_dialog.searxng_query", "") if self.settings_manager else None
            self.defaults = read_defaults(self._resolve_scan_query_config_path(), legacy_query=legacy_query)
        except (OSError, ValueError, RuntimeError) as exc:
            self.defaults = {}
            default_error = f" Saved defaults unavailable: {exc}"
        self.topic_combo.configure(values=("All topics", *sorted({row["topic"] for row in all_rows})))
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        for provider, label in PROVIDER_LABELS.items():
            self.tree.insert("", tk.END, iid=provider, text=label, open=opened.get(provider, True))
        for row in rows:
            iid = str(row["entry_id"])
            self.rows[iid] = row
            self.tree.insert(row["provider"], tk.END, iid=iid, text=row["nickname"] or "Untitled dork",
                             values=(row["protocol"] or "—", row["topic"], row["query"],
                                     "✓" if self.defaults.get(_destination(row)) == row["query"].strip() else ""),
                             tags=("builtin",) if _is_builtin_row(row) else ())
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self.status_var.set(f"{len(rows)} dorks. Apply to Search saves a default; selecting only previews." + default_error)
        self._on_selection_changed()

    def _selected_row(self):
        selected = self.tree.selection()
        return self.rows.get(selected[0]) if selected else None

    def _on_selection_changed(self, _event=None):
        row = self._selected_row()
        for name in ("Copy Query", "Apply to Search"):
            self.buttons[name].configure(state=tk.NORMAL if row else tk.DISABLED)
        for name in ("Edit Dork", "Delete Dork"):
            self.buttons[name].configure(state=tk.NORMAL if row and not _is_builtin_row(row) else tk.DISABLED)
        self.preview.configure(state=tk.NORMAL)
        self.preview.delete("1.0", tk.END)
        if row:
            destination = PROVIDER_LABELS[row["provider"]] + (" · " + row["protocol"] if row["protocol"] else "")
            kind = "Built-in · read-only" if _is_builtin_row(row) else "Custom"
            self.preview.insert("1.0", f"{row['nickname'] or 'Untitled dork'} — {kind}\nApplies to: {destination}\n\n{row['query']}\n\n{row['notes'] or ''}")
        self.preview.configure(state=tk.DISABLED)

    def _on_tree_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self._on_selection_changed()
        # Keep Treeview's native expand/collapse behavior for provider headings.

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
        row = self._selected_row()
        menu = self.context_menu
        menu.delete(0, tk.END)
        menu.add_command(label="Add Dork", command=self._on_add)
        if row:
            menu.add_command(label="Copy Query", command=self._on_copy)
            menu.add_command(label="Apply to Search", command=self._on_apply)
            if not _is_builtin_row(row):
                menu.add_command(label="Edit Dork", command=self._on_edit)
                menu.add_command(label="Delete Dork", command=self._on_delete)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_apply(self):
        row = self._selected_row()
        if not row:
            return
        path = self._resolve_scan_query_config_path()
        try:
            query = apply_default(row["provider"], row["protocol"], row["query"], config_path=path)
        except Exception as exc:
            messagebox.showerror("Apply Failed", f"Could not save the search default:\n{exc}", parent=self.window)
            return
        broadcast_applied(self.window, path, _destination(row), query)
        self._load_entries()
        self.status_var.set(f"Applied to {PROVIDER_LABELS[row['provider']]}{(' · ' + row['protocol']) if row['protocol'] else ''}. Saved for future searches.")

    def _on_copy(self):
        row = self._selected_row()
        if row:
            self.window.clipboard_clear()
            self.window.clipboard_append(_clipboard_payload_for_row(row))
            self.status_var.set("Copied query to clipboard.")

    def _show_entry_editor(self, **kwargs):
        return _EntryEditorDialog(self.window, self.theme, **kwargs).show()

    def _on_add(self):
        selected = self.tree.selection()
        row = self._selected_row()
        provider = row["provider"] if row else selected[0] if selected and selected[0] in PROVIDER_LABELS else "shodan"
        payload = self._show_entry_editor(title="Add Dork", provider=provider, protocol=row["protocol"] if row else "HTTP")
        if payload is None:
            return
        try:
            with closing(dork_store.open_connection(self.db_path)) as conn:
                dork_store.create_entry(conn, **payload)
                conn.commit()
        except Exception as exc:
            messagebox.showerror("Add Failed", str(exc), parent=self.window)
            return
        self._load_entries()

    def _on_edit(self):
        row = self._selected_row()
        if not row or _is_builtin_row(row):
            return
        payload = self._show_entry_editor(title="Edit Dork", editing=True,
                                         **{key: row[key] or "" for key in ("nickname", "query", "notes", "provider", "protocol", "topic")})
        if payload is None:
            return
        payload.pop("provider")
        payload.pop("protocol")
        try:
            with closing(dork_store.open_connection(self.db_path)) as conn:
                dork_store.update_entry(conn, entry_id=row["entry_id"], **payload)
                conn.commit()
        except Exception as exc:
            messagebox.showerror("Edit Failed", str(exc), parent=self.window)
            return
        self._load_entries()

    def _confirm_delete(self, row):
        if get_flag(DORKBOOK_DELETE_CONFIRM_MUTE_KEY, False):
            return True
        confirmed, mute = _DeleteConfirmDialog(self.window, self.theme,
            prompt_text="Delete the selected dork?\n\n" + (row.get("nickname") or row["query"])).show()
        if confirmed and mute:
            set_flag(DORKBOOK_DELETE_CONFIRM_MUTE_KEY, True)
        return confirmed

    def _on_delete(self):
        row = self._selected_row()
        if not row or _is_builtin_row(row) or not self._confirm_delete(row):
            return
        try:
            with closing(dork_store.open_connection(self.db_path)) as conn:
                dork_store.delete_entry(conn, row["entry_id"])
                conn.commit()
        except Exception as exc:
            messagebox.showerror("Delete Failed", str(exc), parent=self.window)
            return
        self._load_entries()

    def update_scan_query_context(self, scan_query_config_path):
        normalized = _normalize_scan_query_config_path(scan_query_config_path)
        if normalized:
            self._scan_query_config_path = normalized

    def _resolve_scan_query_config_path(self):
        if self._scan_query_config_path:
            return self._scan_query_config_path
        if self.settings_manager:
            candidate = self.settings_manager.get_setting("backend.config_path", "")
            if not candidate and hasattr(self.settings_manager, "get_smbseek_config_path"):
                candidate = self.settings_manager.get_smbseek_config_path()
            return _normalize_scan_query_config_path(candidate)
        return None

    def focus_provider(self, provider, *, clear_filters=True):
        if provider in PROVIDER_LABELS:
            # Contextual launch resets stale filters; simple navigation keeps them.
            if clear_filters:
                self.search_var.set("")
                self.topic_var.set("All topics")
            if self._refresh_pending is not None:
                self.window.after_cancel(self._refresh_pending)
            self._load_entries()
            self.tree.item(provider, open=True)
            self.tree.selection_set(provider)
            self.tree.focus(provider)
            self.tree.see(provider)

    def _on_focus(self, event):
        if event.widget is self.window:
            self._load_entries()

    def _restore_window_state(self):
        if self.settings_manager:
            geometry = self.settings_manager.get_window_setting(_WINDOW_SETTINGS_NAME, "geometry", _DEFAULT_GEOMETRY)
            if geometry:
                self.window.geometry(geometry)

    def focus_window(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def _on_close(self):
        global _WINDOW_INSTANCE
        if self.settings_manager:
            self.settings_manager.set_window_setting(_WINDOW_SETTINGS_NAME, "geometry", self.window.geometry())
        if self._refresh_pending is not None:
            self.window.after_cancel(self._refresh_pending)
        try:
            self.window.destroy()
        finally:
            if _WINDOW_INSTANCE is self:
                _WINDOW_INSTANCE = None


def show_dorkbook_window(parent, *, settings_manager=None, db_path=None,
                         scan_query_config_path=None, focus_provider=None):
    """Open the same library from Accessories or a contextual query button."""
    global _WINDOW_INSTANCE
    if _window_instance_is_live(_WINDOW_INSTANCE):
        _WINDOW_INSTANCE.update_scan_query_context(scan_query_config_path)
        if focus_provider:
            _WINDOW_INSTANCE.focus_provider(focus_provider)
        _WINDOW_INSTANCE.focus_window()
        return
    try:
        kwargs = dict(settings_manager=settings_manager, db_path=db_path,
                      scan_query_config_path=scan_query_config_path)
        if focus_provider:
            kwargs["focus_provider"] = focus_provider
        _WINDOW_INSTANCE = DorkbookWindow(parent, **kwargs)
    except Exception as exc:
        _WINDOW_INSTANCE = None
        messagebox.showerror("Dorkbook Unavailable", f"Could not open Dorkbook.\n\n{exc}", parent=parent)
