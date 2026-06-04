"""
Headless unit tests for RedditGrabDialog.

Uses RedditGrabDialog.__new__ to skip tkinter construction entirely.
All tk vars and widgets are replaced by MagicMock — no display, no Xvfb needed.
Matches the __new__ + MagicMock pattern established in test_experimental_features_dialog.py.
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

from gui.components.reddit_grab_dialog import RedditGrabDialog  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

def _make_dialog() -> RedditGrabDialog:
    """Minimal RedditGrabDialog with all tk vars/widgets replaced by MagicMocks."""
    d = RedditGrabDialog.__new__(RedditGrabDialog)
    d.mode_var = MagicMock()
    d.mode_var.get.return_value = "feed"
    d.query_var = MagicMock()
    d.query_var.get.return_value = ""
    d._query_lbl = MagicMock()
    d._query_entry = MagicMock()
    d.sort_var = MagicMock()
    d.sort_var.get.return_value = "new"
    d.top_window_var = MagicMock()
    d.top_window_var.get.return_value = "week"
    d._top_window_cb = MagicMock()
    d.max_posts_var = MagicMock()
    d.max_posts_var.get.return_value = "50"
    d.parse_body_var = MagicMock()
    d.parse_body_var.get.return_value = True
    d.include_nsfw_var = MagicMock()
    d.include_nsfw_var.get.return_value = False
    d.replace_cache_var = MagicMock()
    d.replace_cache_var.get.return_value = False
    d.bulk_probe_var = MagicMock()
    d.bulk_probe_var.get.return_value = False
    d.dialog = MagicMock()
    d.settings = None
    return d


# ---------------------------------------------------------------------------
# _on_sort_changed — enable/disable lifecycle
# ---------------------------------------------------------------------------

def test_top_window_combobox_disabled_when_sort_new():
    """sort=new disables the top-window combobox."""
    d = _make_dialog()
    d.sort_var.get.return_value = "new"
    d._on_sort_changed()
    d._top_window_cb.configure.assert_called_with(state="disabled")


def test_top_window_combobox_enabled_when_sort_top():
    """sort=top enables the top-window combobox as readonly."""
    d = _make_dialog()
    d.sort_var.get.return_value = "top"
    d._on_sort_changed()
    d._top_window_cb.configure.assert_called_with(state="readonly")


def test_top_window_resets_to_week_when_sort_switched_back_to_new():
    """Switching back from top to new resets top_window_var to 'week'."""
    d = _make_dialog()
    d.sort_var.get.return_value = "new"
    d._on_sort_changed()
    d.top_window_var.set.assert_called_with("week")
    d._top_window_cb.configure.assert_called_with(state="disabled")


# ---------------------------------------------------------------------------
# _validate — IngestOptions.top_window propagation
# ---------------------------------------------------------------------------

def test_validate_passes_top_window_in_ingest_options():
    """_validate returns IngestOptions with top_window matching the combobox value."""
    d = _make_dialog()
    d.sort_var.get.return_value = "top"
    d.top_window_var.get.return_value = "month"
    result = d._validate()
    assert result is not None
    assert result.top_window == "month"


def test_validate_top_window_defaults_to_week_for_top_sort():
    """_validate returns top_window='week' when top-window combobox not changed."""
    d = _make_dialog()
    d.sort_var.get.return_value = "top"
    d.top_window_var.get.return_value = "week"
    result = d._validate()
    assert result is not None
    assert result.top_window == "week"


def test_validate_new_sort_carries_top_window_field():
    """sort=new still propagates top_window (service ignores it for new mode)."""
    d = _make_dialog()
    d.sort_var.get.return_value = "new"
    d.top_window_var.get.return_value = "week"
    result = d._validate()
    assert result is not None
    assert result.sort == "new"
    assert result.top_window == "week"


def test_validate_passes_bulk_probe_flag():
    d = _make_dialog()
    d.bulk_probe_var.get.return_value = True
    result = d._validate()
    assert result is not None
    assert result.bulk_probe_enabled is True


# ---------------------------------------------------------------------------
# search mode — _on_mode_changed visibility
# ---------------------------------------------------------------------------

def test_mode_search_shows_query_widgets():
    """mode=search calls grid() on query label and entry."""
    d = _make_dialog()
    d.mode_var.get.return_value = "search"
    d._on_mode_changed()
    d._query_lbl.grid.assert_called_once()
    d._query_entry.grid.assert_called_once()


def test_mode_feed_hides_query_widgets():
    """mode=feed calls grid_remove() on query label and entry."""
    d = _make_dialog()
    d.mode_var.get.return_value = "feed"
    d._on_mode_changed()
    d._query_lbl.grid_remove.assert_called_once()
    d._query_entry.grid_remove.assert_called_once()


# ---------------------------------------------------------------------------
# search mode — _validate
# ---------------------------------------------------------------------------

def test_validate_search_mode_empty_query_returns_none(monkeypatch):
    """Empty search query shows showerror and returns None."""
    calls = []
    monkeypatch.setattr(
        "gui.components.reddit_grab_dialog.messagebox.showerror",
        lambda *a, **kw: calls.append(a),
    )
    d = _make_dialog()
    d.mode_var.get.return_value = "search"
    d.query_var.get.return_value = ""
    result = d._validate()
    assert result is None
    assert len(calls) == 1


def test_validate_search_mode_nonempty_query_returns_options():
    """Non-empty search query passes validation and propagates to IngestOptions."""
    d = _make_dialog()
    d.mode_var.get.return_value = "search"
    d.query_var.get.return_value = "ftp files"
    result = d._validate()
    assert result is not None
    assert result.mode == "search"
    assert result.query == "ftp files"


def test_validate_feed_mode_returns_mode_feed_query_empty():
    """feed mode returns IngestOptions with mode='feed' and query=''."""
    d = _make_dialog()
    d.mode_var.get.return_value = "feed"
    result = d._validate()
    assert result is not None
    assert result.mode == "feed"
    assert result.query == ""


def test_mode_feed_hides_query():
    """mode=feed hides query widgets."""
    d = _make_dialog()
    d.mode_var.get.return_value = "feed"
    d._on_mode_changed()
    d._query_lbl.grid_remove.assert_called_once()
    d._query_entry.grid_remove.assert_called_once()


def test_mode_search_shows_query():
    """mode=search shows query widgets."""
    d = _make_dialog()
    d.mode_var.get.return_value = "search"
    d._on_mode_changed()
    d._query_lbl.grid.assert_called_once()
    d._query_entry.grid.assert_called_once()


# ---------------------------------------------------------------------------
# discontinued user mode — _validate
# ---------------------------------------------------------------------------

def test_validate_user_mode_returns_none(monkeypatch):
    """mode=user is no longer supported by anonymous RSS mode."""
    calls = []
    monkeypatch.setattr(
        "gui.components.reddit_grab_dialog.messagebox.showerror",
        lambda *a, **kw: calls.append(a),
    )
    d = _make_dialog()
    d.mode_var.get.return_value = "user"
    result = d._validate()
    assert result is None
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Settings persistence — _load_settings
# ---------------------------------------------------------------------------

def _make_settings_mock(overrides: dict):
    """Return a mock settings_manager whose get_setting returns overrides or default."""
    sm = MagicMock()
    sm.get_setting.side_effect = lambda key, default=None: overrides.get(key, default)
    return sm


def test_load_settings_no_op_when_settings_none():
    """With settings=None, _load_settings does not call set on any var."""
    d = _make_dialog()
    d.settings = None
    d._load_settings()
    d.mode_var.set.assert_not_called()
    d.sort_var.set.assert_not_called()


def test_load_settings_restores_mode():
    """Stored mode=search is loaded into mode_var."""
    d = _make_dialog()
    d.settings = _make_settings_mock({'reddit_grab.mode': 'search'})
    d._load_settings()
    d.mode_var.set.assert_called_with('search')


def test_load_settings_restores_sort_and_top_window():
    """Stored sort=top and top_window=month are both loaded."""
    d = _make_dialog()
    d.settings = _make_settings_mock({
        'reddit_grab.sort': 'top',
        'reddit_grab.top_window': 'month',
    })
    d._load_settings()
    d.sort_var.set.assert_called_with('top')
    d.top_window_var.set.assert_called_with('month')


def test_load_settings_invalid_mode_falls_back_to_feed():
    """Stored mode='invalid' falls back to 'feed'."""
    d = _make_dialog()
    d.settings = _make_settings_mock({'reddit_grab.mode': 'invalid'})
    d._load_settings()
    d.mode_var.set.assert_called_with('feed')


def test_load_settings_user_mode_falls_back_to_feed():
    """Stored mode='user' falls back to feed after RSS cutover."""
    d = _make_dialog()
    d.settings = _make_settings_mock({'reddit_grab.mode': 'user'})
    d._load_settings()
    d.mode_var.set.assert_called_with('feed')


def test_load_settings_invalid_sort_falls_back_to_new():
    """Stored sort='hot' falls back to 'new'."""
    d = _make_dialog()
    d.settings = _make_settings_mock({'reddit_grab.sort': 'hot'})
    d._load_settings()
    d.sort_var.set.assert_called_with('new')


def test_load_settings_invalid_top_window_falls_back_to_week():
    """Stored top_window='fortnight' falls back to 'week'."""
    d = _make_dialog()
    d.settings = _make_settings_mock({'reddit_grab.top_window': 'fortnight'})
    d._load_settings()
    d.top_window_var.set.assert_called_with('week')


def test_load_settings_max_posts_clamped_below_min():
    """Stored max_posts=0 is clamped to 1."""
    d = _make_dialog()
    d.settings = _make_settings_mock({'reddit_grab.max_posts': 0})
    d._load_settings()
    d.max_posts_var.set.assert_called_with('1')


def test_load_settings_max_posts_clamped_above_max():
    """Stored max_posts=999 is clamped to 200."""
    d = _make_dialog()
    d.settings = _make_settings_mock({'reddit_grab.max_posts': 999})
    d._load_settings()
    d.max_posts_var.set.assert_called_with('200')


def test_load_settings_max_posts_non_integer_fallback():
    """Non-integer max_posts falls back to 50."""
    d = _make_dialog()
    d.settings = _make_settings_mock({'reddit_grab.max_posts': 'abc'})
    d._load_settings()
    d.max_posts_var.set.assert_called_with('50')


def test_load_settings_query_coerced_to_string():
    """Non-string query (e.g. int) is coerced to str."""
    d = _make_dialog()
    d.settings = _make_settings_mock({'reddit_grab.query': 42})
    d._load_settings()
    d.query_var.set.assert_called_with('42')


def test_load_settings_bool_string_false_coerced_correctly():
    """Stored parse_body='false' is coerced to False, not True."""
    d = _make_dialog()
    d.settings = _make_settings_mock({'reddit_grab.parse_body': 'false'})
    d._load_settings()
    d.parse_body_var.set.assert_called_with(False)


def test_load_settings_restores_bulk_probe_flag():
    d = _make_dialog()
    d.settings = _make_settings_mock({'reddit_grab.bulk_probe_enabled': 'true'})
    d._load_settings()
    d.bulk_probe_var.set.assert_called_with(True)


# ---------------------------------------------------------------------------
# Settings persistence — _save_settings
# ---------------------------------------------------------------------------

def test_save_settings_no_op_when_settings_none():
    """_save_settings with settings=None raises no error."""
    d = _make_dialog()
    d.settings = None
    d._save_settings()  # must not raise


def test_save_settings_writes_all_supported_fields():
    """_save_settings persists the supported feed/search settings."""
    d = _make_dialog()
    sm = MagicMock()
    d.settings = sm
    d.max_posts_var.get.return_value = "75"
    d._save_settings()
    assert sm.set_setting.call_count == 9
    sm.set_setting.assert_any_call('reddit_grab.bulk_probe_enabled', False)


def test_on_run_calls_save_settings_before_callback(monkeypatch):
    """_on_run saves settings before invoking the grab callback."""
    call_order = []
    d = _make_dialog()
    sm = MagicMock()
    sm.set_setting.side_effect = lambda *_a, **_k: call_order.append("save")
    d.settings = sm
    d.grab_start_callback = lambda _opts: call_order.append("callback")
    monkeypatch.setattr(
        "gui.components.reddit_grab_dialog.messagebox.showerror",
        lambda *a, **kw: None,
    )
    d._on_run()
    assert call_order.index("save") < call_order.index("callback")


def test_on_cancel_calls_save_settings():
    """_on_cancel persists settings before destroying the dialog."""
    d = _make_dialog()
    sm = MagicMock()
    d.settings = sm
    d._on_cancel()
    assert sm.set_setting.called


def test_mode_visibility_correct_after_settings_restore_search():
    """After _load_settings restores mode=search, _on_mode_changed shows query field."""
    d = _make_dialog()
    d.settings = _make_settings_mock({'reddit_grab.mode': 'search'})
    d._load_settings()
    # Simulate _build_dialog calling _on_mode_changed with the loaded value
    d.mode_var.get.return_value = 'search'
    d._on_mode_changed()
    d._query_lbl.grid.assert_called_once()
    d._query_entry.grid.assert_called_once()
