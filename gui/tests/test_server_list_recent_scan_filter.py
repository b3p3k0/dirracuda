"""Recent desktop scan filtering and quick-filter persistence."""

from datetime import datetime, timezone
import inspect
import tkinter as tk
from unittest.mock import MagicMock

import pytest

from gui.components.server_list_window import filters, table
from gui.components.server_list_window.filter_dropdown import QUICK_FILTERS
from gui.components.server_list_window.window import ServerListWindow
from gui.utils import last_scan_window
from gui.utils.style import SMBSeekTheme
from gui.utils.template_store import TemplateStore


WINDOW = {
    "start": datetime(2026, 10, 4, 12, tzinfo=timezone.utc),
    "end": datetime(2026, 10, 4, 13, tzinfo=timezone.utc),
}
KEY = "most_recent_scan_only"
PREFS_KEY = "windows.server_list.filter_preferences"


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for Tk")
    yield root
    root.destroy()


@pytest.fixture
def window(tk_root, tmp_path, monkeypatch):
    monkeypatch.setattr(last_scan_window, "load_last_scan_window", lambda _path: None)
    win = ServerListWindow.__new__(ServerListWindow)
    win.window = tk_root
    win.theme = SMBSeekTheme()
    for key, _ in QUICK_FILTERS:
        setattr(win, key, tk.BooleanVar(tk_root))
    for key in ("protocol_smb", "protocol_ftp", "protocol_http"):
        setattr(win, key, tk.BooleanVar(tk_root, value=True))
    for key in ("search_text", "country_filter_text", "filter_template_var"):
        setattr(win, key, tk.StringVar(tk_root))
    win.date_filter = tk.StringVar(tk_root, value="All")
    win._last_scan_window = None
    win.filter_widgets = None
    win.country_listbox = None
    win.country_code_list = []
    win.filter_template_store = None
    win._selected_filter_template_slug = None
    win.is_advanced_mode = False
    win._db_available = True
    win.all_servers = [
        {"row_key": "S:1", "host_type": "S", "last_seen": "2026-10-04 12:30:00", "notes": "reviewed"},
        {"row_key": "F:1", "host_type": "F", "last_seen": "2026-10-04T13:00:00Z", "notes": ""},
        {"row_key": "H:1", "host_type": "H", "last_seen": "2026-10-04 11:59:59", "notes": "older"},
    ]
    win.filtered_servers = []
    win.db_reader = MagicMock(db_path=str(tmp_path / "hosts.db"))
    win.db_reader.is_database_available.return_value = True
    win.db_reader.get_protocol_server_list.return_value = (win.all_servers, 3)
    win.db_reader.get_denied_share_counts.return_value = {}
    prefs = {}
    win.settings_manager = MagicMock()
    win.settings_manager.get_setting.side_effect = lambda key, default=None: prefs.get(key, default)
    win.settings_manager.set_setting.side_effect = lambda key, value: prefs.update({key: value})
    win._is_batch_active = lambda: False
    for method in ("_hide_notes_tooltip", "_set_empty_state_hint", "_update_action_buttons_state",
                   "_update_mode_display", "_populate_country_filter", "_attach_probe_status",
                   "_attach_sherlock_risk", "_reset_sort_state"):
        setattr(win, method, MagicMock())
    _, win.tree, *_ = table.create_server_table(tk_root, win.theme, {})
    win.count_label = tk.Label(tk_root)
    return win


@pytest.mark.parametrize("stamp, included", [
    ("2026-10-04 12:00:00", True),
    ("2026-10-04 13:00:00", True),
    ("2026-10-04 11:59:59.999999", False),
    ("2026-10-04 13:00:00.000001", False),
    ("2026-10-04 12:30:00", True),
    ("2026-10-04T12:30:00", True),
    ("2026-10-04T12:30:00Z", True),
    ("2026-10-04 12:30:00.123456", True),
    ("2026-10-04T12:30:00.123456Z", True),
    ("2026-10-04T14:00:00+02:00", True),
    ("2026-10-04T15:00:00+02:00", True),
    ("2026-10-04T14:30:00.123456+02:00", True),
    ("2026-10-04T12:30:00+02:00", False),
    ("2026-10-04T08:30:00-04:00", True),
    (None, False), ("", False), ("   ", False), ("garbage", False), (123, False),
])
def test_recent_scan_timestamps(stamp, included):
    servers = [{"last_seen": stamp}]
    assert filters.apply_recent_scan_filter(servers, WINDOW) == (servers if included else [])


