"""
Server List Filter Operations

Handles filter UI creation and pure filtering logic.
Uses callback pattern for event wiring to prevent tight coupling.
"""

import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta
from typing import Dict, List, Any, Callable


def create_filter_panel(parent, theme, filter_vars, callbacks):
    """
    Create filtering controls panel.

    Args:
        parent: Parent widget for the filter panel
        theme: Theme object for styling
        filter_vars: Dict of tkinter variables for filter state
        callbacks: Dict of callback functions for filter events

    Returns:
        tuple: (filter_frame, widget_refs) for parent access
    """
    # Filter container (initially hidden for simple mode)
    filter_frame = tk.Frame(parent)
    theme.apply_to_widget(filter_frame, "card")

    # Search box (always visible)
    search_frame = tk.Frame(filter_frame)
    theme.apply_to_widget(search_frame, "card")
    search_frame.pack(fill=tk.X, padx=10, pady=5)

    search_label = theme.create_styled_label(
        search_frame,
        "🔍 Search:",
        "body"
    )
    search_label.pack(side=tk.LEFT, padx=(0, 5))

    search_entry = tk.Entry(
        search_frame,
        textvariable=filter_vars['search_text'],
        width=30
    )
    theme.apply_to_widget(search_entry, "entry")
    search_entry.pack(side=tk.LEFT, padx=(0, 10))
    search_entry.bind("<KeyRelease>", lambda e: callbacks['on_search_changed']())

    # Clear search button
    clear_button = tk.Button(
        search_frame,
        text="Clear",
        command=callbacks['on_clear_search']
    )
    theme.apply_to_widget(clear_button, "button_secondary")
    clear_button.pack(side=tk.LEFT, padx=(0, 10))

    # Favorites only filter checkbox
    favorites_checkbox = tk.Checkbutton(
        search_frame,
        text="Favorites only",
        variable=filter_vars['favorites_only'],
        command=callbacks['on_favorites_only_changed']
    )
    theme.apply_to_widget(favorites_checkbox, "checkbox")
    favorites_checkbox.pack(side=tk.LEFT, padx=(0, 10))

    exclude_avoid_checkbox = tk.Checkbutton(
        search_frame,
        text="Exclude avoid",
        variable=filter_vars['exclude_avoid'],
        command=callbacks['on_exclude_avoid_changed']
    )
    theme.apply_to_widget(exclude_avoid_checkbox, "checkbox")
    exclude_avoid_checkbox.pack(side=tk.LEFT, padx=(0, 10))

    probed_only_checkbox = tk.Checkbutton(
        search_frame,
        text="Probed only",
        variable=filter_vars['probed_only'],
        command=callbacks['on_probed_only_changed']
    )
    theme.apply_to_widget(probed_only_checkbox, "checkbox")
    probed_only_checkbox.pack(side=tk.LEFT, padx=(0, 10))

    exclude_compromised_checkbox = tk.Checkbutton(
        search_frame,
        text="Exclude compromised",
        variable=filter_vars['exclude_compromised'],
        command=callbacks['on_exclude_compromised_changed']
    )
    theme.apply_to_widget(exclude_compromised_checkbox, "checkbox")
    exclude_compromised_checkbox.pack(side=tk.LEFT, padx=(0, 10))

    # Advanced toggle button aligned to the right of the search row
    mode_button = tk.Button(
        search_frame,
        text="🔧 Advanced",
        command=callbacks['on_toggle_mode']
    )
    theme.apply_to_widget(mode_button, "button_secondary")
    mode_button.pack(side=tk.RIGHT)

    add_record_button = tk.Button(
        search_frame,
        text="Add Record",
        command=callbacks['on_add_record']
    )
    theme.apply_to_widget(add_record_button, "button_secondary")
    # Right-packed after mode_button so this appears immediately to its left.
    add_record_button.pack(side=tk.RIGHT, padx=(0, 8))

    # Show all results toggle (if callback provided)
    show_all_button = None
    if 'on_show_all_toggle' in callbacks:
        show_all_button = tk.Button(
            search_frame,
            text="📈 Show All Results",
            command=callbacks['on_show_all_toggle']
        )
        theme.apply_to_widget(show_all_button, "button_primary")
        show_all_button.pack(side=tk.LEFT)

    # Advanced filters (hidden initially)
    advanced_filters_frame = tk.Frame(filter_frame)
    theme.apply_to_widget(advanced_filters_frame, "card")

    # Three-column layout: left (templates + filters), middle (protocols), right (countries)
    left_column = tk.Frame(advanced_filters_frame)
    theme.apply_to_widget(left_column, "card")
    left_column.pack(side=tk.LEFT, padx=10, pady=5)

    # --- Template controls section (top of left column) ---
    # Button row: Save, Reset, Delete
    template_button_row = tk.Frame(left_column)
    template_button_row.pack(anchor="w", pady=(0, 2))

    save_button = tk.Button(
        template_button_row,
        text="Save",
        command=callbacks['on_save_filter_template']
    )
    theme.apply_to_widget(save_button, "button_secondary")
    save_button.pack(side=tk.LEFT, padx=(0, 2))

    reset_button = tk.Button(
        template_button_row,
        text="Reset",
        command=callbacks['on_reset_filters']
    )
    theme.apply_to_widget(reset_button, "button_secondary")
    reset_button.pack(side=tk.LEFT, padx=(0, 2))

    delete_button = tk.Button(
        template_button_row,
        text="Del",
        command=callbacks['on_delete_filter_template'],
        state=tk.DISABLED
    )
    theme.apply_to_widget(delete_button, "button_secondary")
    delete_button.pack(side=tk.LEFT)

    # Template dropdown
    template_dropdown = ttk.Combobox(
        left_column,
        width=20,
        state="readonly"
    )
    template_dropdown.pack(anchor="w", pady=(0, 5))
    template_dropdown.bind("<<ComboboxSelected>>", lambda e: callbacks['on_filter_template_selected']())

    # --- Date/Shares section (below templates) ---
    shares_filter_checkbox = tk.Checkbutton(
        left_column,
        text="Shares > 0",
        variable=filter_vars['shares_filter'],
        command=callbacks['on_shares_filter_changed']
    )
    theme.apply_to_widget(shares_filter_checkbox, "checkbox")
    shares_filter_checkbox.pack(anchor="w", pady=(0, 2))

    date_label = theme.create_styled_label(
        left_column,
        "Discovery Date:",
        "small"
    )
    date_label.pack(anchor="w")

    date_combo = ttk.Combobox(
        left_column,
        textvariable=filter_vars['date_filter'],
        values=["All", "Since Last Scan", "Last 24 Hours", "Last 7 Days", "Last 30 Days"],
        width=18,
        state="readonly"
    )
    date_combo.set("All")
    date_combo.pack(anchor="w")
    date_combo.bind("<<ComboboxSelected>>", lambda e: callbacks['on_date_filter_changed']())

    # --- Middle column: Protocol filter ---
    protocol_column = tk.Frame(advanced_filters_frame)
    theme.apply_to_widget(protocol_column, "card")
    protocol_column.pack(side=tk.LEFT, padx=10, pady=5, anchor="n")

    protocol_label = theme.create_styled_label(
        protocol_column,
        "Protocols:",
        "small"
    )
    protocol_label.pack(anchor="w")

    protocol_smb_checkbox = tk.Checkbutton(
        protocol_column,
        text="SMB",
        variable=filter_vars["protocol_smb"],
        command=callbacks["on_protocol_filter_changed"],
    )
    theme.apply_to_widget(protocol_smb_checkbox, "checkbox")
    protocol_smb_checkbox.pack(anchor="w", pady=(2, 0))

    protocol_ftp_checkbox = tk.Checkbutton(
        protocol_column,
        text="FTP",
        variable=filter_vars["protocol_ftp"],
        command=callbacks["on_protocol_filter_changed"],
    )
    theme.apply_to_widget(protocol_ftp_checkbox, "checkbox")
    protocol_ftp_checkbox.pack(anchor="w", pady=(2, 0))

    protocol_http_checkbox = tk.Checkbutton(
        protocol_column,
        text="HTTP",
        variable=filter_vars["protocol_http"],
        command=callbacks["on_protocol_filter_changed"],
    )
    theme.apply_to_widget(protocol_http_checkbox, "checkbox")
    protocol_http_checkbox.pack(anchor="w", pady=(2, 0))

    # --- Right column: Country filter ---
    right_column = tk.Frame(advanced_filters_frame)
    theme.apply_to_widget(right_column, "card")
    right_column.pack(side=tk.LEFT, padx=10, pady=5, anchor="n")

    country_label = theme.create_styled_label(
        right_column,
        "Countries (2-letter):",
        "small"
    )
    country_label.pack(anchor="w")

    country_list_container = tk.Frame(right_column)
    country_list_container.pack(anchor="w")

    country_scrollbar = tk.Scrollbar(country_list_container)
    country_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    country_listbox = tk.Listbox(
        country_list_container,
        height=6,
        width=15,
        selectmode=tk.MULTIPLE,
        exportselection=False,
        yscrollcommand=country_scrollbar.set
    )
    country_listbox.pack(side=tk.LEFT)
    country_scrollbar.config(command=country_listbox.yview)

    country_listbox.bind('<<ListboxSelect>>', lambda e: callbacks['on_country_filter_changed']())
    theme.apply_to_widget(country_listbox, "listbox")

    # Container for Clear button and filter entry
    country_controls_frame = tk.Frame(right_column)
    country_controls_frame.pack(anchor="w")

    country_clear_button = tk.Button(
        country_controls_frame,
        text="Clear",
        command=callbacks['on_clear_countries']
    )
    theme.apply_to_widget(country_clear_button, "button_secondary")
    country_clear_button.pack(side=tk.LEFT)

    country_filter_entry = tk.Entry(
        country_controls_frame,
        width=5,
        textvariable=filter_vars.get('country_filter_text')
    )
    theme.apply_to_widget(country_filter_entry, "entry")
    country_filter_entry.pack(side=tk.LEFT, padx=(5, 0))
    country_filter_entry.bind('<KeyRelease>', lambda e: callbacks['on_country_filter_text_changed']())

    # Widget references for parent access
    widget_refs = {
        'advanced_filters_frame': advanced_filters_frame,
        'search_entry': search_entry,
        'date_combo': date_combo,
        'shares_filter_checkbox': shares_filter_checkbox,
        'favorites_checkbox': favorites_checkbox,
        'exclude_avoid_checkbox': exclude_avoid_checkbox,
        'probed_only_checkbox': probed_only_checkbox,
        'exclude_compromised_checkbox': exclude_compromised_checkbox,
        'protocol_smb_checkbox': protocol_smb_checkbox,
        'protocol_ftp_checkbox': protocol_ftp_checkbox,
        'protocol_http_checkbox': protocol_http_checkbox,
        'country_listbox': country_listbox,
        'reset_button': reset_button,
        'add_record_button': add_record_button,
        'mode_button': mode_button,
        'filter_template_dropdown': template_dropdown,
        'filter_template_save_button': save_button,
        'filter_template_delete_button': delete_button,
    }

    if show_all_button:
        widget_refs['show_all_button'] = show_all_button

    return filter_frame, widget_refs


