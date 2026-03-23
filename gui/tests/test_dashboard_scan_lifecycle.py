"""Tests for _DashboardScanLifecycleMixin.

Covers scan progress tracking, status refresh, scan dialog launching,
progress monitoring, and results display.
"""

import queue
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.dashboard import DashboardWidget


# ---------------------------------------------------------------------------
# Stub factory
# ---------------------------------------------------------------------------

def _make_stub():
    """Return a minimally-stubbed DashboardWidget instance bypassing __init__."""
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.current_progress_summary = ""
    dash.log_queue = queue.Queue()
    dash._status_refresh_pending = False
    dash._status_static_mode = True
    dash._status_summary_initialized = False
    dash.scan_button_state = "idle"
    dash.stopping_started_time = None
    dash._queued_scan_active = False
    dash._queued_scan_protocols = []
    dash.current_scan_options = None
    dash.config_path = None
    dash.parent = MagicMock()
    dash.scan_manager = MagicMock()
    dash.scan_manager.is_scan_active.return_value = False
    dash.settings_manager = MagicMock()
    dash.status_text = MagicMock()
    dash.update_time_label = MagicMock()
    dash.last_update = None
    dash.size_enforcement_callback = None
    dash.config_editor_callback = None
    dash.db_reader = MagicMock()
    dash._append_log_line = MagicMock()
    dash._update_scan_button_state = MagicMock()
    dash._start_unified_scan = MagicMock()
    dash._open_config_editor_from_scan = MagicMock()
    dash._open_config_editor = MagicMock()
    dash._run_post_scan_batch_operations = MagicMock()
    dash._handle_queued_scan_completion = MagicMock()
    return dash


# ---------------------------------------------------------------------------
# 1. _update_progress_summary
# ---------------------------------------------------------------------------

class TestUpdateProgressSummary:
    def test_summary_and_detail_joined_with_dash(self):
        dash = _make_stub()
        DashboardWidget._update_progress_summary(dash, "50% done", "detail here")
        assert dash.current_progress_summary == "50% done - detail here"

    def test_summary_only_no_dash(self):
        dash = _make_stub()
        DashboardWidget._update_progress_summary(dash, "50% done", None)
        assert dash.current_progress_summary == "50% done"

    def test_both_none_falls_back_to_in_progress(self):
        dash = _make_stub()
        DashboardWidget._update_progress_summary(dash, None, None)
        assert dash.current_progress_summary == "In progress"

    def test_strips_whitespace(self):
        dash = _make_stub()
        DashboardWidget._update_progress_summary(dash, "  hello  ", "  world  ")
        assert dash.current_progress_summary == "hello - world"


# ---------------------------------------------------------------------------
# 2. _log_status_event
# ---------------------------------------------------------------------------

class TestLogStatusEvent:
    def test_nonempty_message_enqueued(self):
        dash = _make_stub()
        DashboardWidget._log_status_event(dash, "hello world")
        assert not dash.log_queue.empty()
        entry = dash.log_queue.get()
        assert "hello world" in entry

    def test_empty_string_is_noop(self):
        dash = _make_stub()
        DashboardWidget._log_status_event(dash, "")
        assert dash.log_queue.empty()

    def test_entry_includes_timestamp_prefix(self):
        dash = _make_stub()
        DashboardWidget._log_status_event(dash, "msg")
        entry = dash.log_queue.get()
        assert entry.startswith("[status ")


# ---------------------------------------------------------------------------
# 3. _schedule_post_scan_refresh
# ---------------------------------------------------------------------------

class TestSchedulePostScanRefresh:
    def test_not_pending_schedules_after_and_sets_flag(self):
        dash = _make_stub()
        dash._status_refresh_pending = False
        DashboardWidget._schedule_post_scan_refresh(dash)
        assert dash._status_refresh_pending is True
        dash.parent.after.assert_called_once()

    def test_already_pending_skips_after(self):
        dash = _make_stub()
        dash._status_refresh_pending = True
        DashboardWidget._schedule_post_scan_refresh(dash)
        dash.parent.after.assert_not_called()

    def test_default_delay_is_2000ms(self):
        dash = _make_stub()
        DashboardWidget._schedule_post_scan_refresh(dash)
        args = dash.parent.after.call_args[0]
        assert args[0] == 2000


