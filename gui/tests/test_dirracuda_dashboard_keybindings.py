"""Tests for dashboard/root keyboard shortcut wiring in canonical entrypoint."""

from __future__ import annotations

from gui.components import global_shortcuts as _global_shortcuts
from gui.utils.dirracuda_loader import load_dirracuda_module

DIRRACUDA = load_dirracuda_module()


class _RootBindStub:
    def __init__(self) -> None:
        self.bindings = {}
        self.global_bindings = {}

    def bind(self, sequence, callback):
        self.bindings[sequence] = callback

    def bind_all(self, sequence, callback):
        self.global_bindings[sequence] = callback


class _DashboardStub:
    def __init__(self, calls):
        self._calls = calls

    def _handle_scan_button_click(self):
        self._calls.append("scan")

    def _open_drill_down(self, window_type: str):
        self._calls.append(f"drill:{window_type}")

    def _open_db_tools(self):
        self._calls.append("db_tools")

    def _handle_experimental_button_click(self):
        self._calls.append("experimental")

    def _open_config_editor(self):
        self._calls.append("config")

    def _open_about_dialog(self):
        self._calls.append("about")

    def _toggle_theme(self):
        self._calls.append("theme")


def _build_app_stub():
    app = DIRRACUDA.XSMBSeekGUI.__new__(DIRRACUDA.XSMBSeekGUI)
    app.root = _RootBindStub()
    calls = []
    app.dashboard = _DashboardStub(calls)
    app._on_closing = lambda: calls.append("close")
    app._refresh_dashboard = lambda: calls.append("refresh")
    app._open_drill_down_window = lambda window_type, payload: calls.append(f"root_drill:{window_type}")
    app._open_xsmbseek_settings = lambda: calls.append("settings")
    return app, calls


def test_setup_event_handlers_registers_dashboard_alt_shortcuts() -> None:
    app, _calls = _build_app_stub()
    DIRRACUDA.XSMBSeekGUI._setup_event_handlers(app)

    # Existing global shortcuts remain present.
    for seq in ("<F5>", "<Control-r>", "<Control-i>", "<Control-comma>"):
        assert seq in app.root.bindings

    # App-wide global shortcuts are registered via bind_all.
    for seq in (
        "<Control-q>", "<Control-Q>", "<Command-q>", "<Command-Q>",
        "<Control-h>", "<Control-H>", "<Command-h>", "<Command-H>",
        "<Control-t>", "<Control-T>", "<Command-t>", "<Command-T>",
    ):
        assert seq in app.root.global_bindings

    # New dashboard shortcuts are registered.
    for seq in (
        "<Alt-KeyPress-1>",
        "<Alt-KeyPress-2>",
        "<Alt-KeyPress-3>",
        "<Alt-KeyPress-4>",
        "<Alt-KeyPress-5>",
        "<Alt-KeyPress-6>",
        "<Alt-KeyPress-7>",
        "<Alt-KeyPress-8>",
        "<Alt-KeyPress-9>",
        "<Alt-KeyPress-0>",
    ):
        assert seq in app.root.bindings
    assert "<Alt-KeyPress-t>" not in app.root.bindings


def test_dashboard_alt_shortcuts_dispatch_expected_actions() -> None:
    app, calls = _build_app_stub()
    DIRRACUDA.XSMBSeekGUI._setup_event_handlers(app)

    app.root.bindings["<Alt-KeyPress-1>"](None)
    app.root.bindings["<Alt-KeyPress-2>"](None)
    app.root.bindings["<Alt-KeyPress-3>"](None)
    app.root.bindings["<Alt-KeyPress-4>"](None)
    app.root.bindings["<Alt-KeyPress-5>"](None)
    app.root.bindings["<Alt-KeyPress-6>"](None)

    assert calls == [
        "scan",
        "drill:server_list",
        "db_tools",
        "experimental",
        "config",
        "about",
    ]


def test_reserved_alt_digits_are_consumed_without_action() -> None:
    app, calls = _build_app_stub()
    DIRRACUDA.XSMBSeekGUI._setup_event_handlers(app)

    for seq in ("<Alt-KeyPress-7>", "<Alt-KeyPress-8>", "<Alt-KeyPress-9>", "<Alt-KeyPress-0>"):
        result = app.root.bindings[seq](None)
        assert result == "break"

    assert calls == []


def test_global_shortcuts_dispatch_quit_help_and_theme() -> None:
    app, calls = _build_app_stub()
    help_calls = []
    original_help = _global_shortcuts.open_help_manual_dialog
    _global_shortcuts.open_help_manual_dialog = lambda *_args, **_kwargs: help_calls.append("help")
    DIRRACUDA.XSMBSeekGUI._setup_event_handlers(app)

    try:
        assert app.root.global_bindings["<Control-q>"](None) == "break"
        assert app.root.global_bindings["<Control-t>"](None) == "break"
        assert app.root.global_bindings["<Control-h>"](None) == "break"
    finally:
        _global_shortcuts.open_help_manual_dialog = original_help

    assert "close" in calls
    assert "theme" in calls
    assert help_calls == ["help"]
