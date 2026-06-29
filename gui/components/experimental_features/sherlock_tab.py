"""
Sherlock tab for the Accessories (Experimental Features) dialog.

C3: settings surface for the display-only Sherlock exposure-triage layer. Users
edit severity colors, toggle scan options, and manage the built-in/custom
pattern catalog. This tab only reads/writes Sherlock *settings* via
settings_manager (top-level "sherlock" config-store module). It never matches,
writes Sherlock results, downloads files, reads file contents, authenticates, or
triggers probing - those belong to later cards.

C10: adds User1/User2/User3 color inputs to the main tab and moves pattern
management into a tall modal "Sherlock Patterns" dialog. Pattern edits stay
staged in memory until the main tab's Save persists settings.

C14: the severity/User color rows use clickable color swatches (opening Tk's
native colorchooser) instead of visible hex entries; User rows show "None" when
empty and gain a Clear control. The underlying hex StringVars and sherlock.json
wire format are unchanged.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
import tkinter as tk
from datetime import datetime
from tkinter import ttk, filedialog
from typing import Any, Dict, List, Optional, Tuple

from gui.utils import safe_messagebox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import get_theme
from shared.sherlock.export import build_export_payload
from shared.sherlock import (
    COLOR_TAG_NONE,
    SHERLOCK_SETTINGS_KEY,
    USER_COLOR_KEYS,
    Severity,
    SherlockPattern,
    SherlockSettings,
    builtin_patterns,
    category_choices,
    default_settings,
    is_valid_color,
    normalize_color_tag,
    settings_from_dict,
    settings_to_dict,
    validate_color,
    validate_user_color,
)
from shared.sherlock.serialize import severity_from_str, severity_to_str

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


class SherlockTab:
    """Content widget for the Sherlock experimental feature tab."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
        # Background for empty/invalid swatch faces (a real color string).
        self._neutral_bg = self._theme.colors["secondary_bg"]
        self.frame = tk.Frame(parent)
        self._theme.apply_to_widget(self.frame, "main_window")

        settings = self._load_settings()
        # Working state: options + colors live in Tk vars; patterns in a list.
        self._patterns: List[SherlockPattern] = list(settings.patterns)
        self._ignore_case_var = tk.BooleanVar(value=bool(settings.ignore_case))
        self._run_after_probe_var = tk.BooleanVar(value=bool(settings.run_after_probe))
        self._color_vars: Dict[Severity, tk.StringVar] = {
            sev: tk.StringVar(value=settings.color_for(sev)) for sev in _SEVERITY_ORDER
        }
        self._user_color_vars: Dict[str, tk.StringVar] = {
            key: tk.StringVar(value=settings.user_colors.get(key, ""))
            for key in USER_COLOR_KEYS
        }
        # Swatch buttons, kept so pick/clear handlers can repaint them.
        self._severity_swatches: Dict[Severity, tk.Button] = {}
        self._user_swatches: Dict[str, tk.Button] = {}
        # Pattern table + manager dialog are built lazily on demand.
        self._tree: Optional[ttk.Treeview] = None
        self._pattern_manager: Optional[tk.Toplevel] = None
        # True when pattern-manager edits have not been persisted to disk;
        # cleared only by a successful save (never auto-reset on manager open).
        self._pattern_manager_dirty: bool = False
        self._build(self.frame)

    # ------------------------------------------------------------------
    # Settings load/save
    # ------------------------------------------------------------------

    def _load_settings(self) -> SherlockSettings:
        """Load persisted Sherlock settings, defaulting safely."""
        sm = self._context.get("settings_manager")
        if sm is None:
            return default_settings()
        try:
            raw = sm.get_setting(SHERLOCK_SETTINGS_KEY, {})
        except Exception:
            return default_settings()
        return settings_from_dict(raw)

    def _collect_colors(self) -> Optional[Dict[Severity, str]]:
        """Validate severity color entries; on first invalid show error, None."""
        colors: Dict[Severity, str] = {}
        for sev in _SEVERITY_ORDER:
            raw = self._color_vars[sev].get().strip()
            try:
                colors[sev] = validate_color(raw)
            except ValueError:
                safe_messagebox.showerror(
                    "Invalid Color",
                    "{0} color {1!r} is not a valid #RRGGBB value.".format(
                        _SEVERITY_LABELS[sev], raw
                    ),
                    parent=self.frame.winfo_toplevel(),
                )
                return None
        return colors

    def _collect_user_colors(self) -> Optional[Dict[str, str]]:
        """Validate user color entries; empty is allowed, else #RRGGBB.

        On the first invalid non-empty value, show an error and return None so
        save aborts before any write.
        """
        user_colors: Dict[str, str] = {}
        for key in USER_COLOR_KEYS:
            raw = self._user_color_vars[key].get().strip()
            try:
                user_colors[key] = validate_user_color(raw)
            except ValueError:
                safe_messagebox.showerror(
                    "Invalid Color",
                    "{0} color {1!r} must be empty or a #RRGGBB value.".format(
                        _USER_COLOR_LABELS[key], raw
                    ),
                    parent=self.frame.winfo_toplevel(),
                )
                return None
        return user_colors

    def _on_save(self) -> bool:
        """Validate and persist the current settings. Return True on success.

        A successful write clears _pattern_manager_dirty (the same shard carries
        self._patterns), so both the main-tab Save and the manager's Save & Close
        mark pattern edits as persisted.
        """
        colors = self._collect_colors()
        if colors is None:
            return False
        user_colors = self._collect_user_colors()
        if user_colors is None:
            return False

        settings = SherlockSettings(
            ignore_case=bool(self._ignore_case_var.get()),
            run_after_probe=bool(self._run_after_probe_var.get()),
            colors=colors,
            user_colors=user_colors,
            patterns=list(self._patterns),
        )
        data = settings_to_dict(settings)

        sm = self._context.get("settings_manager")
        if sm is None:
            self._set_status("Settings unavailable; not saved.")
            return False
        try:
            ok = sm.set_setting(SHERLOCK_SETTINGS_KEY, data)
        except Exception as exc:  # pragma: no cover - defensive
            self._set_status("Save failed: {0}".format(exc))
            return False
        if ok:
            self._pattern_manager_dirty = False
            self._set_status("Saved.")
            return True
        self._set_status("Save failed; settings were not written.")
        return False

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build(self, frame: tk.Frame) -> None:
        desc = tk.Label(
            frame,
            text="Snapshot-path highlights only; no downloads or content reads.",
            justify="left",
            anchor="w",
            wraplength=520,
        )
        self._theme.apply_to_widget(desc, "label")
        desc.pack(anchor="w", padx=12, pady=(8, 4))

        # Options row
        opt_row = tk.Frame(frame)
        self._theme.apply_to_widget(opt_row, "main_window")
        opt_row.pack(anchor="w", padx=12, pady=(0, 6), fill=tk.X)

        ignore_cb = tk.Checkbutton(
            opt_row, text="Ignore case", variable=self._ignore_case_var
        )
        self._theme.apply_to_widget(ignore_cb, "checkbox")
        ignore_cb.pack(side=tk.LEFT)

        probe_cb = tk.Checkbutton(
            opt_row, text="Run after probe", variable=self._run_after_probe_var
        )
        self._theme.apply_to_widget(probe_cb, "checkbox")
        probe_cb.pack(side=tk.LEFT, padx=(16, 0))

        # Severity colors
        self._build_caption(frame, "Severity colors")
        color_row = tk.Frame(frame)
        self._theme.apply_to_widget(color_row, "main_window")
        color_row.pack(anchor="w", padx=12, pady=(0, 6), fill=tk.X)
        for sev in _SEVERITY_ORDER:
            self._severity_swatches[sev] = self._build_color_input(
                color_row,
                "{0}:".format(_SEVERITY_LABELS[sev]),
                self._color_vars[sev],
                is_user=False,
                on_pick=lambda s=sev: self._pick_color(s),
            )

        # User colors
        self._build_caption(frame, "User colors")
        user_row = tk.Frame(frame)
        self._theme.apply_to_widget(user_row, "main_window")
        user_row.pack(anchor="w", padx=12, pady=(0, 6), fill=tk.X)
        for key in USER_COLOR_KEYS:
            var = self._user_color_vars[key]
            self._user_swatches[key] = self._build_color_input(
                user_row,
                "{0}:".format(_USER_COLOR_LABELS[key]),
                var,
                is_user=True,
                on_pick=lambda k=key: self._pick_user_color(k),
                on_clear=lambda k=key: self._clear_user_color(k),
            )

        # Patterns
        self._build_caption(frame, "Patterns")
        pattern_row = tk.Frame(frame)
        self._theme.apply_to_widget(pattern_row, "main_window")
        pattern_row.pack(anchor="w", padx=12, pady=(0, 6), fill=tk.X)
        manage_btn = tk.Button(
            pattern_row, text="Manage Patterns...", command=self._open_pattern_manager
        )
        self._theme.apply_to_widget(manage_btn, "button_secondary")
        manage_btn.pack(side=tk.LEFT)

        # Footer: Save + status
        footer = tk.Frame(frame)
        self._theme.apply_to_widget(footer, "main_window")
        footer.pack(anchor="w", padx=12, pady=(4, 10), fill=tk.X)

        save_btn = tk.Button(footer, text="Save", command=self._on_save)
        self._theme.apply_to_widget(save_btn, "button_primary")
        save_btn.pack(side=tk.LEFT)

        self._status_label = tk.Label(footer, text="", anchor="w")
        self._theme.apply_to_widget(self._status_label, "label")
        self._status_label.pack(side=tk.LEFT, padx=(10, 0))

    def _build_caption(self, parent: tk.Widget, text: str) -> None:
        caption = tk.Label(parent, text=text, anchor="w")
        self._theme.apply_to_widget(caption, "label")
        caption.pack(anchor="w", padx=12, pady=(2, 1))

    def _build_color_input(
        self,
        parent: tk.Widget,
        label_text: str,
        var: tk.StringVar,
        *,
        is_user: bool,
        on_pick,
        on_clear=None,
    ) -> tk.Button:
        """Build a label + clickable color swatch (+ Clear for user rows).

        Returns the swatch button so callers can repaint it after a pick/clear.
        """
        group = tk.Frame(parent)
        self._theme.apply_to_widget(group, "main_window")
        group.pack(side=tk.LEFT, padx=(0, 14))

        label = tk.Label(group, text=label_text)
        self._theme.apply_to_widget(label, "label")
        label.pack(side=tk.LEFT)

        swatch = tk.Button(group, width=4, height=1, relief="solid", bd=1, command=on_pick)
        self._theme.apply_to_widget(swatch, "button_secondary")
        swatch.pack(side=tk.LEFT, padx=(4, 2))
        self._render_swatch(swatch, var.get(), is_user)

        if on_clear is not None:
            clear = tk.Button(group, text="Clear", width=5, command=on_clear)
            self._theme.apply_to_widget(clear, "button_secondary")
            clear.pack(side=tk.LEFT)

        return swatch

    def _swatch_face(self, value: object, *, is_user: bool) -> Tuple[str, str]:
        """Map a stored color string to a (background, caption) swatch face.

        Valid colors show the color with no caption. Empty user colors show
        'None'. Any other (invalid internal) value renders defensively as the
        neutral background with a '?' caption - never the raw string - so no
        stray hex text leaks into the color rows. Save still rejects invalid
        values via the unchanged validators.
        """
        text = (value or "").strip() if isinstance(value, str) else ""
        if is_valid_color(text):
            return text.lower(), ""
        if is_user and text == "":
            return self._neutral_bg, "None"
        return self._neutral_bg, "?"

    def _render_swatch(self, swatch: tk.Button, value: object, is_user: bool) -> None:
        """Repaint a swatch button to reflect *value*."""
        bg, caption = self._swatch_face(value, is_user=is_user)
        swatch.configure(bg=bg, activebackground=bg, text=caption)

    def _build_filter_row(self, parent: tk.Widget) -> None:
        """Build the C18 filter row above the pattern table.

        Display-only: edits here re-render the visible rows but never touch
        self._patterns or the dirty flag. Created fresh on every manager open.
        """
        self._filter_search_var = tk.StringVar(value="")
        self._filter_category_var = tk.StringVar(value=_FACET_ALL)
        self._filter_severity_var = tk.StringVar(value=_FACET_ALL)
        self._filter_user_tag_var = tk.StringVar(value=_FACET_ALL)
        self._filter_enabled_var = tk.StringVar(value=_FACET_ALL)

        row = tk.Frame(parent)
        self._theme.apply_to_widget(row, "main_window")
        row.pack(anchor="w", pady=(0, 8), fill=tk.X)

        def _label(text: str) -> None:
            lbl = tk.Label(row, text=text, anchor="w")
            self._theme.apply_to_widget(lbl, "label")
            lbl.pack(side=tk.LEFT, padx=(0, 3))

        def _facet(var: tk.StringVar, values, width: int) -> ttk.Combobox:
            combo = ttk.Combobox(
                row, textvariable=var, values=list(values), state="readonly", width=width
            )
            combo.pack(side=tk.LEFT, padx=(0, 10))
            combo.bind("<<ComboboxSelected>>", self._on_filter_change)
            return combo

        _label("Search:")
        search_entry = tk.Entry(row, textvariable=self._filter_search_var, width=18)
        self._theme.apply_to_widget(search_entry, "entry")
        search_entry.pack(side=tk.LEFT, padx=(0, 10))
        self._filter_search_var.trace_add("write", self._on_filter_change)

        _label("Category:")
        self._filter_category_combo = _facet(self._filter_category_var, [_FACET_ALL], 14)
        _label("Severity:")
        _facet(self._filter_severity_var, _SEVERITY_FACET_CHOICES, 7)
        _label("User Tag:")
        self._filter_user_tag_combo = _facet(self._filter_user_tag_var, [_FACET_ALL], 8)
        _label("Enabled:")
        _facet(self._filter_enabled_var, _ENABLED_FACET_CHOICES, 9)

        clear_btn = tk.Button(row, text="Clear", command=self._on_clear_filters)
        self._theme.apply_to_widget(clear_btn, "button_secondary")
        clear_btn.pack(side=tk.LEFT)

    def _on_filter_change(self, *_args: Any) -> None:
        """Re-render the visible rows; clear selection so hidden rows can't be acted on."""
        if getattr(self, "_applying_filter_reset", False):
            return
        tree = self._tree
        if tree is None:
            return
        selection = tree.selection()
        if selection:
            tree.selection_remove(*selection)
        self._refresh_table()

    def _on_clear_filters(self) -> None:
        """Reset every facet/search field to its default and re-render once."""
        self._applying_filter_reset = True
        try:
            self._filter_search_var.set("")
            self._filter_category_var.set(_FACET_ALL)
            self._filter_severity_var.set(_FACET_ALL)
            self._filter_user_tag_var.set(_FACET_ALL)
            self._filter_enabled_var.set(_FACET_ALL)
        finally:
            self._applying_filter_reset = False
        self._on_filter_change()

    def _build_table(self, parent: tk.Widget) -> None:
        table_frame = tk.Frame(parent)
        self._theme.apply_to_widget(table_frame, "main_window")
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
            tree.bind(sequence, self._on_mousewheel)
        tree.bind("<Double-1>", self._on_row_double_click)

        self._tree = tree

    # ------------------------------------------------------------------
    # Pattern manager dialog
    # ------------------------------------------------------------------

    def _open_pattern_manager(self) -> None:
        """Open the tall modal Sherlock Patterns dialog (staged edits)."""
        parent = self.frame.winfo_toplevel()
        dialog = tk.Toplevel(parent)
        dialog.title("Sherlock Patterns")
        dialog.transient(parent)
        self._theme.apply_to_widget(dialog, "main_window")
        dialog.geometry("1100x600")
        dialog.minsize(1080, 560)

        outer = tk.Frame(dialog, padx=12, pady=10)
        self._theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True)

        self._build_filter_row(outer)
        self._build_table(outer)

        action_row = tk.Frame(outer)
        self._theme.apply_to_widget(action_row, "main_window")
        action_row.pack(anchor="w", pady=(8, 0), fill=tk.X)

        add_btn = tk.Button(action_row, text="Add", command=self._on_add)
        self._theme.apply_to_widget(add_btn, "button_secondary")
        add_btn.pack(side=tk.LEFT, padx=(0, 6))

        edit_btn = tk.Button(action_row, text="Edit", command=self._on_edit)
        self._theme.apply_to_widget(edit_btn, "button_secondary")
        edit_btn.pack(side=tk.LEFT, padx=(0, 6))

        toggle_btn = tk.Button(
            action_row, text="Enable/Disable", command=self._on_toggle
        )
        self._theme.apply_to_widget(toggle_btn, "button_secondary")
        toggle_btn.pack(side=tk.LEFT, padx=(0, 6))

        delete_btn = tk.Button(action_row, text="Delete", command=self._on_delete)
        self._theme.apply_to_widget(delete_btn, "button_danger")
        delete_btn.pack(side=tk.LEFT, padx=(0, 6))

        copy_btn = tk.Button(action_row, text="Copy", command=self._on_copy)
        self._theme.apply_to_widget(copy_btn, "button_secondary")
        copy_btn.pack(side=tk.LEFT, padx=(0, 6))

        restore_btn = tk.Button(
            action_row, text="Restore Built-ins", command=self._on_restore_builtins
        )
        self._theme.apply_to_widget(restore_btn, "button_secondary")
        restore_btn.pack(side=tk.LEFT, padx=(0, 6))

        export_btn = tk.Button(action_row, text="Export", command=self._on_export)
        self._theme.apply_to_widget(export_btn, "button_secondary")
        export_btn.pack(side=tk.LEFT, padx=(0, 6))

        savec_btn = tk.Button(
            action_row, text="Save & Close", command=self._save_and_close_manager
        )
        self._theme.apply_to_widget(savec_btn, "button_primary")
        savec_btn.pack(side=tk.LEFT, padx=(0, 6))

        close_btn = tk.Button(action_row, text="Close", command=self._close_manager)
        self._theme.apply_to_widget(close_btn, "button_secondary")
        close_btn.pack(side=tk.LEFT, padx=(0, 6))

        self._pattern_manager = dialog
        self._refresh_table()
        try:
            self._tree.focus_set()
        except Exception:
            pass

        dialog.protocol("WM_DELETE_WINDOW", self._close_manager)
        dialog.grab_set()
        ensure_dialog_focus(dialog, parent)
        dialog.wait_window()

    def _teardown_manager(self) -> None:
        """Clear manager references and destroy the dialog."""
        dialog = self._pattern_manager
        self._pattern_manager = None
        self._tree = None
        self._filter_category_combo = None
        self._filter_user_tag_combo = None
        if dialog is not None:
            dialog.destroy()

    def _close_manager(self) -> None:
        """Close button / window close: warn first if there are staged edits."""
        if self._pattern_manager_dirty:
            confirm = safe_messagebox.askyesno(
                "Unsaved Pattern Changes",
                "You have unsaved pattern changes. Close without saving?",
                parent=self._pattern_manager,
            )
            if not confirm:
                return
        self._teardown_manager()

    def _save_and_close_manager(self) -> None:
        """Run the standard save/validation path; close only when it succeeds."""
        if self._on_save():  # success clears _pattern_manager_dirty
            self._teardown_manager()

    # ------------------------------------------------------------------
    # Table rendering / selection
    # ------------------------------------------------------------------

    def _current_filter_state(self) -> Tuple[str, str, str, str, str]:
        """Read the filter row vars, defaulting to no-filter when absent.

        Returns (search, category, severity, user_tag, enabled). When the filter
        widgets have not been built (e.g. unit tests that mock the tree), every
        facet reads as `_FACET_ALL` so the whole catalog stays visible.
        """
        def _get(name: str, default: str) -> str:
            var = getattr(self, name, None)
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

    def _visible_patterns(self) -> List[SherlockPattern]:
        """Staged patterns surviving the current filter row (never mutates state)."""
        search, category, severity, user_tag, enabled = self._current_filter_state()
        return [
            p
            for p in self._patterns
            if _pattern_matches_filters(
                p,
                search=search,
                category=category,
                severity=severity,
                user_tag=user_tag,
                enabled=enabled,
            )
        ]

    def _visible_keys(self) -> set:
        """Keys of currently-visible rows; used to keep selection on visible rows."""
        return {p.key for p in self._visible_patterns()}

    def _refresh_facet_choices(self) -> None:
        """Rebuild the dynamic Category / User Tag facet value lists.

        Reset a facet to `_FACET_ALL` when its selected value is no longer
        present among staged rows. No-op when the combos have not been built.
        """
        cat_combo = getattr(self, "_filter_category_combo", None)
        if cat_combo is not None:
            try:
                exists = cat_combo.winfo_exists()
            except Exception:
                exists = False
            if exists:
                choices = [_FACET_ALL] + category_choices(
                    self._patterns, always_include=()
                )
                cat_combo["values"] = choices
                if self._filter_category_var.get() not in choices:
                    self._filter_category_var.set(_FACET_ALL)

        tag_combo = getattr(self, "_filter_user_tag_combo", None)
        if tag_combo is not None:
            try:
                exists = tag_combo.winfo_exists()
            except Exception:
                exists = False
            if exists:
                choices = [_FACET_ALL] + _user_tag_facet_choices(self._patterns)
                tag_combo["values"] = choices
                if self._filter_user_tag_var.get() not in choices:
                    self._filter_user_tag_var.set(_FACET_ALL)

    def _refresh_table(self) -> None:
        tree = self._tree
        if tree is None:
            return
        self._refresh_facet_choices()
        for iid in tree.get_children():
            tree.delete(iid)
        for pattern in self._visible_patterns():
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

    def _selected_patterns(self) -> List[SherlockPattern]:
        """Staged patterns for the current selection, in visible/table order.

        Iterating self._patterns reproduces the table render order (_refresh_table
        inserts in that order), so the result follows the visible row order
        regardless of Ctrl/Shift click sequence.
        """
        selection = set(self._tree.selection())
        if not selection:
            return []
        return [p for p in self._patterns if p.key in selection]

    def _replace_pattern(self, key: str, new_pattern: SherlockPattern) -> None:
        self._patterns = [
            new_pattern if p.key == key else p for p in self._patterns
        ]

    def _on_mousewheel(self, event: Any) -> str:
        delta = 0
        if getattr(event, "delta", 0):
            delta = -1 if event.delta > 0 else 1
        elif getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        if delta:
            self._tree.yview_scroll(delta, "units")
        return "break"

    def _on_row_double_click(self, event: Any) -> str:
        """Double-click a row to edit exactly that row.

        Selects the clicked row first, then routes through _on_edit so the C15
        built-in lifecycle holds (built-ins edit-as-copy, customs edit in place).
        A double-click on empty space changes nothing.

        The edit is deferred with after_idle so dialog creation runs after this
        click event finishes; the Add/Edit dialog also waits until it is
        viewable before taking a modal grab.
        """
        tree = self._tree
        if tree is None:
            return "break"
        row = tree.identify_row(event.y)
        if not row:
            return "break"
        tree.selection_set(row)
        tree.after_idle(self._on_edit)
        return "break"

    def _set_status(self, text: str) -> None:
        self._status_label.configure(text=text)

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _on_toggle(self) -> None:
        patterns = self._selected_patterns()
        if not patterns:
            self._set_status("Select one or more patterns to enable or disable.")
            return
        keys = {p.key for p in patterns}
        self._patterns = [
            _with_enabled(p, not p.enabled) if p.key in keys else p
            for p in self._patterns
        ]
        self._pattern_manager_dirty = True
        self._refresh_table()
        visible = self._visible_keys()
        self._tree.selection_set([p.key for p in patterns if p.key in visible])

    def _on_delete(self) -> None:
        patterns = self._selected_patterns()
        if not patterns:
            self._set_status("Select one or more patterns to delete.")
            return
        keys = {p.key for p in patterns}
        self._patterns = [p for p in self._patterns if p.key not in keys]
        self._pattern_manager_dirty = True
        self._refresh_table()

    def _on_restore_builtins(self) -> None:
        """Re-enable all built-ins (clears any disabled state); customs untouched."""
        customs = [p for p in self._patterns if not p.builtin]
        self._patterns = builtin_patterns() + customs
        self._pattern_manager_dirty = True
        self._refresh_table()
        self._set_status("Built-ins restored.")

    def _on_export(self) -> None:
        """Write the full staged pattern list to a user-chosen JSON file.

        Read-only: never mutates _patterns, filter vars, selection, the dirty
        flag, settings, or persistence. Cancel is silent; write errors report via
        safe_messagebox; success only updates the status line (manager stays open).
        """
        now = datetime.now()
        default_name = f"sherlock_patterns_{now.strftime('%Y%m%d_%H%M%S')}.json"
        path = filedialog.asksaveasfilename(
            parent=self._pattern_manager,
            title="Export Sherlock Patterns",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile=default_name,
        )
        if not path:
            return
        payload = build_export_payload(
            self._patterns, exported_at=now.isoformat(timespec="seconds")
        )
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
        except OSError as exc:
            safe_messagebox.showerror(
                "Export Failed",
                "Could not write pattern export:\n{0}".format(exc),
                parent=self._active_dialog_parent(),
            )
            return
        self._set_status(
            "Exported {0} patterns to {1}.".format(payload["count"], path)
        )

    def _on_add(self) -> None:
        result = self._open_pattern_dialog()
        if result is None:
            return
        self._append_custom_from_result(result)

    def _on_copy(self) -> None:
        patterns = self._selected_patterns()
        if len(patterns) != 1:
            self._set_status("Select exactly one pattern to copy.")
            return
        self._add_from_source(patterns[0])

    def _add_from_source(self, source: SherlockPattern) -> None:
        """Open the Add dialog prefilled from *source*; save as a new custom."""
        result = self._open_pattern_dialog(prefill=source)
        if result is None:
            return
        self._append_custom_from_result(result)

    def _append_custom_from_result(self, result: Dict[str, Any]) -> None:
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
        self._patterns.append(pattern)
        self._pattern_manager_dirty = True
        self._refresh_table()
        if pattern.key in self._visible_keys():
            self._tree.selection_set(pattern.key)

    def _on_edit(self) -> None:
        patterns = self._selected_patterns()
        if len(patterns) != 1:
            self._set_status("Select exactly one pattern to edit.")
            return
        pattern = patterns[0]
        if pattern.builtin:
            self._add_from_source(pattern)
            return
        result = self._open_pattern_dialog(existing=pattern)
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
        self._replace_pattern(pattern.key, updated)
        self._pattern_manager_dirty = True
        self._refresh_table()
        if pattern.key in self._visible_keys():
            self._tree.selection_set(pattern.key)

    # ------------------------------------------------------------------
    # Color picker / add-edit dialog
    # ------------------------------------------------------------------

    def _active_dialog_parent(self) -> tk.Misc:
        """Prefer the open Pattern Manager so child dialogs stay modal to it."""
        mgr = getattr(self, "_pattern_manager", None)
        if mgr is not None:
            try:
                if mgr.winfo_exists():
                    return mgr
            except Exception:
                pass
        return self.frame.winfo_toplevel()

    def _pick_color(self, severity: Severity) -> None:
        """Open the Tk color chooser for a severity color, then repaint."""
        var = self._color_vars[severity]
        if self._pick_color_into(var, "{0} color".format(_SEVERITY_LABELS[severity])):
            self._render_swatch(self._severity_swatches[severity], var.get(), False)

    def _pick_user_color(self, key: str) -> None:
        """Open the Tk color chooser for a user color, then repaint."""
        var = self._user_color_vars[key]
        if self._pick_color_into(var, "{0} color".format(_USER_COLOR_LABELS[key])):
            self._render_swatch(self._user_swatches[key], var.get(), True)

    def _clear_user_color(self, key: str) -> None:
        """Reset a user color to the saved empty-string value and repaint."""
        var = self._user_color_vars[key]
        var.set("")
        self._render_swatch(self._user_swatches[key], "", True)

    def _pick_color_into(self, var: tk.StringVar, title: str) -> bool:
        """Open the Tk color chooser and write the chosen hex into *var*.

        Returns True when a color was chosen (var updated), False on cancel/error.
        """
        from tkinter import colorchooser

        current = var.get().strip()
        try:
            _rgb, chosen = colorchooser.askcolor(
                color=current or None,
                title=title,
                parent=self._active_dialog_parent(),
            )
        except Exception:
            return False
        if chosen:
            var.set(str(chosen).lower())
            return True
        return False

    def _open_pattern_dialog(
        self,
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
        parent = self._active_dialog_parent()
        dialog = tk.Toplevel(parent)
        dialog.title("Edit Pattern" if existing is not None else "Add Pattern")
        dialog.resizable(False, False)
        dialog.transient(parent)
        self._theme.apply_to_widget(dialog, "main_window")

        outer = tk.Frame(dialog, padx=14, pady=12)
        self._theme.apply_to_widget(outer, "main_window")
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
            self._theme.apply_to_widget(field_label, "label")
            field_label.grid(row=row, column=0, sticky="w", pady=3)
            entry = tk.Entry(outer, textvariable=var, width=28)
            self._theme.apply_to_widget(entry, "entry")
            entry.grid(row=row, column=1, sticky="ew", pady=3, padx=(8, 0))

        _add_field(0, "Label:", label_var)

        cat_label = tk.Label(outer, text="Category:", anchor="w")
        self._theme.apply_to_widget(cat_label, "label")
        cat_label.grid(row=1, column=0, sticky="w", pady=3)
        cat_combo = ttk.Combobox(
            outer,
            textvariable=category_var,
            values=category_choices(self._patterns),
            state="normal",
            width=28,
        )
        cat_combo.grid(row=1, column=1, sticky="ew", pady=3, padx=(8, 0))

        _add_field(2, "Pattern:", pattern_var)

        sev_label = tk.Label(outer, text="Severity:", anchor="w")
        self._theme.apply_to_widget(sev_label, "label")
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
        self._theme.apply_to_widget(tag_label, "label")
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
        self._theme.apply_to_widget(enabled_cb, "checkbox")
        enabled_cb.grid(row=5, column=1, sticky="w", pady=3, padx=(8, 0))

        result: Dict[str, Any] = {}

        def _on_ok() -> None:
            ok, message = validate_pattern_fields(pattern_var.get())
            if not ok:
                safe_messagebox.showerror("Invalid Pattern", message, parent=dialog)
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
        self._theme.apply_to_widget(btn_row, "main_window")
        btn_row.grid(row=6, column=0, columnspan=2, sticky="e", pady=(10, 0))

        ok_btn = tk.Button(btn_row, text="OK", command=_on_ok)
        self._theme.apply_to_widget(ok_btn, "button_primary")
        ok_btn.pack(side=tk.RIGHT, padx=(6, 0))

        cancel_btn = tk.Button(btn_row, text="Cancel", command=dialog.destroy)
        self._theme.apply_to_widget(cancel_btn, "button_secondary")
        cancel_btn.pack(side=tk.RIGHT)

        outer.grid_columnconfigure(1, weight=1)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        _grab_when_viewable(dialog, parent)
        dialog.wait_window()

        # Restore the Pattern Manager's modality: the child grab_set() stole the
        # grab and destroying it does not give it back automatically.
        mgr = getattr(self, "_pattern_manager", None)
        if mgr is not None:
            try:
                if mgr.winfo_exists():
                    mgr.grab_set()
                    ensure_dialog_focus(mgr, self.frame.winfo_toplevel())
            except Exception:
                pass

        return result or None


def build_sherlock_tab(parent: tk.Widget, context: dict) -> tk.Widget:
    """Build and return the Sherlock tab frame."""
    tab = SherlockTab(parent, context)
    return tab.frame


def open_sherlock_settings_window(parent: tk.Widget, settings_manager: Any) -> None:
    """Open the existing Sherlock settings UI in a focused modal window.

    Hosts the real Sherlock tab (no UI duplication); it only needs settings_manager.
    Blocks until closed so callers can refresh from the shard afterward.
    """
    theme = get_theme()
    win = tk.Toplevel(parent)
    win.title("Sherlock Settings")
    win.transient(parent)
    theme.apply_to_widget(win, "main_window")

    frame = build_sherlock_tab(win, {"settings_manager": settings_manager})
    frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    close_btn = tk.Button(win, text="Close", command=win.destroy)
    theme.apply_to_widget(close_btn, "button_secondary")
    close_btn.pack(side=tk.RIGHT, padx=8, pady=(0, 8))

    win.protocol("WM_DELETE_WINDOW", win.destroy)
    win.grab_set()
    ensure_dialog_focus(win, parent)
    win.wait_window()
