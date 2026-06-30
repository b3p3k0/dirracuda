"""
Sherlock Pattern Manager dialog, extracted from sherlock_tab.py in C21.

This module is the home for the "Sherlock Patterns" modal dialog: the C23 two-pane
(Regedit-style) layout - a left category index pane and a right grouped value-row
pane - plus the C18 search/facet filter row, the Add/Edit dialog, and the Copy /
Delete / Enable-Disable / Restore Built-ins / Export / Save & Close actions.

C23 replaced the original single flat pattern table with the two-pane view. The
right pane shows one row per *value group* (built via shared.sherlock.grouping),
so several stored SherlockPattern rows sharing category/label/severity/tag/type
collapse into one display row; row actions map back to every underlying member.
Category selection/search and value filtering are display-only: they never mutate
tab._patterns or the dirty flag, and hidden rows are never actionable. The stored
schema and wire format are unchanged - one SherlockPattern per pattern string.

Each public function takes the owning SherlockTab instance as ``tab`` and reads
and writes its existing instance state (``tab._patterns``,
``tab._pattern_manager_dirty``, ``tab._tree`` and the ``tab._filter_*`` vars).
SherlockTab keeps thin delegating stub methods, so the dialog is still driven as
``tab._on_add()``, ``tab._open_pattern_dialog()`` and so on; sibling operations
are invoked through ``tab._method(...)`` so instance monkeypatches still apply.
Messageboxes resolve ``sherlock_tab.safe_messagebox`` at call time via ``_mb()``
so the existing test patches on that namespace keep intercepting.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
import tkinter as tk
from collections import Counter
from datetime import datetime
from tkinter import ttk, filedialog
from typing import Any, Dict, List, Optional, Tuple

from gui.utils.dialog_helpers import ensure_dialog_focus
from shared.sherlock.export import build_export_payload
from shared.sherlock.grouping import PatternValueGroup, group_patterns
from shared.sherlock import (
    COLOR_TAG_NONE,
    USER_COLOR_KEYS,
    Severity,
    SherlockPattern,
    builtin_patterns,
    category_choices,
    normalize_color_tag,
)


def _mb():
    """Resolve safe_messagebox through the sherlock_tab namespace at call time.

    Keeps ``monkeypatch.setattr(sherlock_tab, "safe_messagebox", ...)`` effective
    for messageboxes raised here, following the dashboard satellites' documented
    ``_mb()`` dispatch discipline. Imported lazily so this module never imports
    sherlock_tab at load time (one-way import: sherlock_tab -> this module).
    """
    from gui.components.experimental_features import sherlock_tab

    return sherlock_tab.safe_messagebox


_SEVERITY_ORDER: Tuple[Severity, ...] = (Severity.HIGH, Severity.MED, Severity.LOW)
_SEVERITY_LABELS: Dict[Severity, str] = {
    Severity.HIGH: "High",
    Severity.MED: "Med",
    Severity.LOW: "Low",
}
_SEVERITY_CHOICES: Tuple[str, ...] = tuple(_SEVERITY_LABELS[s] for s in _SEVERITY_ORDER)
_LABEL_TO_SEVERITY: Dict[str, Severity] = {v: k for k, v in _SEVERITY_LABELS.items()}

# User color tag tokens <-> display labels.
_USER_COLOR_LABELS: Dict[str, str] = {key: key.capitalize() for key in USER_COLOR_KEYS}
# Add/Edit dialog dropdown choices (custom patterns only).
_COLOR_TAG_CHOICES: Tuple[str, ...] = ("None",) + tuple(
    _USER_COLOR_LABELS[key] for key in USER_COLOR_KEYS
)
_DIALOG_LABEL_TO_TAG: Dict[str, str] = {"None": COLOR_TAG_NONE}
_DIALOG_LABEL_TO_TAG.update({_USER_COLOR_LABELS[key]: key for key in USER_COLOR_KEYS})
_TAG_TO_DIALOG_LABEL: Dict[str, str] = {v: k for k, v in _DIALOG_LABEL_TO_TAG.items()}
# Pattern table "User Tag" cell: blank for the no-tag token, label otherwise.
_COLOR_TAG_CELL_LABELS: Dict[str, str] = {COLOR_TAG_NONE: ""}
_COLOR_TAG_CELL_LABELS.update({key: _USER_COLOR_LABELS[key] for key in USER_COLOR_KEYS})

# C23 left-pane category index. The "All Categories" row uses a fixed iid distinct
# from any "cat::<name>" row, and maps to the _FACET_ALL active-category sentinel.
_ALL_ROW_IID = "all-categories"
_ALL_CATEGORIES_LABEL = "All Categories"
_CATEGORY_IID_PREFIX = "cat::"

# C18 pattern-manager filter facets. "All" disables a facet. Severity and Enabled
# are fixed lists; User Tag is rebuilt from the staged rows. Category is chosen in
# the C23 left pane (tab._active_category), no longer a right-pane facet combo.
_FACET_ALL = "All"
_SEVERITY_FACET_CHOICES: Tuple[str, ...] = (
    _FACET_ALL,
) + tuple(s.display_name for s in _SEVERITY_ORDER)
_ENABLED_FACET_CHOICES: Tuple[str, ...] = (_FACET_ALL, "Enabled", "Disabled")


def _user_tag_facet_choices(patterns) -> List[str]:
    """User Tag facet values present among *patterns* (without leading 'All').

    Emits 'None' when any row is untagged, then User1/User2/User3 in canonical
    order for whichever tags actually occur. Callers prepend _FACET_ALL.
    """
    present = {normalize_color_tag(p.color_tag) for p in patterns}
    choices: List[str] = []
    if COLOR_TAG_NONE in present:
        choices.append(_COLOR_TAG_CELL_LABELS.get(COLOR_TAG_NONE) or "None")
    for key in USER_COLOR_KEYS:
        if key in present:
            choices.append(_USER_COLOR_LABELS[key])
    return choices


def _pattern_matches_filters(
    pattern: SherlockPattern,
    *,
    search: str,
    category: str,
    severity: str,
    user_tag: str,
    enabled: str,
) -> bool:
    """Pure predicate: does *pattern* survive the current filter row?

    `search` is a free-text substring (empty = no filter); the facet values use
    exact staged values with `_FACET_ALL` meaning no filter. All conditions are
    ANDed.
    """
    needle = (search or "").strip().casefold()
    if needle:
        tag = normalize_color_tag(pattern.color_tag)
        haystack = " ".join(
            (
                pattern.label,
                pattern.category,
                pattern.pattern,
                pattern.severity.display_name,
                _COLOR_TAG_CELL_LABELS.get(tag, ""),
                tag,
                "Built-in" if pattern.builtin else "Custom",
            )
        ).casefold()
        if needle not in haystack:
            return False
    if category != _FACET_ALL and category.casefold() != (pattern.category or "").casefold():
        return False
    if severity != _FACET_ALL and severity != pattern.severity.display_name:
        return False
    if user_tag != _FACET_ALL:
        if _DIALOG_LABEL_TO_TAG.get(user_tag) != normalize_color_tag(pattern.color_tag):
            return False
    if enabled != _FACET_ALL and (enabled == "Enabled") != bool(pattern.enabled):
        return False
    return True


def _group_iid(group: PatternValueGroup) -> str:
    """Right-pane row iid for a value group: its first member's key.

    First-member keys are globally unique, so group iids are unique. For the
    common single-member group iid == the pattern key, keeping single-row
    selection behavior and selection restore identical to the pre-C23 table.
    """
    return group.members[0].key


def _group_search_matches(group: PatternValueGroup, needle: str) -> bool:
    """Group-level search: match against shared fields or *any* member pattern.

    Matching at the group level (not per-member) keeps a multi-member value row
    intact - a search that hits one member never partially hides the group.
    *needle* is already stripped+casefolded by the caller.
    """
    if not needle:
        return True
    tag = normalize_color_tag(group.color_tag)
    parts = [
        group.label,
        group.category,
        group.severity.display_name,
        _COLOR_TAG_CELL_LABELS.get(tag, ""),
        tag,
        "Built-in" if group.builtin else "Custom",
    ]
    parts.extend(group.patterns)
    return needle in " ".join(parts).casefold()


def _pattern_passes_facets(
    pattern: SherlockPattern,
    *,
    category: str,
    severity: str,
    user_tag: str,
    enabled: str,
) -> bool:
    """The group-invariant facets only (no search): category/severity/tag/enabled.

    These fields are all part of the group key, so filtering patterns by them
    never splits a value group. Search is applied at the group level instead.
    """
    return _pattern_matches_filters(
        pattern,
        search="",
        category=category,
        severity=severity,
        user_tag=user_tag,
        enabled=enabled,
    )


def validate_pattern_fields(pattern: str) -> Tuple[bool, str]:
    """Pure add/edit validation: pattern text must be non-empty."""
    if not isinstance(pattern, str) or not pattern.strip():
        return False, "Pattern is required."
    return True, ""


def _with_enabled(pattern: SherlockPattern, enabled: bool) -> SherlockPattern:
    """Return a copy of *pattern* with a new enabled flag (patterns are frozen).

    Uses dataclasses.replace so every field (incl. color_tag) is preserved.
    """
    return dataclasses.replace(pattern, enabled=enabled)


def _grab_when_viewable(dialog: tk.Toplevel, parent: tk.Misc) -> None:
    """Apply a modal grab only after Tk has mapped the dialog window."""
    dialog.update_idletasks()
    dialog.update()
    if not dialog.winfo_exists():
        return
    dialog.grab_set()
    ensure_dialog_focus(dialog, parent)


# ----------------------------------------------------------------------
# Filter row
# ----------------------------------------------------------------------


def build_filter_row(tab, parent: tk.Widget) -> None:
    """Build the C18 value-search/facet row above the grouped value table.

    Display-only: edits here re-render the visible rows but never touch
    tab._patterns or the dirty flag. Created fresh on every manager open. The
    Category facet moved to the C23 left pane (tab._active_category), so only the
    value search + Severity/User Tag/Enabled facets live here.
    """
    tab._filter_search_var = tk.StringVar(value="")
    tab._filter_severity_var = tk.StringVar(value=_FACET_ALL)
    tab._filter_user_tag_var = tk.StringVar(value=_FACET_ALL)
    tab._filter_enabled_var = tk.StringVar(value=_FACET_ALL)
    # Left pane owns category now; keep the attr cleared for teardown safety.
    tab._filter_category_combo = None

    row = tk.Frame(parent)
    tab._theme.apply_to_widget(row, "main_window")
    row.pack(anchor="w", pady=(0, 8), fill=tk.X)

    def _label(text: str) -> None:
        lbl = tk.Label(row, text=text, anchor="w")
        tab._theme.apply_to_widget(lbl, "label")
        lbl.pack(side=tk.LEFT, padx=(0, 3))

    def _facet(var: tk.StringVar, values, width: int) -> ttk.Combobox:
        combo = ttk.Combobox(
            row, textvariable=var, values=list(values), state="readonly", width=width
        )
        combo.pack(side=tk.LEFT, padx=(0, 10))
        combo.bind("<<ComboboxSelected>>", tab._on_filter_change)
        return combo

    _label("Search:")
    search_entry = tk.Entry(row, textvariable=tab._filter_search_var, width=16)
    tab._theme.apply_to_widget(search_entry, "entry")
    search_entry.pack(side=tk.LEFT, padx=(0, 10))
    tab._filter_search_var.trace_add("write", tab._on_filter_change)

    _label("Severity:")
    _facet(tab._filter_severity_var, _SEVERITY_FACET_CHOICES, 7)
    _label("User Tag:")
    tab._filter_user_tag_combo = _facet(tab._filter_user_tag_var, [_FACET_ALL], 8)
    _label("Enabled:")
    _facet(tab._filter_enabled_var, _ENABLED_FACET_CHOICES, 9)

    clear_btn = tk.Button(row, text="Clear", command=tab._on_clear_filters)
    tab._theme.apply_to_widget(clear_btn, "button_secondary")
    clear_btn.pack(side=tk.LEFT)


def build_category_pane(tab, parent: tk.Widget) -> None:
    """Build the C23 left category index: search box + category Treeview.

    Display-only. Selecting a category sets tab._active_category and re-renders
    the right pane; the search box filters which category rows show without
    touching the right pane, the active category, _patterns, or the dirty flag.
    """
    tab._category_search_var = tk.StringVar(value="")

    header = tk.Label(parent, text="Categories", anchor="w")
    tab._theme.apply_to_widget(header, "label")
    header.pack(anchor="w", pady=(0, 4))

    search_row = tk.Frame(parent)
    tab._theme.apply_to_widget(search_row, "main_window")
    search_row.pack(anchor="w", pady=(0, 8), fill=tk.X)
    search_label = tk.Label(search_row, text="Search:", anchor="w")
    tab._theme.apply_to_widget(search_label, "label")
    search_label.pack(side=tk.LEFT, padx=(0, 3))
    search_entry = tk.Entry(search_row, textvariable=tab._category_search_var, width=16)
    tab._theme.apply_to_widget(search_entry, "entry")
    search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
    tab._category_search_var.trace_add("write", tab._on_category_search)

    tab._build_category_action_row(parent)

    tree_frame = tk.Frame(parent)
    tab._theme.apply_to_widget(tree_frame, "main_window")
    tree_frame.pack(fill=tk.BOTH, expand=True)

    tree = ttk.Treeview(
        tree_frame,
        columns=("category", "count"),
        show="headings",
        selectmode="browse",
        height=16,
    )
    tree.heading("category", text="Category")
    tree.heading("count", text="#")
    tree.column("category", width=150, anchor="w", stretch=True)
    tree.column("count", width=44, anchor="center", stretch=False)

    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    tree_frame.grid_rowconfigure(0, weight=1)
    tree_frame.grid_columnconfigure(0, weight=1)

    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        tree.bind(sequence, tab._on_mousewheel)
    tree.bind("<<TreeviewSelect>>", tab._on_category_select)

    tab._category_tree = tree


def on_filter_change(tab, *_args: Any) -> None:
    """Re-render the visible rows; clear selection so hidden rows can't be acted on."""
    if getattr(tab, "_applying_filter_reset", False):
        return
    tree = tab._tree
    if tree is None:
        return
    selection = tree.selection()
    if selection:
        tree.selection_remove(*selection)
    tab._refresh_table()


