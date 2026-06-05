"""Compact visual layout for :mod:`gui.components.unified_scan_dialog`."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from gui.components.dashboard_provider_queue import PROVIDER_SPECS, rank_providers
from gui.components.scan_dialog import ScanDialog
from gui.components.scan_provider_options import (
    build_reddit_sub_panel,
    build_searxng_sub_panel,
)
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.keybindings import bind_close_shortcuts, bind_submit_shortcuts


DEFAULT_GEOMETRY = "960x700"
MIN_WIDTH = 840
MIN_HEIGHT = 680


def build_dialog(owner: Any) -> None:
    """Build the Start Scan window without changing its behavioral contract."""
    owner.dialog = tk.Toplevel(owner.parent)
    owner.dialog.title("Start Scan")
    owner.dialog.geometry(DEFAULT_GEOMETRY)
    owner.dialog.minsize(MIN_WIDTH, MIN_HEIGHT)
    owner.dialog.resizable(True, True)
    owner.theme.apply_to_widget(owner.dialog, "main_window")
    owner.dialog.transient(owner.parent)
    _center_dialog(owner)

    body_shell = tk.Frame(owner.dialog)
    owner.theme.apply_to_widget(body_shell, "main_window")
    body_shell.pack(fill=tk.BOTH, expand=True)
    body_shell.grid_rowconfigure(0, weight=1)
    body_shell.grid_columnconfigure(0, weight=1)

    owner._canvas = tk.Canvas(
        body_shell,
        highlightthickness=0,
        borderwidth=0,
        bg=owner.theme.colors["primary_bg"],
    )
    owner._canvas.grid(row=0, column=0, sticky="nsew")

    owner._scrollbar = ttk.Scrollbar(
        body_shell,
        orient=tk.VERTICAL,
        command=owner._canvas.yview,
    )
    owner._scrollbar.grid(row=0, column=1, sticky="ns")
    owner._scrollbar.grid_remove()
    owner._scrollbar_visible = False
    owner._canvas.configure(yscrollcommand=owner._scrollbar.set)

    owner._content = tk.Frame(owner._canvas)
    owner.theme.apply_to_widget(owner._content, "main_window")
    owner._content_window = owner._canvas.create_window(
        (0, 0),
        window=owner._content,
        anchor="nw",
    )

    owner._content.bind("<Configure>", lambda _event: _sync_scroll_region(owner))
    owner._canvas.bind("<Configure>", lambda event: _sync_canvas_width(owner, event))
    for widget in (owner._canvas, owner._content):
        widget.bind("<MouseWheel>", lambda event: _on_mousewheel(owner, event))
        widget.bind("<Button-4>", lambda event: _on_mousewheel(owner, event))
        widget.bind("<Button-5>", lambda event: _on_mousewheel(owner, event))

    _build_template_row(owner, owner._content)
    _build_provider_section(owner, owner._content)
    _build_targeting_runtime(owner, owner._content)
    _build_footer(owner)

    owner.dialog.protocol("WM_DELETE_WINDOW", owner._cancel)
    bind_submit_shortcuts(owner.dialog, owner._start)
    bind_close_shortcuts(owner.dialog, owner._cancel)
    owner.country_var.trace_add("write", owner._validate_country_input)

    owner._refresh_template_toolbar()
    owner._update_region_status()
    owner._sync_shodan_options_state()
    owner._sync_searxng_options_state()
    owner._sync_reddit_options_state()
    owner._sync_targeting_mode_state()
    owner._refresh_provider_queue_label()
    owner.theme.apply_theme_to_application(owner.dialog)
    if owner.country_entry:
        owner.country_entry.focus_set()
    ensure_dialog_focus(owner.dialog, owner.parent)
    owner.dialog.after_idle(lambda: _update_scrollbar(owner))


def refresh_provider_queue_label(owner: Any) -> str:
    """Refresh and return the provider execution-order label."""
    selected = owner._resolve_selected_providers()
    ranked = rank_providers(selected)
    labels = [PROVIDER_SPECS[key].label for key in ranked]
    text = "Queue: " + (" -> ".join(labels) if labels else "none selected")
    label = getattr(owner, "_provider_queue_label", None)
    if label is not None:
        label.configure(text=text)
    return text


def _center_dialog(owner: Any) -> None:
    owner.dialog.update_idletasks()
    width, height = (int(value) for value in DEFAULT_GEOMETRY.split("x"))
    try:
        x = owner.parent.winfo_rootx() + (owner.parent.winfo_width() - width) // 2
        y = owner.parent.winfo_rooty() + (owner.parent.winfo_height() - height) // 2
        x = max(0, min(x, owner.dialog.winfo_screenwidth() - width))
        y = max(0, min(y, owner.dialog.winfo_screenheight() - height))
        owner.dialog.geometry(f"{width}x{height}+{x}+{y}")
    except tk.TclError:
        owner.dialog.geometry(DEFAULT_GEOMETRY)


def _sync_scroll_region(owner: Any) -> None:
    owner._canvas.configure(scrollregion=owner._canvas.bbox("all"))
    owner.dialog.after_idle(lambda: _update_scrollbar(owner))


def _sync_canvas_width(owner: Any, event: tk.Event) -> None:
    owner._canvas.itemconfigure(owner._content_window, width=max(1, event.width))
    _update_scrollbar(owner)


def _update_scrollbar(owner: Any) -> None:
    try:
        overflow = owner._content.winfo_reqheight() > owner._canvas.winfo_height() + 2
        if overflow and not owner._scrollbar_visible:
            owner._scrollbar.grid()
            owner._scrollbar_visible = True
        elif not overflow and owner._scrollbar_visible:
            owner._scrollbar.grid_remove()
            owner._scrollbar_visible = False
            owner._canvas.yview_moveto(0)
    except tk.TclError:
        return


def _on_mousewheel(owner: Any, event: tk.Event) -> str | None:
    if not getattr(owner, "_scrollbar_visible", False):
        return None
    if getattr(event, "delta", 0):
        delta = -1 if event.delta > 0 else 1
    elif getattr(event, "num", None) == 4:
        delta = -1
    elif getattr(event, "num", None) == 5:
        delta = 1
    else:
        return None
    owner._canvas.yview_scroll(delta, "units")
    return "break"


def _bold_label(owner: Any, parent: tk.Widget, text: str) -> tk.Label:
    family, size, _weight = owner.theme.fonts["body"]
    label = tk.Label(
        parent,
        text=text,
        font=(family, size, "bold"),
    )
    owner.theme.apply_to_widget(label, "label")
    return label


def _muted_label(owner: Any, parent: tk.Widget, text: str) -> tk.Label:
    return owner.theme.create_styled_label(
        parent,
        text,
        "small",
        anchor="w",
        fg=owner.theme.colors["text_secondary"],
    )


def _section_heading(
    owner: Any,
    parent: tk.Widget,
    text: str,
    *,
    right_text: str | None = None,
) -> tk.Label | None:
    row = tk.Frame(parent)
    owner.theme.apply_to_widget(row, "main_window")
    row.pack(fill=tk.X)
    _bold_label(owner, row, text).pack(side=tk.LEFT)
    right_label = None
    if right_text is not None:
        right_label = _muted_label(owner, row, right_text)
        right_label.pack(side=tk.RIGHT)
    owner.theme.create_separator(parent).pack(fill=tk.X, pady=(4, 6))
    return right_label


def _build_template_row(owner: Any, parent: tk.Widget) -> None:
    row = tk.Frame(parent)
    owner.theme.apply_to_widget(row, "main_window")
    row.pack(fill=tk.X, padx=18, pady=(8, 4))
    row.grid_columnconfigure(1, weight=1)

    owner.theme.create_styled_label(row, "Template", "body").grid(
        row=0,
        column=0,
        sticky="w",
        padx=(0, 8),
    )
    owner.template_dropdown = ttk.Combobox(
        row,
        textvariable=owner.template_var,
        state="readonly",
    )
    owner.template_dropdown.grid(row=0, column=1, sticky="ew")
    owner.template_dropdown.bind(
        "<<ComboboxSelected>>",
        owner._handle_template_selected,
    )

    save_button = tk.Button(
        row,
        text="Save",
        command=owner._prompt_save_template,
        font=owner.theme.fonts["small"],
    )
    owner.theme.apply_to_widget(save_button, "button_secondary")
    save_button.grid(row=0, column=2, padx=(8, 4))

    owner.delete_template_button = tk.Button(
        row,
        text="Delete",
        command=owner._delete_selected_template,
        font=owner.theme.fonts["small"],
    )
    owner.theme.apply_to_widget(owner.delete_template_button, "button_secondary")
    owner.delete_template_button.grid(row=0, column=3)


def _build_provider_section(owner: Any, parent: tk.Widget) -> None:
    section = tk.Frame(parent)
    owner.theme.apply_to_widget(section, "main_window")
    section.pack(fill=tk.X, padx=18, pady=(2, 8))
    owner._providers_section = section

    owner._provider_queue_label = _section_heading(
        owner,
        section,
        "Providers",
        right_text="",
    )

    grid = tk.Frame(section)
    owner.theme.apply_to_widget(grid, "main_window")
    grid.pack(fill=tk.X)
    grid.grid_columnconfigure(1, weight=1)

    shodan_cb = ttk.Checkbutton(
        grid,
        text="Shodan",
        variable=owner.provider_shodan_var,
        command=owner._sync_shodan_options_state,
    )
    shodan_cb.grid(row=0, column=0, sticky="nw", padx=(0, 12), pady=2)
    owner._shodan_opts_frame = _build_shodan_options(owner, grid)
    owner._shodan_opts_frame.grid(row=0, column=1, sticky="ew", pady=2)

    searxng_cb = ttk.Checkbutton(
        grid,
        text="SearXNG",
        variable=owner.provider_searxng_var,
        command=owner._sync_searxng_options_state,
    )
    searxng_cb.grid(row=1, column=0, sticky="nw", padx=(0, 12), pady=(5, 2))
    owner._searxng_opts_frame = build_searxng_sub_panel(
        grid,
        {
            "instance_url": owner.searxng_instance_url_var,
            "query": owner.searxng_query_var,
            "max_results": owner.searxng_max_results_var,
        },
        owner.theme,
    )
    owner._searxng_opts_frame.grid(row=1, column=1, sticky="ew", pady=(5, 2))
    owner._searxng_helper_label = getattr(
        owner._searxng_opts_frame,
        "_helper_label",
        None,
    )
    owner._searxng_instance_entry = getattr(
        owner._searxng_opts_frame,
        "_searxng_instance_entry",
        None,
    )
    owner._searxng_query_entry = getattr(
        owner._searxng_opts_frame,
        "_searxng_query_entry",
        None,
    )
    owner._searxng_results_entry = getattr(
        owner._searxng_opts_frame,
        "_searxng_results_entry",
        None,
    )

    reddit_cb = ttk.Checkbutton(
        grid,
        text="Reddit",
        variable=owner.provider_reddit_var,
        command=owner._sync_reddit_options_state,
    )
    reddit_cb.grid(row=2, column=0, sticky="nw", padx=(0, 12), pady=(5, 2))
    owner._reddit_opts_frame = build_reddit_sub_panel(
        grid,
        {
            "mode": owner.reddit_mode_var,
            "sort": owner.reddit_sort_var,
            "top_window": owner.reddit_top_window_var,
            "max_posts": owner.reddit_max_posts_var,
            "query": owner.reddit_query_var,
            "username": owner.reddit_username_var,
            "parse_body": owner.reddit_parse_body_var,
            "include_nsfw": owner.reddit_include_nsfw_var,
        },
        owner.theme,
        on_state_change=owner._sync_reddit_options_state,
    )
    owner._reddit_opts_frame.grid(row=2, column=1, sticky="ew", pady=(5, 2))
    owner._reddit_query_entry = getattr(
        owner._reddit_opts_frame,
        "_reddit_query_widgets",
        (None, None),
    )[1]
    owner._reddit_top_window_combo = getattr(
        owner._reddit_opts_frame,
        "_reddit_top_window_combo",
        None,
    )
    owner._reddit_helper_label = getattr(
        owner._reddit_opts_frame,
        "_helper_label",
        None,
    )


def _build_shodan_options(owner: Any, parent: tk.Widget) -> tk.Frame:
    frame = tk.Frame(parent)
    owner.theme.apply_to_widget(frame, "main_window")

    protocol_items = (
        ("SMB", owner.protocol_smb_var, owner.smb_max_results_var),
        ("FTP", owner.protocol_ftp_var, owner.ftp_max_results_var),
        ("HTTP", owner.protocol_http_var, owner.http_max_results_var),
    )
    for index, (label, selected_var, results_var) in enumerate(protocol_items):
        column = index * 2
        check = ttk.Checkbutton(
            frame,
            text=label,
            variable=selected_var,
            command=owner._refresh_protocol_estimate_lines,
        )
        check.grid(row=0, column=column, sticky="w", padx=(0, 4), pady=2)
        entry = ttk.Entry(frame, textvariable=results_var, width=8)
        entry.grid(row=0, column=column + 1, sticky="w", padx=(0, 12), pady=2)

    edit_button = tk.Button(
        frame,
        text="Edit Queries",
        command=owner._open_query_editor,
        font=owner.theme.fonts["small"],
    )
    owner.theme.apply_to_widget(edit_button, "button_secondary")
    edit_button.grid(row=0, column=6, sticky="e", pady=2)
    frame.grid_columnconfigure(6, weight=1)

    estimate_row = tk.Frame(frame)
    owner.theme.apply_to_widget(estimate_row, "main_window")
    estimate_row.grid(row=1, column=0, columnspan=7, sticky="ew", pady=(0, 2))
    owner._shodan_helper_row = estimate_row
    owner.protocol_cost_label = _muted_label(owner, estimate_row, "")
    owner.protocol_cost_label.pack(side=tk.LEFT)
    owner.protocol_results_label = _muted_label(owner, estimate_row, "")
    owner.protocol_results_label.pack(side=tk.LEFT, padx=(10, 0))
    help_link = tk.Label(
        estimate_row,
        text="How?",
        fg=owner.theme.colors["accent"],
        cursor="hand2",
        font=owner.theme.fonts["small"],
    )
    help_link.pack(side=tk.LEFT, padx=(10, 0))
    help_link.bind("<Button-1>", owner._on_cost_estimate_help_clicked)
    owner._refresh_protocol_estimate_lines()
    return frame


def _build_targeting_runtime(owner: Any, parent: tk.Widget) -> None:
    columns = tk.Frame(parent)
    owner.theme.apply_to_widget(columns, "main_window")
    columns.pack(fill=tk.X, padx=18, pady=(4, 8))
    owner._lower_columns = columns
    columns.grid_columnconfigure(0, weight=1, uniform="lower")
    columns.grid_columnconfigure(1, weight=1, uniform="lower")

    targeting = tk.Frame(columns)
    owner.theme.apply_to_widget(targeting, "main_window")
    targeting.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
    runtime = tk.Frame(columns)
    owner.theme.apply_to_widget(runtime, "main_window")
    runtime.grid(row=0, column=1, sticky="nsew", padx=(14, 0))

    _build_targeting(owner, targeting)
    _build_runtime(owner, runtime)


def _build_targeting(owner: Any, parent: tk.Widget) -> None:
    _section_heading(owner, parent, "Targeting")

    country_row = tk.Frame(parent)
    owner.theme.apply_to_widget(country_row, "main_window")
    country_row.pack(fill=tk.X, pady=(0, 5))
    owner.theme.create_styled_label(country_row, "Country", "small").pack(side=tk.LEFT)
    owner.country_entry = ttk.Entry(
        country_row,
        textvariable=owner.country_var,
        width=16,
    )
    owner.country_entry.pack(side=tk.LEFT, padx=(8, 6))
    _muted_label(owner, country_row, "US, GB, CA").pack(side=tk.LEFT)

    regions = tk.Frame(parent)
    owner.theme.apply_to_widget(regions, "main_window")
    regions.pack(fill=tk.X)
    regions.grid_columnconfigure(0, weight=1)
    regions.grid_columnconfigure(1, weight=1)
    owner._region_checkbuttons = []
    region_items = (
        ("Africa", owner.africa_var),
        ("Asia", owner.asia_var),
        ("Europe", owner.europe_var),
        ("North America", owner.north_america_var),
        ("Oceania", owner.oceania_var),
        ("South America", owner.south_america_var),
    )
    for index, (name, var) in enumerate(region_items):
        checkbox = ttk.Checkbutton(
            regions,
            text=f"{name} ({len(ScanDialog.REGIONS[name])})",
            variable=var,
            command=owner._on_region_selection_changed,
        )
        checkbox.grid(
            row=index // 2,
            column=index % 2,
            sticky="w",
            pady=1,
        )
        owner._region_checkbuttons.append(checkbox)

    actions = tk.Frame(parent)
    owner.theme.apply_to_widget(actions, "main_window")
    actions.pack(fill=tk.X, pady=(5, 0))
    owner._region_action_buttons = []
    for label, command in (
        ("Select all", owner._select_all_regions),
        ("Clear", owner._clear_all_regions),
    ):
        button = tk.Button(
            actions,
            text=label,
            command=command,
            font=owner.theme.fonts["small"],
        )
        owner.theme.apply_to_widget(button, "button_secondary")
        button.pack(side=tk.LEFT, padx=(0, 5))
        owner._region_action_buttons.append(button)
    owner.region_status_label = _muted_label(owner, actions, "")
    owner.region_status_label.pack(side=tk.LEFT, padx=(6, 0))


def _build_runtime(owner: Any, parent: tk.Widget) -> None:
    _section_heading(owner, parent, "Runtime & safety")
    validate_cmd = owner.dialog.register(owner._validate_integer_input)

    inputs = tk.Frame(parent)
    owner.theme.apply_to_widget(inputs, "main_window")
    inputs.pack(fill=tk.X, pady=(0, 3))
    owner.theme.create_styled_label(inputs, "Concurrency", "small").pack(side=tk.LEFT)
    concurrency = ttk.Entry(
        inputs,
        textvariable=owner.shared_concurrency_var,
        width=6,
        validate="key",
        validatecommand=(validate_cmd, "%P"),
    )
    concurrency.pack(side=tk.LEFT, padx=(6, 14))
    owner.theme.create_styled_label(inputs, "Timeout", "small").pack(side=tk.LEFT)
    timeout = ttk.Entry(
        inputs,
        textvariable=owner.shared_timeout_var,
        width=6,
        validate="key",
        validatecommand=(validate_cmd, "%P"),
    )
    timeout.pack(side=tk.LEFT, padx=(6, 4))
    _muted_label(owner, inputs, "sec").pack(side=tk.LEFT)

    for text, variable, command in (
        ("Verbose backend output", owner.verbose_var, None),
        ("Run bulk probe after each scan", owner.bulk_probe_enabled_var, None),
        (
            "Run bulk extract after each scan",
            owner.bulk_extract_enabled_var,
            owner._sync_skip_indicator_extract_state,
        ),
    ):
        checkbox = ttk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            command=command,
        )
        checkbox.pack(anchor="w", pady=1)

    owner.skip_indicator_extract_checkbox = ttk.Checkbutton(
        parent,
        text="Skip extract on hosts with malware indicators",
        variable=owner.skip_indicator_extract_var,
    )
    owner.skip_indicator_extract_checkbox.pack(anchor="w", padx=(18, 0), pady=1)
    owner._sync_skip_indicator_extract_state()

    security_row = tk.Frame(parent)
    owner.theme.apply_to_widget(security_row, "main_window")
    security_row.pack(fill=tk.X, pady=(4, 1))
    owner.theme.create_styled_label(security_row, "SMB mode", "small").pack(side=tk.LEFT)
    owner._security_mode_display_var = tk.StringVar()
    security_combo = ttk.Combobox(
        security_row,
        textvariable=owner._security_mode_display_var,
        values=(
            "Cautious - signed SMB2+/SMB3",
            "Legacy - SMB1/unsigned",
        ),
        state="readonly",
        width=28,
    )
    security_combo.pack(side=tk.LEFT, padx=(8, 0))
    _bind_security_mode(owner)

    tls = ttk.Checkbutton(
        parent,
        text="Allow insecure HTTPS certificates",
        variable=owner.allow_insecure_tls_var,
    )
    tls.pack(anchor="w", pady=(2, 0))


def _bind_security_mode(owner: Any) -> None:
    display_by_mode = {
        "cautious": "Cautious - signed SMB2+/SMB3",
        "legacy": "Legacy - SMB1/unsigned",
    }
    mode_by_display = {value: key for key, value in display_by_mode.items()}

    def sync_display(*_args) -> None:
        mode = str(owner.security_mode_var.get() or "cautious").strip().lower()
        desired = display_by_mode.get(mode, display_by_mode["cautious"])
        if owner._security_mode_display_var.get() != desired:
            owner._security_mode_display_var.set(desired)

    def sync_model(*_args) -> None:
        desired = mode_by_display.get(owner._security_mode_display_var.get())
        if desired and owner.security_mode_var.get() != desired:
            owner.security_mode_var.set(desired)

    owner.security_mode_var.trace_add("write", sync_display)
    owner._security_mode_display_var.trace_add("write", sync_model)
    sync_display()


def _build_config_row(owner: Any, parent: tk.Widget) -> None:
    row = tk.Frame(parent)
    owner.theme.apply_to_widget(row, "main_window")
    row.pack(fill=tk.X, padx=18, pady=3)
    owner._config_row = row

    config_label = _muted_label(owner, row, f"Config: {owner.config_path}")
    config_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
    edit_button = tk.Button(
        row,
        text="Edit Config",
        command=owner._open_config_editor,
        font=owner.theme.fonts["small"],
    )
    owner.theme.apply_to_widget(edit_button, "button_secondary")
    edit_button.pack(side=tk.RIGHT)


def _build_footer(owner: Any) -> None:
    footer = tk.Frame(owner.dialog)
    owner.theme.apply_to_widget(footer, "main_window")
    footer.pack(fill=tk.X, side=tk.BOTTOM)
    owner._footer_frame = footer
    owner.theme.create_separator(footer).pack(fill=tk.X)
    _build_config_row(owner, footer)
    owner.theme.create_separator(footer).pack(fill=tk.X)

    row = tk.Frame(footer)
    owner.theme.apply_to_widget(row, "main_window")
    row.pack(fill=tk.X, padx=18, pady=4)
    owner._footer_actions_row = row
    hint = _muted_label(
        owner,
        row,
        "Enter start scan  •  Esc cancel  •  Ctrl/Cmd+W close",
    )
    hint.pack(side=tk.LEFT)

    start_button = tk.Button(row, text="Start Scan", command=owner._start)
    owner.theme.apply_to_widget(start_button, "button_primary")
    start_button.pack(side=tk.RIGHT)
    cancel_button = tk.Button(row, text="Cancel", command=owner._cancel)
    owner.theme.apply_to_widget(cancel_button, "button_secondary")
    cancel_button.pack(side=tk.RIGHT, padx=(0, 8))
