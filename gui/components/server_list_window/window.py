"""
Main Server List Window

Orchestrates all server list functionality using extracted modules.
Maintains all shared state and coordinates between components.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from datetime import datetime
import sqlite3
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, Future
import threading
import platform
import csv
import os
import sys

from gui.utils.database_access import DatabaseReader
from gui.utils.style import get_theme
from gui.utils.data_export_engine import get_export_engine
from gui.utils.scan_manager import get_scan_manager
from gui.utils.template_store import TemplateStore
from gui.utils.logging_config import get_logger
from gui.components.pry_dialog import PryDialog
from gui.components.pry_status_dialog import BatchStatusDialog
from gui.components.reddit_browser_window import show_reddit_browser_window
from shared.db_migrations import run_migrations

_logger = get_logger("server_list_window")


def _format_notes_tooltip_text(notes: str, *, max_line_len: int = 60, max_lines: int = 2) -> str:
    """
    Format notes for compact hover tooltip display.

    Rules:
    - Normalize whitespace
    - Clamp to max_line_len * max_lines characters
    - Render at most max_lines lines
    - Add trailing ellipsis when truncated
    """
    normalized = " ".join(str(notes or "").split())
    if not normalized:
        return ""

    max_chars = max_line_len * max_lines
    truncated = len(normalized) > max_chars
    visible = normalized[:max_chars]

    if truncated:
        if len(visible) >= 3:
            visible = visible[:-3] + "..."
        else:
            visible = "..."

    lines = [visible[i:i + max_line_len] for i in range(0, len(visible), max_line_len)]
    return "\n".join(lines[:max_lines])

# Import modular components
from . import export, details, filters, table
try:
    from batch_extract_dialog import BatchExtractSettingsDialog  # standalone/absolute import
except ImportError:
    from ..batch_extract_dialog import BatchExtractSettingsDialog  # package relative fallback
from gui.utils import probe_cache, probe_patterns, probe_runner, extract_runner, pry_runner
from shared.quarantine import create_quarantine_dir

from gui.components.server_list_window.actions import ServerListWindowActionsMixin


class ServerListWindow(ServerListWindowActionsMixin):
    """
    Server list browser window with filtering and export capabilities.

    Orchestrates modular components while maintaining all shared state.
    Acts as facade for clean external interface.
    """
    FILTER_TEMPLATE_PLACEHOLDER = "Select filter template..."
    HINT_NO_DATABASE = "No database found. Select one in Config or run a scan."
    HINT_EMPTY_DATABASE = "No servers in the database yet. Run a scan or import data."
    HINT_FILTER_EMPTY = "No results to display. Try less restrictive filters."

    def __init__(self, parent: tk.Widget, db_reader: DatabaseReader,
                 window_data: Dict[str, Any] = None, settings_manager = None):
        """
        Initialize server list browser window.

        Args:
            parent: Parent widget
            db_reader: Database access instance
            window_data: Optional data for filtering/focus
            settings_manager: Optional settings manager for favorites functionality
        """
        self.parent = parent
        self.db_reader = db_reader
        self.theme = get_theme()
        self.window_data = window_data or {}
        self.settings_manager = settings_manager
        self.probe_status_map = {}
        self.ransomware_indicators = []
        self.indicator_patterns = []

        # Favorites / avoid / probe filter toggles
        self.favorites_only = tk.BooleanVar()
        self.exclude_avoid = tk.BooleanVar()
        self.probed_only = tk.BooleanVar()
        self.exclude_compromised = tk.BooleanVar()
        self.protocol_smb = tk.BooleanVar(value=True)
        self.protocol_ftp = tk.BooleanVar(value=True)
        self.protocol_http = tk.BooleanVar(value=True)

        # Window and UI components
        self.window = None
        self.main_frame = None
        self.filter_frame = None
        self.filter_widgets = None
        self.table_frame = None
        self.button_frame = None

        # Table components
        self.tree = None
        self.scrollbar_v = None
        self.scrollbar_h = None

        # Filter variables - simplified for enhanced share tracking
        self.search_text = tk.StringVar()
        self.search_var = tk.StringVar()  # Additional search reference
        self.date_filter = tk.StringVar(value="All")
        self.shares_filter = tk.BooleanVar(value=True)  # Default checked to hide zero-share servers

        # Country filter state
        self.country_listbox = None
        self.country_code_list = []
        self.country_filter_text = tk.StringVar()

        # UI components
        self.count_label = None
        self.selection_label = None
        self.status_label = None
        self.mode_button = None
        self.add_record_button = None
        self.show_all_button = None
        self.context_menu = None
        self.probe_button = None
        self.extract_button = None
        self.pry_button = None
        self.browser_button = None
        self.stop_button = None
        self.delete_button = None
        self._selection_menu_indices: List[int] = []  # Selection-dependent context menu entries
        self._delete_menu_index = None  # Store context menu index
        self._delete_in_progress = False  # Flag to prevent concurrent deletes
        self.table_overlay = None
        self.table_overlay_label = None
        self.pry_status_button = None
        self.batch_status_dialog = None
        self._stop_button_original_style = None
        self._context_menu_visible = False
        self._context_menu_bindings = []
        self._notes_tooltip = None
        self._notes_tooltip_label = None
        self._hover_notes_row_key = None
        self._hover_notes_text = ""
        self.filter_template_var = tk.StringVar()
        self._filter_template_label_to_slug: Dict[str, str] = {}
        self._selected_filter_template_slug: Optional[str] = None
        try:
            self.filter_template_store = TemplateStore(
                settings_manager=None,
                base_dir=Path.home() / ".dirracuda" / "filter_templates",
                seed_dir=None
            )
        except Exception as exc:
            _logger.warning("Filter template store unavailable: %s", exc)
            self.filter_template_store = None

        # Date filtering state
        self.filter_recent = self.window_data.get("filter_recent", False)
        self.last_scan_time = None

        # Data management
        self.all_servers = []
        self.filtered_servers = []
        self.selected_servers = []
        self.active_jobs: Dict[str, Dict[str, Any]] = {}
        self._pending_table_refresh = False
        self._pending_selection = []
        self._initial_load_after_id = None
        self._initial_load_started = False
        self._initial_map_bind_id = None
        self._db_available = True
        self._table_locked = False
        self._empty_state_hint: Optional[str] = None

        # Window state
        self.is_advanced_mode = False
        self.pry_status_dialog = None

        # Sort state tracking for bidirectional column sorting
        self.current_sort_column = None
        self.current_sort_direction = None
        self.original_headers = {}  # Cache original column text for clean restoration

        # Default sort directions for each column
        self.default_sort_directions = {
            "IP Address": "asc",      # alphabetical A-Z
            "Shares": "desc",         # high numbers first (10, 5, 1)
            "Accessible": "desc",     # high share count first (sorts by number of shares)
            "Last Seen": "desc",      # MOST RECENT dates first (2024-01-02, 2024-01-01, 2023-12-31)
            "Country": "asc",         # alphabetical A-Z
            "probe": "desc",
            "extracted": "desc"
        }

        self._create_window()
        self._prime_initial_render()
        self._schedule_initial_data_load()

        if self.settings_manager:
            self.probe_status_map = self.settings_manager.get_probe_status_map()
            self._load_indicator_patterns()
        else:
            self._load_indicator_patterns()

    def _create_window(self) -> None:
        """Create the server list window."""
        self.window = tk.Toplevel(self.parent)
        self.window.title("Server List Browser")
        self.window.geometry("1500x1000")
        self.window.minsize(800, 500)

        # Apply theme
        self.theme.apply_to_widget(self.window, "main_window")

        # Modeless window; do not force stacking above dashboard

        # Center window
        self._center_window()

        # Build UI components
        self._create_header()
        self._create_filter_panel()
        self._create_server_table()
        self._create_button_panel()

        # Bind events
        self._setup_event_handlers()

        # Ensure window appears on top/focused after it is actually mapped.
        # Running focus/topmost choreography too early can cause first-open
        # paint starvation on some window managers.
        self.window.after(10, self._ensure_window_focus_when_mapped)
        self.theme.apply_theme_to_application(self.window)

    def _ensure_window_focus_when_mapped(self) -> None:
        """Apply focus/z-order helpers only after the window is mapped."""
        if self.window is None:
            return
        try:
            if not self.window.winfo_exists():
                return
            if not self.window.winfo_ismapped():
                self.window.after(25, self._ensure_window_focus_when_mapped)
                return
            # Keep this modeless window focus path lightweight.
            # Aggressive topmost/focus-force choreography can re-trigger
            # first-open no-paint behavior on some window managers.
            self.window.lift()
            self.window.focus_set()
            # Nudge one immediate repaint and one delayed repaint frame so
            # first render is visible without requiring manual titlebar move.
            self.window.after_idle(self._prime_initial_render)
            self.window.after(33, self._prime_initial_render)
        except tk.TclError:
            pass

    def _prime_initial_render(self) -> None:
        """
        Force an initial paint before synchronous data loading begins.

        This avoids a first-open blank frame on some window managers where the
        first full draw can be deferred until a later expose/move event.
        """
        if self.window is None:
            return
        try:
            self.window.update_idletasks()
            self.window.update()
        except tk.TclError:
            # Best effort only; fall back to normal load path.
            pass

    def _schedule_initial_data_load(self) -> None:
        """
        Defer first data load until Tk has processed initial paint events.

        Loading rows can take noticeable time on larger datasets; doing it in
        __init__ can block first paint and present as a blank/transparent window.
        """
        if self.window is None or self._initial_load_started:
            return
        try:
            # Trigger load as soon as the window is mapped on screen.
            if self._initial_map_bind_id is None:
                self._initial_map_bind_id = self.window.bind(
                    "<Map>", self._on_window_mapped_for_initial_load, add="+"
                )
            # Failsafe timer in case map events are delayed/dropped on some WMs.
            self._initial_load_after_id = self.window.after(350, self._run_initial_data_load)
        except tk.TclError:
            self._initial_load_after_id = None
            self._run_initial_data_load()

    def _on_window_mapped_for_initial_load(self, _event=None) -> None:
        """Start initial load right after the first map event."""
        if self._initial_load_started or self.window is None:
            return
        self._cancel_initial_data_load()
        try:
            # Give the WM/compositor one paint frame before synchronous work.
            self._initial_load_after_id = self.window.after(16, self._run_initial_data_load)
        except tk.TclError:
            self._initial_load_after_id = None
            self._run_initial_data_load()

    def _clear_initial_map_binding(self) -> None:
        """Remove one-time map binding used for initial load scheduling."""
        if self.window is None or self._initial_map_bind_id is None:
            self._initial_map_bind_id = None
            return
        try:
            self.window.unbind("<Map>", self._initial_map_bind_id)
        except tk.TclError:
            pass
        self._initial_map_bind_id = None

    def _run_initial_data_load(self) -> None:
        """Run deferred initial load if window still exists."""
        if self._initial_load_started:
            return
        if not self.window:
            return
        try:
            if not self.window.winfo_exists():
                return
            # Guard against starting data work before first visible paint; this
            # can reintroduce first-open blank/transparent render on some WMs.
            if (not self.window.winfo_ismapped()) or (not self.window.winfo_viewable()):
                self._initial_load_after_id = self.window.after(50, self._run_initial_data_load)
                return
        except tk.TclError:
            return
        self._initial_load_started = True
        self._initial_load_after_id = None
        self._clear_initial_map_binding()
        self._load_data()
        # Force a repaint pass after the synchronous table population.
        try:
            self.window.update_idletasks()
            self.window.update()
        except tk.TclError:
            pass

    def _cancel_initial_data_load(self) -> None:
        """Cancel deferred initial load callback when another load path is chosen."""
        if self.window is None:
            self._initial_load_after_id = None
            self._clear_initial_map_binding()
            return
        if self._initial_load_after_id is not None:
            try:
                self.window.after_cancel(self._initial_load_after_id)
            except tk.TclError:
                pass
        self._initial_load_after_id = None
        self._clear_initial_map_binding()

    def _load_indicator_patterns(self) -> None:
        """Load ransomware indicator patterns from SMBSeek config."""
        config_path = None
        if self.settings_manager:
            config_path = self.settings_manager.get_setting('backend.config_path', None)
            if not config_path:
                try:
                    config_path = self.settings_manager.get_smbseek_config_path()
                except Exception:
                    config_path = None
        self.ransomware_indicators = probe_patterns.load_ransomware_indicators(config_path)
        self.indicator_patterns = probe_patterns.compile_indicator_patterns(self.ransomware_indicators)

    def _center_window(self) -> None:
        """Center window on parent."""
        if self.window is not None:
            self.window.update_idletasks()
            # Get parent window position and size
            parent_x = self.parent.winfo_x()
            parent_y = self.parent.winfo_y()
            parent_width = self.parent.winfo_width()
            parent_height = self.parent.winfo_height()
            # Calculate center position
            width = self.window.winfo_width()
            height = self.window.winfo_height()
            x = parent_x + (parent_width // 2) - (width // 2)
            y = parent_y + (parent_height // 2) - (height // 2)
            self.window.geometry(f"{width}x{height}+{x}+{y}")

    def _create_header(self) -> None:
        """Create window header with title and controls."""
        header_frame = tk.Frame(self.window)
        self.theme.apply_to_widget(header_frame, "main_window")
        header_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        # Title
        title_label = self.theme.create_styled_label(
            header_frame,
            "🖥 Server List",
            "heading"
        )
        title_label.pack(side=tk.LEFT)

        # Server count
        self.count_label = self.theme.create_styled_label(
            header_frame,
            "Loading...",
            "body"
        )
        self.count_label.pack(side=tk.LEFT, padx=(20, 0))

        # Experimental Reddit browser entrypoint
        reddit_browser_button = tk.Button(
            header_frame,
            text="Reddit Post DB (EXP)",
            command=lambda: show_reddit_browser_window(parent=self.window),
        )
        self.theme.apply_to_widget(reddit_browser_button, "button_secondary")
        reddit_browser_button.pack(side=tk.RIGHT)

    def _create_filter_panel(self) -> None:
        """Create filtering controls panel using filters module."""
        # Load persisted filter preferences before building UI
        self._load_filter_preferences()

        # Prepare filter variables
        filter_vars = {
            'search_text': self.search_text,
            'date_filter': self.date_filter,
            'shares_filter': self.shares_filter,
            'favorites_only': self.favorites_only,
            'exclude_avoid': self.exclude_avoid,
            'probed_only': self.probed_only,
            'exclude_compromised': self.exclude_compromised,
            'protocol_smb': self.protocol_smb,
            'protocol_ftp': self.protocol_ftp,
            'protocol_http': self.protocol_http,
            'country_filter_text': self.country_filter_text
        }

        # Prepare callbacks
        filter_callbacks = {
            'on_search_changed': self._apply_filters,
            'on_date_filter_changed': self._apply_filters,
            'on_shares_filter_changed': self._apply_filters,
            'on_favorites_only_changed': self._apply_filters,
            'on_exclude_avoid_changed': self._apply_filters,
            'on_probed_only_changed': self._apply_filters,
            'on_exclude_compromised_changed': self._apply_filters,
            'on_protocol_filter_changed': self._apply_filters,
            'on_country_filter_changed': self._apply_filters,
            'on_country_filter_text_changed': self._on_country_filter_text_changed,
            'on_clear_countries': self._clear_countries,
            'on_clear_search': self._clear_search,
            'on_reset_filters': self._reset_filters,
            'on_toggle_mode': self._toggle_mode,
            'on_add_record': self._on_add_record,
            'on_filter_template_selected': self._on_filter_template_selected,
            'on_save_filter_template': self._on_save_filter_template,
            'on_delete_filter_template': self._on_delete_filter_template
        }

        # Add show all toggle if needed
        if self.filter_recent:
            filter_callbacks['on_show_all_toggle'] = self._toggle_show_all_results

        # Create filter panel using module
        self.filter_frame, self.filter_widgets = filters.create_filter_panel(
            self.window, self.theme, filter_vars, filter_callbacks
        )

        # Wire template dropdown variable and populate options
        if 'filter_template_dropdown' in self.filter_widgets:
            dropdown = self.filter_widgets['filter_template_dropdown']
            dropdown.configure(textvariable=self.filter_template_var)
            self._refresh_filter_templates()

        # Capture mode toggle reference from filter panel
        if 'mode_button' in self.filter_widgets:
            self.mode_button = self.filter_widgets['mode_button']
        if 'add_record_button' in self.filter_widgets:
            self.add_record_button = self.filter_widgets['add_record_button']

        # Disable favorites/avoid checkboxes if no settings manager
        if not self.settings_manager:
            if 'favorites_checkbox' in self.filter_widgets:
                self.filter_widgets['favorites_checkbox'].configure(state="disabled")
            if 'exclude_avoid_checkbox' in self.filter_widgets:
                self.filter_widgets['exclude_avoid_checkbox'].configure(state="disabled")

        # Extract country listbox reference and populate it
        if 'country_listbox' in self.filter_widgets:
            self.country_listbox = self.filter_widgets['country_listbox']
            # Populate country filter with codes from database
            self._populate_country_filter()
            # Restore saved country filter selections from settings
            self._restore_country_filter_selections()

        # Pack filter frame (shown/hidden based on mode)
        self._update_mode_display()

    def _create_server_table(self) -> None:
        """Create server data table using table module."""
        # Prepare callbacks
        table_callbacks = {
            'on_selection_changed': self._on_selection_changed,
            'on_double_click':      self._on_double_click,
            'on_treeview_click':    self._on_treeview_click,
            'on_favorite_toggle':   lambda rk, v: self._apply_flag_toggle(rk, "favorite", v),
            'on_avoid_toggle':      lambda rk, v: self._apply_flag_toggle(rk, "avoid",    v),
            'on_sort_column':       self._sort_by_column,
        }

        # Create table using module
        self.table_frame, self.tree, self.scrollbar_v, self.scrollbar_h = table.create_server_table(
            self.window, self.theme, table_callbacks
        )

        self._create_context_menu(self.tree)
        self._bind_context_menu_events(self.tree)
        self._bind_hover_tooltip_events(self.tree)
        self._create_table_overlay()

        # Pack table frame
        self.table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    def _create_context_menu(self, tree: ttk.Treeview) -> None:
        self.context_menu = tk.Menu(self.window, tearoff=0)
        self._selection_menu_indices = []

        def _add_selection_command(label: str, command) -> None:
            self.context_menu.add_command(label=label, command=command)
            idx = self.context_menu.index("end")
            if idx is not None:
                self._selection_menu_indices.append(int(idx))

        _add_selection_command("📋 Copy IP", self._on_copy_ip)
        self.context_menu.add_separator()
        _add_selection_command("🔍 Probe Selected", self._on_probe_selected)
        _add_selection_command("📦 Extract Selected", self._on_extract_selected)
        _add_selection_command("🔓 Pry Selected", self._on_pry_selected)
        _add_selection_command("🗂️ Browse Selected", self._on_file_browser_selected)
        self.context_menu.add_separator()
        _add_selection_command("⭐ Toggle Favorite", self._on_mark_favorite_selected)
        _add_selection_command("🚫 Toggle Avoid", self._on_mark_avoid_selected)
        _add_selection_command("⚠ Toggle Compromised", self._on_mark_compromised_selected)
        self.context_menu.add_separator()

        # Store index before adding Delete item.
        end_idx = self.context_menu.index("end")
        self._delete_menu_index = 0 if end_idx is None else int(end_idx) + 1
        self.context_menu.add_command(label="🗑️ Delete Selected", command=self._on_delete_selected)
        self._update_context_menu_state()

    def _bind_context_menu_events(self, tree: ttk.Treeview) -> None:
        tree.bind("<Button-3>", self._show_context_menu)
        if platform.system() == "Darwin":
            tree.bind("<Button-2>", self._show_context_menu)
            tree.bind("<Control-Button-1>", self._show_context_menu)

    def _bind_hover_tooltip_events(self, tree: ttk.Treeview) -> None:
        """Bind Treeview events used by row-notes hover tooltips."""
        tree.bind("<Motion>", self._on_tree_hover_for_notes, add="+")
        tree.bind("<Leave>", self._hide_notes_tooltip, add="+")
        tree.bind("<Button-1>", self._hide_notes_tooltip, add="+")
        tree.bind("<Button-3>", self._hide_notes_tooltip, add="+")
        tree.bind("<MouseWheel>", self._hide_notes_tooltip, add="+")
        tree.bind("<Button-4>", self._hide_notes_tooltip, add="+")
        tree.bind("<Button-5>", self._hide_notes_tooltip, add="+")

    def _get_server_by_row_key(self, row_key: str) -> Optional[Dict[str, Any]]:
        """Lookup a visible server row by row_key."""
        if not row_key:
            return None
        return next((s for s in self.filtered_servers if s.get("row_key") == row_key), None)

    def _show_notes_tooltip(self, text: str, x_root: int, y_root: int) -> None:
        """Create or update notes tooltip near pointer location."""
        if not text:
            self._hide_notes_tooltip()
            return

        x = int(x_root) + 14
        y = int(y_root) + 16
        geometry = f"+{x}+{y}"

        if self._notes_tooltip and self._notes_tooltip.winfo_exists():
            self._notes_tooltip.geometry(geometry)
            if self._hover_notes_text != text and self._notes_tooltip_label:
                self._notes_tooltip_label.configure(text=text)
                self._hover_notes_text = text
            return

        tip = tk.Toplevel(self.window)
        tip.wm_overrideredirect(True)
        try:
            tip.wm_attributes("-topmost", True)
        except Exception:
            pass
        tip.geometry(geometry)

        label = tk.Label(
            tip,
            text=text,
            justify=tk.LEFT,
            anchor="w",
            bg="#fffde8",
            fg="#222222",
            relief=tk.SOLID,
            borderwidth=1,
            padx=6,
            pady=4,
            font=("TkDefaultFont", 9),
        )
        label.pack()

        self._notes_tooltip = tip
        self._notes_tooltip_label = label
        self._hover_notes_text = text

    def _hide_notes_tooltip(self, _event=None) -> None:
        """Destroy notes tooltip if visible."""
        tip = self._notes_tooltip
        self._notes_tooltip = None
        self._notes_tooltip_label = None
        self._hover_notes_row_key = None
        self._hover_notes_text = ""
        if tip is not None:
            try:
                tip.destroy()
            except Exception:
                pass

    def _on_tree_hover_for_notes(self, event) -> None:
        """
        Show notes tooltip while hovering rows with notes.

        If notes are empty/whitespace, no tooltip is created.
        """
        if not self.tree:
            self._hide_notes_tooltip()
            return

        row_key = self.tree.identify_row(event.y)
        if not row_key:
            self._hide_notes_tooltip()
            return

        row = self._get_server_by_row_key(row_key)
        if not row:
            self._hide_notes_tooltip()
            return

        notes_raw = row.get("notes", "")
        if not str(notes_raw or "").strip():
            self._hide_notes_tooltip()
            return

        tooltip_text = _format_notes_tooltip_text(notes_raw, max_line_len=60, max_lines=2)
        if not tooltip_text:
            self._hide_notes_tooltip()
            return

        self._hover_notes_row_key = row_key
        self._show_notes_tooltip(tooltip_text, event.x_root, event.y_root)

    def _create_table_overlay(self) -> None:
        overlay_bg, overlay_fg = self._get_table_overlay_colors()
        self.table_overlay = tk.Frame(self.table_frame, bg=overlay_bg)
        self.table_overlay.place_forget()
        self.table_overlay_label = tk.Label(
            self.table_overlay,
            text="Batch in progress… Server list locked",
            bg=overlay_bg,
            fg=overlay_fg,
            justify=tk.CENTER,
            wraplength=560,
        )
        self.table_overlay_label.pack(expand=True)

    def _get_table_overlay_colors(self) -> Tuple[str, str]:
        """Return theme-aware (background, foreground) colors for table overlay hints."""
        colors = getattr(self.theme, "colors", {}) or {}
        overlay_bg = colors.get("secondary_bg", "#f0f0f0")
        overlay_fg = colors.get("text_secondary", "#555555")
        return overlay_bg, overlay_fg

    def _set_empty_state_hint(self, message: Optional[str]) -> None:
        """Set/clear table hint text shown when results are unavailable."""
        self._empty_state_hint = message.strip() if message and message.strip() else None
        self._update_table_overlay_state()

    def _resolve_empty_state_hint(self) -> Optional[str]:
        """
        Resolve which helper hint should be shown for the current table state.

        Priority order:
        1. No database available at runtime
        2. Database available but contains zero server rows
        3. Database has rows but current filters hide all rows
        """
        if not self._db_available:
            return self.HINT_NO_DATABASE
        if not self.all_servers:
            return self.HINT_EMPTY_DATABASE
        if not self.filtered_servers:
            return self.HINT_FILTER_EMPTY
        return None

    def _update_table_overlay_state(self) -> None:
        """Render the table overlay for batch lock or empty-state hints."""
        if not self.table_overlay or not self.table_overlay_label:
            return
        overlay_bg, overlay_fg = self._get_table_overlay_colors()
        self.table_overlay.configure(bg=overlay_bg)

        overlay_message = None
        if self._table_locked:
            overlay_message = "Batch in progress… Server list locked"
        elif self._empty_state_hint:
            overlay_message = self._empty_state_hint

        if not overlay_message:
            self.table_overlay.place_forget()
            return

        self.table_overlay_label.configure(text=overlay_message, bg=overlay_bg, fg=overlay_fg)
        self.table_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.table_overlay.lift()

    def _set_table_interaction_enabled(self, enabled: bool) -> None:
        self._table_locked = not enabled
        self._update_table_overlay_state()

    def _create_button_panel(self) -> None:
        """Create bottom button panel with actions."""
        self.button_frame = tk.Frame(self.window)
        self.theme.apply_to_widget(self.button_frame, "main_window")
        self.button_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        # Left side - selection + status info
        info_container = tk.Frame(self.button_frame)
        self.theme.apply_to_widget(info_container, "main_window")
        info_container.pack(side=tk.LEFT, anchor="w")

        self.selection_label = self.theme.create_styled_label(
            info_container,
            "No selection",
            "small"
        )
        self.selection_label.pack(anchor="w")

        self.status_label = self.theme.create_styled_label(
            info_container,
            "Idle",
            "small"
        )
        self.status_label.pack(anchor="w")

        # Hidden by default; becomes visible to reopen batch status dialog
        self.pry_status_button = tk.Button(
            info_container,
            text="Show Task Status",
            command=self._show_pry_status_dialog
        )
        self.theme.apply_to_widget(self.pry_status_button, "button_secondary")
        self.pry_status_button.pack(anchor="w", pady=(4, 0))
        self.pry_status_button.pack_forget()

        # Right side - action buttons
        button_container = tk.Frame(self.button_frame)
        self.theme.apply_to_widget(button_container, "main_window")
        button_container.pack(side=tk.RIGHT)

        # Batch/quick action buttons
        self.details_button = tk.Button(
            button_container,
            text="📋 View Details",
            command=self._view_server_details
        )
        self.theme.apply_to_widget(self.details_button, "button_secondary")
        self.details_button.pack(side=tk.LEFT, padx=(0, 8))

        self.probe_button = tk.Button(
            button_container,
            text="🔍 Probe Selected",
            command=self._on_probe_selected,
            state=tk.DISABLED
        )
        # Use secondary styling to match the rest of the group (avoid blue highlight)
        self.theme.apply_to_widget(self.probe_button, "button_secondary")
        self.probe_button.pack(side=tk.LEFT, padx=(0, 8))

        self.extract_button = tk.Button(
            button_container,
            text="📦 Extract Selected",
            command=self._on_extract_selected,
            state=tk.DISABLED
        )
        self.theme.apply_to_widget(self.extract_button, "button_secondary")
        self.extract_button.pack(side=tk.LEFT, padx=(0, 8))

        self.browser_button = tk.Button(
            button_container,
            text="🗂️ Browse Selected",
            command=self._on_file_browser_selected,
            state=tk.DISABLED
        )
        self.theme.apply_to_widget(self.browser_button, "button_secondary")
        self.browser_button.pack(side=tk.LEFT, padx=(0, 8))

        self.pry_button = tk.Button(
            button_container,
            text="🔓 Pry Selected",
            command=self._on_pry_selected,
            state=tk.DISABLED
        )
        self.theme.apply_to_widget(self.pry_button, "button_secondary")
        self.pry_button.pack(side=tk.LEFT, padx=(0, 8))

        self.delete_button = tk.Button(
            button_container,
            text="🗑️ Delete Selected",
            command=self._on_delete_selected,
            state=tk.DISABLED
        )
        # Force a red theme to signal destructive action
        self.theme.apply_to_widget(self.delete_button, "button_danger")
        # Double padding before delete for visual separation; standard after
        self.delete_button.pack(side=tk.LEFT, padx=(16, 8))

        self.stop_button = tk.Button(
            button_container,
            text="⏹ Stop Batch",
            command=self._stop_active_batch,
            state=tk.DISABLED
        )
        self.theme.apply_to_widget(self.stop_button, "button_secondary")
        self.stop_button.pack(side=tk.LEFT, padx=(0, 20))
        self._stop_button_original_style = {
            "bg": self.stop_button.cget("bg"),
            "fg": self.stop_button.cget("fg"),
            "activebackground": self.stop_button.cget("activebackground"),
            "activeforeground": self.stop_button.cget("activeforeground"),
            "text": self.stop_button.cget("text")
        }

        self._update_action_buttons_state()

    def _setup_event_handlers(self) -> None:
        """Setup event handlers for the window."""
        # Window close event
        self.window.protocol("WM_DELETE_WINDOW", self._close_window)

        # Keyboard shortcuts
        self.window.bind("<Control-a>", self._select_all)
        self.window.bind("<Control-e>", lambda e: self._export_selected_servers())
        self.window.bind("<Control-b>", lambda e: self._on_file_browser_selected())
        self.window.bind("<Escape>", lambda e: self._close_window())
        self.window.bind("<F5>", lambda e: self._refresh_data())

    def _apply_filters(self, *, force: bool = False) -> None:
        """Apply current filters to server list using filter module functions."""
        if self._is_batch_active() and not force:
            if not self._pending_table_refresh:
                self._pending_selection = self._get_selected_row_keys()
            self._pending_table_refresh = True
            return
        self._hide_notes_tooltip()

        filtered = self.all_servers[:]

        selected_protocols = []
        if self.protocol_smb.get():
            selected_protocols.append("S")
        if self.protocol_ftp.get():
            selected_protocols.append("F")
        if self.protocol_http.get():
            selected_protocols.append("H")
        filtered = filters.apply_protocol_filter(filtered, selected_protocols)

        # Apply search filter
        search_term = self.search_text.get()
        if search_term:
            filtered = filters.apply_search_filter(filtered, search_term)

        # Apply country filter
        selected_codes = self._get_selected_country_codes()
        if selected_codes:
            filtered = filters.apply_country_filter(filtered, selected_codes)

        # Apply date filter
        date_filter_value = self.date_filter.get()
        if date_filter_value and date_filter_value != "All":
            filtered = filters.apply_date_filter(filtered, date_filter_value, self.last_scan_time)

        # Apply accessible shares filter
        if self.shares_filter.get():
            filtered = filters.apply_shares_filter(filtered, True)

        # Apply favorites filter
        if self.favorites_only.get():
            filtered = filters.apply_favorites_filter(filtered, True, self.settings_manager)

        # Apply exclude avoid filter
        if self.exclude_avoid.get():
            filtered = filters.apply_exclude_avoid_filter(filtered, True, self.settings_manager)

        # Apply probed-only filter
        if self.probed_only.get():
            filtered = filters.apply_probed_filter(filtered, True)

        # Apply exclude compromised filter
        if self.exclude_compromised.get():
            filtered = filters.apply_exclude_compromised_filter(filtered, True)

        self.filtered_servers = filtered

        # Update table display using table module
        table.update_table_display(self.tree, self.filtered_servers, self.settings_manager)

        # Update count display
        self.count_label.configure(
            text=f"Showing: {len(self.filtered_servers)} of {len(self.all_servers)} servers"
        )
        self._set_empty_state_hint(self._resolve_empty_state_hint())

        self._update_action_buttons_state()
        self._persist_filter_preferences()

    def _load_data(self) -> None:
        """Load server data from database."""
        try:
            self._db_available = bool(self.db_reader and self.db_reader.is_database_available())
            if not self._db_available:
                self.all_servers = []
                self.filtered_servers = []
                table.update_table_display(self.tree, self.filtered_servers, self.settings_manager)
                self.count_label.configure(text="Total: 0 servers")
                self._set_empty_state_hint(self.HINT_NO_DATABASE)
                self._update_action_buttons_state()
                return

            # Get last scan time from scan manager
            scan_manager = get_scan_manager()
            self.last_scan_time = scan_manager.get_last_scan_time()

            # Get all servers (no pagination limit) — unified S+F rows
            servers, total_count = self.db_reader.get_protocol_server_list(
                limit=None,
                offset=0
            )

            # Attach denied share counts — SMB rows only (FTP has no share_access data)
            try:
                denied_map = self.db_reader.get_denied_share_counts()
            except Exception:
                denied_map = {}

            for server in servers:
                if server.get("host_type", "S") == "S":
                    ip = server.get("ip_address")
                    server["denied_shares_count"] = denied_map.get(ip, 0) if ip else 0
                else:
                    server["denied_shares_count"] = 0

            self.all_servers = servers
            self._attach_probe_status(self.all_servers)

            # Do NOT preload denied share lists for every server here.
            # For large datasets this results in thousands of per-server DB queries
            # on the UI thread and can make the window appear permanently "Loading...".
            # Denied share lists are fetched lazily when a details popup is opened.

            # Set initial date filter if requested
            if self.filter_recent and self.last_scan_time:
                self.date_filter.set("Since Last Scan")

            # Reset sort state for fresh dataset
            self._reset_sort_state()

            # Repopulate country filter with fresh data
            # This handles new scans or deletions that change available countries
            # Note: _populate_country_filter preserves current selections
            self._populate_country_filter()

            # Apply initial filters and display data
            self._apply_filters()

            # Update count display
            self.count_label.configure(text=f"Total: {total_count} servers")

        except Exception as e:
            messagebox.showerror(
                "Data Loading Error",
                f"Failed to load server data:\n{str(e)}",
                parent=self.window
            )

    def _reset_sort_state(self) -> None:
        """Reset sort state and restore all headers to original text."""
        # Restore all headers to original text
        for column, original_text in self.original_headers.items():
            self.tree.heading(column, text=original_text)

        # Clear sort state
        self.current_sort_column = None
        self.current_sort_direction = None

    # Event handlers
    def _on_selection_changed(self) -> None:
        """Handle table selection changes."""
        selected_items = self.tree.selection()
        selected_count = len(selected_items)

        if selected_count == 0:
            self.selection_label.configure(text="No selection")
        elif selected_count == 1:
            self.selection_label.configure(text="1 server selected")
        else:
            self.selection_label.configure(text=f"{selected_count} servers selected")

        self._update_action_buttons_state()

    def _apply_flag_toggle(self, row_key: str, field: str, new_value: int) -> None:
        """
        Persist a favorite/avoid toggle for a protocol row.

        Steps:
          a) look up server by row_key
          b) optimistically update in-memory dict
          c) persist via upsert_user_flags_for_host (correct per-protocol table)
          d) on any failure, revert both in-memory and UI icon
        """
        server = next((s for s in self.all_servers if s.get("row_key") == row_key), None)
        if not server:
            # Row not in memory — revert icon that table.py already flipped
            if self.tree and self.tree.exists(row_key):
                if field == "favorite":
                    self.tree.set(row_key, "favorite", "✔" if not new_value else "○")
                elif field == "avoid":
                    self.tree.set(row_key, "avoid", "✖" if not new_value else "○")
            return

        ip = server.get("ip_address", "")
        host_type = server.get("host_type", "S")
        old_value = server.get(field, 0)

        # Optimistically update in-memory state
        server[field] = new_value

        try:
            self.db_reader.upsert_user_flags_for_host(
                ip,
                host_type,
                protocol_server_id=server.get("protocol_server_id"),
                port=server.get("port"),
                **{field: bool(new_value)},
            )
        except Exception as exc:
            _logger.warning("Flag toggle DB write failed for %s (%s): %s", row_key, field, exc)
            # Revert in-memory state
            server[field] = old_value
            # Revert the tree icon
            if self.tree and self.tree.exists(row_key):
                if field == "favorite":
                    self.tree.set(row_key, "favorite", "✔" if old_value else "○")
                elif field == "avoid":
                    self.tree.set(row_key, "avoid", "✖" if old_value else "○")

    def _on_double_click(self, event) -> None:
        """Handle double-click on table row using table module."""
        table.handle_double_click(
            self.tree, event, self.filtered_servers,
            self._show_server_detail_popup, self.window
        )

    def _on_treeview_click(self, event) -> None:
        """Handle treeview clicks using table module."""
        callbacks = {
            'on_favorites_filter_changed': self._apply_filters,
            'on_avoid_filter_changed':     self._apply_filters,
            'on_favorite_toggle':          lambda rk, v: self._apply_flag_toggle(rk, "favorite", v),
            'on_avoid_toggle':             lambda rk, v: self._apply_flag_toggle(rk, "avoid",    v),
        }
        table.handle_treeview_click(self.tree, event, self.settings_manager, callbacks)

    def _sort_by_column(self, column: str) -> None:
        """Sort table by specified column using table module."""
        self.current_sort_column, self.current_sort_direction = table.sort_table_by_column(
            self.tree, column, self.current_sort_column, self.current_sort_direction,
            self.original_headers, self.default_sort_directions
        )

    def _select_all(self, event=None) -> None:
        """Select all items in table."""
        table.select_all_items(self.tree)

    # Action handlers
    def _view_server_details(self) -> None:
        """Show detailed information for selected server."""
        selected_items = self.tree.selection()

        if not selected_items:
            messagebox.showwarning("No Selection", "Please select a server to view details.", parent=self.window)
            return

        if len(selected_items) > 1:
            messagebox.showwarning("Multiple Selection", "Please select only one server to view details.", parent=self.window)
            return

        # Get server data — item is the iid == row_key
        item = selected_items[0]
        row_key = item  # TreeView item ID == iid == row_key
        server_data = next(
            (server for server in self.filtered_servers if server.get("row_key") == row_key),
            None
        )

        if not server_data:
            messagebox.showerror("Error", "Server data not found.", parent=self.window)
            return

        # Show details using details module
        self._show_server_detail_popup(server_data)

    def _show_server_detail_popup(self, server_data: Dict[str, Any]) -> None:
        """Show server detail popup using details module."""
        ip_address = server_data.get("ip_address")
        if server_data.get("host_type", "S") == "S" and ip_address and "denied_shares_list" not in server_data:
            try:
                server_data["denied_shares_list"] = self.db_reader.get_denied_shares(ip_address)
            except Exception as exc:
                _logger.warning("Failed to fetch denied share list for %s: %s", ip_address, exc)
                server_data["denied_shares_list"] = []
        else:
            server_data.setdefault("denied_shares_list", [])

        details.show_server_detail_popup(
            self.window,
            server_data,
            self.theme,
            self.settings_manager,
            probe_status_callback=self._handle_probe_status_update,
            indicator_patterns=self.indicator_patterns,
            probe_callback=self._launch_probe_from_detail,
            extract_callback=self._launch_extract_from_detail,
            browse_callback=self._launch_browse_from_detail,
            rce_status_callback=self._handle_rce_status_update
        )

    def _export_selected_servers(self) -> None:
        """Export selected servers using export module."""
        selected_data = table.get_selected_server_data(self.tree, self.filtered_servers)
        if not selected_data:
            messagebox.showwarning("No Selection", "Please select servers to export.", parent=self.window)
            return

        export.show_export_menu(
            self.window, selected_data, "selected", self.theme, get_export_engine()
        )

    def _export_all_servers(self) -> None:
        """Export all filtered servers using export module."""
        if not self.filtered_servers:
            messagebox.showwarning("No Data", "No servers to export.")
            return

        export.show_export_menu(
            self.window, self.filtered_servers, "all", self.theme, get_export_engine()
        )

    # Batch + context actions are implemented in ServerListWindowActionsMixin


def open_server_list_window(parent: tk.Widget, db_reader: DatabaseReader,
                           window_data: Dict[str, Any] = None, settings_manager = None) -> 'ServerListWindow':
    """
    Open server list browser window.

    Args:
        parent: Parent widget
        db_reader: Database reader instance
        window_data: Optional data for window initialization
        settings_manager: Optional settings manager for favorites functionality

    Returns:
        ServerListWindow instance for tracking and reuse
    """
    return ServerListWindow(parent, db_reader, window_data, settings_manager)