def on_clear_filters(tab) -> None:
    """Reset value search/facets, the category search, and the active category.

    Display-only: resets both panes to the unfiltered "All Categories" view and
    re-renders once each, never touching tab._patterns or the dirty flag.
    """
    tab._applying_filter_reset = True
    try:
        tab._filter_search_var.set("")
        tab._filter_severity_var.set(_FACET_ALL)
        tab._filter_user_tag_var.set(_FACET_ALL)
        tab._filter_enabled_var.set(_FACET_ALL)
        cat_search = getattr(tab, "_category_search_var", None)
        if cat_search is not None:
            cat_search.set("")
        tab._active_category = _FACET_ALL
    finally:
        tab._applying_filter_reset = False
    tab._refresh_category_list()
    tab._on_filter_change()


# ----------------------------------------------------------------------
# Left category pane: render / select / search (display-only)
# ----------------------------------------------------------------------


def refresh_category_list(tab) -> None:
    """Render the left category index: All Categories + per-category rows.

    Counts are *grouped value-row* counts (one per value group), not raw pattern
    counts. The category search filters which rows show (display-only). Reselects
    the active-category row under a guard so the reselection never re-fires the
    selection handler. No-op until the category tree has been built.
    """
    tree = getattr(tab, "_category_tree", None)
    if tree is None:
        return
    groups = group_patterns(tab._patterns)
    counts = Counter(group.category for group in groups)
    total = len(groups)
    placeholders = getattr(tab, "_placeholder_categories", None) or ()
    names = category_choices(tab._patterns, always_include=tuple(placeholders))

    needle = ""
    var = getattr(tab, "_category_search_var", None)
    if var is not None:
        needle = (var.get() or "").strip().casefold()

    active = getattr(tab, "_active_category", _FACET_ALL)
    target = _ALL_ROW_IID if active == _FACET_ALL else _CATEGORY_IID_PREFIX + active

    tab._applying_category_refresh = True
    try:
        for iid in tree.get_children():
            tree.delete(iid)
        tree.insert(
            "", "end", iid=_ALL_ROW_IID, values=(_ALL_CATEGORIES_LABEL, total)
        )
        for name in names:
            if needle and needle not in name.casefold():
                continue
            tree.insert(
                "",
                "end",
                iid=_CATEGORY_IID_PREFIX + name,
                values=(name, counts.get(name, 0)),
            )
        if tree.exists(target):
            tree.selection_set(target)
        else:
            # Active category currently filtered out of the left list: leave the
            # left selection empty but keep _active_category so the right pane
            # still shows it (category search is display-only).
            selection = tree.selection()
            if selection:
                tree.selection_remove(*selection)
    finally:
        tab._applying_category_refresh = False


