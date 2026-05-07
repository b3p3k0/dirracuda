"""
Shared keyboard shortcut helpers for Tk dialogs and windows.

Design goals:
- Keep most bindings scoped to each owning window/dialog.
- Preserve safe default behavior for multiline Text widgets.
- Centralize cross-platform shortcut aliases (Ctrl + Command).
"""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, Dict, Iterable, Optional


ShortcutHandler = Callable[[], None]


def _safe_bind(widget: tk.Misc, sequence: str, callback: Callable[[tk.Event], Optional[str]]) -> None:
    """Best-effort binding helper."""
    try:
        widget.bind(sequence, callback)
    except Exception:
        pass


def _safe_bind_all(widget: tk.Misc, sequence: str, callback: Callable[[tk.Event], Optional[str]]) -> None:
    """Best-effort global binding helper."""
    try:
        widget.bind_all(sequence, callback)
    except Exception:
        pass


def _focus_widget(container: tk.Misc) -> Optional[tk.Misc]:
    try:
        return container.focus_get()
    except Exception:
        return None


def is_multiline_text_focused(container: tk.Misc) -> bool:
    """
    Return True when keyboard focus is on a Tk Text widget.

    Enter should remain newline in this case unless a Ctrl/Cmd+Enter override is
    explicitly used.
    """
    widget = _focus_widget(container)
    if widget is None:
        return False
    try:
        return str(widget.winfo_class()) == "Text"
    except Exception:
        return False


def _invoke(handler: ShortcutHandler) -> Optional[str]:
    try:
        handler()
    finally:
        # Always consume the event to avoid double-trigger patterns.
        return "break"


def bind_escape(window: tk.Misc, on_cancel: ShortcutHandler) -> None:
    _safe_bind(window, "<Escape>", lambda _e: _invoke(on_cancel))


def bind_close_shortcuts(window: tk.Misc, on_close: ShortcutHandler) -> None:
    """
    Bind close shortcuts for non-destructive windows.

    Includes Esc and Ctrl/Cmd+W.
    """
    bind_escape(window, on_close)
    for seq in ("<Control-w>", "<Control-W>", "<Command-w>", "<Command-W>"):
        _safe_bind(window, seq, lambda _e: _invoke(on_close))


def bind_save_shortcuts(window: tk.Misc, on_save: ShortcutHandler) -> None:
    """Bind Ctrl/Cmd+S save/apply shortcuts."""
    for seq in ("<Control-s>", "<Control-S>", "<Command-s>", "<Command-S>"):
        _safe_bind(window, seq, lambda _e: _invoke(on_save))


def bind_submit_shortcuts(
    window: tk.Misc,
    on_submit: ShortcutHandler,
    *,
    allow_text_submit_with_enter: bool = False,
    allow_ctrl_enter_submit: bool = True,
) -> None:
    """
    Bind Enter-based submit behavior.

    Defaults:
    - Return/KP_Enter submit unless focus is in multiline Text.
    - Ctrl/Cmd+Return submits even from multiline Text.
    """

    def _on_return(_event: tk.Event) -> Optional[str]:
        if not allow_text_submit_with_enter and is_multiline_text_focused(window):
            # Preserve newline behavior in Text widgets.
            return None
        return _invoke(on_submit)

    for seq in ("<Return>", "<KP_Enter>"):
        _safe_bind(window, seq, _on_return)

    if allow_ctrl_enter_submit:
        for seq in (
            "<Control-Return>",
            "<Control-KP_Enter>",
            "<Command-Return>",
            "<Command-KP_Enter>",
        ):
            _safe_bind(window, seq, lambda _e: _invoke(on_submit))


def bind_tree_enter_shortcut(tree: tk.Misc, on_open_selected: ShortcutHandler) -> None:
    """Make Enter on list/tree act like open/reopen selected."""
    for seq in ("<Return>", "<KP_Enter>"):
        _safe_bind(tree, seq, lambda _e: _invoke(on_open_selected))


