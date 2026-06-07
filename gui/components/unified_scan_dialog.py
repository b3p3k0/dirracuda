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
import tkinter as tk
from pathlib import Path
from tkinter import simpledialog
from gui.utils import safe_messagebox as messagebox
from typing import Any, Callable, Dict, Optional

from gui.components.scan_dialog import ScanDialog
from gui.components.query_budget_dialog import (
    _coerce_int as _cap_coerce_int,
    _credits_for_cap,
    load_query_budget_state,
    persist_query_budget_state,
    resolve_config_path_from_settings,
)
from gui.components.scan_dork_editor_dialog import show_scan_dork_editor_dialog
from gui.components.scan_preflight import run_preflight
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import get_theme
from gui.utils.template_store import TemplateStore

REGIONS = ScanDialog.REGIONS

_MAX_COUNTRIES = 100
_CONCURRENCY_UPPER = 256
_TIMEOUT_UPPER = 300


class UnifiedScanDialog:
    """Single-instance, non-blocking dialog for queued multi-protocol scan runs."""

    TEMPLATE_PLACEHOLDER_TEXT = "Select a template..."

    def __init__(
        self,
        parent: tk.Widget,
        config_path: str,
        scan_start_callback: Callable[[Dict[str, Any]], None],
        settings_manager: Optional[Any] = None,
        config_editor_callback: Optional[Callable[[str], None]] = None,
        query_editor_callback: Optional[Callable[[], None]] = None,
        reddit_grab_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        self.parent = parent
        self.config_path = Path(config_path).resolve()
        self.scan_start_callback = scan_start_callback
        self._settings_manager = settings_manager
        self.config_editor_callback = config_editor_callback
        self.query_editor_callback = query_editor_callback
        self.reddit_grab_callback = reddit_grab_callback
        self.theme = get_theme()
        self.template_store = TemplateStore(settings_manager=settings_manager)

        self.result = None
        self.dialog = None
        self.country_entry = None
        self.region_status_label = None
        self.template_dropdown = None
        self.delete_template_button = None
        self.skip_indicator_extract_checkbox = None
        self.protocol_cost_label = None
        self.protocol_results_label = None

        # Protocol selections (default: all enabled)
        self.protocol_smb_var = tk.BooleanVar(value=True)
        self.protocol_ftp_var = tk.BooleanVar(value=True)
        self.protocol_http_var = tk.BooleanVar(value=True)

        # Provider selections (Shodan on by default; SearXNG promoted in C3; Reddit in C4)
        self.provider_shodan_var = tk.BooleanVar(value=True)
        self.provider_searxng_var = tk.BooleanVar(value=False)
        self.provider_reddit_var = tk.BooleanVar(value=False)

        # SearXNG options (active when SearXNG provider is selected)
        self.searxng_instance_url_var = tk.StringVar(value="")
        self.searxng_query_var = tk.StringVar(value="")
        self.searxng_max_results_var = tk.StringVar(value="500")
        self.searxng_request_timeout_var = tk.DoubleVar(value=15.0)
        self.searxng_short_retry_delay_var = tk.DoubleVar(value=30.0)
        self.searxng_long_retry_delay_var = tk.DoubleVar(value=180.0)

        # Reddit options (active when Reddit provider is selected; C4)
        self.reddit_mode_var = tk.StringVar(value="feed")
        self.reddit_sort_var = tk.StringVar(value="new")
        self.reddit_top_window_var = tk.StringVar(value="week")
        self.reddit_max_posts_var = tk.StringVar(value="100")
        self.reddit_query_var = tk.StringVar(value="")
        self.reddit_username_var = tk.StringVar(value="")
        self.reddit_parse_body_var = tk.BooleanVar(value=True)
        self.reddit_include_nsfw_var = tk.BooleanVar(value=False)

        # Shared targeting
        self.country_var = tk.StringVar()
        self.africa_var = tk.BooleanVar(value=False)
        self.asia_var = tk.BooleanVar(value=False)
        self.europe_var = tk.BooleanVar(value=False)
        self.north_america_var = tk.BooleanVar(value=False)
        self.oceania_var = tk.BooleanVar(value=False)
        self.south_america_var = tk.BooleanVar(value=False)

        # Shared runtime settings
        self.shared_concurrency_var = tk.StringVar(value="10")
        self.shared_timeout_var = tk.StringVar(value="10")
        self.verbose_var = tk.BooleanVar(value=False)
        self.bulk_probe_enabled_var = tk.BooleanVar(value=False)
        self.bulk_extract_enabled_var = tk.BooleanVar(value=False)
        self.skip_indicator_extract_var = tk.BooleanVar(value=True)

        # Protocol-specific settings
        self.security_mode_var = tk.StringVar(value="cautious")
        self.allow_insecure_tls_var = tk.BooleanVar(value=True)

        # Per-protocol max Shodan results (inline fields, populated from settings at init)
        self.smb_max_results_var = tk.StringVar(value="100")
        self.ftp_max_results_var = tk.StringVar(value="100")
        self.http_max_results_var = tk.StringVar(value="100")

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
            self.provider_shodan_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.provider_shodan", True), True)
            )
            self.provider_searxng_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.provider_searxng", False), False)
            )
            self.provider_reddit_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.provider_reddit", False), False)
            )
            if not (
                self.provider_shodan_var.get()
                or self.provider_searxng_var.get()
                or self.provider_reddit_var.get()
            ):
                self.provider_shodan_var.set(True)
            from gui.components.scan_provider_options import load_reddit_settings, load_searxng_settings
            load_searxng_settings(self, self._settings_manager)
            load_reddit_settings(self, self._settings_manager)
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

        try:
            config_path = resolve_config_path_from_settings(self._settings_manager) or str(self.config_path)
            _initial = load_query_budget_state(
                settings_manager=self._settings_manager,
                config_path=config_path,
            )
            self.smb_max_results_var.set(str(_initial["smb_max_shodan_results_per_scan"]))
            self.ftp_max_results_var.set(str(_initial["ftp_max_shodan_results_per_scan"]))
            self.http_max_results_var.set(str(_initial["http_max_shodan_results_per_scan"]))
        except Exception:
            pass

        # Wire trace callbacks so estimates refresh live as values are typed
        for _v in (self.smb_max_results_var, self.ftp_max_results_var, self.http_max_results_var):
            _v.trace_add("write", lambda *_: self._refresh_protocol_estimate_lines())

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
            self._settings_manager.set_setting("unified_scan_dialog.provider_shodan", bool(self.provider_shodan_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.provider_searxng", bool(self.provider_searxng_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.provider_reddit", bool(self.provider_reddit_var.get()))
            from gui.components.scan_provider_options import persist_reddit_settings, persist_searxng_settings
            persist_searxng_settings(self, self._settings_manager)
            persist_reddit_settings(self, self._settings_manager)

            shared_concurrency = _coerce_int(self.shared_concurrency_var.get(), 1, _CONCURRENCY_UPPER)
            if shared_concurrency is not None:
                self._settings_manager.set_setting("unified_scan_dialog.shared_concurrency", shared_concurrency)

            shared_timeout = _coerce_int(self.shared_timeout_var.get(), 1, _TIMEOUT_UPPER)
            if shared_timeout is not None:
                self._settings_manager.set_setting("unified_scan_dialog.shared_timeout_seconds", shared_timeout)

            self._settings_manager.set_setting("unified_scan_dialog.country_code", self.country_var.get().strip().upper())

            self._settings_manager.set_setting("unified_scan_dialog.verbose", bool(self.verbose_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.bulk_probe_enabled", bool(self.bulk_probe_enabled_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.bulk_extract_enabled", bool(self.bulk_extract_enabled_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.bulk_extract_skip_indicators", bool(self.skip_indicator_extract_var.get()))

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
        from gui.components.unified_scan_layout import build_dialog

        build_dialog(self)

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
            "providers": {
                "shodan": self.provider_shodan_var.get(),
                "searxng": self.provider_searxng_var.get(),
                "reddit": self.provider_reddit_var.get(),
            },
            "searxng_options": {
                "instance_url": self.searxng_instance_url_var.get(),
                "query": self.searxng_query_var.get(),
                "max_results": self.searxng_max_results_var.get(),
                "request_timeout": int(self.searxng_request_timeout_var.get()),
                "short_retry_delay": int(self.searxng_short_retry_delay_var.get()),
                "long_retry_delay": int(self.searxng_long_retry_delay_var.get()),
            },
            "reddit_options": {k: getattr(self, f"reddit_{k}_var").get() for k in ("mode", "sort", "top_window", "max_posts", "query", "username", "parse_body", "include_nsfw")},
            "protocols": {
                "smb": self.protocol_smb_var.get(),
                "ftp": self.protocol_ftp_var.get(),
                "http": self.protocol_http_var.get(),
            },
            "country_code": self.country_var.get(),
            "regions": {
                "africa": self.africa_var.get(),
                "asia": self.asia_var.get(),
                "europe": self.europe_var.get(),
                "north_america": self.north_america_var.get(),
                "oceania": self.oceania_var.get(),
                "south_america": self.south_america_var.get(),
            },
            "shared_concurrency": self.shared_concurrency_var.get(),
            "shared_timeout_seconds": self.shared_timeout_var.get(),
            "verbose": self.verbose_var.get(),
            "bulk_probe_enabled": self.bulk_probe_enabled_var.get(),
            "bulk_extract_enabled": self.bulk_extract_enabled_var.get(),
            "bulk_extract_skip_indicators": self.skip_indicator_extract_var.get(),
            "security_mode": self.security_mode_var.get(),
            "allow_insecure_tls": self.allow_insecure_tls_var.get(),
        }

    def _apply_form_state(self, state: Dict[str, Any]) -> None:
        providers_state = state.get("providers", {})
        self.provider_shodan_var.set(bool(providers_state.get("shodan", True)))
        self.provider_searxng_var.set(bool(providers_state.get("searxng", False)))
        self.provider_reddit_var.set(bool(providers_state.get("reddit", False)))

        from gui.components.scan_provider_options import apply_reddit_form_state, apply_searxng_form_state
        apply_searxng_form_state(self, state.get("searxng_options") or {})
        apply_reddit_form_state(self, state.get("reddit_options") or {})

        protocols = state.get("protocols", {})
        self.protocol_smb_var.set(bool(protocols.get("smb", True)))
        self.protocol_ftp_var.set(bool(protocols.get("ftp", True)))
        self.protocol_http_var.set(bool(protocols.get("http", True)))

        self.country_var.set(state.get("country_code", ""))

        regions = state.get("regions", {})
        self.africa_var.set(bool(regions.get("africa", False)))
        self.asia_var.set(bool(regions.get("asia", False)))
        self.europe_var.set(bool(regions.get("europe", False)))
        self.north_america_var.set(bool(regions.get("north_america", False)))
        self.oceania_var.set(bool(regions.get("oceania", False)))
        self.south_america_var.set(bool(regions.get("south_america", False)))

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

        mode = str(state.get("security_mode", "cautious")).strip().lower()
        self.security_mode_var.set(mode if mode in {"cautious", "legacy"} else "cautious")
        self.allow_insecure_tls_var.set(bool(state.get("allow_insecure_tls", True)))
        self._sync_skip_indicator_extract_state()

        self._sync_targeting_mode_state()
        self._refresh_protocol_estimate_lines()
        self._sync_shodan_options_state()
        self._sync_searxng_options_state()
        self._sync_reddit_options_state()

    def _sync_skip_indicator_extract_state(self) -> None:
        skip_checkbox = getattr(self, "skip_indicator_extract_checkbox", None)
        if skip_checkbox is None:
            return
        state = tk.NORMAL if bool(self.bulk_extract_enabled_var.get()) else tk.DISABLED
        skip_checkbox.configure(state=state)

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
    # Layout state
    # ------------------------------------------------------------------

    def _refresh_provider_queue_label(self) -> str:
        from gui.components.unified_scan_layout import refresh_provider_queue_label

        return refresh_provider_queue_label(self)

    def _sync_shodan_options_state(self, *_args) -> None:
        from gui.components.scan_provider_options import sync_option_entries

        sync_option_entries(
            getattr(self, "_shodan_opts_frame", None),
            self.provider_shodan_var.get(),
        )
        self._refresh_provider_queue_label()

    def _sync_searxng_options_state(self, *_args) -> None:
        from gui.components.scan_provider_options import sync_searxng_option_state

        sync_searxng_option_state(
            getattr(self, "_searxng_opts_frame", None),
            self.provider_searxng_var.get(),
        )
        self._refresh_provider_queue_label()

    def _sync_reddit_options_state(self, *_args) -> None:
        from gui.components.scan_provider_options import sync_reddit_option_state

        sync_reddit_option_state(
            getattr(self, "_reddit_opts_frame", None),
            self.provider_reddit_var.get(),
        )
        self._refresh_provider_queue_label()

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
        """Open non-blocking discovery dork editor with defensive fallback."""
        try:
            show_scan_dork_editor_dialog(
                parent=self.dialog,
                config_path=str(self.config_path),
                settings_manager=self._settings_manager,
            )
            return
        except Exception as editor_exc:
            if self.query_editor_callback:
                try:
                    self.query_editor_callback()
                    return
                except Exception as callback_exc:
                    messagebox.showerror(
                        "Query Editor Error",
                        (
                            "Failed to open discovery dork editor:\n"
                            f"{editor_exc}\n\n"
                            "Fallback query editor also failed:\n"
                            f"{callback_exc}"
                        ),
                        parent=self.dialog,
                    )
                    return

            messagebox.showwarning(
                "Discovery Dorks Unavailable",
                (
                    "Failed to open discovery dork editor.\n"
                    f"Reason: {editor_exc}\n\n"
                    "Falling back to Application Configuration."
                ),
                parent=self.dialog,
            )
            try:
                self._open_config_editor()
            except Exception:
                return

    def _on_cost_estimate_help_clicked(self, _event=None) -> None:
        self._show_cost_estimate_help_dialog()

    def _build_cost_estimate_help_text(self) -> str:
        lines = [
            "• This estimate shows how much raw search data we can pull for API credits spent. "
            "Shodan charges by search pages, not by accessible hosts. "
            "One API credit typically yields roughly 100 search results.",
            "• Max Shodan Results sets the maximum initial candidates each protocol can fetch.",
            "• Estimated cost is about one query credit per 100 candidates requested.",
            "• Initial Shodan search returns a list of candidates, not results. "
            "The subsequent screening process thins out invalid hosts, leaving the operator "
            "with a better quality list of potential hosts to investigate.",
        ]
        return "\n".join(lines)

    def _show_cost_estimate_help_dialog(self) -> None:
        help_dialog = tk.Toplevel(self.dialog)
        help_dialog.title("Cost & Result Estimate Help")
        help_dialog.transient(self.dialog)
        self.theme.apply_to_widget(help_dialog, "main_window")

        frame = tk.Frame(help_dialog)
        self.theme.apply_to_widget(frame, "main_window")
        frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        heading = self.theme.create_styled_label(frame, "Cost & Result Estimate Help", "heading")
        heading.pack(anchor="w")

        body = self.theme.create_styled_label(
            frame,
            self._build_cost_estimate_help_text(),
            "body",
            justify="left",
            anchor="w",
            fg=self.theme.colors["text_secondary"],
            wraplength=470,
        )
        body.pack(anchor="w", pady=(10, 12))

        button_row = tk.Frame(frame)
        self.theme.apply_to_widget(button_row, "main_window")
        button_row.pack(fill=tk.X)

        close_button = tk.Button(button_row, text="Close", command=help_dialog.destroy)
        self.theme.apply_to_widget(close_button, "button_primary")
        close_button.pack(side=tk.RIGHT)

        if self.theme:
            self.theme.apply_theme_to_application(help_dialog)

        ensure_dialog_focus(help_dialog, self.dialog)
        self._try_grab_dialog(help_dialog)
        help_dialog.wait_window()

    def _try_grab_dialog(self, dialog: tk.Toplevel) -> None:
        """Best-effort modal grab for helper dialogs."""
        try:
            dialog.wait_visibility()
        except Exception:
            # Window may already be viewable, or platform may not support visibility wait.
            pass
        try:
            dialog.grab_set()
        except tk.TclError:
            # Some Tk/WM combos reject grab while viewability is racing; keep dialog usable.
            pass

    def _open_reddit_grab(self) -> None:
        """Close this dialog and open the Reddit Grab flow via callback."""
        if not self.reddit_grab_callback:
            return
        self._persist_dialog_state()
        self.result = "cancel"
        self.dialog.destroy()
        self.reddit_grab_callback()

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
        self._sync_targeting_mode_state()

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

    def _resolve_selected_providers(self) -> list[str]:
        providers: list[str] = []
        shodan_var = getattr(self, "provider_shodan_var", None)
        if shodan_var is None or shodan_var.get():
            providers.append("shodan")
        searxng_var = getattr(self, "provider_searxng_var", None)
        if searxng_var is not None and searxng_var.get():
            providers.append("searxng")
        reddit_var = getattr(self, "provider_reddit_var", None)
        if reddit_var is not None and reddit_var.get():
            providers.append("reddit")
        return providers

    def _resolve_selected_protocols(self) -> list[str]:
        protocols: list[str] = []
        if bool(self.protocol_smb_var.get()):
            protocols.append("smb")
        if bool(self.protocol_ftp_var.get()):
            protocols.append("ftp")
        if bool(self.protocol_http_var.get()):
            protocols.append("http")
        return protocols

    def _refresh_protocol_estimate_lines(self) -> None:
        cost_label = getattr(self, "protocol_cost_label", None)
        results_label = getattr(self, "protocol_results_label", None)
        if cost_label is None or results_label is None:
            return

        selected = self._resolve_selected_protocols()
        if not selected:
            cost_label.configure(text="Est. cost: ~0 credits")
            results_label.configure(text="Est. initial results: none selected")
            return

        cap_by_protocol = {
            "smb": max(1, _cap_coerce_int(self.smb_max_results_var.get(), 100, minimum=1, maximum=100000)),
            "ftp": max(1, _cap_coerce_int(self.ftp_max_results_var.get(), 100, minimum=1, maximum=100000)),
            "http": max(1, _cap_coerce_int(self.http_max_results_var.get(), 100, minimum=1, maximum=100000)),
        }

        total_credits = sum(max(1, (cap_by_protocol[p] + 99) // 100) for p in selected)
        cost_label.configure(text=f"Est. cost: ~{total_credits} credits")

        parts = []
        for protocol in selected:
            label = protocol.upper()
            results = cap_by_protocol[protocol]
            parts.append(f"{label} ~{results}")
        results_label.configure(text=f"Est. initial results: {'   '.join(parts)}")

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
        if manual_input.strip() and self._get_selected_region_countries():
            return [], (
                "Use either individual country codes or region selections, not both."
            )

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

    def _sync_targeting_mode_state(self) -> None:
        """Keep manual country entry and region targeting mutually exclusive."""
        manual_active = bool(self.country_var.get().strip())
        region_vars = (
            self.africa_var,
            self.asia_var,
            self.europe_var,
            self.north_america_var,
            self.oceania_var,
            self.south_america_var,
        )
        region_active = any(bool(var.get()) for var in region_vars)

        # Explicit country codes win when restoring an older conflicting state.
        if manual_active and region_active:
            for var in region_vars:
                var.set(False)
            region_active = False
            self._update_region_status()

        country_entry = getattr(self, "country_entry", None)
        if country_entry is not None:
            country_entry.configure(
                state=tk.DISABLED if region_active else tk.NORMAL
            )

        region_state = tk.DISABLED if manual_active else tk.NORMAL
        for widget in getattr(self, "_region_checkbuttons", ()):
            widget.configure(state=region_state)
        for widget in getattr(self, "_region_action_buttons", ()):
            widget.configure(state=region_state)

    def _on_region_selection_changed(self) -> None:
        self._update_region_status()
        self._sync_targeting_mode_state()

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
        self._on_region_selection_changed()

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
        self._on_region_selection_changed()

    # ------------------------------------------------------------------
    # Build/start/cancel
    # ------------------------------------------------------------------

    def _build_scan_request(self) -> Dict[str, Any]:
        providers = self._resolve_selected_providers()
        if not providers:
            raise ValueError("Select at least one discovery provider (Shodan, SearXNG, or Reddit).")

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

        # Protocols only required when Shodan is selected
        if "shodan" in providers:
            protocols = self._resolve_selected_protocols()
            if not protocols:
                raise ValueError("Select at least one protocol (SMB, FTP, or HTTP).")
        else:
            protocols = []

        # SearXNG options required when SearXNG is selected
        instance_url = ""
        searxng_query = ""
        searxng_max_results = 500
        if "searxng" in providers:
            _url_var = getattr(self, "searxng_instance_url_var", None)
            instance_url = str(_url_var.get() if _url_var else "").strip()
            if not instance_url:
                raise ValueError("SearXNG instance URL is required when SearXNG is selected.")
            _q_var = getattr(self, "searxng_query_var", None)
            searxng_query = str(_q_var.get() if _q_var else "").strip()
            if not searxng_query:
                raise ValueError("SearXNG search query is required when SearXNG is selected.")
            from gui.components.scan_provider_options import validate_searxng_max_results
            _mr_var = getattr(self, "searxng_max_results_var", None)
            searxng_max_results = validate_searxng_max_results(_mr_var.get() if _mr_var else "500")

        reddit_opts: Dict[str, Any] = {}
        if "reddit" in providers:
            from gui.components.scan_provider_options import validate_reddit_scan_options
            reddit_opts = validate_reddit_scan_options({k: getattr(self, f"reddit_{k}_var").get() for k in ("mode", "sort", "top_window", "max_posts", "query", "username", "parse_body", "include_nsfw")})

        manual_input = self.country_var.get().strip()
        countries, err = self._get_all_selected_countries(manual_input)
        if err:
            raise ValueError(err)
        country_param = ",".join(countries) if countries else None

        mode = (self.security_mode_var.get() or "cautious").strip().lower()
        if mode not in {"cautious", "legacy"}:
            mode = "cautious"

        smb_cap = _cap_coerce_int(self.smb_max_results_var.get(), 100, minimum=1, maximum=100000)
        ftp_cap = _cap_coerce_int(self.ftp_max_results_var.get(), 100, minimum=1, maximum=100000)
        http_cap = _cap_coerce_int(self.http_max_results_var.get(), 100, minimum=1, maximum=100000)

        try:
            persist_query_budget_state(self._settings_manager, {
                "smb_max_shodan_results_per_scan": smb_cap,
                "ftp_max_shodan_results_per_scan": ftp_cap,
                "http_max_shodan_results_per_scan": http_cap,
            })
        except Exception:
            pass

        self._persist_dialog_state()

        request: Dict[str, Any] = {
            "providers": providers,
            "protocols": protocols,
            "country": country_param,
            "shared_concurrency": shared_concurrency,
            "shared_timeout_seconds": shared_timeout,
            "verbose": bool(self.verbose_var.get()),
            "bulk_probe_enabled": bool(self.bulk_probe_enabled_var.get()),
            "bulk_extract_enabled": bool(self.bulk_extract_enabled_var.get()),
            "bulk_extract_skip_indicators": bool(self.skip_indicator_extract_var.get()),
            "security_mode": mode,
            "allow_insecure_tls": bool(self.allow_insecure_tls_var.get()),
            "smb_max_shodan_results_per_scan": smb_cap,
            "ftp_max_shodan_results_per_scan": ftp_cap,
            "http_max_shodan_results_per_scan": http_cap,
            "smb_max_query_credits_per_scan": _credits_for_cap(smb_cap),
            "ftp_max_query_credits_per_scan": _credits_for_cap(ftp_cap),
            "http_max_query_credits_per_scan": _credits_for_cap(http_cap),
        }
        if "searxng" in providers:
            request["searxng_instance_url"] = instance_url
            request["searxng_query"] = searxng_query
            request["searxng_max_results"] = searxng_max_results
            from gui.components.scan_provider_options import (
                coerce_searxng_tuning,
                SEARXNG_TIMEOUT_DEFAULT, SEARXNG_TIMEOUT_MIN, SEARXNG_TIMEOUT_MAX,
                SEARXNG_SHORT_RETRY_DEFAULT, SEARXNG_SHORT_RETRY_MIN, SEARXNG_SHORT_RETRY_MAX,
                SEARXNG_LONG_RETRY_DEFAULT, SEARXNG_LONG_RETRY_MIN, SEARXNG_LONG_RETRY_MAX,
            )
            request["searxng_request_timeout"] = coerce_searxng_tuning(
                self.searxng_request_timeout_var.get(),
                default=SEARXNG_TIMEOUT_DEFAULT, lo=SEARXNG_TIMEOUT_MIN,
                hi=SEARXNG_TIMEOUT_MAX, step=1,
            )
            request["searxng_short_retry_delay"] = coerce_searxng_tuning(
                self.searxng_short_retry_delay_var.get(),
                default=SEARXNG_SHORT_RETRY_DEFAULT, lo=SEARXNG_SHORT_RETRY_MIN,
                hi=SEARXNG_SHORT_RETRY_MAX, step=5,
            )
            request["searxng_long_retry_delay"] = coerce_searxng_tuning(
                self.searxng_long_retry_delay_var.get(),
                default=SEARXNG_LONG_RETRY_DEFAULT, lo=SEARXNG_LONG_RETRY_MIN,
                hi=SEARXNG_LONG_RETRY_MAX, step=30,
            )
        if "reddit" in providers:
            request.update(reddit_opts)
        return request

    def _start(self) -> None:
        self._persist_dialog_state()
        try:
            scan_request = self._build_scan_request()
        except ValueError as exc:
            messagebox.showerror("Invalid Input", str(exc), parent=self.dialog)
            return

        # Build scan description including provider and protocol context (M1)
        provider_label = ", ".join(p.upper() for p in scan_request["providers"])
        protocol_label = ", ".join(p.upper() for p in scan_request["protocols"])
        country_desc = scan_request.get("country") or "global"
        if protocol_label:
            scan_desc = f"providers: {provider_label}; protocols: {protocol_label}; target: {country_desc}"
        else:
            scan_desc = f"providers: {provider_label}; target: {country_desc}"

        # Extract is Shodan protocol-completion only — silence it for SearXNG-only (M4)
        if "shodan" not in scan_request.get("providers", []):
            scan_request["bulk_extract_enabled"] = False

        # Skip Shodan preflight (credit estimate, API key gate) for SearXNG-only (M1/M5)
        if "shodan" in scan_request.get("providers", []):
            preflight_result = run_preflight(
                self.dialog,
                self.theme,
                self._settings_manager,
                scan_request,
                scan_desc,
            )
            if preflight_result is None:
                return
            scan_request = preflight_result

        self.result = "start"
        self.scan_start_callback(scan_request)
        self.dialog.destroy()

    def _cancel(self) -> None:
        self._persist_dialog_state()
        self.result = "cancel"
        self.dialog.destroy()

    def show(self) -> Optional[str]:
        self.parent.wait_window(self.dialog)
        return self.result

    def focus_dialog(self) -> None:
        """Bring the existing dialog instance to front."""
        try:
            self.dialog.deiconify()
            ensure_dialog_focus(self.dialog, self.parent)
        except Exception:
            pass


_ACTIVE_UNIFIED_SCAN_DIALOG: Optional[UnifiedScanDialog] = None


def _dialog_instance_is_live(instance: Optional[UnifiedScanDialog]) -> bool:
    if instance is None:
        return False
    try:
        return bool(instance.dialog.winfo_exists())
    except Exception:
        return False


def show_unified_scan_dialog(
    parent: tk.Widget,
    config_path: str,
    scan_start_callback: Callable[[Dict[str, Any]], None],
    settings_manager: Optional[Any] = None,
    config_editor_callback: Optional[Callable[[str], None]] = None,
    query_editor_callback: Optional[Callable[[], None]] = None,
    reddit_grab_callback: Optional[Callable[[], None]] = None,
) -> Optional[str]:
    """Show the unified scan launch dialog as a single-instance window."""
    global _ACTIVE_UNIFIED_SCAN_DIALOG
    if _dialog_instance_is_live(_ACTIVE_UNIFIED_SCAN_DIALOG):
        _ACTIVE_UNIFIED_SCAN_DIALOG.focus_dialog()
        return None

    dialog = UnifiedScanDialog(
        parent=parent,
        config_path=config_path,
        scan_start_callback=scan_start_callback,
        settings_manager=settings_manager,
        config_editor_callback=config_editor_callback,
        query_editor_callback=query_editor_callback,
        reddit_grab_callback=reddit_grab_callback,
    )
    _ACTIVE_UNIFIED_SCAN_DIALOG = dialog
    try:
        return dialog.show()
    finally:
        if _ACTIVE_UNIFIED_SCAN_DIALOG is dialog:
            _ACTIVE_UNIFIED_SCAN_DIALOG = None