def on_category_select(tab, *_args: Any) -> None:
    """Left-pane selection: set the active category and re-render the right pane.

    Display-only: never mutates _patterns or the dirty flag. The right-pane
    selection is cleared so no previously-selected (now hidden) row stays
    actionable. Guarded against the reselection done inside refresh_category_list.
    """
    if getattr(tab, "_applying_category_refresh", False):
        return
    tree = getattr(tab, "_category_tree", None)
    if tree is None:
        return
    selection = tree.selection()
    if not selection:
        return
    iid = selection[0]
    if iid == _ALL_ROW_IID:
        tab._active_category = _FACET_ALL
    elif iid.startswith(_CATEGORY_IID_PREFIX):
        tab._active_category = iid[len(_CATEGORY_IID_PREFIX):]
    else:
        return
    rtree = getattr(tab, "_tree", None)
    if rtree is not None:
        rsel = rtree.selection()
        if rsel:
            rtree.selection_remove(*rsel)
    tab._refresh_table()


def on_category_search(tab, *_args: Any) -> None:
    """Category search edit: re-render only the left list (display-only)."""
    if getattr(tab, "_applying_filter_reset", False):
        return
    tab._refresh_category_list()


def build_table(tab, parent: tk.Widget) -> None:
    table_frame = tk.Frame(parent)
    tab._theme.apply_to_widget(table_frame, "main_window")
    table_frame.pack(fill=tk.BOTH, expand=True)

    columns = (
        "enabled",
        "severity",
        "user_tag",
        "label",
        "pattern",
        "type",
    )
    tree = ttk.Treeview(
        table_frame,
        columns=columns,
        show="headings",
        selectmode="extended",
        height=16,
    )
    # Category lives in the left pane now; "Patterns" holds the grouped tokens.
    headings = {
        "enabled": ("On", 40),
        "severity": ("Severity", 66),
        "user_tag": ("User Tag", 70),
        "label": ("Label", 150),
        "pattern": ("Patterns", 220),
        "type": ("Type", 70),
    }
    for col, (heading, width) in headings.items():
        tree.heading(col, text=heading)
        anchor = "center" if col in ("enabled", "severity", "user_tag", "type") else "w"
        tree.column(col, width=width, anchor=anchor, stretch=(col == "pattern"))

    scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)

    tree.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    table_frame.grid_rowconfigure(0, weight=1)
    table_frame.grid_columnconfigure(0, weight=1)

    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        tree.bind(sequence, tab._on_mousewheel)
    tree.bind("<Double-1>", tab._on_row_double_click)

    tab._tree = tree


