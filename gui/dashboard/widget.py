"""
DashboardWidget — extracted to gui.dashboard package (C9).

gui.components.dashboard remains the canonical shim; all frozen patch paths
(gui.components.dashboard.messagebox, .threading, .tk, .ttk, etc.) continue
to resolve correctly via _mb() / _d() at call time.
"""

import tkinter as tk
from tkinter import ttk
import webbrowser
import tkinter.font as tkfont
from gui.utils import safe_messagebox as messagebox
from gui.utils import safe_messagebox as _fallback_msgbox
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
from gui.components.reddit_grab_dialog import show_reddit_grab_dialog
from experimental.redseek.service import IngestOptions, IngestResult, run_ingest
from gui.components.scan_results_dialog import show_scan_results_dialog
from gui.components.batch_summary_dialog import show_batch_summary_dialog
from gui.utils.settings_manager import get_settings_manager
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.components import dashboard_logs
from gui.components import dashboard_status
from gui.components import dashboard_scan
from gui.components import dashboard_batch_ops
from gui.utils import (
    probe_cache,
    probe_patterns,
    extract_runner,
)
from gui.utils.probe_cache_dispatch import get_probe_snapshot_path_for_host, dispatch_probe_run
from gui.utils.probe_snapshot_summary import summarize_probe_snapshot
from gui.utils.logging_config import get_logger
from shared.quarantine import create_quarantine_dir
from shared.tmpfs_quarantine import get_tmpfs_runtime_state

_logger = get_logger("dashboard")


# ── Patch-safe helpers ────────────────────────────────────────────────────────

def _mb():
    """Return messagebox from gui.components.dashboard's namespace.

    Tests patch gui.components.dashboard.messagebox. Calling through this
    helper means the patched object is used at call-time, preserving all
    frozen patch paths.
    Falls back to the real safe_messagebox if dashboard is not yet loaded.
    """
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None and hasattr(mod, "messagebox"):
        return mod.messagebox
    return _fallback_msgbox


