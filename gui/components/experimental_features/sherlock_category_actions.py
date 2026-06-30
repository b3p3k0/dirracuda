"""
Sherlock Pattern Manager category-pane actions (C24).

C23 made the Pattern Manager's left pane a read-only category index. C24 adds the
three management actions for that pane - ``Add``, ``Copy``, ``Delete`` - plus the
small name-prompt modal they share and the left-pane action-button row.

This logic lives in its own satellite module (not in
``sherlock_pattern_manager.py``) to honour RISK_REGISTER R23: the pattern manager
module sits right at the 1200-line guardrail, so a card that touches it must extract
a helper before adding logic. ``sherlock_pattern_manager.py`` keeps only thin wiring
(the action-row call, the placeholder merge in the category list, the open-time
placeholder reset, and the Add/Edit dialog's placeholder-aware category choices);
everything else is here.

Categories remain labels derived from staged ``SherlockPattern`` rows. The only new
state is ``tab._placeholder_categories``: a UI-only list of empty category names a
user created with Add but has not yet put a value under. Placeholders show in the
left pane and in the Add/Edit category combobox, but never touch ``tab._patterns``,
export, the matcher, settings serialization, or the dirty flag. They are reset on
manager open, so they vanish on close/reopen unless a value was added under that name
(which makes it a real category via ``tab._patterns``).

Each function takes the owning SherlockTab as ``tab`` and drives its existing state
through ``tab._method(...)`` so instance monkeypatches still apply. ``_FACET_ALL``,
``_grab_when_viewable`` and ``_mb`` are reused from sherlock_pattern_manager (one-way
import: this module -> pattern manager; the pattern manager never imports this one).
"""

from __future__ import annotations

import uuid
import tkinter as tk
from typing import List, Optional

from gui.utils.dialog_helpers import ensure_dialog_focus
from shared.sherlock import (
    SherlockPattern,
    category_choices,
    group_patterns,
    normalize_color_tag,
)

from .sherlock_pattern_manager import _FACET_ALL, _grab_when_viewable, _mb


# ----------------------------------------------------------------------
# Category helpers (placeholder-aware)
# ----------------------------------------------------------------------


def _placeholder_categories(tab) -> List[str]:
    """The current UI-only placeholder category names (never None)."""
    return getattr(tab, "_placeholder_categories", None) or []


def category_names(tab) -> List[str]:
    """Merged, case-folded, sorted category list: staged rows + placeholders.

    Reuses ``category_choices`` (first-casing-seen wins, blanks skipped) so the
    left pane and the lookups here agree. ``always_include`` carries the
    placeholders so empty categories appear without forcing a "Custom" row.
    """
    return category_choices(
        tab._patterns, always_include=tuple(_placeholder_categories(tab))
    )


def find_existing_category(tab, name: str) -> Optional[str]:
    """Return the existing display-cased category matching *name*, else None."""
    needle = (name or "").strip().casefold()
    if not needle:
        return None
    for existing in category_names(tab):
        if existing.casefold() == needle:
            return existing
    return None


def dialog_category_default(tab, source) -> str:
    """Default category for the Add/Edit dialog.

    Edit/Copy keep the source's category; a fresh Add defaults to the active
    left-pane category (real or placeholder) so adding under a just-created
    category is one click, falling back to "Custom" when no category is selected.
    """
    if source is not None:
        return source.category
    active = getattr(tab, "_active_category", _FACET_ALL)
    return active if active != _FACET_ALL else "Custom"


def dialog_category_choices(tab) -> List[str]:
    """Category combobox values for the Add/Edit dialog.

    Always offers "Custom" plus every staged category and UI-only placeholder, so
    a value can be added under a placeholder (which then becomes a real category).
    """
    return category_choices(
        tab._patterns, always_include=("Custom",) + tuple(_placeholder_categories(tab))
    )


# ----------------------------------------------------------------------
# Left-pane action row
# ----------------------------------------------------------------------


