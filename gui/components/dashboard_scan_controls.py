"""
DashboardWidget scan-controls mixin.

Extracted from dashboard.py to keep that module's line count manageable.
Provides status-bar management, scan button state machine, start/stop
handlers, and external-scan detection as a private mixin class consumed
only by DashboardWidget.  Do not import or instantiate directly.
"""

import tkinter as tk
from tkinter import messagebox
import time
import os

from gui.components.ftp_scan_dialog import show_ftp_scan_dialog
from gui.components.http_scan_dialog import show_http_scan_dialog
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.logging_config import get_logger

_logger = get_logger("dashboard")


class _DashboardScanControlsMixin:
    """
    Private mixin providing scan-controls methods for DashboardWidget.

    Relies on the following attributes being set by DashboardWidget.__init__:
        self.parent               - tk root / parent widget
        self.main_frame           - primary container frame
        self.theme                - theme object with apply_to_widget(), fonts, colors
        self.scan_button          - tk.Button for SMB scans
        self.scan_button_state    - str state ("idle", "scanning", "stopping", …)
        self.ftp_scan_button      - tk.Button for FTP scans (may be None)
        self.http_scan_button     - tk.Button for HTTP scans (may be None)
        self.external_scan_pid    - int PID of detected external scan (may be None)
        self.stopping_started_time - float or None, used by stop-timeout logic
        self.scan_manager         - ScanManager instance
        self.backend_interface    - BackendInterface instance
        self.config_path          - path to SMBSeek config.json (may be None)
        self.current_scan_options - dict of active scan options (may be None)
        self._mock_mode_notice_shown - bool flag for one-time mock warning
        self.current_progress_summary - str (may be "")
        self.settings_manager     - SettingsManager instance
    """

    def _build_status_bar(self) -> None:
        """Build status bar for external scan notifications."""
        self.status_bar = tk.Frame(self.main_frame)
        self.theme.apply_to_widget(self.status_bar, "status_bar")
        self.status_bar.pack(fill=tk.X, pady=(10, 0))

        # Status message label (initially hidden)
        self.status_message = tk.Label(
            self.status_bar,
            text="",
            font=self.theme.fonts["small"]
        )
        self.theme.apply_to_widget(self.status_message, "status_bar")

        # Start hidden
        self._hide_status_bar()

    def _show_status_bar(self, message: str) -> None:
        """Show status bar with message."""
        self.status_message.config(text=message)
        self.status_message.pack(padx=10, pady=5)
        self.status_bar.pack(fill=tk.X, pady=(10, 0))

    def _hide_status_bar(self) -> None:
        """Hide status bar."""
        self.status_message.pack_forget()
        self.status_bar.pack_forget()

    def _handle_scan_button_click(self) -> None:
        """Handle scan button click based on current state."""
        if self.scan_button_state == "idle":
            self._maybe_warn_mock_mode_persistence()
            self._check_external_scans()  # Check again before starting
            if self.scan_button_state == "idle":  # Still idle after check
                self._show_quick_scan_dialog()
        elif self.scan_button_state == "scanning":
            self._show_stop_confirmation()
        elif self.scan_button_state == "disabled_external":
            # Show info about external scan
            messagebox.showinfo(
                "Scan In Progress",
                f"Another scan is currently running (PID: {self.external_scan_pid}). "
                "Please wait for it to complete or stop it from that application."
            )
        elif self.scan_button_state in ("retry", "error"):
            # Retry stopping the scan
            self._stop_scan_immediate()
        # "stopping" state doesn't respond to clicks (button is disabled)

    def _handle_ftp_scan_button_click(self) -> None:
        """Handle FTP scan button click — opens FTP scan dialog."""
        if self.scan_button_state == "idle":
            self._maybe_warn_mock_mode_persistence()
            self._check_external_scans()
            if self.scan_button_state == "idle":
                show_ftp_scan_dialog(
                    parent=self.parent,
                    config_path=self.config_path,
                    scan_start_callback=self._start_ftp_scan,
                    settings_manager=getattr(self, "settings_manager", None),
                    config_editor_callback=self._open_config_editor_from_scan,
                )
        # Non-idle states: button is disabled; defensive no-op if somehow reached.

    def _handle_http_scan_button_click(self) -> None:
        """Handle HTTP scan button click — opens HTTP scan dialog."""
        if self.scan_button_state == "idle":
            self._maybe_warn_mock_mode_persistence()
            self._check_external_scans()
            if self.scan_button_state == "idle":
                show_http_scan_dialog(
                    parent=self.parent,
                    config_path=self.config_path,
                    scan_start_callback=self._start_http_scan,
                    settings_manager=getattr(self, "settings_manager", None),
                    config_editor_callback=self._open_config_editor_from_scan,
                )
        # Non-idle states: button is disabled; defensive no-op if somehow reached.

    def _maybe_warn_mock_mode_persistence(self) -> None:
        """Show one-time warning that mock scans are non-persistent."""
        if self._mock_mode_notice_shown:
            return
        if not getattr(self.backend_interface, "mock_mode", False):
            return
        self._mock_mode_notice_shown = True
        messagebox.showinfo(
            "Mock Mode Active",
            "Mock mode is enabled. Scan results are simulated and are not written to the database.",
            parent=self.parent,
        )

    def _start_ftp_scan(self, scan_options: dict) -> bool:
        """Start FTP scan with options from dialog. Mirrors _start_new_scan()."""
        # Final race-condition check before acquiring scan lock.
        self._check_external_scans()
        if self.scan_button_state != "idle":
            return False

        # BackendInterface expects a directory path; "." mirrors BackendInterface defaults.
        backend_path_obj = getattr(self.backend_interface, "backend_path", None)
        backend_path = str(backend_path_obj) if backend_path_obj else "."

        started = self.scan_manager.start_ftp_scan(
            scan_options=scan_options,
            backend_path=backend_path,
            progress_callback=self._handle_scan_progress,
            log_callback=self._handle_scan_log_line,
            config_path=self.config_path,
        )

        if started:
            self.current_scan_options = scan_options
            self._reset_log_output(scan_options.get("country"))
            self._update_scan_button_state("scanning")
            self._show_scan_progress(scan_options.get("country"))
            self._monitor_scan_completion()
            return True
        else:
            messagebox.showerror(
                "FTP Scan Error",
                "Could not start FTP scan.\n"
                "A scan may already be running.",
                parent=self.parent,
            )
            return False

    def _start_http_scan(self, scan_options: dict) -> bool:
        """Start HTTP scan with options from dialog. Mirrors _start_ftp_scan()."""
        # Final race-condition check before acquiring scan lock.
        self._check_external_scans()
        if self.scan_button_state != "idle":
            return False

        # BackendInterface expects a directory path; "." mirrors BackendInterface defaults.
        backend_path_obj = getattr(self.backend_interface, "backend_path", None)
        backend_path = str(backend_path_obj) if backend_path_obj else "."

        started = self.scan_manager.start_http_scan(
            scan_options=scan_options,
            backend_path=backend_path,
            progress_callback=self._handle_scan_progress,
            log_callback=self._handle_scan_log_line,
            config_path=self.config_path,
        )

        if started:
            self.current_scan_options = scan_options
            self._reset_log_output(scan_options.get("country"))
            self._update_scan_button_state("scanning")
            self._show_scan_progress(scan_options.get("country"))
            self._monitor_scan_completion()
            return True
        else:
            messagebox.showerror(
                "HTTP Scan Error",
                "Could not start HTTP scan.\n"
                "A scan may already be running.",
                parent=self.parent,
            )
            return False

    def _update_scan_button_state(self, new_state: str) -> None:
        """Update scan button state and appearance."""
        self.scan_button_state = new_state

        if new_state == "idle":
            self._set_button_to_start()
            self._hide_status_bar()
            self.stopping_started_time = None  # Clear stop timeout tracking
            if self.ftp_scan_button is not None:
                self.ftp_scan_button.config(state=tk.NORMAL)
            if self.http_scan_button is not None:
                self.http_scan_button.config(state=tk.NORMAL)
        elif new_state == "disabled_external":
            self._set_button_to_disabled()
            self._show_status_bar(f"Scan running by PID: {self.external_scan_pid} - Please wait")
            if self.ftp_scan_button is not None:
                self.ftp_scan_button.config(state=tk.DISABLED)
            if self.http_scan_button is not None:
                self.http_scan_button.config(state=tk.DISABLED)
        elif new_state == "scanning":
            self._set_button_to_stop()
            self._hide_status_bar()
            if self.ftp_scan_button is not None:
                self.ftp_scan_button.config(state=tk.DISABLED)
            if self.http_scan_button is not None:
                self.http_scan_button.config(state=tk.DISABLED)
        elif new_state == "stopping":
            self._set_button_to_stopping()
            if self.ftp_scan_button is not None:
                self.ftp_scan_button.config(state=tk.DISABLED)
            if self.http_scan_button is not None:
                self.http_scan_button.config(state=tk.DISABLED)
        elif new_state == "retry":
            self._set_button_to_retry()
            if self.ftp_scan_button is not None:
                self.ftp_scan_button.config(state=tk.DISABLED)
            if self.http_scan_button is not None:
                self.http_scan_button.config(state=tk.DISABLED)
        elif new_state == "error":
            self._set_button_to_error()
            if self.ftp_scan_button is not None:
                self.ftp_scan_button.config(state=tk.DISABLED)
            if self.http_scan_button is not None:
                self.http_scan_button.config(state=tk.DISABLED)

    def _set_button_to_start(self) -> None:
        """Configure button for start state."""
        self.scan_button.config(
            text="▶ Start Scan",
            state="normal"
        )
        self.theme.apply_to_widget(self.scan_button, "button_primary")

    def _set_button_to_stop(self) -> None:
        """Configure button for stop state."""
        self.scan_button.config(
            text="⬛ Stop Scan",
            state="normal"
        )
        self.theme.apply_to_widget(self.scan_button, "button_danger")

    def _set_button_to_disabled(self) -> None:
        """Configure button for disabled state (external scan)."""
        self.scan_button.config(
            text="🔍 Scan Running",
            state="disabled"
        )
        self.theme.apply_to_widget(self.scan_button, "button_disabled")

    def _set_button_to_stopping(self) -> None:
        """Configure button for stopping state with warning color."""
        self.stopping_started_time = time.time()
        self.scan_button.config(
            text="⏳ Stopping...",
            state="disabled"
        )
        # Apply secondary theme first, then override with warning color
        self.theme.apply_to_widget(self.scan_button, "button_secondary")
        self.scan_button.config(bg=self.theme.colors["warning"])

    def _set_button_to_retry(self) -> None:
        """Configure button for retry state after stop timeout."""
        self.scan_button.config(
            text="⏹ Stop (retry)",
            state="normal"
        )
        # Use warning color to indicate retry needed
        self.theme.apply_to_widget(self.scan_button, "button_secondary")
        self.scan_button.config(bg=self.theme.colors["warning"])

    def _set_button_to_error(self) -> None:
        """Configure button for error state."""
        self.scan_button.config(
            text="⬛ Stop Failed",
            state="normal"
        )
        self.theme.apply_to_widget(self.scan_button, "button_danger")

    # ===== LOCK FILE MANAGEMENT =====

    def _check_external_scans(self) -> None:
        """Check for external scans using lock file system."""
        try:
            if self.scan_manager.is_scan_active():
                # Get lock file info
                lock_file_path = os.path.join(os.path.dirname(__file__), '..', '..', '.scan_lock')
                if os.path.exists(lock_file_path):
                    import json
                    with open(lock_file_path, 'r') as f:
                        lock_data = json.load(f)

                    # Check if it's our own scan or external
                    lock_pid = lock_data.get('process_id')
                    current_pid = os.getpid()

                    if lock_pid != current_pid:
                        # External scan detected
                        if self._validate_external_process(lock_pid):
                            self.external_scan_pid = lock_pid
                            self._update_scan_button_state("disabled_external")
                            return
                        else:
                            # Stale lock file - clean it up
                            self.scan_manager._cleanup_stale_locks()
                    else:
                        # Our own scan is running
                        if self.scan_manager.is_scanning:
                            self._update_scan_button_state("scanning")
                        else:
                            # Scan completed, update state
                            self._update_scan_button_state("idle")
                        return

            # No active scans detected
            self._update_scan_button_state("idle")

        except Exception as e:
            _logger.warning("Error checking external scans: %s", e)
            # Fallback to idle state
            self._update_scan_button_state("idle")

    def _validate_external_process(self, pid: int) -> bool:
        """Validate that external process is actually running."""
        try:
            # Try psutil first (more reliable)
            try:
                import psutil
                return psutil.pid_exists(pid)
            except ImportError:
                # Fallback to os.kill method
                import signal
                os.kill(pid, 0)  # Doesn't actually kill, just checks existence
                return True
        except (OSError, ProcessLookupError):
            return False
        except Exception:
            # Unknown error, assume process exists to be safe
            return True

    # ===== STOP CONFIRMATION DIALOG =====

    def _show_stop_confirmation(self) -> None:
        """Show confirmation dialog for stopping scan."""
        # Custom dialog for stop options
        dialog = tk.Toplevel(self.parent)
        dialog.title("Stop Scan")
        dialog.geometry("400x250")
        dialog.minsize(300, 200)
        dialog.transient(self.parent)

        # Apply theme
        self.theme.apply_to_widget(dialog, "main_window")

        # Header
        header_label = self.theme.create_styled_label(
            dialog,
            "⚠️ Stop Scan Confirmation",
            "heading"
        )
        header_label.pack(pady=(20, 10))

        # Warning message
        warning_text = (
            "Stopping the scan may result in incomplete data collection.\n"
            "Choose how you would like to stop the scan:"
        )
        warning_label = self.theme.create_styled_label(
            dialog,
            warning_text,
            "body",
            justify="center"
        )
        warning_label.pack(pady=(0, 20), padx=20)

        # Progress context (if available)
        current_progress = getattr(self, "current_progress_summary", "")
        if current_progress:
            progress_label = self.theme.create_styled_label(
                dialog,
                f"Current: {current_progress}",
                "small",
                fg=self.theme.colors["text_secondary"]
            )
            progress_label.pack(pady=(0, 20))

        # Buttons frame
        buttons_frame = tk.Frame(dialog)
        self.theme.apply_to_widget(buttons_frame, "main_window")
        buttons_frame.pack(pady=20)

        # Stop now button
        stop_now_btn = tk.Button(
            buttons_frame,
            text="Stop Now",
            command=lambda: self._handle_stop_choice(dialog, "immediate")
        )
        self.theme.apply_to_widget(stop_now_btn, "button_danger")
        stop_now_btn.pack(side=tk.LEFT, padx=(0, 10))

        # Stop after host button
        stop_after_btn = tk.Button(
            buttons_frame,
            text="Stop After Current Host",
            command=lambda: self._handle_stop_choice(dialog, "graceful")
        )
        self.theme.apply_to_widget(stop_after_btn, "button_secondary")
        stop_after_btn.pack(side=tk.LEFT, padx=(10, 0))

        # Cancel button
        cancel_btn = tk.Button(
            buttons_frame,
            text="Cancel",
            command=dialog.destroy
        )
        self.theme.apply_to_widget(cancel_btn, "button_secondary")
        cancel_btn.pack(side=tk.LEFT, padx=(10, 0))

        # Handle window close
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        # Finalize geometry and focus/stacking after content exists.
        dialog.update_idletasks()
        parent_x = self.parent.winfo_x()
        parent_y = self.parent.winfo_y()
        parent_w = self.parent.winfo_width()
        parent_h = self.parent.winfo_height()
        dialog_w = dialog.winfo_width()
        dialog_h = dialog.winfo_height()
        x = parent_x + max(0, (parent_w - dialog_w) // 2)
        y = parent_y + max(0, (parent_h - dialog_h) // 2)
        dialog.geometry(f"{dialog_w}x{dialog_h}+{x}+{y}")

        dialog.grab_set()
        ensure_dialog_focus(dialog, self.parent)
        cancel_btn.focus_set()

    def _handle_stop_choice(self, dialog: tk.Toplevel, choice: str) -> None:
        """Handle user's stop choice."""
        dialog.destroy()

        if choice == "immediate":
            self._stop_scan_immediate()
        elif choice == "graceful":
            self._stop_scan_after_host()

    # ===== SCAN STOP FUNCTIONALITY =====

    def _stop_scan_immediate(self) -> None:
        """Stop scan immediately."""
        self._update_scan_button_state("stopping")

        try:
            success = self.scan_manager.interrupt_scan()

            if success:
                # Stop signal sent - stay in "stopping" state
                # Monitor loop will detect when scan actually terminates
                # and transition to "idle" or "retry" as appropriate
                self._log_status_event("Stop command sent, waiting for scan to terminate...")
            else:
                # Stop failed immediately
                self._handle_stop_error("Failed to interrupt scan - scan may not be active")

        except Exception as e:
            self._handle_stop_error(f"Error stopping scan: {str(e)}")

    def _stop_scan_after_host(self) -> None:
        """Stop scan after current host completes."""
        # For now, implement as immediate stop with different message
        # Future enhancement: could add graceful stopping to scan manager
        self._update_scan_button_state("stopping")

        try:
            success = self.scan_manager.interrupt_scan()

            if success:
                # Stop signal sent - stay in "stopping" state
                # Monitor loop will handle the transition
                self._log_status_event("Stop command sent, scan will finish current host...")
            else:
                self._handle_stop_error("Failed to schedule graceful stop")

        except Exception as e:
            self._handle_stop_error(f"Error scheduling graceful stop: {str(e)}")

    def _handle_stop_error(self, error_message: str) -> None:
        """Handle scan stop error."""
        # Double-check actual scan state
        if not self.scan_manager.is_scanning:
            # Scan actually stopped despite error
            self._update_scan_button_state("idle")
            messagebox.showinfo(
                "Scan Stopped",
                "Scan has stopped (despite error in communication)."
            )
        else:
            # Scan still running, show error state
            self._update_scan_button_state("error")
            messagebox.showerror(
                "Stop Failed",
                f"Failed to stop scan: {error_message}\n\n"
                "Click 'Stop Scan' again to retry."
            )
