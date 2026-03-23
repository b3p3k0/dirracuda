"""Focused regression tests for _DashboardScanControlsMixin state transitions."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.dashboard import DashboardWidget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stub(state="idle"):
    """Return a minimally-stubbed DashboardWidget instance."""
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.scan_button_state = state
    dash.external_scan_pid = None
    dash.stopping_started_time = None

    # Stub tk widgets
    dash.scan_button = MagicMock()
    dash.ftp_scan_button = MagicMock()
    dash.http_scan_button = MagicMock()
    dash.status_bar = MagicMock()
    dash.status_message = MagicMock()
    dash.main_frame = MagicMock()
    dash.parent = MagicMock()

    # Stub theme
    dash.theme = MagicMock()
    dash.theme.colors = {"warning": "#ff9900", "text_secondary": "#888"}
    dash.theme.fonts = {"small": ("Arial", 9)}

    # Stub scan_manager
    dash.scan_manager = MagicMock()
    dash.scan_manager.is_scan_active.return_value = False
    dash.scan_manager.is_scanning = False

    # Stub backend_interface
    dash.backend_interface = MagicMock()
    dash.backend_interface.mock_mode = False

    # Other attrs accessed by mixin methods
    dash._mock_mode_notice_shown = False
    dash.config_path = None
    dash.current_scan_options = None
    dash.current_progress_summary = ""
    dash.settings_manager = MagicMock()

    # Mixin methods that delegate to main dashboard (kept as mocks here)
    dash._set_button_to_start = MagicMock()
    dash._set_button_to_stop = MagicMock()
    dash._set_button_to_disabled = MagicMock()
    dash._set_button_to_stopping = MagicMock()
    dash._set_button_to_retry = MagicMock()
    dash._set_button_to_error = MagicMock()
    dash._hide_status_bar = MagicMock()
    dash._show_status_bar = MagicMock()
    dash._check_external_scans = MagicMock()
    dash._show_quick_scan_dialog = MagicMock()
    dash._show_stop_confirmation = MagicMock()
    dash._stop_scan_immediate = MagicMock()
    dash._update_scan_button_state = MagicMock(
        side_effect=lambda s: setattr(dash, "scan_button_state", s)
    )

    return dash


# ---------------------------------------------------------------------------
# _update_scan_button_state
# ---------------------------------------------------------------------------

class TestUpdateScanButtonState:
    def _make(self, state="idle"):
        dash = _make_stub(state)
        # Use real _update_scan_button_state from mixin
        dash._update_scan_button_state = DashboardWidget._update_scan_button_state.__get__(dash)
        return dash

    def test_idle_enables_ftp_and_http_buttons(self):
        dash = self._make()
        dash._update_scan_button_state("idle")
        assert dash.scan_button_state == "idle"
        dash._set_button_to_start.assert_called_once()
        dash._hide_status_bar.assert_called()
        assert dash.stopping_started_time is None
        dash.ftp_scan_button.config.assert_called_with(state="normal")
        dash.http_scan_button.config.assert_called_with(state="normal")

    def test_scanning_disables_ftp_and_http_buttons(self):
        dash = self._make()
        dash._update_scan_button_state("scanning")
        assert dash.scan_button_state == "scanning"
        dash._set_button_to_stop.assert_called_once()
        dash.ftp_scan_button.config.assert_called_with(state="disabled")
        dash.http_scan_button.config.assert_called_with(state="disabled")

    def test_disabled_external_disables_all_buttons(self):
        dash = self._make()
        dash.external_scan_pid = 9999
        dash._update_scan_button_state("disabled_external")
        assert dash.scan_button_state == "disabled_external"
        dash._set_button_to_disabled.assert_called_once()
        dash._show_status_bar.assert_called_once()
        dash.ftp_scan_button.config.assert_called_with(state="disabled")
        dash.http_scan_button.config.assert_called_with(state="disabled")

    def test_stopping_disables_ftp_and_http_buttons(self):
        dash = self._make()
        dash._update_scan_button_state("stopping")
        dash._set_button_to_stopping.assert_called_once()
        dash.ftp_scan_button.config.assert_called_with(state="disabled")
        dash.http_scan_button.config.assert_called_with(state="disabled")

    def test_error_disables_ftp_and_http_buttons(self):
        dash = self._make()
        dash._update_scan_button_state("error")
        dash._set_button_to_error.assert_called_once()
        dash.ftp_scan_button.config.assert_called_with(state="disabled")
        dash.http_scan_button.config.assert_called_with(state="disabled")


# ---------------------------------------------------------------------------
# _handle_scan_button_click
# ---------------------------------------------------------------------------

class TestHandleScanButtonClick:
    def test_idle_calls_check_then_dialog(self):
        dash = _make_stub("idle")
        DashboardWidget._handle_scan_button_click(dash)
        dash._check_external_scans.assert_called_once()
        # state is still "idle" after mock check, so dialog should open
        dash._show_quick_scan_dialog.assert_called_once()

    def test_idle_does_not_open_dialog_if_state_flipped(self):
        """If _check_external_scans flips state away from idle, dialog must NOT open."""
        dash = _make_stub("idle")

        def flip_state():
            dash.scan_button_state = "disabled_external"

        dash._check_external_scans.side_effect = flip_state
        DashboardWidget._handle_scan_button_click(dash)
        dash._show_quick_scan_dialog.assert_not_called()

    def test_scanning_opens_stop_confirmation(self):
        dash = _make_stub("scanning")
        DashboardWidget._handle_scan_button_click(dash)
        dash._show_stop_confirmation.assert_called_once()

    def test_retry_triggers_stop_immediate(self):
        dash = _make_stub("retry")
        DashboardWidget._handle_scan_button_click(dash)
        dash._stop_scan_immediate.assert_called_once()

    def test_error_triggers_stop_immediate(self):
        dash = _make_stub("error")
        DashboardWidget._handle_scan_button_click(dash)
        dash._stop_scan_immediate.assert_called_once()

    def test_stopping_is_no_op(self):
        """'stopping' state must not trigger any action (button is disabled)."""
        dash = _make_stub("stopping")
        DashboardWidget._handle_scan_button_click(dash)
        dash._show_stop_confirmation.assert_not_called()
        dash._stop_scan_immediate.assert_not_called()
        dash._show_quick_scan_dialog.assert_not_called()


# ---------------------------------------------------------------------------
# _check_external_scans
# ---------------------------------------------------------------------------

class TestCheckExternalScans:
    def _make_real(self, state="idle"):
        dash = _make_stub(state)
        # Use real _check_external_scans but stub _update_scan_button_state
        # (already stubbed in _make_stub via side_effect)
        return dash

    def test_no_active_scan_sets_idle(self):
        dash = self._make_real()
        dash.scan_manager.is_scan_active.return_value = False
        DashboardWidget._check_external_scans(dash)
        dash._update_scan_button_state.assert_called_with("idle")

    def test_external_pid_detected_sets_disabled_external(self, tmp_path):
        """When lock file has a different PID and process is valid → disabled_external."""
        import json, os

        lock_file = tmp_path / ".scan_lock"
        lock_data = {"process_id": 99999}
        lock_file.write_text(json.dumps(lock_data))

        dash = self._make_real()
        dash.scan_manager.is_scan_active.return_value = True

        dash._validate_external_process = MagicMock(return_value=True)

        with patch("os.path.join", return_value=str(lock_file)), \
             patch("os.path.exists", return_value=True), \
             patch("os.getpid", return_value=12345):
            DashboardWidget._check_external_scans(dash)

        assert dash.external_scan_pid == 99999
        dash._update_scan_button_state.assert_called_with("disabled_external")

    def test_exception_fallback_to_idle(self):
        dash = self._make_real()
        dash.scan_manager.is_scan_active.side_effect = RuntimeError("boom")
        DashboardWidget._check_external_scans(dash)
        dash._update_scan_button_state.assert_called_with("idle")


# ---------------------------------------------------------------------------
# _handle_stop_error
# ---------------------------------------------------------------------------

class TestHandleStopError:
    def test_scan_stopped_despite_error_goes_idle(self):
        dash = _make_stub("stopping")
        dash.scan_manager.is_scanning = False

        with patch("gui.components.dashboard_scan_controls.messagebox") as mb:
            DashboardWidget._handle_stop_error(dash, "some error")

        dash._update_scan_button_state.assert_called_with("idle")
        mb.showinfo.assert_called_once()
        mb.showerror.assert_not_called()

    def test_scan_still_running_goes_error(self):
        dash = _make_stub("stopping")
        dash.scan_manager.is_scanning = True

        with patch("gui.components.dashboard_scan_controls.messagebox") as mb:
            DashboardWidget._handle_stop_error(dash, "timed out")

        dash._update_scan_button_state.assert_called_with("error")
        mb.showerror.assert_called_once()
        mb.showinfo.assert_not_called()
