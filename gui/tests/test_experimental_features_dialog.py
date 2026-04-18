"""
Tests for ExperimentalFeaturesDialog, RedditTab, and dashboard_experimental routing.

C3  — regression guards for post-removal behavioral correctness
C5  — comprehensive coverage: button ordering, warning-dismiss persistence,
       add-to-DB path resolution
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Lightweight impacket stub so GUI modules import cleanly in headless test env.
if "impacket" not in sys.modules:
    _imod = types.ModuleType("impacket")
    _ismb = types.ModuleType("impacket.smb")
    _ismb.SMB2_DIALECT_002 = object()
    _iconn = types.ModuleType("impacket.smbconnection")
    _iconn.SMBConnection = object

    class _SessionError(Exception):
        pass

    _iconn.SessionError = _SessionError
    _imod.smb = _ismb
    sys.modules["impacket"] = _imod
    sys.modules["impacket.smb"] = _ismb
    sys.modules["impacket.smbconnection"] = _iconn

import tkinter as tk

from gui.components.experimental_features.reddit_tab import RedditTab
from gui.components.dashboard import DashboardWidget
import gui.components.dashboard_experimental as dashboard_experimental


# ---------------------------------------------------------------------------
# C5 Group A — Experimental button packed between DB Tools and Config
# ---------------------------------------------------------------------------

def test_experimental_button_packed_between_db_tools_and_config(monkeypatch):
    """
    Verifies button layout order by recording the text of each tk.Button
    at its .pack() call. pack() is called in source order, so position in
    packed_texts == render order.
    """
    packed_texts = []

    class TrackingButton:
        def __init__(self, parent=None, text="", **kw):
            self._text = text
        def pack(self, **kw):
            packed_texts.append(self._text)
        def configure(self, **kw): pass
        def config(self, **kw): pass

    monkeypatch.setattr(tk, "Button", TrackingButton)
    monkeypatch.setattr(tk, "Frame", lambda *a, **kw: MagicMock())

    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = MagicMock()
    dash.theme = MagicMock()
    dash.theme.apply_to_widget = lambda w, s: None
    dash.theme.create_styled_label = lambda *a, **kw: MagicMock(pack=lambda **k: None)
    dash.theme.fonts = {"small": ("f", 9)}
    dash.main_frame = MagicMock()
    dash._theme_toggle_button_text = lambda: "☀️"

    dash._build_header_section()

    db_idx = next(i for i, t in enumerate(packed_texts) if "DB Tools" in t)
    exp_idx = next(i for i, t in enumerate(packed_texts) if "Experimental" in t)
    cfg_idx = next(i for i, t in enumerate(packed_texts) if "Config" in t)
    assert db_idx < exp_idx < cfg_idx


# ---------------------------------------------------------------------------
# C3 — Reddit tab routes reddit_grab_callback correctly (regression guard)
# ---------------------------------------------------------------------------

def test_reddit_grab_callback_invoked_from_reddit_tab():
    """_invoke_reddit_grab must call the context callback — not a stub."""
    handler_called = []
    context = {
        "reddit_grab_callback": lambda: handler_called.append(True),
        "open_reddit_post_db": lambda: None,
    }
    # Use __new__ to skip tkinter construction; _invoke_reddit_grab only needs _context.
    tab = RedditTab.__new__(RedditTab)
    tab._context = context
    tab._invoke_reddit_grab()
    assert handler_called == [True]


def test_open_reddit_post_db_callback_invoked_from_reddit_tab():
    """_invoke_open_reddit_post_db must call the context callback."""
    post_db_called = []
    context = {
        "reddit_grab_callback": lambda: None,
        "open_reddit_post_db": lambda: post_db_called.append(True),
    }
    tab = RedditTab.__new__(RedditTab)
    tab._context = context
    tab._invoke_open_reddit_post_db()
    assert post_db_called == [True]


def test_reddit_tab_silent_when_no_grab_callback():
    """_invoke_reddit_grab must not raise when callback is absent."""
    tab = RedditTab.__new__(RedditTab)
    tab._context = {}
    tab._invoke_reddit_grab()  # must not raise


def test_reddit_tab_silent_when_no_post_db_callback():
    """_invoke_open_reddit_post_db must not raise when callback is absent."""
    tab = RedditTab.__new__(RedditTab)
    tab._context = {}
    tab._invoke_open_reddit_post_db()  # must not raise


# ---------------------------------------------------------------------------
# C5 Group C — open_reddit_post_db path resolution (three cases)
# ---------------------------------------------------------------------------

def _make_dash():
    """Minimal DashboardWidget stub for dashboard_experimental tests."""
    from gui.components.dashboard import DashboardWidget
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = MagicMock()
    dash._server_list_getter = None
    dash._open_drill_down = MagicMock()
    return dash


def test_open_reddit_post_db_with_live_server_window(monkeypatch):
    """When server window is live, browser opens with server window as parent."""
    dash = _make_dash()
    mock_win = MagicMock()
    mock_win.window.winfo_exists.return_value = True
    dash._server_list_getter = lambda: mock_win

    calls = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_reddit_browser_window",
        lambda **kw: calls.append(kw),
    )

    dashboard_experimental.open_reddit_post_db(dash)

    assert len(calls) == 1
    assert calls[0]["parent"] is mock_win.window
    assert calls[0]["add_record_callback"] is mock_win.open_add_record_dialog


def test_open_reddit_post_db_fallback_when_no_server_window(monkeypatch):
    """Getter=None path falls back to widget.parent without opening Server List."""
    dash = _make_dash()
    dash._server_list_getter = MagicMock(return_value=None)
    dash._open_drill_down = MagicMock()

    calls = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_reddit_browser_window",
        lambda **kw: calls.append(kw),
    )

    dashboard_experimental.open_reddit_post_db(dash)

    assert len(calls) == 1
    assert calls[0]["parent"] is dash.parent
    assert calls[0]["add_record_callback"] is None
    dash._open_drill_down.assert_not_called()
    assert dash._server_list_getter.call_count == 1


def test_open_reddit_post_db_treats_dead_window_as_none(monkeypatch):
    """A window whose winfo_exists() returns False is treated as None."""
    dash = _make_dash()
    mock_win = MagicMock()
    mock_win.window.winfo_exists.return_value = False
    dash._server_list_getter = lambda: mock_win
    dash._open_drill_down = MagicMock()

    calls = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_reddit_browser_window",
        lambda **kw: calls.append(kw),
    )

    dashboard_experimental.open_reddit_post_db(dash)

    assert len(calls) == 1
    assert calls[0]["parent"] is dash.parent
    assert calls[0]["add_record_callback"] is None
    dash._open_drill_down.assert_not_called()


def test_open_reddit_post_db_does_not_open_server_list_on_fallback(monkeypatch):
    """Fallback path must not open Server List as a side effect."""
    dash = _make_dash()
    dash._server_list_getter = MagicMock(return_value=None)
    dash._open_drill_down = MagicMock()

    calls = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_reddit_browser_window",
        lambda **kw: calls.append(kw),
    )

    dashboard_experimental.open_reddit_post_db(dash)

    assert len(calls) == 1
    assert calls[0]["parent"] is dash.parent
    assert calls[0]["add_record_callback"] is None
    dash._open_drill_down.assert_not_called()


def test_open_reddit_post_db_fallback_when_getter_raises(monkeypatch):
    """Getter exceptions are logged and converted to deterministic fallback."""
    dash = _make_dash()
    getter_calls = {"count": 0}

    def _raising_getter():
        getter_calls["count"] += 1
        raise RuntimeError("getter boom")

    dash._server_list_getter = _raising_getter
    dash._open_drill_down = MagicMock()

    calls = []
    log_warnings = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_reddit_browser_window",
        lambda **kw: calls.append(kw),
    )
    monkeypatch.setattr(
        dashboard_experimental._logger,
        "warning",
        lambda msg, *args: log_warnings.append(msg % args if args else msg),
    )

    dashboard_experimental.open_reddit_post_db(dash)

    assert len(calls) == 1
    assert calls[0]["parent"] is dash.parent
    assert calls[0]["add_record_callback"] is None
    dash._open_drill_down.assert_not_called()
    assert getter_calls["count"] == 1
    assert any("server list getter failed" in msg for msg in log_warnings)


# ---------------------------------------------------------------------------
# C5 Group C — set_server_list_getter stores callable on widget
# ---------------------------------------------------------------------------

def test_set_server_list_getter_stores_callable():
    dash = _make_dash()
    getter = lambda: None
    dashboard_experimental.set_server_list_getter(dash, getter)
    assert dash._server_list_getter is getter


# ---------------------------------------------------------------------------
# C5 Group B — Warning-dismiss persistence (four cases)
# ---------------------------------------------------------------------------

def _make_sm(dismissed: bool) -> MagicMock:
    sm = MagicMock()
    sm.get_setting.return_value = dismissed
    return sm


def _make_dialog_stub(dismissed: bool):
    from gui.components.experimental_features_dialog import ExperimentalFeaturesDialog
    sm = _make_sm(dismissed)
    d = ExperimentalFeaturesDialog.__new__(ExperimentalFeaturesDialog)
    d._warning_frame_built = False
    d.dismiss_var = None
    d._theme = MagicMock()
    d._theme.apply_to_widget = lambda w, s: None
    d._theme.create_styled_label = lambda *a, **kw: MagicMock()
    return d, sm


def _patch_warning_tk(monkeypatch):
    import gui.components.experimental_features_dialog as dialog_mod

    class _FakeWidget:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.pack_calls = []

        def pack(self, *args, **kwargs):
            self.pack_calls.append((args, kwargs))

    class _FakeBooleanVar:
        def __init__(self, value=False):
            self._value = bool(value)
            self._callbacks = []

        def get(self):
            return self._value

        def set(self, value):
            self._value = bool(value)
            for mode, callback in list(self._callbacks):
                callback("name", "index", mode)

        def trace_add(self, mode, callback):
            self._callbacks.append((mode, callback))
            return f"trace_{len(self._callbacks)}"

    monkeypatch.setattr(dialog_mod.tk, "Frame", _FakeWidget)
    monkeypatch.setattr(dialog_mod.tk, "Label", _FakeWidget)
    monkeypatch.setattr(dialog_mod.tk, "Checkbutton", _FakeWidget)
    monkeypatch.setattr(dialog_mod.tk, "BooleanVar", _FakeBooleanVar)


def test_warning_shown_when_not_dismissed(monkeypatch):
    _patch_warning_tk(monkeypatch)
    d, sm = _make_dialog_stub(False)
    parent = MagicMock()
    d._build_warning_section(parent, sm)
    assert d._warning_frame_built is True
    assert d.dismiss_var is not None


def test_warning_hidden_when_already_dismissed(monkeypatch):
    _patch_warning_tk(monkeypatch)
    d, sm = _make_dialog_stub(True)
    parent = MagicMock()
    d._build_warning_section(parent, sm)
    assert d._warning_frame_built is False
    assert d.dismiss_var is None


def test_dismiss_checkbox_writes_immediately_on_toggle(monkeypatch):
    _patch_warning_tk(monkeypatch)
    d, sm = _make_dialog_stub(False)
    parent = MagicMock()
    d._build_warning_section(parent, sm)
    assert d.dismiss_var is not None
    d.dismiss_var.set(True)
    sm.set_setting.assert_called_once_with("experimental.warning_dismissed", True)


def test_dismiss_does_not_write_false_on_uncheck(monkeypatch):
    _patch_warning_tk(monkeypatch)
    d, sm = _make_dialog_stub(False)
    parent = MagicMock()
    d._build_warning_section(parent, sm)
    assert d.dismiss_var is not None
    d.dismiss_var.set(True)
    d.dismiss_var.set(False)
    false_writes = [c for c in sm.set_setting.call_args_list if c.args[1] is False]
    assert false_writes == []
