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

C21: the "Sherlock Patterns" modal dialog (its table, filter row, Add/Edit
dialog, and Copy/Delete/Enable-Disable/Restore/Export/Save & Close actions) now
lives in sherlock_pattern_manager.py. This tab keeps the settings surface and
thin delegating stub methods that drive that dialog; behavior is unchanged.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog
from typing import Any, Dict, List, Optional, Tuple

from gui.utils import safe_messagebox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import get_theme
from gui.components.experimental_features import sherlock_pattern_manager
from gui.components.experimental_features.sherlock_pattern_manager import (
    _FACET_ALL,
    _SEVERITY_LABELS,
    _SEVERITY_ORDER,
    _USER_COLOR_LABELS,
    _pattern_matches_filters,
    _user_tag_facet_choices,
    validate_pattern_fields,
)
from shared.sherlock import (
    SHERLOCK_SETTINGS_KEY,
    USER_COLOR_KEYS,
    Severity,
    SherlockPattern,
    SherlockSettings,
    default_settings,
    is_valid_color,
    settings_from_dict,
    settings_to_dict,
    validate_color,
    validate_user_color,
)
from shared.sherlock.serialize import severity_from_str, severity_to_str


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
        # Pattern manager panes + dialog are built lazily on demand.
        self._tree: Optional[ttk.Treeview] = None
        self._category_tree: Optional[ttk.Treeview] = None
        # C23 left-pane selected category (display-only); _FACET_ALL == "All".
        self._active_category: str = _FACET_ALL
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

    # ------------------------------------------------------------------
    # Pattern manager filter row + table (see sherlock_pattern_manager.py)
    # ------------------------------------------------------------------

    def _build_filter_row(self, parent: tk.Widget) -> None:
        return sherlock_pattern_manager.build_filter_row(self, parent)

    def _on_filter_change(self, *args: Any) -> None:
        return sherlock_pattern_manager.on_filter_change(self, *args)

    def _on_clear_filters(self) -> None:
        return sherlock_pattern_manager.on_clear_filters(self)

    def _build_table(self, parent: tk.Widget) -> None:
        return sherlock_pattern_manager.build_table(self, parent)

    def _build_category_pane(self, parent: tk.Widget) -> None:
        return sherlock_pattern_manager.build_category_pane(self, parent)

    def _refresh_category_list(self) -> None:
        return sherlock_pattern_manager.refresh_category_list(self)

    def _on_category_select(self, *args: Any) -> None:
        return sherlock_pattern_manager.on_category_select(self, *args)

    def _on_category_search(self, *args: Any) -> None:
        return sherlock_pattern_manager.on_category_search(self, *args)

    # ------------------------------------------------------------------
    # Pattern manager dialog
    # ------------------------------------------------------------------

    def _open_pattern_manager(self) -> None:
        return sherlock_pattern_manager.open_pattern_manager(self)

    def _teardown_manager(self) -> None:
        return sherlock_pattern_manager.teardown_manager(self)

    def _close_manager(self) -> None:
        return sherlock_pattern_manager.close_manager(self)

    def _save_and_close_manager(self) -> None:
        return sherlock_pattern_manager.save_and_close_manager(self)

    # ------------------------------------------------------------------
    # Table rendering / selection
    # ------------------------------------------------------------------

    def _current_filter_state(self) -> Tuple[str, str, str, str, str]:
        return sherlock_pattern_manager.current_filter_state(self)

    def _visible_groups(self):
        return sherlock_pattern_manager.visible_groups(self)

    def _visible_keys(self) -> set:
        return sherlock_pattern_manager.visible_keys(self)

    def _refresh_after_mutation(self) -> None:
        return sherlock_pattern_manager.refresh_after_mutation(self)

    def _refresh_facet_choices(self) -> None:
        return sherlock_pattern_manager.refresh_facet_choices(self)

    def _refresh_table(self) -> None:
        return sherlock_pattern_manager.refresh_table(self)

    def _selected_groups(self):
        return sherlock_pattern_manager.selected_groups(self)

    def _selected_patterns(self) -> List[SherlockPattern]:
        return sherlock_pattern_manager.selected_patterns(self)

    def _replace_pattern(self, key: str, new_pattern: SherlockPattern) -> None:
        return sherlock_pattern_manager.replace_pattern(self, key, new_pattern)

    def _on_mousewheel(self, event: Any) -> str:
        return sherlock_pattern_manager.on_mousewheel(self, event)

    def _on_row_double_click(self, event: Any) -> str:
        return sherlock_pattern_manager.on_row_double_click(self, event)

    def _set_status(self, text: str) -> None:
        self._status_label.configure(text=text)

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _on_toggle(self) -> None:
        return sherlock_pattern_manager.on_toggle(self)

    def _on_delete(self) -> None:
        return sherlock_pattern_manager.on_delete(self)

    def _on_restore_builtins(self) -> None:
        return sherlock_pattern_manager.on_restore_builtins(self)

    def _on_export(self) -> None:
        return sherlock_pattern_manager.on_export(self)

    def _on_add(self) -> None:
        return sherlock_pattern_manager.on_add(self)

    def _on_copy(self) -> None:
        return sherlock_pattern_manager.on_copy(self)

    def _duplicate_group_as_customs(self, group: Any) -> None:
        return sherlock_pattern_manager.duplicate_group_as_customs(self, group)

    def _add_from_source(self, source: SherlockPattern) -> None:
        return sherlock_pattern_manager.add_from_source(self, source)

    def _append_custom_from_result(self, result: Dict[str, Any]) -> None:
        return sherlock_pattern_manager.append_custom_from_result(self, result)

    def _on_edit(self) -> None:
        return sherlock_pattern_manager.on_edit(self)

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
        return sherlock_pattern_manager.open_pattern_dialog(
            self, existing=existing, prefill=prefill
        )


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
