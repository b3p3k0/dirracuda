"""
Reddit Post DB Browser

Table view of reddit_targets joined with reddit_posts.

Columns (display name -> row dict key):
  Target  -> target_normalized
  Proto   -> protocol
  Conf    -> parse_confidence
  Author  -> post_author
  NSFW    -> is_nsfw
  Notes   -> notes
  Probed  -> probe_status
  Preview -> probe_preview
  Checked -> probe_checked_at
  Date    -> created_at

Actions: Open in Explorer, Open Reddit Post, Probe Selected, Refresh, Clear DB,
Add to dirracuda DB

Filter scope: target_normalized only (MVP). Expanding to other fields
requires deliberate change here and in _apply_filter_and_sort.

Row identity: iid = str(target.id) — DB primary key used as Treeview iid so
that sort/filter never desynchronises selection from _row_by_iid.
"""

import sqlite3
import socket
import webbrowser
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import queue
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import ttk
import threading
from gui.utils import safe_messagebox as messagebox
from gui.utils.sidecar_promotion import (
    SidecarPromotionError,
    format_promotion_success,
)
from gui.utils.sidecar_probe import (
    PROBE_STATUS_UNPROBED,
    SidecarProbeOutcome,
    SidecarProbeUnsupported,
    build_indicator_patterns,
    build_probe_target_from_sidecar_row,
    run_sidecar_probe,
    utcnow_iso,
)
from gui.utils.probe_snapshot_details import format_probe_section

import experimental.redseek.store as store
from gui.components.pry_status_dialog import BatchStatusDialog
from gui.components.unified_browser_window import open_ftp_http_browser
from gui.utils.running_tasks import get_running_task_registry
from gui.utils.style import get_theme
from experimental.redseek import explorer_bridge
from experimental.redseek.models import RedditTarget


# ---------------------------------------------------------------------------
# Column layout
# ---------------------------------------------------------------------------

# Maps Treeview column id -> row dict key (SQL alias)
COLUMN_KEY_MAP = {
    "target": "target_normalized",
    "proto":  "protocol",
    "conf":   "parse_confidence",
    "author": "post_author",
    "nsfw":   "is_nsfw",
    "notes":  "notes",
    "probe":  "probe_status",
    "preview": "probe_preview",
    "checked": "probe_checked_at",
    "date":   "created_at",
}

COLUMNS = list(COLUMN_KEY_MAP.keys())

COL_HEADERS = {
    "target": "Target",
    "proto":  "Proto",
    "conf":   "Conf",
    "author": "Author",
    "nsfw":   "NSFW",
    "notes":  "Notes",
    "probe":  "Probed",
    "preview": "Probe Preview",
    "checked": "Checked",
    "date":   "Date",
}

COL_WIDTHS = {
    "target": 280,
    "proto":  60,
    "conf":   55,
    "author": 90,
    "nsfw":   45,
    "notes":  160,
    "probe":  70,
    "preview": 240,
    "checked": 150,
    "date":   140,
}

PROBE_STATUS_EMOJI = {
    "clean": "✔",
    "issue": "✖",
    "unprobed": "○",
}

_QUERY = """
SELECT
    t.id,
    t.post_id,
    t.target_normalized,
    t.host,
    t.protocol,
    t.parse_confidence,
    t.notes,
    t.target_raw,
    t.dedupe_key,
    t.created_at,
    t.probe_status,
    t.probe_indicator_matches,
    t.probe_preview,
    t.probe_checked_at,
    t.probe_error,
    t.probe_snapshot_json,
    p.post_author,
    p.post_title,
    p.post_created_utc,
    p.is_nsfw,
    p.source_sort,
    p.last_seen_at
FROM reddit_targets t
LEFT JOIN reddit_posts p ON t.post_id = p.post_id
ORDER BY t.id DESC
"""