# ----------------------------------------------------------------------
# Pattern manager dialog
# ----------------------------------------------------------------------


def open_pattern_manager(tab) -> None:
    """Open the tall modal Sherlock Patterns dialog (staged edits)."""
    parent = tab.frame.winfo_toplevel()
    dialog = tk.Toplevel(parent)
    dialog.title("Sherlock Patterns")
    dialog.transient(parent)
    tab._theme.apply_to_widget(dialog, "main_window")
    dialog.geometry("1200x620")
    dialog.minsize(1120, 560)

    outer = tk.Frame(dialog, padx=12, pady=10)
    tab._theme.apply_to_widget(outer, "main_window")
    outer.pack(fill=tk.BOTH, expand=True)

    panes = ttk.PanedWindow(outer, orient=tk.HORIZONTAL)
    panes.pack(fill=tk.BOTH, expand=True)

    left_pane = tk.Frame(panes)
    tab._theme.apply_to_widget(left_pane, "main_window")
    right_pane = tk.Frame(panes)
    tab._theme.apply_to_widget(right_pane, "main_window")
    panes.add(left_pane, weight=1)
    panes.add(right_pane, weight=3)

    tab._build_category_pane(left_pane)
    tab._build_filter_row(right_pane)
    tab._build_table(right_pane)

    action_row = tk.Frame(outer)
    tab._theme.apply_to_widget(action_row, "main_window")
    action_row.pack(anchor="w", pady=(8, 0), fill=tk.X)

    add_btn = tk.Button(action_row, text="Add", command=tab._on_add)
    tab._theme.apply_to_widget(add_btn, "button_secondary")
    add_btn.pack(side=tk.LEFT, padx=(0, 6))

    edit_btn = tk.Button(action_row, text="Edit", command=tab._on_edit)
    tab._theme.apply_to_widget(edit_btn, "button_secondary")
    edit_btn.pack(side=tk.LEFT, padx=(0, 6))

    toggle_btn = tk.Button(
        action_row, text="Enable/Disable", command=tab._on_toggle
    )
    tab._theme.apply_to_widget(toggle_btn, "button_secondary")
    toggle_btn.pack(side=tk.LEFT, padx=(0, 6))

    delete_btn = tk.Button(action_row, text="Delete", command=tab._on_delete)
    tab._theme.apply_to_widget(delete_btn, "button_danger")
    delete_btn.pack(side=tk.LEFT, padx=(0, 6))

    copy_btn = tk.Button(action_row, text="Copy", command=tab._on_copy)
    tab._theme.apply_to_widget(copy_btn, "button_secondary")
    copy_btn.pack(side=tk.LEFT, padx=(0, 6))

    restore_btn = tk.Button(
        action_row, text="Restore Built-ins", command=tab._on_restore_builtins
    )
    tab._theme.apply_to_widget(restore_btn, "button_secondary")
    restore_btn.pack(side=tk.LEFT, padx=(0, 6))

    export_btn = tk.Button(action_row, text="Export", command=tab._on_export)
    tab._theme.apply_to_widget(export_btn, "button_secondary")
    export_btn.pack(side=tk.LEFT, padx=(0, 6))

    savec_btn = tk.Button(
        action_row, text="Save & Close", command=tab._save_and_close_manager
    )
    tab._theme.apply_to_widget(savec_btn, "button_primary")
    savec_btn.pack(side=tk.LEFT, padx=(0, 6))

    close_btn = tk.Button(action_row, text="Close", command=tab._close_manager)
    tab._theme.apply_to_widget(close_btn, "button_secondary")
    close_btn.pack(side=tk.LEFT, padx=(0, 6))

    tab._pattern_manager = dialog
    tab._active_category = _FACET_ALL
    tab._placeholder_categories = []
    tab._refresh_category_list()
    tab._refresh_table()
    try:
        tab._tree.focus_set()
    except Exception:
        pass

    dialog.protocol("WM_DELETE_WINDOW", tab._close_manager)
    dialog.grab_set()
    ensure_dialog_focus(dialog, parent)
    dialog.wait_window()