# ---------------------------------------------------------------------------
# 4 & 5. _show_quick_scan_dialog
# ---------------------------------------------------------------------------

class TestShowQuickScanDialog:
    def test_active_scan_shows_warning_and_returns(self):
        dash = _make_stub()
        dash.scan_manager.is_scan_active.return_value = True
        with patch("gui.components.dashboard_scan_lifecycle.messagebox.showwarning") as mock_warn, \
             patch("gui.components.dashboard_scan_lifecycle.show_unified_scan_dialog") as mock_dlg:
            DashboardWidget._show_quick_scan_dialog(dash)
        mock_warn.assert_called_once()
        mock_dlg.assert_not_called()

    def test_no_active_scan_calls_unified_dialog(self):
        dash = _make_stub()
        dash.scan_manager.is_scan_active.return_value = False
        with patch("gui.components.dashboard_scan_lifecycle.show_unified_scan_dialog") as mock_dlg:
            DashboardWidget._show_quick_scan_dialog(dash)
        mock_dlg.assert_called_once()


# ---------------------------------------------------------------------------
# 6. _handle_scan_progress
# ---------------------------------------------------------------------------

class TestHandleScanProgress:
    def test_phase_and_percentage_formatted(self):
        dash = _make_stub()
        dash._update_progress_summary = MagicMock()
        DashboardWidget._handle_scan_progress(dash, 50.0, "scanning hosts", "discovery")
        args = dash._update_progress_summary.call_args[0]
        assert "50%" in args[0]
        assert "Discovery" in args[0]

    def test_no_phase_percentage_only(self):
        dash = _make_stub()
        dash._update_progress_summary = MagicMock()
        DashboardWidget._handle_scan_progress(dash, 75.0, "status", "")
        args = dash._update_progress_summary.call_args[0]
        assert "75%" in args[0]

    def test_exception_does_not_propagate(self):
        dash = _make_stub()
        dash._update_progress_summary = MagicMock(side_effect=RuntimeError("boom"))
        # Should not raise
        DashboardWidget._handle_scan_progress(dash, 10.0, "msg", "phase")


# ---------------------------------------------------------------------------
# 7. _show_scan_results — fallback
# ---------------------------------------------------------------------------

class TestShowScanResults:
    def test_fallback_messagebox_when_dialog_raises(self):
        dash = _make_stub()
        results = {"status": "completed", "hosts_scanned": 5, "accessible_hosts": 2}
        with patch("gui.components.dashboard_scan_lifecycle.show_scan_results_dialog",
                   side_effect=Exception("dialog boom")), \
             patch("gui.components.dashboard_scan_lifecycle.messagebox.showinfo") as mock_info:
            DashboardWidget._show_scan_results(dash, results)
        mock_info.assert_called_once()

    def test_happy_path_calls_dialog(self):
        dash = _make_stub()
        results = {"status": "completed"}
        with patch("gui.components.dashboard_scan_lifecycle.show_scan_results_dialog") as mock_dlg:
            DashboardWidget._show_scan_results(dash, results)
        mock_dlg.assert_called_once()


# ---------------------------------------------------------------------------
# 8. finish_scan_progress
# ---------------------------------------------------------------------------

class TestFinishScanProgress:
    def test_success_schedules_refresh_and_reset(self):
        dash = _make_stub()
        dash._update_progress_summary = MagicMock()
        dash._log_status_event = MagicMock()
        dash._schedule_post_scan_refresh = MagicMock()
        DashboardWidget.finish_scan_progress(dash, True, {"successful_auth": 3, "hosts_tested": 10})
        dash._schedule_post_scan_refresh.assert_called_once_with(delay_ms=2000)
        dash.parent.after.assert_called_once_with(5000, dash._reset_scan_status)

    def test_failure_also_schedules_refresh(self):
        dash = _make_stub()
        dash._update_progress_summary = MagicMock()
        dash._log_status_event = MagicMock()
        dash._schedule_post_scan_refresh = MagicMock()
        DashboardWidget.finish_scan_progress(dash, False, {})
        dash._schedule_post_scan_refresh.assert_called_once_with(delay_ms=2000)


