"""
Tests for Reddit Grab button/handler/worker wiring in DashboardWidget.

Groups:
  A — reddit_grab_button enable/disable via _update_scan_button_state
  B — click handler calls _check_external_scans before opening dialog
  C — worker exception path always schedules _on_reddit_grab_done and resets flag
"""

from __future__ import annotations

import sys
import tkinter as tk
import types
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Lightweight impacket stub for environments where the dependency is unavailable.
if "impacket" not in sys.modules:
    impacket_mod = types.ModuleType("impacket")
    impacket_smb_mod = types.ModuleType("impacket.smb")
    impacket_smb_mod.SMB2_DIALECT_002 = object()
    impacket_smbconn_mod = types.ModuleType("impacket.smbconnection")
    impacket_smbconn_mod.SMBConnection = object

    class _SessionError(Exception):
        pass

    impacket_smbconn_mod.SessionError = _SessionError
    impacket_mod.smb = impacket_smb_mod
    sys.modules["impacket"] = impacket_mod
    sys.modules["impacket.smb"] = impacket_smb_mod
    sys.modules["impacket.smbconnection"] = impacket_smbconn_mod

from gui.components.dashboard import DashboardWidget
from experimental.redseek.service import IngestOptions, IngestResult


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_dash() -> DashboardWidget:
    """Construct a minimal DashboardWidget stub without building any Tk widgets."""
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.scan_button_state = "idle"
    dash._reddit_grab_running = False
    dash.reddit_grab_button = None
    dash.ftp_scan_button = None
    dash.http_scan_button = None
    dash.external_scan_pid = None
    dash.stopping_started_time = None
    # scan_button required by _set_button_to_* helpers — use a mock
    dash.scan_button = MagicMock()
    # parent required by show_reddit_grab_dialog and messagebox calls
    dash.parent = MagicMock()
    # log helper used by handler methods
    dash._log_status_event = lambda _msg: None
    return dash


def _make_ingest_result(**kwargs) -> IngestResult:
    defaults = dict(
        sort="new",
        subreddit="opendirectories",
        pages_fetched=1,
        posts_stored=5,
        posts_skipped=0,
        targets_stored=3,
        targets_deduped=0,
        parse_errors=0,
        stopped_by_cursor=False,
        stopped_by_max_posts=False,
        replace_cache_done=False,
        rate_limited=False,
        error=None,
    )
    defaults.update(kwargs)
    return IngestResult(**defaults)


def _make_options() -> IngestOptions:
    return IngestOptions(
        sort="new",
        max_posts=50,
        parse_body=True,
        include_nsfw=False,
        replace_cache=False,
    )


# ---------------------------------------------------------------------------
# Group A — _update_scan_button_state enables/disables reddit_grab_button
# ---------------------------------------------------------------------------

class TestUpdateScanButtonStateRedditButton:

    def _stub_set_helpers(self, dash: DashboardWidget, monkeypatch) -> None:
        """No-op all _set_button_to_* and status-bar helpers."""
        for name in (
            "_set_button_to_start", "_set_button_to_stop", "_set_button_to_disabled",
            "_set_button_to_stopping", "_set_button_to_retry", "_set_button_to_error",
            "_hide_status_bar", "_show_status_bar",
        ):
            monkeypatch.setattr(dash, name, lambda *_a, **_k: None, raising=False)

    def test_button_disabled_while_scanning(self, monkeypatch):
        dash = _make_dash()
        mock_btn = MagicMock()
        dash.reddit_grab_button = mock_btn
        self._stub_set_helpers(dash, monkeypatch)

        dash._update_scan_button_state("scanning")

        mock_btn.config.assert_called_with(state=tk.DISABLED)

    def test_button_disabled_while_stopping(self, monkeypatch):
        dash = _make_dash()
        mock_btn = MagicMock()
        dash.reddit_grab_button = mock_btn
        self._stub_set_helpers(dash, monkeypatch)

        dash._update_scan_button_state("stopping")

        mock_btn.config.assert_called_with(state=tk.DISABLED)

    def test_button_disabled_while_disabled_external(self, monkeypatch):
        dash = _make_dash()
        mock_btn = MagicMock()
        dash.reddit_grab_button = mock_btn
        dash.external_scan_pid = 9999
        self._stub_set_helpers(dash, monkeypatch)

        dash._update_scan_button_state("disabled_external")

        mock_btn.config.assert_called_with(state=tk.DISABLED)

    def test_button_enabled_on_idle_when_grab_not_running(self, monkeypatch):
        dash = _make_dash()
        mock_btn = MagicMock()
        dash.reddit_grab_button = mock_btn
        dash._reddit_grab_running = False
        self._stub_set_helpers(dash, monkeypatch)

        dash._update_scan_button_state("idle")

        mock_btn.config.assert_called_with(state=tk.NORMAL)

    def test_button_stays_disabled_on_idle_if_grab_running(self, monkeypatch):
        dash = _make_dash()
        mock_btn = MagicMock()
        dash.reddit_grab_button = mock_btn
        dash._reddit_grab_running = True
        self._stub_set_helpers(dash, monkeypatch)

        dash._update_scan_button_state("idle")

        mock_btn.config.assert_called_with(state=tk.DISABLED)

    def test_none_button_does_not_raise_on_any_state(self, monkeypatch):
        """reddit_grab_button=None must not cause any error in any state."""
        dash = _make_dash()
        dash.reddit_grab_button = None
        self._stub_set_helpers(dash, monkeypatch)

        for state in ("idle", "scanning", "stopping", "disabled_external", "retry", "error"):
            dash._update_scan_button_state(state)  # must not raise