def teardown_manager(tab) -> None:
    """Clear manager references and destroy the dialog."""
    dialog = tab._pattern_manager
    tab._pattern_manager = None
    tab._tree = None
    tab._category_tree = None
    tab._filter_category_combo = None
    tab._filter_user_tag_combo = None
    if dialog is not None:
        dialog.destroy()


def close_manager(tab) -> None:
    """Close button / window close: warn first if there are staged edits."""
    if tab._pattern_manager_dirty:
        confirm = _mb().askyesno(
            "Unsaved Pattern Changes",
            "You have unsaved pattern changes. Close without saving?",
            parent=tab._pattern_manager,
        )
        if not confirm:
            return
    tab._teardown_manager()


def save_and_close_manager(tab) -> None:
    """Run the standard save/validation path; close only when it succeeds."""
    if tab._on_save():  # success clears _pattern_manager_dirty
        tab._teardown_manager()


# ----------------------------------------------------------------------
# Table rendering / selection
# ----------------------------------------------------------------------


def current_filter_state(tab) -> Tuple[str, str, str, str, str]:
    """Read the filter row vars, defaulting to no-filter when absent.

    Returns (search, category, severity, user_tag, enabled). When the filter
    widgets have not been built (e.g. unit tests that mock the tree), every
    facet reads as `_FACET_ALL` so the whole catalog stays visible.
    """
    def _get(name: str, default: str) -> str:
        var = getattr(tab, name, None)
        if var is None:
            return default
        return var.get()

    return (
        _get("_filter_search_var", "").strip(),
        getattr(tab, "_active_category", _FACET_ALL) or _FACET_ALL,
        _get("_filter_severity_var", _FACET_ALL) or _FACET_ALL,
        _get("_filter_user_tag_var", _FACET_ALL) or _FACET_ALL,
        _get("_filter_enabled_var", _FACET_ALL) or _FACET_ALL,
    )


def visible_groups(tab) -> List[PatternValueGroup]:
    """Value groups surviving the current filter state (never mutates state).

    The group-invariant facets (category from the left pane, severity, user tag,
    enabled) filter the patterns first - none of these can split a group - then
    the rows are grouped and the value search is applied at the group level so a
    multi-member group is never partially hidden.
    """
    search, category, severity, user_tag, enabled = tab._current_filter_state()
    candidates = [
        p
        for p in tab._patterns
        if _pattern_passes_facets(
            p,
            category=category,
            severity=severity,
            user_tag=user_tag,
            enabled=enabled,
        )
    ]
    groups = group_patterns(candidates)
    needle = (search or "").strip().casefold()
    if not needle:
        return groups
    return [g for g in groups if _group_search_matches(g, needle)]


def visible_keys(tab) -> set:
    """Iids of currently-visible value groups; used to keep selection visible."""
    return {_group_iid(g) for g in tab._visible_groups()}


