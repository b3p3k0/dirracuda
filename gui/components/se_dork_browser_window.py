"""
SE Dork Results Browser

Table view of dork_results from the se_dork sidecar DB.

Columns (treeview column id → row dict key):
  url             -> url
  probe_status    -> probe_status (rendered as emoji)
  probe_preview   -> probe_preview
  probe_checked_at -> probe_checked_at

Actions: Copy URL, Open in Explorer, Open in system browser, Probe URL,
Add to dirracuda DB.

Promotion uses a direct main-DB callback when available. add_record_callback
is retained only for legacy callers that still want the Add Record dialog.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import ipaddress
import json
import queue
import socket
import threading
import webbrowser
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
import tkinter as tk
from tkinter import ttk

from gui.components.unified_browser_window import open_ftp_http_browser
from gui.components.pry_status_dialog import BatchStatusDialog
from gui.utils.running_tasks import get_running_task_registry
from gui.utils import safe_messagebox as messagebox
from gui.utils.sidecar_promotion import (
    SidecarPromotionError,
    format_promotion_success,
)
from gui.utils.probe_snapshot_details import format_probe_section
from gui.utils.style import get_theme

# ---------------------------------------------------------------------------
# Column layout — IDs match get_all_results() dict keys exactly
# ---------------------------------------------------------------------------

COL_HEADERS = {
    "url":         "URL",
    "probe_status": "Probed",
    "probe_preview": "Probe Preview",
    "probe_checked_at": "Checked",
}

COL_WIDTHS = {
    "url":         300,
    "probe_status": 70,
    "probe_preview": 500,
    "probe_checked_at": 150,
}

COLUMNS = ["url", "probe_status", "probe_preview", "probe_checked_at"]

PROBE_STATUS_EMOJI = {
    "clean": "✔",
    "issue": "✖",
    "unprobed": "○",
}


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


class SeDorkBrowserWindow:
    """
    Toplevel window for reviewing se_dork classification results.

    Loads all rows from the se_dork sidecar DB on open.
    Promotion to dirracuda.db is available when promote_record_callback is supplied.
    """

    def __init__(
        self,
        parent: tk.Widget,
        db_path: Optional[Path] = None,
        add_record_callback=None,
        promote_record_callback=None,
        promote_records_callback=None,
        allow_promotion: bool = True,
        settings_manager=None,
    ) -> None:
        self.parent = parent
        self.db_path = db_path
        self.theme = get_theme()
        self._add_record_callback = add_record_callback
        self._promote_record_callback = promote_record_callback
        self._promote_records_callback = promote_records_callback
        self._allow_promotion = bool(allow_promotion)
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
        self.window.title("SearXNG Dork Results")
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
            anchor = "center" if col == "probe_status" else "w"
            self.tree.column(col, width=COL_WIDTHS[col], minwidth=30, anchor=anchor)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Context menu
        self._context_menu = tk.Menu(self.window, tearoff=0)
        self._context_menu.add_command(
            label="Copy URL",
            command=self._on_copy_url,
        )
        self._context_menu.add_command(
            label="Open in Explorer",
            command=self._on_context_open_explorer,
        )
        self._context_menu.add_command(
            label="Open in system browser",
            command=self._on_open_system_browser,
        )
        self._context_menu.add_command(
            label="Probe URL",
            command=self._on_context_probe_url,
        )
        if self._allow_promotion:
            self._context_menu.add_separator()
            self._context_menu.add_command(
                label="Add to dirracuda DB",
                command=self._on_add_to_db,
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

        open_explorer_btn = tk.Button(
            btn_frame,
            text="Open in Explorer",
            command=self._on_open_explorer,
        )
        self.theme.apply_to_widget(open_explorer_btn, "button_secondary")
        open_explorer_btn.pack(side=tk.LEFT, padx=(0, 6))

        probe_btn = tk.Button(
            btn_frame,
            text="Probe Selected",
            command=self._on_probe_selected,
        )
        self.theme.apply_to_widget(probe_btn, "button_secondary")
        probe_btn.pack(side=tk.LEFT, padx=(0, 6))

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
        from experimental.se_dork.store import (
            delete_non_open_results,
            get_all_results,
            init_db,
            open_connection,
        )

        self.tree.delete(*self.tree.get_children())
        self._row_by_iid.clear()

        try:
            init_db(self.db_path)
            conn = open_connection(self.db_path)
            try:
                # Historical purge: retain OPEN_INDEX rows only.
                delete_non_open_results(conn, run_id=None)
                conn.commit()
                rows = get_all_results(conn)
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
                    row.get("url", ""),
                    self._probe_status_to_emoji(row.get("probe_status")),
                    row.get("probe_preview") or "",
                    row.get("probe_checked_at") or row.get("checked_at") or "",
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
        """Open a read-only details view for the clicked result row."""
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

    def _show_result_details(self, row: dict) -> None:
        """Show a read-only notes/details window for a SearXNG result."""
        dialog = tk.Toplevel(self.window)
        dialog.title("SearXNG Result Details")
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
        """Return read-only details text for a SearXNG result row."""
        source_engine = str(row.get("source_engine") or "").strip()
        source_engines = str(row.get("source_engines_json") or "").strip()
        source = source_engine or source_engines or "Unknown"

        lines = [
            "SearXNG Result Details",
            "",
            "Result",
            f"URL: {row.get('url') or 'Unknown'}",
            f"Title: {row.get('title') or 'N/A'}",
            f"Snippet: {row.get('snippet') or 'N/A'}",
            f"Source: {source}",
            "",
            "Classification",
            f"Verdict: {row.get('verdict') or 'Unknown'}",
            f"Reason: {row.get('reason_code') or 'N/A'}",
            f"HTTP Status: {row.get('http_status') or 'N/A'}",
            f"Checked: {row.get('checked_at') or 'N/A'}",
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
    # Row actions
    # ------------------------------------------------------------------

    def _on_copy_url(self) -> None:
        self._hide_context_menu()
        row = self._selected_row()
        if row is None:
            return
        url = row.get("url", "")
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(url)
        except Exception:
            pass

    def _on_open_system_browser(self) -> None:
        self._hide_context_menu()
        row = self._selected_row()
        if row is None:
            return
        url = row.get("url", "")
        if url:
            try:
                webbrowser.open(url)
            except Exception:
                pass

    def _on_open_explorer(self) -> None:
        row = self._selected_row()
        if row is None:
            messagebox.showinfo("No selection", "Select a row first.", parent=self.window)
            return

        url = row.get("url", "")
        try:
            parsed = urlparse(url)
            scheme = (parsed.scheme or "").lower()
            host = parsed.hostname or ""
        except Exception:
            messagebox.showinfo(
                "Cannot open in Explorer",
                f"URL '{url}' is invalid for internal explorer.",
                parent=self.window,
            )
            return

        if scheme not in ("http", "https", "ftp") or not host:
            messagebox.showinfo(
                "Cannot open in Explorer",
                f"URL '{url}' must be an http/https/ftp URL with a hostname.",
                parent=self.window,
            )
            return

        try:
            if scheme == "https":
                port = parsed.port or 443
            elif scheme == "http":
                port = parsed.port or 80
            else:
                port = parsed.port or 21
        except ValueError:
            messagebox.showinfo(
                "Cannot open in Explorer",
                f"URL '{url}' has an invalid port.",
                parent=self.window,
            )
            return

        start_path = parsed.path or "/"
        host_type = "F" if scheme == "ftp" else "H"

        try:
            open_ftp_http_browser(
                host_type,
                self.window,
                host,
                port,
                initial_path=start_path,
                scheme=scheme if host_type == "H" else None,
                theme=self.theme,
            )
        except Exception as exc:
            messagebox.showinfo(
                "Cannot open in Explorer",
                f"Internal explorer failed: {exc}",
                parent=self.window,
            )

    def _on_context_open_explorer(self) -> None:
        self._hide_context_menu()
        self._on_open_explorer()

    def _on_context_probe_url(self) -> None:
        self._hide_context_menu()
        self._on_probe_selected()

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

        from experimental.se_dork.probe import ProbeOutcome, PROBE_STATUS_UNPROBED, probe_url
        from experimental.se_dork.store import init_db, open_connection, update_result_probe

        total_rows = len(rows)
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
                "Target": "SearXNG Results",
                "Selected": str(total_rows),
            },
            on_cancel=_request_cancel,
            total=total_rows,
        )
        status_dialog.update_progress(0, total_rows, "Starting probe run…")
        status_dialog.show()
        task_id = task_registry.create_task(
            task_type="probe",
            name="SE Dork Probe Batch",
            state="running",
            progress=f"0/{total_rows} targets",
            reopen_callback=status_dialog.show,
            cancel_callback=_request_cancel,
        )

        config_path = self._resolve_probe_config_path()
        worker_count = self._resolve_probe_worker_count()
        selected_iids = [str(row.get("result_id")) for row in rows]
        unprobed_errors: list[str] = []
        processed_count = 0
        try:
            init_db(self.db_path)
            conn = open_connection(self.db_path)
            try:
                max_workers = max(1, min(worker_count, total_rows))
                executor = ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="se-dork-probe-ui",
                )
                pending = {}
                row_iter = iter(rows)

                def _submit_next() -> bool:
                    if cancel_requested["value"]:
                        return False
                    try:
                        row = next(row_iter)
                    except StopIteration:
                        return False
                    future = executor.submit(
                        probe_url,
                        row.get("url", ""),
                        config_path=config_path,
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
                            outcome = ProbeOutcome(
                                probe_status=PROBE_STATUS_UNPROBED,
                                probe_indicator_matches=0,
                                probe_preview=None,
                                probe_checked_at=_utcnow(),
                                probe_error=str(exc),
                            )

                        update_result_probe(
                            conn,
                            result_id=int(row["result_id"]),
                            probe_status=outcome.probe_status,
                            probe_indicator_matches=outcome.probe_indicator_matches,
                            probe_preview=outcome.probe_preview,
                            probe_checked_at=outcome.probe_checked_at,
                            probe_error=outcome.probe_error,
                            probe_snapshot_payload=getattr(outcome, "probe_snapshot_payload", None),
                        )
                        if outcome.probe_status == "unprobed" and outcome.probe_error:
                            unprobed_errors.append(
                                f"{row.get('url', '')}: {outcome.probe_error}"
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
                            f"Probed {row.get('url', '')}",
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
                f"Could not probe selected URL: {exc}",
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
        existing = [iid for iid in selected_iids if iid and self.tree.exists(iid)]
        if existing:
            self.tree.selection_set(*existing)
        if task_id:
            task_registry.remove_task(task_id)

    # ------------------------------------------------------------------
    # Promotion: Add to dirracuda DB
    # ------------------------------------------------------------------

    def _build_prefill(self, row: dict) -> Optional[dict]:
        """
        Build Add Record prefill payload from a se_dork result row.

        Returns None for unsupported/missing schemes or empty hostname.
        """
        url = row.get("url", "")
        try:
            parsed = urlparse(url)
            scheme = (parsed.scheme or "").lower()
        except Exception:
            return None

        if scheme not in ("http", "https"):
            return None

        hostname = parsed.hostname or ""
        if not hostname:
            return None

        try:
            port = parsed.port or (443 if scheme == "https" else 80)
        except ValueError:
            return None
        path = parsed.path or "/"

        prefill = {
            "host_type": "H",
            "host": hostname,
            "port": port,
            "scheme": scheme,
            "_probe_host_hint": hostname,
            "_probe_path_hint": path,
            "_promotion_source": "se_dork_browser",
            "_probe_cache": {
                "status": row.get("probe_status"),
                "indicator_matches": row.get("probe_indicator_matches"),
                "preview": row.get("probe_preview"),
                "checked_at": row.get("probe_checked_at"),
                "error": row.get("probe_error"),
            },
            "_probe_snapshot_source": "sidecar:se_dork",
        }
        snapshot = self._parse_probe_snapshot(row.get("probe_snapshot_json"))
        if snapshot is not None:
            prefill["_probe_snapshot"] = snapshot
        return prefill

    def _resolve_prefill_host_ipv4(self, prefill: dict) -> tuple[str, bool]:
        """
        Resolve prefill host to IPv4 for promotion.

        Returns (host_to_use, was_resolved). If resolution fails or is not needed,
        host_to_use is the original host and was_resolved=False.
        """
        host = str(prefill.get("host") or "").strip()
        if not host:
            return host, False

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
        if not self._allow_promotion:
            messagebox.showinfo(
                "Promotion disabled",
                "Rows from this view are already synced to the main database.",
                parent=self.window,
            )
            return
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
            url = row.get("url", "")
            messagebox.showinfo(
                "Cannot promote",
                f"URL '{url}' has an unsupported scheme or missing hostname.",
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
        if not was_resolved:
            try:
                ipaddress.ip_address(resolved_host)
            except ValueError:
                if resolved_host:
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
                url = str(row.get("url") or "").strip() or "unknown URL"
                skipped_reasons.append(
                    f"{url}: unsupported scheme or missing hostname."
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
                "Target": "SearXNG Results",
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


def show_se_dork_browser_window(
    parent: tk.Widget,
    db_path: Optional[Path] = None,
    add_record_callback=None,
    promote_record_callback=None,
    promote_records_callback=None,
    allow_promotion: bool = True,
    settings_manager=None,
) -> None:
    """Open the SE Dork results browser window."""
    SeDorkBrowserWindow(
        parent,
        db_path=db_path,
        add_record_callback=add_record_callback,
        promote_record_callback=promote_record_callback,
        promote_records_callback=promote_records_callback,
        allow_promotion=allow_promotion,
        settings_manager=settings_manager,
    )
