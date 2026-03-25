"""
Dirracuda Mission Control Dashboard

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
from gui.components.unified_scan_dialog import show_unified_scan_dialog
from gui.components.ftp_scan_dialog import show_ftp_scan_dialog
from gui.components.http_scan_dialog import show_http_scan_dialog
from gui.components.scan_results_dialog import show_scan_results_dialog
from gui.components.batch_summary_dialog import show_batch_summary_dialog
from gui.utils.settings_manager import get_settings_manager
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.components import dashboard_logs
from gui.utils import (
    probe_cache,
    probe_patterns,
    extract_runner,
)
from gui.utils.probe_cache_dispatch import get_probe_snapshot_path_for_host, dispatch_probe_run
from gui.utils.probe_snapshot_summary import summarize_probe_snapshot
from gui.utils.logging_config import get_logger
from shared.quarantine import create_quarantine_dir

_logger = get_logger("dashboard")


class DashboardWidget:
    """
    Main dashboard displaying key Dirracuda metrics and status.
    
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
        self.settings_manager = get_settings_manager()
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
        self.servers_button = None
        self.db_tools_button = None
        self.config_button = None
        self.about_button = None
        self.theme_toggle_button = None
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
        self.copy_log_button = None
        self.clear_log_button = None
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
        self.http_scan_button = None
        self._queued_scan_active = False
        self._queued_scan_protocols: List[str] = []
        self._queued_scan_common_options: Optional[Dict[str, Any]] = None
        self._queued_scan_current_protocol: Optional[str] = None
        self._queued_scan_failures: List[Dict[str, str]] = []

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
            "Dirracuda",
            "title"
        )
        title_label.pack(anchor=tk.W, pady=(0, 5))

        # Line 2: Action buttons with natural sizing
        actions_frame = tk.Frame(header_frame)
        self.theme.apply_to_widget(actions_frame, "main_window")
        actions_frame.pack(fill=tk.X)

        left_actions = tk.Frame(actions_frame)
        self.theme.apply_to_widget(left_actions, "main_window")
        left_actions.pack(side=tk.LEFT, anchor=tk.W)

        right_actions = tk.Frame(actions_frame)
        self.theme.apply_to_widget(right_actions, "main_window")
        right_actions.pack(side=tk.RIGHT, anchor=tk.E)

        # Start Scan button (preserve state management)
        self.scan_button = tk.Button(
            left_actions,
            text="▶ Start Scan",
            command=self._handle_scan_button_click
        )
        self.theme.apply_to_widget(self.scan_button, "button_primary")
        self.scan_button.pack(side=tk.LEFT, padx=(0, 5))

        # Unified servers browser (SMB + FTP rows)
        self.servers_button = tk.Button(
            left_actions,
            text="📋 Servers",
            command=lambda: self._open_drill_down("server_list")
        )
        self.theme.apply_to_widget(self.servers_button, "button_secondary")
        self.servers_button.pack(side=tk.LEFT, padx=(0, 5))

        # DB Tools button
        self.db_tools_button = tk.Button(
            left_actions,
            text="\U0001F5C4 DB Tools",  # File cabinet emoji
            command=self._open_db_tools
        )
        self.theme.apply_to_widget(self.db_tools_button, "button_secondary")
        self.db_tools_button.pack(side=tk.LEFT, padx=(0, 5))

        # Config button (existing functionality)
        self.config_button = tk.Button(
            left_actions,
            text="⚙ Config",
            command=self._open_config_editor
        )
        self.theme.apply_to_widget(self.config_button, "button_secondary")
        self.config_button.pack(side=tk.LEFT)

        # About button
        self.about_button = tk.Button(
            left_actions,
            text="❔ About",
            command=self._open_about_dialog
        )
        self.theme.apply_to_widget(self.about_button, "button_secondary")
        self.about_button.pack(side=tk.LEFT, padx=(8, 0))

        # Theme toggle button (right-aligned)
        self.theme_toggle_button = tk.Button(
            right_actions,
            text=self._theme_toggle_button_text(),
            command=self._toggle_theme
        )
        self.theme.apply_to_widget(self.theme_toggle_button, "button_secondary")
        self.theme_toggle_button.pack(side=tk.RIGHT)

    def _theme_toggle_button_text(self) -> str:
        """Return dashboard button label for switching to the opposite theme."""
        return "☀️" if self.theme.get_mode() == "dark" else "🌙"

    def _refresh_theme_cached_colors(self) -> None:
        """Refresh dashboard-local color caches after a theme switch."""
        self.log_bg_color = self.theme.colors.get("log_bg", "#111418")
        self.log_fg_color = self.theme.colors.get("log_fg", "#f5f5f5")
        self.log_placeholder_color = self.theme.colors.get("log_placeholder", "#9ea4b3")

        for button in (
            getattr(self, "servers_button", None),
            getattr(self, "db_tools_button", None),
            getattr(self, "config_button", None),
            getattr(self, "about_button", None),
            getattr(self, "theme_toggle_button", None),
            getattr(self, "copy_log_button", None),
            getattr(self, "clear_log_button", None),
        ):
            if button and button.winfo_exists():
                self.theme.apply_to_widget(button, "button_secondary")

        if self.log_jump_button and self.log_jump_button.winfo_exists():
            self.theme.apply_to_widget(self.log_jump_button, "button_secondary")

        if self.log_text_widget and self.log_text_widget.winfo_exists():
            try:
                self.log_text_widget.configure(
                    bg=self.log_bg_color,
                    fg=self.log_fg_color,
                    insertbackground=self.log_fg_color,
                )
                self._configure_log_tags()
                if self._log_placeholder_visible:
                    self._render_log_placeholder()
            except tk.TclError:
                pass

    def _toggle_theme(self) -> None:
        """Toggle global light/dark mode and persist preference."""
        try:
            new_mode = self.theme.toggle_mode(root=self.parent)
            if self.settings_manager:
                self.settings_manager.set_setting("interface.theme", new_mode)

            self._refresh_theme_cached_colors()
            if self.theme_toggle_button and self.theme_toggle_button.winfo_exists():
                self.theme_toggle_button.configure(text=self._theme_toggle_button_text())

            # Re-apply scan button appearance in current state using updated palette.
            self._update_scan_button_state(self.scan_button_state)
        except Exception as exc:
            _logger.error("Failed to toggle theme: %s", exc)
            messagebox.showerror(
                "Theme Error",
                f"Failed to switch theme: {exc}",
                parent=self.parent,
            )
        
    
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

        self.copy_log_button = tk.Button(
            button_frame,
            text="Copy All",
            command=self._copy_log_output
        )
        self.theme.apply_to_widget(self.copy_log_button, "button_secondary")
        self.copy_log_button.pack(side=tk.LEFT, padx=(0, 5))

        self.clear_log_button = tk.Button(
            button_frame,
            text="Clear",
            command=self._clear_log_output
        )
        self.theme.apply_to_widget(self.clear_log_button, "button_secondary")
        self.clear_log_button.pack(side=tk.LEFT)
    
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
        )
    
    def _open_config_editor_from_scan(self, config_path: str) -> None:
        """Open configuration editor from scan dialog."""
        if self.config_editor_callback:
            self.config_editor_callback(config_path)

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

    def _abort_queued_scan_on_failure(
        self,
        protocol: str,
        reason: str,
        *,
        title: str = "Protocol Scan Failed",
    ) -> None:
        """Abort remaining queued protocol scans after a failure."""
        remaining = [p.upper() for p in self._queued_scan_protocols if p]
        skipped_text = ", ".join(remaining) if remaining else "None"

        self._queued_scan_failures.append({"protocol": protocol, "reason": reason})
        self._clear_queued_scan_state()
        messagebox.showwarning(
            title,
            f"{protocol.upper()} scan failed. Remaining queued scans were not started.\n\n"
            f"Reason: {reason}\n"
            f"Skipped protocols: {skipped_text}",
        )

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
            self._abort_queued_scan_on_failure(
                protocol,
                "failed to start",
                title="Protocol Start Failed",
            )
            return

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
            self._abort_queued_scan_on_failure(protocol, reason)
            return

        if self._queued_scan_protocols:
            try:
                self.parent.after(150, self._launch_next_queued_scan)
            except tk.TclError:
                pass
        else:
            self._launch_next_queued_scan()

    def _resolve_active_config_path(self) -> Optional[Path]:
        """Resolve the effective config.json path used for scan launches."""
        candidate = self.config_path
        if not candidate and self.settings_manager:
            candidate = self.settings_manager.get_setting('backend.config_path', None)
            if not candidate:
                try:
                    candidate = self.settings_manager.get_smbseek_config_path()
                except Exception:
                    candidate = None
        if not candidate:
            return None
        try:
            return Path(str(candidate)).expanduser().resolve()
        except Exception:
            return None

    def _read_shodan_api_key_from_config(self) -> str:
        """Return shodan.api_key from active config, or empty string when absent/unreadable."""
        config_path = self._resolve_active_config_path()
        if not config_path or not config_path.exists():
            return ""
        try:
            config_data = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(config_data, dict):
                return ""
            shodan_cfg = config_data.get("shodan", {})
            if not isinstance(shodan_cfg, dict):
                return ""
            return str(shodan_cfg.get("api_key", "") or "").strip()
        except Exception as exc:
            _logger.warning("Could not read Shodan API key from config: %s", exc)
            return ""

    def _persist_shodan_api_key_to_config(self, api_key: str) -> bool:
        """Write shodan.api_key into the active config file."""
        key = str(api_key or "").strip()
        if not key:
            return False

        config_path = self._resolve_active_config_path()
        if not config_path:
            return False

        try:
            config_data: Dict[str, Any] = {}
            if config_path.exists():
                config_data = json.loads(config_path.read_text(encoding="utf-8"))
                if not isinstance(config_data, dict):
                    config_data = {}

            shodan_cfg = config_data.get("shodan")
            if not isinstance(shodan_cfg, dict):
                shodan_cfg = {}
                config_data["shodan"] = shodan_cfg
            shodan_cfg["api_key"] = key

            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(config_data, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            return True
        except Exception as exc:
            _logger.error("Failed to persist Shodan API key to config: %s", exc)
            return False

    def _prompt_for_shodan_api_key(self) -> Optional[str]:
        """
        Prompt the user to enter a Shodan API key.

        Returns:
            Trimmed API key string when saved, or None when cancelled.
        """
        dialog = tk.Toplevel(self.parent)
        dialog.title("Shodan API Key Required")
        dialog.geometry("540x220")
        dialog.resizable(False, False)
        dialog.transient(self.parent)
        dialog.grab_set()
        self.theme.apply_to_widget(dialog, "main_window")

        container = tk.Frame(dialog)
        self.theme.apply_to_widget(container, "main_window")
        container.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)

        title_label = tk.Label(container, text="Shodan API Key Required", font=("TkDefaultFont", 11, "bold"))
        self.theme.apply_to_widget(title_label, "label")
        title_label.pack(anchor="w")

        helper = tk.Label(
            container,
            text="A Shodan API key is required to start discovery scans. Enter your key to continue.",
            justify="left",
            wraplength=500,
        )
        self.theme.apply_to_widget(helper, "label")
        helper.pack(anchor="w", pady=(8, 10))

        key_row = tk.Frame(container)
        self.theme.apply_to_widget(key_row, "main_window")
        key_row.pack(fill=tk.X)

        key_label = tk.Label(key_row, text="API Key:")
        self.theme.apply_to_widget(key_label, "label")
        key_label.pack(side=tk.LEFT, padx=(0, 8))

        key_var = tk.StringVar()
        key_entry = tk.Entry(key_row, textvariable=key_var, width=54)
        self.theme.apply_to_widget(key_entry, "entry")
        key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        link_row = tk.Frame(container)
        self.theme.apply_to_widget(link_row, "main_window")
        link_row.pack(fill=tk.X, pady=(10, 0))

        need_label = tk.Label(link_row, text="Need a key?")
        self.theme.apply_to_widget(need_label, "label")
        need_label.pack(side=tk.LEFT, padx=(0, 6))

        link_label = tk.Label(
            link_row,
            text="https://account.shodan.io/register",
            cursor="hand2",
            font=("TkDefaultFont", 9, "underline"),
            fg=self.theme.colors.get("accent", "#4da3ff"),
        )
        self.theme.apply_to_widget(link_label, "label")
        link_label.configure(fg=self.theme.colors.get("accent", "#4da3ff"))
        link_label.pack(side=tk.LEFT)
        link_label.bind("<Button-1>", lambda _e: webbrowser.open("https://account.shodan.io/register"))

        result: Dict[str, Optional[str]] = {"api_key": None}

        def _cancel() -> None:
            result["api_key"] = None
            dialog.destroy()

        def _save() -> None:
            key_value = key_var.get().strip()
            if not key_value:
                messagebox.showerror("Missing API Key", "Please enter a Shodan API key.", parent=dialog)
                return
            result["api_key"] = key_value
            dialog.destroy()

        btn_row = tk.Frame(container)
        self.theme.apply_to_widget(btn_row, "main_window")
        btn_row.pack(fill=tk.X, pady=(14, 0))

        cancel_btn = tk.Button(btn_row, text="Cancel", command=_cancel)
        self.theme.apply_to_widget(cancel_btn, "button_secondary")
        cancel_btn.pack(side=tk.RIGHT, padx=(8, 0))

        save_btn = tk.Button(btn_row, text="Save & Continue", command=_save)
        self.theme.apply_to_widget(save_btn, "button_primary")
        save_btn.pack(side=tk.RIGHT)

        key_entry.bind("<Return>", lambda _e: _save())
        dialog.bind("<Escape>", lambda _e: _cancel())
        key_entry.focus_set()

        ensure_dialog_focus(dialog, self.parent)
        dialog.protocol("WM_DELETE_WINDOW", _cancel)
        self.parent.wait_window(dialog)
        return result["api_key"]

    def _ensure_shodan_api_key_for_scan(self, scan_options: Dict[str, Any]) -> bool:
        """
        Ensure scans have a persisted Shodan API key before launch.

        If config key is missing:
        - Use api_key_override when provided (persist and continue), or
        - Prompt user for key (persist; abort when cancelled/failed).
        """
        if bool(getattr(self.backend_interface, "mock_mode", False)):
            return True

        configured_key = self._read_shodan_api_key_from_config()
        if configured_key:
            return True

        override_key = str(scan_options.get("api_key_override") or "").strip()
        if not override_key:
            override_key = str(self._prompt_for_shodan_api_key() or "").strip()
            if not override_key:
                messagebox.showinfo(
                    "Scan Cancelled",
                    "Scan start was cancelled because no Shodan API key was provided.",
                    parent=self.parent,
                )
                return False

        if not self._persist_shodan_api_key_to_config(override_key):
            messagebox.showerror(
                "Configuration Error",
                "Failed to save Shodan API key to config file.\n\n"
                "Please check config file permissions and try again.",
                parent=self.parent,
            )
            return False

        # Ensure immediate run uses the newly provided key even before any
        # backend config reload.
        scan_options["api_key_override"] = override_key
        return True

    def _start_new_scan(self, scan_options: dict) -> bool:
        """Start new scan with specified options."""
        try:
            # Final check for external scans before starting
            self._check_external_scans()
            if self.scan_button_state != "idle":
                return False  # External scan detected, don't proceed

            if not self._ensure_shodan_api_key_for_scan(scan_options):
                return False

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

    def _run_post_scan_batch_operations(
        self,
        scan_options: Dict[str, Any],
        scan_results: Dict[str, Any],
        *,
        schedule_reset: bool = True,
        show_dialogs: bool = True,
    ) -> None:
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
                if show_dialogs:
                    self._show_scan_results(scan_results)
                if schedule_reset:
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
                if show_dialogs:
                    self._show_scan_results(scan_results)
                if schedule_reset:
                    try:
                        self.parent.after(5000, self._reset_scan_status)
                    except tk.TclError:
                        pass
                return

            # Query database for eligible servers in the active protocol (keep UI responsive)
            scan_protocol = str(scan_results.get("protocol") or "").strip().lower()
            host_type_filter = {
                "ftp": "F",
                "http": "H",
            }.get(scan_protocol, "S")

            def _fetch_servers():
                return self._get_servers_for_bulk_ops(
                    skip_indicator_extract=scan_options.get("bulk_extract_skip_indicators", True),
                    host_type_filter=host_type_filter,
                    scan_start_time=scan_results.get("start_time"),
                    scan_end_time=scan_results.get("end_time"),
                )

            servers_for_ops, fetch_error = self._run_background_fetch(
                title="Preparing Bulk Operations",
                message="Gathering eligible servers for bulk operations...",
                fetch_fn=_fetch_servers
            )

            if fetch_error:
                if show_dialogs:
                    messagebox.showerror(
                        "Bulk Operations Error",
                        f"Failed to gather servers for bulk operations:\n{fetch_error}"
                    )
                    self._show_scan_results(scan_results)
                else:
                    _logger.warning("Bulk operations fetch error (suppressed dialog): %s", fetch_error)
                if schedule_reset:
                    try:
                        self.parent.after(5000, self._reset_scan_status)
                    except tk.TclError:
                        pass
                return

            probe_targets = servers_for_ops.get("probe") if isinstance(servers_for_ops, dict) else []
            extract_targets = servers_for_ops.get("extract") if isinstance(servers_for_ops, dict) else []

            if not probe_targets and not extract_targets and (bulk_probe_enabled or bulk_extract_enabled):
                # Show info message only if bulk operations were enabled.
                if show_dialogs:
                    messagebox.showinfo(
                        "Bulk Operations Skipped",
                        "No eligible servers found for bulk operations.\n\n"
                        "Bulk probe/extract operations require at least one accessible server."
                    )
                    self._show_scan_results(scan_results)
                if schedule_reset:
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
                if show_dialogs and results:
                    self._show_batch_summary(results, job_type=job_type)

            # After bulk operations, show the deferred scan summary
            if show_dialogs:
                self._show_scan_results(scan_results)
            if schedule_reset:
                try:
                    self.parent.after(5000, self._reset_scan_status)
                except tk.TclError:
                    pass

        except Exception as e:
            if show_dialogs:
                messagebox.showerror(
                    "Batch Operations Error",
                    f"Error running post-scan batch operations: {str(e)}\n\n"
                    f"The scan completed successfully but bulk operations encountered an error."
                )
            else:
                _logger.exception("Post-scan batch operations error (suppressed dialog): %s", e)

            # Even on error, fall back to showing the scan summary when dialogs are enabled.
            try:
                if show_dialogs:
                    self._show_scan_results(scan_results)
                if schedule_reset:
                    self.parent.after(5000, self._reset_scan_status)
            except Exception:
                pass

    def _get_servers_for_bulk_ops(
        self,
        skip_indicator_extract: bool = True,
        host_type_filter: Optional[str] = None,
        scan_start_time: Optional[str] = None,
        scan_end_time: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Gather servers eligible for bulk probe and extract.

        Probe:
          - Post-scan path: accessible rows from the immediate prior scan only
            (when scan_start_time + scan_end_time are provided)
          - Other callers: recent active rows in selected protocol
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

            scan_cohort_ids: Optional[set[int]] = None
            if (
                host_type_filter in {"S", "F", "H"}
                and scan_start_time
                and scan_end_time
                and hasattr(self.db_reader, "get_protocol_scan_cohort_server_ids")
            ):
                try:
                    scan_cohort_ids = set(
                        self.db_reader.get_protocol_scan_cohort_server_ids(
                            host_type_filter,
                            scan_start_time,
                            scan_end_time,
                        )
                    )
                except Exception as exc:
                    _logger.warning(
                        "Scan cohort filter unavailable for protocol %s: %s",
                        host_type_filter,
                        exc,
                    )
                    # Avoid widening post-scan probe scope on errors.
                    scan_cohort_ids = set()

            for server in servers:
                server_host_type = (server.get("host_type") or "S").upper()
                if host_type_filter and server_host_type != host_type_filter:
                    continue

                if scan_cohort_ids is not None:
                    try:
                        server_id = int(server.get("protocol_server_id"))
                    except (TypeError, ValueError):
                        continue
                    if server_id not in scan_cohort_ids:
                        continue

                # Probe eligibility remains row-based (not share-count based).
                # Optional scan-cohort filtering above narrows post-scan runs to
                # immediate prior scan results only.
                result["probe"].append(server)

                accessible = (server.get("accessible_shares") or 0) > 0
                if not accessible:
                    continue

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
        enable_rce = bool(
            (self.current_scan_options or {}).get(
                "rce_enabled",
                self.settings_manager.get_setting('scan_dialog.rce_enabled', False)
            )
        )

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
        self.theme.apply_to_widget(progress_label, "label")
        progress_label.pack(pady=(18, 8))

        progress_bar = ttk.Progressbar(
            progress_dialog,
            length=320,
            mode='determinate',
            maximum=len(servers),
            style="SMBSeek.Horizontal.TProgressbar",
        )
        progress_bar.pack(pady=(0, 10))

        cancel_button = tk.Button(progress_dialog, text="Cancel", command=lambda: cancel_event.set())
        self.theme.apply_to_widget(cancel_button, "button_secondary")
        cancel_button.pack(pady=(0, 10))

        self.theme.apply_theme_to_application(progress_dialog)

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

            try:
                snapshot = dispatch_probe_run(
                    ip_address, host_type,
                    max_directories=int(max_dirs),
                    max_files=int(max_files),
                    timeout_seconds=int(timeout_seconds),
                    cancel_event=cancel_event,
                    port=port,
                )
                analysis = probe_patterns.attach_indicator_analysis(snapshot, self.indicator_patterns)
                issue_detected = bool(analysis.get("is_suspicious"))
                status = "issue" if issue_detected else "clean"

                probe_summary = summarize_probe_snapshot(snapshot)
                display_entries = probe_summary["display_entries"]
                accessible_dirs_count = len(display_entries)
                accessible_dirs_list = ",".join(display_entries)
                try:
                    snapshot_path = get_probe_snapshot_path_for_host(ip_address, host_type, port=port)
                except TypeError:
                    snapshot_path = get_probe_snapshot_path_for_host(ip_address, host_type)

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

        # HTTP probe path
        elif host_type == "H":
            try:
                protocol_server_id = server.get("protocol_server_id")
                try:
                    http_port = int(server.get("port")) if server.get("port") is not None else None
                except (TypeError, ValueError):
                    http_port = None
                http_scheme = server.get("scheme")
                if self.db_reader and (http_scheme is None or http_port is None):
                    detail = self.db_reader.get_http_server_detail(
                        ip_address,
                        protocol_server_id=protocol_server_id,
                        port=http_port,
                    )
                    if http_port is None:
                        try:
                            http_port = int((detail or {}).get("port") or 80)
                        except (TypeError, ValueError):
                            http_port = 80
                    if http_scheme is None:
                        http_scheme = (detail or {}).get("scheme") or ("https" if http_port == 443 else "http")
                if http_port is None:
                    http_port = 80
                if http_scheme is None:
                    http_scheme = "https" if http_port == 443 else "http"
                snapshot = dispatch_probe_run(
                    ip_address, host_type,
                    max_directories=int(max_dirs),
                    max_files=int(max_files),
                    timeout_seconds=int(timeout_seconds),
                    cancel_event=cancel_event,
                    port=http_port,
                    scheme=http_scheme,
                    protocol_server_id=protocol_server_id,
                    db_reader=self.db_reader,
                )
                analysis = probe_patterns.attach_indicator_analysis(snapshot, self.indicator_patterns)
                issue_detected = bool(analysis.get("is_suspicious"))
                status = "issue" if issue_detected else "clean"

                probe_summary = summarize_probe_snapshot(snapshot)
                dir_names = probe_summary["directory_names"]
                display_entries = probe_summary["display_entries"]
                total_files = int(probe_summary["total_file_count"])
                total = len(dir_names) + total_files
                accessible_dirs_count = len(dir_names)
                accessible_dirs_list = ",".join(display_entries)
                try:
                    snapshot_path = get_probe_snapshot_path_for_host(ip_address, host_type, port=http_port)
                except TypeError:
                    snapshot_path = get_probe_snapshot_path_for_host(ip_address, host_type)

                try:
                    if self.db_reader:
                        self.db_reader.upsert_probe_cache_for_host(
                            ip_address,
                            "H",
                            status=status,
                            indicator_matches=len(analysis.get("matches", [])),
                            snapshot_path=snapshot_path,
                            accessible_dirs_count=accessible_dirs_count,
                            accessible_dirs_list=accessible_dirs_list,
                            accessible_files_count=total_files,
                            protocol_server_id=protocol_server_id,
                            port=http_port,
                        )
                except Exception:
                    pass

                notes_h: List[str] = [f"{total} entries"]
                if issue_detected:
                    notes_h.append("Indicators detected")

                return {
                    "ip_address": ip_address,
                    "action": "probe",
                    "status": "success",
                    "notes": ", ".join(notes_h),
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
            result = dispatch_probe_run(
                ip_address, host_type,
                max_directories=max_dirs,
                max_files=max_files,
                timeout_seconds=timeout_seconds,
                cancel_event=cancel_event,
                shares=shares,
                username=username,
                password=password,
                enable_rce=enable_rce,
                allow_empty=True,
                db_reader=self.db_reader,
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
        quarantine_base_path: Optional[Path] = None
        config_path = self.settings_manager.get_setting('backend.config_path', None) if self.settings_manager else None
        if config_path and Path(config_path).exists():
            try:
                config_data = json.loads(Path(config_path).read_text(encoding="utf-8"))
                file_cfg = config_data.get("file_collection", {})
                included_extensions = file_cfg.get("included_extensions", []) or []
                excluded_extensions = file_cfg.get("excluded_extensions", []) or []
                quarantine_candidate = (
                    config_data.get("file_browser", {}).get("quarantine_root")
                    or config_data.get("ftp_browser", {}).get("quarantine_base")
                    or config_data.get("http_browser", {}).get("quarantine_base")
                    or config_data.get("file_collection", {}).get("quarantine_base")
                )
                if quarantine_candidate:
                    quarantine_base_path = Path(str(quarantine_candidate)).expanduser()
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
                    quarantine_base_path,
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
                                 quarantine_base_path: Optional[Path],
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
            quarantine_dir = create_quarantine_dir(
                ip_address,
                purpose="post-scan-extract",
                base_path=quarantine_base_path,
            )
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
        show_batch_summary_dialog(
            parent=self.parent,
            theme=self.theme,
            job_type=job_type or "batch",
            results=results,
            title_suffix="Batch Summary",
            geometry="700x400",
            show_export=True,
            show_stats=False,
            wait=True,
            modal=True,
        )

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
        dialog.title("About Dirracuda")
        dialog.transient(self.parent)
        dialog.grab_set()
        if self.theme:
            apply_theme_to_window(dialog)

        body = tk.Frame(dialog)
        self.theme.apply_to_widget(body, "main_window")
        body.pack(padx=18, pady=16, fill=tk.BOTH, expand=True)

        title = tk.Label(
            body,
            text="Dirracuda",
            font=(None, 14, "bold"),
            bg=self.theme.colors["primary_bg"],
            fg=self.theme.colors["text"],
        )
        title.pack(anchor="w")

        blurb = (
            "Dirracuda helps defensive analysts find exposed servers (SMB, FTP, HTTP)\n"
            "with weak authentication and demonstrate impact via safe, guided workflows.\n"
            "No warranty expressed or implied; use at your own risk."
        )
        tk.Label(
            body,
            text=blurb,
            justify="left",
            anchor="w",
            bg=self.theme.colors["primary_bg"],
            fg=self.theme.colors["text"],
        ).pack(anchor="w", pady=(6, 10))

        link = tk.Label(
            body,
            text="GitHub: https://github.com/b3p3k0/dirracuda",
            fg=self.theme.colors["accent"],
            bg=self.theme.colors["primary_bg"],
            cursor="hand2",
        )
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/b3p3k0/dirracuda"))

        btn_frame = tk.Frame(body)
        self.theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(fill=tk.X, pady=(12, 0))
        close_button = tk.Button(btn_frame, text="Close", command=dialog.destroy)
        self.theme.apply_to_widget(close_button, "button_secondary")
        close_button.pack(side=tk.RIGHT)

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

        if not self._ensure_shodan_api_key_for_scan(scan_options):
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

        if not self._ensure_shodan_api_key_for_scan(scan_options):
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
