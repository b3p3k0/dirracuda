"""
Template lifecycle mixin for UnifiedScanDialog.

Extracted from unified_scan_dialog.py (Slice 4A refactor).
All methods reference ``self.*`` attributes defined on UnifiedScanDialog and
resolve correctly via Python's MRO when this mixin is listed as a base class.

Do NOT import from unified_scan_dialog.py — that would create a circular import.
``TEMPLATE_PLACEHOLDER_TEXT`` is intentionally left on UnifiedScanDialog and
accessed here as ``self.TEMPLATE_PLACEHOLDER_TEXT``.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Any, Dict, Optional

from gui.utils.template_store import TemplateStore


class _UnifiedScanDialogTemplateMixin:
    """Mixin — template lifecycle methods only; no ``__init__``."""

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
