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
import webbrowser
from pathlib import Path
from tkinter import messagebox
from typing import Optional
import tkinter as tk
from tkinter import ttk

import experimental.redseek.store as store
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

    def __init__(self, parent: tk.Widget, db_path: Optional[Path] = None) -> None:
        self.parent = parent
        self.db_path = db_path
        self.theme = get_theme()

        # Row data store — keyed by iid (str(target.id))
        self._row_by_iid: dict[str, dict] = {}
        self._all_rows: list[dict] = []  # full unfiltered load

        # Sort state — column key ("target", "proto", …), not dict key
        self._sort_col: Optional[str] = None
        self._sort_reverse: bool = False

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

    def _on_open_explorer(self) -> None:
        row = self._selected_row()
        if row is None:
            messagebox.showinfo("No selection", "Select a row first.", parent=self.window)
            return
        target = RedditTarget(
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
        explorer_bridge.open_target(target, self.window)

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


def show_reddit_browser_window(
    parent: tk.Widget,
    db_path: Optional[Path] = None,
) -> None:
    """Open the Reddit Post DB browser window."""
    RedditBrowserWindow(parent, db_path)
