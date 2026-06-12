"""
Censys Discovery Results Browser

Table view of censys_results from the censys_discovery sidecar DB.

Columns (treeview column id → row dict key):
  protocol           -> protocol
  ip_address         -> ip_address
  port               -> port
  transport_protocol -> transport_protocol
  banner             -> banner
  scan_time          -> scan_time

Actions: Add to dirracuda DB (single + bulk), Show Details.

Promotion uses a direct main-DB callback when available. add_record_callback
is retained only for legacy callers that still want the Add Record dialog.
"""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import ttk

from gui.components.pry_status_dialog import BatchStatusDialog
from gui.utils import safe_messagebox as messagebox
from gui.utils.sidecar_promotion import (
    SidecarPromotionError,
    format_promotion_success,
)
from gui.utils.style import get_theme

# ---------------------------------------------------------------------------
# Column layout — IDs match list_results() dict keys exactly
# ---------------------------------------------------------------------------

COL_HEADERS = {
    "protocol":           "Protocol",
    "ip_address":         "IP Address",
    "port":               "Port",
    "transport_protocol": "Transport",
    "banner":             "Banner",
    "scan_time":          "Scanned",
}

COL_WIDTHS = {
    "protocol":           70,
    "ip_address":         130,
    "port":               60,
    "transport_protocol": 70,
    "banner":             400,
    "scan_time":          150,
}

COLUMNS = ["protocol", "ip_address", "port", "transport_protocol", "banner", "scan_time"]