class RedditBrowserWindow:
    """
    Toplevel window for reviewing ingested reddit targets.

    Supports column-click sort and text filter on target_normalized.
    Row identity is stable across sort/filter via _row_by_iid.
    """

    def __init__(
        self,
        parent: tk.Widget,
        db_path: Optional[Path] = None,
        add_record_callback=None,
        promote_record_callback=None,
        promote_records_callback=None,
        settings_manager=None,
    ) -> None:
        self.parent = parent
        self.db_path = db_path
        self.theme = get_theme()
        self._add_record_callback = add_record_callback
        self._promote_record_callback = promote_record_callback
        self._promote_records_callback = promote_records_callback
        self._settings_manager = settings_manager

        # Row data store — keyed by iid (str(target.id))
        self._row_by_iid: dict[str, dict] = {}
        self._all_rows: list[dict] = []  # full unfiltered load

        # Sort state — column key ("target", "proto", …), not dict key
        self._sort_col: Optional[str] = None
        self._sort_reverse: bool = False
        self._context_menu_visible: bool = False
        self._context_menu_bindings: list[tuple[tk.Widget, str, str]] = []

        self.window = tk.Toplevel(parent)
        self._build_window()
        self._load_rows()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_window(self) -> None:
        self.window.title("Reddit Post DB")
        self.window.geometry("1000x500")

        # Filter bar
        filter_frame = tk.Frame(self.window)
        self.theme.apply_to_widget(filter_frame, "main_window")
        filter_frame.pack(fill=tk.X, padx=8, pady=(8, 2))

        filter_label = tk.Label(filter_frame, text="Filter:")
        self.theme.apply_to_widget(filter_label, "label")
        filter_label.pack(side=tk.LEFT)

        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._apply_filter_and_sort())
        filter_entry = tk.Entry(filter_frame, textvariable=self._filter_var, width=40)
        self.theme.apply_to_widget(filter_entry, "entry")
        filter_entry.pack(side=tk.LEFT, padx=(4, 0))

        # Tree + scrollbar
        tree_frame = tk.Frame(self.window)
        self.theme.apply_to_widget(tree_frame, "main_window")
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(2, 2))

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=COLUMNS,
            show="headings",
            selectmode="extended",
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self.tree.yview)

        for col in COLUMNS:
            self.tree.heading(
                col,
                text=COL_HEADERS[col],
                command=lambda c=col: self._on_sort(c),
            )
            self.tree.column(col, width=COL_WIDTHS[col], minwidth=30)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Context menu (right-click)
        self._context_menu = tk.Menu(self.window, tearoff=0)
        self._context_menu.add_command(
            label="Copy IP/Host",
            command=self._on_copy_host,
        )
        self._context_menu.add_command(
            label="Open in Explorer",
            command=self._on_context_open_explorer,
        )
        self._context_menu.add_command(
            label="Open in system browser",
            command=self._on_context_open_system_browser,
        )
        self._context_menu.add_command(
            label="Probe Target",
            command=self._on_context_probe_target,
        )
        self._context_menu.add_separator()
        self._context_menu.add_command(
            label="Add to dirracuda DB",
            command=self._on_add_to_db,
        )
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Double-1>", self._on_double_click)

        # Status label
        self.status_var = tk.StringVar(value="")
        status_label = tk.Label(self.window, textvariable=self.status_var, anchor=tk.W)
        self.theme.apply_to_widget(status_label, "label")
        status_label.pack(fill=tk.X, padx=8, pady=(0, 2))

        # Action buttons
        btn_frame = tk.Frame(self.window)
        self.theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(fill=tk.X, padx=8, pady=(0, 8))

        for text, cmd in (
            ("Open in Explorer", self._on_open_explorer),
            ("Open Reddit Post", self._on_open_reddit_post),
            ("Probe Selected", self._on_probe_selected),
            ("Refresh", self._on_refresh),
            ("Clear DB", self._on_clear_db),
        ):
            btn = tk.Button(btn_frame, text=text, command=cmd)
            self.theme.apply_to_widget(btn, "button_secondary")
            btn.pack(side=tk.LEFT, padx=(0, 6))

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_rows(self) -> None:
        """Load all rows from sidecar DB; delegates status text to _apply_filter_and_sort."""
        self._row_by_iid.clear()
        self._all_rows.clear()
        self.tree.delete(*self.tree.get_children())

        try:
            store.init_db(self.db_path)
        except (sqlite3.Error, OSError) as e:
            self.status_var.set(f"DB error: {e}")
            return

        try:
            conn = store.open_connection(self.db_path)
        except (sqlite3.Error, OSError, RuntimeError, FileNotFoundError) as e:
            self.status_var.set(f"DB error: {e}")
            return

        try:
            rows = conn.execute(_QUERY).fetchall()
        except sqlite3.Error as e:
            self.status_var.set(f"Query error: {e}")
            return
        finally:
            conn.close()

        for row in rows:
            d = dict(row)
            iid = str(d["id"])
            self._all_rows.append(d)
            self._row_by_iid[iid] = d

        self._apply_filter_and_sort()

    # ------------------------------------------------------------------
    # Filter + sort (sole owner of status text on success)
    # ------------------------------------------------------------------

    def _apply_filter_and_sort(self) -> None:
        """
        Re-render tree from _all_rows according to current filter and sort state.

        Filter scope: target_normalized only (MVP).
        Sort key: resolved via COLUMN_KEY_MAP[_sort_col].
        Status text: this method is the only place it is written on success.
        """
        filter_text = self._filter_var.get().strip().lower()

        if filter_text:
            # Filter on target_normalized only — expand here for future multi-field search
            visible = [
                r for r in self._all_rows
                if filter_text in (r.get("target_normalized") or "").lower()
            ]
        else:
            visible = list(self._all_rows)

        if self._sort_col is not None:
            dict_key = COLUMN_KEY_MAP[self._sort_col]
            visible.sort(
                key=lambda r: str(r.get(dict_key) or "").lower(),
                reverse=self._sort_reverse,
            )

        self.tree.delete(*self.tree.get_children())
        for row in visible:
            iid = str(row["id"])
            values = [
                self._value_for_column(row, c)
                for c in COLUMNS
            ]
            self.tree.insert("", tk.END, iid=iid, values=values)

        total = len(self._all_rows)
        shown = len(visible)
        if filter_text:
            self.status_var.set(f"{shown} of {total} targets")
        else:
            self.status_var.set(f"{total} targets loaded")

    def _value_for_column(self, row: dict, col: str) -> str:
        """Return display value for a tree column."""
        if col == "probe":
            return self._probe_status_to_emoji(row.get("probe_status"))
        return str(row.get(COLUMN_KEY_MAP[col]) or "")

    # ------------------------------------------------------------------
    # Sort
    # ------------------------------------------------------------------

    def _reset_headings(self) -> None:
        """Restore all column headings to plain text (no ▲/▼ indicator)."""
        for col in COLUMNS:
            self.tree.heading(col, text=COL_HEADERS[col])

    def _on_sort(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            # Clear indicator on previously sorted column
            if self._sort_col is not None:
                self.tree.heading(self._sort_col, text=COL_HEADERS[self._sort_col])
            self._sort_col = col
            self._sort_reverse = False

        indicator = " ▼" if self._sort_reverse else " ▲"
        self.tree.heading(col, text=COL_HEADERS[col] + indicator)
        self._apply_filter_and_sort()

    # ------------------------------------------------------------------
    # Selection helper
    # ------------------------------------------------------------------

    def _selected_row(self) -> Optional[dict]:
        """Return row dict for selected tree item, or None if nothing selected."""
        rows = self._selected_rows()
        if not rows:
            return None
        return rows[0]

    def _selected_rows(self) -> list[dict]:
        """Return selected row dicts in tree selection order."""
        rows: list[dict] = []
        sel = self.tree.selection()
        for iid in sel:
            row = self._row_by_iid.get(iid)
            if row is not None:
                rows.append(row)
        return rows

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _selected_target(self) -> Optional[RedditTarget]:
        """Return selected row as RedditTarget, or None if nothing selected."""
        row = self._selected_row()
        if row is None:
            return None
        return RedditTarget(
            id=row["id"],
            post_id=row["post_id"],
            target_raw=row["target_raw"],
            target_normalized=row["target_normalized"],
            host=row["host"],
            protocol=row["protocol"],
            notes=row["notes"],
            parse_confidence=row["parse_confidence"],
            created_at=row["created_at"],
            dedupe_key=row["dedupe_key"],
            probe_status=row.get("probe_status") or "unprobed",
            probe_indicator_matches=int(row.get("probe_indicator_matches") or 0),
            probe_preview=row.get("probe_preview"),
            probe_checked_at=row.get("probe_checked_at"),
            probe_error=row.get("probe_error"),
            probe_snapshot_json=row.get("probe_snapshot_json"),
        )

    def _on_open_explorer(self) -> None:
        target = self._selected_target()
        if target is None:
            messagebox.showinfo("No selection", "Select a row first.", parent=self.window)
            return
        def _factory(scheme: str, host: str, port: int, *, start_path: str = "/") -> None:
            host_type = "F" if scheme == "ftp" else "H"
            open_ftp_http_browser(
                host_type, self.window, host, port,
                initial_path=start_path,
                scheme=scheme if host_type == "H" else None,
                theme=self.theme,
            )

        explorer_bridge.open_target(target, self.window, browser_factory=_factory)

    def _on_open_system_browser(self) -> None:
        target = self._selected_target()
        if target is None:
            messagebox.showinfo("No selection", "Select a row first.", parent=self.window)
            return
        explorer_bridge.open_target_system_browser(target, self.window)

    def _on_open_reddit_post(self) -> None:
        row = self._selected_row()
        if row is None:
            messagebox.showinfo("No selection", "Select a row first.", parent=self.window)
            return
        url = f"https://www.reddit.com/r/opendirectories/comments/{row['post_id']}/"
        webbrowser.open(url)

    def _on_refresh(self) -> None:
        self._sort_col = None
        self._sort_reverse = False
        self._reset_headings()
        # Clear filter BEFORE reloading rows.
        # If _load_rows hits a DB/query error it sets status_var accordingly; doing
        # filter clear afterwards can trigger the trace callback and overwrite that
        # error with a misleading "0 targets loaded" status.
        if self._filter_var.get():
            self._filter_var.set("")
        self._load_rows()

    def _on_clear_db(self) -> None:
        confirmed = messagebox.askyesno(
            "Clear DB",
            "This will delete all Reddit targets and posts from the sidecar DB. Continue?",
            parent=self.window,
        )
        if not confirmed:
            return
        try:
            store.wipe_all(self.db_path)
        except (sqlite3.Error, OSError) as e:
            messagebox.showerror("Clear DB Failed", str(e), parent=self.window)
            return
        self._load_rows()

    def _on_right_click(self, event) -> str:
        if self._context_menu_visible:
            self._hide_context_menu()
        iid = self.tree.identify_row(event.y)
        if not iid:
            return "break"
        selected = set(self.tree.selection())
        if iid not in selected:
            self.tree.selection_set(iid)
        try:
            self._context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._context_menu.grab_release()
        self._context_menu_visible = True
        self._install_context_dismiss_handlers()
        return "break"

    def _install_context_dismiss_handlers(self) -> None:
        self._remove_context_dismiss_handlers()
        for widget in (self.window, self.tree):
            for sequence in ("<Button-1>", "<Button-3>"):
                bind_id = widget.bind(sequence, self._handle_context_dismiss_click, add="+")
                if bind_id:
                    self._context_menu_bindings.append((widget, sequence, bind_id))

    def _remove_context_dismiss_handlers(self) -> None:
        if not self._context_menu_bindings:
            return
        for widget, sequence, bind_id in self._context_menu_bindings:
            try:
                widget.unbind(sequence, bind_id)
            except Exception:
                pass
        self._context_menu_bindings = []

    def _handle_context_dismiss_click(self, event=None):
        self._hide_context_menu()

    def _hide_context_menu(self) -> None:
        if not self._context_menu_visible:
            return
        try:
            self._context_menu.unpost()
        except Exception:
            pass
        self._context_menu_visible = False
        self._remove_context_dismiss_handlers()

    def _on_copy_host(self) -> None:
        self._hide_context_menu()
        row = self._selected_row()
        if row is None:
            return
        host = str(row.get("host") or "").strip()
        if not host:
            return
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(host)
        except tk.TclError:
            pass

    def _on_context_open_explorer(self) -> None:
        self._hide_context_menu()
        self._on_open_explorer()

    def _on_context_open_system_browser(self) -> None:
        self._hide_context_menu()
        self._on_open_system_browser()

    def _on_context_probe_target(self) -> None:
        self._hide_context_menu()
        self._on_probe_selected()

    def _on_double_click(self, event) -> None:
        """Open a read-only details view for the clicked Reddit target row."""
        try:
            if self.tree.identify_region(event.x, event.y) == "heading":
                return
        except Exception:
            pass
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        row = self._row_by_iid.get(iid)
        if row is None:
            return
        try:
            self.tree.selection_set(iid)
        except Exception:
            pass
        self._show_target_details(row)

    def _show_target_details(self, row: dict) -> None:
        """Show a read-only notes/details window for a Reddit target."""
        dialog = tk.Toplevel(self.window)
        dialog.title("Reddit Target Details")
        dialog.transient(self.window)
        self.theme.apply_to_widget(dialog, "main_window")

        frame = tk.Frame(dialog, padx=10, pady=10)
        self.theme.apply_to_widget(frame, "main_window")
        frame.pack(fill=tk.BOTH, expand=True)

        text = tk.Text(frame, width=92, height=24, wrap="word")
        text.insert(tk.END, self._format_target_details(row))
        text.configure(state=tk.DISABLED)
        self.theme.apply_to_widget(text, "text_area")
        text.pack(fill=tk.BOTH, expand=True)

        buttons = tk.Frame(frame)
        self.theme.apply_to_widget(buttons, "main_window")
        buttons.pack(fill=tk.X, pady=(8, 0))

        close_btn = tk.Button(buttons, text="Close", command=dialog.destroy)
        self.theme.apply_to_widget(close_btn, "button_secondary")
        close_btn.pack(side=tk.RIGHT)

    def _format_target_details(self, row: dict) -> str:
        """Return read-only details text for a Reddit target row."""
        lines = [
            "Reddit Target Details",
            "",
            "Target",
            f"Target: {row.get('target_normalized') or 'Unknown'}",
            f"Raw: {row.get('target_raw') or 'N/A'}",
            f"Host: {row.get('host') or 'N/A'}",
            f"Protocol: {row.get('protocol') or 'unknown'}",
            f"Parse Confidence: {row.get('parse_confidence') or 'N/A'}",
            f"Notes: {row.get('notes') or 'N/A'}",
            "",
            "Reddit Post",
            f"Post ID: {row.get('post_id') or 'N/A'}",
            f"Title: {row.get('post_title') or 'N/A'}",
            f"Author: {row.get('post_author') or 'N/A'}",
            f"NSFW: {'yes' if row.get('is_nsfw') else 'no'}",
            f"Source: {row.get('source_sort') or 'N/A'}",
            f"Post Created: {row.get('post_created_utc') or 'N/A'}",
            f"Target Created: {row.get('created_at') or 'N/A'}",
            "",
            "Probe",
            f"Status: {row.get('probe_status') or 'unprobed'}",
            f"Indicator Matches: {row.get('probe_indicator_matches') or 0}",
            f"Preview: {row.get('probe_preview') or 'N/A'}",
            f"Checked: {row.get('probe_checked_at') or 'N/A'}",
            f"Error: {row.get('probe_error') or 'N/A'}",
        ]
        snapshot = self._parse_probe_snapshot(row.get("probe_snapshot_json"))
        if snapshot:
            lines.extend(["", format_probe_section(snapshot, show_rce_details=True).rstrip()])
        elif row.get("probe_status") in {"clean", "issue"}:
            lines.extend([
                "",
                "Probe Snapshot:",
                "   Full probe tree is not stored for this legacy sidecar row. Re-probe the row to populate it.",
            ])
        return "\n".join(lines)

    def _parse_probe_snapshot(self, value) -> Optional[dict]:
        """Return stored probe snapshot JSON as a dict, if available."""
        if isinstance(value, dict):
            return value
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _probe_status_to_emoji(self, probe_status: Optional[str]) -> str:
        return PROBE_STATUS_EMOJI.get((probe_status or "unprobed").lower(), "○")

    def _resolve_probe_config_path(self) -> Optional[str]:
        sm = self._settings_manager
        if sm is None:
            return None
        if hasattr(sm, "get_smbseek_config_path"):
            try:
                return sm.get_smbseek_config_path()
            except Exception:
                return None
        return None

    def _resolve_probe_worker_count(self) -> int:
        sm = self._settings_manager
        if sm is None:
            return 3
        try:
            return max(1, min(8, int(sm.get_setting("probe.batch_max_workers", 3))))
        except Exception:
            return 3

    def _on_probe_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            messagebox.showinfo("No selection", "Select a row first.", parent=self.window)
            return

        selected_iids = [str(row.get("id")) for row in rows]
        candidates: list[tuple[dict, object]] = []
        skipped: list[str] = []
        for row in rows:
            try:
                candidates.append((row, build_probe_target_from_sidecar_row(row)))
            except SidecarProbeUnsupported as exc:
                skipped.append(f"{row.get('target_normalized') or row.get('host')}: {exc}")

        if not candidates:
            self._show_probe_skipped_message(len(skipped), skipped)
            return

        total_rows = len(candidates)
        cancel_requested = {"value": False}
        cancel_event = threading.Event()
        task_registry = get_running_task_registry()
        task_id: Optional[str] = None

        def _request_cancel() -> None:
            cancel_requested["value"] = True
            cancel_event.set()

        status_dialog = BatchStatusDialog(
            parent=self.window,
            theme=self.theme,
            title="Probe Status",
            fields={
                "Target": "Reddit Targets",
                "Selected": str(len(rows)),
            },
            on_cancel=_request_cancel,
            total=total_rows,
        )
        status_dialog.update_progress(0, total_rows, "Starting probe run...")
        status_dialog.show()
        task_id = task_registry.create_task(
            task_type="probe",
            name="Reddit Sidecar Probe Batch",
            state="running",
            progress=f"0/{total_rows} targets",
            reopen_callback=status_dialog.show,
            cancel_callback=_request_cancel,
        )

        config_path = self._resolve_probe_config_path()
        worker_count = self._resolve_probe_worker_count()
        unprobed_errors: list[str] = []
        processed_count = 0

        try:
            patterns = build_indicator_patterns(config_path)
        except Exception:
            patterns = None

        try:
            store.init_db(self.db_path)
            conn = store.open_connection(self.db_path)
            try:
                max_workers = max(1, min(worker_count, total_rows))
                executor = ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="reddit-probe-ui",
                )
                pending = {}
                candidate_iter = iter(candidates)

                def _submit_next() -> bool:
                    if cancel_requested["value"]:
                        return False
                    try:
                        row, target = next(candidate_iter)
                    except StopIteration:
                        return False
                    future = executor.submit(
                        run_sidecar_probe,
                        target,
                        config_path=config_path,
                        indicator_patterns=patterns,
                        cancel_event=cancel_event,
                    )
                    pending[future] = row
                    return True

                try:
                    for _ in range(max_workers):
                        if not _submit_next():
                            break

                    while pending:
                        future = next(as_completed(tuple(pending.keys())))
                        row = pending.pop(future)
                        try:
                            outcome = future.result()
                        except Exception as exc:
                            outcome = SidecarProbeOutcome(
                                probe_status=PROBE_STATUS_UNPROBED,
                                probe_indicator_matches=0,
                                probe_preview=None,
                                probe_checked_at=utcnow_iso(),
                                probe_error=str(exc),
                            )

                        store.update_target_probe(
                            conn,
                            target_id=int(row["id"]),
                            probe_status=outcome.probe_status,
                            probe_indicator_matches=outcome.probe_indicator_matches,
                            probe_preview=outcome.probe_preview,
                            probe_checked_at=outcome.probe_checked_at,
                            probe_error=outcome.probe_error,
                            probe_snapshot_payload=getattr(outcome, "probe_snapshot_payload", None),
                        )
                        if outcome.probe_status == "unprobed" and outcome.probe_error:
                            unprobed_errors.append(
                                f"{row.get('target_normalized', '')}: {outcome.probe_error}"
                            )
                        processed_count += 1
                        if task_id:
                            task_registry.update_task(
                                task_id,
                                state="running",
                                progress=f"{processed_count}/{total_rows} targets",
                                reopen_callback=status_dialog.show,
                                cancel_callback=_request_cancel,
                            )
                        status_dialog.update_progress(
                            processed_count,
                            total_rows,
                            f"Probed {row.get('target_normalized', '')}",
                        )
                        try:
                            if status_dialog.window and status_dialog.window.winfo_exists():
                                status_dialog.window.update_idletasks()
                                status_dialog.window.update()
                        except Exception:
                            pass

                        if cancel_requested["value"]:
                            for pending_future in tuple(pending.keys()):
                                pending_future.cancel()
                            pending.clear()
                            break

                        _submit_next()
                finally:
                    executor.shutdown(
                        wait=not cancel_requested["value"],
                        cancel_futures=cancel_requested["value"],
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            status_dialog.mark_finished("failed", str(exc))
            status_dialog.show()
            if task_id:
                task_registry.remove_task(task_id)
            messagebox.showinfo(
                "Probe failed",
                f"Could not probe selected target: {exc}",
                parent=self.window,
            )
            return

        if cancel_requested["value"] and processed_count < total_rows:
            status_dialog.mark_finished(
                "cancelled",
                f"Processed {processed_count}/{total_rows} row(s) before cancellation.",
            )
        elif unprobed_errors:
            status_dialog.mark_finished(
                "partial",
                f"Processed {processed_count}/{total_rows} row(s); "
                f"{len(unprobed_errors)} row(s) were unprobed.",
            )
        else:
            status_dialog.mark_finished(
                "success",
                f"Processed {processed_count}/{total_rows} row(s).",
            )
        status_dialog.show()

        if skipped:
            self._show_probe_skipped_message(len(skipped), skipped)
        if unprobed_errors:
            details = "\n".join(unprobed_errors[:3])
            if len(unprobed_errors) > 3:
                details += f"\n...and {len(unprobed_errors) - 3} more"
            messagebox.showinfo(
                "Probe unavailable",
                f"Probe did not complete for {len(unprobed_errors)} row(s):\n{details}",
                parent=self.window,
            )

        self._load_rows()
        existing = []
        for iid in selected_iids:
            if not iid:
                continue
            try:
                row_exists = bool(self.tree.exists(iid))
            except Exception:
                row_exists = iid in self._row_by_iid
            if row_exists:
                existing.append(iid)
        if existing:
            self.tree.selection_set(*existing)
        if task_id:
            task_registry.remove_task(task_id)

    def _show_probe_skipped_message(self, skipped_count: int, skipped: list[str]) -> None:
        """Notify the operator that unknown/unsupported rows were skipped."""
        if skipped_count <= 0:
            return
        details = "\n".join(skipped[:3])
        if len(skipped) > 3:
            details += f"\n...and {len(skipped) - 3} more"
        messagebox.showinfo(
            "Probe skipped",
            (
                f"{skipped_count} host(s) can't be scanned because protocol info "
                "is unavailable or unsupported; manually update/add with a protocol "
                f"and try again.\n{details}"
            ),
            parent=self.window,
        )

    def _build_prefill(self, row: dict) -> Optional[dict]:
        """
        Build Add Record prefill payload from a Reddit target row.

        Maps protocol to host_type/scheme; extracts port from URL (D1: host:port only).
        Returns None for unsupported protocols.
        """
        from urllib.parse import urlparse
        protocol = (row.get("protocol") or "").lower().strip()
        if protocol in ("http", "https"):
            host_type, scheme = "H", protocol
        elif protocol == "ftp":
            host_type, scheme = "F", None
        elif protocol == "smb":
            host_type, scheme = "S", None
        else:
            return None

        port = None
        url = row.get("target_normalized") or ""
        if url:
            try:
                port = urlparse(url).port  # None when not explicit in URL
            except Exception:
                port = None
        # Fallback: handle bare host:port form with no scheme (e.g. "192.168.1.1:8080")
        if port is None and url and "://" not in url:
            segment = url.split("/")[0]
            if ":" in segment:
                try:
                    port = int(segment.rsplit(":", 1)[1])
                except (ValueError, IndexError):
                    port = None

        prefill = {
            "host_type": host_type,
            "host": row.get("host") or "",
            "port": port,
            "scheme": scheme,
            "_promotion_source": "reddit_browser",
            "_probe_cache": {
                "status": row.get("probe_status"),
                "indicator_matches": row.get("probe_indicator_matches"),
                "preview": row.get("probe_preview"),
                "checked_at": row.get("probe_checked_at"),
                "error": row.get("probe_error"),
            },
            "_probe_snapshot_source": "sidecar:reddit",
        }
        if host_type == "H":
            parsed = None
            try:
                parse_target = url if "://" in url else f"{scheme or 'http'}://{url}"
                parsed = urlparse(parse_target) if parse_target else None
            except Exception:
                parsed = None
            probe_host_hint = (parsed.hostname if parsed is not None else None) or str(row.get("host") or "").strip()
            probe_path_hint = (parsed.path if parsed is not None else "") or "/"
            probe_path_hint = probe_path_hint.split("?", 1)[0].split("#", 1)[0].strip() or "/"
            if not probe_path_hint.startswith("/"):
                probe_path_hint = "/" + probe_path_hint.lstrip("/")
            prefill["_probe_host_hint"] = probe_host_hint
            prefill["_probe_path_hint"] = probe_path_hint
        snapshot = self._parse_probe_snapshot(row.get("probe_snapshot_json"))
        if snapshot is not None:
            prefill["_probe_snapshot"] = snapshot
        return prefill

    def _resolve_prefill_host_ipv4(self, prefill: dict) -> tuple[str, bool]:
        """
        Resolve prefill host to IPv4 for Reddit promotion.

        Returns (host_to_use, was_resolved). If resolution fails or is not needed,
        host_to_use is the original host and was_resolved=False.
        """
        host = str(prefill.get("host") or "").strip()
        if not host:
            return host, False

        # Literal IP values are already valid inputs for Add Record.
        try:
            ipaddress.ip_address(host)
            return host, False
        except ValueError:
            pass

        try:
            infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        except OSError:
            return host, False

        for info in infos:
            sockaddr = info[4] if len(info) > 4 else None
            if isinstance(sockaddr, tuple) and sockaddr:
                candidate = str(sockaddr[0]).strip()
                if candidate:
                    return candidate, True

        return host, False

    def _on_add_to_db(self) -> None:
        self._hide_context_menu()
        rows = self._selected_rows()
        if not rows:
            messagebox.showinfo("No selection", "Select a row first.", parent=self.window)
            return
        if len(rows) == 1:
            self._on_add_to_db_single(rows[0])
            return
        self._on_add_to_db_bulk(rows)

    def _on_add_to_db_single(self, row: dict) -> None:
        prefill = self._build_prefill(row)
        if prefill is None:
            messagebox.showinfo(
                "Cannot promote",
                (
                    "Protocol info is unavailable for this row; manually update/add "
                    "with a protocol and try again."
                ),
                parent=self.window,
            )
            return
        if self._promote_record_callback is not None:
            self._promote_prefill_direct(prefill)
            return

        if self._add_record_callback is None:
            messagebox.showinfo(
                "Main database unavailable",
                "No main database promotion handler is available for this window.",
                parent=self.window,
            )
            return

        resolved_host, was_resolved = self._resolve_prefill_host_ipv4(prefill)
        if not was_resolved and resolved_host != "" and resolved_host == str(prefill.get("host") or "").strip():
            try:
                ipaddress.ip_address(resolved_host)
            except ValueError:
                messagebox.showwarning(
                    "Host Resolution Failed",
                    (
                        f"Could not resolve '{resolved_host}' to an IPv4 address.\n"
                        "You can still continue, but Save may fail until an IP address is entered."
                    ),
                    parent=self.window,
                )
        prefill["host"] = resolved_host
        self._add_record_callback(prefill)

    def _on_add_to_db_bulk(self, rows: list[dict]) -> None:
        if self._promote_records_callback is None:
            messagebox.showinfo(
                "Bulk import unavailable",
                (
                    "Bulk import requires direct sidecar promotion context.\n"
                    "Open this window from Dashboard -> Experimental Features."
                ),
                parent=self.window,
            )
            return

        prefills, skipped_reasons = self._collect_bulk_prefills(rows)
        self._start_bulk_promotion(
            prefills=prefills,
            skipped_reasons=skipped_reasons,
            selected_count=len(rows),
        )

    def _collect_bulk_prefills(self, rows: list[dict]) -> tuple[list[dict], list[str]]:
        prefills: list[dict] = []
        skipped_reasons: list[str] = []
        for row in rows:
            prefill = self._build_prefill(row)
            if prefill is None:
                target = str(row.get("target_normalized") or row.get("host") or "").strip()
                target = target or "unknown target"
                skipped_reasons.append(f"{target}: protocol info unavailable.")
                continue
            prefills.append(prefill)
        return prefills, skipped_reasons

    def _start_bulk_promotion(
        self,
        *,
        prefills: list[dict],
        skipped_reasons: list[str],
        selected_count: int,
    ) -> None:
        if not prefills:
            summary = self._merge_bulk_promotion_summary(
                selected_count,
                skipped_reasons,
                {},
            )
            messagebox.showinfo(
                "Bulk import summary",
                self._format_bulk_promotion_summary(summary),
                parent=self.window,
            )
            return

        cancel_event = threading.Event()
        updates: queue.Queue = queue.Queue()

        def _request_cancel() -> None:
            cancel_event.set()

        status_dialog = BatchStatusDialog(
            parent=self.window,
            theme=self.theme,
            title="Bulk Import Status",
            fields={
                "Target": "Reddit Targets",
                "Selected": str(selected_count),
            },
            on_cancel=_request_cancel,
            total=selected_count,
        )
        status_dialog.update_progress(
            len(skipped_reasons),
            selected_count,
            "Starting bulk import...",
        )
        status_dialog.show()

        def _progress(done: int, _total: int, message: str) -> None:
            updates.put(("progress", (done, message)))

        def _worker() -> None:
            try:
                summary = self._promote_records_callback(
                    prefills,
                    cancel_event=cancel_event,
                    progress_callback=_progress,
                )
                updates.put(("done", summary))
            except Exception as exc:
                updates.put(("error", str(exc)))

        worker = threading.Thread(target=_worker, daemon=True)
        worker.start()

        def _poll_updates() -> None:
            try:
                while True:
                    kind, payload = updates.get_nowait()
                    if kind == "progress":
                        done, message = payload
                        status_dialog.update_progress(
                            min(selected_count, int(done) + len(skipped_reasons)),
                            selected_count,
                            str(message),
                        )
                    elif kind == "done":
                        summary = self._merge_bulk_promotion_summary(
                            selected_count,
                            skipped_reasons,
                            payload if isinstance(payload, dict) else {},
                        )
                        final_status = "cancelled" if int(summary.get("cancelled", 0)) else "done"
                        status_dialog.mark_finished(final_status, "Bulk import finished.")
                        status_dialog.destroy()
                        messagebox.showinfo(
                            "Bulk import summary",
                            self._format_bulk_promotion_summary(summary),
                            parent=self.window,
                        )
                        return
                    elif kind == "error":
                        status_dialog.destroy()
                        messagebox.showerror(
                            "Bulk import failed",
                            str(payload),
                            parent=self.window,
                        )
                        return
            except queue.Empty:
                pass

            if worker.is_alive():
                self.window.after(90, _poll_updates)

        self.window.after(40, _poll_updates)

    def _merge_bulk_promotion_summary(
        self,
        selected_count: int,
        skipped_reasons: list[str],
        summary: dict,
    ) -> dict:
        base = dict(summary or {})
        pre_skipped = len(skipped_reasons)
        processed = int(base.get("processed", 0)) + pre_skipped
        merged_skipped = int(base.get("skipped", 0)) + pre_skipped
        cancelled = max(0, selected_count - processed)
        samples = list(skipped_reasons)
        samples.extend(list(base.get("skipped_reason_samples") or []))
        return {
            "selected": selected_count,
            "processed": processed,
            "inserted": int(base.get("inserted", 0)),
            "updated": int(base.get("updated", 0)),
            "skipped": merged_skipped,
            "failed": int(base.get("failed", 0)),
            "cancelled": cancelled,
            "skipped_reason_samples": samples[:6],
            "failed_reason_samples": list(base.get("failed_reason_samples") or [])[:6],
        }

    def _format_bulk_promotion_summary(self, summary: dict) -> str:
        lines = [
            f"Selected: {int(summary.get('selected', 0))}",
            f"Processed: {int(summary.get('processed', 0))}",
            f"Imported: {int(summary.get('inserted', 0))}",
            f"Updated: {int(summary.get('updated', 0))}",
            f"Skipped: {int(summary.get('skipped', 0))}",
            f"Failed: {int(summary.get('failed', 0))}",
        ]
        cancelled = int(summary.get("cancelled", 0))
        if cancelled > 0:
            lines.append(f"Cancelled: {cancelled}")

        skipped_samples = list(summary.get("skipped_reason_samples") or [])
        failed_samples = list(summary.get("failed_reason_samples") or [])
        if skipped_samples:
            lines.append("")
            lines.append("Skipped samples:")
            lines.extend(f"- {reason}" for reason in skipped_samples)
        if failed_samples:
            lines.append("")
            lines.append("Failure samples:")
            lines.extend(f"- {reason}" for reason in failed_samples)
        return "\n".join(lines)

    def _promote_prefill_direct(self, prefill: dict) -> None:
        try:
            promotion = self._promote_record_callback(prefill)
        except SidecarPromotionError as exc:
            messagebox.showinfo(
                "Cannot promote",
                str(exc),
                parent=self.window,
            )
            return
        except Exception as exc:
            messagebox.showerror(
                "Add Record Failed",
                f"Unable to save record:\n{exc}",
                parent=self.window,
            )
            return

        messagebox.showinfo(
            "Record Added",
            format_promotion_success(promotion),
            parent=self.window,
        )


def show_reddit_browser_window(
    parent: tk.Widget,
    db_path: Optional[Path] = None,
    add_record_callback=None,
    promote_record_callback=None,
    promote_records_callback=None,
    settings_manager=None,
) -> None:
    """Open the Reddit Post DB browser window."""
    RedditBrowserWindow(
        parent,
        db_path,
        add_record_callback=add_record_callback,
        promote_record_callback=promote_record_callback,
        promote_records_callback=promote_records_callback,
        settings_manager=settings_manager,
    )
