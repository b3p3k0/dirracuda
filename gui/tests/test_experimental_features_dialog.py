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
from gui.components.experimental_features.dorkbook_tab import DorkbookTab
from gui.components.dashboard import DashboardWidget
from gui.utils.sidecar_promotion import SidecarPromotionError
import gui.components.dashboard_experimental as dashboard_experimental


class _ValueVar:
    def __init__(self) -> None:
        self.value = None

    def set(self, value) -> None:
        self.value = value


class _StatusWidget:
    def __init__(self) -> None:
        self.configure_calls = []
        self.pack_calls = []
        self.pack_forget_calls = 0

    def configure(self, **kwargs) -> None:
        self.configure_calls.append(kwargs)

    def pack(self, **kwargs) -> None:
        self.pack_calls.append(kwargs)

    def pack_forget(self) -> None:
        self.pack_forget_calls += 1


class _FrameWidget:
    def __init__(self) -> None:
        self.after_calls = []

    def winfo_exists(self) -> bool:
        return True

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))
        return "after-id"


def _make_reddit_status_tab(context: dict) -> RedditTab:
    tab = RedditTab.__new__(RedditTab)
    tab._context = context
    tab._status_visible = False
    tab._status_var = _ValueVar()
    tab._grab_btn = _StatusWidget()
    tab._status_label = _StatusWidget()
    tab.frame = _FrameWidget()
    return tab


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
        def grid(self, **kw):
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


def test_reddit_tab_running_status_visible_and_disables_grab():
    """Reddit tab shows a simple running status while Reddit Grab is active."""
    tab = _make_reddit_status_tab({"reddit_grab_status_getter": lambda: True})

    tab._update_running_status()

    assert tab._status_var.value == "Reddit Grab is running..."
    assert tab._grab_btn.configure_calls[-1] == {"state": tk.DISABLED}
    assert tab._status_label.pack_calls
    assert tab._status_visible is True
    assert tab.frame.after_calls


def test_reddit_tab_running_status_clears_and_enables_grab():
    """Reddit tab clears the status line when Reddit Grab is idle."""
    tab = _make_reddit_status_tab({"reddit_grab_status_getter": lambda: False})
    tab._status_visible = True

    tab._update_running_status()

    assert tab._status_var.value == ""
    assert tab._grab_btn.configure_calls[-1] == {"state": tk.NORMAL}
    assert tab._status_label.pack_forget_calls == 1
    assert tab._status_visible is False


def test_reddit_tab_missing_running_getter_is_safe():
    """Missing running-state getter leaves the Reddit status line blank."""
    tab = _make_reddit_status_tab({})

    tab._update_running_status()

    assert tab._status_var.value == ""
    assert tab._grab_btn.configure_calls[-1] == {"state": tk.NORMAL}
    assert tab._status_label.pack_calls == []


def test_dorkbook_callback_invoked_from_tab():
    """_invoke_open_dorkbook must call the context callback."""
    opened = []
    tab = DorkbookTab.__new__(DorkbookTab)
    tab._context = {"open_dorkbook": lambda: opened.append(True)}
    tab._invoke_open_dorkbook()
    assert opened == [True]


def test_dorkbook_tab_silent_when_no_callback():
    """Missing callback should be a no-op, not an exception."""
    tab = DorkbookTab.__new__(DorkbookTab)
    tab._context = {}
    tab._invoke_open_dorkbook()


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
    dash._open_config_editor = MagicMock()
    dash.settings_manager = MagicMock()
    dash.refresh_after_database_change = MagicMock()
    dash.db_reader = MagicMock()
    dash.db_reader.upsert_manual_server_record.return_value = {
        "host_type": "H",
        "protocol_server_id": 3,
        "row_key": "H:3",
        "operation": "insert",
    }
    return dash


def test_experimental_context_includes_reddit_grab_status_getter(monkeypatch):
    """Experimental dialog context exposes the dashboard Reddit Grab running state."""
    dash = _make_dash()
    dash._handle_reddit_grab_button_click = MagicMock()
    dash._open_reddit_post_db = MagicMock()
    dash._reddit_grab_running = True
    captured = {}
    monkeypatch.setattr(
        "gui.components.experimental_features_dialog.show_experimental_features_dialog",
        lambda parent, context, settings_manager: captured.update(
            parent=parent,
            context=context,
            settings_manager=settings_manager,
        ),
    )

    dashboard_experimental.handle_experimental_button_click(dash)

    getter = captured["context"]["reddit_grab_status_getter"]
    assert callable(getter)
    assert getter() is True
    dash._reddit_grab_running = False
    assert getter() is False


