"""
SMBSeek Mission Control Dashboard

Implements the main dashboard with all critical information in a single view.
Provides key metrics cards, progress display, top findings, and summary breakdowns
with drill-down capabilities to detailed windows.

Design Decision: Single-panel "mission control" layout provides situation awareness
while drill-down buttons allow detailed exploration without losing overview context.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser
import tkinter.font as tkfont
import threading
import time
import json
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime
import sys
import os
import queue
from collections import deque
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from gui.utils.database_access import DatabaseReader
from gui.utils.backend_interface import BackendInterface
from gui.utils.style import get_theme, apply_theme_to_window
from gui.utils.scan_manager import get_scan_manager
from gui.components.scan_dialog import show_scan_dialog
from gui.components.ftp_scan_dialog import show_ftp_scan_dialog
from gui.components.scan_results_dialog import show_scan_results_dialog
from gui.utils.settings_manager import SettingsManager
from gui.utils.probe_runner import run_probe
from gui.utils.extract_runner import run_extract
from gui.components import dashboard_logs
from gui.utils import (
    probe_cache,
    probe_patterns,
    probe_runner,
    extract_runner,
    ftp_probe_runner,
    ftp_probe_cache,
)
from gui.utils.logging_config import get_logger
from shared.quarantine import create_quarantine_dir

_logger = get_logger("dashboard")


class DashboardWidget:
    """
    Main dashboard displaying key SMBSeek metrics and status.
    
    Implements mission control pattern with:
    - Key metrics cards (clickable for drill-down)
    - Real-time scan progress display
    - Top security findings summary
    - Country and activity breakdowns
    - Quick scan interface
    
    Design Pattern: Single view with progressive disclosure through drill-down
    windows activated by clicking on metric cards and summary sections.
    """
    
    def __init__(self, parent: tk.Widget, db_reader: DatabaseReader, 
                 backend_interface: BackendInterface, config_path: str = None):
        """
        Initialize dashboard widget.
        
        Args:
            parent: Parent tkinter widget
            db_reader: Database access instance
            backend_interface: Backend communication interface
            config_path: Path to SMBSeek configuration file (optional)
            
        Design Decision: Dependency injection allows easy testing with mock
        objects and clear separation of concerns.
        """
        self.parent = parent
        self.db_reader = db_reader
        self.backend_interface = backend_interface
        self.theme = get_theme()
        
        # Dashboard state
        self.current_scan = None
        self.current_scan_options = None  # Store options for post-scan batch operations
        self.last_update = None
        
        # Scan management
        self.scan_manager = get_scan_manager()
        self.config_path = config_path
        self.settings_manager = SettingsManager()
        self.ransomware_indicators: List[str] = []
        self.indicator_patterns = []
        self._mock_mode_notice_shown = False

        # UI components
        self.main_frame = None
        self.body_canvas = None
        self.body_scrollbar = None
        self.body_frame = None
        self.body_canvas_window = None
        self.progress_frame = None
        self.metrics_frame = None
        self.scan_button = None
        self.status_bar = None
        self.update_time_label = None
        self.status_message = None
        
        # Progress tracking
        self.current_progress_summary = ""
        self.status_text = tk.StringVar(value="Loading dashboard summary...")
        self._status_static_mode = True  # Keep status label static post-initialization
        self._status_summary_initialized = False

        # Live log viewer state
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.log_history = deque(maxlen=500)
        self.log_text_widget: Optional[tk.Text] = None
        self.log_autoscroll = True
        self._log_placeholder_visible = True
        self.log_processing_job = None
        self.log_jump_button = None
        self.log_bg_color = self.theme.colors.get("log_bg", "#111418")
        self.log_fg_color = self.theme.colors.get("log_fg", "#f5f5f5")
        self.log_placeholder_color = self.theme.colors.get("log_placeholder", "#9ea4b3")

        # ANSI parsing helpers for preserving backend colors
        self._ansi_pattern = re.compile(r"\x1b\[([\d;]*)m")
        self._ansi_color_tag_map = {
            "30": "ansi_fg_black",
            "31": "ansi_fg_red",
            "32": "ansi_fg_green",
            "33": "ansi_fg_yellow",
            "34": "ansi_fg_blue",
            "35": "ansi_fg_magenta",
            "36": "ansi_fg_cyan",
            "37": "ansi_fg_white",
            "90": "ansi_fg_bright_black",
            "91": "ansi_fg_bright_red",
            "92": "ansi_fg_bright_green",
            "93": "ansi_fg_bright_yellow",
            "94": "ansi_fg_bright_blue",
            "95": "ansi_fg_bright_magenta",
            "96": "ansi_fg_bright_cyan",
            "97": "ansi_fg_bright_white"
        }
        self._ansi_color_tags = set(self._ansi_color_tag_map.values())
        self.log_placeholder_text = "Scan output will appear here once a scan starts."
        
        # Scan button state management
        self.scan_button_state = "idle"  # idle, disabled_external, scanning, stopping, retry, error
        self.external_scan_pid = None
        self.stopping_started_time = None  # Timestamp when stop was initiated
        self.ftp_scan_button = None
        
        # Callbacks
        self.drill_down_callback = None
        self.config_editor_callback = None
        self.size_enforcement_callback = None
        
        # Load indicator patterns for post-scan probes
        self._load_indicator_patterns()

        self._build_dashboard()
        
        # Initial data load
        self._refresh_dashboard_data()
    
    def set_drill_down_callback(self, callback: Callable[[str, Dict], None]) -> None:
        """
        Set callback for opening drill-down windows.
        
        Args:
            callback: Function to call with (window_type, data) for drill-downs
        """
        self.drill_down_callback = callback
    
    def set_config_editor_callback(self, callback: Callable[[str], None]) -> None:
        """
        Set callback for opening configuration editor.
        
        Args:
            callback: Function to call with config file path
        """
        self.config_editor_callback = callback
    
    def set_size_enforcement_callback(self, callback: Callable[[], None]) -> None:
        """
        Set callback for enforcing window size after operations that might trigger auto-resize.
        
        Args:
            callback: Function to call to enforce intended window dimensions
        """
        self.size_enforcement_callback = callback
    
    def _build_dashboard(self) -> None:
        """
        Build the complete dashboard layout.

        Design Decision: Vertical layout with sections allows natural reading
        flow and responsive behavior on different screen sizes.

        Layout structure:
        - Header (fixed at top): title and action buttons
        - Body (scrollable): progress frame with log viewer
        - Status bar (fixed at bottom): external scan notifications
        """
        # Main container
        self.main_frame = tk.Frame(self.parent)
        self.theme.apply_to_widget(self.main_frame, "main_window")
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=5)

        # Header section (fixed at top)
        self._build_header_section()

        # Scrollable body area for progress/log content
        self._build_scrollable_body()

        # Status bar (fixed at bottom)
        self._build_status_bar()

    def _build_scrollable_body(self) -> None:
        """Build scrollable container for body content (log viewer)."""
        # Container frame for canvas and scrollbar
        body_container = tk.Frame(self.main_frame)
        self.theme.apply_to_widget(body_container, "main_window")
        body_container.pack(fill=tk.BOTH, expand=True)

        # Canvas for scrollable content
        self.body_canvas = tk.Canvas(
            body_container,
            highlightthickness=0,
            bg=self.theme.colors["primary_bg"]
        )
        self.body_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Scrollbar (only visible when content overflows)
        self.body_scrollbar = ttk.Scrollbar(
            body_container,
            orient=tk.VERTICAL,
            command=self.body_canvas.yview
        )
        self.body_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.body_canvas.configure(yscrollcommand=self._on_body_scroll)

        # Inner frame to hold body content
        self.body_frame = tk.Frame(self.body_canvas)
        self.theme.apply_to_widget(self.body_frame, "main_window")
        self.body_canvas_window = self.body_canvas.create_window(
            (0, 0),
            window=self.body_frame,
            anchor="nw"
        )

        # Update scroll region when content changes
        self.body_frame.bind("<Configure>", self._on_body_frame_configure)
        self.body_canvas.bind("<Configure>", self._on_canvas_configure)

        # Build progress section inside the scrollable body
        self._build_progress_section()

    def _on_body_frame_configure(self, event=None) -> None:
        """Update canvas scroll region when body content changes."""
        self.body_canvas.configure(scrollregion=self.body_canvas.bbox("all"))
        self._update_scrollbar_visibility()

    def _on_canvas_configure(self, event=None) -> None:
        """Expand body frame to fill canvas width."""
        if event:
            self.body_canvas.itemconfig(self.body_canvas_window, width=event.width)
        self._update_scrollbar_visibility()

    def _on_body_scroll(self, *args) -> None:
        """Handle scroll events and update scrollbar."""
        self.body_scrollbar.set(*args)
        self._update_scrollbar_visibility()

    def _update_scrollbar_visibility(self) -> None:
        """Show scrollbar only when content overflows."""
        try:
            # Check if content exceeds visible area
            bbox = self.body_canvas.bbox("all")
            if bbox:
                content_height = bbox[3] - bbox[1]
                canvas_height = self.body_canvas.winfo_height()
                if content_height > canvas_height and canvas_height > 1:
                    if not self.body_scrollbar.winfo_ismapped():
                        self.body_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
                else:
                    if self.body_scrollbar.winfo_ismapped():
                        self.body_scrollbar.pack_forget()
        except tk.TclError:
            pass  # Widget may not exist during shutdown
        
        # Initial scan state check and data load
        self._check_external_scans()
        self._refresh_dashboard_data()
        self._process_log_queue()
    
    def _build_header_section(self) -> None:
        """Build responsive two-line header with title and action buttons."""
        header_frame = tk.Frame(self.main_frame)
        self.theme.apply_to_widget(header_frame, "main_window")
        header_frame.pack(fill=tk.X, pady=(0, 10))

        # Line 1: Title only
        title_label = self.theme.create_styled_label(
            header_frame,
            "SMBSeek Security Toolkit",
            "title"
        )
        title_label.pack(anchor=tk.W, pady=(0, 5))

        # Line 2: Action buttons with natural sizing
        actions_frame = tk.Frame(header_frame)
        self.theme.apply_to_widget(actions_frame, "main_window")
        actions_frame.pack(anchor=tk.W)

        # Start Scan button (preserve state management)
        self.scan_button = tk.Button(
            actions_frame,
            text="🔍 Start SMB Scan",
            command=self._handle_scan_button_click
        )
        self.theme.apply_to_widget(self.scan_button, "button_primary")
        self.scan_button.pack(side=tk.LEFT, padx=(0, 5))

        # FTP Scan button (placeholder; wired in Card 2+)
        self.ftp_scan_button = tk.Button(
            actions_frame,
            text="📡 Start FTP Scan",
            command=self._handle_ftp_scan_button_click
        )
        self.theme.apply_to_widget(self.ftp_scan_button, "button_primary")
        self.ftp_scan_button.pack(side=tk.LEFT, padx=(0, 5))

        # Unified servers browser (SMB + FTP rows)
        servers_button = tk.Button(
            actions_frame,
            text="📋 Servers",
            command=lambda: self._open_drill_down("server_list")
        )
        self.theme.apply_to_widget(servers_button, "button_secondary")
        servers_button.pack(side=tk.LEFT, padx=(0, 5))

        # DB Tools button
        db_tools_button = tk.Button(
            actions_frame,
            text="\U0001F5C4 DB Tools",  # File cabinet emoji
            command=self._open_db_tools
        )
        self.theme.apply_to_widget(db_tools_button, "button_secondary")
        db_tools_button.pack(side=tk.LEFT, padx=(0, 5))

        # Config button (existing functionality)
        config_button = tk.Button(
            actions_frame,
            text="⚙ Config",
            command=self._open_config_editor
        )
        self.theme.apply_to_widget(config_button, "button_secondary")
        config_button.pack(side=tk.LEFT)

        # About button
        about_button = tk.Button(
            actions_frame,
            text="❔ About",
            command=self._open_about_dialog
        )
        self.theme.apply_to_widget(about_button, "button_secondary")
        about_button.pack(side=tk.LEFT, padx=(8, 0))
        
    
    def _build_progress_section(self) -> None:
        """Build persistent progress display that's always visible."""
        self.progress_frame = tk.Frame(self.body_frame)
        self.theme.apply_to_widget(self.progress_frame, "card")
        self.progress_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))

        self._build_log_viewer()
        self._build_status_footer()

    def _configure_log_tags(self) -> None:
        dashboard_logs.configure_log_tags(self)

    def _render_log_placeholder(self) -> None:
        dashboard_logs.render_log_placeholder(self)

    def _reset_log_output(self, country: Optional[str]) -> None:
        dashboard_logs.reset_log_output(self, country)

    def _append_log_line(self, line: str) -> None:
        dashboard_logs.append_log_line(self, line)

    def _parse_ansi_segments(self, text: str) -> List[tuple]:
        return dashboard_logs.parse_ansi_segments(self, text)

    def _apply_ansi_codes(self, active_tags: List[str], codes: List[str]) -> List[str]:
        return dashboard_logs.apply_ansi_codes(self, active_tags, codes)

    def _handle_scan_log_line(self, line: str) -> None:
        dashboard_logs.handle_scan_log_line(self, line)

    def _process_log_queue(self) -> None:
        dashboard_logs.process_log_queue(self)

    def _update_log_autoscroll_state(self, *_args) -> None:
        dashboard_logs.update_log_autoscroll_state(self, *_args)

    def _is_log_at_bottom(self) -> bool:
        return dashboard_logs.is_log_at_bottom(self)

    def _scroll_log_to_latest(self) -> None:
        dashboard_logs.scroll_log_to_latest(self)

    def _show_log_jump_button(self) -> None:
        dashboard_logs.show_log_jump_button(self)

    def _hide_log_jump_button(self) -> None:
        dashboard_logs.hide_log_jump_button(self)

    def _copy_log_output(self) -> None:
        dashboard_logs.copy_log_output(self)

    def _clear_log_output(self) -> None:
        dashboard_logs.clear_log_output(self)

    def _build_log_viewer(self) -> None:
        dashboard_logs.build_log_viewer(self)
    
    def _build_status_footer(self) -> None:
        """Place status summary + clipboard controls below the console."""
        footer = tk.Frame(
            self.progress_frame,
            bg=self.theme.colors["card_bg"],
            highlightthickness=0
        )
        footer.pack(fill=tk.X, padx=10, pady=(0, 12))
        footer.columnconfigure(0, weight=1)
        footer.columnconfigure(1, weight=0)

        status_summary_label = tk.Label(
            footer,
            textvariable=self.status_text,
            anchor="w",
            justify="left",
            bg=self.theme.colors["card_bg"],
            fg=self.theme.colors["text_secondary"],
            font=self.theme.fonts["status"],
            wraplength=520
        )
        status_summary_label.grid(row=0, column=0, sticky="w")

        self.update_time_label = tk.Label(
            footer,
            text="",
            anchor="w",
            bg=self.theme.colors["card_bg"],
            fg=self.theme.colors["text_secondary"],
            font=self.theme.fonts["status"]
        )
        self.update_time_label.grid(row=1, column=0, sticky="w", pady=(4, 0))

        button_frame = tk.Frame(
            footer,
            bg=self.theme.colors["card_bg"]
        )
        button_frame.grid(row=0, column=1, rowspan=2, sticky="se", padx=(10, 0))

        copy_button = tk.Button(
            button_frame,
            text="Copy All",
            command=self._copy_log_output
        )
        self.theme.apply_to_widget(copy_button, "button_secondary")
        copy_button.pack(side=tk.LEFT, padx=(0, 5))

        clear_button = tk.Button(
            button_frame,
            text="Clear",
            command=self._clear_log_output
        )
        self.theme.apply_to_widget(clear_button, "button_secondary")
        clear_button.pack(side=tk.LEFT)
    
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
        
        # Show scan dialog
        result = show_scan_dialog(
            parent=self.parent,
            config_path=self.config_path,
            config_editor_callback=self._open_config_editor_from_scan,
            scan_start_callback=self._start_new_scan,
            backend_interface=self.backend_interface,
            settings_manager=getattr(self, 'settings_manager', None)
        )
    
    def _open_config_editor_from_scan(self, config_path: str) -> None:
        """Open configuration editor from scan dialog."""
        if self.config_editor_callback:
            self.config_editor_callback(config_path)
    
    def _start_new_scan(self, scan_options: dict) -> None:
        """Start new scan with specified options."""
        try:
            # Final check for external scans before starting
            self._check_external_scans()
            if self.scan_button_state != "idle":
                return  # External scan detected, don't proceed

            # Store scan options for post-scan batch operations
            self.current_scan_options = scan_options

            # Get backend path for external SMBSeek installation
            backend_path = getattr(self.backend_interface, "backend_path", "./smbseek")
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
            else:
                # Get more specific error information
                error_details = []
                
                # Check if backend path exists
                if not os.path.exists(backend_path):
                    error_details.append(f"• Backend path not found: {backend_path}")
                
                # Check if SMBSeek executable exists
                smbseek_cli = os.path.join(backend_path, "smbseek")
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
                            self._run_post_scan_batch_operations(self.current_scan_options, results)
                        else:
                            # Show scan summary immediately when no bulk ops or no data
                            self._show_scan_results(results)
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

    def _run_post_scan_batch_operations(self, scan_options: Dict[str, Any], scan_results: Dict[str, Any]) -> None:
        """Run bulk probe/extract operations after scan completion.

        Called when:
        - Scan completes (not cancelled)
        - Scan has success=True OR status in ('completed', 'success', 'failed')
        - current_scan_options is set
        - At least one bulk operation (probe/extract) is enabled

        Will show info dialog if no accessible servers are found.
        """
        try:
            # Check if any bulk operations are enabled
            bulk_probe_enabled = scan_options.get('bulk_probe_enabled', False)
            bulk_extract_enabled = scan_options.get('bulk_extract_enabled', False)

            if not (bulk_probe_enabled or bulk_extract_enabled):
                self._show_scan_results(scan_results)
                try:
                    self.parent.after(5000, self._reset_scan_status)
                except tk.TclError:
                    pass
                return  # No bulk operations requested

            # Skip bulk if scan failed or produced no hosts (use tolerant metrics)
            host_metric = max(
                scan_results.get("hosts_scanned", 0) or scan_results.get("hosts_tested", 0) or scan_results.get("hosts_discovered", 0) or 0,
                scan_results.get("accessible_hosts", 0) or 0,
                scan_results.get("shares_found", 0) or 0,
            )
            if scan_results.get("error") or host_metric == 0:
                self._show_scan_results(scan_results)
                try:
                    self.parent.after(5000, self._reset_scan_status)
                except tk.TclError:
                    pass
                return

            # Query database for servers with successful authentication (keep UI responsive)
            scan_protocol = str(scan_results.get("protocol") or "").lower()
            host_type_filter = "F" if scan_protocol == "ftp" else "S"

            def _fetch_servers():
                return self._get_servers_for_bulk_ops(
                    skip_indicator_extract=scan_options.get("bulk_extract_skip_indicators", True),
                    host_type_filter=host_type_filter,
                )

            servers_for_ops, fetch_error = self._run_background_fetch(
                title="Preparing Bulk Operations",
                message="Gathering servers with successful authentication...",
                fetch_fn=_fetch_servers
            )

            if fetch_error:
                messagebox.showerror(
                    "Bulk Operations Error",
                    f"Failed to gather servers for bulk operations:\n{fetch_error}"
                )
                self._show_scan_results(scan_results)
                try:
                    self.parent.after(5000, self._reset_scan_status)
                except tk.TclError:
                    pass
                return

            probe_targets = servers_for_ops.get("probe") if isinstance(servers_for_ops, dict) else []
            extract_targets = servers_for_ops.get("extract") if isinstance(servers_for_ops, dict) else []

            if not probe_targets and not extract_targets and (bulk_probe_enabled or bulk_extract_enabled):
                # Show info message only if bulk operations were enabled
                messagebox.showinfo(
                    "Bulk Operations Skipped",
                    "No servers with successful authentication found.\n\n"
                    "Bulk probe/extract operations require at least one accessible server."
                )
                self._show_scan_results(scan_results)
                try:
                    self.parent.after(5000, self._reset_scan_status)
                except tk.TclError:
                    pass
                return

            # Run batch operations (record summaries per op, show in LIFO order)
            summary_stack: List[Tuple[str, List[Dict[str, Any]]]] = []

            if bulk_probe_enabled:
                probe_results = self._execute_batch_probe(probe_targets)
                summary_stack.append(("probe", probe_results))

            if bulk_extract_enabled:
                if not extract_targets:
                    summary_stack.append(("extract", [{
                        "ip_address": "",
                        "action": "extract",
                        "status": "skipped",
                        "notes": "All accessible hosts were flagged with indicators; extract skipped."
                    }]))
                else:
                    extract_results = self._execute_batch_extract(extract_targets)
                    summary_stack.append(("extract", extract_results))

            # Present summaries in LIFO order
            while summary_stack:
                job_type, results = summary_stack.pop()
                if results:
                    self._show_batch_summary(results, job_type=job_type)

            # After bulk operations, show the deferred scan summary
            self._show_scan_results(scan_results)
            try:
                self.parent.after(5000, self._reset_scan_status)
            except tk.TclError:
                pass

        except Exception as e:
            messagebox.showerror(
                "Batch Operations Error",
                f"Error running post-scan batch operations: {str(e)}\n\n"
                f"The scan completed successfully but bulk operations encountered an error."
            )
            # Even on error, fall back to showing the scan summary
            try:
                self._show_scan_results(scan_results)
                self.parent.after(5000, self._reset_scan_status)
            except Exception:
                pass

    def _get_servers_for_bulk_ops(
        self,
        skip_indicator_extract: bool = True,
        host_type_filter: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Gather servers eligible for bulk probe and extract.

        Probe: accessible_shares > 0
        Extract: accessible_shares > 0 AND (no indicators) unless toggle disabled
        """
        result = {"probe": [], "extract": []}
        try:
            if not self.db_reader:
                return result

            if hasattr(self.db_reader, "get_protocol_server_list"):
                servers, _ = self.db_reader.get_protocol_server_list(
                    limit=5000,
                    offset=0,
                    country_filter=None,
                    recent_scan_only=True,
                )
            else:
                servers, _ = self.db_reader.get_server_list(
                    limit=5000,
                    offset=0,
                    country_filter=None,
                    recent_scan_only=True,
                )

            for server in servers:
                server_host_type = (server.get("host_type") or "S").upper()
                if host_type_filter and server_host_type != host_type_filter:
                    continue

                accessible = (server.get("accessible_shares") or 0) > 0
                if not accessible:
                    continue

                result["probe"].append(server)

                indicator_matches = int(server.get("indicator_matches", 0) or 0)
                probe_status = (server.get("probe_status") or "").lower()
                is_issue = probe_status == "issue" or indicator_matches > 0

                if skip_indicator_extract and is_issue:
                    continue

                result["extract"].append(server)

        except Exception as e:
            _logger.error("Error querying servers for bulk ops: %s", e)

        return result

    def _load_indicator_patterns(self) -> None:
        """Load ransomware indicator patterns from SMBSeek config."""
        config_path = self.config_path
        if not config_path and self.settings_manager:
            config_path = self.settings_manager.get_setting('backend.config_path', None)
            if not config_path:
                try:
                    config_path = self.settings_manager.get_smbseek_config_path()
                except Exception:
                    config_path = None
        self.ransomware_indicators = probe_patterns.load_ransomware_indicators(config_path)
        self.indicator_patterns = probe_patterns.compile_indicator_patterns(self.ransomware_indicators)

    def _run_background_fetch(self, title: str, message: str, fetch_fn: Callable[[], Any]) -> tuple[Any, Optional[str]]:
        """
        Run a blocking fetch function off the UI thread while showing a small modal.

        Returns:
            (result, error_message_or_None)
        """
        result_container = {"result": None, "error": None, "done": False}

        dialog = tk.Toplevel(self.parent)
        dialog.title(title)
        dialog.geometry("380x140")
        dialog.transient(self.parent)
        dialog.grab_set()
        self.theme.apply_to_widget(dialog, "main_window")

        label = tk.Label(dialog, text=message)
        label.pack(pady=(20, 10))

        progress = ttk.Progressbar(dialog, mode="indeterminate", length=260)
        progress.pack(pady=(0, 10))
        progress.start(10)

        dialog.update_idletasks()

        def worker():
            try:
                result_container["result"] = fetch_fn()
            except Exception as exc:  # pragma: no cover - best-effort guard
                result_container["error"] = str(exc)
            finally:
                result_container["done"] = True
                try:
                    dialog.after(0, dialog.destroy)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()
        self.parent.wait_window(dialog)
        return result_container["result"], result_container["error"]

    def _execute_batch_probe(self, servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute bulk probe operation on servers."""
        # Load probe settings
        worker_count = int(self.settings_manager.get_setting('probe.batch_max_workers', 3))
        worker_count = max(1, min(8, worker_count))
        max_dirs = int(self.settings_manager.get_setting('probe.max_directories_per_share', 3))
        max_files = int(self.settings_manager.get_setting('probe.max_files_per_directory', 5))
        timeout_seconds = int(self.settings_manager.get_setting('probe.share_timeout_seconds', 10))
        enable_rce = bool(self.settings_manager.get_setting('scan_dialog.rce_enabled', False))

        results: List[Dict[str, Any]] = []
        cancel_event = threading.Event()

        # Create progress dialog quickly, then hand work to background thread
        progress_dialog = tk.Toplevel(self.parent)
        progress_dialog.title("Bulk Probe Progress")
        progress_dialog.geometry("420x170")
        progress_dialog.transient(self.parent)
        progress_dialog.grab_set()
        self.theme.apply_to_widget(progress_dialog, "main_window")

        progress_label = tk.Label(progress_dialog, text=f"Probing 0/{len(servers)} servers...")
        progress_label.pack(pady=(18, 8))

        progress_bar = ttk.Progressbar(progress_dialog, length=320, mode='determinate', maximum=len(servers))
        progress_bar.pack(pady=(0, 10))

        cancel_button = tk.Button(progress_dialog, text="Cancel", command=lambda: cancel_event.set())
        cancel_button.pack(pady=(0, 10))

        # Ensure initial paint before heavy work
        progress_dialog.update_idletasks()

        # Shared state for UI updates from worker
        state = {
            "completed": 0,
            "total": len(servers),
            "results": results,
            "done": False,
            "error": None
        }

        def ui_tick():
            """Periodic UI refresher to keep dialog responsive."""
            try:
                progress_label.config(text=f"Probing {state['completed']}/{state['total']} servers...")
                progress_bar['value'] = state['completed']
                progress_dialog.update_idletasks()
            except tk.TclError:
                return  # Dialog closed

            if not state["done"]:
                progress_dialog.after(150, ui_tick)

        def worker():
            """Run probes off the UI thread and report completion order."""
            try:
                with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="probe-batch") as executor:
                    future_to_server = {
                        executor.submit(
                            self._probe_single_server,
                            server,
                            max_dirs,
                            max_files,
                            timeout_seconds,
                            enable_rce,
                            cancel_event
                        ): server for server in servers
                    }

                    # Consume futures as they complete to avoid head-of-line blocking
                    for future in as_completed(future_to_server):
                        server = future_to_server[future]
                        if cancel_event.is_set():
                            break
                        try:
                            result = future.result(timeout=timeout_seconds + 10)
                        except Exception as e:
                            result = {
                                "ip_address": server.get("ip_address"),
                                "action": "probe",
                                "status": "failed",
                                "notes": str(e)
                            }
                        results.append(result)
                        state["completed"] = len(results)
            except Exception as exc:
                state["error"] = str(exc)
            finally:
                state["done"] = True
                try:
                    progress_dialog.after(0, progress_dialog.destroy)
                except Exception:
                    pass

        # Start background worker and UI tick
        threading.Thread(target=worker, daemon=True).start()
        progress_dialog.after(150, ui_tick)

        # Block until dialog destroyed (worker sets done then destroys dialog)
        self.parent.wait_window(progress_dialog)
        return results

    def _probe_single_server(self, server: Dict[str, Any], max_dirs: int, max_files: int,
                              timeout_seconds: int, enable_rce: bool, cancel_event: threading.Event) -> Dict[str, Any]:
        """Probe a single server (SMB or FTP)."""
        if cancel_event.is_set():
            return {
                "ip_address": server.get("ip_address"),
                "action": "probe",
                "status": "cancelled",
                "notes": "Cancelled"
            }

        ip_address = server.get("ip_address")
        host_type = (server.get("host_type") or "S").upper()

        # FTP probe path
        if host_type == "F":
            try:
                port = int(server.get("port") or 21)
            except Exception:
                port = 21

            max_entries = max(1, int(max_dirs) * int(max_files))
            try:
                snapshot = ftp_probe_runner.run_ftp_probe(
                    ip_address,
                    port=port,
                    max_entries=max_entries,
                    cancel_event=cancel_event,
                )
                analysis = probe_patterns.attach_indicator_analysis(snapshot, self.indicator_patterns)
                issue_detected = bool(analysis.get("is_suspicious"))
                status = "issue" if issue_detected else "clean"

                shares = snapshot.get("shares", [])
                first_share = shares[0] if shares else {}
                dir_names = [
                    d.get("name")
                    for d in first_share.get("directories", [])
                    if isinstance(d, dict) and d.get("name")
                ]
                accessible_dirs_count = len(dir_names)
                accessible_dirs_list = ",".join(dir_names)
                snapshot_path = str(ftp_probe_cache.get_ftp_cache_path(ip_address))

                try:
                    if self.db_reader:
                        self.db_reader.upsert_probe_cache_for_host(
                            ip_address,
                            "F",
                            status=status,
                            indicator_matches=len(analysis.get("matches", [])),
                            snapshot_path=snapshot_path,
                            accessible_dirs_count=accessible_dirs_count,
                            accessible_dirs_list=accessible_dirs_list,
                        )
                except Exception:
                    pass

                notes: List[str] = [f"{accessible_dirs_count} directorie(s)"]
                if issue_detected:
                    notes.append("Indicators detected")

                return {
                    "ip_address": ip_address,
                    "action": "probe",
                    "status": "success",
                    "notes": ", ".join(notes),
                }
            except Exception as e:
                status = "cancelled" if "cancel" in str(e).lower() else "failed"
                return {
                    "ip_address": ip_address,
                    "action": "probe",
                    "status": status,
                    "notes": str(e)
                }

        # SMB probe path
        raw_shares = server.get("accessible_shares_list") or server.get("accessible_shares") or ""
        shares = [s.strip() for s in str(raw_shares).split(",") if s.strip()]

        # Derive credentials from auth method
        auth_method = server.get("auth_method", "")
        username = "" if "anonymous" in auth_method.lower() else "guest"
        password = ""

        try:
            result = probe_runner.run_probe(
                ip_address,
                shares,
                max_directories=max_dirs,
                max_files=max_files,
                timeout_seconds=timeout_seconds,
                username=username,
                password=password,
                enable_rce_analysis=enable_rce,
                cancel_event=cancel_event,
                allow_empty=True,
                db_accessor=self.db_reader,
            )
            # Persist probe snapshot to disk and DB (align with server list workflow)
            probe_cache.save_probe_result(ip_address, result)
            snapshot_path = None
            try:
                if hasattr(probe_cache, "get_probe_result_path"):
                    snapshot_path = probe_cache.get_probe_result_path(ip_address)
            except Exception:
                snapshot_path = None

            # Attach ransomware indicator analysis (mirror server list behavior)
            analysis = probe_patterns.attach_indicator_analysis(result, self.indicator_patterns)
            issue_detected = bool(analysis.get("is_suspicious"))

            try:
                if self.db_reader:
                    self.db_reader.upsert_probe_cache_for_host(
                        ip_address,
                        "S",
                        status="issue" if issue_detected else "clean",
                        indicator_matches=len(analysis.get("matches", [])),
                        snapshot_path=snapshot_path
                    )
            except Exception:
                pass

            return {
                "ip_address": ip_address,
                "action": "probe",
                "status": "success",
                "notes": self._build_probe_notes(len(shares), enable_rce, issue_detected, analysis, result)
            }
        except Exception as e:
            status = "cancelled" if "cancel" in str(e).lower() else "failed"
            return {
                "ip_address": ip_address,
                "action": "probe",
                "status": status,
                "notes": str(e)
            }

    def _build_probe_notes(self, share_count: int, enable_rce: bool, issue_detected: bool, analysis: Dict[str, Any], result: Dict[str, Any]) -> str:
        notes: List[str] = []
        if share_count:
            notes.append(f"{share_count} share(s)")
        else:
            notes.append("No accessible shares")

        if enable_rce and result.get("rce_analysis"):
            rce_status = result["rce_analysis"].get("rce_status", "not_run")
            notes.append(f"RCE: {rce_status}")
            try:
                self._handle_rce_status_update(result.get("ip_address") or "", rce_status)
            except Exception:
                pass

        if issue_detected:
            match_count = len(analysis.get("matches", [])) if isinstance(analysis, dict) else 0
            notes.append(f"Indicators detected ({match_count})" if match_count else "Indicators detected")

        return ", ".join(notes) if notes else "Probed"

    def _execute_batch_extract(self, servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute bulk extract operation on servers."""
        # Load extract settings
        worker_count = int(self.settings_manager.get_setting('extract.batch_max_workers', 2))
        worker_count = max(1, min(8, worker_count))
        max_file_mb = int(self.settings_manager.get_setting('extract.max_file_size_mb', 50))
        max_total_mb = int(self.settings_manager.get_setting('extract.max_total_size_mb', 200))
        max_time = int(self.settings_manager.get_setting('extract.max_time_seconds', 300))
        max_files = int(self.settings_manager.get_setting('extract.max_files_per_target', 10))
        extension_mode = str(self.settings_manager.get_setting('extract.extension_mode', 'allow_only')).lower()

        # Load extension filters from config if available
        included_extensions: List[str] = []
        excluded_extensions: List[str] = []
        config_path = self.settings_manager.get_setting('backend.config_path', None) if self.settings_manager else None
        if config_path and Path(config_path).exists():
            try:
                config_data = json.loads(Path(config_path).read_text(encoding="utf-8"))
                file_cfg = config_data.get("file_collection", {})
                included_extensions = file_cfg.get("included_extensions", []) or []
                excluded_extensions = file_cfg.get("excluded_extensions", []) or []
            except Exception:
                pass

        results = []
        cancel_event = threading.Event()

        # Create progress dialog
        progress_dialog = tk.Toplevel(self.parent)
        progress_dialog.title("Bulk Extract Progress")
        progress_dialog.geometry("400x150")
        progress_dialog.transient(self.parent)
        progress_dialog.grab_set()
        self.theme.apply_to_widget(progress_dialog, "main_window")

        progress_label = tk.Label(progress_dialog, text=f"Extracting from 0/{len(servers)} servers...")
        progress_label.pack(pady=20)

        progress_bar = ttk.Progressbar(progress_dialog, length=300, mode='determinate', maximum=len(servers))
        progress_bar.pack(pady=10)

        cancel_button = tk.Button(progress_dialog, text="Cancel", command=lambda: cancel_event.set())
        cancel_button.pack(pady=10)

        def update_progress(completed_count):
            progress_label.config(text=f"Extracting from {completed_count}/{len(servers)} servers...")
            progress_bar['value'] = completed_count
            progress_dialog.update()

        # Run extract operations with ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="extract-batch") as executor:
            futures = []
            for server in servers:
                future = executor.submit(
                    self._extract_single_server,
                    server,
                    max_file_mb,
                    max_total_mb,
                    max_time,
                    max_files,
                    extension_mode,
                    included_extensions,
                    excluded_extensions,
                    cancel_event
                )
                futures.append((server, future))

            for server, future in futures:
                if cancel_event.is_set():
                    break
                try:
                    result = future.result(timeout=max_time + 30)
                    results.append(result)
                except Exception as e:
                    results.append({
                        "ip_address": server.get("ip_address"),
                        "action": "extract",
                        "status": "failed",
                        "notes": str(e)
                    })
                update_progress(len(results))

        progress_dialog.destroy()
        return results

    def _extract_single_server(self, server: Dict[str, Any], max_file_mb: int, max_total_mb: int,
                                 max_time: int, max_files: int, extension_mode: str,
                                 included_extensions: List[str], excluded_extensions: List[str],
                                 cancel_event: threading.Event) -> Dict[str, Any]:
        """Extract files from a single server."""
        if cancel_event.is_set():
            return {
                "ip_address": server.get("ip_address"),
                "action": "extract",
                "status": "cancelled",
                "notes": "Cancelled"
            }

        ip_address = server.get("ip_address")
        raw_shares = server.get("accessible_shares_list") or server.get("accessible_shares") or ""
        shares = [s.strip() for s in str(raw_shares).split(",") if s.strip()]

        if not shares:
            return {
                "ip_address": ip_address,
                "action": "extract",
                "status": "skipped",
                "notes": "No accessible shares"
            }

        # Create quarantine directory
        try:
            quarantine_dir = create_quarantine_dir(ip_address, purpose="post-scan-extract")
        except Exception as e:
            return {
                "ip_address": ip_address,
                "action": "extract",
                "status": "failed",
                "notes": f"Quarantine error: {e}"
            }

        # Derive credentials
        auth_method = server.get("auth_method", "")
        username = "" if "anonymous" in auth_method.lower() else "guest"
        password = ""

        try:
            summary = extract_runner.run_extract(
                ip_address,
                shares,
                download_dir=quarantine_dir,
                username=username,
                password=password,
                max_total_bytes=max_total_mb * 1024 * 1024,
                max_file_bytes=max_file_mb * 1024 * 1024,
                max_file_count=max_files,
                max_seconds=max_time,
                max_depth=3,
                allowed_extensions=included_extensions,
                denied_extensions=excluded_extensions,
                delay_seconds=0,
                connection_timeout=30,
                extension_mode=extension_mode,
                progress_callback=None,
                cancel_event=cancel_event
            )

            files = summary["totals"].get("files_downloaded", 0)
            bytes_downloaded = summary["totals"].get("bytes_downloaded", 0)
            size_mb = bytes_downloaded / (1024 * 1024) if bytes_downloaded else 0

            # Mark host as extracted (one-way flag)
            try:
                if self.db_reader:
                    self.db_reader.upsert_extracted_flag(ip_address, True)
            except Exception:
                pass

            return {
                "ip_address": ip_address,
                "action": "extract",
                "status": "success",
                "notes": f"{files} file(s), {size_mb:.1f} MB"
            }
        except Exception as e:
            status = "cancelled" if "cancel" in str(e).lower() else "failed"
            return {
                "ip_address": ip_address,
                "action": "extract",
                "status": status,
                "notes": str(e)
            }

    def _show_batch_summary(self, results: List[Dict[str, Any]], job_type: Optional[str] = None) -> None:
        """Show summary dialog for batch operations."""
        dialog = tk.Toplevel(self.parent)
        title = f"{(job_type or 'Batch').title()} Operations Summary"
        dialog.title(title)
        dialog.geometry("700x515")
        dialog.transient(self.parent)
        self.theme.apply_to_widget(dialog, "main_window")
        dialog.grab_set()

        tk.Label(dialog, text=f"{title} Complete", font=("TkDefaultFont", 12, "bold")).pack(pady=10)

        # Create treeview for results
        tree_frame = tk.Frame(dialog)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        tree = ttk.Treeview(tree_frame, columns=("IP", "Action", "Status", "Notes"), show="headings", height=15)
        tree.heading("IP", text="IP Address")
        tree.heading("Action", text="Operation")
        tree.heading("Status", text="Status")
        tree.heading("Notes", text="Notes")

        tree.column("IP", width=120)
        tree.column("Action", width=80)
        tree.column("Status", width=80)
        tree.column("Notes", width=380)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Populate results
        success_count = 0
        failed_count = 0
        for result in results:
            status = result.get("status", "unknown")
            if status == "success":
                success_count += 1
            elif status in ("failed", "error"):
                failed_count += 1

            tree.insert("", tk.END, values=(
                result.get("ip_address", ""),
                result.get("action", ""),
                status,
                result.get("notes", "")
            ))

        # Summary stats
        stats_label = tk.Label(
            dialog,
            text=f"Total: {len(results)} | Success: {success_count} | Failed: {failed_count}",
            font=("TkDefaultFont", 10)
        )
        stats_label.pack(pady=5)

        # Close button
        close_button = tk.Button(dialog, text="Close", command=dialog.destroy)
        close_button.pack(pady=10)

        # Block until closed to preserve display sequencing
        self.parent.wait_window(dialog)

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
    
    def _open_config_editor(self) -> None:
        """Open application configuration dialog."""
        if self.drill_down_callback:
            self.drill_down_callback("app_config", {})

    def _open_db_tools(self) -> None:
        """Open database tools dialog."""
        from gui.components.db_tools_dialog import show_db_tools_dialog

        if not self.db_reader:
            messagebox.showerror(
                "Database Not Found",
                "No database is currently loaded."
            )
            return

        db_path = str(self.db_reader.db_path)

        show_db_tools_dialog(
            parent=self.parent,
            db_path=db_path,
            on_database_changed=self._refresh_after_db_tools
        )

    def _refresh_after_db_tools(self) -> None:
        """Refresh dashboard after DB tools operation."""
        try:
            if self.db_reader:
                self.db_reader.clear_cache()
            self._refresh_dashboard_data()
        except Exception as e:
            _logger.warning("Dashboard refresh error after DB tools: %s", e)

    def _open_about_dialog(self) -> None:
        dialog = tk.Toplevel(self.parent)
        dialog.title("About SMBSeek")
        dialog.transient(self.parent)
        dialog.grab_set()
        if self.theme:
            apply_theme_to_window(dialog)

        body = tk.Frame(dialog)
        body.pack(padx=18, pady=16, fill=tk.BOTH, expand=True)

        title = tk.Label(body, text="SMBSeek", font=(None, 14, "bold"))
        title.pack(anchor="w")

        blurb = (
            "SMBSeek helps defensive analysts find SMB servers with weak \n"
            "authentication and demonstrate impact via safe, guided workflows.\n"
            "No warranty expressed or implied; use at your own risk."
        )
        tk.Label(body, text=blurb, justify="left", anchor="w").pack(anchor="w", pady=(6, 10))

        link = tk.Label(body, text="GitHub: https://github.com/b3p3k0/smbseek", fg="#0066cc", cursor="hand2")
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/b3p3k0/smbseek"))

        btn_frame = tk.Frame(body)
        btn_frame.pack(fill=tk.X, pady=(12, 0))
        tk.Button(btn_frame, text="Close", command=dialog.destroy).pack(side=tk.RIGHT)

        dialog.update_idletasks()
        dialog.lift()
        dialog.focus_set()
    
    
    def _open_drill_down(self, window_type: str) -> None:
        """
        Open drill-down window.

        Args:
            window_type: Type of drill-down window to open
        """
        if self.drill_down_callback:
            self.drill_down_callback(window_type, {})
    
    def enable_mock_mode(self) -> None:
        """Enable mock mode for testing."""
        self.db_reader.enable_mock_mode()
        self.backend_interface.enable_mock_mode()
        self._refresh_dashboard_data()
    
    def disable_mock_mode(self) -> None:
        """Disable mock mode."""
        self.db_reader.disable_mock_mode()
        self.backend_interface.disable_mock_mode()
        self._refresh_dashboard_data()
    
    # ===== SCAN BUTTON STATE MANAGEMENT =====
    
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

    def _start_ftp_scan(self, scan_options: dict) -> None:
        """Start FTP scan with options from dialog. Mirrors _start_new_scan()."""
        # Final race-condition check before acquiring scan lock.
        self._check_external_scans()
        if self.scan_button_state != "idle":
            return

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
        else:
            messagebox.showerror(
                "FTP Scan Error",
                "Could not start FTP scan.\n"
                "A scan may already be running.",
                parent=self.parent,
            )

    def _update_scan_button_state(self, new_state: str) -> None:
        """Update scan button state and appearance."""
        self.scan_button_state = new_state
        
        if new_state == "idle":
            self._set_button_to_start()
            self._hide_status_bar()
            self.stopping_started_time = None  # Clear stop timeout tracking
            if self.ftp_scan_button is not None:
                self.ftp_scan_button.config(state=tk.NORMAL)
        elif new_state == "disabled_external":
            self._set_button_to_disabled()
            self._show_status_bar(f"Scan running by PID: {self.external_scan_pid} - Please wait")
            if self.ftp_scan_button is not None:
                self.ftp_scan_button.config(state=tk.DISABLED)
        elif new_state == "scanning":
            self._set_button_to_stop()
            self._hide_status_bar()
            if self.ftp_scan_button is not None:
                self.ftp_scan_button.config(state=tk.DISABLED)
        elif new_state == "stopping":
            self._set_button_to_stopping()
            if self.ftp_scan_button is not None:
                self.ftp_scan_button.config(state=tk.DISABLED)
        elif new_state == "retry":
            self._set_button_to_retry()
            if self.ftp_scan_button is not None:
                self.ftp_scan_button.config(state=tk.DISABLED)
        elif new_state == "error":
            self._set_button_to_error()
            if self.ftp_scan_button is not None:
                self.ftp_scan_button.config(state=tk.DISABLED)
    
    def _set_button_to_start(self) -> None:
        """Configure button for start state."""
        self.scan_button.config(
            text="🔍 Start SMB Scan",
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
        dialog.grab_set()
        
        # Center the dialog
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")
        
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
