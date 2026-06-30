"""
Sherlock Pattern Manager value-row actions (C25).

C23 made the Pattern Manager's right pane a grouped value-row view; C24 extracted
the left-pane category actions into their own satellite. C25 turns each value row
into a first-class grouped entity - comma-separated Add/Edit, in-place multi-member
custom edit, prefilled Copy, and bulk color-tag assignment - so the value-row action
handlers move here, mirroring the C24 extraction.

This logic lives in its own satellite module (not in ``sherlock_pattern_manager.py``)
to honour RISK_REGISTER R23: the pattern manager module sits right at the 1200-line
guardrail, so a card that adds logic must extract first. ``sherlock_pattern_manager``
keeps only rendering, the Add/Edit dialog, the filters, and the open-time wiring;
every value-row mutation lives here.

The stored schema and wire format are unchanged: one ``SherlockPattern`` per pattern
string, always. Comma input and grouping are display/parse conveniences built on the
pure C22 helpers (``split_pattern_input``, ``build_group_patterns``, ``reuse_keys``).

Built-in tag policy: ``settings_to_dict`` persists built-ins as only their disabled /
deleted keys, so a ``color_tag`` set on a built-in row in memory is silently lost on
Save/reload. ``on_apply_tag`` therefore never mutates built-in rows - it tags custom
members only and reports any skipped built-ins. Tagging a built-in stays possible the
deliberate way: Copy/Edit it into a custom first (edit-as-copy), then tag.

Each function takes the owning SherlockTab as ``tab`` and drives its existing state
through ``tab._method(...)`` so instance monkeypatches still apply. ``_FACET_ALL`` is
unused here; helpers ``_grab_when_viewable``/``_mb`` and the dialog/label maps are
reused from sherlock_pattern_manager (one-way import: this module -> pattern manager;
the pattern manager never imports this one).
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime
from tkinter import filedialog
from typing import Any, Dict, List, Optional, Sequence

from shared.sherlock.export import build_export_payload
from shared.sherlock.grouping import (
    build_group_patterns,
    reuse_keys,
    split_pattern_input,
)
from shared.sherlock import (
    COLOR_TAG_NONE,
    SherlockPattern,
    builtin_patterns,
    normalize_color_tag,
)

from .sherlock_pattern_manager import (
    _DIALOG_LABEL_TO_TAG,
    _group_iid,
    _mb,
    _with_enabled,
)


def _mint_key() -> str:
    """Mint a fresh, catalog-unique custom key (runtime concern, kept out of C22)."""
    return "custom_{0}".format(uuid.uuid4().hex)


def _result_patterns(result: Dict[str, Any]) -> List[str]:
    """Token list from an Add/Edit dialog result.

    Prefers the C25 ``patterns`` list emitted by the live dialog; falls back to
    splitting a legacy single ``pattern`` string so older callers/tests that
    return one pattern still produce one row.
    """
    patterns = result.get("patterns")
    if patterns:
        return list(patterns)
    return split_pattern_input(result.get("pattern", ""))


def _replace_group_members(
    tab, old_keys: Sequence[str], new_rows: Sequence[SherlockPattern]
) -> None:
    """Splice *new_rows* in where the first old member sat, dropping the rest.

    Keeps the edited value row in its original position (first-occurrence order
    drives both grouping and rendering) instead of moving it to the end.
    """
    old = set(old_keys)
    result: List[SherlockPattern] = []
    inserted = False
    for pattern in tab._patterns:
        if pattern.key in old:
            if not inserted:
                result.extend(new_rows)
                inserted = True
            continue
        result.append(pattern)
    if not inserted:
        result.extend(new_rows)
    tab._patterns = result


# ----------------------------------------------------------------------
# Enable/Disable, Delete, Restore, Export
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


# ----------------------------------------------------------------------
# Add / Copy / Edit (grouped, comma-separated)
# ----------------------------------------------------------------------


def on_add(tab) -> None:
    result = tab._open_pattern_dialog()
    if result is None:
        return
    tab._append_custom_from_result(result)


def on_copy(tab) -> None:
    """Copy exactly one selected visible value group as new custom rows.

    Always opens the prefilled Add dialog - single- and multi-member alike - so a
    grouped value is never silently duplicated. Saving mints fresh custom keys.
    """
    groups = tab._selected_groups()
    if len(groups) != 1:
        tab._set_status("Select exactly one pattern to copy.")
        return
    group = groups[0]
    tab._add_from_source(group.members[0], patterns=list(group.patterns))


def add_from_source(
    tab, source: SherlockPattern, *, patterns: Optional[Sequence[str]] = None
) -> None:
    """Open the Add dialog prefilled from *source*; save as new custom rows.

    *patterns* seeds the comma-separated Patterns field for a multi-member group;
    when omitted the field falls back to ``source.pattern``.
    """
    result = tab._open_pattern_dialog(prefill=source, prefill_patterns=patterns)
    if result is None:
        return
    tab._append_custom_from_result(result)


def append_custom_from_result(tab, result: Dict[str, Any]) -> None:
    """Append one custom SherlockPattern per dialog token, with fresh keys."""
    tokens = _result_patterns(result)
    if not tokens:
        return
    new_rows = build_group_patterns(
        category=result["category"],
        label=result["label"],
        severity=result["severity"],
        enabled=result["enabled"],
        builtin=False,
        color_tag=result.get("color_tag", COLOR_TAG_NONE),
        patterns=tokens,
        key_for=lambda _pattern: _mint_key(),
    )
    tab._patterns.extend(new_rows)
    tab._pattern_manager_dirty = True
    tab._refresh_after_mutation()
    if new_rows and new_rows[0].key in tab._visible_keys():
        tab._tree.selection_set(new_rows[0].key)


def on_edit(tab) -> None:
    """Edit exactly one selected value group.

    Built-in groups follow C15 edit-as-copy (built-ins are never mutated in
    place). Custom groups - single or multi-member - edit in place from the
    comma-separated Patterns field: unchanged tokens keep their keys (reuse_keys)
    and new tokens mint fresh custom keys, so serialize's custom keys don't churn.
    """
    groups = tab._selected_groups()
    if len(groups) != 1:
        tab._set_status("Select exactly one pattern to edit.")
        return
    group = groups[0]
    if group.builtin:
        tab._add_from_source(group.members[0], patterns=list(group.patterns))
        return
    result = tab._open_pattern_dialog(
        existing=group.members[0], prefill_patterns=list(group.patterns)
    )
    if result is None:
        return
    tokens = _result_patterns(result)
    if not tokens:
        return
    key_for = reuse_keys(group.members, _mint_key)
    new_rows = build_group_patterns(
        category=result["category"],
        label=result["label"],
        severity=result["severity"],
        enabled=result["enabled"],
        builtin=False,
        color_tag=result.get("color_tag", COLOR_TAG_NONE),
        patterns=tokens,
        key_for=key_for,
    )
    _replace_group_members(tab, [m.key for m in group.members], new_rows)
    tab._pattern_manager_dirty = True
    tab._refresh_after_mutation()
    if new_rows and new_rows[0].key in tab._visible_keys():
        tab._tree.selection_set(new_rows[0].key)


# ----------------------------------------------------------------------
# Bulk color-tag assignment
# ----------------------------------------------------------------------


def on_apply_tag(tab) -> None:
    """Assign the chosen color tag to every custom member of selected groups.

    Built-in members are skipped (their color_tag can't survive persistence) and
    reported. Dirties only when at least one custom row actually changes; refreshes
    both panes and restores visible selection best-effort (color_tag is part of the
    group key, so retagged rows re-group).
    """
    groups = tab._selected_groups()
    if not groups:
        tab._set_status("Select one or more patterns to tag.")
        return
    target = normalize_color_tag(
        _DIALOG_LABEL_TO_TAG.get(tab._value_tag_var.get(), COLOR_TAG_NONE)
    )
    selected_keys = {m.key for g in groups for m in g.members if not m.builtin}
    skipped_builtins = sum(
        1 for g in groups for m in g.members if m.builtin
    )

    changed = 0
    new_list: List[SherlockPattern] = []
    for pattern in tab._patterns:
        if (
            pattern.key in selected_keys
            and normalize_color_tag(pattern.color_tag) != target
        ):
            new_list.append(dataclasses.replace(pattern, color_tag=target))
            changed += 1
        else:
            new_list.append(pattern)

    if not changed:
        if skipped_builtins:
            tab._set_status(
                "Built-ins can't be tagged - copy to a custom first."
            )
        else:
            tab._set_status("Tag unchanged.")
        return

    tab._patterns = new_list
    tab._pattern_manager_dirty = True
    tab._refresh_after_mutation()
    visible = tab._visible_keys()
    tab._tree.selection_set(
        [_group_iid(g) for g in groups if _group_iid(g) in visible]
    )
    message = "Tagged {0} pattern(s)".format(changed)
    if skipped_builtins:
        message += "; skipped {0} built-in(s) (copy to a custom to tag).".format(
            skipped_builtins
        )
    else:
        message += "."
    tab._set_status(message)
