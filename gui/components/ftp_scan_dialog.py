"""
FTP Scan Dialog

Modal dialog for configuring and starting FTP scans.
Styled consistently with the SMB ScanDialog.

Design: Compact two-column layout covering FTP-specific parameters
(country/region, max results, API key, concurrency, timeouts, verbose).
Uses the same region map and country validation logic as ScanDialog.
"""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import json
import webbrowser
from pathlib import Path
from typing import Optional, Callable, Dict, Any

from gui.utils.style import get_theme
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.template_store import TemplateStore
from gui.components.scan_dialog import ScanDialog
from gui.components.ftp_scan_template_mixin import _FtpScanTemplateMixin
from gui.components.ftp_scan_options_mixin import _FtpScanOptionsMixin, _CONCURRENCY_UPPER, _TIMEOUT_UPPER

# Reuse the canonical region map — no forked copy.
REGIONS = ScanDialog.REGIONS

_MAX_COUNTRIES = 100


class FtpScanDialog(_FtpScanTemplateMixin, _FtpScanOptionsMixin):
    """
    Modal dialog for configuring and starting FTP scans.

    Collects FTP-specific launch parameters and passes a scan_options dict
    to scan_start_callback on Start.  All runtime overrides (concurrency,
    timeouts, API key, max results) are passed through to _ftp_scan_worker
    via the existing temporary-config-override mechanism in scan_manager.
    """

    TEMPLATE_PLACEHOLDER_TEXT = "Select a template..."

    def __init__(
        self,
        parent: tk.Widget,
        config_path: str,
        scan_start_callback: Callable[[Dict[str, Any]], None],
        settings_manager: Optional[Any] = None,
        config_editor_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.parent = parent
        self.config_path = Path(config_path).resolve()
        self.scan_start_callback = scan_start_callback
        self._settings_manager = settings_manager
        self.config_editor_callback = config_editor_callback
        self.template_store = TemplateStore(settings_manager=settings_manager)
        self.theme = get_theme()

        self.result = None
        self.dialog = None
        self.country_entry = None
        self.region_status_label = None
        self.custom_filters_entry = None
        self.template_dropdown = None
        self.delete_template_button = None

        # --- country / region ---
        self.country_var = tk.StringVar()
        self.africa_var = tk.BooleanVar(value=False)
        self.asia_var = tk.BooleanVar(value=False)
        self.europe_var = tk.BooleanVar(value=False)
        self.north_america_var = tk.BooleanVar(value=False)
        self.oceania_var = tk.BooleanVar(value=False)
        self.south_america_var = tk.BooleanVar(value=False)

        # --- scan options ---
        self.custom_filters_var = tk.StringVar()
        self.max_results_var = tk.IntVar(value=1000)
        self.api_key_var = tk.StringVar()
        self.discovery_concurrency_var = tk.StringVar()
        self.access_concurrency_var = tk.StringVar()
        self.connect_timeout_var = tk.StringVar()
        self.auth_timeout_var = tk.StringVar()
        self.listing_timeout_var = tk.StringVar()
        self.verbose_var = tk.BooleanVar(value=False)
        self.bulk_probe_enabled_var = tk.BooleanVar(value=False)
        self.template_var = tk.StringVar()
        self._template_label_to_slug: Dict[str, str] = {}
        self._selected_template_slug: Optional[str] = None

        self._load_config_defaults()
        self._load_initial_values()
        self._create_dialog()

    # ------------------------------------------------------------------
    # Dialog construction
    # ------------------------------------------------------------------

    def _create_dialog(self) -> None:
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Start FTP Scan")
        self.dialog.geometry("1010x825")
        self.dialog.resizable(True, True)
        self.theme.apply_to_widget(self.dialog, "main_window")
        self.dialog.transient(self.parent)
        self.dialog.grab_set()
        self._center_dialog()

        # Scrollable content
        wrapper = tk.Frame(self.dialog, bg=self.theme.colors["primary_bg"])
        wrapper.pack(fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(wrapper, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas = tk.Canvas(
            wrapper,
            highlightthickness=0,
            borderwidth=0,
            bg=self.theme.colors["primary_bg"],
            yscrollcommand=scrollbar.set,
        )
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.configure(command=self._canvas.yview)
        self._content = tk.Frame(self._canvas, bg=self.theme.colors["primary_bg"])
        self._canvas.create_window((0, 0), window=self._content, anchor="nw")
        self._content.bind(
            "<Configure>",
            lambda _e: self._canvas.configure(
                scrollregion=self._canvas.bbox("all")
            ),
        )
        for w in (self._canvas, self._content):
            w.bind("<MouseWheel>", self._on_mousewheel)
            w.bind("<Button-4>", self._on_mousewheel)
            w.bind("<Button-5>", self._on_mousewheel)

        self._create_header()
        self._create_options()
        self._create_config_section()
        self._create_button_panel()

        self.dialog.protocol("WM_DELETE_WINDOW", self._cancel)
        self.dialog.bind("<Return>", lambda _e: self._start())
        self.dialog.bind("<Escape>", lambda _e: self._cancel())
        self.country_var.trace_add("write", self._validate_country_input)
        self.max_results_var.trace_add("write", self._validate_max_results)

        target_entry = self.custom_filters_entry or self.country_entry
        if target_entry:
            target_entry.focus_set()
        ensure_dialog_focus(self.dialog, self.parent)

    def _center_dialog(self) -> None:
        self.dialog.update_idletasks()
        px, py = self.parent.winfo_x(), self.parent.winfo_y()
        pw, ph = self.parent.winfo_width(), self.parent.winfo_height()
        w, h = self.dialog.winfo_width(), self.dialog.winfo_height()
        self.dialog.geometry(f"{w}x{h}+{px + pw // 2 - w // 2}+{py + ph // 2 - h // 2}")

    def _on_mousewheel(self, event) -> None:
        delta = 0
        if getattr(event, "delta", 0):
            delta = -1 if event.delta > 0 else 1
        elif getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        if delta:
            self._canvas.yview_scroll(delta, "units")

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def _create_header(self) -> None:
        frame = tk.Frame(self._content)
        self.theme.apply_to_widget(frame, "main_window")
        frame.pack(fill=tk.X, padx=20, pady=(15, 5))
        self.theme.create_styled_label(frame, "📡 Start FTP Scan", "heading").pack(anchor="w")
        self.theme.create_styled_label(
            frame,
            "Configure and start a new FTP scan to discover accessible servers.",
            "body",
            fg=self.theme.colors["text_secondary"],
        ).pack(anchor="w", pady=(5, 0))

    # ------------------------------------------------------------------
    # Options area
    # ------------------------------------------------------------------

    def _create_options(self) -> None:
        options_frame = tk.Frame(self._content)
        self.theme.apply_to_widget(options_frame, "card")
        options_frame.pack(fill=tk.X, padx=20, pady=5)

        self._create_template_toolbar(options_frame)

        self.theme.create_styled_label(
            options_frame, "Scan Parameters", "heading"
        ).pack(anchor="w", padx=15, pady=(10, 5))

        columns = tk.Frame(options_frame)
        self.theme.apply_to_widget(columns, "card")
        columns.pack(fill=tk.BOTH, padx=15, pady=(0, 10))

        left = tk.Frame(columns)
        self.theme.apply_to_widget(left, "card")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        right = tk.Frame(columns)
        self.theme.apply_to_widget(right, "card")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Left column
        self._create_custom_filters_option(left)
        self._create_country_option(left)
        self._create_region_selection(left)
        self._create_max_results_option(left)
        self._create_api_key_option(left)

        # Right column
        self._create_verbose_option(right)
        self._create_bulk_probe_option(right)
        self._create_concurrency_options(right)
        self._create_timeout_options(right)

    # ------------------------------------------------------------------
    # Helpers shared with inner methods
    # ------------------------------------------------------------------

    def _create_accent_heading(self, parent: tk.Widget, text: str) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            anchor="w",
            padx=10,
            pady=4,
            bg=self.theme.colors["accent"],
            fg="white",
            font=self.theme.fonts["heading"],
        )

    def _validate_integer_input(self, proposed: str) -> bool:
        return proposed == "" or proposed.isdigit()

    # ------------------------------------------------------------------
    # Country / region
    # ------------------------------------------------------------------

    def _create_custom_filters_option(self, parent: tk.Frame) -> None:
        """Create custom Shodan filters input option with helper link."""
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        heading_frame = tk.Frame(container)
        self.theme.apply_to_widget(heading_frame, "card")
        heading_frame.pack(fill=tk.X)

        heading = self._create_accent_heading(heading_frame, "🔍 Custom Shodan Filters (optional)")
        heading.pack(side=tk.LEFT)

        help_link = tk.Label(
            heading_frame,
            text="Filter Reference",
            fg="#0066cc",
            cursor="hand2",
            font=self.theme.fonts["small"],
        )
        help_link.pack(side=tk.LEFT, padx=(10, 0))
        help_link.bind("<Button-1>", lambda _e: webbrowser.open("https://www.shodan.io/search/filters"))

        input_frame = tk.Frame(container)
        self.theme.apply_to_widget(input_frame, "card")
        input_frame.pack(fill=tk.X, pady=(5, 0))

        self.custom_filters_entry = tk.Entry(
            input_frame,
            textvariable=self.custom_filters_var,
            width=50,
            font=self.theme.fonts["body"],
        )
        self.custom_filters_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        desc = self.theme.create_styled_label(
            container,
            '(e.g., "port:21 has_screenshot:true" or "org:\\"Example ISP\\"" — appended to base query)',
            "small",
            fg=self.theme.colors["text_secondary"],
        )
        desc.pack(anchor="w", pady=(5, 0))

    def _create_country_option(self, parent: tk.Frame) -> None:
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        heading = self._create_accent_heading(container, "📌 Country Code (optional)")
        heading.pack(fill=tk.X)

        input_frame = tk.Frame(container)
        self.theme.apply_to_widget(input_frame, "card")
        input_frame.pack(fill=tk.X, pady=(5, 0))

        self.country_entry = tk.Entry(
            input_frame, textvariable=self.country_var, width=10,
            font=self.theme.fonts["body"]
        )
        self.country_entry.pack(side=tk.LEFT)

        hint = self.theme.create_styled_label(
            input_frame,
            "  (e.g., US, GB, CA — combines with region selections below)",
            "small",
            fg=self.theme.colors["text_secondary"],
        )
        hint.configure(font=(self.theme.fonts["small"][0], self.theme.fonts["small"][1], "italic"))
        hint.pack(side=tk.LEFT)

    def _create_region_selection(self, parent: tk.Frame) -> None:
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        self._create_accent_heading(container, "📍 Region Selection").pack(fill=tk.X, pady=(0, 10))

        checkboxes_frame = tk.Frame(container)
        self.theme.apply_to_widget(checkboxes_frame, "card")
        checkboxes_frame.pack(fill=tk.X, pady=(5, 5))

        region_vars = [
            ("Africa", self.africa_var),
            ("Asia", self.asia_var),
            ("Europe", self.europe_var),
            ("North America", self.north_america_var),
            ("Oceania", self.oceania_var),
            ("South America", self.south_america_var),
        ]
        for i, (name, var) in enumerate(region_vars):
            cb = tk.Checkbutton(
                checkboxes_frame,
                text=f"{name} ({len(REGIONS[name])})",
                variable=var,
                font=self.theme.fonts["small"],
                command=self._update_region_status,
            )
            self.theme.apply_to_widget(cb, "checkbox")
            cb.grid(row=i // 3, column=i % 3, sticky="w", padx=5, pady=2)

        bottom = tk.Frame(container)
        self.theme.apply_to_widget(bottom, "card")
        bottom.pack(fill=tk.X, pady=(5, 10))

        actions = tk.Frame(bottom)
        self.theme.apply_to_widget(actions, "card")
        actions.pack(side=tk.LEFT)

        for label, cmd in [("Select All", self._select_all_regions), ("Clear All", self._clear_all_regions)]:
            btn = tk.Button(actions, text=label, command=cmd, font=self.theme.fonts["small"])
            self.theme.apply_to_widget(btn, "button_secondary")
            btn.pack(side=tk.LEFT, padx=(0, 5))

        self.region_status_label = self.theme.create_styled_label(
            bottom, "", "small", fg=self.theme.colors["text_secondary"]
        )
        self.region_status_label.pack(side=tk.RIGHT, padx=(10, 5))
        self._update_region_status()

    def _update_region_status(self) -> None:
        if not self.region_status_label:
            return
        region_vars = [
            ("Africa", self.africa_var), ("Asia", self.asia_var),
            ("Europe", self.europe_var), ("North America", self.north_america_var),
            ("Oceania", self.oceania_var), ("South America", self.south_america_var),
        ]
        selected, total = [], 0
        for name, var in region_vars:
            if var.get():
                selected.append(name)
                total += len(REGIONS[name])
        if selected:
            text = (f"{selected[0]} ({total} countries)" if len(selected) == 1
                    else f"{len(selected)} regions ({total} countries)")
        else:
            text = ""
        self.region_status_label.configure(text=text)

    def _select_all_regions(self) -> None:
        for var in (self.africa_var, self.asia_var, self.europe_var,
                    self.north_america_var, self.oceania_var, self.south_america_var):
            var.set(True)
        self._update_region_status()

    def _clear_all_regions(self) -> None:
        for var in (self.africa_var, self.asia_var, self.europe_var,
                    self.north_america_var, self.oceania_var, self.south_america_var):
            var.set(False)
        self._update_region_status()

    # ------------------------------------------------------------------
    # Max results
    # ------------------------------------------------------------------

    def _create_max_results_option(self, parent: tk.Frame) -> None:
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        self._create_accent_heading(container, "🔢 Max Shodan Results").pack(fill=tk.X)

        row = tk.Frame(container)
        self.theme.apply_to_widget(row, "card")
        row.pack(fill=tk.X, pady=(5, 0))

        tk.Entry(
            row, textvariable=self.max_results_var, width=8,
            font=self.theme.fonts["body"]
        ).pack(side=tk.LEFT)

        hint = self.theme.create_styled_label(
            row, "  (1–1000, default: 1000)", "small",
            fg=self.theme.colors["text_secondary"]
        )
        hint.configure(font=(self.theme.fonts["small"][0], self.theme.fonts["small"][1], "italic"))
        hint.pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # API key
    # ------------------------------------------------------------------

    def _create_api_key_option(self, parent: tk.Frame) -> None:
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        self._create_accent_heading(container, "🔑 API Key Override").pack(fill=tk.X)

        row = tk.Frame(container)
        self.theme.apply_to_widget(row, "card")
        row.pack(fill=tk.X, pady=(5, 0))

        tk.Entry(
            row, textvariable=self.api_key_var, width=40,
            font=self.theme.fonts["body"], show="*"
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        hint = self.theme.create_styled_label(
            row, "  (temporary override)", "small",
            fg=self.theme.colors["text_secondary"]
        )
        hint.configure(font=(self.theme.fonts["small"][0], self.theme.fonts["small"][1], "italic"))
        hint.pack(side=tk.LEFT, padx=(5, 0))

    # ------------------------------------------------------------------
    # Verbose
    # ------------------------------------------------------------------

    def _create_verbose_option(self, parent: tk.Frame) -> None:
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        self._create_accent_heading(container, "📣 Verbose Mode").pack(fill=tk.X)

        cb = tk.Checkbutton(
            container, text="Send backend verbose output",
            variable=self.verbose_var, font=self.theme.fonts["small"]
        )
        self.theme.apply_to_widget(cb, "checkbox")
        cb.pack(anchor="w", padx=12, pady=(6, 4))

    def _create_bulk_probe_option(self, parent: tk.Frame) -> None:
        """Create optional post-scan probe checkbox."""
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        self._create_accent_heading(container, "🔍 Bulk Probe").pack(fill=tk.X)

        options_frame = tk.Frame(container)
        self.theme.apply_to_widget(options_frame, "card")
        options_frame.pack(fill=tk.X, pady=(5, 5))

        bulk_probe_checkbox = tk.Checkbutton(
            options_frame,
            text="Run bulk probe after scan",
            variable=self.bulk_probe_enabled_var,
            font=self.theme.fonts["small"],
        )
        self.theme.apply_to_widget(bulk_probe_checkbox, "checkbox")
        bulk_probe_checkbox.pack(anchor="w", padx=10, pady=2)

        info_label = self.theme.create_styled_label(
            container,
            "Automatically run protocol-aware probe on discovered FTP hosts.",
            "small",
            fg=self.theme.colors["text_secondary"],
        )
        info_label.pack(anchor="w", padx=15, pady=(0, 5))

    # ------------------------------------------------------------------
    # Concurrency
    # ------------------------------------------------------------------

    def _create_concurrency_options(self, parent: tk.Frame) -> None:
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        self._create_accent_heading(container, "🧵 Concurrency").pack(fill=tk.X)

        validate_cmd = self.dialog.register(self._validate_integer_input)

        rows = [
            ("Discovery workers:", self.discovery_concurrency_var, "Hosts authenticated in parallel"),
            ("Access workers:", self.access_concurrency_var, "Hosts tested in parallel during access"),
        ]
        for label_text, var, hint_text in rows:
            row = tk.Frame(container)
            self.theme.apply_to_widget(row, "card")
            row.pack(fill=tk.X, pady=(5, 0))

            self.theme.create_styled_label(row, label_text, "small").pack(side=tk.LEFT)
            entry = tk.Entry(
                row, textvariable=var, width=6,
                validate="key", validatecommand=(validate_cmd, "%P")
            )
            self.theme.apply_to_widget(entry, "entry")
            entry.pack(side=tk.LEFT, padx=(8, 0))

            hint = self.theme.create_styled_label(
                row, f"  {hint_text}", "small",
                fg=self.theme.colors["text_secondary"]
            )
            hint.configure(font=(self.theme.fonts["small"][0], self.theme.fonts["small"][1], "italic"))
            hint.pack(side=tk.LEFT, padx=(4, 0))

        self.theme.create_styled_label(
            container,
            f"Allowed range: 1 – {_CONCURRENCY_UPPER} workers",
            "small", fg=self.theme.colors["text_secondary"]
        ).pack(anchor="w", pady=(6, 0))

    # ------------------------------------------------------------------
    # Timeouts
    # ------------------------------------------------------------------

    def _create_timeout_options(self, parent: tk.Frame) -> None:
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        self._create_accent_heading(container, "⏱ FTP Timeouts (seconds)").pack(fill=tk.X)

        validate_cmd = self.dialog.register(self._validate_integer_input)

        timeout_rows = [
            ("Connect timeout:", self.connect_timeout_var, "TCP connect"),
            ("Auth timeout:", self.auth_timeout_var, "Login / anonymous auth"),
            ("Listing timeout:", self.listing_timeout_var, "Directory listing"),
        ]
        for label_text, var, hint_text in timeout_rows:
            row = tk.Frame(container)
            self.theme.apply_to_widget(row, "card")
            row.pack(fill=tk.X, pady=(5, 0))

            self.theme.create_styled_label(row, label_text, "small").pack(side=tk.LEFT)
            entry = tk.Entry(
                row, textvariable=var, width=6,
                validate="key", validatecommand=(validate_cmd, "%P")
            )
            self.theme.apply_to_widget(entry, "entry")
            entry.pack(side=tk.LEFT, padx=(8, 0))

            hint = self.theme.create_styled_label(
                row, f"  {hint_text}", "small",
                fg=self.theme.colors["text_secondary"]
            )
            hint.configure(font=(self.theme.fonts["small"][0], self.theme.fonts["small"][1], "italic"))
            hint.pack(side=tk.LEFT, padx=(4, 0))

        self.theme.create_styled_label(
            container,
            f"Allowed range: 1 – {_TIMEOUT_UPPER} seconds",
            "small", fg=self.theme.colors["text_secondary"]
        ).pack(anchor="w", pady=(6, 0))

    def _create_config_section(self) -> None:
        """Create configuration file section."""
        config_frame = tk.Frame(self._content)
        self.theme.apply_to_widget(config_frame, "card")
        config_frame.pack(fill=tk.X, padx=20, pady=(0, 5))

        config_title = self.theme.create_styled_label(
            config_frame,
            "Configuration",
            "heading",
        )
        config_title.pack(anchor="w", padx=15, pady=(10, 5))

        config_info_frame = tk.Frame(config_frame)
        self.theme.apply_to_widget(config_info_frame, "card")
        config_info_frame.pack(fill=tk.X, padx=15, pady=(0, 5))

        info_text = f"Using configuration from:\n{self.config_path}"
        config_path_label = self.theme.create_styled_label(
            config_info_frame,
            info_text,
            "small",
            fg=self.theme.colors["text_secondary"],
            justify="left",
        )
        config_path_label.pack(anchor="w")

        config_button_frame = tk.Frame(config_frame)
        self.theme.apply_to_widget(config_button_frame, "card")
        config_button_frame.pack(fill=tk.X, padx=15, pady=(0, 10))

        edit_config_button = tk.Button(
            config_button_frame,
            text="⚙ Edit Configuration",
            command=self._open_config_editor,
        )
        self.theme.apply_to_widget(edit_config_button, "button_secondary")
        edit_config_button.pack(side=tk.LEFT)

    def _open_config_editor(self) -> None:
        """Open configuration editor."""
        if not self.config_editor_callback:
            messagebox.showwarning(
                "Configuration Editor Unavailable",
                "No configuration editor callback is available in this context.",
                parent=self.dialog,
            )
            return
        try:
            self.config_editor_callback(str(self.config_path))
        except Exception as exc:
            messagebox.showerror(
                "Configuration Editor Error",
                f"Failed to open configuration editor:\n{exc}\n\n"
                "Please ensure the configuration system is properly set up.",
                parent=self.dialog,
            )

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    def _create_button_panel(self) -> None:
        frame = tk.Frame(self.dialog)
        self.theme.apply_to_widget(frame, "main_window")
        frame.pack(fill=tk.X, padx=20, pady=(5, 15))

        btns = tk.Frame(frame)
        self.theme.apply_to_widget(btns, "main_window")
        btns.pack(side=tk.RIGHT)

        cancel_btn = tk.Button(btns, text="Cancel", command=self._cancel)
        self.theme.apply_to_widget(cancel_btn, "button_secondary")
        cancel_btn.pack(side=tk.LEFT, padx=(0, 10))

        start_btn = tk.Button(btns, text="🚀 Start FTP Scan", command=self._start)
        self.theme.apply_to_widget(start_btn, "button_primary")
        start_btn.pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_country_input(self, *_args) -> None:
        raw = self.country_var.get()
        upper = raw.upper()
        if upper != raw:
            self.country_var.set(upper)

    def _validate_max_results(self, *_args) -> None:
        try:
            v = self.max_results_var.get()
            if not (1 <= v <= 1000):
                self.max_results_var.set(max(1, min(1000, v)))
        except tk.TclError:
            self.max_results_var.set(1000)

    def _parse_positive_int(
        self, value_str: str, field_name: str, *, minimum: int = 1, maximum: int
    ) -> int:
        if not value_str.strip():
            raise ValueError(f"{field_name} is required.")
        try:
            v = int(value_str)
        except ValueError:
            raise ValueError(f"{field_name} must be a whole number.")
        if v < minimum:
            raise ValueError(f"{field_name} must be at least {minimum}.")
        if v > maximum:
            raise ValueError(f"{field_name} must be {maximum} or less.")
        return v

    # ------------------------------------------------------------------
    # Country validation (mirrors ScanDialog exactly)
    # ------------------------------------------------------------------

    def _parse_and_validate_countries(self, country_input: str) -> tuple:
        if not country_input.strip():
            return [], ""
        codes = [c.strip().upper() for c in country_input.split(",")]
        valid = []
        for code in codes:
            if not code:
                continue
            if len(code) < 2 or len(code) > 3:
                return [], f"Invalid country code '{code}': must be 2-3 characters (e.g., US, GB, CA)"
            if not code.isalpha():
                return [], f"Invalid country code '{code}': must contain only letters (e.g., US, GB, CA)"
            valid.append(code)
        if not valid:
            return [], "Please enter at least one valid country code"
        return valid, ""

    def _get_selected_region_countries(self) -> list:
        region_vars = [
            ("Africa", self.africa_var), ("Asia", self.asia_var),
            ("Europe", self.europe_var), ("North America", self.north_america_var),
            ("Oceania", self.oceania_var), ("South America", self.south_america_var),
        ]
        out = []
        for name, var in region_vars:
            if var.get():
                out.extend(REGIONS[name])
        return out

    def _get_all_selected_countries(self, manual_input: str) -> tuple:
        manual, err = self._parse_and_validate_countries(manual_input)
        if err:
            return [], err
        region = self._get_selected_region_countries()
        all_countries = sorted(set(manual + region))
        if len(all_countries) > _MAX_COUNTRIES:
            return [], (
                f"Too many countries selected ({len(all_countries)}). "
                f"Maximum allowed: {_MAX_COUNTRIES}. Please reduce your selection."
            )
        return all_countries, ""

    # ------------------------------------------------------------------
    # Build scan_options dict
    # ------------------------------------------------------------------

    def _build_scan_options(self) -> Dict[str, Any]:
        """
        Return a scan_options dict for scan_manager.start_ftp_scan().

        country is None for global scans; comma-separated codes otherwise.
        Key names match SMB conventions for consistent scan_manager handling.
        """
        # Collect and validate concurrency / timeouts
        discovery_concurrency = self._parse_positive_int(
            self.discovery_concurrency_var.get().strip(),
            "Discovery workers", minimum=1, maximum=_CONCURRENCY_UPPER
        )
        access_concurrency = self._parse_positive_int(
            self.access_concurrency_var.get().strip(),
            "Access workers", minimum=1, maximum=_CONCURRENCY_UPPER
        )
        connect_timeout = self._parse_positive_int(
            self.connect_timeout_var.get().strip(),
            "Connect timeout", minimum=1, maximum=_TIMEOUT_UPPER
        )
        auth_timeout = self._parse_positive_int(
            self.auth_timeout_var.get().strip(),
            "Auth timeout", minimum=1, maximum=_TIMEOUT_UPPER
        )
        listing_timeout = self._parse_positive_int(
            self.listing_timeout_var.get().strip(),
            "Listing timeout", minimum=1, maximum=_TIMEOUT_UPPER
        )
        max_results = self.max_results_var.get()
        custom_filters = self.custom_filters_var.get().strip()

        api_key = self.api_key_var.get().strip()
        api_key = api_key if api_key else None

        verbose = bool(self.verbose_var.get())

        # Country resolution
        manual_input = self.country_var.get().strip()
        countries, err = self._get_all_selected_countries(manual_input)
        if err:
            raise ValueError(err)

        country_param: Optional[str] = ",".join(countries) if countries else None

        if self._settings_manager is not None:
            try:
                # Save only manual country entry (region picks are saved separately).
                manual_country_input = self.country_var.get().strip()
                self._settings_manager.set_setting("ftp_scan_dialog.max_shodan_results", max_results)
                self._settings_manager.set_setting("ftp_scan_dialog.api_key_override", api_key or "")
                self._settings_manager.set_setting("ftp_scan_dialog.custom_filters", custom_filters)
                self._settings_manager.set_setting("ftp_scan_dialog.country_code", manual_country_input)
                self._settings_manager.set_setting(
                    "ftp_scan_dialog.discovery_max_concurrent_hosts",
                    discovery_concurrency,
                )
                self._settings_manager.set_setting(
                    "ftp_scan_dialog.access_max_concurrent_hosts",
                    access_concurrency,
                )
                self._settings_manager.set_setting("ftp_scan_dialog.connect_timeout", connect_timeout)
                self._settings_manager.set_setting("ftp_scan_dialog.auth_timeout", auth_timeout)
                self._settings_manager.set_setting("ftp_scan_dialog.listing_timeout", listing_timeout)
                self._settings_manager.set_setting("ftp_scan_dialog.verbose", verbose)
                self._settings_manager.set_setting(
                    "ftp_scan_dialog.bulk_probe_enabled", bool(self.bulk_probe_enabled_var.get())
                )

                self._settings_manager.set_setting("ftp_scan_dialog.region_africa", self.africa_var.get())
                self._settings_manager.set_setting("ftp_scan_dialog.region_asia", self.asia_var.get())
                self._settings_manager.set_setting("ftp_scan_dialog.region_europe", self.europe_var.get())
                self._settings_manager.set_setting(
                    "ftp_scan_dialog.region_north_america", self.north_america_var.get()
                )
                self._settings_manager.set_setting("ftp_scan_dialog.region_oceania", self.oceania_var.get())
                self._settings_manager.set_setting(
                    "ftp_scan_dialog.region_south_america", self.south_america_var.get()
                )
            except Exception:
                # Best-effort only; do not block scan start on settings write failures.
                pass

        return {
            "country": country_param,
            "max_shodan_results": max_results,
            "api_key_override": api_key,
            "custom_filters": custom_filters,
            "discovery_max_concurrent_hosts": discovery_concurrency,
            "access_max_concurrent_hosts": access_concurrency,
            "connect_timeout": connect_timeout,
            "auth_timeout": auth_timeout,
            "listing_timeout": listing_timeout,
            "verbose": verbose,
            "bulk_probe_enabled": bool(self.bulk_probe_enabled_var.get()),
        }

    # ------------------------------------------------------------------
    # Start / Cancel
    # ------------------------------------------------------------------

    def _start(self) -> None:
        # Persist user edits even before validation (best-effort).
        self._persist_dialog_state()
        try:
            scan_options = self._build_scan_options()
        except ValueError as exc:
            messagebox.showerror("Invalid Input", str(exc), parent=self.dialog)
            return

        self.result = "start"
        self.scan_start_callback(scan_options)
        self.dialog.destroy()

    def _cancel(self) -> None:
        self._persist_dialog_state()
        self.result = "cancel"
        self.dialog.destroy()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(self) -> Optional[str]:
        """Wait for dialog to close and return result string."""
        self.parent.wait_window(self.dialog)
        return self.result


def show_ftp_scan_dialog(
    parent: tk.Widget,
    config_path: str,
    scan_start_callback: Callable[[Dict[str, Any]], None],
    settings_manager: Optional[Any] = None,
    config_editor_callback: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """
    Show the FTP scan configuration dialog modally.

    Calls scan_start_callback(scan_options) when user presses Start.
    Returns "start", "cancel", or None.
    """
    dialog = FtpScanDialog(
        parent,
        config_path,
        scan_start_callback,
        settings_manager,
        config_editor_callback=config_editor_callback,
    )
    return dialog.show()
