"""
Unified scan launch dialog.

Single entrypoint for SMB/FTP/HTTP scan launches. Supports:
- Multi-protocol selection (queue execution handled by dashboard)
- Shared scan settings across protocols
- Protocol-specific toggles (SMB security mode, HTTP TLS behavior)
- Template save/load
"""

from __future__ import annotations

import json
import webbrowser
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, simpledialog
from typing import Any, Callable, Dict, Optional

from gui.components.scan_dialog import ScanDialog
from gui.components.scan_preflight import run_preflight
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import get_theme
from gui.utils.template_store import TemplateStore

REGIONS = ScanDialog.REGIONS

_MAX_COUNTRIES = 100
_CONCURRENCY_UPPER = 256
_TIMEOUT_UPPER = 300


class UnifiedScanDialog:
    """Modal dialog for configuring queued multi-protocol scan runs."""

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
    # Defaults/load/persist
    # ------------------------------------------------------------------

    def _load_config_defaults(self) -> None:
        """Load initial concurrency/timeout defaults from config file."""
        config_data: Dict[str, Any] = {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                config_data = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError):
            config_data = {}

        if not isinstance(config_data, dict):
            config_data = {}

        discovery = config_data.get("discovery", {})
        connection = config_data.get("connection", {})

        try:
            disc = int(discovery.get("max_concurrent_hosts", 10))
        except Exception:
            disc = 10
        try:
            timeout = int(connection.get("timeout", 10))
        except Exception:
            timeout = 10

        self.shared_concurrency_var.set(str(max(1, disc)))
        self.shared_timeout_var.set(str(max(1, timeout)))

    def _load_initial_values(self) -> None:
        """Load last-used values from settings manager."""
        if self._settings_manager is None:
            return

        def _coerce_int(value: Any, default: int, minimum: int = 1) -> int:
            try:
                v = int(value)
                return v if v >= minimum else default
            except (TypeError, ValueError):
                return default

        def _coerce_bool(value: Any, default: bool = False) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"1", "true", "yes", "on"}:
                    return True
                if normalized in {"0", "false", "no", "off", ""}:
                    return False
            return default

        try:
            self.protocol_smb_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.protocol_smb", True), True)
            )
            self.protocol_ftp_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.protocol_ftp", True), True)
            )
            self.protocol_http_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.protocol_http", True), True)
            )

            self.max_results_var.set(
                _coerce_int(self._settings_manager.get_setting("unified_scan_dialog.max_shodan_results", 1000), 1000)
            )
            self.custom_filters_var.set(str(self._settings_manager.get_setting("unified_scan_dialog.custom_filters", "")))
            self.country_var.set(str(self._settings_manager.get_setting("unified_scan_dialog.country_code", "")))

            self.shared_concurrency_var.set(
                str(_coerce_int(self._settings_manager.get_setting("unified_scan_dialog.shared_concurrency", 10), 10))
            )
            self.shared_timeout_var.set(
                str(_coerce_int(self._settings_manager.get_setting("unified_scan_dialog.shared_timeout_seconds", 10), 10))
            )

            self.verbose_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.verbose", False), False)
            )
            self.bulk_probe_enabled_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.bulk_probe_enabled", False), False)
            )
            self.bulk_extract_enabled_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.bulk_extract_enabled", False), False)
            )
            self.skip_indicator_extract_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.bulk_extract_skip_indicators", True), True)
            )
            self.rce_enabled_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.rce_enabled", False), False)
            )

            mode = str(self._settings_manager.get_setting("unified_scan_dialog.security_mode", "cautious")).strip().lower()
            self.security_mode_var.set(mode if mode in {"cautious", "legacy"} else "cautious")

            self.allow_insecure_tls_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.allow_insecure_tls", True), True)
            )

            self.africa_var.set(_coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.region_africa", False), False))
            self.asia_var.set(_coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.region_asia", False), False))
            self.europe_var.set(_coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.region_europe", False), False))
            self.north_america_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.region_north_america", False), False)
            )
            self.oceania_var.set(_coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.region_oceania", False), False))
            self.south_america_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.region_south_america", False), False)
            )
        except Exception:
            pass

        # Safety: ensure at least one protocol remains selected.
        if not (self.protocol_smb_var.get() or self.protocol_ftp_var.get() or self.protocol_http_var.get()):
            self.protocol_smb_var.set(True)
            self.protocol_ftp_var.set(True)
            self.protocol_http_var.set(True)

    def _persist_dialog_state(self) -> None:
        """Best-effort persistence of dialog state."""
        if self._settings_manager is None:
            return

        def _coerce_int(value: Any, minimum: int, maximum: int) -> Optional[int]:
            try:
                v = int(str(value).strip())
            except (TypeError, ValueError):
                return None
            if v < minimum or v > maximum:
                return None
            return v

        try:
            self._settings_manager.set_setting("unified_scan_dialog.protocol_smb", bool(self.protocol_smb_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.protocol_ftp", bool(self.protocol_ftp_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.protocol_http", bool(self.protocol_http_var.get()))

            max_results = _coerce_int(self.max_results_var.get(), 1, 1000)
            if max_results is not None:
                self._settings_manager.set_setting("unified_scan_dialog.max_shodan_results", max_results)

            shared_concurrency = _coerce_int(self.shared_concurrency_var.get(), 1, _CONCURRENCY_UPPER)
            if shared_concurrency is not None:
                self._settings_manager.set_setting("unified_scan_dialog.shared_concurrency", shared_concurrency)

            shared_timeout = _coerce_int(self.shared_timeout_var.get(), 1, _TIMEOUT_UPPER)
            if shared_timeout is not None:
                self._settings_manager.set_setting("unified_scan_dialog.shared_timeout_seconds", shared_timeout)

            self._settings_manager.set_setting("unified_scan_dialog.custom_filters", self.custom_filters_var.get().strip())
            self._settings_manager.set_setting("unified_scan_dialog.country_code", self.country_var.get().strip().upper())

            self._settings_manager.set_setting("unified_scan_dialog.verbose", bool(self.verbose_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.bulk_probe_enabled", bool(self.bulk_probe_enabled_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.bulk_extract_enabled", bool(self.bulk_extract_enabled_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.bulk_extract_skip_indicators", bool(self.skip_indicator_extract_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.rce_enabled", bool(self.rce_enabled_var.get()))

            mode = (self.security_mode_var.get() or "cautious").strip().lower()
            if mode not in {"cautious", "legacy"}:
                mode = "cautious"
            self._settings_manager.set_setting("unified_scan_dialog.security_mode", mode)
            self._settings_manager.set_setting("unified_scan_dialog.allow_insecure_tls", bool(self.allow_insecure_tls_var.get()))

            self._settings_manager.set_setting("unified_scan_dialog.region_africa", bool(self.africa_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.region_asia", bool(self.asia_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.region_europe", bool(self.europe_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.region_north_america", bool(self.north_america_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.region_oceania", bool(self.oceania_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.region_south_america", bool(self.south_america_var.get()))
        except Exception:
            pass

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
    # Template handling
    # ------------------------------------------------------------------

    def _refresh_template_toolbar(self, select_slug: Optional[str] = None) -> None:
        if not self.template_dropdown:
            return

        templates = self.template_store.list_templates()
        self._template_label_to_slug = {tpl.name: tpl.slug for tpl in templates}
        values = [tpl.name for tpl in templates]

        if not values:
            self.template_dropdown.configure(state="disabled", values=["No templates saved"])
            self.template_var.set("No templates saved")
            self._selected_template_slug = None
            self.delete_template_button.configure(state=tk.DISABLED)
            return

        placeholder = self.TEMPLATE_PLACEHOLDER_TEXT
        display_values = [placeholder] + values
        self.template_dropdown.configure(state="readonly", values=display_values)

        slug_to_label = {tpl.slug: tpl.name for tpl in templates}
        desired_slug = select_slug

        if desired_slug and desired_slug in slug_to_label:
            self.template_var.set(slug_to_label[desired_slug])
            self._selected_template_slug = desired_slug
            self.delete_template_button.configure(state=tk.NORMAL)
        else:
            self.template_var.set(placeholder)
            self._selected_template_slug = None
            self.delete_template_button.configure(state=tk.DISABLED)

    def _handle_template_selected(self, _event=None) -> None:
        label = self.template_var.get()
        if label == self.TEMPLATE_PLACEHOLDER_TEXT:
            self._selected_template_slug = None
            self.delete_template_button.configure(state=tk.DISABLED)
            return
        slug = self._template_label_to_slug.get(label)
        self._selected_template_slug = slug
        if slug:
            self._apply_template_by_slug(slug)
            self.delete_template_button.configure(state=tk.NORMAL)

    def _get_selected_template_name(self) -> Optional[str]:
        label = self.template_var.get()
        if label == self.TEMPLATE_PLACEHOLDER_TEXT:
            return None
        return label.strip() if label else None

    def _prompt_save_template(self) -> None:
        initial_name = self._get_selected_template_name()
        name = simpledialog.askstring(
            "Save Template",
            "Template name:",
            parent=self.dialog,
            initialvalue=initial_name or "",
        )
        if not name:
            return
        name = name.strip()
        if not name:
            messagebox.showwarning("Save Template", "Template name cannot be empty.", parent=self.dialog)
            return

        slug = TemplateStore.slugify(name)
        existing = self.template_store.load_template(slug)
        if existing:
            overwrite = messagebox.askyesno(
                "Overwrite Template",
                f"A template named '{name}' already exists. Overwrite it?",
                parent=self.dialog,
            )
            if not overwrite:
                return

        form_state = self._capture_form_state()
        template = self.template_store.save_template(name, form_state)
        self._refresh_template_toolbar(select_slug=template.slug)
        messagebox.showinfo("Template Saved", f"Template '{name}' saved.")

    def _delete_selected_template(self) -> None:
        slug = self._selected_template_slug
        if not slug:
            messagebox.showinfo("Delete Template", "No template selected.")
            return

        label = self.template_var.get()
        confirmed = messagebox.askyesno(
            "Delete Template",
            f"Delete template '{label}'?",
            parent=self.dialog,
        )
        if not confirmed:
            return

        deleted = self.template_store.delete_template(slug)
        if deleted:
            messagebox.showinfo("Template Deleted", f"Template '{label}' removed.")
        else:
            messagebox.showwarning("Delete Template", "Failed to delete template.", parent=self.dialog)

        self._refresh_template_toolbar()

    def _capture_form_state(self) -> Dict[str, Any]:
        return {
            "protocols": {
                "smb": self.protocol_smb_var.get(),
                "ftp": self.protocol_ftp_var.get(),
                "http": self.protocol_http_var.get(),
            },
            "custom_filters": self.custom_filters_var.get(),
            "country_code": self.country_var.get(),
            "regions": {
                "africa": self.africa_var.get(),
                "asia": self.asia_var.get(),
                "europe": self.europe_var.get(),
                "north_america": self.north_america_var.get(),
                "oceania": self.oceania_var.get(),
                "south_america": self.south_america_var.get(),
            },
            "max_results": self.max_results_var.get(),
            "shared_concurrency": self.shared_concurrency_var.get(),
            "shared_timeout_seconds": self.shared_timeout_var.get(),
            "verbose": self.verbose_var.get(),
            "bulk_probe_enabled": self.bulk_probe_enabled_var.get(),
            "bulk_extract_enabled": self.bulk_extract_enabled_var.get(),
            "bulk_extract_skip_indicators": self.skip_indicator_extract_var.get(),
            "rce_enabled": self.rce_enabled_var.get(),
            "security_mode": self.security_mode_var.get(),
            "allow_insecure_tls": self.allow_insecure_tls_var.get(),
        }

    def _apply_form_state(self, state: Dict[str, Any]) -> None:
        protocols = state.get("protocols", {})
        self.protocol_smb_var.set(bool(protocols.get("smb", True)))
        self.protocol_ftp_var.set(bool(protocols.get("ftp", True)))
        self.protocol_http_var.set(bool(protocols.get("http", True)))

        self.custom_filters_var.set(state.get("custom_filters", ""))
        self.country_var.set(state.get("country_code", ""))

        regions = state.get("regions", {})
        self.africa_var.set(bool(regions.get("africa", False)))
        self.asia_var.set(bool(regions.get("asia", False)))
        self.europe_var.set(bool(regions.get("europe", False)))
        self.north_america_var.set(bool(regions.get("north_america", False)))
        self.oceania_var.set(bool(regions.get("oceania", False)))
        self.south_america_var.set(bool(regions.get("south_america", False)))

        max_results = state.get("max_results")
        if max_results is not None:
            try:
                self.max_results_var.set(int(max_results))
            except (ValueError, tk.TclError):
                pass

        shared_conc = state.get("shared_concurrency")
        if shared_conc is not None:
            self.shared_concurrency_var.set(str(shared_conc))

        shared_timeout = state.get("shared_timeout_seconds")
        if shared_timeout is not None:
            self.shared_timeout_var.set(str(shared_timeout))

        self.verbose_var.set(bool(state.get("verbose", False)))
        self.bulk_probe_enabled_var.set(bool(state.get("bulk_probe_enabled", False)))
        self.bulk_extract_enabled_var.set(bool(state.get("bulk_extract_enabled", False)))
        self.skip_indicator_extract_var.set(bool(state.get("bulk_extract_skip_indicators", True)))
        self.rce_enabled_var.set(bool(state.get("rce_enabled", False)))

        mode = str(state.get("security_mode", "cautious")).strip().lower()
        self.security_mode_var.set(mode if mode in {"cautious", "legacy"} else "cautious")
        self.allow_insecure_tls_var.set(bool(state.get("allow_insecure_tls", True)))

        self._update_region_status()

    def _apply_template_by_slug(self, slug: str, *, silent: bool = False) -> None:
        template = self.template_store.load_template(slug)
        if not template:
            if not silent:
                messagebox.showwarning("Template Missing", "Selected template could not be loaded.", parent=self.dialog)
            self._refresh_template_toolbar()
            return

        self._apply_form_state(template.form_state)
        self.template_store.set_last_used(slug)
        self._selected_template_slug = slug

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
            fg="#0066cc",
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

        tk.Entry(row, textvariable=self.max_results_var, width=8, font=self.theme.fonts["body"]).pack(side=tk.LEFT)

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
        return proposed == "" or proposed.isdigit()

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
        if not value_str.strip():
            raise ValueError(f"{field_name} is required.")
        try:
            v = int(value_str)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a whole number.") from exc
        if v < minimum:
            raise ValueError(f"{field_name} must be at least {minimum}.")
        if v > maximum:
            raise ValueError(f"{field_name} must be {maximum} or less.")
        return v

    def _parse_and_validate_countries(self, country_input: str) -> tuple[list[str], str]:
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

    def _get_selected_region_countries(self) -> list[str]:
        region_vars = [
            ("Africa", self.africa_var),
            ("Asia", self.asia_var),
            ("Europe", self.europe_var),
            ("North America", self.north_america_var),
            ("Oceania", self.oceania_var),
            ("South America", self.south_america_var),
        ]
        out = []
        for name, var in region_vars:
            if var.get():
                out.extend(REGIONS[name])
        return out

    def _get_all_selected_countries(self, manual_input: str) -> tuple[list[str], str]:
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

    def _update_region_status(self) -> None:
        if not self.region_status_label:
            return

        region_vars = [
            ("Africa", self.africa_var),
            ("Asia", self.asia_var),
            ("Europe", self.europe_var),
            ("North America", self.north_america_var),
            ("Oceania", self.oceania_var),
            ("South America", self.south_america_var),
        ]
        selected, total = [], 0
        for name, var in region_vars:
            if var.get():
                selected.append(name)
                total += len(REGIONS[name])

        if selected:
            text = f"{selected[0]} ({total} countries)" if len(selected) == 1 else f"{len(selected)} regions ({total} countries)"
        else:
            text = ""
        self.region_status_label.configure(text=text)

    def _select_all_regions(self) -> None:
        for var in (
            self.africa_var,
            self.asia_var,
            self.europe_var,
            self.north_america_var,
            self.oceania_var,
            self.south_america_var,
        ):
            var.set(True)
        self._update_region_status()

    def _clear_all_regions(self) -> None:
        for var in (
            self.africa_var,
            self.asia_var,
            self.europe_var,
            self.north_america_var,
            self.oceania_var,
            self.south_america_var,
        ):
            var.set(False)
        self._update_region_status()

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
) -> Optional[str]:
    """Show the unified scan launch dialog modally."""
    dialog = UnifiedScanDialog(
        parent=parent,
        config_path=config_path,
        scan_start_callback=scan_start_callback,
        settings_manager=settings_manager,
        config_editor_callback=config_editor_callback,
    )
    return dialog.show()
