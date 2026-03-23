"""
Scan Dialog UI Builders Mixin

Method-local widget construction helpers extracted from scan_dialog.py (Slice 7D).
Each method builds one labelled section of the scan options form and packs it into
the supplied parent frame.  No behaviour changes from the original — bodies are
verbatim copies.
"""

from __future__ import annotations

import tkinter as tk
import webbrowser

from gui.components.unified_scan_validators import validate_integer_char


class _ScanDialogUIBuildersMixin:
    """Mixin carrying UI-builder helpers for ScanDialog.  No __init__."""

    # ------------------------------------------------------------------
    # Shared primitive
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Option-section builders
    # ------------------------------------------------------------------

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

    def _create_max_results_option(self, parent_frame: tk.Frame) -> None:
        """Create max Shodan results option."""
        max_results_container = tk.Frame(parent_frame)
        self.theme.apply_to_widget(max_results_container, "card")
        max_results_container.pack(fill=tk.X, padx=15, pady=(0, 10))

        # Label
        max_results_heading = self._create_accent_heading(
            max_results_container,
            "🔢 Max Shodan Results"
        )
        max_results_heading.pack(fill=tk.X)

        # Input frame
        input_frame = tk.Frame(max_results_container)
        self.theme.apply_to_widget(input_frame, "card")
        input_frame.pack(fill=tk.X, pady=(5, 0))

        # Entry field
        self.max_results_entry = tk.Entry(
            input_frame,
            textvariable=self.max_results_var,
            width=8,
            font=self.theme.fonts["body"]
        )
        self.max_results_entry.pack(side=tk.LEFT)

        # Description
        desc_label = self.theme.create_styled_label(
            input_frame,
            "  (1–1000, default: 1000)",
            "small",
            fg=self.theme.colors["text_secondary"]
        )
        desc_label.configure(font=(self.theme.fonts["small"][0], self.theme.fonts["small"][1], "italic"))
        desc_label.pack(side=tk.LEFT)

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

        validate_cmd = self.dialog.register(validate_integer_char)

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

        validate_cmd = self.dialog.register(validate_integer_char)

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
