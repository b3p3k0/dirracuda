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
import threading
import json
from typing import Dict, List, Any, Optional, Callable
import sys
import queue
from collections import deque
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from gui.utils.database_access import DatabaseReader
from gui.utils.backend_interface import BackendInterface
from gui.utils.style import get_theme, apply_theme_to_window
from gui.utils.scan_manager import get_scan_manager
from gui.utils.settings_manager import get_settings_manager
from gui.components import dashboard_logs
from gui.utils.logging_config import get_logger
from gui.components.dashboard_bulk_ops import _DashboardBulkOpsMixin
from gui.components.dashboard_scan_controls import _DashboardScanControlsMixin
from gui.components.dashboard_scan_orchestration import _DashboardScanOrchestrationMixin
from gui.components.dashboard_scan_lifecycle import _DashboardScanLifecycleMixin

_logger = get_logger("dashboard")


class DashboardWidget(_DashboardBulkOpsMixin, _DashboardScanControlsMixin, _DashboardScanOrchestrationMixin, _DashboardScanLifecycleMixin):
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
        self._status_refresh_pending = False
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
            "SMBSeek Security Toolkit",
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
        self.theme.apply_to_widget(body, "main_window")
        body.pack(padx=18, pady=16, fill=tk.BOTH, expand=True)

        title = tk.Label(
            body,
            text="SMBSeek",
            font=(None, 14, "bold"),
            bg=self.theme.colors["primary_bg"],
            fg=self.theme.colors["text"],
        )
        title.pack(anchor="w")

        blurb = (
            "SMBSeek helps defensive analysts find SMB servers with weak \n"
            "authentication and demonstrate impact via safe, guided workflows.\n"
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
            text="GitHub: https://github.com/b3p3k0/smbseek",
            fg=self.theme.colors["accent"],
            bg=self.theme.colors["primary_bg"],
            cursor="hand2",
        )
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/b3p3k0/smbseek"))

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
    
