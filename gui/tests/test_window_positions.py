"""Persistence, validation, and event ordering for remembered positions."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gui.utils.settings_manager import SettingsManager
from gui.utils.window_positions import remember_window_position


def window(geometry="800x400+20+30"):
    win = MagicMock()
    win.state.return_value = "normal"
    win.attributes.return_value = False
    win.geometry.return_value = geometry
    return win


@pytest.mark.parametrize("position", ["+120+700", "-20-30", "+-500+-200", "+0+0"])
def test_restores_position_only_across_settings_instances(tmp_path, position):
    first = SettingsManager(str(tmp_path))
    first.set_setting("windows.start_scan.position", position)
    other_process_settings = SettingsManager(str(tmp_path))
    win = window()
    remember_window_position(win, "start_scan", other_process_settings)
    win.geometry.assert_called_once_with(position)
    win.focus_force.assert_not_called()
    win.lift.assert_not_called()
    win.deiconify.assert_not_called()


@pytest.mark.parametrize("invalid", [None, True, 42, [], {}, "center", "800x400+2+3", "+2", "+nan+5", "+9999999999999+0"])
def test_invalid_position_keeps_existing_default(tmp_path, invalid):
    settings = SettingsManager(str(tmp_path))
    settings.set_setting("windows.start_scan.position", invalid)
    win = window()
    remember_window_position(win, "start_scan", settings)
    win.geometry.assert_not_called()


def test_drag_is_debounced_and_hide_flushes_latest_position(tmp_path):
    settings = SettingsManager(str(tmp_path))
    settings.set_setting = MagicMock(wraps=settings.set_setting)
    win = window()
    controller = remember_window_position(win, "start_scan", settings)
    event = SimpleNamespace(widget=win)
    for n in range(20):
        win.geometry.return_value = f"800x400+{n}+700"
        controller._moved(event)
    settings.set_setting.assert_not_called()
    assert win.after.call_count == 20
    assert win.after_cancel.call_count == 19
    win.state.return_value = "withdrawn"
    controller._flush_event(event)
    settings.set_setting.assert_called_once_with("windows.start_scan.position", "+19+700")
    assert SettingsManager(str(tmp_path)).get_setting("windows.start_scan.position") == "+19+700"


def test_child_events_and_non_normal_states_do_not_overwrite_position(tmp_path):
    settings = SettingsManager(str(tmp_path))
    settings.set_setting = MagicMock(wraps=settings.set_setting)
    win = window()
    controller = remember_window_position(win, "scan_results", settings)
    controller._moved(SimpleNamespace(widget=object()))
    for state in ("iconic", "withdrawn", "zoomed"):
        win.state.return_value = state
        controller._moved(SimpleNamespace(widget=win))
    win.state.return_value = "normal"
    win.attributes.return_value = True
    controller._moved(SimpleNamespace(widget=win))
    controller.flush()
    win.after.assert_not_called()
    settings.set_setting.assert_not_called()


def test_destroy_flushes_snapshot_without_querying_destroyed_window(tmp_path):
    settings = SettingsManager(str(tmp_path))
    win = window("900x600+300+400")
    controller = remember_window_position(win, "scan_results", settings)
    controller._moved(SimpleNamespace(widget=win))
    win.geometry.side_effect = AssertionError("window already destroyed")
    controller._flush_event(SimpleNamespace(widget=win))
    assert settings.get_setting("windows.scan_results.position") == "+300+400"


def test_window_types_are_independent_and_resize_does_not_write_again(tmp_path):
    settings = SettingsManager(str(tmp_path))
    settings.set_setting("windows.start_scan.position", "+10+20")
    win = window("800x400+300+400")
    controller = remember_window_position(win, "scan_results", settings)
    controller._moved(SimpleNamespace(widget=win))
    controller.flush()
    settings.set_setting = MagicMock(wraps=settings.set_setting)
    win.geometry.return_value = "1000x700+300+400"
    controller._moved(SimpleNamespace(widget=win))
    controller.flush()
    settings.set_setting.assert_not_called()
    assert settings.get_setting("windows.start_scan.position") == "+10+20"


def test_failed_save_can_retry_on_close(tmp_path):
    settings = SettingsManager(str(tmp_path))
    win = window()
    controller = remember_window_position(win, "scan_results", settings)
    controller._moved(SimpleNamespace(widget=win))
    settings.set_setting = MagicMock(side_effect=[False, True])
    controller.flush()
    controller._flush_event(SimpleNamespace(widget=win))
    assert settings.set_setting.call_count == 2