def build_category_action_row(tab, parent: tk.Widget) -> None:
    """Build the [Add] [Copy] [Delete] button row under the category search box."""
    row = tk.Frame(parent)
    tab._theme.apply_to_widget(row, "main_window")
    row.pack(anchor="w", pady=(0, 8), fill=tk.X)

    add_btn = tk.Button(row, text="Add", command=tab._on_category_add)
    tab._theme.apply_to_widget(add_btn, "button_secondary")
    add_btn.pack(side=tk.LEFT, padx=(0, 4))

    copy_btn = tk.Button(row, text="Copy", command=tab._on_category_copy)
    tab._theme.apply_to_widget(copy_btn, "button_secondary")
    copy_btn.pack(side=tk.LEFT, padx=(0, 4))

    delete_btn = tk.Button(row, text="Delete", command=tab._on_category_delete)
    tab._theme.apply_to_widget(delete_btn, "button_danger")
    delete_btn.pack(side=tk.LEFT)


# ----------------------------------------------------------------------
# Name-prompt modal
# ----------------------------------------------------------------------


def prompt_category_name(
    tab, *, title: str, prompt: str, initial: str = ""
) -> Optional[str]:
    """Small modal asking for a category name. Returns the raw text, or None.

    Mirrors open_pattern_dialog's lifecycle: themed widgets, grab only once
    viewable, and the manager's modal grab re-asserted on close. The caller
    strips/validates the returned string; None means cancel/window-close.
    """
    parent = tab._active_dialog_parent()
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.resizable(False, False)
    dialog.transient(parent)
    tab._theme.apply_to_widget(dialog, "main_window")

    outer = tk.Frame(dialog, padx=14, pady=12)
    tab._theme.apply_to_widget(outer, "main_window")
    outer.pack(fill=tk.BOTH, expand=True)

    field_label = tk.Label(outer, text=prompt, anchor="w")
    tab._theme.apply_to_widget(field_label, "label")
    field_label.grid(row=0, column=0, sticky="w", pady=3)

    name_var = tk.StringVar(value=initial)
    entry = tk.Entry(outer, textvariable=name_var, width=28)
    tab._theme.apply_to_widget(entry, "entry")
    entry.grid(row=1, column=0, sticky="ew", pady=3)

    result = {}

    def _on_ok() -> None:
        result["value"] = name_var.get()
        dialog.destroy()

    btn_row = tk.Frame(outer)
    tab._theme.apply_to_widget(btn_row, "main_window")
    btn_row.grid(row=2, column=0, sticky="e", pady=(10, 0))

    ok_btn = tk.Button(btn_row, text="OK", command=_on_ok)
    tab._theme.apply_to_widget(ok_btn, "button_primary")
    ok_btn.pack(side=tk.RIGHT, padx=(6, 0))

    cancel_btn = tk.Button(btn_row, text="Cancel", command=dialog.destroy)
    tab._theme.apply_to_widget(cancel_btn, "button_secondary")
    cancel_btn.pack(side=tk.RIGHT)

    outer.grid_columnconfigure(0, weight=1)
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    _grab_when_viewable(dialog, parent)
    dialog.wait_window()

    # Restore the Pattern Manager's modality (the child grab stole it).
    mgr = getattr(tab, "_pattern_manager", None)
    if mgr is not None:
        try:
            if mgr.winfo_exists():
                mgr.grab_set()
                ensure_dialog_focus(mgr, tab.frame.winfo_toplevel())
        except Exception:
            pass

    return result.get("value")


# ----------------------------------------------------------------------
# Action handlers
# ----------------------------------------------------------------------


def on_category_add(tab) -> None:
    """Create a UI-only category placeholder (or select an existing match).

    Never mutates tab._patterns or the dirty flag: a placeholder only becomes a
    real category once a value is added under it. Refreshes both panes directly
    (not _refresh_after_mutation) so the new non-real active category survives.
    """
    raw = tab._prompt_category_name(title="Add Category", prompt="New category name:")
    if raw is None:
        return
    name = raw.strip()
    if not name:
        tab._set_status("Category name cannot be blank.")
        return
    existing = find_existing_category(tab, name)
    if existing is not None:
        tab._active_category = existing
        tab._refresh_category_list()
        tab._refresh_table()
        tab._set_status("Category '{0}' already exists.".format(existing))
        return
    placeholders = getattr(tab, "_placeholder_categories", None)
    if placeholders is None:
        placeholders = []
        tab._placeholder_categories = placeholders
    placeholders.append(name)
    tab._active_category = name
    tab._refresh_category_list()
    tab._refresh_table()
    tab._set_status("Added category '{0}'.".format(name))