def test_missing_last_seen_and_no_window():
    servers = [{}, {"first_seen": "2026-10-04 12:30:00"}]
    assert filters.apply_recent_scan_filter(servers, WINDOW) == []
    assert filters.apply_recent_scan_filter(servers, None) is servers


def test_notes_intersection(window):
    window._last_scan_window = WINDOW
    window.most_recent_scan_only.set(True)
    window.has_notes_only.set(True)
    window._apply_filters()
    assert window.filtered_servers == [window.all_servers[0]]
    assert window.tree.get_children() == ("S:1",)


def test_initial_panel_disables_saved_true_and_shows_hint(window, tk_root):
    window.settings_manager.set_setting(PREFS_KEY, {KEY: True})
    window._create_filter_panel()
    assert window.most_recent_scan_only.get() is False
    dropdown = window.filter_widgets["filter_dropdown"]
    dropdown.open()
    tk_root.update()
    assert dropdown.option_widgets[KEY].cget("state") == "disabled"
    hint = dropdown.hint_widgets[KEY]
    assert hint.cget("text") == "No recorded scan yet"
    assert hint.winfo_viewable()
    packed = dropdown.popover.pack_slaves()
    assert packed[packed.index(dropdown.option_widgets[KEY]) + 1] is hint
    dropdown.option_widgets[KEY].invoke()
    assert window.most_recent_scan_only.get() is False

    window._last_scan_window = WINDOW
    window._sync_recent_scan_option()
    assert dropdown.option_widgets[KEY].cget("state") == "normal"
    assert KEY not in dropdown.hint_widgets
    assert not hint.winfo_exists()
    window.most_recent_scan_only.set(True)
    window._last_scan_window = None
    window._sync_recent_scan_option()
    tk_root.update()
    assert window.most_recent_scan_only.get() is False
    assert dropdown.hint_widgets[KEY].winfo_viewable()
    assert dropdown.option_widgets[KEY].cget("state") == "disabled"


def test_real_checkbutton_filters_rows_and_counts(window, tk_root, monkeypatch):
    monkeypatch.setattr(last_scan_window, "load_last_scan_window", lambda _path: WINDOW)
    window._create_filter_panel()
    window._apply_filters()
    dropdown = window.filter_widgets["filter_dropdown"]
    dropdown.open()
    tk_root.update()
    assert KEY not in dropdown.hint_widgets
    assert dropdown.button.cget("text") == "Filters ▾"
    assert window.tree.get_children() == ("S:1", "F:1", "H:1")
    dropdown.option_widgets[KEY].invoke()
    assert dropdown.is_open()
    assert window.tree.get_children() == ("S:1", "F:1")
    assert window.filtered_servers == window.all_servers[:2]
    assert dropdown.button.cget("text") == "Filters (1) ▾"
    dropdown.option_widgets[KEY].invoke()
    assert window.tree.get_children() == ("S:1", "F:1", "H:1")
    assert dropdown.button.cget("text") == "Filters ▾"


def test_preferences_round_trip_and_reset(window, monkeypatch):
    monkeypatch.setattr(last_scan_window, "load_last_scan_window", lambda _path: WINDOW)
    window._create_filter_panel()
    window.most_recent_scan_only.set(True)
    window._persist_filter_preferences()
    assert window.settings_manager.get_setting(PREFS_KEY)[KEY] is True
    window.most_recent_scan_only.set(False)
    window._load_filter_preferences()
    assert window.most_recent_scan_only.get() is True
    window._reset_filters()
    assert window.most_recent_scan_only.get() is False
    assert window.settings_manager.get_setting(PREFS_KEY)[KEY] is False


def test_template_round_trip_and_default(window, tmp_path):
    store = TemplateStore(base_dir=tmp_path / "templates", seed_dir=tmp_path / "no-seeds")
    window._last_scan_window = WINDOW
    window.most_recent_scan_only.set(True)
    template = store.save_template("Recent scan", window._capture_filter_state())
    window.most_recent_scan_only.set(False)
    window._apply_filter_state(store.load_template(template.slug).form_state)
    assert window.most_recent_scan_only.get() is True
    window._apply_filter_state({})
    assert window.most_recent_scan_only.get() is False