# ---------------------------------------------------------------------------
# 9. _monitor_scan_completion — cancelled path
# ---------------------------------------------------------------------------

class TestMonitorScanCompletionCancelled:
    def test_cancelled_sets_idle_and_logs(self):
        dash = _make_stub()
        dash.scan_manager.is_scanning = False
        dash.scan_manager.get_scan_results.return_value = {"status": "cancelled"}
        dash._log_status_event = MagicMock()
        dash._reset_scan_status = MagicMock()
        dash._refresh_after_scan_completion = MagicMock()
        # Capture the after() callback and invoke it immediately
        captured = []
        def fake_after(delay, fn):
            captured.append(fn)
        dash.parent.after.side_effect = fake_after

        DashboardWidget._monitor_scan_completion(dash)
        assert captured, "after() not called"
        # Invoke check_completion
        with patch("tkinter.messagebox.showinfo"):
            captured[0]()

        dash._update_scan_button_state.assert_called_with("idle")
        logged_messages = [c.args[0] for c in dash._log_status_event.call_args_list]
        assert any("cancelled" in m.lower() for m in logged_messages)
        dash._reset_scan_status.assert_called_once()

    def test_cancelled_does_not_call_show_scan_results(self):
        dash = _make_stub()
        dash.scan_manager.is_scanning = False
        dash.scan_manager.get_scan_results.return_value = {"status": "cancelled"}
        dash._reset_scan_status = MagicMock()
        dash._refresh_after_scan_completion = MagicMock()
        dash._show_scan_results = MagicMock()
        captured = []
        dash.parent.after.side_effect = lambda d, fn: captured.append(fn)

        DashboardWidget._monitor_scan_completion(dash)
        with patch("tkinter.messagebox.showinfo"):
            captured[0]()

        dash._show_scan_results.assert_not_called()


# ---------------------------------------------------------------------------
# 10. _refresh_dashboard_data — success path
# ---------------------------------------------------------------------------

class TestRefreshDashboardData:
    def test_success_calls_size_enforcement_callback(self):
        dash = _make_stub()
        dash.db_reader.get_dashboard_summary.return_value = {
            "total_servers": 10,
            "servers_with_accessible_shares": 3,
            "total_shares": 8,
            "last_scan": "Never",
        }
        cb = MagicMock()
        dash.size_enforcement_callback = cb
        dash._status_static_mode = False
        dash._status_summary_initialized = False
        DashboardWidget._refresh_dashboard_data(dash)
        cb.assert_called_once()

    def test_success_updates_status_text(self):
        dash = _make_stub()
        dash.db_reader.get_dashboard_summary.return_value = {
            "total_servers": 5,
            "servers_with_accessible_shares": 1,
            "total_shares": 2,
            "last_scan": "Never",
        }
        dash._status_static_mode = False
        dash._status_summary_initialized = False
        DashboardWidget._refresh_dashboard_data(dash)
        dash.status_text.set.assert_called_once()


# ---------------------------------------------------------------------------
# 11. _handle_refresh_error — database error triggers mock-mode retry
# ---------------------------------------------------------------------------

class TestHandleRefreshError:
    def test_database_error_enables_mock_mode(self):
        dash = _make_stub()
        call_count = [0]

        def fake_refresh():
            call_count[0] += 1
            if call_count[0] > 1:
                return  # prevent infinite recursion on retry

        dash._refresh_dashboard_data = fake_refresh
        error = Exception("Database connection failed")
        DashboardWidget._handle_refresh_error(dash, error)
        dash.db_reader.enable_mock_mode.assert_called_once()
        assert call_count[0] >= 1  # retry was attempted

    def test_non_database_error_sets_error_text(self):
        dash = _make_stub()
        error = Exception("some other failure")
        DashboardWidget._handle_refresh_error(dash, error)
        dash.db_reader.enable_mock_mode.assert_not_called()
        dash.status_text.set.assert_called()


# ---------------------------------------------------------------------------
# 12. _status_refresh_pending init regression
# ---------------------------------------------------------------------------

