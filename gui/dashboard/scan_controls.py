"""
Scan control helpers extracted from DashboardWidget.

These functions are bound onto DashboardWidget at import-time to keep
public method names stable while reducing widget.py size.
"""

import os
import sys
import time
import tkinter as tk
from typing import Any

from experimental.redseek.service import IngestOptions, IngestResult
from gui.components import dashboard_scan
from gui.utils import safe_messagebox as _fallback_msgbox
from gui.utils.dialog_helpers import ensure_dialog_focus


def _mb():
    """Return messagebox from gui.components.dashboard's namespace."""
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None and hasattr(mod, "messagebox"):
        return mod.messagebox
    return _fallback_msgbox


def _d(name: str) -> Any:
    """Resolve a name from gui.components.dashboard at call-time."""
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None:
        return getattr(mod, name)
    raise RuntimeError(
        f"gui.components.dashboard not yet loaded (looking for {name!r})"
    )

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
        _mb().showinfo(
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
            _d("show_ftp_scan_dialog")(
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
            _d("show_http_scan_dialog")(
                parent=self.parent,
                config_path=self.config_path,
                scan_start_callback=self._start_http_scan,
                settings_manager=getattr(self, "settings_manager", None),
                config_editor_callback=self._open_config_editor_from_scan,
            )
    # Non-idle states: button is disabled; defensive no-op if somehow reached.

def _handle_reddit_grab_button_click(self) -> None:
    """Handle Reddit Grab button click — opens Reddit Grab dialog."""
    if self._reddit_grab_running:
        return
    self._check_external_scans()
    if self.scan_button_state != "idle":
        return
    _d('show_reddit_grab_dialog')(
        parent=self.parent,
        grab_start_callback=self._handle_reddit_grab_start,
        settings_manager=getattr(self, "settings_manager", None),
    )

def _handle_reddit_grab_start(self, options: IngestOptions) -> None:
    """Callback from Reddit Grab dialog — launches background ingest worker."""
    # Second state check: dialog may have been open while scan state changed.
    self._check_external_scans()
    if self.scan_button_state != "idle" or self._reddit_grab_running:
        return

    self._reddit_grab_running = True
    if self.reddit_grab_button is not None:
        self.reddit_grab_button.config(state=tk.DISABLED)
    self._log_status_event(
        f"Reddit Grab started (sort={options.sort}, max_posts={options.max_posts})"
    )
    _d('threading').Thread(
        target=self._reddit_grab_worker,
        args=(options,),
        daemon=True,
    ).start()

def _reddit_grab_worker(self, options: IngestOptions) -> None:
    """Background worker: runs run_ingest and marshals result to main thread."""
    try:
        result = _d('run_ingest')(options)
    except Exception as exc:
        result = IngestResult(
            sort=options.sort,
            subreddit=options.subreddit,
            pages_fetched=0,
            posts_stored=0,
            posts_skipped=0,
            targets_stored=0,
            targets_deduped=0,
            parse_errors=0,
            stopped_by_cursor=False,
            stopped_by_max_posts=False,
            replace_cache_done=False,
            rate_limited=False,
            error=f"unexpected error: {exc}",
        )
    self.parent.after(0, self._on_reddit_grab_done, result)

def _on_reddit_grab_done(self, result: IngestResult) -> None:
    """Main-thread completion handler for a Reddit Grab run."""
    self._reddit_grab_running = False
    if self.reddit_grab_button is not None and self.scan_button_state == "idle":
        self.reddit_grab_button.config(state=tk.NORMAL)

    if result.error:
        detail = f"Error: {result.error}"
        if result.rate_limited:
            detail = f"Rate limited (HTTP 429). {detail}"
        if result.replace_cache_done:
            detail += "\nNote: cache was wiped before the failure."
        self._log_status_event(f"Reddit Grab failed: {result.error}")
        _mb().showerror("Reddit Grab Failed", detail, parent=self.parent)
    else:
        stop_reason = ""
        if result.stopped_by_cursor:
            stop_reason = " (stopped at known cursor)"
        elif result.stopped_by_max_posts:
            stop_reason = " (max posts reached)"
        summary = (
            f"sort={result.sort}{stop_reason}\n"
            f"Pages fetched: {result.pages_fetched}\n"
            f"Posts stored: {result.posts_stored}  "
            f"Skipped: {result.posts_skipped}\n"
            f"Targets stored: {result.targets_stored}  "
            f"Deduped: {result.targets_deduped}"
        )
        if result.replace_cache_done:
            summary += "\nCache replaced before run."
        self._log_status_event(
            f"Reddit Grab done — {result.posts_stored} posts, "
            f"{result.targets_stored} targets"
        )
        _mb().showinfo("Reddit Grab Complete", summary, parent=self.parent)

def _maybe_warn_mock_mode_persistence(self) -> None:
    """Show one-time warning that mock scans are non-persistent."""
    if self._mock_mode_notice_shown:
        return
    if not getattr(self.backend_interface, "mock_mode", False):
        return
    self._mock_mode_notice_shown = True
    _mb().showinfo(
        "Mock Mode Active",
        "Mock mode is enabled. Scan results are simulated and are not written to the database.",
        parent=self.parent,
    )

def _start_ftp_scan(self, scan_options: dict) -> bool:
    """Start FTP scan with options from dialog. Mirrors _start_new_scan()."""
    return dashboard_scan.start_ftp_scan(self, scan_options)

def _start_http_scan(self, scan_options: dict) -> bool:
    """Start HTTP scan with options from dialog. Mirrors _start_ftp_scan()."""
    return dashboard_scan.start_http_scan(self, scan_options)

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
        if self.reddit_grab_button is not None:
            state = tk.DISABLED if self._reddit_grab_running else tk.NORMAL
            self.reddit_grab_button.config(state=state)
    elif new_state == "disabled_external":
        self._set_button_to_disabled()
        self._show_status_bar(f"Scan running by PID: {self.external_scan_pid} - Please wait")
        if self.ftp_scan_button is not None:
            self.ftp_scan_button.config(state=tk.DISABLED)
        if self.http_scan_button is not None:
            self.http_scan_button.config(state=tk.DISABLED)
        if self.reddit_grab_button is not None:
            self.reddit_grab_button.config(state=tk.DISABLED)
    elif new_state == "scanning":
        self._set_button_to_stop()
        self._hide_status_bar()
        if self.ftp_scan_button is not None:
            self.ftp_scan_button.config(state=tk.DISABLED)
        if self.http_scan_button is not None:
            self.http_scan_button.config(state=tk.DISABLED)
        if self.reddit_grab_button is not None:
            self.reddit_grab_button.config(state=tk.DISABLED)
    elif new_state == "stopping":
        self._set_button_to_stopping()
        if self.ftp_scan_button is not None:
            self.ftp_scan_button.config(state=tk.DISABLED)
        if self.http_scan_button is not None:
            self.http_scan_button.config(state=tk.DISABLED)
        if self.reddit_grab_button is not None:
            self.reddit_grab_button.config(state=tk.DISABLED)
    elif new_state == "retry":
        self._set_button_to_retry()
        if self.ftp_scan_button is not None:
            self.ftp_scan_button.config(state=tk.DISABLED)
        if self.http_scan_button is not None:
            self.http_scan_button.config(state=tk.DISABLED)
        if self.reddit_grab_button is not None:
            self.reddit_grab_button.config(state=tk.DISABLED)
    elif new_state == "error":
        self._set_button_to_error()
        if self.ftp_scan_button is not None:
            self.ftp_scan_button.config(state=tk.DISABLED)
        if self.http_scan_button is not None:
            self.http_scan_button.config(state=tk.DISABLED)
        if self.reddit_grab_button is not None:
            self.reddit_grab_button.config(state=tk.DISABLED)

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
    dashboard_scan.check_external_scans(self)

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
    dashboard_scan.stop_scan_immediate(self)

def _stop_scan_after_host(self) -> None:
    """Stop scan after current host completes."""
    dashboard_scan.stop_scan_after_host(self)

def _handle_stop_error(self, error_message: str) -> None:
    """Handle scan stop error."""
    dashboard_scan.handle_stop_error(self, error_message)


def bind_scan_control_methods(widget_cls) -> None:
    """Attach extracted scan-control methods to DashboardWidget."""
    method_names = (
        "_handle_scan_button_click",
        "_handle_ftp_scan_button_click",
        "_handle_http_scan_button_click",
        "_handle_reddit_grab_button_click",
        "_handle_reddit_grab_start",
        "_reddit_grab_worker",
        "_on_reddit_grab_done",
        "_maybe_warn_mock_mode_persistence",
        "_start_ftp_scan",
        "_start_http_scan",
        "_update_scan_button_state",
        "_set_button_to_start",
        "_set_button_to_stop",
        "_set_button_to_disabled",
        "_set_button_to_stopping",
        "_set_button_to_retry",
        "_set_button_to_error",
        "_check_external_scans",
        "_validate_external_process",
        "_show_stop_confirmation",
        "_handle_stop_choice",
        "_stop_scan_immediate",
        "_stop_scan_after_host",
        "_handle_stop_error",
    )
    for name in method_names:
        setattr(widget_cls, name, globals()[name])
