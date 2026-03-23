"""
DashboardWidget scan-lifecycle and status mixin.

Extracted from dashboard.py to keep that module's line count manageable.
Provides scan progress tracking, status refresh, dashboard data refresh,
scan dialog launching, progress monitoring, and results display as a private
mixin class consumed only by DashboardWidget.  Do not import or instantiate
directly.
"""

import tkinter as tk
from tkinter import messagebox
import tkinter.font as tkfont
from datetime import datetime
import time
import os
from typing import Dict, Any, Optional, List

from gui.components.unified_scan_dialog import show_unified_scan_dialog
from gui.components.scan_results_dialog import show_scan_results_dialog
from gui.utils.logging_config import get_logger

_logger = get_logger("dashboard")


class _DashboardScanLifecycleMixin:
    """
    Private mixin providing scan lifecycle and status methods for DashboardWidget.

    Relies on the following attributes being set by DashboardWidget.__init__:
        self.parent                      - tk root / parent widget
        self.scan_manager                - ScanManager instance
        self.db_reader                   - DatabaseReader instance
        self.current_progress_summary    - str
        self.log_queue                   - queue.Queue[str]
        self.current_scan_options        - dict or None
        self.status_text                 - tk.StringVar
        self.update_time_label           - tk.Label
        self.size_enforcement_callback   - Callable or None
        self.config_editor_callback      - Callable or None
        self.config_path                 - str or None
        self.settings_manager            - SettingsManager instance
        self.scan_button_state           - str
        self.stopping_started_time       - float or None
        self._status_static_mode         - bool
        self._status_summary_initialized - bool
        self._status_refresh_pending     - bool
        self._queued_scan_active         - bool
        self._queued_scan_protocols      - List[str]
        self._pending_scan_results       - dict (set at runtime)

    Cross-mixin dependencies (resolved via MRO):
        self._append_log_line()                - dashboard_logs
        self._update_scan_button_state()       - _DashboardScanControlsMixin
        self._handle_queued_scan_completion()  - _DashboardScanOrchestrationMixin
        self._run_post_scan_batch_operations() - _DashboardBulkOpsMixin
        self._start_unified_scan()             - _DashboardScanOrchestrationMixin
    """

    def _pixels_to_text_lines(self, pixels: int) -> int:
        """
        Convert a pixel delta into Tk Text height units (lines).

        Text widgets size their height in lines (TkDocs Text tutorial),
        so we translate requested padding into line counts using the active
        monospace font metrics. This avoids fragile hard-coded guesses.
        """
        if pixels <= 0:
            return 0
        try:
            log_font = tkfont.Font(font=self.theme.fonts["mono"])
            line_height = max(1, log_font.metrics("linespace"))
        except tk.TclError:
            # Safe fallback during shutdown/detached widgets
            line_height = 14
        extra_lines = pixels // line_height
        if pixels % line_height:
            extra_lines += 1
        return extra_lines

    def _update_progress_summary(self, summary: Optional[str], detail: Optional[str] = None) -> None:
        """Cache scan progress summary for dialogs; UI status label stays static."""
        summary_text = summary.strip() if isinstance(summary, str) else (summary or "")
        detail_text = detail.strip() if isinstance(detail, str) else (detail or "")
        parts = []
        if summary_text:
            parts.append(summary_text)
        if detail_text:
            parts.append(detail_text)
        status_body = " - ".join(parts) if parts else "In progress"
        self.current_progress_summary = status_body

    def _log_status_event(self, message: str) -> None:
        """Append controller-level status lines to the console output."""
        if not message:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[status {timestamp}] {message}"
        try:
            self.log_queue.put(entry)
        except Exception:
            # Fallback if queue is unavailable (e.g., during shutdown)
            try:
                self._append_log_line(entry)
            except Exception:
                pass

    def _reset_scan_status(self) -> None:
        """Return dashboard status indicators to the ready state."""
        self.current_progress_summary = ""

    def _refresh_dashboard_data(self) -> None:
        """
        Refresh all dashboard data from database.

        Design Decision: Single refresh method ensures consistent data state
        across all dashboard components and handles errors gracefully.
        """
        try:
            # Get dashboard summary
            summary = self.db_reader.get_dashboard_summary()

            # Update status
            self.last_update = datetime.now()
            self._update_status_display(summary)

            # Enforce window size after data refresh to prevent auto-resizing
            if self.size_enforcement_callback:
                self.size_enforcement_callback()

        except Exception as e:
            self._handle_refresh_error(e)

    def _refresh_after_scan_completion(self) -> None:
        """
        Refresh dashboard after scan completion with cache invalidation.

        Ensures fresh data is loaded by clearing cache before refresh,
        which is critical for displaying updated Recent Discoveries count.
        """
        try:
            self._unlock_status_updates()
            # Clear cache to force fresh database queries
            self.db_reader.clear_cache()

            # Refresh dashboard with new data
            self._refresh_dashboard_data()
        except Exception as e:
            _logger.warning("Dashboard refresh error after scan completion: %s", e)
            # Continue anyway
        finally:
            self._lock_status_updates()
            self._status_refresh_pending = False

    def _update_status_display(self, summary: Dict[str, Any]) -> None:
        """Update status bar information."""
        if self._status_static_mode and self._status_summary_initialized:
            return

        # Main status
        total_servers = summary.get("total_servers", 0)
        servers_with_accessible_shares = summary.get("servers_with_accessible_shares", 0)
        total_shares = summary.get("total_shares", 0)
        last_scan = summary.get("last_scan", "Never")

        if last_scan != "Never":
            # Format last scan time
            try:
                scan_time = datetime.fromisoformat(last_scan.replace("Z", "+00:00"))
                formatted_time = scan_time.strftime("%Y-%m-%d %H:%M")
            except:
                formatted_time = "Unknown"
        else:
            formatted_time = "Never"

        status_text = (
            f"Last Scan: {formatted_time} | "
            f"DB: {total_servers:,} servers, {servers_with_accessible_shares:,} with accessible shares, "
            f"{total_shares:,} total shares"
        )
        self.status_text.set(status_text)
        self._status_summary_initialized = True

        # Update time
        if self.last_update:
            update_text = f"Updated: {self.last_update.strftime('%H:%M:%S')}"
            self.update_time_label.configure(text=update_text)

    def _handle_refresh_error(self, error: Exception) -> None:
        """Handle dashboard refresh errors gracefully."""
        error_message = f"Dashboard refresh failed: {str(error)}"
        self.status_text.set(f"Error: {error_message}")
        self._status_summary_initialized = False

        # If database is unavailable, enable mock mode
        if "Database" in str(error) or "database" in str(error):
            try:
                self.db_reader.enable_mock_mode()
                self._refresh_dashboard_data()  # Retry with mock data
                self.status_text.set("Using mock data - database unavailable")
            except:
                self.status_text.set("Dashboard unavailable - check backend")

    def _schedule_post_scan_refresh(self, delay_ms: int = 2000) -> None:
        """Schedule a status-refreshing dashboard update after scans finish."""
        if self._status_refresh_pending:
            return
        self._status_refresh_pending = True
        self.parent.after(delay_ms, self._refresh_after_scan_completion)

    def _unlock_status_updates(self) -> None:
        """Allow status summary text to update on next refresh."""
        self._status_static_mode = False
        self._status_summary_initialized = False

    def _lock_status_updates(self) -> None:
        """Freeze status summary text until explicitly unlocked."""
        self._status_static_mode = True


    def start_scan_progress(self, scan_type: str, countries: List[str]) -> None:
        """
        Start displaying scan progress.

        Args:
            scan_type: Type of scan being performed
            countries: Countries being scanned
        """
        countries_text = ", ".join(countries) if countries else "global"
        summary = f"Starting {scan_type} scan"
        detail = f"Countries: {countries_text}"
        self._update_progress_summary(summary, detail)
        self._log_status_event(f"{summary} for {countries_text}")

    def update_scan_progress(self, percentage: Optional[float], message: str) -> None:
        """
        Update scan progress display.

        Args:
            percentage: Progress percentage (0-100) or None for status-only update
            message: Progress message to display
        """
        if percentage is not None:
            summary = f"{percentage:.0f}% complete"
            detail = message if message else None
        else:
            summary = message if message else "Processing..."
            detail = None

        self._update_progress_summary(summary, detail)

        # Force UI update without triggering window auto-resize
        # Using update() instead of update_idletasks() to prevent geometry recalculation
        try:
            self.parent.update()
            # Enforce window size after UI update to prevent auto-resizing
            if self.size_enforcement_callback:
                self.size_enforcement_callback()
        except tk.TclError:
            # UI may be destroyed, ignore
            pass

    def finish_scan_progress(self, success: bool, results: Dict[str, Any]) -> None:
        """
        Finish scan progress display.

        Args:
            success: Whether scan completed successfully
            results: Scan results dictionary
        """
        if success:
            successful = results.get("successful_auth", 0)
            total = results.get("hosts_tested", 0)
            summary = f"Scan complete: {successful}/{total} servers accessible"
            self._update_progress_summary(summary, "Refreshing dashboard...")
            self._log_status_event(summary)

            # Refresh dashboard with new data (clear cache for fresh Recent Discoveries count)
            self._schedule_post_scan_refresh(delay_ms=2000)
        else:
            summary = "Scan failed - check backend connection"
            self._update_progress_summary(summary, None)
            self._log_status_event(summary)
            self._schedule_post_scan_refresh(delay_ms=2000)

        # Return to ready state after giving the user time to read the summary
        self.parent.after(5000, self._reset_scan_status)

    def _show_quick_scan_dialog(self) -> None:
        """Show scan configuration dialog and start scan."""
        # Check if scan is already active
        if self.scan_manager.is_scan_active():
            messagebox.showwarning(
                "Scan in Progress",
                "A scan is already running. Please wait for it to complete before starting another scan."
            )
            return

        # Show unified scan dialog
        show_unified_scan_dialog(
            parent=self.parent,
            config_path=self.config_path,
            scan_start_callback=self._start_unified_scan,
            settings_manager=getattr(self, "settings_manager", None),
            config_editor_callback=self._open_config_editor_from_scan,
            query_editor_callback=self._open_config_editor,
        )

    def _open_config_editor_from_scan(self, config_path: str) -> None:
        """Open configuration editor from scan dialog."""
        if self.config_editor_callback:
            self.config_editor_callback(config_path)

    def _handle_scan_progress(self, percentage: float, status: str, phase: str) -> None:
        """Handle progress updates from scan manager."""
        try:
            # Update status text with phase/percentage info
            detail_text = status if status else None
            if phase:
                phase_display = phase.replace("_", " ").title()
                if percentage is not None:
                    progress_text = f"{phase_display}: {percentage:.0f}%"
                else:
                    progress_text = phase_display
            else:
                if percentage is not None:
                    progress_text = f"{percentage:.0f}% complete"
                else:
                    progress_text = None

            if not progress_text:
                progress_text = detail_text if detail_text else "Processing..."
                detail_text = None

            self._update_progress_summary(progress_text, detail_text)

            # Note: No explicit update() needed here. When using UIDispatcher,
            # this callback runs on the main thread via after(), so Tk's event
            # loop handles UI refreshes automatically. Calling update() from a
            # dispatched callback would be unnecessary and risk reentrancy.

        except Exception as e:
            # Log error but don't interrupt scan
            _logger.warning("Progress update error: %s", e)

    def _show_scan_progress(self, country: Optional[str]) -> None:
        """Transition progress display to active scanning state."""
        scan_target = country if country else "global"
        summary = f"Initializing {scan_target} scan"
        self._update_progress_summary(summary, "Setting up scan parameters...")
        self._log_status_event(summary)

    def _monitor_scan_completion(self) -> None:
        """Monitor scan for completion and show results."""
        STOP_TIMEOUT_SECONDS = 10

        def check_completion():
            try:
                # Check for stop timeout while in "stopping" state
                if self.scan_button_state == "stopping" and self.stopping_started_time:
                    elapsed = time.time() - self.stopping_started_time
                    if elapsed > STOP_TIMEOUT_SECONDS and self.scan_manager.is_scanning:
                        # Stop is taking too long - offer retry
                        self._update_scan_button_state("retry")
                        self._log_status_event(
                            f"Stop taking longer than {STOP_TIMEOUT_SECONDS}s. "
                            "Click 'Stop (retry)' to try again."
                        )
                        # Continue monitoring
                        try:
                            self.parent.after(1000, check_completion)
                        except tk.TclError:
                            pass
                        return

                if not self.scan_manager.is_scanning:
                    # Get results first to check status
                    results = self.scan_manager.get_scan_results()
                    queue_has_more = self._queued_scan_active and bool(self._queued_scan_protocols)

                    # Reset button state to idle
                    self._update_scan_button_state("idle")

                    # Handle cancelled scans differently
                    if results and results.get("status") == "cancelled":
                        # Show lightweight info message for cancelled scan
                        try:
                            import tkinter.messagebox as msgbox
                            msgbox.showinfo(
                                "Scan Cancelled",
                                "Scan was cancelled by user request."
                            )
                        except Exception:
                            # Fallback - log message
                            _logger.info("Scan cancelled by user")
                        self._log_status_event("Scan cancelled by user request")
                        self._reset_scan_status()
                    elif results:
                        status = results.get("status", "")
                        success = results.get("success", False)
                        error = results.get("error")
                        # Be tolerant of different result field names
                        hosts_scanned = (
                            results.get("hosts_scanned", 0)
                            or results.get("hosts_tested", 0)
                            or results.get("hosts_discovered", 0)
                            or results.get("accessible_hosts", 0)
                            or results.get("shares_found", 0)
                            or 0
                        )

                        # Run bulk ops if scan finished and wasn't cancelled
                        # Permissive: check success flag OR status in completed/success/failed
                        is_finished = status not in {"cancelled"} and (
                            success or status in {"completed", "success", "failed"}
                        )
                        has_new_hosts = hosts_scanned > 0

                        bulk_probe_enabled = self.current_scan_options.get('bulk_probe_enabled', False) if self.current_scan_options else False
                        bulk_extract_enabled = self.current_scan_options.get('bulk_extract_enabled', False) if self.current_scan_options else False
                        has_bulk_ops = self.current_scan_options and is_finished and has_new_hosts and (bulk_probe_enabled or bulk_extract_enabled)

                        # Debug output for bulk ops decision
                        if os.getenv("XSMBSEEK_DEBUG_PARSING"):
                            _logger.debug("Bulk ops decision: status=%s, success=%s, is_finished=%s",
                                        status, success, is_finished)
                            _logger.debug("hosts_scanned=%d, has_new_hosts=%s",
                                        hosts_scanned, has_new_hosts)
                            _logger.debug("bulk_probe_enabled=%s, bulk_extract_enabled=%s",
                                        bulk_probe_enabled, bulk_extract_enabled)
                            _logger.debug("has_bulk_ops=%s", has_bulk_ops)

                        if has_bulk_ops:
                            self._pending_scan_results = results
                            self._run_post_scan_batch_operations(
                                self.current_scan_options,
                                results,
                                schedule_reset=not queue_has_more,
                                show_dialogs=not queue_has_more,
                            )
                        else:
                            # For queued multi-protocol runs, suppress intermediate summaries
                            # so the next protocol can start automatically.
                            if not queue_has_more:
                                self._show_scan_results(results)
                            if not queue_has_more:
                                try:
                                    self.parent.after(5000, self._reset_scan_status)
                                except tk.TclError:
                                    pass
                    else:
                        self._reset_scan_status()
                    # If no results, scan may have been cancelled before any results were recorded

                    # Refresh dashboard data with cache invalidation
                    try:
                        self._refresh_after_scan_completion()
                    except Exception as e:
                        _logger.warning("Dashboard refresh error after scan: %s", e)
                        # Continue anyway

                    if self._queued_scan_active and results:
                        self._handle_queued_scan_completion(results)
                else:
                    # Check again in 1 second
                    try:
                        self.parent.after(1000, check_completion)
                    except tk.TclError:
                        # UI destroyed, stop monitoring
                        pass

            except Exception as e:
                # Critical error in monitoring, show error and stop
                try:
                    messagebox.showerror(
                        "Scan Monitoring Error",
                        f"Error monitoring scan progress: {str(e)}\n\n"
                        "The scan may still be running in the background.\n"
                        "Please check the scan results manually."
                    )
                except:
                    # Even error dialog failed, just stop monitoring
                    pass

                # Try to clean up
                try:
                    self._reset_scan_status()
                except:
                    pass

        # Start monitoring with error protection
        try:
            self.parent.after(1000, check_completion)
        except tk.TclError:
            # UI not available
            pass

    def _show_scan_results(self, results: Dict[str, Any]) -> None:
        """Show scan results dialog."""
        try:
            # Show results dialog (details button removed by workflow change)
            show_scan_results_dialog(
                parent=self.parent,
                scan_results=results
            )

        except Exception as e:
            # Fallback to simple message box if results dialog fails
            status = results.get("status", "unknown")
            hosts_scanned = results.get("hosts_scanned", 0)
            accessible_hosts = results.get("accessible_hosts", 0)

            fallback_message = (
                f"Scan completed with status: {status}\n\n"
                f"Results:\n"
                f"• Hosts scanned: {hosts_scanned}\n"
                f"• Accessible hosts: {accessible_hosts}\n\n"
                f"Note: Full results dialog could not be displayed due to error:\n{str(e)}"
            )

            messagebox.showinfo("Scan Results", fallback_message)