def test_open_reddit_post_db_with_live_server_window(monkeypatch):
    """Promotion wiring no longer depends on a live Server List Browser."""
    dash = _make_dash()
    mock_win = MagicMock()
    mock_win.window.winfo_exists.return_value = True
    dash._server_list_getter = MagicMock(return_value=mock_win)

    calls = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_reddit_browser_window",
        lambda **kw: calls.append(kw),
    )

    dashboard_experimental.open_reddit_post_db(dash)

    assert len(calls) == 1
    assert calls[0]["parent"] is dash.parent
    assert calls[0]["add_record_callback"] is None
    assert callable(calls[0]["promote_record_callback"])
    assert callable(calls[0]["promote_records_callback"])
    assert calls[0]["settings_manager"] is dash.settings_manager
    dash._server_list_getter.assert_not_called()

    result = calls[0]["promote_record_callback"]({
        "host_type": "H",
        "host": "1.2.3.4",
        "port": 80,
        "scheme": "http",
    })
    dash.db_reader.upsert_manual_server_record.assert_called_once_with({
        "host_type": "H",
        "ip_address": "1.2.3.4",
        "port": 80,
        "scheme": "http",
    })
    assert result["result"]["row_key"] == "H:3"
    dash.refresh_after_database_change.assert_called_once_with(refresh_runtime_status=False)


def test_sidecar_bulk_promotion_callback_refreshes_once(monkeypatch):
    dash = _make_dash()
    monkeypatch.setattr(
        dashboard_experimental,
        "promote_sidecar_prefills",
        MagicMock(
            return_value={
                "selected": 2,
                "processed": 2,
                "inserted": 1,
                "updated": 1,
                "skipped": 0,
                "failed": 0,
                "cancelled": 0,
                "skipped_reason_samples": [],
                "failed_reason_samples": [],
            }
        ),
    )
    callback = dashboard_experimental._make_sidecar_bulk_promote_callback(dash)

    result = callback(
        [
            {"host_type": "H", "host": "1.2.3.4", "port": 80, "scheme": "http"},
            {"host_type": "H", "host": "1.2.3.5", "port": 80, "scheme": "http"},
        ]
    )

    assert result["selected"] == 2
    dash.refresh_after_database_change.assert_called_once_with(refresh_runtime_status=False)


def test_sidecar_promotion_callback_does_not_refresh_when_promotion_fails(monkeypatch):
    dash = _make_dash()
    monkeypatch.setattr(
        dashboard_experimental,
        "promote_sidecar_prefill",
        MagicMock(side_effect=SidecarPromotionError("cannot promote")),
    )

    callback = dashboard_experimental._make_sidecar_promote_callback(dash)

    try:
        callback({"host_type": "H", "host": "example.invalid"})
    except SidecarPromotionError:
        pass

    dash.refresh_after_database_change.assert_not_called()


def test_open_reddit_post_db_fallback_when_no_server_window(monkeypatch):
    """Getter=None path still opens with direct promotion wiring."""
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
    assert callable(calls[0]["promote_record_callback"])
    assert callable(calls[0]["promote_records_callback"])
    assert calls[0]["settings_manager"] is dash.settings_manager
    dash._open_drill_down.assert_not_called()
    dash._server_list_getter.assert_not_called()


def test_open_reddit_post_db_treats_dead_window_as_none(monkeypatch):
    """Dead windows are irrelevant to direct sidecar promotion wiring."""
    dash = _make_dash()
    mock_win = MagicMock()
    mock_win.window.winfo_exists.return_value = False
    dash._server_list_getter = MagicMock(return_value=mock_win)
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
    assert callable(calls[0]["promote_record_callback"])
    assert callable(calls[0]["promote_records_callback"])
    assert calls[0]["settings_manager"] is dash.settings_manager
    dash._open_drill_down.assert_not_called()
    dash._server_list_getter.assert_not_called()


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
    assert callable(calls[0]["promote_record_callback"])
    assert callable(calls[0]["promote_records_callback"])
    assert calls[0]["settings_manager"] is dash.settings_manager
    dash._open_drill_down.assert_not_called()
    dash._server_list_getter.assert_not_called()


def test_open_reddit_post_db_fallback_when_getter_raises(monkeypatch):
    """Getter exceptions cannot block direct sidecar promotion wiring."""
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
    assert callable(calls[0]["promote_record_callback"])
    assert callable(calls[0]["promote_records_callback"])
    assert calls[0]["settings_manager"] is dash.settings_manager
    dash._open_drill_down.assert_not_called()
    assert getter_calls["count"] == 0
    assert log_warnings == []