def apply_search_filter(servers: List[Dict[str, Any]], search_term: str) -> List[Dict[str, Any]]:
    """
    Apply search filter to server list.

    Args:
        servers: List of servers to filter
        search_term: Search term to match against IP and shares

    Returns:
        Filtered list of servers
    """
    if not search_term:
        return servers

    search_term = search_term.lower()
    filtered = []

    for server in servers:
        # Search in IP address and accessible shares list
        if (search_term in server.get("ip_address", "").lower() or
            search_term in server.get("accessible_shares_list", "").lower()):
            filtered.append(server)

    return filtered


def apply_date_filter(servers: List[Dict[str, Any]], filter_type: str, last_scan_time) -> List[Dict[str, Any]]:
    """
    Apply date-based filtering to server list.

    Args:
        servers: List of servers to filter
        filter_type: Type of date filter to apply
        last_scan_time: Last scan time for "Since Last Scan" filter

    Returns:
        Filtered list of servers
    """
    if not filter_type or filter_type == "All":
        return servers

    now = datetime.now()
    cutoff_time = None

    if filter_type == "Since Last Scan" and last_scan_time:
        cutoff_time = last_scan_time
    elif filter_type == "Last 24 Hours":
        cutoff_time = now - timedelta(hours=24)
    elif filter_type == "Last 7 Days":
        cutoff_time = now - timedelta(days=7)
    elif filter_type == "Last 30 Days":
        cutoff_time = now - timedelta(days=30)

    if not cutoff_time:
        return servers

    filtered = []
    for server in servers:
        # Check various date fields that might be available
        server_date = None

        # Try different date field names
        for date_field in ["first_seen", "last_seen", "discovery_date", "created_at"]:
            if date_field in server and server[date_field]:
                try:
                    server_date = datetime.fromisoformat(server[date_field].replace("Z", "+00:00"))
                    break
                except (ValueError, AttributeError):
                    continue

        # If we found a valid date, compare it
        if server_date and server_date >= cutoff_time:
            filtered.append(server)
        elif not server_date:
            # If no date available and we're filtering for recent items, exclude
            # But if filtering "Since Last Scan" and no date, include (assume old data)
            if filter_type == "Since Last Scan":
                filtered.append(server)

    return filtered


