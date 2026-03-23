"""
Unified scan launch dialog.

Single entrypoint for SMB/FTP/HTTP scan launches. Supports:
- Multi-protocol selection (queue execution handled by dashboard)
- Shared scan settings across protocols
- Protocol-specific toggles (SMB security mode, HTTP TLS behavior)
- Template save/load
"""

from __future__ import annotations

import webbrowser
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, simpledialog
from typing import Any, Callable, Dict, Optional

from gui.components.scan_dialog import ScanDialog
from gui.components.scan_preflight import run_preflight
from gui.components.unified_scan_options_mixin import (
    _UnifiedScanDialogOptionsMixin,
    _CONCURRENCY_UPPER,
    _TIMEOUT_UPPER,
)
from gui.components.unified_scan_region_mixin import _UnifiedScanDialogRegionMixin
from gui.components.unified_scan_template_mixin import _UnifiedScanDialogTemplateMixin
from gui.components.unified_scan_validators import (
    parse_and_validate_countries as _parse_and_validate_countries_fn,
    parse_positive_int as _parse_positive_int_fn,
    validate_integer_char as _validate_integer_char_fn,
)
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import get_theme
from gui.utils.template_store import TemplateStore

REGIONS = ScanDialog.REGIONS


class UnifiedScanDialog(
    _UnifiedScanDialogTemplateMixin,
    _UnifiedScanDialogRegionMixin,
    _UnifiedScanDialogOptionsMixin,
):
    """Modal dialog for configuring queued multi-protocol scan runs."""

    TEMPLATE_PLACEHOLDER_TEXT = "Select a template..."

    def __init__(
        self,
        parent: tk.Widget,
        config_path: str,
        scan_start_callback: Callable[[Dict[str, Any]], None],
        settings_manager: Optional[Any] = None,
        config_editor_callback: Optional[Callable[[str], None]] = None,
        query_editor_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        self.parent = parent
        self.config_path = Path(config_path).resolve()
        self.scan_start_callback = scan_start_callback
        self._settings_manager = settings_manager
        self.config_editor_callback = config_editor_callback
        self.query_editor_callback = query_editor_callback
        self.theme = get_theme()
        self.template_store = TemplateStore(settings_manager=settings_manager)

        self.result = None
        self.dialog = None
        self.country_entry = None
        self.region_status_label = None
        self.custom_filters_entry = None
        self.template_dropdown = None
        self.delete_template_button = None

        # Protocol selections (default: all enabled)
        self.protocol_smb_var = tk.BooleanVar(value=True)
        self.protocol_ftp_var = tk.BooleanVar(value=True)
        self.protocol_http_var = tk.BooleanVar(value=True)

        # Shared targeting
        self.custom_filters_var = tk.StringVar()
        self.country_var = tk.StringVar()
        self.africa_var = tk.BooleanVar(value=False)
        self.asia_var = tk.BooleanVar(value=False)
        self.europe_var = tk.BooleanVar(value=False)
        self.north_america_var = tk.BooleanVar(value=False)
        self.oceania_var = tk.BooleanVar(value=False)
        self.south_america_var = tk.BooleanVar(value=False)

        # Shared runtime settings
        self.max_results_var = tk.IntVar(value=1000)
        self.shared_concurrency_var = tk.StringVar(value="10")
        self.shared_timeout_var = tk.StringVar(value="10")
        self.verbose_var = tk.BooleanVar(value=False)
        self.bulk_probe_enabled_var = tk.BooleanVar(value=False)
        self.bulk_extract_enabled_var = tk.BooleanVar(value=False)
        self.skip_indicator_extract_var = tk.BooleanVar(value=True)
        self.rce_enabled_var = tk.BooleanVar(value=False)

        # Protocol-specific settings
        self.security_mode_var = tk.StringVar(value="cautious")
        self.allow_insecure_tls_var = tk.BooleanVar(value=True)

        # Template UI state
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
        self.dialog.title("Start Scan")
        self.dialog.geometry("1120x880")
        self.dialog.resizable(True, True)
        self.theme.apply_to_widget(self.dialog, "main_window")
        self.dialog.transient(self.parent)
        self.dialog.grab_set()
        self._center_dialog()

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
            lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")),
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

        self._refresh_template_toolbar()
        self._update_region_status()
        self.theme.apply_theme_to_application(self.dialog)
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

    def _create_header(self) -> None:
        frame = tk.Frame(self._content)
        self.theme.apply_to_widget(frame, "main_window")
        frame.pack(fill=tk.X, padx=20, pady=(15, 5))

        self.theme.create_styled_label(frame, "Start Scan", "heading").pack(anchor="w")
        self.theme.create_styled_label(
            frame,
            "Launch SMB, FTP, and HTTP scans from one dialog. Selected protocols run sequentially.",
            "body",
            fg=self.theme.colors["text_secondary"],
        ).pack(anchor="w", pady=(5, 0))

    def _create_options(self) -> None:
        options_frame = tk.Frame(self._content)
        self.theme.apply_to_widget(options_frame, "card")
        options_frame.pack(fill=tk.X, padx=20, pady=5)

        self._create_template_toolbar(options_frame)

        self.theme.create_styled_label(
            options_frame,
            "Scan Parameters",
            "heading",
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

        self._create_protocol_selection(left)
        self._create_custom_filters_option(left)
        self._create_country_option(left)
        self._create_region_selection(left)
        self._create_max_results_option(left)

        self._create_shared_runtime_options(right)
        self._create_protocol_specific_options(right)

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

    def _create_template_toolbar(self, parent_frame: tk.Frame) -> None:
        toolbar = tk.Frame(parent_frame)
        self.theme.apply_to_widget(toolbar, "card")
        toolbar.pack(fill=tk.X, padx=15, pady=(10, 0))

        label = self.theme.create_styled_label(toolbar, "Templates:", "body")
        label.pack(side=tk.LEFT)

        self.template_dropdown = ttk.Combobox(
            toolbar,
            textvariable=self.template_var,
            state="readonly",
            width=32,
        )
        self.template_dropdown.pack(side=tk.LEFT, padx=(10, 10))
        self.template_dropdown.bind("<<ComboboxSelected>>", self._handle_template_selected)

        save_button = tk.Button(
            toolbar,
            text="Save Current",
            command=self._prompt_save_template,
            font=self.theme.fonts["small"],
        )
        self.theme.apply_to_widget(save_button, "button_secondary")
        save_button.pack(side=tk.LEFT, padx=(0, 5))

        self.delete_template_button = tk.Button(
            toolbar,
            text="Delete",
            command=self._delete_selected_template,
            font=self.theme.fonts["small"],
        )
        self.theme.apply_to_widget(self.delete_template_button, "button_secondary")
        self.delete_template_button.pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _create_protocol_selection(self, parent: tk.Frame) -> None:
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        self._create_accent_heading(container, "Protocols").pack(fill=tk.X)

        row = tk.Frame(container)
        self.theme.apply_to_widget(row, "card")
        row.pack(fill=tk.X, pady=(5, 5))

        for text, var in (
            ("SMB", self.protocol_smb_var),
            ("FTP", self.protocol_ftp_var),
            ("HTTP", self.protocol_http_var),
        ):
            cb = tk.Checkbutton(row, text=text, variable=var, font=self.theme.fonts["small"])
            self.theme.apply_to_widget(cb, "checkbox")
            cb.pack(side=tk.LEFT, padx=(10, 12), pady=2)

        edit_queries_btn = tk.Button(
            row,
            text="Edit Queries",
            command=self._open_query_editor,
            font=self.theme.fonts["small"],
        )
        self.theme.apply_to_widget(edit_queries_btn, "button_secondary")
        edit_queries_btn.pack(side=tk.RIGHT, padx=(0, 10))

        info = self.theme.create_styled_label(
            container,
            "Selected protocols run sequentially in one queue.",
            "small",
            fg=self.theme.colors["text_secondary"],
        )
        info.pack(anchor="w", padx=15, pady=(0, 5))

    def _create_custom_filters_option(self, parent: tk.Frame) -> None:
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        heading_frame = tk.Frame(container)
        self.theme.apply_to_widget(heading_frame, "card")
        heading_frame.pack(fill=tk.X)

        heading = self._create_accent_heading(heading_frame, "Custom Shodan Filters (optional)")
        heading.pack(side=tk.LEFT)

        help_link = tk.Label(
            heading_frame,
            text="Filter Reference",
            fg=self.theme.colors["accent"],
            cursor="hand2",
            font=self.theme.fonts["small"],
        )
        help_link.pack(side=tk.LEFT, padx=(10, 0))
        help_link.bind("<Button-1>", lambda _e: webbrowser.open("https://www.shodan.io/search/filters"))

        row = tk.Frame(container)
        self.theme.apply_to_widget(row, "card")
        row.pack(fill=tk.X, pady=(5, 0))

        self.custom_filters_entry = tk.Entry(
            row,
            textvariable=self.custom_filters_var,
            width=50,
            font=self.theme.fonts["body"],
        )
        self.theme.apply_to_widget(self.custom_filters_entry, "entry")
        self.custom_filters_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _create_country_option(self, parent: tk.Frame) -> None:
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        heading = self._create_accent_heading(container, "Country Code (optional)")
        heading.pack(fill=tk.X)

        row = tk.Frame(container)
        self.theme.apply_to_widget(row, "card")
        row.pack(fill=tk.X, pady=(5, 0))

        self.country_entry = tk.Entry(
            row,
            textvariable=self.country_var,
            width=10,
            font=self.theme.fonts["body"],
        )
        self.theme.apply_to_widget(self.country_entry, "entry")
        self.country_entry.pack(side=tk.LEFT)

        hint = self.theme.create_styled_label(
            row,
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

        self._create_accent_heading(container, "Region Selection").pack(fill=tk.X, pady=(0, 10))

        checkboxes = tk.Frame(container)
        self.theme.apply_to_widget(checkboxes, "card")
        checkboxes.pack(fill=tk.X, pady=(5, 5))

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
                checkboxes,
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

        for label, cmd in (("Select All", self._select_all_regions), ("Clear All", self._clear_all_regions)):
            btn = tk.Button(actions, text=label, command=cmd, font=self.theme.fonts["small"])
            self.theme.apply_to_widget(btn, "button_secondary")
            btn.pack(side=tk.LEFT, padx=(0, 5))

        self.region_status_label = self.theme.create_styled_label(
            bottom,
            "",
            "small",
            fg=self.theme.colors["text_secondary"],
        )
        self.region_status_label.pack(side=tk.RIGHT, padx=(10, 5))

    def _create_max_results_option(self, parent: tk.Frame) -> None:
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        self._create_accent_heading(container, "Max Shodan Results").pack(fill=tk.X)

        row = tk.Frame(container)
        self.theme.apply_to_widget(row, "card")
        row.pack(fill=tk.X, pady=(5, 0))

        max_results_entry = tk.Entry(
            row,
            textvariable=self.max_results_var,
            width=8,
            font=self.theme.fonts["body"],
        )
        self.theme.apply_to_widget(max_results_entry, "entry")
        max_results_entry.pack(side=tk.LEFT)

        hint = self.theme.create_styled_label(
            row,
            "  (1–1000, default: 1000)",
            "small",
            fg=self.theme.colors["text_secondary"],
        )
        hint.configure(font=(self.theme.fonts["small"][0], self.theme.fonts["small"][1], "italic"))
        hint.pack(side=tk.LEFT)

    def _create_shared_runtime_options(self, parent: tk.Frame) -> None:
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        self._create_accent_heading(container, "Shared Runtime").pack(fill=tk.X)

        validate_cmd = self.dialog.register(self._validate_integer_input)

        conc_row = tk.Frame(container)
        self.theme.apply_to_widget(conc_row, "card")
        conc_row.pack(fill=tk.X, pady=(6, 0))

        self.theme.create_styled_label(conc_row, "Backend concurrency:", "small").pack(side=tk.LEFT)
        conc_entry = tk.Entry(
            conc_row,
            textvariable=self.shared_concurrency_var,
            width=6,
            validate="key",
            validatecommand=(validate_cmd, "%P"),
        )
        self.theme.apply_to_widget(conc_entry, "entry")
        conc_entry.pack(side=tk.LEFT, padx=(8, 0))

        timeout_row = tk.Frame(container)
        self.theme.apply_to_widget(timeout_row, "card")
        timeout_row.pack(fill=tk.X, pady=(6, 0))

        self.theme.create_styled_label(timeout_row, "Shared timeout:", "small").pack(side=tk.LEFT)
        timeout_entry = tk.Entry(
            timeout_row,
            textvariable=self.shared_timeout_var,
            width=6,
            validate="key",
            validatecommand=(validate_cmd, "%P"),
        )
        self.theme.apply_to_widget(timeout_entry, "entry")
        timeout_entry.pack(side=tk.LEFT, padx=(8, 0))

        self.theme.create_styled_label(
            container,
            f"Allowed ranges: concurrency 1–{_CONCURRENCY_UPPER}, timeout 1–{_TIMEOUT_UPPER} seconds",
            "small",
            fg=self.theme.colors["text_secondary"],
        ).pack(anchor="w", pady=(6, 2))

        self.theme.create_styled_label(
            container,
            "SMB rate/share delays continue to use configuration defaults to avoid unintended scan throttling.",
            "small",
            fg=self.theme.colors["text_secondary"],
        ).pack(anchor="w", pady=(0, 4))

        verbose_cb = tk.Checkbutton(
            container,
            text="Verbose backend output",
            variable=self.verbose_var,
            font=self.theme.fonts["small"],
        )
        self.theme.apply_to_widget(verbose_cb, "checkbox")
        verbose_cb.pack(anchor="w", padx=10, pady=(2, 2))

        probe_cb = tk.Checkbutton(
            container,
            text="Run bulk probe after each scan",
            variable=self.bulk_probe_enabled_var,
            font=self.theme.fonts["small"],
        )
        self.theme.apply_to_widget(probe_cb, "checkbox")
        probe_cb.pack(anchor="w", padx=10, pady=(2, 2))

        extract_cb = tk.Checkbutton(
            container,
            text="Run bulk extract after each scan",
            variable=self.bulk_extract_enabled_var,
            font=self.theme.fonts["small"],
        )
        self.theme.apply_to_widget(extract_cb, "checkbox")
        extract_cb.pack(anchor="w", padx=10, pady=(2, 2))

        skip_cb = tk.Checkbutton(
            container,
            text="Skip extract on hosts with malware indicators (recommended)",
            variable=self.skip_indicator_extract_var,
            font=self.theme.fonts["small"],
        )
        self.theme.apply_to_widget(skip_cb, "checkbox")
        skip_cb.pack(anchor="w", padx=10, pady=(2, 2))

        rce_cb = tk.Checkbutton(
            container,
            text="Enable RCE analysis",
            variable=self.rce_enabled_var,
            font=self.theme.fonts["small"],
        )
        self.theme.apply_to_widget(rce_cb, "checkbox")
        rce_cb.pack(anchor="w", padx=10, pady=(2, 2))

        rce_hint = self.theme.create_styled_label(
            container,
            "RCE analysis currently applies to SMB probe flow only.",
            "small",
            fg=self.theme.colors["text_secondary"],
        )
        rce_hint.pack(anchor="w", padx=15, pady=(0, 5))

        extract_hint = self.theme.create_styled_label(
            container,
            "Bulk extract currently supports SMB shares only.",
            "small",
            fg=self.theme.colors["text_secondary"],
        )
        extract_hint.pack(anchor="w", padx=15, pady=(0, 5))

    def _create_protocol_specific_options(self, parent: tk.Frame) -> None:
        container = tk.Frame(parent)
        self.theme.apply_to_widget(container, "card")
        container.pack(fill=tk.X, padx=15, pady=(0, 10))

        self._create_accent_heading(container, "Protocol-specific").pack(fill=tk.X)

        smb_frame = tk.Frame(container)
        self.theme.apply_to_widget(smb_frame, "card")
        smb_frame.pack(fill=tk.X, pady=(6, 2))

        self.theme.create_styled_label(smb_frame, "SMB Security Mode", "small").pack(anchor="w", padx=10, pady=(0, 2))
        cautious_radio = tk.Radiobutton(
            smb_frame,
            text="Cautious – signed SMB2+/SMB3 only",
            variable=self.security_mode_var,
            value="cautious",
            font=self.theme.fonts["small"],
        )
        self.theme.apply_to_widget(cautious_radio, "checkbox")
        cautious_radio.pack(anchor="w", padx=10)

        legacy_radio = tk.Radiobutton(
            smb_frame,
            text="Legacy – allow SMB1/unsigned connections",
            variable=self.security_mode_var,
            value="legacy",
            font=self.theme.fonts["small"],
        )
        self.theme.apply_to_widget(legacy_radio, "checkbox")
        legacy_radio.pack(anchor="w", padx=10, pady=(0, 2))

        http_frame = tk.Frame(container)
        self.theme.apply_to_widget(http_frame, "card")
        http_frame.pack(fill=tk.X, pady=(2, 5))

        tls_cb = tk.Checkbutton(
            http_frame,
            text="HTTP: Allow insecure HTTPS certificates",
            variable=self.allow_insecure_tls_var,
            font=self.theme.fonts["small"],
        )
        self.theme.apply_to_widget(tls_cb, "checkbox")
        tls_cb.pack(anchor="w", padx=10, pady=(2, 2))

    def _create_config_section(self) -> None:
        config_frame = tk.Frame(self._content)
        self.theme.apply_to_widget(config_frame, "card")
        config_frame.pack(fill=tk.X, padx=20, pady=(0, 5))

        title = self.theme.create_styled_label(config_frame, "Configuration", "heading")
        title.pack(anchor="w", padx=15, pady=(10, 5))

        info_frame = tk.Frame(config_frame)
        self.theme.apply_to_widget(info_frame, "card")
        info_frame.pack(fill=tk.X, padx=15, pady=(0, 5))

        info_text = f"Using configuration from:\n{self.config_path}"
        self.theme.create_styled_label(
            info_frame,
            info_text,
            "small",
            fg=self.theme.colors["text_secondary"],
            justify="left",
        ).pack(anchor="w")

        btn_frame = tk.Frame(config_frame)
        self.theme.apply_to_widget(btn_frame, "card")
        btn_frame.pack(fill=tk.X, padx=15, pady=(0, 10))

        edit_btn = tk.Button(btn_frame, text="Edit Configuration", command=self._open_config_editor)
        self.theme.apply_to_widget(edit_btn, "button_secondary")
        edit_btn.pack(side=tk.LEFT)

    def _open_config_editor(self) -> None:
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
                f"Failed to open configuration editor:\n{exc}\n\nPlease ensure the configuration system is properly set up.",
                parent=self.dialog,
            )

    def _open_query_editor(self) -> None:
        """Open query manager from protocol section, falling back to config editor."""
        if self.query_editor_callback:
            try:
                self.query_editor_callback()
                return
            except Exception as exc:
                messagebox.showerror(
                    "Query Editor Error",
                    f"Failed to open query editor:\n{exc}",
                    parent=self.dialog,
                )
                return
        self._open_config_editor()

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

        start_btn = tk.Button(btns, text="Start Scan", command=self._start)
        self.theme.apply_to_widget(start_btn, "button_primary")
        start_btn.pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_integer_input(self, proposed: str) -> bool:
        return _validate_integer_char_fn(proposed)

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

    def _parse_positive_int(self, value_str: str, field_name: str, *, minimum: int = 1, maximum: int) -> int:
        return _parse_positive_int_fn(value_str, field_name, minimum=minimum, maximum=maximum)

    def _parse_and_validate_countries(self, country_input: str) -> tuple[list[str], str]:
        return _parse_and_validate_countries_fn(country_input)

    # ------------------------------------------------------------------
    # Build/start/cancel
    # ------------------------------------------------------------------

    def _build_scan_request(self) -> Dict[str, Any]:
        shared_concurrency = self._parse_positive_int(
            self.shared_concurrency_var.get().strip(),
            "Backend concurrency",
            minimum=1,
            maximum=_CONCURRENCY_UPPER,
        )
        shared_timeout = self._parse_positive_int(
            self.shared_timeout_var.get().strip(),
            "Shared timeout",
            minimum=1,
            maximum=_TIMEOUT_UPPER,
        )

        protocols = []
        if self.protocol_smb_var.get():
            protocols.append("smb")
        if self.protocol_ftp_var.get():
            protocols.append("ftp")
        if self.protocol_http_var.get():
            protocols.append("http")

        if not protocols:
            raise ValueError("Select at least one protocol (SMB, FTP, or HTTP).")

        manual_input = self.country_var.get().strip()
        countries, err = self._get_all_selected_countries(manual_input)
        if err:
            raise ValueError(err)
        country_param = ",".join(countries) if countries else None

        mode = (self.security_mode_var.get() or "cautious").strip().lower()
        if mode not in {"cautious", "legacy"}:
            mode = "cautious"

        self._persist_dialog_state()

        return {
            "protocols": protocols,
            "country": country_param,
            "max_shodan_results": int(self.max_results_var.get()),
            "custom_filters": self.custom_filters_var.get().strip(),
            "shared_concurrency": shared_concurrency,
            "shared_timeout_seconds": shared_timeout,
            "verbose": bool(self.verbose_var.get()),
            "bulk_probe_enabled": bool(self.bulk_probe_enabled_var.get()),
            "bulk_extract_enabled": bool(self.bulk_extract_enabled_var.get()),
            "bulk_extract_skip_indicators": bool(self.skip_indicator_extract_var.get()),
            "rce_enabled": bool(self.rce_enabled_var.get()),
            "security_mode": mode,
            "allow_insecure_tls": bool(self.allow_insecure_tls_var.get()),
        }

    def _start(self) -> None:
        self._persist_dialog_state()
        try:
            scan_request = self._build_scan_request()
        except ValueError as exc:
            messagebox.showerror("Invalid Input", str(exc), parent=self.dialog)
            return

        protocol_label = ", ".join(p.upper() for p in scan_request["protocols"])
        country_desc = scan_request.get("country") or "global"
        scan_desc = f"protocols: {protocol_label}; target: {country_desc}"

        preflight_result = run_preflight(
            self.dialog,
            self.theme,
            self._settings_manager,
            scan_request,
            scan_desc,
        )
        if preflight_result is None:
            return

        self.result = "start"
        self.scan_start_callback(preflight_result)
        self.dialog.destroy()

    def _cancel(self) -> None:
        self._persist_dialog_state()
        self.result = "cancel"
        self.dialog.destroy()

    def show(self) -> Optional[str]:
        self.parent.wait_window(self.dialog)
        return self.result


def show_unified_scan_dialog(
    parent: tk.Widget,
    config_path: str,
    scan_start_callback: Callable[[Dict[str, Any]], None],
    settings_manager: Optional[Any] = None,
    config_editor_callback: Optional[Callable[[str], None]] = None,
    query_editor_callback: Optional[Callable[[], None]] = None,
) -> Optional[str]:
    """Show the unified scan launch dialog modally."""
    dialog = UnifiedScanDialog(
        parent=parent,
        config_path=config_path,
        scan_start_callback=scan_start_callback,
        settings_manager=settings_manager,
        config_editor_callback=config_editor_callback,
        query_editor_callback=query_editor_callback,
    )
    return dialog.show()
