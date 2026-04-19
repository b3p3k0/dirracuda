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

Promotion follows the same callback pattern as reddit_browser_window:
  add_record_callback(prefill) is called with an HTTP prefill dict.
  If callback is None, "Not available" is shown on promotion attempt.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import ipaddress
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
from gui.utils import safe_messagebox as messagebox
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
    Promotion to dirracuda.db is available when add_record_callback is supplied.
    """

    def __init__(
        self,
        parent: tk.Widget,
        db_path: Optional[Path] = None,
        add_record_callback=None,
        settings_manager=None,
    ) -> None:
        self.parent = parent
        self.db_path = db_path
        self.theme = get_theme()
        self._add_record_callback = add_record_callback
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
        self._context_menu.add_separator()
        self._context_menu.add_command(
            label="Add to dirracuda DB",
            command=self._on_add_to_db,
        )
        self.tree.bind("<Button-3>", self._on_right_click)

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
                        )
                        if outcome.probe_status == "unprobed" and outcome.probe_error:
                            unprobed_errors.append(
                                f"{row.get('url', '')}: {outcome.probe_error}"
                            )
                        processed_count += 1
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

        return {
            "host_type": "H",
            "host": hostname,
            "port": port,
            "scheme": scheme,
            "_probe_host_hint": hostname,
            "_probe_path_hint": path,
            "_promotion_source": "se_dork_browser",
        }

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
            url = row.get("url", "")
            messagebox.showinfo(
                "Cannot promote",
                f"URL '{url}' has an unsupported scheme or missing hostname.",
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


def show_se_dork_browser_window(
    parent: tk.Widget,
    db_path: Optional[Path] = None,
    add_record_callback=None,
    settings_manager=None,
) -> None:
    """Open the SE Dork results browser window."""
    SeDorkBrowserWindow(
        parent,
        db_path=db_path,
        add_record_callback=add_record_callback,
        settings_manager=settings_manager,
    )
