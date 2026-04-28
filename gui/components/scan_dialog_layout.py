"""
Layout/template helpers extracted from ScanDialog.
"""

import csv
import io
import json
import os
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

import tkinter as tk
from tkinter import simpledialog, ttk

from gui.utils import safe_messagebox as messagebox

def _create_template_toolbar(self, parent_frame: tk.Frame) -> None:
    """Create template selector + actions above scan parameters."""
    toolbar = tk.Frame(parent_frame)
    self.theme.apply_to_widget(toolbar, "card")
    toolbar.pack(fill=tk.X, padx=15, pady=(10, 0))

    label = self.theme.create_styled_label(
        toolbar,
        "Templates:",
        "body"
    )
    label.pack(side=tk.LEFT)

    self.template_dropdown = ttk.Combobox(
        toolbar,
        textvariable=self.template_var,
        state="readonly",
        width=32
    )
    self.template_dropdown.pack(side=tk.LEFT, padx=(10, 10))
    self.template_dropdown.bind("<<ComboboxSelected>>", self._handle_template_selected)

    save_button = tk.Button(
        toolbar,
        text="💾 Save Current",
        command=self._prompt_save_template,
        font=self.theme.fonts["small"]
    )
    self.theme.apply_to_widget(save_button, "button_secondary")
    save_button.pack(side=tk.LEFT, padx=(0, 5))

    self.delete_template_button = tk.Button(
        toolbar,
        text="🗑 Delete",
        command=self._delete_selected_template,
        font=self.theme.fonts["small"]
    )
    self.theme.apply_to_widget(self.delete_template_button, "button_secondary")
    self.delete_template_button.pack(side=tk.LEFT)

    self._refresh_template_toolbar()

def _create_accent_heading(self, parent: tk.Widget, text: str) -> tk.Label:
    """Create a heading label with accent background for readability."""
    label = tk.Label(
        parent,
        text=text,
        anchor="w",
        padx=10,
        pady=4,
        bg=self.theme.colors["accent"],
        fg="white",
        font=self.theme.fonts["heading"]
    )
    return label

def _refresh_template_toolbar(self, select_slug: Optional[str] = None) -> None:
    """Refresh template dropdown values."""
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
        label = slug_to_label[desired_slug]
        self.template_var.set(label)
        self._selected_template_slug = desired_slug
        self.delete_template_button.configure(state=tk.NORMAL)
    else:
        self.template_var.set(placeholder)
        self._selected_template_slug = None
        self.delete_template_button.configure(state=tk.DISABLED)

def _handle_template_selected(self, _event=None) -> None:
    """Apply template when user selects it from dropdown."""
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
    """Return the display name of the currently selected template, if any."""
    label = self.template_var.get()
    if label == self.TEMPLATE_PLACEHOLDER_TEXT:
        return None
    return label.strip() if label else None

def _prompt_save_template(self) -> None:
    """Ask for template name and persist current form state."""
    initial_name = self._get_selected_template_name()

    name = simpledialog.askstring(
        "Save Template",
        "Template name:",
        parent=self.dialog,
        initialvalue=initial_name or ""
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
            parent=self.dialog
        )
        if not overwrite:
            return

    form_state = self._capture_form_state()
    template = self.template_store.save_template(name, form_state)
    self._refresh_template_toolbar(select_slug=template.slug)
    messagebox.showinfo("Template Saved", f"Template '{name}' saved.")