def _d(name: str) -> Any:
    """Resolve a name from gui.components.dashboard at call-time.

    Tests patch gui.components.dashboard.<name>. Using this helper ensures
    the patched binding is used rather than a cached import-time reference.
    """
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None:
        return getattr(mod, name)
    raise RuntimeError(
        f"gui.components.dashboard not yet loaded (looking for {name!r})"
    )


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
        # Bind vars to explicit parent to avoid reliance on Tk default-root state.
        self.status_text = tk.StringVar(master=self.parent, value="Loading dashboard summary...")
        self.clamav_status_text = tk.StringVar(
            master=self.parent,
            value="✖ ClamAV integration active <auto>",
        )
        self.tmpfs_status_text = tk.StringVar(
            master=self.parent,
            value=f"✖ tmpfs activated <{Path.home() / '.dirracuda' / 'quarantine_tmpfs'}>",
        )
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
        self.reddit_grab_button = None
        self._reddit_grab_running = False
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
            "Dirracuda      ><(((°>",
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
            getattr(self, "reddit_grab_button", None),
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
            _mb().showerror(
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

        clamav_status_label = tk.Label(
            footer,
            textvariable=self.clamav_status_text,
            anchor="w",
            justify="left",
            bg=self.theme.colors["card_bg"],
            fg=self.theme.colors["text_secondary"],
            font=self.theme.fonts["status"],
            wraplength=520,
        )
        clamav_status_label.grid(row=0, column=0, sticky="w")

        tmpfs_status_label = tk.Label(
            footer,
            textvariable=self.tmpfs_status_text,
            anchor="w",
            justify="left",
            bg=self.theme.colors["card_bg"],
            fg=self.theme.colors["text_secondary"],
            font=self.theme.fonts["status"],
            wraplength=520,
        )
        tmpfs_status_label.grid(row=1, column=0, sticky="w", pady=(2, 0))

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
        status_summary_label.grid(row=2, column=0, sticky="w", pady=(4, 0))

        self.update_time_label = tk.Label(
            footer,
            text="",
            anchor="w",
            bg=self.theme.colors["card_bg"],
            fg=self.theme.colors["text_secondary"],
            font=self.theme.fonts["status"]
        )
        self.update_time_label.grid(row=3, column=0, sticky="w", pady=(4, 0))

        button_frame = tk.Frame(
            footer,
            bg=self.theme.colors["card_bg"]
        )
        button_frame.grid(row=0, column=1, rowspan=4, sticky="se", padx=(10, 0))

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
            self._update_runtime_status_display()

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

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        """Convert mixed config values to bool with safe defaults."""
        return dashboard_status.coerce_bool_dashboard(value)

    @staticmethod
    def _normalize_clamav_backend(value: Any) -> str:
        """Normalize backend mode to one of auto/clamdscan/clamscan."""
        return dashboard_status.normalize_clamav_backend(value)

    def _compose_runtime_status_lines(
        self,
        clamav_cfg: Optional[Dict[str, Any]] = None,
        tmpfs_state: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, str]:
        """Build ClamAV/tmpfs status lines shown below console output."""
        clamav_cfg = clamav_cfg if isinstance(clamav_cfg, dict) else self._load_clamav_config()
        tmpfs_state = tmpfs_state if isinstance(tmpfs_state, dict) else get_tmpfs_runtime_state()
        return dashboard_status.compose_runtime_status_lines(clamav_cfg, tmpfs_state)

    def _update_runtime_status_display(self) -> None:
        """Refresh runtime status rows for ClamAV and tmpfs."""
        try:
            clamav_line, tmpfs_line = self._compose_runtime_status_lines()
            self.clamav_status_text.set(clamav_line)
            self.tmpfs_status_text.set(tmpfs_line)
        except Exception as exc:
            _logger.debug("Failed to refresh runtime status rows: %s", exc)

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
        dashboard_scan.start_scan_progress(self, scan_type, countries)

    def update_scan_progress(self, percentage: Optional[float], message: str) -> None:
        """
        Update scan progress display.

        Args:
            percentage: Progress percentage (0-100) or None for status-only update
            message: Progress message to display
        """
        dashboard_scan.update_scan_progress(self, percentage, message)

    def finish_scan_progress(self, success: bool, results: Dict[str, Any]) -> None:
        """
        Finish scan progress display.

        Args:
            success: Whether scan completed successfully
            results: Scan results dictionary
        """
        dashboard_scan.finish_scan_progress(self, success, results)

    def _show_quick_scan_dialog(self) -> None:
        """Show scan configuration dialog and start scan."""
        # Check if scan is already active
        if self.scan_manager.is_scan_active():
            _mb().showwarning(
                "Scan in Progress",
                "A scan is already running. Please wait for it to complete before starting another scan."
            )
            return

        # Show unified scan dialog
        _d('show_unified_scan_dialog')(
            parent=self.parent,
            config_path=self.config_path,
            scan_start_callback=self._start_unified_scan,
            settings_manager=getattr(self, "settings_manager", None),
            config_editor_callback=self._open_config_editor_from_scan,
            query_editor_callback=self._open_config_editor,
            reddit_grab_callback=self._handle_reddit_grab_button_click,
        )

    def _open_config_editor_from_scan(self, config_path: str) -> None:
        """Open configuration editor from scan dialog."""
        if self.config_editor_callback:
            self.config_editor_callback(config_path)

    def _clear_queued_scan_state(self) -> None:
        """Reset in-memory state for queued multi-protocol scan runs."""
        dashboard_scan.clear_queued_scan_state(self)

    def _start_unified_scan(self, scan_request: dict) -> None:
        """
        Start scans from unified dialog request.

        If multiple protocols are selected, scans execute sequentially.
        """
        dashboard_scan.start_unified_scan(self, scan_request)

    def _build_protocol_scan_options(self, protocol: str, common_options: Dict[str, Any]) -> Dict[str, Any]:
        """Convert unified dialog options into protocol-specific scan options."""
        return dashboard_scan.build_protocol_scan_options(protocol, common_options)

    def _start_protocol_scan(self, protocol: str, scan_options: Dict[str, Any]) -> bool:
        """Dispatch launch to the existing protocol-specific start handlers."""
        return dashboard_scan.start_protocol_scan(self, protocol, scan_options)

    def _abort_queued_scan_on_failure(
        self,
        protocol: str,
        reason: str,
        *,
        title: str = "Protocol Scan Failed",
    ) -> None:
        """Abort remaining queued protocol scans after a failure."""
        dashboard_scan.abort_queued_scan_on_failure(self, protocol, reason, title=title)

    def _launch_next_queued_scan(self) -> None:
        """Start the next protocol in queue, if any remain."""
        dashboard_scan.launch_next_queued_scan(self)

    def _handle_queued_scan_completion(self, results: Dict[str, Any]) -> None:
        """Handle queue continuation after each protocol scan completes."""
        dashboard_scan.handle_queued_scan_completion(self, results)

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
                _mb().showerror("Missing API Key", "Please enter a Shodan API key.", parent=dialog)
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
        return dashboard_scan.ensure_shodan_api_key_for_scan(self, scan_options)

    def _start_new_scan(self, scan_options: dict) -> bool:
        """Start new scan with specified options."""
        return dashboard_scan.start_new_scan(self, scan_options)

    def _handle_scan_progress(self, percentage: float, status: str, phase: str) -> None:
        """Handle progress updates from scan manager."""
        dashboard_scan.handle_scan_progress(self, percentage, status, phase)

    def _show_scan_progress(self, country: Optional[str]) -> None:
        """Transition progress display to active scanning state."""
        dashboard_scan.show_scan_progress(self, country)

    def _monitor_scan_completion(self) -> None:
        """Monitor scan for completion and show results."""
        dashboard_scan.monitor_scan_completion(self)

    def _run_post_scan_batch_operations(
        self,
        scan_options: Dict[str, Any],
        scan_results: Dict[str, Any],
        *,
        schedule_reset: bool = True,
        show_dialogs: bool = True,
    ) -> None:
        dashboard_batch_ops.run_post_scan_batch_operations(
            self, scan_options, scan_results,
            schedule_reset=schedule_reset, show_dialogs=show_dialogs,
        )

    def _get_servers_for_bulk_ops(
        self,
        skip_indicator_extract: bool = True,
        host_type_filter: Optional[str] = None,
        scan_start_time: Optional[str] = None,
        scan_end_time: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        return dashboard_batch_ops.get_servers_for_bulk_ops(
            self,
            skip_indicator_extract=skip_indicator_extract,
            host_type_filter=host_type_filter,
            scan_start_time=scan_start_time,
            scan_end_time=scan_end_time,
        )

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

    def _run_background_fetch(self, title: str, message: str, fetch_fn: Callable[[], Any]) -> tuple:
        return dashboard_batch_ops.run_background_fetch(self, title, message, fetch_fn)

    def _execute_batch_probe(self, servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return dashboard_batch_ops.execute_batch_probe(self, servers)

    def _probe_single_server(self, server: Dict[str, Any], max_dirs: int, max_files: int,
                              timeout_seconds: int, enable_rce: bool, cancel_event: threading.Event) -> Dict[str, Any]:
        return dashboard_batch_ops.probe_single_server(
            self, server, max_dirs, max_files, timeout_seconds, enable_rce, cancel_event
        )

    def _protocol_label_from_host_type(self, host_type: Optional[str]) -> str:
        return dashboard_batch_ops.protocol_label_from_host_type(host_type)

    def _protocol_label_for_result(self, result: Dict[str, Any]) -> str:
        return dashboard_batch_ops.protocol_label_for_result(self, result)

    def _build_probe_notes(self, share_count: int, enable_rce: bool, issue_detected: bool,
                            analysis: Dict[str, Any], result: Dict[str, Any]) -> str:
        return dashboard_batch_ops.build_probe_notes(
            self, share_count, enable_rce, issue_detected, analysis, result
        )

    def _execute_batch_extract(self, servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return dashboard_batch_ops.execute_batch_extract(self, servers)

    def _extract_single_server(self, server: Dict[str, Any], max_file_mb: int, max_total_mb: int,
                                 max_time: int, max_files: int, extension_mode: str,
                                 included_extensions: List[str], excluded_extensions: List[str],
                                 quarantine_base_path: Optional[Path],
                                 cancel_event: threading.Event,
                                 clamav_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return dashboard_batch_ops.extract_single_server(
            self, server, max_file_mb, max_total_mb, max_time, max_files,
            extension_mode, included_extensions, excluded_extensions,
            quarantine_base_path, cancel_event, clamav_config=clamav_config,
        )

    def _show_batch_summary(self, results: List[Dict[str, Any]], job_type: Optional[str] = None) -> None:
        dashboard_batch_ops.show_batch_summary(self, results, job_type=job_type)

    def _load_clamav_config(self) -> Dict[str, Any]:
        return dashboard_batch_ops.load_clamav_config(self)

    def _maybe_show_clamav_dialog(
        self,
        results: List[Dict[str, Any]],
        clamav_cfg: Dict[str, Any],
        *,
        wait: bool = False,
        modal: bool = False,
    ) -> None:
        dashboard_batch_ops.maybe_show_clamav_dialog(
            self, results, clamav_cfg, wait=wait, modal=modal
        )

    def _show_scan_results(self, results: Dict[str, Any]) -> None:
        dashboard_batch_ops.show_scan_results(self, results)

    def _open_config_editor(self) -> None:
        """Open application configuration dialog."""
        if self.drill_down_callback:
            self.drill_down_callback("app_config", {})

    def _open_db_tools(self) -> None:
        """Open database tools dialog."""
        from gui.components.db_tools_dialog import show_db_tools_dialog

        if not self.db_reader:
            _mb().showerror(
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