def bind_browser_navigation_shortcuts(
    window: tk.Misc,
    tree: tk.Misc,
    *,
    on_open_selected: ShortcutHandler,
    on_up: ShortcutHandler,
    on_refresh: ShortcutHandler,
    on_close: ShortcutHandler,
) -> None:
    """
    Bind browser navigation shortcuts for SMB/FTP/HTTP explorer windows.

    - Enter/KP_Enter: open selected row (double-click parity).
    - BackSpace and Alt+Up: navigate to parent/up.
    - F5 and Ctrl/Cmd+R: refresh current view.
    - Esc and Ctrl/Cmd+W: close browser window.
    """
    bind_tree_enter_shortcut(tree, on_open_selected)

    for seq in ("<BackSpace>", "<Alt-Up>"):
        _safe_bind(window, seq, lambda _e: _invoke(on_up))

    for seq in ("<F5>", "<Control-r>", "<Control-R>", "<Command-r>", "<Command-R>"):
        _safe_bind(window, seq, lambda _e: _invoke(on_refresh))

    bind_close_shortcuts(window, on_close)


def bind_viewer_shortcuts(
    window: tk.Misc,
    *,
    on_close: ShortcutHandler,
    on_save: Optional[ShortcutHandler] = None,
) -> None:
    """
    Bind file/image viewer shortcuts.

    - Esc/Ctrl+W/Cmd+W: close viewer.
    - Ctrl/Cmd+S: save to quarantine when save callback is provided.
    """
    bind_close_shortcuts(window, on_close)
    if on_save is not None:
        bind_save_shortcuts(window, on_save)


def bind_dashboard_alt_shortcuts(
    root: tk.Misc,
    *,
    actions_by_digit: Dict[str, ShortcutHandler],
    theme_toggle: Optional[ShortcutHandler] = None,
    reserved_digits: Iterable[str] = ("7", "8", "9", "0"),
) -> None:
    """
    Bind Alt-based dashboard launch shortcuts.

    - Alt+1..Alt+6 map to action handlers.
    - Alt+7..Alt+0 are reserved no-op (consumed).
    """

    def _bind_alt_digit(digit: str, handler: ShortcutHandler) -> None:
        seq = f"<Alt-KeyPress-{digit}>"
        _safe_bind(root, seq, lambda _e: _invoke(handler))

    for digit, handler in actions_by_digit.items():
        _bind_alt_digit(str(digit), handler)

    for digit in reserved_digits:
        _bind_alt_digit(str(digit), lambda: None)

    if theme_toggle is not None:
        for seq in ("<Alt-KeyPress-t>", "<Alt-KeyPress-T>"):
            _safe_bind(root, seq, lambda _e: _invoke(theme_toggle))


def bind_global_app_shortcuts(
    root: tk.Misc,
    *,
    on_quit: ShortcutHandler,
    on_help: ShortcutHandler,
    on_theme_toggle: ShortcutHandler,
) -> None:
    """
    Bind application-wide shortcuts intended to work from any focused window.

    Uses bind_all intentionally for app-global behavior:
    - Ctrl/Cmd+Q quits through existing close flow.
    - Ctrl/Cmd+H opens help/manual surface.
    - Ctrl/Cmd+T toggles theme.
    """
    mappings = {
        on_quit: ("<Control-q>", "<Control-Q>", "<Command-q>", "<Command-Q>"),
        on_help: ("<Control-h>", "<Control-H>", "<Command-h>", "<Command-H>"),
        on_theme_toggle: ("<Control-t>", "<Control-T>", "<Command-t>", "<Command-T>"),
    }
    for handler, sequences in mappings.items():
        for seq in sequences:
            _safe_bind_all(root, seq, lambda _e, h=handler: _invoke(h))


def add_shortcut_hint(parent: tk.Widget, theme: Any, text: str) -> tk.Label:
    """Render a lightweight footer hint line near action buttons."""
    frame = tk.Frame(parent)
    if theme:
        try:
            theme.apply_to_widget(frame, "main_window")
        except Exception:
            pass
    frame.pack(fill=tk.X, pady=(0, 6))

    label = tk.Label(frame, text=text, anchor="w", justify="left")
    if theme:
        try:
            theme.apply_to_widget(label, "label")
            label.configure(fg=theme.colors.get("text_secondary", label.cget("fg")))
        except Exception:
            pass
    label.pack(anchor="w")
    return label
