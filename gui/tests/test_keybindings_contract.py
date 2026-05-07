"""Contract tests for shared keyboard shortcut helpers."""

from __future__ import annotations

from typing import Callable, Dict, Optional

from gui.utils import keybindings


class _FocusWidget:
    def __init__(self, cls_name: str) -> None:
        self._cls_name = cls_name

    def winfo_class(self) -> str:
        return self._cls_name


class _BindTarget:
    def __init__(self) -> None:
        self.bindings: Dict[str, Callable] = {}
        self.global_bindings: Dict[str, Callable] = {}
        self._focus: Optional[_FocusWidget] = None

    def bind(self, sequence: str, callback: Callable) -> None:
        self.bindings[sequence] = callback

    def bind_all(self, sequence: str, callback: Callable) -> None:
        self.global_bindings[sequence] = callback

    def focus_get(self):
        return self._focus

    def set_focus(self, cls_name: Optional[str]) -> None:
        self._focus = _FocusWidget(cls_name) if cls_name else None


def test_submit_shortcuts_skip_plain_enter_in_multiline_text_focus() -> None:
    target = _BindTarget()
    calls = {"count": 0}

    keybindings.bind_submit_shortcuts(target, lambda: calls.__setitem__("count", calls["count"] + 1))
    target.set_focus("Text")

    # Plain Enter should preserve newline behavior inside Text widgets.
    result = target.bindings["<Return>"](None)
    assert result is None
    assert calls["count"] == 0

    # Ctrl+Enter should submit even when focus is on Text.
    ctrl_result = target.bindings["<Control-Return>"](None)
    assert ctrl_result == "break"
    assert calls["count"] == 1


def test_submit_shortcuts_fire_plain_enter_for_non_text_focus() -> None:
    target = _BindTarget()
    calls = {"count": 0}
    keybindings.bind_submit_shortcuts(target, lambda: calls.__setitem__("count", calls["count"] + 1))

    target.set_focus("Entry")
    result = target.bindings["<Return>"](None)

    assert result == "break"
    assert calls["count"] == 1


def test_close_shortcuts_bind_escape_and_ctrl_cmd_w() -> None:
    target = _BindTarget()
    calls = {"count": 0}
    keybindings.bind_close_shortcuts(target, lambda: calls.__setitem__("count", calls["count"] + 1))

    for seq in ("<Escape>", "<Control-w>", "<Command-w>"):
        assert seq in target.bindings
        assert target.bindings[seq](None) == "break"

    assert calls["count"] == 3


def test_save_shortcuts_bind_ctrl_and_command_variants() -> None:
    target = _BindTarget()
    calls = {"count": 0}
    keybindings.bind_save_shortcuts(target, lambda: calls.__setitem__("count", calls["count"] + 1))

    assert target.bindings["<Control-s>"](None) == "break"
    assert target.bindings["<Command-s>"](None) == "break"
    assert calls["count"] == 2


def test_tree_enter_shortcut_triggers_open_action() -> None:
    target = _BindTarget()
    calls = {"count": 0}
    keybindings.bind_tree_enter_shortcut(target, lambda: calls.__setitem__("count", calls["count"] + 1))

    assert target.bindings["<Return>"](None) == "break"
    assert calls["count"] == 1


def test_dashboard_alt_shortcuts_bind_actions_and_reserved_keys() -> None:
    target = _BindTarget()
    calls = {"scan": 0}

    keybindings.bind_dashboard_alt_shortcuts(
        target,
        actions_by_digit={"1": lambda: calls.__setitem__("scan", calls["scan"] + 1)},
        reserved_digits=("7", "8", "9", "0"),
    )

    assert target.bindings["<Alt-KeyPress-1>"](None) == "break"
    assert calls["scan"] == 1

    assert "<Alt-KeyPress-t>" not in target.bindings

    # Reserved keys are bound and consumed as no-ops.
    for digit in ("7", "8", "9", "0"):
        assert target.bindings[f"<Alt-KeyPress-{digit}>"](None) == "break"


def test_global_app_shortcuts_bind_ctrl_and_command_aliases() -> None:
    target = _BindTarget()
    calls = {"quit": 0, "help": 0, "theme": 0}

    keybindings.bind_global_app_shortcuts(
        target,
        on_quit=lambda: calls.__setitem__("quit", calls["quit"] + 1),
        on_help=lambda: calls.__setitem__("help", calls["help"] + 1),
        on_theme_toggle=lambda: calls.__setitem__("theme", calls["theme"] + 1),
    )

    assert target.global_bindings["<Control-q>"](None) == "break"
    assert target.global_bindings["<Command-h>"](None) == "break"
    assert target.global_bindings["<Control-t>"](None) == "break"
    assert calls == {"quit": 1, "help": 1, "theme": 1}


def test_browser_navigation_shortcuts_bind_and_dispatch() -> None:
    window = _BindTarget()
    tree = _BindTarget()
    calls = {"open": 0, "up": 0, "refresh": 0, "close": 0}

    keybindings.bind_browser_navigation_shortcuts(
        window,
        tree,
        on_open_selected=lambda: calls.__setitem__("open", calls["open"] + 1),
        on_up=lambda: calls.__setitem__("up", calls["up"] + 1),
        on_refresh=lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
        on_close=lambda: calls.__setitem__("close", calls["close"] + 1),
    )

    assert tree.bindings["<Return>"](None) == "break"
    assert window.bindings["<BackSpace>"](None) == "break"
    assert window.bindings["<Alt-Up>"](None) == "break"
    assert window.bindings["<F5>"](None) == "break"
    assert window.bindings["<Control-r>"](None) == "break"
    assert window.bindings["<Escape>"](None) == "break"
    assert window.bindings["<Control-w>"](None) == "break"

    assert calls == {"open": 1, "up": 2, "refresh": 2, "close": 2}


def test_viewer_shortcuts_bind_close_and_optional_save() -> None:
    window_no_save = _BindTarget()
    calls_no_save = {"close": 0}
    keybindings.bind_viewer_shortcuts(
        window_no_save,
        on_close=lambda: calls_no_save.__setitem__("close", calls_no_save["close"] + 1),
    )
    assert window_no_save.bindings["<Escape>"](None) == "break"
    assert "<Control-s>" not in window_no_save.bindings
    assert calls_no_save["close"] == 1

    window_with_save = _BindTarget()
    calls_with_save = {"close": 0, "save": 0}
    keybindings.bind_viewer_shortcuts(
        window_with_save,
        on_close=lambda: calls_with_save.__setitem__("close", calls_with_save["close"] + 1),
        on_save=lambda: calls_with_save.__setitem__("save", calls_with_save["save"] + 1),
    )
    assert window_with_save.bindings["<Control-s>"](None) == "break"
    assert window_with_save.bindings["<Escape>"](None) == "break"
    assert calls_with_save == {"close": 1, "save": 1}