@pytest.mark.parametrize("source", ["prefs", "template"])
@pytest.mark.parametrize("date_value", ["Since Last Scan", "unknown", None])
def test_legacy_date_and_saved_true_without_window(window, source, date_value):
    state = {KEY: True, "date_filter": date_value}
    if source == "prefs":
        window.settings_manager.set_setting(PREFS_KEY, state)
        window._load_filter_preferences()
    else:
        window._apply_filter_state(state)
    assert window.date_filter.get() == "All"
    assert window.most_recent_scan_only.get() is False


def test_date_options_and_signature(window):
    window._create_filter_panel()
    assert list(window.filter_widgets["date_combo"]["values"]) == filters.DATE_FILTER_OPTIONS
    assert filters.DATE_FILTER_OPTIONS == ["All", "Last 24 Hours", "Last 7 Days", "Last 30 Days"]
    assert list(inspect.signature(filters.apply_date_filter).parameters) == ["servers", "filter_type"]
    assert filters.apply_date_filter([{}], "Last 24 Hours") == []


def test_load_refreshes_window_before_filtering(window, monkeypatch):
    window._create_filter_panel()
    loader = MagicMock(return_value=WINDOW)
    monkeypatch.setattr(last_scan_window, "load_last_scan_window", loader)
    window.most_recent_scan_only.set(True)
    window._load_data()
    loader.assert_called_once_with(window.db_reader.db_path)
    assert window.filtered_servers == window.all_servers[:2]
    assert window._last_scan_window is WINDOW
    loader.return_value = None
    window.db_reader.db_path = "another.db"
    window._load_data()
    loader.assert_called_with("another.db")
    assert window._last_scan_window is None
    assert window.most_recent_scan_only.get() is False
    assert window.filtered_servers == window.all_servers


def test_no_database_clears_window(window, monkeypatch):
    monkeypatch.setattr(last_scan_window, "load_last_scan_window", lambda _path: WINDOW)
    window.most_recent_scan_only.set(True)
    window._create_filter_panel()
    window.db_reader = None
    loader = MagicMock()
    monkeypatch.setattr(last_scan_window, "load_last_scan_window", loader)
    window._load_data()
    loader.assert_not_called()
    assert window._last_scan_window is None
    assert window.most_recent_scan_only.get() is False
    assert window.tree.get_children() == ()
    dropdown = window.filter_widgets["filter_dropdown"]
    dropdown.open()
    assert dropdown.option_widgets[KEY].cget("state") == "disabled"


@pytest.mark.parametrize("recorded_window", [WINDOW, None], ids=["valid-window", "no-window"])
def test_saved_recent_scan_filter_survives_deferred_initial_load(
    window, tk_root, monkeypatch, recorded_window,
):
    loader = MagicMock(return_value=recorded_window)
    monkeypatch.setattr(last_scan_window, "load_last_scan_window", loader)
    window.settings_manager.set_setting(PREFS_KEY, {KEY: True, "shares_filter": False})
    window.settings_manager.set_setting.reset_mock()
    window.all_servers = []
    window._initial_load_started = False
    window._initial_load_after_id = None
    window._initial_map_bind_id = None
    window._apply_filters = MagicMock(wraps=window._apply_filters)
    assert window._last_scan_window is None

    window._create_filter_panel()
    enabled = recorded_window is not None
    assert window.most_recent_scan_only.get() is enabled
    window._apply_filters.assert_not_called()
    window.settings_manager.set_setting.assert_not_called()
    dropdown = window.filter_widgets["filter_dropdown"]
    dropdown.open()
    tk_root.update_idletasks()
    assert dropdown.option_widgets[KEY].cget("state") == ("normal" if enabled else "disabled")
    if enabled:
        assert KEY not in dropdown.hint_widgets
    else:
        assert dropdown.hint_widgets[KEY].cget("text") == "No recorded scan yet"
        assert dropdown.hint_widgets[KEY].winfo_viewable()

    window._initial_load_after_id = tk_root.after_idle(window._run_initial_data_load)
    tk_root.update()

    assert window._initial_load_started is True
    window._apply_filters.assert_called_once_with()
    assert loader.call_count == 2
    loader.assert_called_with(window.db_reader.db_path)
    assert window.most_recent_scan_only.get() is enabled
    assert window.tree.get_children() == (("S:1", "F:1") if enabled else ("S:1", "F:1", "H:1"))
    window.settings_manager.set_setting.assert_called_once()
    assert window.settings_manager.get_setting(PREFS_KEY)[KEY] is enabled