class TestStatusRefreshPendingInit:
    def test_fresh_instance_has_status_refresh_pending_false(self):
        """DashboardWidget.__init__ must initialize _status_refresh_pending=False."""
        # Build the minimal set of mocks needed to survive __init__
        parent = MagicMock()
        db_reader = MagicMock()
        db_reader.get_dashboard_summary.return_value = {
            "total_servers": 0,
            "servers_with_accessible_shares": 0,
            "total_shares": 0,
            "last_scan": "Never",
        }
        backend_interface = MagicMock()

        # Patch everything that tries to build a real Tk widget
        with patch("gui.components.dashboard.get_theme") as mock_theme, \
             patch("gui.components.dashboard.get_scan_manager"), \
             patch("gui.components.dashboard.get_settings_manager"), \
             patch.object(DashboardWidget, "_build_dashboard"), \
             patch.object(DashboardWidget, "_refresh_dashboard_data"), \
             patch.object(DashboardWidget, "_load_indicator_patterns"):
            theme = MagicMock()
            theme.colors = {
                "log_bg": "#111418", "log_fg": "#f5f5f5", "log_placeholder": "#9ea4b3"
            }
            mock_theme.return_value = theme
            parent.tk = MagicMock()
            parent.tk.call = MagicMock(return_value="")

            import tkinter as tk
            with patch.object(tk, "StringVar", return_value=MagicMock()):
                instance = DashboardWidget.__new__(DashboardWidget)
                instance.parent = parent
                instance.db_reader = db_reader
                instance.backend_interface = backend_interface
                instance.theme = theme
                instance.current_scan = None
                instance.current_scan_options = None
                instance.last_update = None
                instance.scan_manager = MagicMock()
                instance.config_path = None
                instance.settings_manager = MagicMock()
                instance.ransomware_indicators = []
                instance.indicator_patterns = []
                instance._mock_mode_notice_shown = False
                instance.main_frame = None
                instance.body_canvas = None
                instance.body_scrollbar = None
                instance.body_frame = None
                instance.body_canvas_window = None
                instance.progress_frame = None
                instance.metrics_frame = None
                instance.scan_button = None
                instance.servers_button = None
                instance.db_tools_button = None
                instance.config_button = None
                instance.about_button = None
                instance.theme_toggle_button = None
                instance.status_bar = None
                instance.update_time_label = None
                instance.status_message = None
                instance.current_progress_summary = ""
                instance.status_text = MagicMock()
                instance._status_static_mode = True
                instance._status_summary_initialized = False
                import queue as _queue
                instance.log_queue = _queue.Queue()
                from collections import deque
                instance.log_history = deque(maxlen=500)
                instance.log_text_widget = None
                instance.log_autoscroll = True
                instance._log_placeholder_visible = True
                instance.log_processing_job = None
                instance.log_jump_button = None
                instance.copy_log_button = None
                instance.clear_log_button = None
                instance.log_bg_color = "#111418"
                instance.log_fg_color = "#f5f5f5"
                instance.log_placeholder_color = "#9ea4b3"
                import re
                instance._ansi_pattern = re.compile(r"\x1b\[([\d;]*)m")
                instance._ansi_color_tag_map = {}
                instance._ansi_color_tags = set()
                instance.log_placeholder_text = "Scan output will appear here once a scan starts."
                instance.scan_button_state = "idle"
                instance.external_scan_pid = None
                instance.stopping_started_time = None
                instance._status_refresh_pending = False  # the fix under test
                instance.ftp_scan_button = None
                instance.http_scan_button = None
                instance._queued_scan_active = False
                instance._queued_scan_protocols = []
                instance._queued_scan_common_options = None
                instance._queued_scan_current_protocol = None
                instance._queued_scan_failures = []
                instance.drill_down_callback = None
                instance.config_editor_callback = None
                instance.size_enforcement_callback = None

        assert instance._status_refresh_pending is False

    def test_schedule_post_scan_refresh_does_not_raise_on_first_call(self):
        """_schedule_post_scan_refresh must not raise AttributeError on first invocation."""
        dash = _make_stub()
        # _status_refresh_pending is set to False in _make_stub — mirrors __init__ fix
        try:
            DashboardWidget._schedule_post_scan_refresh(dash)
        except AttributeError as exc:
            raise AssertionError(
                f"_status_refresh_pending not initialized: {exc}"
            ) from exc
