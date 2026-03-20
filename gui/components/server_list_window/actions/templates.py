"""
Server List Filter Template & Preferences Mixin

Handles filter preference persistence, country selections, and template CRUD
for the server list window.
"""

import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Dict, Any, Optional

from gui.components.server_list_window import filters, table
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.template_store import TemplateStore


class ServerListWindowTemplateMixin:
    def _restore_country_filter_selections(self) -> None:
        """Restore saved country filter selections from the listbox."""
        if not self.country_listbox or not self.country_code_list:
            return

        saved_codes = self.settings_manager.get_setting('windows.server_list.selected_countries', []) if self.settings_manager else []
        saved_codes_set = set(saved_codes)

        self.country_listbox.selection_clear(0, tk.END)
        for idx, code in enumerate(self.country_code_list):
            if code in saved_codes_set:
                self.country_listbox.selection_set(idx)

    def _get_selected_country_codes(self):
        """Get selected country codes from listbox."""
        if not self.country_listbox:
            return []
        return [self.country_code_list[i] for i in self.country_listbox.curselection()]

    def _get_selected_protocol_types(self):
        """Return selected protocol host_type values in S/F/H form."""
        selected = []
        if bool(self.protocol_smb.get()):
            selected.append("S")
        if bool(self.protocol_ftp.get()):
            selected.append("F")
        if bool(self.protocol_http.get()):
            selected.append("H")
        return selected

    def _set_selected_protocol_types(self, selected_types):
        """Apply selected protocol host_type values to UI vars."""
        selected = {str(v).upper() for v in (selected_types or [])}
        self.protocol_smb.set("S" in selected)
        self.protocol_ftp.set("F" in selected)
        self.protocol_http.set("H" in selected)

    def _toggle_show_all_results(self) -> None:
        """Toggle showing all results when in recent-filter mode."""
        self.filter_recent = not self.filter_recent
        self._update_mode_display()
        self._apply_filters(force=True)

    def _reset_filters(self) -> None:
        """Reset filters to default values and refresh list."""
        self.search_text.set("")
        self.date_filter.set("All")
        self.shares_filter.set(True)
        self.favorites_only.set(False)
        self.exclude_avoid.set(False)
        self.probed_only.set(False)
        self.exclude_compromised.set(False)
        self.protocol_smb.set(True)
        self.protocol_ftp.set(True)
        self.protocol_http.set(True)
        self.country_filter_text.set("")

        if self.country_listbox:
            self.country_listbox.selection_clear(0, tk.END)
            # Repopulate to show all countries (in case filter text was active)
            self._on_country_filter_text_changed()

        # Clear any selected filter template to avoid implying an applied template
        self.filter_template_var.set(self.FILTER_TEMPLATE_PLACEHOLDER)
        self._selected_filter_template_slug = None
        self._set_last_filter_template_slug(None)
        self._refresh_filter_templates(select_slug=None)

        self._apply_filters()

    def _clear_countries(self) -> None:
        """Clear country filter selections without affecting other filters."""
        self.country_filter_text.set("")
        if self.country_listbox:
            self.country_listbox.selection_clear(0, tk.END)
            # Repopulate to show all countries (in case filter text was active)
            self._on_country_filter_text_changed()
        self._apply_filters()

    def _on_country_filter_text_changed(self) -> None:
        """Filter country listbox based on typed text."""
        if not self.country_listbox or not hasattr(self, 'country_full_data'):
            return

        filter_text = self.country_filter_text.get().upper()

        # Save current selections
        saved_selections = set(self._get_selected_country_codes())

        # Clear and repopulate with filtered items
        self.country_listbox.delete(0, tk.END)
        self.country_code_list = []

        for code, count in self.country_full_data:
            if not filter_text or code.startswith(filter_text):
                display_text = f"{code} ({count})"
                self.country_listbox.insert(tk.END, display_text)
                self.country_code_list.append(code)
                # Restore selection if it was selected
                if code in saved_selections:
                    self.country_listbox.selection_set(tk.END)

    def _load_filter_preferences(self) -> None:
        """Load filter preferences from settings manager if available."""
        if not self.settings_manager:
            return

        prefs = self.settings_manager.get_setting('windows.server_list.filter_preferences', {})
        if not prefs:
            return

        try:
            self.search_text.set(prefs.get('search_text', ''))
            self.date_filter.set(prefs.get('date_filter', 'All'))
            self.shares_filter.set(prefs.get('shares_filter', True))
            self.favorites_only.set(prefs.get('favorites_only', False))
            self.exclude_avoid.set(prefs.get('exclude_avoid', False))
            self.probed_only.set(prefs.get('probed_only', False))
            self.exclude_compromised.set(prefs.get('exclude_compromised', False))
            self._set_selected_protocol_types(prefs.get('protocol_types', ['S', 'F', 'H']))
        except Exception:
            # Graceful degradation if settings are malformed
            pass

    def _persist_filter_preferences(self) -> None:
        """Persist filter preferences to settings manager if available."""
        if not self.settings_manager:
            return

        prefs = {
            'search_text': self.search_text.get(),
            'date_filter': self.date_filter.get(),
            'shares_filter': bool(self.shares_filter.get()),
            'favorites_only': bool(self.favorites_only.get()),
            'exclude_avoid': bool(self.exclude_avoid.get()),
            'probed_only': bool(self.probed_only.get()),
            'exclude_compromised': bool(self.exclude_compromised.get()),
            'protocol_types': self._get_selected_protocol_types(),
        }

        if self.country_listbox:
            prefs['country_codes'] = self._get_selected_country_codes()

        try:
            self.settings_manager.set_setting('windows.server_list.filter_preferences', prefs)
        except Exception:
            pass

    def _get_last_filter_template_slug(self) -> Optional[str]:
        if not self.settings_manager:
            return None
        return self.settings_manager.get_setting('windows.server_list.filter_template_last_used', None)

    def _set_last_filter_template_slug(self, slug: Optional[str]) -> None:
        if not self.settings_manager:
            return
        self.settings_manager.set_setting('windows.server_list.filter_template_last_used', slug)

    def _refresh_filter_templates(self, select_slug: Optional[str] = None) -> None:
        """Refresh filter template dropdown options."""
        if 'filter_template_delete_button' in self.filter_widgets:
            delete_btn = self.filter_widgets['filter_template_delete_button']
            delete_btn.config(state=tk.NORMAL if self.filter_template_store else tk.DISABLED)

        if 'filter_template_dropdown' not in self.filter_widgets or not self.filter_template_store:
            return

        dropdown = self.filter_widgets['filter_template_dropdown']
        templates = self.filter_template_store.list_templates()
        self._filter_template_label_to_slug = {tpl.name: tpl.slug for tpl in templates}
        labels = sorted(self._filter_template_label_to_slug.keys())

        dropdown.configure(values=labels or [self.FILTER_TEMPLATE_PLACEHOLDER])

        target_slug = select_slug or self._get_last_filter_template_slug()
        if target_slug is None:
            # Explicit request to clear selection
            self.filter_template_var.set(self.FILTER_TEMPLATE_PLACEHOLDER)
            self._selected_filter_template_slug = None
        elif target_slug in self._filter_template_label_to_slug.values():
            target_label = next((name for name, slug in self._filter_template_label_to_slug.items() if slug == target_slug), self.FILTER_TEMPLATE_PLACEHOLDER)
            self.filter_template_var.set(target_label)
            self._selected_filter_template_slug = self._filter_template_label_to_slug.get(target_label)
        else:
            self.filter_template_var.set(self.FILTER_TEMPLATE_PLACEHOLDER)
            self._selected_filter_template_slug = None

    def _capture_filter_state(self) -> Dict[str, Any]:
        """Capture current filter state for template saving."""
        return {
            'search_text': self.search_text.get(),
            'date_filter': self.date_filter.get(),
            'shares_filter': bool(self.shares_filter.get()),
            'favorites_only': bool(self.favorites_only.get()),
            'exclude_avoid': bool(self.exclude_avoid.get()),
            'probed_only': bool(self.probed_only.get()),
            'exclude_compromised': bool(self.exclude_compromised.get()),
            'protocol_types': self._get_selected_protocol_types(),
            'country_codes': self._get_selected_country_codes(),
            'advanced_mode': bool(self.is_advanced_mode),
        }

    def _apply_filter_state(self, state: Dict[str, Any]) -> None:
        """Apply a saved filter state to UI and refresh results."""
        self.search_text.set(state.get('search_text', ''))
        self.date_filter.set(state.get('date_filter', 'All'))
        self.shares_filter.set(bool(state.get('shares_filter', False)))
        self.favorites_only.set(bool(state.get('favorites_only', False)))
        self.exclude_avoid.set(bool(state.get('exclude_avoid', False)))
        self.probed_only.set(bool(state.get('probed_only', False)))
        self.exclude_compromised.set(bool(state.get('exclude_compromised', False)))
        self._set_selected_protocol_types(state.get('protocol_types', ['S', 'F', 'H']))

        if self.country_listbox:
            self.country_listbox.selection_clear(0, tk.END)
            selected = set(state.get('country_codes', []))
            for idx, code in enumerate(self.country_code_list):
                if code in selected:
                    self.country_listbox.selection_set(idx)

        if state.get('advanced_mode') is not None:
            self.is_advanced_mode = bool(state['advanced_mode'])
            self._update_mode_display()

        self._apply_filters()

    def _on_filter_template_selected(self) -> None:
        """Handle template selection change."""
        if not self.filter_template_store:
            return

        label = self.filter_template_var.get()
        if not label or label == self.FILTER_TEMPLATE_PLACEHOLDER:
            self._selected_filter_template_slug = None
            return

        slug = self._filter_template_label_to_slug.get(label)
        self._selected_filter_template_slug = slug

        template = self.filter_template_store.load_template(slug) if self.filter_template_store else None
        if template:
            self._apply_filter_state(template.form_state)
            self._set_last_filter_template_slug(slug)

    def _on_save_filter_template(self) -> None:
        """Save current filter state as a template."""
        if not self.filter_template_store:
            messagebox.showinfo(
                "Filter Template",
                "Filter template storage is unavailable.",
                parent=self.window
            )
            return

        current_label = self.filter_template_var.get()
        has_selection = bool(current_label and current_label != self.FILTER_TEMPLATE_PLACEHOLDER and self._selected_filter_template_slug)
        selected_slug = self._selected_filter_template_slug if has_selection else None

        def _prompt_name(initial: str = "") -> Optional[str]:
            name = simpledialog.askstring(
                "Save Filter Template",
                "Template name:",
                parent=self.window,
                initialvalue=initial
            )
            if not name:
                return None
            name = name.strip()
            return name or None

        def _prompt_overwrite_choice(label: str) -> Optional[str]:
            """Custom prompt to choose update vs save-as-new with explicit button labels."""
            choice = {"value": None}
            dialog = tk.Toplevel(self.window)
            dialog.title("Save Filter Template")
            dialog.transient(self.window)
            dialog.grab_set()
            ensure_dialog_focus(dialog, self.window)

            tk.Label(dialog, text=f"Update existing template '{label}'?").pack(padx=20, pady=(15, 10))
            buttons = tk.Frame(dialog)
            buttons.pack(pady=(0, 15))

            def _set(val):
                choice["value"] = val
                dialog.destroy()

            tk.Button(buttons, text="Update", command=lambda: _set("update")).pack(side=tk.LEFT, padx=5)
            tk.Button(buttons, text="New Template", command=lambda: _set("new")).pack(side=tk.LEFT, padx=5)
            tk.Button(buttons, text="Cancel", command=lambda: _set(None)).pack(side=tk.LEFT, padx=5)

            dialog.wait_window()
            return choice["value"]

        # Decide whether to overwrite or save-as-new
        save_as_new = False
        if selected_slug:
            choice = _prompt_overwrite_choice(current_label)
            if choice is None:
                return  # cancel
            if choice == "update":
                name = current_label
                slug = selected_slug
            else:
                # Save as new template
                save_as_new = True
                name = _prompt_name(f"{current_label} copy")
                if not name:
                    return
                slug = TemplateStore.slugify(name)
        else:
            # No selection → create new template
            name = current_label if current_label and current_label != self.FILTER_TEMPLATE_PLACEHOLDER else None
            if not name:
                name = _prompt_name()
                if not name:
                    return
            slug = TemplateStore.slugify(name)

        # If target slug exists and it's not the selected one, confirm overwrite
        existing = self.filter_template_store.load_template(slug)
        if existing and (slug != selected_slug or save_as_new):
            overwrite = messagebox.askyesno(
                "Overwrite Template",
                f"Template '{name}' already exists. Overwrite?",
                parent=self.window
            )
            if not overwrite:
                return

        template = self.filter_template_store.save_template(name, self._capture_filter_state())
        self._set_last_filter_template_slug(template.slug)
        self._refresh_filter_templates(select_slug=template.slug)

    def _on_delete_filter_template(self) -> None:
        """Delete the selected filter template."""
        if not self.filter_template_store:
            messagebox.showinfo(
                "Filter Template",
                "Filter template storage is unavailable.",
                parent=self.window
            )
            return

        label = self.filter_template_var.get()
        if not label or label == self.FILTER_TEMPLATE_PLACEHOLDER:
            messagebox.showinfo(
                "Delete Filter Template",
                "Select a template to delete.",
                parent=self.window
            )
            return

        slug = self._filter_template_label_to_slug.get(label)
        if not slug:
            messagebox.showwarning(
                "Delete Filter Template",
                "Template not found.",
                parent=self.window
            )
            return

        confirmed = messagebox.askyesno(
            "Delete Filter Template",
            f"Delete template '{label}'? This cannot be undone.",
            parent=self.window
        )
        if not confirmed:
            return

        deleted = self.filter_template_store.delete_template(slug)
        if deleted:
            messagebox.showinfo(
                "Delete Filter Template",
                f"Template '{label}' deleted.",
                parent=self.window
            )
        else:
            messagebox.showwarning(
                "Delete Filter Template",
                "Failed to delete template.",
                parent=self.window
            )

        if self._get_last_filter_template_slug() == slug:
            self._set_last_filter_template_slug(None)

        self.filter_template_var.set(self.FILTER_TEMPLATE_PLACEHOLDER)
        self._selected_filter_template_slug = None
        self._refresh_filter_templates()

    def _refresh_data(self) -> None:
        """Refresh server data from the database."""
        self._load_data()
        self._persist_filter_preferences()

    def _close_window(self) -> None:
        """Close the window and persist filter prefs."""
        self._persist_filter_preferences()
        if hasattr(self, "_cancel_initial_data_load"):
            try:
                self._cancel_initial_data_load()
            except Exception:
                pass
        if self.window is not None:
            self.window.destroy()

    def restore_and_focus(self) -> None:
        """Restore window focus (public adapter)."""
        if self.window:
            self.window.deiconify()
            self.window.lift()
            ensure_dialog_focus(self.window, self.parent)

    def apply_recent_discoveries_filter(self) -> None:
        """Apply filter to show only recent discoveries (used by dashboard)."""
        try:
            if hasattr(self, "_cancel_initial_data_load"):
                self._cancel_initial_data_load()

            # Clear existing filters first
            self.search_text.set("")
            self.date_filter.set("All")

            # Load servers with recent scan filter
            servers, total_count = self.db_reader.get_server_list(
                limit=None,
                offset=0,
                country_codes=None,
                favorites_only=False,
                probed_only=False,
                exclude_avoid=False,
                exclude_compromised=False,
                shares_filter=False,
                recent_discovery_only=True
            )

            self.all_servers = servers
            self.filtered_servers = servers
            self.count_label.config(text=f"Showing {len(servers)} of {total_count} servers")

            table.populate_server_table(
                self.tree,
                self.filtered_servers,
                self.country_code_list,
                attach_probe_status=self._attach_probe_status
            )

            self._reset_sort_state()
            self._on_selection_changed()
            self._update_action_buttons_state()
            self._update_context_menu_state()

        except Exception as e:
            messagebox.showerror(
                "Recent Discoveries",
                f"Failed to apply recent discoveries filter: {e}",
                parent=self.window
            )