def apply_shares_filter(servers: List[Dict[str, Any]], shares_only: bool) -> List[Dict[str, Any]]:
    """
    Apply accessible-shares filter to server list.

    Args:
        servers: List of servers to filter
        shares_only: If True, only show rows with accessible shares > 0.

    Returns:
        Filtered list of servers
    """
    if not shares_only:
        return servers

    return [server for server in servers if server.get("accessible_shares", 0) > 0]


def apply_favorites_filter(servers: List[Dict[str, Any]], favorites_only: bool, settings_manager=None) -> List[Dict[str, Any]]:
    """
    Apply favorites filter using the DB-backed per-protocol row field.

    Note: settings_manager is ignored — DB row['favorite'] is canonical as of Card 4.
    Settings-file favorites are intentionally not consumed here (migration boundary).
    """
    if not favorites_only:
        return servers
    return [server for server in servers if server.get("favorite", 0)]


def apply_exclude_avoid_filter(servers: List[Dict[str, Any]], exclude_avoid: bool, settings_manager=None) -> List[Dict[str, Any]]:
    """
    Exclude servers marked as avoid using the DB-backed per-protocol row field.

    Note: settings_manager is ignored — DB row['avoid'] is canonical as of Card 4.
    """
    if not exclude_avoid:
        return servers
    return [server for server in servers if not server.get("avoid", 0)]