def test_open_reddit_post_db_without_db_reader_has_no_promote_callback(monkeypatch):
    """Browser still opens, but action reports DB unavailability from the browser."""
    dash = _make_dash()
    dash.db_reader = None

    calls = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_reddit_browser_window",
        lambda **kw: calls.append(kw),
    )

    dashboard_experimental.open_reddit_post_db(dash)

    assert len(calls) == 1
    assert calls[0]["parent"] is dash.parent
    assert calls[0]["add_record_callback"] is None
    assert calls[0]["promote_record_callback"] is None
    assert calls[0]["promote_records_callback"] is None
    assert calls[0]["settings_manager"] is dash.settings_manager


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


# ---------------------------------------------------------------------------
# C1 — Tab registry assertions
# ---------------------------------------------------------------------------

def test_registry_contains_searxng_tab():
    from gui.components.experimental_features.registry import _get_features
    labels = [f.label for f in _get_features()]
    assert "SearXNG" in labels


def test_registry_does_not_contain_placeholder_tab():
    from gui.components.experimental_features.registry import _get_features
    labels = [f.label for f in _get_features()]
    assert "placeholder" not in labels


def test_registry_reddit_tab_unchanged():
    from gui.components.experimental_features.registry import _get_features
    labels = [f.label for f in _get_features()]
    assert "Reddit" in labels


def test_registry_contains_dorkbook_tab():
    from gui.components.experimental_features.registry import _get_features
    labels = [f.label for f in _get_features()]
    assert "Dorkbook" in labels


def test_registry_se_dork_feature_id():
    from gui.components.experimental_features.registry import _get_features
    ids = [f.feature_id for f in _get_features()]
    assert "se_dork" in ids


def test_registry_orders_se_dork_before_reddit():
    from gui.components.experimental_features.registry import _get_features

    features = _get_features()
    assert features[0].feature_id == "se_dork"
    assert features[1].feature_id == "reddit"


def test_registry_dorkbook_after_reddit():
    from gui.components.experimental_features.registry import _get_features

    features = _get_features()
    assert features[2].feature_id == "dorkbook"


# ---------------------------------------------------------------------------
# C4 — open_se_dork_results_db path resolution
# ---------------------------------------------------------------------------


def test_open_se_dork_results_db_with_live_server_window(monkeypatch):
    """SE Dork promotion wiring does not depend on a live Server List Browser."""
    dash = _make_dash()
    mock_win = MagicMock()
    mock_win.window.winfo_exists.return_value = True
    dash._server_list_getter = MagicMock(return_value=mock_win)

    calls = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_se_dork_browser_window",
        lambda **kw: calls.append(kw),
    )

    dashboard_experimental.open_se_dork_results_db(dash)

    assert len(calls) == 1
    assert calls[0]["parent"] is dash.parent
    assert calls[0]["add_record_callback"] is None
    assert callable(calls[0]["promote_record_callback"])
    assert callable(calls[0]["promote_records_callback"])
    assert calls[0]["settings_manager"] is dash.settings_manager
    dash._server_list_getter.assert_not_called()

    calls[0]["promote_record_callback"]({
        "host_type": "H",
        "host": "1.2.3.4",
        "port": 80,
        "scheme": "http",
    })
    dash.refresh_after_database_change.assert_called_once_with(refresh_runtime_status=False)


def test_open_se_dork_results_db_fallback_when_no_server_window(monkeypatch):
    """Getter=None still opens with direct promotion wiring."""
    dash = _make_dash()
    dash._server_list_getter = None

    calls = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_se_dork_browser_window",
        lambda **kw: calls.append(kw),
    )

    dashboard_experimental.open_se_dork_results_db(dash)

    assert len(calls) == 1
    assert calls[0]["parent"] is dash.parent
    assert calls[0]["add_record_callback"] is None
    assert callable(calls[0]["promote_record_callback"])
    assert callable(calls[0]["promote_records_callback"])
    assert calls[0]["settings_manager"] is dash.settings_manager


def test_open_se_dork_results_db_treats_dead_window_as_none(monkeypatch):
    """Dead windows are irrelevant to direct promotion wiring."""
    dash = _make_dash()
    mock_win = MagicMock()
    mock_win.window.winfo_exists.return_value = False
    dash._server_list_getter = MagicMock(return_value=mock_win)

    calls = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_se_dork_browser_window",
        lambda **kw: calls.append(kw),
    )

    dashboard_experimental.open_se_dork_results_db(dash)

    assert len(calls) == 1
    assert calls[0]["parent"] is dash.parent
    assert calls[0]["add_record_callback"] is None
    assert callable(calls[0]["promote_record_callback"])
    assert callable(calls[0]["promote_records_callback"])
    assert calls[0]["settings_manager"] is dash.settings_manager
    dash._server_list_getter.assert_not_called()