class CensysBrowserWindow:
    """
    Toplevel window for reviewing censys_discovery sidecar results.

    Loads all rows from the censys_discovery sidecar DB on open.
    Promotion to dirracuda.db is available when promote_record_callback is supplied.
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

        self._row_by_iid: dict[str, dict] = {}
        self._context_menu_visible: bool = False

        self.window = tk.Toplevel(parent)
        self._build_window()
        self._load_rows()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_window(self) -> None:
        self.window.title("Censys Discovery Results")
        self.window.geometry("1050x480")
        self.theme.apply_to_widget(self.window, "main_window")

        tree_frame = tk.Frame(self.window)
        self.theme.apply_to_widget(tree_frame, "main_window")
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self._v_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=COLUMNS,
            show="headings",
            selectmode="extended",
            yscrollcommand=self._v_scrollbar.set,
        )
        self._v_scrollbar.config(command=self.tree.yview)

        for col in COLUMNS:
            self.tree.heading(col, text=COL_HEADERS[col])
            anchor = "center" if col in ("port",) else "w"
            self.tree.column(col, width=COL_WIDTHS[col], minwidth=30, anchor=anchor)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Context menu
        self._context_menu = tk.Menu(self.window, tearoff=0)
        self._context_menu.add_command(
            label="Add to dirracuda DB",
            command=self._on_add_to_db,
        )
        self._context_menu.add_separator()
        self._context_menu.add_command(
            label="Show Details",
            command=self._on_context_show_details,
        )
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Double-1>", self._on_double_click)

        # Status bar
        status_frame = tk.Frame(self.window)
        self.theme.apply_to_widget(status_frame, "main_window")
        status_frame.pack(fill=tk.X, padx=8, pady=(0, 6))

        self._status_label = tk.Label(status_frame, text="", anchor="w")
        self.theme.apply_to_widget(self._status_label, "label")
        self._status_label.pack(side=tk.LEFT)

        btn_frame = tk.Frame(self.window)
        self.theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(fill=tk.X, padx=8, pady=(0, 8))

        refresh_btn = tk.Button(btn_frame, text="Refresh", command=self._load_rows)
        self.theme.apply_to_widget(refresh_btn, "button_secondary")
        refresh_btn.pack(side=tk.LEFT)

        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        self.window.bind("<Escape>", lambda _: self.window.destroy())

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_rows(self) -> None:
        """Load all results from sidecar DB and populate the treeview."""
        from experimental.censys_discovery.store import (
            init_db,
            list_results,
            open_connection,
        )

        self.tree.delete(*self.tree.get_children())
        self._row_by_iid.clear()

        try:
            init_db(self.db_path)
            conn = open_connection(self.db_path)
            try:
                rows = list_results(conn)
            finally:
                conn.close()
        except Exception as exc:
            self._status_label.configure(text=f"Load error: {exc}")
            return

        for row in rows:
            iid = str(row["result_id"])
            self._row_by_iid[iid] = row
            self.tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    row.get("protocol", ""),
                    row.get("ip_address", ""),
                    row.get("port", ""),
                    row.get("transport_protocol") or "",
                    row.get("banner") or "",
                    row.get("scan_time") or "",
                ),
            )

        count = len(rows)
        self._status_label.configure(text=f"{count} result{'s' if count != 1 else ''}")

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _on_right_click(self, event: tk.Event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            selected = set(self.tree.selection())
            if iid not in selected:
                self.tree.selection_set(iid)
        self._context_menu.post(event.x_root, event.y_root)
        self._context_menu_visible = True

    def _hide_context_menu(self) -> None:
        try:
            self._context_menu.unpost()
        except Exception:
            pass
        self._context_menu_visible = False

    def _on_double_click(self, event: tk.Event) -> None:
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
        self._show_result_details(row)

    def _on_context_show_details(self) -> None:
        self._hide_context_menu()
        row = self._selected_row()
        if row is None:
            return
        self._show_result_details(row)

    def _show_result_details(self, row: dict) -> None:
        dialog = tk.Toplevel(self.window)
        dialog.title("Censys Result Details")
        dialog.transient(self.window)
        self.theme.apply_to_widget(dialog, "main_window")

        frame = tk.Frame(dialog, padx=10, pady=10)
        self.theme.apply_to_widget(frame, "main_window")
        frame.pack(fill=tk.BOTH, expand=True)

        text = tk.Text(frame, width=92, height=24, wrap="word")
        text.insert(tk.END, self._format_result_details(row))
        text.configure(state=tk.DISABLED)
        self.theme.apply_to_widget(text, "text_area")
        text.pack(fill=tk.BOTH, expand=True)

        buttons = tk.Frame(frame)
        self.theme.apply_to_widget(buttons, "main_window")
        buttons.pack(fill=tk.X, pady=(8, 0))

        close_btn = tk.Button(buttons, text="Close", command=dialog.destroy)
        self.theme.apply_to_widget(close_btn, "button_secondary")
        close_btn.pack(side=tk.RIGHT)

    def _format_result_details(self, row: dict) -> str:
        source_json_raw = row.get("source_json") or ""
        try:
            source_pretty = json.dumps(json.loads(source_json_raw), indent=2)
        except Exception:
            source_pretty = source_json_raw

        lines = [
            "Censys Result Details",
            "",
            f"Protocol:  {row.get('protocol') or 'Unknown'}",
            f"IP:        {row.get('ip_address') or 'Unknown'}",
            f"Port:      {row.get('port') or 'Unknown'}",
            f"Transport: {row.get('transport_protocol') or 'N/A'}",
            f"Banner:    {row.get('banner') or 'N/A'}",
            f"Scanned:   {row.get('scan_time') or 'N/A'}",
            f"Run ID:    {row.get('run_id') or 'N/A'}",
            "",
            "Source JSON:",
            source_pretty,
        ]
        return "\n".join(lines)

    def _selected_row(self) -> Optional[dict]:
        rows = self._selected_rows()
        if not rows:
            return None
        return rows[0]

    def _selected_rows(self) -> list[dict]:
        rows: list[dict] = []
        for iid in self.tree.selection():
            row = self._row_by_iid.get(iid)
            if row is not None:
                rows.append(row)
        return rows

    # ------------------------------------------------------------------
    # Promotion: Add to dirracuda DB
    # ------------------------------------------------------------------

    def _build_prefill(self, row: dict) -> Optional[dict]:
        """
        Build Add Record prefill payload from a censys_result row.

        Returns None for unsupported protocols.
        """
        protocol = str(row.get("protocol") or "").upper().strip()
        ip_address = str(row.get("ip_address") or "").strip()
        if not ip_address:
            return None

        if protocol == "SMB":
            return {
                "host_type": "S",
                "host": ip_address,
                "ip_address": ip_address,
                "_promotion_source": "censys_browser",
            }

        port = row.get("port")
        if protocol == "FTP":
            prefill = {
                "host_type": "F",
                "host": ip_address,
                "ip_address": ip_address,
                "port": port,
                "_promotion_source": "censys_browser",
            }
            return prefill

        if protocol == "HTTP":
            scheme = "https" if port == 443 else "http"
            return {
                "host_type": "H",
                "host": ip_address,
                "ip_address": ip_address,
                "port": port,
                "scheme": scheme,
                "_promotion_source": "censys_browser",
            }

        return None

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
            proto = row.get("protocol", "")
            messagebox.showinfo(
                "Cannot promote",
                f"Protocol '{proto}' is not supported for promotion.",
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
                proto = str(row.get("protocol") or "").strip() or "unknown"
                ip = str(row.get("ip_address") or "").strip() or "unknown IP"
                skipped_reasons.append(
                    f"{proto} {ip}: unsupported protocol."
                )
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
                "Target": "Censys Results",
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
                            min(selected_count, done + len(skipped_reasons)),
                            selected_count,
                            message,
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


def show_censys_browser_window(
    parent: tk.Widget,
    db_path: Optional[Path] = None,
    add_record_callback=None,
    promote_record_callback=None,
    promote_records_callback=None,
    settings_manager=None,
) -> None:
    """Open the Censys Discovery results browser window."""
    CensysBrowserWindow(
        parent,
        db_path=db_path,
        add_record_callback=add_record_callback,
        promote_record_callback=promote_record_callback,
        promote_records_callback=promote_records_callback,
        settings_manager=settings_manager,
    )