def apply_probed_filter(servers: List[Dict[str, Any]], probed_only: bool) -> List[Dict[str, Any]]:
    """Keep only servers that have been probed."""
    if not probed_only:
        return servers

    def _is_probed(server: Dict[str, Any]) -> bool:
        status = (server.get("probe_status") or "").lower()
        if status and status not in ("unprobed", "unknown"):
            return True
        return bool(server.get("indicator_matches", 0) > 0)

    return [server for server in servers if _is_probed(server)]


def apply_exclude_compromised_filter(servers: List[Dict[str, Any]], exclude_compromised: bool) -> List[Dict[str, Any]]:
    """Drop servers marked as compromised/issue."""
    if not exclude_compromised:
        return servers

    def _is_compromised(server: Dict[str, Any]) -> bool:
        status = (server.get("probe_status") or "").lower()
        if status == "issue":
            return True
        return bool(server.get("indicator_matches", 0) > 0)

    return [server for server in servers if not _is_compromised(server)]


def apply_protocol_filter(servers: List[Dict[str, Any]], selected_types: List[str]) -> List[Dict[str, Any]]:
    """
    Keep only rows whose host_type is selected.

    Args:
        servers: Server rows from the unified S/F/H dataset
        selected_types: Allowed protocol types, e.g. ["S", "F", "H"]

    Returns:
        Filtered rows matching selected host_type values.
    """
    allowed = {str(t).upper() for t in (selected_types or []) if t}
    if not allowed:
        return []
    return [server for server in servers if str(server.get("host_type", "S")).upper() in allowed]


def apply_country_filter(servers: List[Dict[str, Any]], selected_codes: List[str]) -> List[Dict[str, Any]]:
    """
    Filter servers by selected country codes.

    Args:
        servers: List of server dictionaries
        selected_codes: List of selected 2-letter country codes (e.g., ["US", "GB"])

    Returns:
        Filtered list of servers matching selected countries
    """
    if not selected_codes:
        return servers  # No selection = no filter

    # Case-insensitive matching
    selected_codes_upper = [code.upper() for code in selected_codes]
    return [
        server for server in servers
        if server.get("country_code", "").upper() in selected_codes_upper
    ]


def update_mode_display(advanced_filters_frame, is_advanced_mode: bool):
    """
    Update display based on current mode.

    Args:
        advanced_filters_frame: Advanced filters frame widget
        is_advanced_mode: Whether advanced mode is active
    """
    if is_advanced_mode:
        advanced_filters_frame.pack(fill=tk.X, pady=(5, 0))
    else:
        advanced_filters_frame.pack_forget()