def test_open_se_dork_results_db_fallback_when_getter_raises(monkeypatch):
    """Getter exceptions cannot block direct promotion wiring."""
    dash = _make_dash()
    getter_calls = {"count": 0}

    def _boom():
        getter_calls["count"] += 1
        raise RuntimeError("getter boom")

    dash._server_list_getter = _boom

    calls = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_se_dork_browser_window",
        lambda **kw: calls.append(kw),
    )

    dashboard_experimental.open_se_dork_results_db(dash)

    assert len(calls) == 1
    assert calls[0]["parent"] is dash.parent
    assert calls[0]["add_record_callback"] is None
    assert callable(calls[0]["promote_record_callback"])
    assert callable(calls[0]["promote_records_callback"])
    assert calls[0]["settings_manager"] is dash.settings_manager
    assert getter_calls["count"] == 0


def test_open_dorkbook_forwards_parent_and_settings_manager(monkeypatch):
    dash = _make_dash()
    calls = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_dorkbook_window",
        lambda **kw: calls.append(kw),
    )

    dashboard_experimental.open_dorkbook(dash)

    assert len(calls) == 1
    assert calls[0]["parent"] is dash.parent
    assert calls[0]["settings_manager"] is dash.settings_manager


# ---------------------------------------------------------------------------
# C4 — se_dork_tab._open_results_browser wiring
# ---------------------------------------------------------------------------


def test_se_dork_tab_open_results_invokes_context_callback():
    """_open_results_browser calls the context callback when present."""
    from gui.components.experimental_features.se_dork_tab import SeDorkTab

    called = []
    tab = SeDorkTab.__new__(SeDorkTab)
    tab._context = {"open_se_dork_results_db": lambda: called.append(True)}
    tab._open_results_browser()
    assert called == [True]


def test_se_dork_tab_fallback_opens_browser_when_no_results_callback(monkeypatch):
    """When context has no open_se_dork_results_db, browser opens with callback=None."""
    from gui.components.experimental_features.se_dork_tab import SeDorkTab

    calls = []
    monkeypatch.setattr(
        "gui.components.se_dork_browser_window.show_se_dork_browser_window",
        lambda *a, **kw: calls.append(kw),
    )

    tab = SeDorkTab.__new__(SeDorkTab)
    tab._context = {}
    tab.frame = MagicMock()
    tab._open_results_browser()

    assert len(calls) == 1
    assert calls[0].get("add_record_callback") is None
    assert calls[0].get("settings_manager") is None


# ---------------------------------------------------------------------------
# C4 — Keymaster tab registry + wiring
# ---------------------------------------------------------------------------

def test_registry_contains_keymaster_tab():
    from gui.components.experimental_features.registry import _get_features
    labels = [f.label for f in _get_features()]
    assert "Keymaster" in labels


def test_registry_keymaster_feature_id():
    from gui.components.experimental_features.registry import _get_features
    ids = [f.feature_id for f in _get_features()]
    assert "keymaster" in ids


def test_registry_keymaster_after_dorkbook():
    from gui.components.experimental_features.registry import _get_features
    features = _get_features()
    assert features[3].feature_id == "keymaster"


def test_keymaster_tab_callback_invoked():
    """_invoke_open_keymaster must call the context callback."""
    from gui.components.experimental_features.keymaster_tab import KeymasterTab
    opened = []
    tab = KeymasterTab.__new__(KeymasterTab)
    tab._context = {"open_keymaster": lambda: opened.append(True)}
    tab._invoke_open_keymaster()
    assert opened == [True]


def test_keymaster_tab_silent_when_no_callback():
    """Missing open_keymaster key is a no-op, not an exception."""
    from gui.components.experimental_features.keymaster_tab import KeymasterTab
    tab = KeymasterTab.__new__(KeymasterTab)
    tab._context = {}
    tab._invoke_open_keymaster()  # must not raise


def test_open_keymaster_forwards_parent_settings_config(monkeypatch):
    """open_keymaster passes parent, settings_manager, and resolved config_path."""
    dash = _make_dash()
    resolved_path = MagicMock()
    dash._resolve_active_config_path = MagicMock(return_value=resolved_path)

    calls = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_keymaster_window",
        lambda **kw: calls.append(kw),
    )

    dashboard_experimental.open_keymaster(dash)

    assert len(calls) == 1
    assert calls[0]["parent"] is dash.parent
    assert calls[0]["settings_manager"] is dash.settings_manager
    assert calls[0]["config_path"] == str(resolved_path)


# ---------------------------------------------------------------------------
# C2 — Censys Discovery tab registration and wiring
# ---------------------------------------------------------------------------

# Sub-group A: Registry

def test_registry_contains_censys_discovery_tab():
    from gui.components.experimental_features.registry import _get_features
    labels = [f.label for f in _get_features()]
    assert "Censys Discovery" in labels