def _delete_selected_template(self) -> None:
    """Delete currently selected template."""
    slug = self._selected_template_slug
    if not slug:
        messagebox.showinfo("Delete Template", "No template selected.")
        return

    label = self.template_var.get()
    confirmed = messagebox.askyesno(
        "Delete Template",
        f"Delete template '{label}'?",
        parent=self.dialog
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
    """Capture current ScanDialog form state for template storage."""
    return {
        "custom_filters": self.custom_filters_var.get(),
        "country_code": self.country_var.get(),
        "regions": {
            "africa": self.africa_var.get(),
            "asia": self.asia_var.get(),
            "europe": self.europe_var.get(),
            "north_america": self.north_america_var.get(),
            "oceania": self.oceania_var.get(),
            "south_america": self.south_america_var.get()
        },
        "recent_hours": self.recent_hours_var.get(),
        "rescan_all": self.rescan_all_var.get(),
        "rescan_failed": self.rescan_failed_var.get(),
        "discovery_concurrency": self.discovery_concurrency_var.get(),
        "access_concurrency": self.access_concurrency_var.get(),
        "rate_limit_delay": self.rate_limit_delay_var.get(),
        "share_access_delay": self.share_access_delay_var.get(),
        "api_key_override": self.api_key_var.get(),
        "verbose": self.verbose_var.get(),
        "rce_enabled": self.rce_enabled_var.get(),
        "bulk_probe_enabled": self.bulk_probe_enabled_var.get(),
        "bulk_extract_enabled": self.bulk_extract_enabled_var.get(),
        "bulk_extract_skip_indicators": self.skip_indicator_extract_var.get()
    }

def _apply_form_state(self, state: Dict[str, Any]) -> None:
    """Populate form fields from saved template state."""
    self.custom_filters_var.set(state.get("custom_filters", ""))
    self.country_var.set(state.get("country_code", ""))

    regions = state.get("regions", {})
    self.africa_var.set(bool(regions.get("africa", False)))
    self.asia_var.set(bool(regions.get("asia", False)))
    self.europe_var.set(bool(regions.get("europe", False)))
    self.north_america_var.set(bool(regions.get("north_america", False)))
    self.oceania_var.set(bool(regions.get("oceania", False)))
    self.south_america_var.set(bool(regions.get("south_america", False)))

    recent_hours = state.get("recent_hours")
    self.recent_hours_var.set("" if recent_hours in (None, "") else str(recent_hours))

    self.rescan_all_var.set(bool(state.get("rescan_all", False)))
    self.rescan_failed_var.set(bool(state.get("rescan_failed", False)))

    security_mode = state.get("security_mode")
    if security_mode in ("cautious", "legacy"):
        self.security_mode_var.set(security_mode)

    for var, key in [
        (self.discovery_concurrency_var, "discovery_concurrency"),
        (self.access_concurrency_var, "access_concurrency"),
        (self.rate_limit_delay_var, "rate_limit_delay"),
        (self.share_access_delay_var, "share_access_delay")
    ]:
        value = state.get(key)
        if value is not None:
            var.set(str(value))

    self.api_key_var.set(state.get("api_key_override", ""))
    self.verbose_var.set(bool(state.get("verbose", False)))

    # RCE analysis setting (with backward compatibility)
    self.rce_enabled_var.set(bool(state.get("rce_enabled", False)))

    # Bulk operation settings (with backward compatibility)
    self.bulk_probe_enabled_var.set(bool(state.get("bulk_probe_enabled", False)))
    self.bulk_extract_enabled_var.set(bool(state.get("bulk_extract_enabled", False)))
    self.skip_indicator_extract_var.set(bool(state.get("bulk_extract_skip_indicators", True)))
    self._sync_skip_indicator_extract_state()

    self._update_region_status()

def _sync_skip_indicator_extract_state(self) -> None:
    """Enable skip-indicator toggle only when bulk extract is enabled."""
    skip_checkbox = getattr(self, "skip_indicator_extract_checkbox", None)
    if skip_checkbox is None:
        return
    state = tk.NORMAL if bool(self.bulk_extract_enabled_var.get()) else tk.DISABLED
    skip_checkbox.configure(state=state)

def _apply_template_by_slug(self, slug: str, *, silent: bool = False) -> None:
    """Load template by slug and populate form."""
    template = self.template_store.load_template(slug)
    if not template:
        if not silent:
            messagebox.showwarning("Template Missing", "Selected template could not be loaded.", parent=self.dialog)
        self._refresh_template_toolbar()
        return

    self._apply_form_state(template.form_state)
    self.template_store.set_last_used(slug)
    self._selected_template_slug = slug

def _create_region_selection(self, parent_frame: tk.Frame) -> None:
    """Create region selection with checkboxes."""
    region_container = tk.Frame(parent_frame)
    self.theme.apply_to_widget(region_container, "card")
    region_container.pack(fill=tk.X, padx=15, pady=(0, 10))

    # Section title
    title_heading = self._create_accent_heading(
        region_container,
        "📍 Region Selection"
    )
    title_heading.pack(fill=tk.X, pady=(0, 10))

    # Region checkboxes in a compact 3x2 grid
    checkboxes_frame = tk.Frame(region_container)
    self.theme.apply_to_widget(checkboxes_frame, "card")
    checkboxes_frame.pack(fill=tk.X, pady=(5, 5))

    # Create region checkboxes in 3 columns
    regions = [
        ("Africa", self.africa_var),
        ("Asia", self.asia_var),
        ("Europe", self.europe_var),
        ("North America", self.north_america_var),
        ("Oceania", self.oceania_var),
        ("South America", self.south_america_var)
    ]

    for i, (region_name, region_var) in enumerate(regions):
        row = i // 3
        col = i % 3

        # Create checkbox
        checkbox = tk.Checkbutton(
            checkboxes_frame,
            text=f"{region_name} ({len(self.REGIONS[region_name])})",
            variable=region_var,
            font=self.theme.fonts["small"],
            command=self._update_region_status
        )
        self.theme.apply_to_widget(checkbox, "checkbox")
        checkbox.grid(row=row, column=col, sticky="w", padx=5, pady=2)

    # Quick action buttons and status
    bottom_frame = tk.Frame(region_container)
    self.theme.apply_to_widget(bottom_frame, "card")
    bottom_frame.pack(fill=tk.X, pady=(5, 10))

    # Action buttons on the left
    actions_frame = tk.Frame(bottom_frame)
    self.theme.apply_to_widget(actions_frame, "card")
    actions_frame.pack(side=tk.LEFT)

    select_all_button = tk.Button(
        actions_frame,
        text="Select All",
        command=self._select_all_regions,
        font=self.theme.fonts["small"]
    )
    self.theme.apply_to_widget(select_all_button, "button_secondary")
    select_all_button.pack(side=tk.LEFT, padx=(0, 5))

    clear_button = tk.Button(
        actions_frame,
        text="Clear All",
        command=self._clear_all_regions,
        font=self.theme.fonts["small"]
    )
    self.theme.apply_to_widget(clear_button, "button_secondary")
    clear_button.pack(side=tk.LEFT)

    # Status label on the right
    self.region_status_label = self.theme.create_styled_label(
        bottom_frame,
        "",
        "small",
        fg=self.theme.colors["text_secondary"]
    )
    self.region_status_label.pack(side=tk.RIGHT, padx=(10, 5))

    # Initialize status display
    self._update_region_status()

def _update_region_status(self) -> None:
    """Update the status label showing selected regions and country count."""
    selected_regions = []
    total_countries = 0

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
            selected_regions.append(region_name)
            total_countries += len(self.REGIONS[region_name])

    if selected_regions:
        if len(selected_regions) == 1:
            status_text = f"{selected_regions[0]} ({total_countries} countries)"
        else:
            status_text = f"{len(selected_regions)} regions ({total_countries} countries)"
    else:
        status_text = ""

    self.region_status_label.configure(text=status_text)

def _select_all_regions(self) -> None:
    """Select all regional checkboxes."""
    self.africa_var.set(True)
    self.asia_var.set(True)
    self.europe_var.set(True)
    self.north_america_var.set(True)
    self.oceania_var.set(True)
    self.south_america_var.set(True)
    self._update_region_status()

def _clear_all_regions(self) -> None:
    """Clear all regional checkboxes."""
    self.africa_var.set(False)
    self.asia_var.set(False)
    self.europe_var.set(False)
    self.north_america_var.set(False)
    self.oceania_var.set(False)
    self.south_america_var.set(False)
    self._update_region_status()

def _create_custom_filters_option(self, parent_frame: tk.Frame) -> None:
    """Create custom Shodan filters input option with helper link."""
    filters_container = tk.Frame(parent_frame)
    self.theme.apply_to_widget(filters_container, "card")
    filters_container.pack(fill=tk.X, padx=15, pady=(0, 10))

    # Heading with helper link
    heading_frame = tk.Frame(filters_container)
    self.theme.apply_to_widget(heading_frame, "card")
    heading_frame.pack(fill=tk.X)

    heading_label = self._create_accent_heading(
        heading_frame,
        "🔍 Custom Shodan Filters (optional)"
    )
    heading_label.pack(side=tk.LEFT)

    # Helper link (clickable, blue, hand cursor)
    help_link = tk.Label(
        heading_frame,
        text="Filter Reference",
        fg="#0066cc",
        cursor="hand2",
        font=self.theme.fonts["small"]
    )
    help_link.pack(side=tk.LEFT, padx=(10, 0))
    help_link.bind(
        "<Button-1>",
        lambda e: webbrowser.open("https://www.shodan.io/search/filters")
    )

    # Input frame
    input_frame = tk.Frame(filters_container)
    self.theme.apply_to_widget(input_frame, "card")
    input_frame.pack(fill=tk.X, pady=(5, 0))

    # Entry field
    self.custom_filters_entry = tk.Entry(
        input_frame,
        textvariable=self.custom_filters_var,
        width=50,
        font=self.theme.fonts["body"]
    )
    self.custom_filters_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    # Description
    desc_frame = tk.Frame(filters_container)
    self.theme.apply_to_widget(desc_frame, "card")
    desc_frame.pack(fill=tk.X, pady=(5, 0))

    desc_label = self.theme.create_styled_label(
        desc_frame,
        '(e.g., "port:445 os:Windows" or "city:\\"Los Angeles\\"" — appended to base query)',
        "small",
        fg=self.theme.colors["text_secondary"]
    )
    desc_label.pack(anchor="w")

def _create_recent_hours_option(self, parent_frame: tk.Frame) -> None:
    """Create recent hours filter option."""
    recent_container = tk.Frame(parent_frame)
    self.theme.apply_to_widget(recent_container, "card")
    recent_container.pack(fill=tk.X, padx=15, pady=(0, 10))

    # Label
    recent_heading = self._create_accent_heading(
        recent_container,
        "⏱️ Recent Hours Filter"
    )
    recent_heading.pack(fill=tk.X)

    # Input frame
    input_frame = tk.Frame(recent_container)
    self.theme.apply_to_widget(input_frame, "card")
    input_frame.pack(fill=tk.X, pady=(5, 0))

    # Entry field
    self.recent_hours_entry = tk.Entry(
        input_frame,
        textvariable=self.recent_hours_var,
        width=8,
        font=self.theme.fonts["body"]
    )
    self.recent_hours_entry.pack(side=tk.LEFT)

    # Description
    desc_label = self.theme.create_styled_label(
        input_frame,
        "  (hours; leave blank for config default)",
        "small",
        fg=self.theme.colors["text_secondary"]
    )
    desc_label.configure(font=(self.theme.fonts["small"][0], self.theme.fonts["small"][1], "italic"))
    desc_label.pack(side=tk.LEFT)

def _create_rescan_options(self, parent_frame: tk.Frame) -> None:
    """Create rescan checkboxes."""
    rescan_container = tk.Frame(parent_frame)
    self.theme.apply_to_widget(rescan_container, "card")
    rescan_container.pack(fill=tk.X, padx=15, pady=(0, 10))

    # Label
    rescan_heading = self._create_accent_heading(
        rescan_container,
        "🔁 Rescan Options"
    )
    rescan_heading.pack(fill=tk.X)

    # Checkboxes frame
    checkboxes_frame = tk.Frame(rescan_container)
    self.theme.apply_to_widget(checkboxes_frame, "card")
    checkboxes_frame.pack(fill=tk.X, pady=(5, 0))

    # Rescan all checkbox
    self.rescan_all_checkbox = tk.Checkbutton(
        checkboxes_frame,
        text="Rescan all existing hosts",
        variable=self.rescan_all_var,
        font=self.theme.fonts["small"]
    )
    self.theme.apply_to_widget(self.rescan_all_checkbox, "checkbox")
    self.rescan_all_checkbox.pack(anchor="w", padx=5)

    # Rescan failed checkbox
    self.rescan_failed_checkbox = tk.Checkbutton(
        checkboxes_frame,
        text="Rescan previously failed hosts",
        variable=self.rescan_failed_var,
        font=self.theme.fonts["small"]
    )
    self.theme.apply_to_widget(self.rescan_failed_checkbox, "checkbox")
    self.rescan_failed_checkbox.pack(anchor="w", padx=5)

def _create_verbose_option(self, parent_frame: tk.Frame) -> None:
    """Create verbose mode toggle at top of right column."""
    container = tk.Frame(parent_frame)
    self.theme.apply_to_widget(container, "card")
    container.pack(fill=tk.X, padx=15, pady=(0, 10))

    heading = self._create_accent_heading(container, "📣 Verbose Mode")
    heading.pack(fill=tk.X)

    checkbox = tk.Checkbutton(
        container,
        text="Send backend verbose output",
        variable=self.verbose_var,
        font=self.theme.fonts["small"]
    )
    self.theme.apply_to_widget(checkbox, "checkbox")
    checkbox.pack(anchor="w", padx=12, pady=(6, 4))

def _create_security_mode_option(self, parent_frame: tk.Frame) -> None:
    """Create security mode toggle."""
    container = tk.Frame(parent_frame)
    self.theme.apply_to_widget(container, "card")
    container.pack(fill=tk.X, padx=15, pady=(0, 10))

    heading = self._create_accent_heading(container, "🛡 Security Mode")
    heading.pack(fill=tk.X)

    options_frame = tk.Frame(container)
    self.theme.apply_to_widget(options_frame, "card")
    options_frame.pack(fill=tk.X, pady=(5, 5))

    cautious_radio = tk.Radiobutton(
        options_frame,
        text="Cautious – signed SMB2+/SMB3 only",
        variable=self.security_mode_var,
        value="cautious",
        font=self.theme.fonts["small"]
    )
    self.theme.apply_to_widget(cautious_radio, "checkbox")
    cautious_radio.pack(anchor="w", padx=10, pady=2)

    legacy_radio = tk.Radiobutton(
        options_frame,
        text="Legacy – allow SMB1/unsigned connections",
        variable=self.security_mode_var,
        value="legacy",
        font=self.theme.fonts["small"]
    )
    self.theme.apply_to_widget(legacy_radio, "checkbox")
    legacy_radio.pack(anchor="w", padx=10, pady=2)

    warning_label = self.theme.create_styled_label(
        container,
        "Legacy mode bypasses built-in safeguards; enable only for trusted targets.",
        "small",
        fg=self.theme.colors.get("text_warning", self.theme.colors.get("warning", "#d97706"))
    )
    warning_label.pack(anchor="w", padx=15, pady=(0, 5))

def _create_rce_analysis_option(self, parent_frame: tk.Frame) -> None:
    """Create RCE vulnerability analysis toggle."""
    container = tk.Frame(parent_frame)
    self.theme.apply_to_widget(container, "card")
    container.pack(fill=tk.X, padx=15, pady=(0, 10))

    heading = self._create_accent_heading(container, "🔍 RCE Vulnerability Analysis")
    heading.pack(fill=tk.X)

    options_frame = tk.Frame(container)
    self.theme.apply_to_widget(options_frame, "card")
    options_frame.pack(fill=tk.X, pady=(5, 5))

    rce_checkbox = tk.Checkbutton(
        options_frame,
        text="Check for RCE vulnerabilities during share access testing",
        variable=self.rce_enabled_var,
        font=self.theme.fonts["small"]
    )
    self.theme.apply_to_widget(rce_checkbox, "checkbox")
    rce_checkbox.pack(anchor="w", padx=10, pady=2)

    info_label = self.theme.create_styled_label(
        container,
        "Experimental feature: analyzes SMB configurations for known RCE vulnerabilities.",
        "small",
        fg=self.theme.colors["text_secondary"]
    )
    info_label.pack(anchor="w", padx=15, pady=(0, 5))

    confidence_label = self.theme.create_styled_label(
        container,
        "Note: All results marked as \"low confidence\" during this initial phase.",
        "small",
        fg=self.theme.colors.get("text_warning", self.theme.colors.get("warning", "#d97706"))
    )
    confidence_label.pack(anchor="w", padx=15, pady=(0, 5))

def _create_hidden_rce_spacer_option(self, parent_frame: tk.Frame) -> None:
    """Reserve visual spacing when RCE controls are hidden."""
    container = tk.Frame(parent_frame, height=120)
    self.theme.apply_to_widget(container, "card")
    container.pack(fill=tk.X, padx=15, pady=(0, 10))
    container.pack_propagate(False)

def _create_bulk_probe_option(self, parent_frame: tk.Frame) -> None:
    """Create bulk probe automation checkbox."""
    container = tk.Frame(parent_frame)
    self.theme.apply_to_widget(container, "card")
    container.pack(fill=tk.X, padx=15, pady=(0, 10))

    heading = self._create_accent_heading(container, "🔍 Bulk Probe")
    heading.pack(fill=tk.X)

    options_frame = tk.Frame(container)
    self.theme.apply_to_widget(options_frame, "card")
    options_frame.pack(fill=tk.X, pady=(5, 5))

    bulk_probe_checkbox = tk.Checkbutton(
        options_frame,
        text="Run bulk probe after scan",
        variable=self.bulk_probe_enabled_var,
        font=self.theme.fonts["small"]
    )
    self.theme.apply_to_widget(bulk_probe_checkbox, "checkbox")
    bulk_probe_checkbox.pack(anchor="w", padx=10, pady=2)

    info_label = self.theme.create_styled_label(
        container,
        "Automatically probe all servers with successful authentication.",
        "small",
        fg=self.theme.colors["text_secondary"]
    )
    info_label.pack(anchor="w", padx=15, pady=(0, 5))

def _create_bulk_extract_option(self, parent_frame: tk.Frame) -> None:
    """Create bulk extract automation checkbox."""
    container = tk.Frame(parent_frame)
    self.theme.apply_to_widget(container, "card")
    container.pack(fill=tk.X, padx=15, pady=(0, 10))

    heading = self._create_accent_heading(container, "📦 Bulk Extract")
    heading.pack(fill=tk.X)

    options_frame = tk.Frame(container)
    self.theme.apply_to_widget(options_frame, "card")
    options_frame.pack(fill=tk.X, pady=(5, 5))

    bulk_extract_checkbox = tk.Checkbutton(
        options_frame,
        text="Run bulk extract after scan",
        variable=self.bulk_extract_enabled_var,
        command=self._sync_skip_indicator_extract_state,
        font=self.theme.fonts["small"]
    )
    self.theme.apply_to_widget(bulk_extract_checkbox, "checkbox")
    bulk_extract_checkbox.pack(anchor="w", padx=10, pady=2)

    # Skip malware hosts toggle
    self.skip_indicator_extract_var = tk.BooleanVar(value=True)
    skip_checkbox = tk.Checkbutton(
        options_frame,
        text="Skip extract on hosts with malware indicators (recommended)",
        variable=self.skip_indicator_extract_var,
        font=self.theme.fonts["small"]
    )
    self.theme.apply_to_widget(skip_checkbox, "checkbox")
    skip_checkbox.pack(anchor="w", padx=10, pady=2)
    self.skip_indicator_extract_checkbox = skip_checkbox
    self._sync_skip_indicator_extract_state()

    info_label = self.theme.create_styled_label(
        container,
        "Automatically extract files from servers with successful authentication.",
        "small",
        fg=self.theme.colors["text_secondary"]
    )
    info_label.pack(anchor="w", padx=15, pady=(0, 5))

    # Load extension filters and display counts
    filters = self._load_extension_filters()
    allowed_count = len(filters["included_extensions"])
    denied_count = len(filters["excluded_extensions"])

    # Build count display text
    if allowed_count == 0:
        allowed_text = "None configured"
    else:
        allowed_text = f"{allowed_count} allowed"

    if denied_count == 0:
        denied_text = "No restrictions"
    else:
        denied_text = f"{denied_count} denied"

    # Extension count label
    self.extension_count_label = self.theme.create_styled_label(
        container,
        f"Extensions: {allowed_text}, {denied_text}",
        "small",
        fg=self.theme.colors["text_secondary"]
    )
    self.extension_count_label.pack(anchor="w", padx=15, pady=(5, 0))

    # Button frame for side-by-side buttons
    button_frame = tk.Frame(container)
    self.theme.apply_to_widget(button_frame, "card")
    button_frame.pack(anchor="w", padx=15, pady=(5, 5))

    # View Filters button
    view_button = tk.Button(
        button_frame,
        text="View Filters",
        command=self._show_extension_table,
        font=self.theme.fonts["small"]
    )
    self.theme.apply_to_widget(view_button, "button_secondary")
    view_button.pack(side=tk.LEFT, padx=(0, 5))

def _create_concurrency_options(self, parent_frame: tk.Frame) -> None:
    """Create backend concurrency controls."""
    concurrency_container = tk.Frame(parent_frame)
    self.theme.apply_to_widget(concurrency_container, "card")
    concurrency_container.pack(fill=tk.X, padx=15, pady=(0, 10))

    concurrency_heading = self._create_accent_heading(
        concurrency_container,
        "🧵 Backend Concurrency"
    )
    concurrency_heading.pack(fill=tk.X)

    validate_cmd = self.dialog.register(self._validate_integer_input)

    discovery_row = tk.Frame(concurrency_container)
    self.theme.apply_to_widget(discovery_row, "card")
    discovery_row.pack(fill=tk.X, pady=(5, 0))

    discovery_label = self.theme.create_styled_label(
        discovery_row,
        "Discovery workers:",
        "small"
    )
    discovery_label.pack(side=tk.LEFT)

    discovery_entry = tk.Entry(
        discovery_row,
        textvariable=self.discovery_concurrency_var,
        width=6,
        validate='key',
        validatecommand=(validate_cmd, '%P')
    )
    self.theme.apply_to_widget(discovery_entry, "entry")
    discovery_entry.pack(side=tk.LEFT, padx=(8, 0))

    discovery_hint = self.theme.create_styled_label(
        discovery_row,
        "Hosts authenticated in parallel",
        "small",
        fg=self.theme.colors["text_secondary"]
    )
    discovery_hint.configure(font=(self.theme.fonts["small"][0], self.theme.fonts["small"][1], "italic"))
    discovery_hint.pack(side=tk.LEFT, padx=(8, 0))

    access_row = tk.Frame(concurrency_container)
    self.theme.apply_to_widget(access_row, "card")
    access_row.pack(fill=tk.X, pady=(5, 0))

    access_label = self.theme.create_styled_label(
        access_row,
        "Access workers:",
        "small"
    )
    access_label.pack(side=tk.LEFT)

    access_entry = tk.Entry(
        access_row,
        textvariable=self.access_concurrency_var,
        width=6,
        validate='key',
        validatecommand=(validate_cmd, '%P')
    )
    self.theme.apply_to_widget(access_entry, "entry")
    access_entry.pack(side=tk.LEFT, padx=(23, 0))

    access_hint = self.theme.create_styled_label(
        access_row,
        "Hosts tested in parallel during share access",
        "small",
        fg=self.theme.colors["text_secondary"]
    )
    access_hint.configure(font=(self.theme.fonts["small"][0], self.theme.fonts["small"][1], "italic"))
    access_hint.pack(side=tk.LEFT, padx=(8, 0))

    helper_label = self.theme.create_styled_label(
        concurrency_container,
        f"Allowed range: 1 - {self._concurrency_upper_limit} workers",
        "small",
        fg=self.theme.colors["text_secondary"]
    )
    helper_label.pack(anchor="w", pady=(6, 0))

    note_label = self.theme.create_styled_label(
        concurrency_container,
        "Raising concurrency increases network load. Update the delays below to stay within limits.",
        "small",
        fg=self.theme.colors["warning"]
    )
    note_label.pack(anchor="w", pady=(2, 0))

def _create_rate_limit_options(self, parent_frame: tk.Frame) -> None:
    """Create rate limit delay controls."""
    delay_container = tk.Frame(parent_frame)
    self.theme.apply_to_widget(delay_container, "card")
    delay_container.pack(fill=tk.X, padx=15, pady=(0, 10))

    delay_heading = self._create_accent_heading(
        delay_container,
        "🐢 Rate Limit Delays (seconds)"
    )
    delay_heading.pack(fill=tk.X)

    validate_cmd = self.dialog.register(self._validate_integer_input)

    rate_row = tk.Frame(delay_container)
    self.theme.apply_to_widget(rate_row, "card")
    rate_row.pack(fill=tk.X, pady=(5, 0))

    rate_label = self.theme.create_styled_label(
        rate_row,
        "Authentication delay:",
        "small"
    )
    rate_label.pack(side=tk.LEFT)

    rate_entry = tk.Entry(
        rate_row,
        textvariable=self.rate_limit_delay_var,
        width=6,
        validate='key',
        validatecommand=(validate_cmd, '%P')
    )
    self.theme.apply_to_widget(rate_entry, "entry")
    rate_entry.pack(side=tk.LEFT, padx=(10, 0))

    rate_hint = self.theme.create_styled_label(
        rate_row,
        "Delay between discovery auth attempts",
        "small",
        fg=self.theme.colors["text_secondary"]
    )
    rate_hint.configure(font=(self.theme.fonts["small"][0], self.theme.fonts["small"][1], "italic"))
    rate_hint.pack(side=tk.LEFT, padx=(8, 0))

    share_row = tk.Frame(delay_container)
    self.theme.apply_to_widget(share_row, "card")
    share_row.pack(fill=tk.X, pady=(5, 0))

    share_label = self.theme.create_styled_label(
        share_row,
        "Share access delay:",
        "small"
    )
    share_label.pack(side=tk.LEFT)

    share_entry = tk.Entry(
        share_row,
        textvariable=self.share_access_delay_var,
        width=6,
        validate='key',
        validatecommand=(validate_cmd, '%P')
    )
    self.theme.apply_to_widget(share_entry, "entry")
    share_entry.pack(side=tk.LEFT, padx=(18, 0))

    share_hint = self.theme.create_styled_label(
        share_row,
        "Delay between share enumerations per host",
        "small",
        fg=self.theme.colors["text_secondary"]
    )
    share_hint.configure(font=(self.theme.fonts["small"][0], self.theme.fonts["small"][1], "italic"))
    share_hint.pack(side=tk.LEFT, padx=(8, 0))

    helper_label = self.theme.create_styled_label(
        delay_container,
        f"Allowed range: 0 - {self._delay_upper_limit} seconds",
        "small",
        fg=self.theme.colors["text_secondary"]
    )
    helper_label.pack(anchor="w", pady=(6, 0))

    note_label = self.theme.create_styled_label(
        delay_container,
        "Increase these delays when scaling concurrency to avoid overwhelming targets.",
        "small",
        fg=self.theme.colors["warning"]
    )
    note_label.pack(anchor="w", pady=(2, 0))

def _create_api_key_option(self, parent_frame: tk.Frame) -> None:
    """Create API key override option."""
    api_container = tk.Frame(parent_frame)
    self.theme.apply_to_widget(api_container, "card")
    api_container.pack(fill=tk.X, padx=15, pady=(0, 10))

    # Label
    api_heading = self._create_accent_heading(
        api_container,
        "🔑 API Key Override"
    )
    api_heading.pack(fill=tk.X)

    # Input frame
    input_frame = tk.Frame(api_container)
    self.theme.apply_to_widget(input_frame, "card")
    input_frame.pack(fill=tk.X, pady=(5, 0))

    # Entry field
    self.api_key_entry = tk.Entry(
        input_frame,
        textvariable=self.api_key_var,
        width=40,
        font=self.theme.fonts["body"]
    )
    self.api_key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    # Description
    desc_label = self.theme.create_styled_label(
        input_frame,
        "  (temporary override)",
        "small",
        fg=self.theme.colors["text_secondary"]
    )
    desc_label.configure(font=(self.theme.fonts["small"][0], self.theme.fonts["small"][1], "italic"))
    desc_label.pack(side=tk.LEFT, padx=(5, 0))

def _create_config_section(self) -> None:
    """Create configuration file section."""
    config_frame = tk.Frame(self.content_frame)
    self.theme.apply_to_widget(config_frame, "card")
    config_frame.pack(fill=tk.X, padx=20, pady=(0, 5))
    
    # Section title
    config_title = self.theme.create_styled_label(
        config_frame,
        "Configuration",
        "heading"
    )
    config_title.pack(anchor="w", padx=15, pady=(10, 5))
    
    # Config file info
    config_info_frame = tk.Frame(config_frame)
    self.theme.apply_to_widget(config_info_frame, "card")
    config_info_frame.pack(fill=tk.X, padx=15, pady=(0, 5))
    
    info_text = f"Using configuration from:\n{self.config_path}"
    config_path_label = self.theme.create_styled_label(
        config_info_frame,
        info_text,
        "small",
        fg=self.theme.colors["text_secondary"],
        justify="left"
    )
    config_path_label.pack(anchor="w")
    
    # Config editor button
    config_button_frame = tk.Frame(config_frame)
    self.theme.apply_to_widget(config_button_frame, "card")
    config_button_frame.pack(fill=tk.X, padx=15, pady=(0, 10))
    
    edit_config_button = tk.Button(
        config_button_frame,
        text="⚙ Edit Configuration",
        command=self._open_config_editor
    )
    self.theme.apply_to_widget(edit_config_button, "button_secondary")
    edit_config_button.pack(side=tk.LEFT)

def _create_button_panel(self) -> None:
    """Create dialog button panel."""
    button_frame = tk.Frame(self.dialog)
    self.theme.apply_to_widget(button_frame, "main_window")
    button_frame.pack(fill=tk.X, padx=20, pady=(5, 15))

    # Button group aligned to the right
    buttons_container = tk.Frame(button_frame)
    self.theme.apply_to_widget(buttons_container, "main_window")
    buttons_container.pack(side=tk.RIGHT)

    budget_button = tk.Button(
        buttons_container,
        text="Query Budget...",
        command=self._open_query_budget_dialog
    )
    self.theme.apply_to_widget(budget_button, "button_secondary")
    budget_button.pack(side=tk.LEFT, padx=(0, 10))
    
    cancel_button = tk.Button(
        buttons_container,
        text="Cancel",
        command=self._cancel_scan
    )
    self.theme.apply_to_widget(cancel_button, "button_secondary")
    cancel_button.pack(side=tk.LEFT, padx=(0, 10))
    
    # Start scan button (right)
    start_button = tk.Button(
        buttons_container,
        text="🚀 Start Scan",
        command=self._start_scan
    )
    self.theme.apply_to_widget(start_button, "button_primary")
    start_button.pack(side=tk.LEFT)



def bind_scan_dialog_layout_methods(dialog_cls) -> None:
    """Attach extracted layout/template methods to ScanDialog."""
    method_names = (
        "_create_template_toolbar",
        "_create_accent_heading",
        "_refresh_template_toolbar",
        "_handle_template_selected",
        "_get_selected_template_name",
        "_prompt_save_template",
        "_delete_selected_template",
        "_capture_form_state",
        "_apply_form_state",
        "_sync_skip_indicator_extract_state",
        "_apply_template_by_slug",
        "_create_region_selection",
        "_update_region_status",
        "_select_all_regions",
        "_clear_all_regions",
        "_create_custom_filters_option",
        "_create_recent_hours_option",
        "_create_rescan_options",
        "_create_verbose_option",
        "_create_security_mode_option",
        "_create_rce_analysis_option",
        "_create_hidden_rce_spacer_option",
        "_create_bulk_probe_option",
        "_create_bulk_extract_option",
        "_create_concurrency_options",
        "_create_rate_limit_options",
        "_create_api_key_option",
        "_create_config_section",
        "_create_button_panel",
    )
    for name in method_names:
        setattr(dialog_cls, name, globals()[name])