def refresh_facet_choices(tab) -> None:
    """Rebuild the dynamic User Tag facet value list.

    Reset the facet to `_FACET_ALL` when its selected value is no longer present
    among staged rows. No-op when the combo has not been built. (Category is the
    left pane now and is refreshed by refresh_category_list.)
    """
    tag_combo = getattr(tab, "_filter_user_tag_combo", None)
    if tag_combo is not None:
        try:
            exists = tag_combo.winfo_exists()
        except Exception:
            exists = False
        if exists:
            choices = [_FACET_ALL] + _user_tag_facet_choices(tab._patterns)
            tag_combo["values"] = choices
            if tab._filter_user_tag_var.get() not in choices:
                tab._filter_user_tag_var.set(_FACET_ALL)


def refresh_table(tab) -> None:
    """Render one right-pane row per visible value group (iid = first member key)."""
    tree = tab._tree
    if tree is None:
        return
    tab._refresh_facet_choices()
    for iid in tree.get_children():
        tree.delete(iid)
    for group in tab._visible_groups():
        tag = normalize_color_tag(group.color_tag)
        tree.insert(
            "",
            "end",
            iid=_group_iid(group),
            values=(
                "Yes" if group.enabled else "No",
                group.severity.display_name,
                _COLOR_TAG_CELL_LABELS.get(tag, ""),
                group.label,
                ", ".join(group.patterns),
                "Built-in" if group.builtin else "Custom",
            ),
        )


def refresh_after_mutation(tab) -> None:
    """Refresh both panes after a staged mutation; reconcile the active category.

    Every handler that changes tab._patterns routes through here. If the active
    category no longer exists in the catalog (its last group was deleted), reset
    it to "All Categories" so the right pane is never filtered by an invisible
    category. Then re-render the left counts and the right grouped rows.
    """
    active = getattr(tab, "_active_category", _FACET_ALL)
    if active != _FACET_ALL and active not in category_choices(
        tab._patterns, always_include=()
    ):
        tab._active_category = _FACET_ALL
    tab._refresh_category_list()
    tab._refresh_table()


def selected_groups(tab) -> List[PatternValueGroup]:
    """Visible value groups whose row is currently selected, in visible order.

    Iterating tab._visible_groups() reproduces the render order, so the result
    follows visible row order regardless of Ctrl/Shift click sequence. Only
    visible groups are returned, so filtered-out rows are never actionable.
    """
    selection = set(tab._tree.selection())
    if not selection:
        return []
    return [g for g in tab._visible_groups() if _group_iid(g) in selection]


def selected_patterns(tab) -> List[SherlockPattern]:
    """Underlying SherlockPattern members of every selected visible group."""
    return [member for group in tab._selected_groups() for member in group.members]


def replace_pattern(tab, key: str, new_pattern: SherlockPattern) -> None:
    tab._patterns = [
        new_pattern if p.key == key else p for p in tab._patterns
    ]


def on_mousewheel(tab, event: Any) -> str:
    """Scroll whichever tree is under the pointer (left index or right values)."""
    widget = getattr(event, "widget", None)
    tree = widget if isinstance(widget, ttk.Treeview) else getattr(tab, "_tree", None)
    if tree is None:
        return "break"
    delta = 0
    if getattr(event, "delta", 0):
        delta = -1 if event.delta > 0 else 1
    elif getattr(event, "num", None) == 4:
        delta = -1
    elif getattr(event, "num", None) == 5:
        delta = 1
    if delta:
        tree.yview_scroll(delta, "units")
    return "break"


def on_row_double_click(tab, event: Any) -> str:
    """Double-click a row to edit exactly that row.

    Selects the clicked row first, then routes through _on_edit so the C15
    built-in lifecycle holds (built-ins edit-as-copy, customs edit in place).
    A double-click on empty space changes nothing.

    The edit is deferred with after_idle so dialog creation runs after this
    click event finishes; the Add/Edit dialog also waits until it is
    viewable before taking a modal grab.
    """
    tree = tab._tree
    if tree is None:
        return "break"
    row = tree.identify_row(event.y)
    if not row:
        return "break"
    tree.selection_set(row)
    tree.after_idle(tab._on_edit)
    return "break"


# ----------------------------------------------------------------------
# Action handlers
# ----------------------------------------------------------------------


def on_toggle(tab) -> None:
    """Enable/Disable every member of each selected visible value group."""
    groups = tab._selected_groups()
    if not groups:
        tab._set_status("Select one or more patterns to enable or disable.")
        return
    keys = {member.key for group in groups for member in group.members}
    tab._patterns = [
        _with_enabled(p, not p.enabled) if p.key in keys else p
        for p in tab._patterns
    ]
    tab._pattern_manager_dirty = True
    tab._refresh_after_mutation()
    # Members keep their keys, so each group's iid (first member key) is stable;
    # restore selection only for groups still visible after the toggle.
    visible = tab._visible_keys()
    tab._tree.selection_set(
        [_group_iid(g) for g in groups if _group_iid(g) in visible]
    )


def on_delete(tab) -> None:
    """Remove every member of each selected visible value group."""
    groups = tab._selected_groups()
    if not groups:
        tab._set_status("Select one or more patterns to delete.")
        return
    keys = {member.key for group in groups for member in group.members}
    tab._patterns = [p for p in tab._patterns if p.key not in keys]
    tab._pattern_manager_dirty = True
    tab._refresh_after_mutation()


def on_restore_builtins(tab) -> None:
    """Re-enable all built-ins (clears any disabled state); customs untouched."""
    customs = [p for p in tab._patterns if not p.builtin]
    tab._patterns = builtin_patterns() + customs
    tab._pattern_manager_dirty = True
    tab._refresh_after_mutation()
    tab._set_status("Built-ins restored.")