def test_registry_censys_discovery_feature_id():
    from gui.components.experimental_features.registry import _get_features
    ids = [f.feature_id for f in _get_features()]
    assert "censys_discovery" in ids


def test_registry_censys_discovery_after_keymaster():
    from gui.components.experimental_features.registry import _get_features
    ids = [f.feature_id for f in _get_features()]
    assert ids.index("censys_discovery") > ids.index("keymaster")


# Sub-group B: Build (dummy widget pattern — no display required)

def _make_censys_build_fixtures():
    """Return (texts, configure_states, _DummyVar, _DummyWidget) for _build() inspection."""
    texts = []
    configure_states = []

    class _DummyVar:
        def __init__(self, value=None):
            self._value = value

        def get(self):
            return self._value

        def set(self, v):
            self._value = v

    class _DummyWidget:
        def __init__(self, *a, **kw):
            self._text = kw.get("text", "")
            if self._text:
                texts.append(self._text)

        def pack(self, *a, **kw):
            return None

        def configure(self, **kw):
            if "state" in kw:
                configure_states.append((self._text, kw["state"]))

        def bind(self, *a, **kw):
            return None

    return texts, configure_states, _DummyVar, _DummyWidget


def _run_censys_build(monkeypatch, texts, configure_states, _DummyVar, _DummyWidget):
    """Monkeypatch tk widgets in censys_discovery_tab, construct tab, call _build()."""
    import gui.components.experimental_features.censys_discovery_tab as tab_mod
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab

    monkeypatch.setattr(tab_mod.tk, "Frame", _DummyWidget)
    monkeypatch.setattr(tab_mod.tk, "Label", _DummyWidget)
    monkeypatch.setattr(tab_mod.tk, "Button", _DummyWidget)
    monkeypatch.setattr(tab_mod.tk, "Checkbutton", _DummyWidget)
    monkeypatch.setattr(tab_mod.tk, "BooleanVar", _DummyVar)

    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab._context = {}
    tab._cfg = None
    tab._protocol_vars = {}
    tab._protocol_checkbuttons = []
    tab._theme = MagicMock()
    tab._theme.apply_to_widget = lambda *a, **kw: None
    tab._build(_DummyWidget())


def test_censys_discovery_tab_build_scaffold_texts_present(monkeypatch):
    from gui.components.experimental_features.censys_discovery_tab import (
        _PLACEHOLDER_STATUS, _PLACEHOLDER_CREDIT, _PLACEHOLDER_BALANCE,
    )
    texts, configure_states, _DummyVar, _DummyWidget = _make_censys_build_fixtures()
    _run_censys_build(monkeypatch, texts, configure_states, _DummyVar, _DummyWidget)

    assert _PLACEHOLDER_STATUS in texts
    assert _PLACEHOLDER_CREDIT in texts
    assert _PLACEHOLDER_BALANCE in texts
    assert "Run" in texts
    assert "Config" in texts
    assert "Open Results" in texts
    assert "FTP" in texts
    assert "HTTP" in texts
    assert "SMB" in texts


def test_censys_discovery_tab_build_run_button_is_enabled(monkeypatch):
    texts, configure_states, _DummyVar, _DummyWidget = _make_censys_build_fixtures()
    _run_censys_build(monkeypatch, texts, configure_states, _DummyVar, _DummyWidget)
    assert ("Run", "normal") in configure_states


def test_censys_discovery_tab_build_open_results_button_is_disabled(monkeypatch):
    texts, configure_states, _DummyVar, _DummyWidget = _make_censys_build_fixtures()
    _run_censys_build(monkeypatch, texts, configure_states, _DummyVar, _DummyWidget)
    assert ("Open Results", "disabled") in configure_states


# Sub-group C: Callback wiring

def test_censys_discovery_tab_open_results_invokes_context_callback():
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab
    called = []
    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab._context = {"open_censys_results_db": lambda: called.append(True)}
    tab._invoke_open_results()
    assert called == [True]


def test_censys_discovery_tab_open_config_invokes_context_callback():
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab

    called = []
    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab._context = {"open_app_config": lambda: called.append(True)}
    tab._invoke_open_config()
    assert called == [True]


def test_censys_discovery_tab_silent_when_no_results_callback():
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab
    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab._context = {}
    tab._invoke_open_results()  # must not raise


def test_censys_discovery_tab_invoke_run_requires_selected_protocol():
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab

    calls = []

    class _Var:
        def __init__(self, value):
            self._value = value

        def get(self):
            return self._value

    class _Label:
        def configure(self, **kw):
            calls.append(kw.get("text", ""))

    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab._context = {}
    tab._protocol_vars = {"FTP": _Var(False), "HTTP": _Var(False), "SMB": _Var(False)}
    tab._status_label = _Label()

    tab._invoke_run()

    assert calls
    assert "Select at least one protocol" in calls[-1]


