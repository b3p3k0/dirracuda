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
"""

from __future__ import annotations

import dataclasses
import uuid
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional, Tuple

from gui.utils import safe_messagebox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import get_theme
from shared.sherlock import (
    COLOR_TAG_NONE,
    SHERLOCK_SETTINGS_KEY,
    USER_COLOR_KEYS,
    Severity,
    SherlockPattern,
    SherlockSettings,
    builtin_patterns,
    default_settings,
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


class SherlockTab:
    """Content widget for the Sherlock experimental feature tab."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
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
        # Pattern table + manager dialog are built lazily on demand.
        self._tree: Optional[ttk.Treeview] = None
        self._pattern_manager: Optional[tk.Toplevel] = None
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

    def _on_save(self) -> None:
        """Validate and persist the current settings."""
        colors = self._collect_colors()
        if colors is None:
            return
        user_colors = self._collect_user_colors()
        if user_colors is None:
            return

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
            return
        try:
            ok = sm.set_setting(SHERLOCK_SETTINGS_KEY, data)
        except Exception as exc:  # pragma: no cover - defensive
            self._set_status("Save failed: {0}".format(exc))
            return
        if ok:
            self._set_status("Saved.")
        else:
            self._set_status("Save failed; settings were not written.")

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
            self._build_color_input(
                color_row,
                "{0}:".format(_SEVERITY_LABELS[sev]),
                self._color_vars[sev],
                lambda s=sev: self._pick_color(s),
            )

        # User colors
        self._build_caption(frame, "User colors")
        user_row = tk.Frame(frame)
        self._theme.apply_to_widget(user_row, "main_window")
        user_row.pack(anchor="w", padx=12, pady=(0, 6), fill=tk.X)
        for key in USER_COLOR_KEYS:
            var = self._user_color_vars[key]
            self._build_color_input(
                user_row,
                "{0}:".format(_USER_COLOR_LABELS[key]),
                var,
                lambda v=var, k=key: self._pick_color_into(
                    v, "{0} color".format(_USER_COLOR_LABELS[k])
                ),
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
        self, parent: tk.Widget, label_text: str, var: tk.StringVar, on_pick
    ) -> None:
        group = tk.Frame(parent)
        self._theme.apply_to_widget(group, "main_window")
        group.pack(side=tk.LEFT, padx=(0, 14))

        label = tk.Label(group, text=label_text)
        self._theme.apply_to_widget(label, "label")
        label.pack(side=tk.LEFT)

        entry = tk.Entry(group, textvariable=var, width=9)
        self._theme.apply_to_widget(entry, "entry")
        entry.pack(side=tk.LEFT, padx=(4, 2))

        pick = tk.Button(group, text="...", width=3, command=on_pick)
        self._theme.apply_to_widget(pick, "button_secondary")
        pick.pack(side=tk.LEFT)

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
            selectmode="browse",
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
        dialog.geometry("700x560")
        dialog.minsize(680, 540)

        outer = tk.Frame(dialog, padx=12, pady=10)
        self._theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True)

        self._build_table(outer)

        def _close() -> None:
            self._pattern_manager = None
            self._tree = None
            dialog.destroy()

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

        restore_btn = tk.Button(
            action_row, text="Restore Built-ins", command=self._on_restore_builtins
        )
        self._theme.apply_to_widget(restore_btn, "button_secondary")
        restore_btn.pack(side=tk.LEFT, padx=(0, 6))

        close_btn = tk.Button(action_row, text="Close", command=_close)
        self._theme.apply_to_widget(close_btn, "button_secondary")
        close_btn.pack(side=tk.LEFT, padx=(0, 6))

        self._pattern_manager = dialog
        self._refresh_table()
        try:
            self._tree.focus_set()
        except Exception:
            pass

        dialog.protocol("WM_DELETE_WINDOW", _close)
        dialog.grab_set()
        ensure_dialog_focus(dialog, parent)
        dialog.wait_window()

    # ------------------------------------------------------------------
    # Table rendering / selection
    # ------------------------------------------------------------------

    def _refresh_table(self) -> None:
        tree = self._tree
        if tree is None:
            return
        for iid in tree.get_children():
            tree.delete(iid)
        for pattern in self._patterns:
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

    def _selected_pattern(self) -> Optional[SherlockPattern]:
        selection = self._tree.selection()
        if not selection:
            return None
        key = selection[0]
        return next((p for p in self._patterns if p.key == key), None)

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

    def _set_status(self, text: str) -> None:
        self._status_label.configure(text=text)

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _on_toggle(self) -> None:
        pattern = self._selected_pattern()
        if pattern is None:
            self._set_status("Select a pattern to enable or disable.")
            return
        self._replace_pattern(pattern.key, _with_enabled(pattern, not pattern.enabled))
        self._refresh_table()
        self._tree.selection_set(pattern.key)

    def _on_delete(self) -> None:
        pattern = self._selected_pattern()
        if pattern is None:
            self._set_status("Select a custom pattern to delete.")
            return
        if pattern.builtin:
            self._set_status("Built-ins cannot be deleted; disable them instead.")
            return
        self._patterns = [p for p in self._patterns if p.key != pattern.key]
        self._refresh_table()

    def _on_restore_builtins(self) -> None:
        """Re-enable all built-ins (clears any disabled state); customs untouched."""
        customs = [p for p in self._patterns if not p.builtin]
        self._patterns = builtin_patterns() + customs
        self._refresh_table()
        self._set_status("Built-ins restored.")

    def _on_add(self) -> None:
        result = self._open_pattern_dialog()
        if result is None:
            return
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
        self._refresh_table()
        self._tree.selection_set(pattern.key)

    def _on_edit(self) -> None:
        pattern = self._selected_pattern()
        if pattern is None:
            self._set_status("Select a custom pattern to edit.")
            return
        if pattern.builtin:
            self._set_status("Built-ins are read-only; disable or restore instead.")
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
        self._refresh_table()
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
        """Open the Tk color chooser for a severity color."""
        self._pick_color_into(
            self._color_vars[severity],
            "{0} color".format(_SEVERITY_LABELS[severity]),
        )

    def _pick_color_into(self, var: tk.StringVar, title: str) -> None:
        """Open the Tk color chooser and write the chosen hex into *var*."""
        from tkinter import colorchooser

        current = var.get().strip()
        try:
            _rgb, chosen = colorchooser.askcolor(
                color=current or None,
                title=title,
                parent=self._active_dialog_parent(),
            )
        except Exception:
            return
        if chosen:
            var.set(str(chosen).lower())

    def _open_pattern_dialog(
        self, existing: Optional[SherlockPattern] = None
    ) -> Optional[Dict[str, Any]]:
        """Modal add/edit dialog. Returns the collected fields or None on cancel.

        Parents to the open Pattern Manager (if any) so it stays modal to the
        manager; the manager's grab is re-asserted once this dialog closes.
        """
        parent = self._active_dialog_parent()
        dialog = tk.Toplevel(parent)
        dialog.title("Edit Pattern" if existing else "Add Pattern")
        dialog.resizable(False, False)
        dialog.transient(parent)
        dialog.grab_set()
        self._theme.apply_to_widget(dialog, "main_window")

        outer = tk.Frame(dialog, padx=14, pady=12)
        self._theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True)

        label_var = tk.StringVar(value=existing.label if existing else "")
        category_var = tk.StringVar(value=existing.category if existing else "Custom")
        pattern_var = tk.StringVar(value=existing.pattern if existing else "")
        severity_var = tk.StringVar(
            value=_SEVERITY_LABELS[existing.severity] if existing else _SEVERITY_LABELS[Severity.MED]
        )
        existing_tag = normalize_color_tag(existing.color_tag) if existing else COLOR_TAG_NONE
        color_tag_var = tk.StringVar(
            value=_TAG_TO_DIALOG_LABEL.get(existing_tag, "None")
        )
        enabled_var = tk.BooleanVar(value=existing.enabled if existing else True)

        def _add_field(row: int, text: str, var: tk.StringVar) -> None:
            field_label = tk.Label(outer, text=text, anchor="w")
            self._theme.apply_to_widget(field_label, "label")
            field_label.grid(row=row, column=0, sticky="w", pady=3)
            entry = tk.Entry(outer, textvariable=var, width=28)
            self._theme.apply_to_widget(entry, "entry")
            entry.grid(row=row, column=1, sticky="ew", pady=3, padx=(8, 0))

        _add_field(0, "Label:", label_var)
        _add_field(1, "Category:", category_var)
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
        ensure_dialog_focus(dialog, parent)
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
