"""
Dirracuda Scan Dialog

Single-instance, non-blocking dialog for configuring and starting new SMB security scans.
Provides simple interface for country selection and configuration management.

Design Decision: Simple windowed approach focuses on essential parameters
while directing users to configuration editor for advanced settings.
"""

import tkinter as tk
from tkinter import ttk, simpledialog
from gui.utils import safe_messagebox as messagebox
import os
import sys
import json
import csv
import io
import webbrowser
from pathlib import Path
from typing import Optional, Callable, Dict, Any

from gui.utils.style import get_theme
from gui.utils.template_store import TemplateStore
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.components.scan_preflight import run_preflight


class ScanDialog:
    """
    Single-instance, non-blocking dialog for configuring and starting SMB scans.

    Provides interface for:
    - Optional country selection (global scan if empty)
    - Regional country selection via checkboxes
    - Configuration file path display and editing
    - Scan initiation with validation and complete options dict

    Design Pattern: Single-instance window with clear call-to-action flow
    that integrates with existing configuration and scan systems.
    Callback contract provides complete scan options dict to ensure
    compatibility with ScanManager expectations.
    """

    TEMPLATE_PLACEHOLDER_TEXT = "Select a template..."

    # Regional country code mappings
    REGIONS = {
        "Africa": ["AO", "BF", "BI", "BJ", "BW", "CD", "CF", "CG", "CI", "CM", "CV", "DJ", "DZ", "EG", "EH", "ER", "ET", "GA", "GH", "GM", "GN", "GQ", "GW", "KE", "KM", "LR", "LS", "LY", "MA", "MG", "ML", "MR", "MU", "MW", "MZ", "NA", "NE", "NG", "RE", "RW", "SC", "SD", "SH", "SL", "SN", "SO", "ST", "SZ", "TD", "TG", "TN", "TZ", "UG", "ZA", "ZM", "ZW"],
        "Asia": ["AE", "AF", "AM", "AZ", "BD", "BH", "BN", "BT", "CN", "GE", "HK", "ID", "IL", "IN", "IQ", "IR", "JO", "JP", "KG", "KH", "KP", "KR", "KW", "KZ", "LA", "LB", "LK", "MM", "MN", "MO", "MV", "MY", "NP", "OM", "PH", "PK", "PS", "QA", "SA", "SG", "SY", "TH", "TJ", "TL", "TM", "TR", "TW", "UZ", "VN", "YE"],
        "Europe": ["AD", "AL", "AT", "AX", "BA", "BE", "BG", "BY", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FO", "FR", "GB", "GI", "GR", "HR", "HU", "IE", "IM", "IS", "IT", "JE", "LI", "LT", "LU", "LV", "MC", "MD", "ME", "MK", "MT", "NL", "NO", "PL", "PT", "RO", "RS", "RU", "SE", "SI", "SK", "SM", "UA", "VA"],
        "North America": ["AG", "AI", "AW", "BB", "BL", "BM", "BQ", "BS", "BZ", "CA", "CR", "CU", "CW", "DM", "DO", "GD", "GL", "GP", "GT", "HN", "HT", "JM", "KN", "KY", "LC", "MF", "MQ", "MS", "MX", "NI", "PA", "PM", "PR", "SV", "SX", "TC", "TT", "US", "VC", "VG", "VI"],
        "Oceania": ["AS", "AU", "CK", "FJ", "FM", "GU", "KI", "MH", "MP", "NC", "NF", "NR", "NU", "NZ", "PF", "PG", "PN", "PW", "SB", "TK", "TO", "TV", "VU", "WF", "WS"],
        "South America": ["AR", "BO", "BR", "CL", "CO", "EC", "GY", "PE", "PY", "SR", "UY", "VE"]
    }
    
    def __init__(self, parent: tk.Widget, config_path: str,
                 config_editor_callback: Callable[[str], None],
                 scan_start_callback: Callable[[Dict[str, Any]], None],
                 backend_interface: Optional[Any] = None,
                 settings_manager: Optional[Any] = None):
        """
        Initialize scan dialog.

        Args:
            parent: Parent widget
            config_path: Path to configuration file
            config_editor_callback: Function to open config editor
            scan_start_callback: Function to start scan with scan options dict
            backend_interface: Optional backend interface for future use
            settings_manager: Optional settings manager for scan defaults
        """
        self.parent = parent
        self.config_path = Path(config_path).resolve()
        self.config_editor_callback = config_editor_callback
        self.scan_start_callback = scan_start_callback
        self.theme = get_theme()

        # Optional components for future use (prefixed to avoid static analyzer warnings)
        self._backend_interface = backend_interface
        self._settings_manager = settings_manager
        self.template_store = TemplateStore(settings_manager=settings_manager)

        # Dialog result
        self.result = None
        self.scan_options = None  # Replaced country_code with scan_options
        
        # UI components
        self.dialog = None
        self.content_canvas = None
        self.content_frame = None
        self.country_var = tk.StringVar()
        self.country_entry = None
        self.custom_filters_var = tk.StringVar()
        self.custom_filters_entry = None
        self.extension_count_label = None
        self.template_var = tk.StringVar()
        self.template_dropdown = None
        self._template_label_to_slug: Dict[str, str] = {}
        self._selected_template_slug: Optional[str] = None
        self._pending_template_slug = None

        # Region selection UI variables
        self.africa_var = tk.BooleanVar(value=False)
        self.asia_var = tk.BooleanVar(value=False)
        self.europe_var = tk.BooleanVar(value=False)
        self.north_america_var = tk.BooleanVar(value=False)
        self.oceania_var = tk.BooleanVar(value=False)
        self.south_america_var = tk.BooleanVar(value=False)

        # Advanced options UI variables
        self.max_results_var = tk.IntVar(value=1000)
        self.recent_hours_var = tk.StringVar()  # Empty means None/default
        self.rescan_all_var = tk.BooleanVar(value=False)
        self.rescan_failed_var = tk.BooleanVar(value=False)
        self.api_key_var = tk.StringVar()

        # Backend concurrency and rate limit controls
        self.discovery_concurrency_var = tk.StringVar()
        self.access_concurrency_var = tk.StringVar()
        self.rate_limit_delay_var = tk.StringVar()
        self.share_access_delay_var = tk.StringVar()

        # Verbose toggle for backend logging
        self.verbose_var = tk.BooleanVar(value=False)

        # Security mode toggle (default cautious)
        self.security_mode_var = tk.StringVar(value="cautious")
        self._security_mode_previous = "cautious"
        self._security_mode_guard = False

        # RCE vulnerability analysis toggle (default disabled)
        self.rce_enabled_var = tk.BooleanVar(value=False)

        # Bulk operation toggles (default disabled)
        self.bulk_probe_enabled_var = tk.BooleanVar(value=False)
        self.skip_indicator_extract_var = tk.BooleanVar(value=True)
        self.bulk_extract_enabled_var = tk.BooleanVar(value=False)

        self._concurrency_upper_limit = 256
        self._delay_upper_limit = 3600

        # Load backend defaults for concurrency and rate limits
        self._load_backend_defaults()

        # Load initial values from settings if available
        self._load_initial_values()
        self._security_mode_previous = (self.security_mode_var.get() or "cautious").lower()
        self.security_mode_var.trace_add("write", self._handle_security_mode_change)

        self._create_dialog()
    
    def _create_dialog(self) -> None:
        """Create the scan configuration dialog."""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Start New Scan")
        width, height = 1300, 1185
        self.dialog.geometry(f"{width}x{height}")
        self.dialog.resizable(True, True)
        
        # Apply theme
        self.theme.apply_to_widget(self.dialog, "main_window")
        
        # Keep tied to dashboard parent, but do not app-lock with grab_set().
        self.dialog.transient(self.parent)
        
        # Center dialog
        self._center_dialog()
        
        # Scrollable content area
        content_wrapper = tk.Frame(self.dialog, bg=self.theme.colors["primary_bg"])
        content_wrapper.pack(fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(content_wrapper, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.content_canvas = tk.Canvas(
            content_wrapper,
            highlightthickness=0,
            borderwidth=0,
            bg=self.theme.colors["primary_bg"],
            yscrollcommand=scrollbar.set
        )
        self.content_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.configure(command=self.content_canvas.yview)

        self.content_frame = tk.Frame(self.content_canvas, bg=self.theme.colors["primary_bg"])
        self.content_canvas.create_window((0, 0), window=self.content_frame, anchor="nw")

        self.content_frame.bind(
            "<Configure>",
            lambda e: self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all"))
        )
        for widget in (self.content_canvas, self.content_frame):
            widget.bind("<MouseWheel>", self._on_mousewheel)
            widget.bind("<Button-4>", self._on_mousewheel)  # Linux scroll up
            widget.bind("<Button-5>", self._on_mousewheel)  # Linux scroll down

        # Build UI inside scrollable area
        self._create_header()
        self._create_scan_options()
        self._create_config_section()
        self._create_button_panel()
        
        # Setup event handlers
        self._setup_event_handlers()

        # Focus on default field
        self._focus_initial_field()

        # Ensure dialog appears on top and gains focus (critical for VMs)
        ensure_dialog_focus(self.dialog, self.parent)

    def _center_dialog(self) -> None:
        """Center dialog on parent window."""
        self.dialog.update_idletasks()
        
        # Get parent position and size
        parent_x = self.parent.winfo_x()
        parent_y = self.parent.winfo_y()
        parent_width = self.parent.winfo_width()
        parent_height = self.parent.winfo_height()
        
        # Calculate center position
        width = self.dialog.winfo_width()
        height = self.dialog.winfo_height()
        x = parent_x + (parent_width // 2) - (width // 2)
        y = parent_y + (parent_height // 2) - (height // 2)
        
        self.dialog.geometry(f"{width}x{height}+{x}+{y}")

    def _on_mousewheel(self, event) -> None:
        """Enable mouse wheel scrolling for the dialog content."""
        if not self.content_canvas:
            return

        delta = 0
        if getattr(event, "delta", 0):
            delta = -1 if event.delta > 0 else 1
        elif getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1

        if delta:
            self.content_canvas.yview_scroll(delta, "units")

    def _handle_security_mode_change(self, *_args) -> None:
        """Prompt when switching into legacy mode."""
        new_value = (self.security_mode_var.get() or "cautious").lower()
        if new_value == self._security_mode_previous or self._security_mode_guard:
            return

        if new_value == "legacy":
            # Ensure dialog has focus before showing messagebox
            self.dialog.lift()
            self.dialog.focus_force()

            proceed = messagebox.askokcancel(
                "Enable Legacy Mode?",
                "Legacy mode allows SMB1/unsigned SMB sessions and bypasses built-in safeguards.\n"
                "Use only when you trust the target network.",
                parent=self.dialog,  # Ensure messagebox is parented to dialog
                icon='warning'
            )

            # Restore focus to dialog after messagebox closes
            ensure_dialog_focus(self.dialog, self.parent)

            if not proceed:
                self._security_mode_guard = True
                self.security_mode_var.set(self._security_mode_previous)
                self._security_mode_guard = False
                return
        elif new_value == "cautious":
            # Ensure dialog has focus before showing messagebox
            self.dialog.lift()
            self.dialog.focus_force()

            messagebox.showinfo(
                "Cautious Mode Reminder",
                "Cautious mode enforces SMB2+/SMB3 and signing. It's extra secure but may return fewer results.",
                parent=self.dialog  # Ensure messagebox is parented to dialog
            )

            # Restore focus to dialog after messagebox closes
            ensure_dialog_focus(self.dialog, self.parent)

        self._security_mode_previous = new_value
    
    def _create_header(self) -> None:
        """Create dialog header with title and description."""
        header_frame = tk.Frame(self.content_frame)
        self.theme.apply_to_widget(header_frame, "main_window")
        header_frame.pack(fill=tk.X, padx=20, pady=(15, 5))
        
        # Title
        title_label = self.theme.create_styled_label(
            header_frame,
            "🔍 Start New Security Scan",
            "heading"
        )
        title_label.pack(anchor="w")
        
        # Description
        desc_label = self.theme.create_styled_label(
            header_frame,
            "Configure and start a new SMB security scan to discover accessible shares.",
            "body",
            fg=self.theme.colors["text_secondary"]
        )
        desc_label.pack(anchor="w", pady=(5, 0))
    
    def _create_scan_options(self) -> None:
        """Create scan configuration options."""
        options_frame = tk.Frame(self.content_frame)
        self.theme.apply_to_widget(options_frame, "card")
        options_frame.pack(fill=tk.X, padx=20, pady=5)

        self._create_template_toolbar(options_frame)
        
        # Section title
        section_title = self.theme.create_styled_label(
            options_frame,
            "Scan Parameters",
            "heading"
        )
        section_title.pack(anchor="w", padx=15, pady=(10, 5))

        # Two-column layout to keep dialog height manageable
        columns_frame = tk.Frame(options_frame)
        self.theme.apply_to_widget(columns_frame, "card")
        columns_frame.pack(fill=tk.BOTH, padx=15, pady=(0, 10))

        left_column = tk.Frame(columns_frame)
        self.theme.apply_to_widget(left_column, "card")
        left_column.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        right_column = tk.Frame(columns_frame)
        self.theme.apply_to_widget(right_column, "card")
        right_column.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Left column: target scope + filters
        self._create_custom_filters_option(left_column)
        
        country_container = tk.Frame(left_column)
        self.theme.apply_to_widget(country_container, "card")
        country_container.pack(fill=tk.X, padx=15, pady=(0, 10))
        
        # Country label and input
        country_heading = self._create_accent_heading(
            country_container,
            "📌 Country Code (optional)"
        )
        country_heading.pack(fill=tk.X)
        
        # Country input with example
        country_input_frame = tk.Frame(country_container)
        self.theme.apply_to_widget(country_input_frame, "card")
        country_input_frame.pack(fill=tk.X, pady=(5, 0))
        
        self.country_entry = tk.Entry(
            country_input_frame,
            textvariable=self.country_var,
            width=10,
            font=self.theme.fonts["body"]
        )
        self.country_entry.pack(side=tk.LEFT)
        
        example_label = self.theme.create_styled_label(
            country_input_frame,
            "  (e.g., US, GB, CA — combines with region selections to the right)",
            "small",
            fg=self.theme.colors["text_secondary"]
        )
        example_label.configure(font=(self.theme.fonts["small"][0], self.theme.fonts["small"][1], "italic"))
        example_label.pack(side=tk.LEFT)
        
        self._create_region_selection(left_column)
        self._create_max_results_option(left_column)
        self._create_recent_hours_option(left_column)
        self._create_concurrency_options(left_column)
        self._create_rate_limit_options(left_column)

        # Right column: execution controls
        self._create_verbose_option(right_column)
        self._create_security_mode_option(right_column)
        self._create_bulk_probe_option(right_column)
        self._create_bulk_extract_option(right_column)
        self._create_rce_analysis_option(right_column)
        self._create_rescan_options(right_column)
        self._create_api_key_option(right_column)

    def _setup_event_handlers(self) -> None:
        """Setup event handlers."""
        self.dialog.protocol("WM_DELETE_WINDOW", self._cancel_scan)
        
        # Keyboard shortcuts
        self.dialog.bind("<Return>", lambda e: self._start_scan())
        self.dialog.bind("<Escape>", lambda e: self._cancel_scan())
        
        # Country input validation
        self.country_var.trace_add("write", self._validate_country_input)

        # Advanced options validation
        self.max_results_var.trace_add("write", self._validate_max_results)
        self.recent_hours_var.trace_add("write", self._validate_recent_hours)
    
    def _focus_initial_field(self) -> None:
        """Set initial focus to custom filters (fallback to country)."""
        target_entry = self.custom_filters_entry or self.country_entry
        if target_entry:
            target_entry.focus_set()

    def _get_selected_region_countries(self) -> list[str]:
        """Get all country codes from selected regions."""
        region_countries = []

        region_vars = [
            ("Africa", self.africa_var),
            ("Asia", self.asia_var),
            ("Europe", self.europe_var),
            ("North America", self.north_america_var),
            ("Oceania", self.oceania_var),
            ("South America", self.south_america_var)
        ]

        for region_name, region_var in region_vars:
            if region_var.get():
                region_countries.extend(self.REGIONS[region_name])

        return region_countries

    def _get_all_selected_countries(self, manual_input: str) -> tuple[list[str], str]:
        """Get combined list of manually entered and region-selected countries.

        Args:
            manual_input: Raw manual country input string

        Returns:
            Tuple of (combined_countries_list, error_message)
            If error_message is empty, validation succeeded
        """
        # Parse manual countries
        manual_countries, error_msg = self._parse_and_validate_countries(manual_input)
        if error_msg:
            return [], error_msg

        # Get region countries
        region_countries = self._get_selected_region_countries()

        # Combine and de-duplicate
        all_countries = list(set(manual_countries + region_countries))
        all_countries.sort()  # Sort for consistent ordering

        # Validate total count (prevent overwhelming the backend)
        max_countries = 100  # Reasonable limit
        if len(all_countries) > max_countries:
            return [], f"Too many countries selected ({len(all_countries)}). Maximum allowed: {max_countries}. Please reduce your selection."

        return all_countries, ""

    def _parse_and_validate_countries(self, country_input: str) -> tuple[list[str], str]:
        """Parse and validate comma-separated country codes.
        
        Args:
            country_input: Raw country input string
            
        Returns:
            Tuple of (valid_countries_list, error_message)
            If error_message is empty, validation succeeded
        """
        if not country_input.strip():
            return [], ""  # Empty input is valid (global scan)
        
        # Parse comma-separated countries
        countries = [country.strip().upper() for country in country_input.split(',')]
        valid_countries = []
        
        for country in countries:
            if not country:  # Skip empty entries from double commas
                continue
                
            # Validate individual country code
            if len(country) < 2 or len(country) > 3:
                return [], f"Invalid country code '{country}': must be 2-3 characters (e.g., US, GB, CA)"
            
            if not country.isalpha():
                return [], f"Invalid country code '{country}': must contain only letters (e.g., US, GB, CA)"
            
            valid_countries.append(country)
        
        if not valid_countries:
            return [], "Please enter at least one valid country code"
            
        return valid_countries, ""
    
    def _validate_country_input(self, *args) -> None:
        """Validate country code input in real-time."""
        country_input = self.country_var.get()
        
        # Allow empty (global scan)
        if not country_input.strip():
            return
        
        # Convert to uppercase but preserve formatting for user experience
        upper_input = country_input.upper()
        if upper_input != country_input:
            self.country_var.set(upper_input)

    def _validate_max_results(self, *args) -> None:
        """Validate max results input."""
        try:
            value = self.max_results_var.get()
            if value < 1 or value > 1000:
                # Reset to valid range
                valid_value = max(1, min(1000, value))
                self.max_results_var.set(valid_value)
        except tk.TclError:
            # Invalid integer, reset to default
            self.max_results_var.set(1000)

    def _validate_recent_hours(self, *args) -> None:
        """Validate recent hours input."""
        recent_text = self.recent_hours_var.get().strip()

        # Allow empty (means default)
        if not recent_text:
            return

        # Validate it's a positive integer
        try:
            value = int(recent_text)
            if value <= 0:
                # Clear invalid negative values
                self.recent_hours_var.set("")
        except ValueError:
            # Remove non-numeric characters, keep only digits
            cleaned = ''.join(c for c in recent_text if c.isdigit())
            self.recent_hours_var.set(cleaned)

    def _load_extension_filters(self) -> Dict[str, list]:
        """Load extension filters from config.json."""
        defaults = {
            "included_extensions": [],
            "excluded_extensions": []
        }

        config_path = None
        if self._settings_manager:
            config_path = self._settings_manager.get_setting('backend.config_path', None)
            if not config_path and hasattr(self._settings_manager, "get_smbseek_config_path"):
                config_path = self._settings_manager.get_smbseek_config_path()

        if not config_path:
            config_path = self.config_path

        if config_path and Path(config_path).exists():
            try:
                config_data = json.loads(Path(config_path).read_text(encoding="utf-8"))
                file_cfg = config_data.get("file_collection", {})
                defaults["included_extensions"] = file_cfg.get("included_extensions", [])
                defaults["excluded_extensions"] = file_cfg.get("excluded_extensions", [])
            except Exception:
                pass  # Use defaults on any error

        return defaults

    def _show_extension_table(self):
        """Show modal dialog with extension filter table."""
        filters = self._load_extension_filters()

        # Reuse the ExtensionEditorDialog from batch_extract_dialog
        try:
            from batch_extract_dialog import ExtensionEditorDialog
        except ImportError:
            from .batch_extract_dialog import ExtensionEditorDialog

        editor = ExtensionEditorDialog(
            parent=self.dialog,
            theme=self.theme,
            config_path=Path(self.config_path),
            initial_included=filters["included_extensions"],
            initial_excluded=filters["excluded_extensions"]
        )

        result = editor.show()

        # If user saved changes, update the summary label
        if result is not None and self.extension_count_label:
            included, excluded = result
            allowed_count = len(included)
            denied_count = len(excluded)
            allowed_text = "None configured" if allowed_count == 0 else f"{allowed_count} allowed"
            denied_text = "No restrictions" if denied_count == 0 else f"{denied_count} denied"
            self.extension_count_label.config(text=f"Extensions: {allowed_text}, {denied_text}")

    def _open_config_editor(self) -> None:
        """Open configuration editor."""
        try:
            self.config_editor_callback(str(self.config_path))
        except Exception as e:
            messagebox.showerror(
                "Configuration Editor Error",
                f"Failed to open configuration editor:\n{str(e)}\n\n"
                "Please ensure the configuration system is properly set up."
            )

    def _load_backend_defaults(self) -> None:
        """Load concurrency and rate limit defaults from the backend configuration."""
        def _coerce_int(value: Any, default: int, minimum: int = 0) -> int:
            try:
                int_value = int(value)
                if int_value < minimum:
                    raise ValueError
                return int_value
            except (TypeError, ValueError):
                return default

        config_data: Dict[str, Any] = {}

        if self._backend_interface is not None:
            try:
                config_data = self._backend_interface.load_effective_config()
            except Exception:
                config_data = {}

        if not config_data:
            try:
                with open(self.config_path, 'r', encoding='utf-8') as config_file:
                    config_data = json.load(config_file)
            except (FileNotFoundError, json.JSONDecodeError, PermissionError):
                config_data = {}

        if not isinstance(config_data, dict):
            config_data = {}

        discovery_defaults = config_data.get('discovery', {}) if isinstance(config_data.get('discovery'), dict) else {}
        access_defaults = config_data.get('access', {}) if isinstance(config_data.get('access'), dict) else {}
        connection_defaults = config_data.get('connection', {}) if isinstance(config_data.get('connection'), dict) else {}

        discovery_value = _coerce_int(discovery_defaults.get('max_concurrent_hosts'), 1, minimum=1)
        access_value = _coerce_int(access_defaults.get('max_concurrent_hosts'), 1, minimum=1)
        rate_limit_value = _coerce_int(connection_defaults.get('rate_limit_delay'), 1, minimum=0)
        share_delay_value = _coerce_int(connection_defaults.get('share_access_delay'), 1, minimum=0)

        self.discovery_concurrency_var.set(str(discovery_value))
        self.access_concurrency_var.set(str(access_value))
        self.rate_limit_delay_var.set(str(rate_limit_value))
        self.share_access_delay_var.set(str(share_delay_value))

    def _parse_positive_int(self, value_str: str, field_name: str, *, minimum: int = 0,
                             maximum: Optional[int] = None) -> int:
        """Parse and validate positive integers for numeric fields."""
        if value_str == "":
            raise ValueError(f"{field_name} is required.")

        try:
            value = int(value_str)
        except ValueError:
            raise ValueError(f"{field_name} must be a whole number.")

        if value < minimum:
            raise ValueError(f"{field_name} must be at least {minimum}.")

        if maximum is not None and value > maximum:
            raise ValueError(f"{field_name} must be {maximum} or less.")

        return value

    def _validate_integer_input(self, proposed: str) -> bool:
        """Allow only positive integer characters during entry editing."""
        if proposed == "":
            return True
        return proposed.isdigit()

    def _build_scan_options(self, country_param: Optional[str]) -> Dict[str, Any]:
        """
        Build complete scan options dict with type-safe settings extraction.

        Args:
            country_param: Country code(s) from user input

        Returns:
            Complete scan options dict with all keys ScanManager expects
        """
        # Get values from UI (user's current selections)
        max_results = self.max_results_var.get()

        # Handle recent hours (empty string means None)
        recent_hours_text = self.recent_hours_var.get().strip()
        recent_hours = int(recent_hours_text) if recent_hours_text else None

        rescan_all = self.rescan_all_var.get()
        rescan_failed = self.rescan_failed_var.get()
        security_mode = (self.security_mode_var.get() or "cautious").strip().lower()
        if security_mode not in {"cautious", "legacy"}:
            security_mode = "cautious"
        verbose_enabled = bool(self.verbose_var.get())

        # Handle API key (empty string means None)
        api_key = self.api_key_var.get().strip()
        api_key = api_key if api_key else None

        # Handle custom filters
        custom_filters = self.custom_filters_var.get().strip()

        discovery_concurrency = self._parse_positive_int(
            self.discovery_concurrency_var.get().strip(),
            "Discovery max concurrent hosts",
            minimum=1,
            maximum=self._concurrency_upper_limit
        )

        access_concurrency = self._parse_positive_int(
            self.access_concurrency_var.get().strip(),
            "Access max concurrent hosts",
            minimum=1,
            maximum=self._concurrency_upper_limit
        )

        rate_limit_delay = self._parse_positive_int(
            self.rate_limit_delay_var.get().strip(),
            "Rate limit delay (seconds)",
            minimum=0,
            maximum=self._delay_upper_limit
        )

        share_access_delay = self._parse_positive_int(
            self.share_access_delay_var.get().strip(),
            "Share access delay (seconds)",
            minimum=0,
            maximum=self._delay_upper_limit
        )

        # Save selections back to settings for next time
        if self._settings_manager is not None:
            try:
                self._settings_manager.set_setting('scan_dialog.max_shodan_results', max_results)
                self._settings_manager.set_setting('scan_dialog.recent_hours', recent_hours)
                self._settings_manager.set_setting('scan_dialog.rescan_all', rescan_all)
                self._settings_manager.set_setting('scan_dialog.rescan_failed', rescan_failed)
                self._settings_manager.set_setting('scan_dialog.api_key_override', api_key or '')
                self._settings_manager.set_setting('scan_dialog.custom_filters', custom_filters)
                # Save only manually entered country codes, not region-selected ones
                manual_country_input = self.country_var.get().strip()
                self._settings_manager.set_setting('scan_dialog.country_code', manual_country_input)
                self._settings_manager.set_setting('scan_dialog.discovery_max_concurrency', discovery_concurrency)
                self._settings_manager.set_setting('scan_dialog.access_max_concurrency', access_concurrency)
                self._settings_manager.set_setting('scan_dialog.rate_limit_delay', rate_limit_delay)
                self._settings_manager.set_setting('scan_dialog.share_access_delay', share_access_delay)
                self._settings_manager.set_setting('scan_dialog.security_mode', security_mode)
                self._settings_manager.set_setting('scan_dialog.verbose', verbose_enabled)
                self._settings_manager.set_setting('scan_dialog.rce_enabled', self.rce_enabled_var.get())
                self._settings_manager.set_setting('scan_dialog.bulk_probe_enabled', self.bulk_probe_enabled_var.get())
                self._settings_manager.set_setting('scan_dialog.bulk_extract_enabled', self.bulk_extract_enabled_var.get())
                self._settings_manager.set_setting('scan_dialog.bulk_extract_skip_indicators', self.skip_indicator_extract_var.get())

                # Save region selections
                self._settings_manager.set_setting('scan_dialog.region_africa', self.africa_var.get())
                self._settings_manager.set_setting('scan_dialog.region_asia', self.asia_var.get())
                self._settings_manager.set_setting('scan_dialog.region_europe', self.europe_var.get())
                self._settings_manager.set_setting('scan_dialog.region_north_america', self.north_america_var.get())
                self._settings_manager.set_setting('scan_dialog.region_oceania', self.oceania_var.get())
                self._settings_manager.set_setting('scan_dialog.region_south_america', self.south_america_var.get())
            except Exception:
                pass  # Don't fail scan if settings save fails

        # Build complete scan options dict
        scan_options = {
            'country': country_param,
            'max_shodan_results': max_results,
            'recent_hours': recent_hours,
            'rescan_all': rescan_all,
            'rescan_failed': rescan_failed,
            'api_key_override': api_key,
            'custom_filters': custom_filters,
            'discovery_max_concurrent_hosts': discovery_concurrency,
            'access_max_concurrent_hosts': access_concurrency,
            'rate_limit_delay': rate_limit_delay,
            'share_access_delay': share_access_delay,
            'security_mode': security_mode,
            'verbose': verbose_enabled,
            'rce_enabled': self.rce_enabled_var.get(),
            'bulk_probe_enabled': self.bulk_probe_enabled_var.get(),
            'bulk_extract_enabled': self.bulk_extract_enabled_var.get(),
            'bulk_extract_skip_indicators': self.skip_indicator_extract_var.get()
        }

        return scan_options

    def _load_initial_values(self) -> None:
        """Load initial values from settings manager into UI variables."""
        if self._settings_manager is not None:
            try:
                # Load saved settings into UI variables
                max_results = int(self._settings_manager.get_setting('scan_dialog.max_shodan_results', 1000))
                recent_hours = self._settings_manager.get_setting('scan_dialog.recent_hours', None)
                rescan_all = bool(self._settings_manager.get_setting('scan_dialog.rescan_all', False))
                rescan_failed = bool(self._settings_manager.get_setting('scan_dialog.rescan_failed', False))
                api_key = str(self._settings_manager.get_setting('scan_dialog.api_key_override', ''))
                custom_filters = str(self._settings_manager.get_setting('scan_dialog.custom_filters', ''))
                country_code = str(self._settings_manager.get_setting('scan_dialog.country_code', ''))

                discovery_concurrency = self._settings_manager.get_setting('scan_dialog.discovery_max_concurrency', None)
                access_concurrency = self._settings_manager.get_setting('scan_dialog.access_max_concurrency', None)
                rate_limit_delay = self._settings_manager.get_setting('scan_dialog.rate_limit_delay', None)
                share_access_delay = self._settings_manager.get_setting('scan_dialog.share_access_delay', None)
                security_mode = self._settings_manager.get_setting('scan_dialog.security_mode', 'cautious')
                verbose_enabled = bool(self._settings_manager.get_setting('scan_dialog.verbose', False))

                # Set UI variables
                self.max_results_var.set(max_results)
                self.recent_hours_var.set(str(recent_hours) if recent_hours is not None else '')
                self.rescan_all_var.set(rescan_all)
                self.rescan_failed_var.set(rescan_failed)
                self.api_key_var.set(api_key)
                self.custom_filters_var.set(custom_filters)
                self.country_var.set(country_code)

                if discovery_concurrency is not None:
                    self.discovery_concurrency_var.set(str(discovery_concurrency))
                if access_concurrency is not None:
                    self.access_concurrency_var.set(str(access_concurrency))
                if rate_limit_delay is not None:
                    self.rate_limit_delay_var.set(str(rate_limit_delay))
                if share_access_delay is not None:
                    self.share_access_delay_var.set(str(share_access_delay))
                if security_mode in ("cautious", "legacy"):
                    self.security_mode_var.set(security_mode)
                self.verbose_var.set(verbose_enabled)

                # Load RCE analysis setting
                rce_enabled = bool(self._settings_manager.get_setting('scan_dialog.rce_enabled', False))
                self.rce_enabled_var.set(rce_enabled)

                # Load bulk operation settings
                bulk_probe_enabled = bool(self._settings_manager.get_setting('scan_dialog.bulk_probe_enabled', False))
                bulk_extract_enabled = bool(self._settings_manager.get_setting('scan_dialog.bulk_extract_enabled', False))
                skip_extract_indicators = bool(self._settings_manager.get_setting('scan_dialog.bulk_extract_skip_indicators', True))
                self.bulk_probe_enabled_var.set(bulk_probe_enabled)
                self.bulk_extract_enabled_var.set(bulk_extract_enabled)
                self.skip_indicator_extract_var.set(skip_extract_indicators)

                # Load region selections
                africa = bool(self._settings_manager.get_setting('scan_dialog.region_africa', False))
                asia = bool(self._settings_manager.get_setting('scan_dialog.region_asia', False))
                europe = bool(self._settings_manager.get_setting('scan_dialog.region_europe', False))
                north_america = bool(self._settings_manager.get_setting('scan_dialog.region_north_america', False))
                oceania = bool(self._settings_manager.get_setting('scan_dialog.region_oceania', False))
                south_america = bool(self._settings_manager.get_setting('scan_dialog.region_south_america', False))

                # Set region variables
                self.africa_var.set(africa)
                self.asia_var.set(asia)
                self.europe_var.set(europe)
                self.north_america_var.set(north_america)
                self.oceania_var.set(oceania)
                self.south_america_var.set(south_america)
            except Exception:
                # Fall back to defaults if settings loading fails
                pass

        # (Query preview removed)

    def _persist_quick_settings(self) -> None:
        """
        Best-effort persistence for worker counts and security mode so they survive
        app restarts even when the user cancels instead of starting a scan.
        """
        if self._settings_manager is None:
            return

        def _coerce_int(value: str, minimum: int, maximum: int) -> Optional[int]:
            try:
                num = int(str(value).strip())
                if num < minimum or num > maximum:
                    return None
                return num
            except Exception:
                return None

        try:
            discovery_val = _coerce_int(self.discovery_concurrency_var.get(), 1, self._concurrency_upper_limit)
            access_val = _coerce_int(self.access_concurrency_var.get(), 1, self._concurrency_upper_limit)
            if discovery_val is not None:
                self._settings_manager.set_setting('scan_dialog.discovery_max_concurrency', discovery_val)
            if access_val is not None:
                self._settings_manager.set_setting('scan_dialog.access_max_concurrency', access_val)

            security_mode = (self.security_mode_var.get() or "cautious").strip().lower()
            if security_mode not in {"cautious", "legacy"}:
                security_mode = "cautious"
            self._settings_manager.set_setting('scan_dialog.security_mode', security_mode)
            self._settings_manager.set_setting('scan_dialog.verbose', bool(self.verbose_var.get()))
        except Exception:
            # Persistence is best-effort; ignore failures here
            pass

    def _start_scan(self) -> None:
        """Validate inputs and start the scan with configured parameters."""
        country_input = self.country_var.get().strip()

        # Get combined countries from manual input and region selections
        countries, error_msg = self._get_all_selected_countries(country_input)

        if error_msg:
            messagebox.showerror(
                "Invalid Country Selection",
                error_msg + "\n\nTip: You can combine manual country codes with region selections.",
                parent=self.dialog
            )
            self.country_entry.focus_set()
            return

        # Prepare country parameter for backend (comma-separated string or None)
        if countries:
            country_param = ",".join(countries)

            # Create descriptive scan description
            manual_countries, _ = self._parse_and_validate_countries(country_input)
            region_countries = self._get_selected_region_countries()

            if manual_countries and region_countries:
                scan_desc = f"Manual: {len(manual_countries)}, Regions: {len(region_countries)}, Total: {len(countries)} countries"
            elif manual_countries:
                if len(manual_countries) == 1:
                    scan_desc = f"country: {manual_countries[0]}"
                else:
                    scan_desc = f"countries: {', '.join(manual_countries)}"
            else:
                scan_desc = f"regions: {len(countries)} countries total"
        else:
            country_param = None
            scan_desc = "global (all countries)"
            
        try:
            # Build complete scan options dict
            scan_options = self._build_scan_options(country_param)

            preflight_result = run_preflight(
                self.dialog,
                self.theme,
                self._settings_manager,
                scan_options,
                scan_desc
            )
            if preflight_result is None:
                return
            scan_options = preflight_result

            # Set results and close dialog
            self.result = "start"
            self.scan_options = scan_options

            # Start the scan with complete options dict
            self.scan_start_callback(scan_options)

            # Close dialog
            self.dialog.destroy()
        except ValueError as e:
            messagebox.showerror(
                "Invalid Input",
                str(e),
                parent=self.dialog
            )
            return
        except Exception as e:
            # Handle scan start errors gracefully
            messagebox.showerror(
                "Scan Start Error",
                f"Failed to start scan:\n{str(e)}\n\n"
                "Please check that the backend is properly configured and try again.",
                parent=self.dialog
            )
            # Don't close dialog so user can try again
    
    def _cancel_scan(self) -> None:
        """Cancel scan and close dialog."""
        # Persist user tweaks even if they don't start a scan
        self._persist_quick_settings()
        self.result = "cancel"
        self.dialog.destroy()

    def focus_dialog(self) -> None:
        """Bring the existing dialog instance to front."""
        try:
            self.dialog.deiconify()
            ensure_dialog_focus(self.dialog, self.parent)
        except Exception:
            pass
    
    def show(self) -> Optional[str]:
        """
        Show dialog and wait for result.
        
        Returns:
            "start" if scan was started, "cancel" if cancelled, None if closed
        """
        # Wait for dialog to close
        self.parent.wait_window(self.dialog)
        return self.result


def show_scan_dialog(parent: tk.Widget, config_path: str,
                    config_editor_callback: Callable[[str], None],
                    scan_start_callback: Callable[[Dict[str, Any]], None],
                    backend_interface: Optional[Any] = None,
                    settings_manager: Optional[Any] = None) -> Optional[str]:
    """
    Show scan configuration dialog as a single-instance window.

    Args:
        parent: Parent widget
        config_path: Path to configuration file
        config_editor_callback: Function to open config editor
        scan_start_callback: Function to start scan with scan options dict
        backend_interface: Optional backend interface for future use
        settings_manager: Optional settings manager for scan defaults

    Returns:
        Dialog result ("start", "cancel", or None)
    """
    global _ACTIVE_SCAN_DIALOG
    if _dialog_instance_is_live(_ACTIVE_SCAN_DIALOG):
        _ACTIVE_SCAN_DIALOG.focus_dialog()
        return None

    dialog = ScanDialog(parent, config_path, config_editor_callback, scan_start_callback,
                       backend_interface, settings_manager)
    _ACTIVE_SCAN_DIALOG = dialog
    try:
        return dialog.show()
    finally:
        if _ACTIVE_SCAN_DIALOG is dialog:
            _ACTIVE_SCAN_DIALOG = None


_ACTIVE_SCAN_DIALOG: Optional[ScanDialog] = None


def _dialog_instance_is_live(instance: Optional[ScanDialog]) -> bool:
    if instance is None:
        return False
    try:
        return bool(instance.dialog.winfo_exists())
    except Exception:
        return False

from gui.components.scan_dialog_layout import bind_scan_dialog_layout_methods

bind_scan_dialog_layout_methods(ScanDialog)
