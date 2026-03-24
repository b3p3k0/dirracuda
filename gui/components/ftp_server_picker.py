"""
FTP server picker dialog for xsmbseek.

Presents a filterable list of discovered FTP servers from the database
and opens a browser window for the selected server. Stays open so the operator can
browse multiple servers simultaneously.
"""

import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional


class FtpServerPickerDialog:
    """
    Lightweight dialog listing anonymous FTP servers from the database.

    The picker remains open after launching a browser so the operator can
    open multiple FTP servers simultaneously.
    """

    def __init__(
        self,
        parent: tk.Widget,
        db_reader,
        config_path: Optional[str] = None,
        theme=None,
        settings_manager=None,
    ) -> None:
        self._parent = parent
        self._db_reader = db_reader
        self._config_path = config_path
        self._theme = theme
        self._settings_manager = settings_manager
        self._all_rows: List[Dict[str, Any]] = []
        self._rows_by_item_id: Dict[str, Dict[str, Any]] = {}

        self._build_dialog()
        self._load_servers()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_dialog(self) -> None:
        self._dialog = tk.Toplevel(self._parent)
        self._dialog.title("FTP Servers")
        self._dialog.geometry("700x450")
        self._dialog.minsize(500, 300)
        if self._theme:
            self._theme.apply_to_widget(self._dialog, "main_window")

        # Filter row
        filter_frame = tk.Frame(self._dialog)
        filter_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(filter_frame, text="Filter:").pack(side=tk.LEFT)
        self.filter_var = tk.StringVar()
        filter_entry = tk.Entry(filter_frame, textvariable=self.filter_var)
        filter_entry.pack(side=tk.LEFT, padx=(5, 10), fill=tk.X, expand=True)
        self.filter_var.trace_add("write", self._on_filter_changed)

        refresh_btn = tk.Button(
            filter_frame, text="\U0001f504 Refresh", command=self._load_servers
        )
        refresh_btn.pack(side=tk.LEFT)

        # Treeview
        tree_frame = tk.Frame(self._dialog)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))

        columns = ("ip", "port", "country", "banner", "last_seen")
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", selectmode="browse"
        )
        self.tree.heading("ip", text="IP Address")
        self.tree.heading("port", text="Port")
        self.tree.heading("country", text="Country")
        self.tree.heading("banner", text="Banner")
        self.tree.heading("last_seen", text="Last Seen")

        self.tree.column("ip", width=140, minwidth=100)
        self.tree.column("port", width=60, minwidth=50)
        self.tree.column("country", width=80, minwidth=50)
        self.tree.column("banner", width=240, minwidth=100)
        self.tree.column("last_seen", width=160, minwidth=100)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)

        self.tree.bind("<Double-1>", lambda _e: self._on_open_browser())

        # Button row
        btn_frame = tk.Frame(self._dialog)
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        browse_btn = tk.Button(
            btn_frame, text="Browse Selected", command=self._on_open_browser
        )
        browse_btn.pack(side=tk.LEFT, padx=(0, 5))

        close_btn = tk.Button(
            btn_frame, text="Close", command=self._dialog.destroy
        )
        close_btn.pack(side=tk.LEFT)

        # Status
        self.status_var = tk.StringVar(value="")
        tk.Label(self._dialog, textvariable=self.status_var, anchor="w").pack(
            fill=tk.X, padx=10, pady=(0, 5)
        )

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_servers(self) -> None:
        try:
            servers = self._db_reader.get_ftp_servers() or []
        except Exception:
            servers = []
        self._all_rows = servers
        self._populate_tree(servers)
        self.status_var.set(f"{len(servers)} server(s)")

    def _populate_tree(self, rows: List[Dict[str, Any]]) -> None:
        self.tree.delete(*self.tree.get_children())
        self._rows_by_item_id = {}
        for row in rows:
            banner = str(row.get("banner") or "")
            if len(banner) > 60:
                banner = banner[:60] + "\u2026"
            item_id = self.tree.insert(
                "",
                "end",
                values=(
                    row.get("ip_address", ""),
                    row.get("port", 21),
                    row.get("country") or row.get("country_code") or "",
                    banner,
                    row.get("last_seen", ""),
                ),
            )
            self._rows_by_item_id[item_id] = row

    def _on_filter_changed(self, *_args) -> None:
        q = self.filter_var.get().lower()
        if not q:
            self._populate_tree(self._all_rows)
            return
        filtered = [
            r for r in self._all_rows
            if q in str(r.get("ip_address", "")).lower()
            or q in str(r.get("country") or "").lower()
            or q in str(r.get("country_code") or "").lower()
            or q in str(r.get("banner") or "").lower()
        ]
        self._populate_tree(filtered)

    # ------------------------------------------------------------------
    # Browser launch
    # ------------------------------------------------------------------

    def _on_open_browser(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        ip = vals[0]
        try:
            port = int(vals[1])
        except (ValueError, IndexError):
            port = 21

        selected_row = self._rows_by_item_id.get(sel[0], {})
        full_banner = str(selected_row.get("banner") or "")

        from gui.components.unified_browser_window import open_ftp_http_browser
        open_ftp_http_browser(
            "F",
            parent=self._dialog,
            ip_address=ip,
            port=port,
            banner=full_banner,
            config_path=self._config_path,
            db_reader=self._db_reader,
            theme=self._theme,
            settings_manager=self._settings_manager,
        )
