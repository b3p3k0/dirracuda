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
  Date    -> created_at

Actions: Open in Explorer, Open Reddit Post, Refresh, Clear DB

Filter scope: target_normalized only (MVP). Expanding to other fields
requires deliberate change here and in _apply_filter_and_sort.

Row identity: iid = str(target.id) — DB primary key used as Treeview iid so
that sort/filter never desynchronises selection from _row_by_iid.
"""

import sqlite3
import socket
import webbrowser
import ipaddress
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import ttk
from gui.utils import safe_messagebox as messagebox

import experimental.redseek.store as store
from gui.components.unified_browser_window import open_ftp_http_browser
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
    "date":   "Date",
}

COL_WIDTHS = {
    "target": 280,
    "proto":  60,
    "conf":   55,
    "author": 90,
    "nsfw":   45,
    "notes":  160,
    "date":   140,
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
    p.post_author,
    p.is_nsfw
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
    ) -> None:
        self.parent = parent
        self.db_path = db_path
        self.theme = get_theme()
        self._add_record_callback = add_record_callback

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
            selectmode="browse",
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
        self._context_menu.add_separator()
        self._context_menu.add_command(
            label="Add to dirracuda DB",
            command=self._on_add_to_db,
        )
        self.tree.bind("<Button-3>", self._on_right_click)

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
            values = [row.get(COLUMN_KEY_MAP[c]) or "" for c in COLUMNS]
            self.tree.insert("", tk.END, iid=iid, values=values)

        total = len(self._all_rows)
        shown = len(visible)
        if filter_text:
            self.status_var.set(f"{shown} of {total} targets")
        else:
            self.status_var.set(f"{total} targets loaded")

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
        sel = self.tree.selection()
        if not sel:
            return None
        return self._row_by_iid.get(sel[0])

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
        if self._add_record_callback is None:
            messagebox.showinfo(
                "Not available",
                "Open this window from the Servers window to use 'Add to dirracuda DB'.",
                parent=self.window,
            )
            return
        row = self._selected_row()
        if row is None:
            messagebox.showinfo("No selection", "Select a row first.", parent=self.window)
            return
        prefill = self._build_prefill(row)
        if prefill is None:
            messagebox.showinfo(
                "Cannot promote",
                f"Protocol '{row.get('protocol')}' is not supported for DB promotion.",
                parent=self.window,
            )
            return
        prefill["_promotion_source"] = "reddit_browser"
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


def show_reddit_browser_window(
    parent: tk.Widget,
    db_path: Optional[Path] = None,
    add_record_callback=None,
) -> None:
    """Open the Reddit Post DB browser window."""
    RedditBrowserWindow(parent, db_path, add_record_callback=add_record_callback)
