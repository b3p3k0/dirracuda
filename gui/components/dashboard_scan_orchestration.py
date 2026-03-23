"""
DashboardWidget scan-orchestration mixin.

Extracted from dashboard.py to keep that module's line count manageable.
Provides queued multi-protocol scan management and the SMB start path as a
private mixin class consumed only by DashboardWidget.  Do not import or
instantiate directly.
"""

import tkinter as tk
from tkinter import messagebox
import os
from typing import Dict, Any

from gui.utils.logging_config import get_logger

_logger = get_logger("dashboard")


class _DashboardScanOrchestrationMixin:
    """
    Private mixin providing scan-orchestration methods for DashboardWidget.

    Relies on the following attributes being set by DashboardWidget.__init__:
        self.parent                       - tk root / parent widget
        self.backend_interface            - BackendInterface instance
        self.scan_manager                 - ScanManager instance
        self.config_path                  - path to conf/config.json
        self.current_scan_options         - dict updated before each scan
        self.scan_button_state            - str state machine value
        self._queued_scan_active          - bool
        self._queued_scan_protocols       - list[str]
        self._queued_scan_common_options  - dict | None
        self._queued_scan_current_protocol - str | None
        self._queued_scan_failures        - list[dict]
    """

    def _clear_queued_scan_state(self) -> None:
        """Reset in-memory state for queued multi-protocol scan runs."""
        self._queued_scan_active = False
        self._queued_scan_protocols = []
        self._queued_scan_common_options = None
        self._queued_scan_current_protocol = None
        self._queued_scan_failures = []

    def _start_unified_scan(self, scan_request: dict) -> None:
        """
        Start scans from unified dialog request.

        If multiple protocols are selected, scans execute sequentially.
        """
        protocols = [
            str(p).strip().lower()
            for p in (scan_request.get("protocols") or [])
            if str(p).strip().lower() in {"smb", "ftp", "http"}
        ]
        if not protocols:
            messagebox.showerror(
                "Scan Error",
                "No protocols selected. Please select at least one protocol."
            )
            return

        # Single protocol: run directly (no queue wrapper).
        if len(protocols) == 1:
            self._clear_queued_scan_state()
            protocol = protocols[0]
            options = self._build_protocol_scan_options(protocol, scan_request)
            self._start_protocol_scan(protocol, options)
            return

        # Multi-protocol queue.
        self._queued_scan_active = True
        self._queued_scan_protocols = list(protocols)
        self._queued_scan_common_options = dict(scan_request)
        self._queued_scan_current_protocol = None
        self._queued_scan_failures = []
        self._launch_next_queued_scan()

    def _build_protocol_scan_options(self, protocol: str, common_options: Dict[str, Any]) -> Dict[str, Any]:
        """Convert unified dialog options into protocol-specific scan options."""
        country = common_options.get("country")
        max_results = common_options.get("max_shodan_results", 1000)
        custom_filters = common_options.get("custom_filters", "")
        verbose = bool(common_options.get("verbose", False))
        bulk_probe = bool(common_options.get("bulk_probe_enabled", False))
        bulk_extract = bool(common_options.get("bulk_extract_enabled", False))
        skip_indicator_extract = bool(common_options.get("bulk_extract_skip_indicators", True))
        rce_enabled = bool(common_options.get("rce_enabled", False))

        try:
            shared_concurrency = int(common_options.get("shared_concurrency", 10))
        except (TypeError, ValueError):
            shared_concurrency = 10
        try:
            shared_timeout = int(common_options.get("shared_timeout_seconds", 10))
        except (TypeError, ValueError):
            shared_timeout = 10

        shared_concurrency = max(1, min(256, shared_concurrency))
        shared_timeout = max(1, min(300, shared_timeout))

        if protocol == "smb":
            security_mode = str(common_options.get("security_mode", "cautious")).strip().lower()
            if security_mode not in {"cautious", "legacy"}:
                security_mode = "cautious"
            return {
                "country": country,
                "max_shodan_results": max_results,
                "custom_filters": custom_filters,
                "discovery_max_concurrent_hosts": shared_concurrency,
                "access_max_concurrent_hosts": shared_concurrency,
                "connection_timeout": shared_timeout,
                "security_mode": security_mode,
                "verbose": verbose,
                "rce_enabled": rce_enabled,
                "bulk_probe_enabled": bulk_probe,
                "bulk_extract_enabled": bulk_extract,
                "bulk_extract_skip_indicators": skip_indicator_extract,
            }

        if protocol == "ftp":
            return {
                "country": country,
                "max_shodan_results": max_results,
                "custom_filters": custom_filters,
                "discovery_max_concurrent_hosts": shared_concurrency,
                "access_max_concurrent_hosts": shared_concurrency,
                "connect_timeout": shared_timeout,
                "auth_timeout": shared_timeout,
                "listing_timeout": shared_timeout,
                "verbose": verbose,
                "rce_enabled": rce_enabled,
                "bulk_probe_enabled": bulk_probe,
                "bulk_extract_enabled": bulk_extract,
                "bulk_extract_skip_indicators": skip_indicator_extract,
            }

        # HTTP
        allow_insecure_tls = bool(common_options.get("allow_insecure_tls", True))
        return {
            "country": country,
            "max_shodan_results": max_results,
            "custom_filters": custom_filters,
            "discovery_max_concurrent_hosts": shared_concurrency,
            "access_max_concurrent_hosts": shared_concurrency,
            "connect_timeout": shared_timeout,
            "request_timeout": shared_timeout,
            "subdir_timeout": shared_timeout,
            "verify_http": True,
            "verify_https": True,
            "allow_insecure_tls": allow_insecure_tls,
            "verbose": verbose,
            "rce_enabled": rce_enabled,
            "bulk_probe_enabled": bulk_probe,
            "bulk_extract_enabled": bulk_extract,
            "bulk_extract_skip_indicators": skip_indicator_extract,
        }

    def _start_protocol_scan(self, protocol: str, scan_options: Dict[str, Any]) -> bool:
        """Dispatch launch to the existing protocol-specific start handlers."""
        if protocol == "smb":
            return bool(self._start_new_scan(scan_options))
        if protocol == "ftp":
            return bool(self._start_ftp_scan(scan_options))
        if protocol == "http":
            return bool(self._start_http_scan(scan_options))
        return False

    def _launch_next_queued_scan(self) -> None:
        """Start the next protocol in queue, if any remain."""
        if not self._queued_scan_active:
            return

        if not self._queued_scan_protocols:
            if self._queued_scan_failures:
                lines = [
                    f"- {item['protocol'].upper()}: {item['reason']}"
                    for item in self._queued_scan_failures
                ]
                messagebox.showwarning(
                    "Queued Scans Completed With Failures",
                    "One or more protocol scans failed:\n\n" + "\n".join(lines),
                )
            self._clear_queued_scan_state()
            return

        protocol = self._queued_scan_protocols.pop(0)
        self._queued_scan_current_protocol = protocol
        common = self._queued_scan_common_options or {}
        scan_options = self._build_protocol_scan_options(protocol, common)

        started = self._start_protocol_scan(protocol, scan_options)
        if not started:
            self._queued_scan_failures.append(
                {"protocol": protocol, "reason": "failed to start"}
            )
            messagebox.showwarning(
                "Protocol Start Failed",
                f"{protocol.upper()} scan failed to start. Continuing to next protocol.",
            )
            try:
                self.parent.after(150, self._launch_next_queued_scan)
            except tk.TclError:
                pass

    def _handle_queued_scan_completion(self, results: Dict[str, Any]) -> None:
        """Handle queue continuation after each protocol scan completes."""
        if not self._queued_scan_active:
            return

        protocol = (self._queued_scan_current_protocol or results.get("protocol") or "smb").lower()
        status = str(results.get("status", "")).lower()
        success = bool(results.get("success", False))
        error = str(results.get("error", "") or "").strip()

        # User cancellation stops the queue.
        if status == "cancelled":
            self._clear_queued_scan_state()
            messagebox.showinfo(
                "Queued Scans Cancelled",
                "Scan queue cancelled by user. Remaining protocols were not started.",
            )
            return

        failed = status in {"failed", "error"} or (not success and bool(error))
        if failed:
            reason = error or status or "unknown error"
            self._queued_scan_failures.append({"protocol": protocol, "reason": reason})
            messagebox.showwarning(
                "Protocol Scan Failed",
                f"{protocol.upper()} scan failed but the queue will continue.\n\nReason: {reason}",
            )

        if self._queued_scan_protocols:
            try:
                self.parent.after(150, self._launch_next_queued_scan)
            except tk.TclError:
                pass
        else:
            self._launch_next_queued_scan()

    def _start_new_scan(self, scan_options: dict) -> bool:
        """Start new scan with specified options."""
        try:
            # Final check for external scans before starting
            self._check_external_scans()
            if self.scan_button_state != "idle":
                return False  # External scan detected, don't proceed

            # Store scan options for post-scan batch operations
            self.current_scan_options = scan_options

            # Get backend path for external SMBSeek installation
            backend_path = getattr(self.backend_interface, "backend_path", ".")
            backend_path = str(backend_path)

            # Start scan via scan manager with new options
            success = self.scan_manager.start_scan(
                scan_options=scan_options,
                backend_path=backend_path,
                progress_callback=self._handle_scan_progress,
                log_callback=self._handle_scan_log_line,
                config_path=self.config_path,
            )

            if success:
                # Reset viewer and note which scan is running
                self._reset_log_output(scan_options.get('country'))

                # Update button state to scanning
                self._update_scan_button_state("scanning")

                # Show progress display
                country = scan_options.get('country')
                self._show_scan_progress(country)

                # Start monitoring scan completion
                self._monitor_scan_completion()
                return True
            else:
                # Get more specific error information
                error_details = []

                # Check if backend path exists
                if not os.path.exists(backend_path):
                    error_details.append(f"• Backend path not found: {backend_path}")

                # Check if SMBSeek executable exists
                smbseek_cli = os.path.join(backend_path, "cli", "smbseek.py")
                if not os.path.exists(smbseek_cli):
                    error_details.append(f"• SMBSeek CLI not found: {smbseek_cli}")

                # Check scan manager state
                if self.scan_manager.is_scanning:
                    error_details.append("• Scan manager reports scan already in progress")

                # Check for lock file
                lock_file_path = os.path.join(os.path.dirname(__file__), '..', '..', '.scan_lock')
                if os.path.exists(lock_file_path):
                    error_details.append("• Lock file exists, indicating another scan may be running")

                if error_details:
                    detailed_msg = "Failed to start scan. Issues detected:\n\n" + "\n".join(error_details)
                    detailed_msg += "\n\nPlease ensure SMBSeek is properly installed and configured."
                else:
                    detailed_msg = "Failed to start scan. Another scan may already be running."

                messagebox.showerror("Scan Error", detailed_msg)
                return False
        except Exception as e:
            error_msg = str(e)

            # Provide specific guidance based on error type
            if "backend" in error_msg.lower() or "not found" in error_msg.lower():
                detailed_msg = (
                    f"Backend interface error: {error_msg}\n\n"
                    "This usually indicates:\n"
                    "• SMBSeek backend is not installed or not in expected location\n"
                    "• Backend CLI is not executable\n"
                    "• Configuration file is missing\n\n"
                    "Please ensure the backend is properly installed and configured."
                )
            elif "lock" in error_msg.lower():
                detailed_msg = (
                    f"Scan coordination error: {error_msg}\n\n"
                    "Another scan may already be running. Please wait for it to complete\n"
                    "or restart the application if the scan appears to be stuck."
                )
            else:
                detailed_msg = (
                    f"Scan initialization failed: {error_msg}\n\n"
                    "Please try again or check the configuration settings."
                )

            messagebox.showerror("Scan Error", detailed_msg)
            return False