def on_export(tab) -> None:
    """Write the full staged pattern list to a user-chosen JSON file.

    Read-only: never mutates _patterns, filter vars, selection, the dirty
    flag, settings, or persistence. Cancel is silent; write errors report via
    safe_messagebox; success only updates the status line (manager stays open).
    """
    now = datetime.now()
    default_name = f"sherlock_patterns_{now.strftime('%Y%m%d_%H%M%S')}.json"
    path = filedialog.asksaveasfilename(
        parent=tab._pattern_manager,
        title="Export Sherlock Patterns",
        defaultextension=".json",
        filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        initialfile=default_name,
    )
    if not path:
        return
    payload = build_export_payload(
        tab._patterns, exported_at=now.isoformat(timespec="seconds")
    )
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError as exc:
        _mb().showerror(
            "Export Failed",
            "Could not write pattern export:\n{0}".format(exc),
            parent=tab._active_dialog_parent(),
        )
        return
    tab._set_status(
        "Exported {0} patterns to {1}.".format(payload["count"], path)
    )


def on_add(tab) -> None:
    result = tab._open_pattern_dialog()
    if result is None:
        return
    tab._append_custom_from_result(result)


def on_copy(tab) -> None:
    """Copy exactly one selected visible value group as new custom rows.

    Single-member group -> the prefilled Add dialog (existing UX). Multi-member
    group -> no-dialog duplicate of every member as a custom (the single-pattern
    dialog can't represent multiple patterns; comma input is a later card).
    """
    groups = tab._selected_groups()
    if len(groups) != 1:
        tab._set_status("Select exactly one pattern to copy.")
        return
    group = groups[0]
    if len(group.members) == 1:
        tab._add_from_source(group.members[0])
    else:
        tab._duplicate_group_as_customs(group)


def duplicate_group_as_customs(tab, group: PatternValueGroup) -> None:
    """Duplicate every member of *group* as a new custom row (no dialog)."""
    new_keys: List[str] = []
    for member in group.members:
        pattern = SherlockPattern(
            key="custom_{0}".format(uuid.uuid4().hex),
            category=member.category,
            label=member.label,
            pattern=member.pattern,
            severity=member.severity,
            enabled=member.enabled,
            builtin=False,
            color_tag=normalize_color_tag(member.color_tag),
        )
        tab._patterns.append(pattern)
        new_keys.append(pattern.key)
    tab._pattern_manager_dirty = True
    tab._refresh_after_mutation()
    if new_keys and new_keys[0] in tab._visible_keys():
        tab._tree.selection_set(new_keys[0])


def add_from_source(tab, source: SherlockPattern) -> None:
    """Open the Add dialog prefilled from *source*; save as a new custom."""
    result = tab._open_pattern_dialog(prefill=source)
    if result is None:
        return
    tab._append_custom_from_result(result)


def append_custom_from_result(tab, result: Dict[str, Any]) -> None:
    pattern = SherlockPattern(
        key="custom_{0}".format(uuid.uuid4().hex),
        category=result["category"],
        label=result["label"],
        pattern=result["pattern"],
        severity=result["severity"],
        enabled=result["enabled"],
        builtin=False,
        color_tag=result.get("color_tag", COLOR_TAG_NONE),
    )
    tab._patterns.append(pattern)
    tab._pattern_manager_dirty = True
    tab._refresh_after_mutation()
    if pattern.key in tab._visible_keys():
        tab._tree.selection_set(pattern.key)


def on_edit(tab) -> None:
    """Edit exactly one selected single-member value group.

    Multi-member groups are guarded (the single-pattern dialog can't represent
    them safely yet); built-in single-member groups follow C15 edit-as-copy.
    """
    groups = tab._selected_groups()
    if len(groups) != 1:
        tab._set_status("Select exactly one pattern to edit.")
        return
    group = groups[0]
    if len(group.members) != 1:
        tab._set_status(
            "Editing a grouped value with multiple patterns isn't supported "
            "yet - use Copy, or delete and re-add."
        )
        return
    pattern = group.members[0]
    if pattern.builtin:
        tab._add_from_source(pattern)
        return
    result = tab._open_pattern_dialog(existing=pattern)
    if result is None:
        return
    updated = SherlockPattern(
        key=pattern.key,
        category=result["category"],
        label=result["label"],
        pattern=result["pattern"],
        severity=result["severity"],
        enabled=result["enabled"],
        builtin=False,
        color_tag=result.get("color_tag", COLOR_TAG_NONE),
    )
    tab._replace_pattern(pattern.key, updated)
    tab._pattern_manager_dirty = True
    tab._refresh_after_mutation()
    if pattern.key in tab._visible_keys():
        tab._tree.selection_set(pattern.key)


# ----------------------------------------------------------------------
# Add/edit dialog
# ----------------------------------------------------------------------