# ---------------------------------------------------------------------------
# C8 — open_censys_results_db wiring
# ---------------------------------------------------------------------------


def test_experimental_context_includes_open_censys_results_db(monkeypatch):
    """handle_experimental_button_click context exposes open_censys_results_db."""
    dash = _make_dash()
    dash._handle_reddit_grab_button_click = MagicMock()
    dash._open_reddit_post_db = MagicMock()
    captured = {}
    monkeypatch.setattr(
        "gui.components.experimental_features_dialog.show_experimental_features_dialog",
        lambda parent, context, settings_manager: captured.update(
            context=context,
        ),
    )

    dashboard_experimental.handle_experimental_button_click(dash)

    assert "open_censys_results_db" in captured["context"]
    assert callable(captured["context"]["open_censys_results_db"])


def test_experimental_context_includes_open_app_config(monkeypatch):
    """handle_experimental_button_click context exposes open_app_config."""
    dash = _make_dash()
    dash._handle_reddit_grab_button_click = MagicMock()
    dash._open_reddit_post_db = MagicMock()
    dash._open_config_editor = MagicMock()
    captured = {}
    monkeypatch.setattr(
        "gui.components.experimental_features_dialog.show_experimental_features_dialog",
        lambda parent, context, settings_manager: captured.update(
            context=context,
        ),
    )

    dashboard_experimental.handle_experimental_button_click(dash)

    assert "open_app_config" in captured["context"]
    assert callable(captured["context"]["open_app_config"])


def test_open_censys_results_db_calls_show_censys_browser_window(monkeypatch):
    """open_censys_results_db opens the Censys browser with expected args."""
    dash = _make_dash()

    calls = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_censys_browser_window",
        lambda **kw: calls.append(kw),
    )

    dashboard_experimental.open_censys_results_db(dash)

    assert len(calls) == 1
    assert calls[0]["parent"] is dash.parent
    assert calls[0]["add_record_callback"] is None
    assert callable(calls[0]["promote_record_callback"])
    assert callable(calls[0]["promote_records_callback"])
    assert calls[0]["settings_manager"] is dash.settings_manager


def test_open_censys_results_db_does_not_call_server_list_getter(monkeypatch):
    """open_censys_results_db must not touch _server_list_getter."""
    dash = _make_dash()
    dash._server_list_getter = MagicMock()

    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_censys_browser_window",
        lambda **kw: None,
    )

    dashboard_experimental.open_censys_results_db(dash)

    dash._server_list_getter.assert_not_called()


def test_open_censys_results_db_without_db_reader_has_no_promote_callback(monkeypatch):
    """Browser still opens, but promote callbacks are None when DB unavailable."""
    dash = _make_dash()
    dash.db_reader = None

    calls = []
    monkeypatch.setattr(
        "gui.components.dashboard_experimental.show_censys_browser_window",
        lambda **kw: calls.append(kw),
    )

    dashboard_experimental.open_censys_results_db(dash)

    assert len(calls) == 1
    assert calls[0]["promote_record_callback"] is None
    assert calls[0]["promote_records_callback"] is None


# ---------------------------------------------------------------------------
# C8 — Censys tab Open Results button state
# ---------------------------------------------------------------------------


def test_censys_discovery_tab_build_open_results_button_enabled_when_callback_present(monkeypatch):
    """Open Results button is NORMAL when context has the callback."""
    texts, configure_states, _DummyVar, _DummyWidget = _make_censys_build_fixtures()

    import gui.components.experimental_features.censys_discovery_tab as tab_mod
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab

    monkeypatch.setattr(tab_mod.tk, "Frame", _DummyWidget)
    monkeypatch.setattr(tab_mod.tk, "Label", _DummyWidget)
    monkeypatch.setattr(tab_mod.tk, "Button", _DummyWidget)
    monkeypatch.setattr(tab_mod.tk, "Checkbutton", _DummyWidget)
    monkeypatch.setattr(tab_mod.tk, "BooleanVar", _DummyVar)

    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab._context = {"open_censys_results_db": lambda: None}
    tab._cfg = None
    tab._protocol_vars = {}
    tab._protocol_checkbuttons = []
    tab._theme = MagicMock()
    tab._theme.apply_to_widget = lambda *a, **kw: None
    tab._build(_DummyWidget())

    assert ("Open Results", "normal") in configure_states