# ---------------------------------------------------------------------------
# Group B — click handler calls _check_external_scans before opening dialog
# ---------------------------------------------------------------------------

class TestClickHandlerGuards:

    def test_calls_check_external_scans_before_dialog(self, monkeypatch):
        dash = _make_dash()
        call_order = []

        monkeypatch.setattr(
            dash, "_check_external_scans",
            lambda: call_order.append("check"),
            raising=False,
        )
        monkeypatch.setattr(
            "gui.components.dashboard.show_reddit_grab_dialog",
            lambda **_k: call_order.append("dialog"),
        )

        dash._handle_reddit_grab_button_click()

        assert call_order == ["check", "dialog"]

    def test_does_not_open_dialog_if_not_idle_after_check(self, monkeypatch):
        dash = _make_dash()
        dialog_opened = []

        def _check_side_effect():
            dash.scan_button_state = "scanning"

        monkeypatch.setattr(dash, "_check_external_scans", _check_side_effect, raising=False)
        monkeypatch.setattr(
            "gui.components.dashboard.show_reddit_grab_dialog",
            lambda **_k: dialog_opened.append(True),
        )

        dash._handle_reddit_grab_button_click()

        assert dialog_opened == []

    def test_does_not_open_dialog_if_grab_already_running(self, monkeypatch):
        dash = _make_dash()
        dash._reddit_grab_running = True
        check_called = []
        dialog_opened = []

        monkeypatch.setattr(
            dash, "_check_external_scans",
            lambda: check_called.append(True),
            raising=False,
        )
        monkeypatch.setattr(
            "gui.components.dashboard.show_reddit_grab_dialog",
            lambda **_k: dialog_opened.append(True),
        )

        dash._handle_reddit_grab_button_click()

        assert check_called == []
        assert dialog_opened == []

    def test_does_not_call_maybe_warn_mock_mode(self, monkeypatch):
        """Reddit path must never call _maybe_warn_mock_mode_persistence."""
        dash = _make_dash()
        warned = []

        monkeypatch.setattr(
            dash, "_check_external_scans", lambda: None, raising=False
        )
        monkeypatch.setattr(
            dash, "_maybe_warn_mock_mode_persistence",
            lambda: warned.append(True),
            raising=False,
        )
        monkeypatch.setattr(
            "gui.components.dashboard.show_reddit_grab_dialog",
            lambda **_k: None,
        )

        dash._handle_reddit_grab_button_click()

        assert warned == []


# ---------------------------------------------------------------------------
# Group C — worker exception path always schedules done callback
# ---------------------------------------------------------------------------

class TestWorkerExceptionPath:

    def test_worker_exception_schedules_done_callback(self, monkeypatch):
        dash = _make_dash()
        after_calls = []
        dash.parent = MagicMock()
        dash.parent.after.side_effect = lambda delay, fn, result: after_calls.append((delay, fn, result))

        monkeypatch.setattr(
            "gui.components.dashboard.run_ingest",
            lambda _opts: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        options = _make_options()
        dash._reddit_grab_worker(options)

        assert len(after_calls) == 1
        delay, fn, result = after_calls[0]
        assert delay == 0
        assert fn == dash._on_reddit_grab_done
        assert result.error is not None
        assert "boom" in result.error

    def test_on_reddit_grab_done_resets_running_flag(self, monkeypatch):
        dash = _make_dash()
        dash._reddit_grab_running = True
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showerror",
            lambda *_a, **_k: None,
        )

        dash._on_reddit_grab_done(_make_ingest_result(error="something"))

        assert dash._reddit_grab_running is False

    def test_on_reddit_grab_done_resets_flag_on_success(self, monkeypatch):
        dash = _make_dash()
        dash._reddit_grab_running = True
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showinfo",
            lambda *_a, **_k: None,
        )
        dash._on_reddit_grab_done(_make_ingest_result())

        assert dash._reddit_grab_running is False

    def test_on_reddit_grab_done_re_enables_button_on_idle(self, monkeypatch):
        dash = _make_dash()
        dash._reddit_grab_running = True
        dash.scan_button_state = "idle"
        mock_btn = MagicMock()
        dash.reddit_grab_button = mock_btn
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showinfo",
            lambda *_a, **_k: None,
        )
        dash._on_reddit_grab_done(_make_ingest_result())

        mock_btn.config.assert_called_with(state=tk.NORMAL)

    def test_on_reddit_grab_done_does_not_enable_button_when_scan_active(self, monkeypatch):
        dash = _make_dash()
        dash._reddit_grab_running = True
        dash.scan_button_state = "scanning"
        mock_btn = MagicMock()
        dash.reddit_grab_button = mock_btn
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showinfo",
            lambda *_a, **_k: None,
        )
        dash._on_reddit_grab_done(_make_ingest_result())

        mock_btn.config.assert_not_called()

    def test_handle_reddit_grab_start_second_gate_blocks_if_scan_started(self, monkeypatch):
        """_handle_reddit_grab_start must abort if scan state changed since dialog opened."""
        dash = _make_dash()
        dash.scan_button_state = "idle"
        thread_started = []

        def _check_side_effect():
            dash.scan_button_state = "scanning"

        monkeypatch.setattr(dash, "_check_external_scans", _check_side_effect, raising=False)
        monkeypatch.setattr(
            "gui.components.dashboard.threading.Thread",
            lambda **_k: type("T", (), {"start": lambda self: thread_started.append(True)})(),
        )
        dash._log_status_event = lambda _msg: None

        dash._handle_reddit_grab_start(_make_options())

        assert dash._reddit_grab_running is False
        assert thread_started == []