def on_category_copy(tab) -> None:
    """Duplicate every pattern in the selected category as customs under a new name.

    Requires a single real category selected (not All Categories). Copies preserve
    label/pattern/severity/enabled/color_tag, force builtin=False, and get fresh
    custom keys. Marks dirty only when rows are actually copied.
    """
    source = getattr(tab, "_active_category", _FACET_ALL) or _FACET_ALL
    if source == _FACET_ALL:
        tab._set_status("Select a single category to copy (not All Categories).")
        return
    fold = source.casefold()
    members = [p for p in tab._patterns if (p.category or "").casefold() == fold]
    if not members:
        tab._set_status("Category '{0}' has no patterns to copy.".format(source))
        return
    raw = tab._prompt_category_name(
        title="Copy Category", prompt="Copy '{0}' to category:".format(source)
    )
    if raw is None:
        return
    dest = raw.strip()
    if not dest:
        tab._set_status("Category name cannot be blank.")
        return
    existing = find_existing_category(tab, dest)
    if existing is not None:
        confirm = _mb().askyesno(
            "Merge Categories",
            "Category '{0}' already exists. Copy {1} pattern(s) into it?".format(
                existing, len(members)
            ),
            parent=tab._active_dialog_parent(),
        )
        if not confirm:
            return
        dest = existing
    for member in members:
        tab._patterns.append(
            SherlockPattern(
                key="custom_{0}".format(uuid.uuid4().hex),
                category=dest,
                label=member.label,
                pattern=member.pattern,
                severity=member.severity,
                enabled=member.enabled,
                builtin=False,
                color_tag=normalize_color_tag(member.color_tag),
            )
        )
    tab._pattern_manager_dirty = True
    tab._active_category = dest
    tab._refresh_after_mutation()
    tab._set_status(
        "Copied {0} pattern(s) to '{1}'.".format(len(members), dest)
    )


def on_category_delete(tab) -> None:
    """Remove every staged pattern in the selected category after confirmation.

    Requires a single real category selected (not All Categories). Built-ins drop
    out of the catalog (staged-deleted via existing absence semantics); customs are
    removed from staged settings. Marks dirty only when rows are actually removed.
    An empty placeholder is just dropped from the UI list (no confirm, no dirty).
    """
    target = getattr(tab, "_active_category", _FACET_ALL) or _FACET_ALL
    if target == _FACET_ALL:
        tab._set_status("Select a single category to delete (not All Categories).")
        return
    fold = target.casefold()
    members = [p for p in tab._patterns if (p.category or "").casefold() == fold]
    placeholders = _placeholder_categories(tab)
    is_placeholder = any(ph.casefold() == fold for ph in placeholders)

    if not members:
        if is_placeholder:
            tab._placeholder_categories = [
                ph for ph in placeholders if ph.casefold() != fold
            ]
            tab._refresh_after_mutation()
            tab._set_status("Removed empty category '{0}'.".format(target))
        else:
            tab._set_status("No patterns in category '{0}'.".format(target))
        return

    groups = [
        g for g in group_patterns(tab._patterns) if g.category.casefold() == fold
    ]
    confirm = _mb().askyesno(
        "Delete Category",
        "Delete category '{0}'? This removes {1} value group(s) and "
        "{2} pattern(s).".format(target, len(groups), len(members)),
        parent=tab._active_dialog_parent(),
    )
    if not confirm:
        return
    keys = {p.key for p in members}
    tab._patterns = [p for p in tab._patterns if p.key not in keys]
    if is_placeholder:
        tab._placeholder_categories = [
            ph for ph in placeholders if ph.casefold() != fold
        ]
    tab._pattern_manager_dirty = True
    tab._refresh_after_mutation()
    tab._set_status("Deleted category '{0}'.".format(target))
