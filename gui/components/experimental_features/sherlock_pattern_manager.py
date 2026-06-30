"""
Sherlock Pattern Manager dialog, extracted from sherlock_tab.py in C21.

This module is the home for the "Sherlock Patterns" modal dialog: the flat
pattern table, the C18 search/facet filter row, the Add/Edit dialog, and the
Copy / Delete / Enable-Disable / Restore Built-ins / Export / Save & Close
actions. C21 is a behavior-preserving structural extraction only - the dialog
behaves exactly as it did when these methods lived on SherlockTab.

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
from datetime import datetime
from tkinter import ttk, filedialog
from typing import Any, Dict, List, Optional, Tuple

from gui.utils.dialog_helpers import ensure_dialog_focus
from shared.sherlock.export import build_export_payload
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

# C18 pattern-manager filter facets. "All" disables a facet. Severity and Enabled
# are fixed lists; Category and User Tag are rebuilt from the staged rows.
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
    """Build the C18 filter row above the pattern table.

    Display-only: edits here re-render the visible rows but never touch
    tab._patterns or the dirty flag. Created fresh on every manager open.
    """
    tab._filter_search_var = tk.StringVar(value="")
    tab._filter_category_var = tk.StringVar(value=_FACET_ALL)
    tab._filter_severity_var = tk.StringVar(value=_FACET_ALL)
    tab._filter_user_tag_var = tk.StringVar(value=_FACET_ALL)
    tab._filter_enabled_var = tk.StringVar(value=_FACET_ALL)

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
    search_entry = tk.Entry(row, textvariable=tab._filter_search_var, width=18)
    tab._theme.apply_to_widget(search_entry, "entry")
    search_entry.pack(side=tk.LEFT, padx=(0, 10))
    tab._filter_search_var.trace_add("write", tab._on_filter_change)

    _label("Category:")
    tab._filter_category_combo = _facet(tab._filter_category_var, [_FACET_ALL], 14)
    _label("Severity:")
    _facet(tab._filter_severity_var, _SEVERITY_FACET_CHOICES, 7)
    _label("User Tag:")
    tab._filter_user_tag_combo = _facet(tab._filter_user_tag_var, [_FACET_ALL], 8)
    _label("Enabled:")
    _facet(tab._filter_enabled_var, _ENABLED_FACET_CHOICES, 9)

    clear_btn = tk.Button(row, text="Clear", command=tab._on_clear_filters)
    tab._theme.apply_to_widget(clear_btn, "button_secondary")
    clear_btn.pack(side=tk.LEFT)


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
    """Reset every facet/search field to its default and re-render once."""
    tab._applying_filter_reset = True
    try:
        tab._filter_search_var.set("")
        tab._filter_category_var.set(_FACET_ALL)
        tab._filter_severity_var.set(_FACET_ALL)
        tab._filter_user_tag_var.set(_FACET_ALL)
        tab._filter_enabled_var.set(_FACET_ALL)
    finally:
        tab._applying_filter_reset = False
    tab._on_filter_change()


def build_table(tab, parent: tk.Widget) -> None:
    table_frame = tk.Frame(parent)
    tab._theme.apply_to_widget(table_frame, "main_window")
    table_frame.pack(fill=tk.BOTH, expand=True)

    columns = (
        "enabled",
        "severity",
        "user_tag",
        "category",
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
    headings = {
        "enabled": ("On", 40),
        "severity": ("Severity", 66),
        "user_tag": ("User Tag", 70),
        "category": ("Category", 110),
        "label": ("Label", 130),
        "pattern": ("Pattern", 150),
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
    dialog.geometry("1100x600")
    dialog.minsize(1080, 560)

    outer = tk.Frame(dialog, padx=12, pady=10)
    tab._theme.apply_to_widget(outer, "main_window")
    outer.pack(fill=tk.BOTH, expand=True)

    tab._build_filter_row(outer)
    tab._build_table(outer)

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
        _get("_filter_category_var", _FACET_ALL) or _FACET_ALL,
        _get("_filter_severity_var", _FACET_ALL) or _FACET_ALL,
        _get("_filter_user_tag_var", _FACET_ALL) or _FACET_ALL,
        _get("_filter_enabled_var", _FACET_ALL) or _FACET_ALL,
    )


def visible_patterns(tab) -> List[SherlockPattern]:
    """Staged patterns surviving the current filter row (never mutates state)."""
    search, category, severity, user_tag, enabled = tab._current_filter_state()
    return [
        p
        for p in tab._patterns
        if _pattern_matches_filters(
            p,
            search=search,
            category=category,
            severity=severity,
            user_tag=user_tag,
            enabled=enabled,
        )
    ]


def visible_keys(tab) -> set:
    """Keys of currently-visible rows; used to keep selection on visible rows."""
    return {p.key for p in tab._visible_patterns()}


def refresh_facet_choices(tab) -> None:
    """Rebuild the dynamic Category / User Tag facet value lists.

    Reset a facet to `_FACET_ALL` when its selected value is no longer
    present among staged rows. No-op when the combos have not been built.
    """
    cat_combo = getattr(tab, "_filter_category_combo", None)
    if cat_combo is not None:
        try:
            exists = cat_combo.winfo_exists()
        except Exception:
            exists = False
        if exists:
            choices = [_FACET_ALL] + category_choices(
                tab._patterns, always_include=()
            )
            cat_combo["values"] = choices
            if tab._filter_category_var.get() not in choices:
                tab._filter_category_var.set(_FACET_ALL)

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
    tree = tab._tree
    if tree is None:
        return
    tab._refresh_facet_choices()
    for iid in tree.get_children():
        tree.delete(iid)
    for pattern in tab._visible_patterns():
        tag = normalize_color_tag(pattern.color_tag)
        tree.insert(
            "",
            "end",
            iid=pattern.key,
            values=(
                "Yes" if pattern.enabled else "No",
                pattern.severity.display_name,
                _COLOR_TAG_CELL_LABELS.get(tag, ""),
                pattern.category,
                pattern.label,
                pattern.pattern,
                "Built-in" if pattern.builtin else "Custom",
            ),
        )


def selected_patterns(tab) -> List[SherlockPattern]:
    """Staged patterns for the current selection, in visible/table order.

    Iterating tab._patterns reproduces the table render order (_refresh_table
    inserts in that order), so the result follows the visible row order
    regardless of Ctrl/Shift click sequence.
    """
    selection = set(tab._tree.selection())
    if not selection:
        return []
    return [p for p in tab._patterns if p.key in selection]


def replace_pattern(tab, key: str, new_pattern: SherlockPattern) -> None:
    tab._patterns = [
        new_pattern if p.key == key else p for p in tab._patterns
    ]


def on_mousewheel(tab, event: Any) -> str:
    delta = 0
    if getattr(event, "delta", 0):
        delta = -1 if event.delta > 0 else 1
    elif getattr(event, "num", None) == 4:
        delta = -1
    elif getattr(event, "num", None) == 5:
        delta = 1
    if delta:
        tab._tree.yview_scroll(delta, "units")
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
    patterns = tab._selected_patterns()
    if not patterns:
        tab._set_status("Select one or more patterns to enable or disable.")
        return
    keys = {p.key for p in patterns}
    tab._patterns = [
        _with_enabled(p, not p.enabled) if p.key in keys else p
        for p in tab._patterns
    ]
    tab._pattern_manager_dirty = True
    tab._refresh_table()
    visible = tab._visible_keys()
    tab._tree.selection_set([p.key for p in patterns if p.key in visible])


def on_delete(tab) -> None:
    patterns = tab._selected_patterns()
    if not patterns:
        tab._set_status("Select one or more patterns to delete.")
        return
    keys = {p.key for p in patterns}
    tab._patterns = [p for p in tab._patterns if p.key not in keys]
    tab._pattern_manager_dirty = True
    tab._refresh_table()


def on_restore_builtins(tab) -> None:
    """Re-enable all built-ins (clears any disabled state); customs untouched."""
    customs = [p for p in tab._patterns if not p.builtin]
    tab._patterns = builtin_patterns() + customs
    tab._pattern_manager_dirty = True
    tab._refresh_table()
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
    patterns = tab._selected_patterns()
    if len(patterns) != 1:
        tab._set_status("Select exactly one pattern to copy.")
        return
    tab._add_from_source(patterns[0])


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
    tab._refresh_table()
    if pattern.key in tab._visible_keys():
        tab._tree.selection_set(pattern.key)


def on_edit(tab) -> None:
    patterns = tab._selected_patterns()
    if len(patterns) != 1:
        tab._set_status("Select exactly one pattern to edit.")
        return
    pattern = patterns[0]
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
    tab._refresh_table()
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
    category_var = tk.StringVar(value=source.category if source else "Custom")
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
        values=category_choices(tab._patterns),
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