def test_censys_discovery_tab_build_open_results_button_disabled_when_no_callback(monkeypatch):
    """Open Results button is DISABLED when context has no callback (existing test updated)."""
    texts, configure_states, _DummyVar, _DummyWidget = _make_censys_build_fixtures()
    _run_censys_build(monkeypatch, texts, configure_states, _DummyVar, _DummyWidget)
    assert ("Open Results", "disabled") in configure_states


# ---------------------------------------------------------------------------
# C9 — Credit UX helpers and live balance
# ---------------------------------------------------------------------------


def test_censys_credit_estimate_shows_profile_from_config():
    from gui.components.experimental_features.censys_discovery_tab import _credit_estimate_text
    cfg = MagicMock()
    cfg.get_censys_credit_profile.return_value = "free_starter"
    result = _credit_estimate_text(cfg)
    assert "free_starter" in result


def test_censys_credit_estimate_falls_back_when_cfg_is_none():
    from gui.components.experimental_features.censys_discovery_tab import (
        _credit_estimate_text, _PLACEHOLDER_CREDIT,
    )
    assert _credit_estimate_text(None) == _PLACEHOLDER_CREDIT


def test_censys_status_text_shows_configured_when_pat_set():
    from gui.components.experimental_features.censys_discovery_tab import _status_text
    cfg = MagicMock()
    cfg.get_censys_pat.return_value = "pat-abc123"
    result = _status_text(cfg)
    assert "configured" in result
    assert "not configured" not in result


def test_censys_status_text_shows_placeholder_when_no_pat():
    from gui.components.experimental_features.censys_discovery_tab import (
        _status_text, _PLACEHOLDER_STATUS,
    )
    cfg = MagicMock()
    cfg.get_censys_pat.side_effect = ValueError("PAT not set")
    assert _status_text(cfg) == _PLACEHOLDER_STATUS


def test_censys_fetch_balance_worker_updates_label_on_success(monkeypatch):
    from experimental.censys_discovery.models import CreditBalance, ClientResult
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab

    configure_calls = []

    class _FakeLabel:
        def configure(self, **kw):
            configure_calls.append(kw.get("text", ""))

    class _FakeFrame:
        after = staticmethod(lambda _ms, cb: cb())

    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab._balance_label = _FakeLabel()
    tab.frame = _FakeFrame()

    monkeypatch.setattr(
        "experimental.censys_discovery.client.CensysClient",
        lambda pat: MagicMock(
            get_user_credits=lambda: ClientResult(
                ok=True, data=CreditBalance(balance=42, resets_at=None), error=None
            )
        ),
    )

    tab._fetch_balance_worker("fake-pat", None)

    assert len(configure_calls) == 1
    assert "42" in configure_calls[0]
    assert "fake-pat" not in configure_calls[0]


def test_censys_fetch_balance_worker_shows_fallback_on_error(monkeypatch):
    from experimental.censys_discovery.models import ClientResult, ApiError
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab

    configure_calls = []

    class _FakeLabel:
        def configure(self, **kw):
            configure_calls.append(kw.get("text", ""))

    class _FakeFrame:
        after = staticmethod(lambda _ms, cb: cb())

    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab._balance_label = _FakeLabel()
    tab.frame = _FakeFrame()

    monkeypatch.setattr(
        "experimental.censys_discovery.client.CensysClient",
        lambda pat: MagicMock(
            get_user_credits=lambda: ClientResult(
                ok=False,
                data=None,
                error=ApiError(
                    reason_code="AUTH_UNAUTHORIZED",
                    status_code=401,
                    message="bad token",
                ),
            )
        ),
    )

    tab._fetch_balance_worker("fake-pat", None)

    assert len(configure_calls) == 1
    assert "unavailable" in configure_calls[0]
    assert "fake-pat" not in configure_calls[0]


def test_censys_refresh_balance_skips_when_cfg_is_none(monkeypatch):
    import threading as _threading
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab

    started = []
    original_start = _threading.Thread.start

    def _fake_start(self):
        started.append(True)

    monkeypatch.setattr(_threading.Thread, "start", _fake_start)

    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab._cfg = None
    tab._refresh_balance()

    assert started == []


def test_censys_refresh_balance_sets_label_when_no_pat():
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab

    configure_calls = []

    class _FakeLabel:
        def configure(self, **kw):
            configure_calls.append(kw.get("text", ""))

    cfg = MagicMock()
    cfg.get_censys_pat.side_effect = ValueError("not set")

    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab._cfg = cfg
    tab._balance_label = _FakeLabel()
    tab._refresh_balance()

    assert len(configure_calls) == 1
    assert "PAT not configured" in configure_calls[0]


