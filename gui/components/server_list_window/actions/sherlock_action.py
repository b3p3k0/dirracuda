"""
Standalone Sherlock scan action + risk attachment for ServerListWindow (C4).

Sherlock is display-only: it loads the latest probe snapshots for selected hosts
and runs the pure matcher. It never spawns a CLI subprocess, downloads files,
reads file contents, authenticates, or probes - only DB reads/writes plus the
pure matcher. The scan-and-persist work is factored into
`gui.utils.sherlock_scan.run_sherlock_scan` so C5's post-probe hook can reuse it.
"""

import threading
from typing import Any, Dict, List

from gui.components.server_list_window import table
from gui.utils import safe_messagebox as messagebox
from gui.utils.sherlock_scan import run_sherlock_scan
from shared.sherlock import default_settings, settings_from_dict


class ServerListWindowSherlockMixin:
    def _load_sherlock_settings(self):
        """Load Sherlock settings (validated colors/patterns) or defaults."""
        sm = getattr(self, "settings_manager", None)
        if sm is None:
            return default_settings()
        try:
            return settings_from_dict(sm.get_setting("sherlock", {}))
        except Exception:
            return default_settings()

    def _attach_sherlock_risk(self, servers: List[Dict[str, Any]]) -> None:
        """Attach per-row Sherlock risk summaries by row_key.

        Degrades silently: a missing method or read failure leaves rows without a
        `sherlock_risk` key, so the Risk column simply stays blank.
        """
        reader = getattr(self, "db_reader", None)
        getter = getattr(reader, "get_sherlock_risk_summary_map", None)
        if getter is None:
            return
        try:
            summary = getter() or {}
        except Exception:
            return
        for server in servers:
            risk = summary.get(server.get("row_key"))
            if risk is not None:
                server["sherlock_risk"] = risk

    def _on_sherlock_scan_selected(self) -> None:
        """Run a standalone Sherlock scan over the selected hosts (threaded)."""
        selected = table.get_selected_server_data(self.tree, self.filtered_servers)
        if not selected:
            messagebox.showwarning(
                "No Selection",
                "Please select one or more servers to scan with Sherlock.",
                parent=self.window,
            )
            return

        if getattr(self, "_sherlock_scan_active", False):
            messagebox.showinfo(
                "Sherlock",
                "A Sherlock scan is already running...",
                parent=self.window,
            )
            return

        targets = [
            {
                "ip_address": s.get("ip_address"),
                "host_type": s.get("host_type", "S"),
                "protocol_server_id": s.get("protocol_server_id"),
                "port": s.get("port"),
            }
            for s in selected
        ]
        settings = self._load_sherlock_settings()
        requested = len(targets)

        self._sherlock_scan_active = True
        self._set_status("Running Sherlock scan...")
        self._update_action_buttons_state()

        def worker():
            summary = None
            try:
                summary = run_sherlock_scan(self.db_reader, settings, targets)
            except Exception:  # never crash the worker thread; report as failure
                summary = None
            finally:
                # UI thread owns refresh/teardown; runs even on worker failure.
                self.window.after(0, lambda: self._finish_sherlock_scan(summary, requested))

        self._sherlock_scan_thread = threading.Thread(target=worker, daemon=True)
        self._sherlock_scan_thread.start()

    def _finish_sherlock_scan(self, summary, requested: int) -> None:
        """UI-thread completion: clear flag, refresh Risk, report summary."""
        self._sherlock_scan_active = False
        try:
            self._load_data()
        finally:
            self._update_action_buttons_state()

        if summary is None:
            self._set_status("Sherlock scan failed")
            messagebox.showerror("Sherlock", "Sherlock scan failed.", parent=self.window)
            return

        msg = (
            f"Sherlock scan: {requested} host(s) - "
            f"{summary.with_findings} with findings, "
            f"{summary.clean} clean, "
            f"{summary.skipped_no_snapshot} skipped (no snapshot)"
        )
        if summary.not_persisted or summary.errors:
            msg += f"; {summary.not_persisted} not persisted, {summary.errors} errors"
        self._set_status(msg)
        messagebox.showinfo("Sherlock", msg, parent=self.window)