def open_pattern_dialog(
    tab,
    existing: Optional[SherlockPattern] = None,
    *,
    prefill: Optional[SherlockPattern] = None,
) -> Optional[Dict[str, Any]]:
    """Modal add/edit dialog. Returns the collected fields or None on cancel.

    *existing* drives the in-place edit of a custom pattern (Edit title, same
    key replaced by the caller). *prefill* seeds the field values for an Add
    (Copy, or editing a built-in) while keeping the Add title, so the caller
    saves a brand-new custom pattern. Parents to the open Pattern Manager (if
    any) so it stays modal to the manager; the manager's grab is re-asserted
    once this dialog closes.
    """
    source = existing if existing is not None else prefill
    parent = tab._active_dialog_parent()
    dialog = tk.Toplevel(parent)
    dialog.title("Edit Pattern" if existing is not None else "Add Pattern")
    dialog.resizable(False, False)
    dialog.transient(parent)
    tab._theme.apply_to_widget(dialog, "main_window")

    outer = tk.Frame(dialog, padx=14, pady=12)
    tab._theme.apply_to_widget(outer, "main_window")
    outer.pack(fill=tk.BOTH, expand=True)

    label_var = tk.StringVar(value=source.label if source else "")
    category_var = tk.StringVar(value=tab._dialog_category_default(source))
    pattern_var = tk.StringVar(value=source.pattern if source else "")
    severity_var = tk.StringVar(
        value=_SEVERITY_LABELS[source.severity] if source else _SEVERITY_LABELS[Severity.MED]
    )
    source_tag = normalize_color_tag(source.color_tag) if source else COLOR_TAG_NONE
    color_tag_var = tk.StringVar(
        value=_TAG_TO_DIALOG_LABEL.get(source_tag, "None")
    )
    enabled_var = tk.BooleanVar(value=source.enabled if source else True)

    def _add_field(row: int, text: str, var: tk.StringVar) -> None:
        field_label = tk.Label(outer, text=text, anchor="w")
        tab._theme.apply_to_widget(field_label, "label")
        field_label.grid(row=row, column=0, sticky="w", pady=3)
        entry = tk.Entry(outer, textvariable=var, width=28)
        tab._theme.apply_to_widget(entry, "entry")
        entry.grid(row=row, column=1, sticky="ew", pady=3, padx=(8, 0))

    _add_field(0, "Label:", label_var)

    cat_label = tk.Label(outer, text="Category:", anchor="w")
    tab._theme.apply_to_widget(cat_label, "label")
    cat_label.grid(row=1, column=0, sticky="w", pady=3)
    cat_combo = ttk.Combobox(
        outer,
        textvariable=category_var,
        values=tab._dialog_category_choices(),
        state="normal",
        width=28,
    )
    cat_combo.grid(row=1, column=1, sticky="ew", pady=3, padx=(8, 0))

    _add_field(2, "Pattern:", pattern_var)

    sev_label = tk.Label(outer, text="Severity:", anchor="w")
    tab._theme.apply_to_widget(sev_label, "label")
    sev_label.grid(row=3, column=0, sticky="w", pady=3)
    sev_combo = ttk.Combobox(
        outer,
        textvariable=severity_var,
        values=list(_SEVERITY_CHOICES),
        state="readonly",
        width=10,
    )
    sev_combo.grid(row=3, column=1, sticky="w", pady=3, padx=(8, 0))

    tag_label = tk.Label(outer, text="Color tag:", anchor="w")
    tab._theme.apply_to_widget(tag_label, "label")
    tag_label.grid(row=4, column=0, sticky="w", pady=3)
    tag_combo = ttk.Combobox(
        outer,
        textvariable=color_tag_var,
        values=list(_COLOR_TAG_CHOICES),
        state="readonly",
        width=10,
    )
    tag_combo.grid(row=4, column=1, sticky="w", pady=3, padx=(8, 0))

    enabled_cb = tk.Checkbutton(outer, text="Enabled", variable=enabled_var)
    tab._theme.apply_to_widget(enabled_cb, "checkbox")
    enabled_cb.grid(row=5, column=1, sticky="w", pady=3, padx=(8, 0))

    result: Dict[str, Any] = {}

    def _on_ok() -> None:
        ok, message = validate_pattern_fields(pattern_var.get())
        if not ok:
            _mb().showerror("Invalid Pattern", message, parent=dialog)
            return
        result.update(
            {
                "label": label_var.get().strip(),
                "category": category_var.get().strip() or "Custom",
                "pattern": pattern_var.get().strip(),
                "severity": _LABEL_TO_SEVERITY.get(severity_var.get(), Severity.MED),
                "color_tag": normalize_color_tag(
                    _DIALOG_LABEL_TO_TAG.get(color_tag_var.get(), COLOR_TAG_NONE)
                ),
                "enabled": bool(enabled_var.get()),
            }
        )
        dialog.destroy()

    btn_row = tk.Frame(outer)
    tab._theme.apply_to_widget(btn_row, "main_window")
    btn_row.grid(row=6, column=0, columnspan=2, sticky="e", pady=(10, 0))

    ok_btn = tk.Button(btn_row, text="OK", command=_on_ok)
    tab._theme.apply_to_widget(ok_btn, "button_primary")
    ok_btn.pack(side=tk.RIGHT, padx=(6, 0))

    cancel_btn = tk.Button(btn_row, text="Cancel", command=dialog.destroy)
    tab._theme.apply_to_widget(cancel_btn, "button_secondary")
    cancel_btn.pack(side=tk.RIGHT)

    outer.grid_columnconfigure(1, weight=1)
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    _grab_when_viewable(dialog, parent)
    dialog.wait_window()

    # Restore the Pattern Manager's modality: the child grab_set() stole the
    # grab and destroying it does not give it back automatically.
    mgr = getattr(tab, "_pattern_manager", None)
    if mgr is not None:
        try:
            if mgr.winfo_exists():
                mgr.grab_set()
                ensure_dialog_focus(mgr, tab.frame.winfo_toplevel())
        except Exception:
            pass

    return result or None