def test_censys_refresh_balance_sets_label_on_invalid_org_id():
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab

    configure_calls = []

    class _FakeLabel:
        def configure(self, **kw):
            configure_calls.append(kw.get("text", ""))

    cfg = MagicMock()
    cfg.get_censys_pat.return_value = "valid-pat"
    cfg.get_censys_org_id.side_effect = ValueError("bad UUID")

    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab._cfg = cfg
    tab._balance_label = _FakeLabel()
    tab._refresh_balance()

    assert len(configure_calls) == 1
    assert "invalid organization_id" in configure_calls[0]


def test_censys_invoke_run_shows_pat_not_configured_when_missing():
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab

    messages = []

    class _Var:
        def __init__(self, value):
            self._value = value

        def get(self):
            return self._value

    class _Label:
        def configure(self, **kw):
            messages.append(kw.get("text", ""))

    cfg = MagicMock()
    cfg.get_censys_pat.side_effect = ValueError("missing")

    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab._context = {}
    tab._protocol_vars = {"FTP": _Var(True), "HTTP": _Var(False), "SMB": _Var(False)}
    tab._status_label = _Label()
    tab._load_config_from_settings = lambda: cfg

    tab._invoke_run()

    assert messages
    assert "PAT is not configured" in messages[-1]


def test_censys_invoke_run_shows_config_unavailable_when_cfg_missing():
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab

    messages = []

    class _Var:
        def __init__(self, value):
            self._value = value

        def get(self):
            return self._value

    class _Label:
        def configure(self, **kw):
            messages.append(kw.get("text", ""))

    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab._context = {}
    tab._protocol_vars = {"FTP": _Var(True), "HTTP": _Var(False), "SMB": _Var(False)}
    tab._status_label = _Label()
    tab._load_config_from_settings = lambda: None

    tab._invoke_run()

    assert messages
    assert "configuration unavailable" in messages[-1]


def test_censys_run_stack_worker_calls_protocols_in_order_and_stops_on_failure(monkeypatch):
    from experimental.censys_discovery.models import CensysRunResult, RUN_STATUS_DONE, RUN_STATUS_ERROR
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab

    call_order = []
    captured = {}

    def _run_ftp(_options):
        call_order.append("FTP")
        return CensysRunResult(
            ok=True,
            run_id=1,
            fetched_count=5,
            deduped_count=4,
            status=RUN_STATUS_DONE,
            error=None,
        )

    def _run_http(_options):
        call_order.append("HTTP")
        return CensysRunResult(
            ok=False,
            run_id=2,
            fetched_count=3,
            deduped_count=3,
            status=RUN_STATUS_ERROR,
            error="boom",
        )

    def _run_smb(_options):
        call_order.append("SMB")
        raise AssertionError("SMB should not be called after HTTP failure")

    monkeypatch.setattr("experimental.censys_discovery.service.run_ftp_discovery", _run_ftp)
    monkeypatch.setattr("experimental.censys_discovery.service.run_http_discovery", _run_http)
    monkeypatch.setattr("experimental.censys_discovery.service.run_smb_discovery", _run_smb)

    class _Frame:
        after = staticmethod(lambda _ms, cb: cb())

    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab.frame = _Frame()
    tab._on_run_stack_done = lambda summaries, failed_protocol, failed_message: captured.update(
        summaries=summaries,
        failed_protocol=failed_protocol,
        failed_message=failed_message,
    )

    tab._run_stack_worker(["FTP", "HTTP", "SMB"], "pat", None, 24, 5, 100)

    assert call_order == ["FTP", "HTTP"]
    assert captured["failed_protocol"] == "HTTP"
    assert captured["failed_message"] == "boom"
    assert len(captured["summaries"]) == 2


def test_censys_on_run_stack_done_formats_partial_failure_summary():
    from experimental.censys_discovery.models import CensysRunResult, RUN_STATUS_DONE
    from gui.components.experimental_features.censys_discovery_tab import CensysDiscoveryTab

    messages = []
    running_flags = []
    refreshed = []

    class _Label:
        def configure(self, **kw):
            messages.append(kw.get("text", ""))

    tab = CensysDiscoveryTab.__new__(CensysDiscoveryTab)
    tab._status_label = _Label()
    tab._set_controls_running = lambda running: running_flags.append(running)
    tab._load_config_from_settings = lambda: None
    tab._refresh_balance = lambda: refreshed.append(True)

    ok_result = CensysRunResult(
        ok=True,
        run_id=1,
        fetched_count=4,
        deduped_count=3,
        status=RUN_STATUS_DONE,
        error=None,
    )
    tab._on_run_stack_done([("FTP", ok_result)], "HTTP", "network down")

    assert messages
    assert "Run failed at HTTP: network down" in messages[-1]
    assert "Completed: FTP: fetched 4, stored 3" in messages[-1]
    assert running_flags == [False]
    assert refreshed == [True]
