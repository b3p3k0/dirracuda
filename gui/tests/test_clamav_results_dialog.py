"""C5 tests: ClamAV results dialog and session mute.

Groups:
  1.  session_flags — set/get/clear, isolation
  2.  should_show_clamav_dialog — all gate conditions incl. string coercion
  3.  Dialog construction — Toplevel returned, totals, rows
  4.  Mute button — callback + flag set
  5.  Fail-safe — Toplevel raises → returns None
  6.  Return dict regression — _extract_single_server / _execute_extract_target carry "clamav"
  7–10. Wiring — dashboard post-scan and server-list finalize call/no-call
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# 1. session_flags
# ---------------------------------------------------------------------------

class TestSessionFlags:
    @pytest.fixture(autouse=True)
    def _reset_flags(self):
        from gui.utils import session_flags
        session_flags._flags.clear()
        yield
        session_flags._flags.clear()

    def test_set_and_get(self):
        from gui.utils import session_flags
        session_flags.set_flag("foo")
        assert session_flags.get_flag("foo") is True

    def test_get_default_when_absent(self):
        from gui.utils import session_flags
        assert session_flags.get_flag("missing") is False
        assert session_flags.get_flag("missing", default=True) is True

    def test_clear_removes_flag(self):
        from gui.utils import session_flags
        session_flags.set_flag("bar", True)
        session_flags.clear_flag("bar")
        assert session_flags.get_flag("bar") is False

    def test_clear_noop_on_absent(self):
        from gui.utils import session_flags
        session_flags.clear_flag("never_set")  # must not raise

    def test_set_false(self):
        from gui.utils import session_flags
        session_flags.set_flag("x", False)
        assert session_flags.get_flag("x") is False


# ---------------------------------------------------------------------------
# 2. should_show_clamav_dialog
# ---------------------------------------------------------------------------

def _one_enabled_result() -> List[Dict[str, Any]]:
    return [{"ip_address": "1.2.3.4", "clamav": {"enabled": True, "files_scanned": 1}}]


class TestShouldShowClamavDialog:
    @pytest.fixture(autouse=True)
    def _reset_flags(self):
        from gui.utils import session_flags
        session_flags._flags.clear()
        yield
        session_flags._flags.clear()

    def test_true_on_happy_path(self):
        from gui.components.clamav_results_dialog import should_show_clamav_dialog
        assert should_show_clamav_dialog("extract", _one_enabled_result(), {}) is True

    def test_false_when_not_extract(self):
        from gui.components.clamav_results_dialog import should_show_clamav_dialog
        assert should_show_clamav_dialog("probe", _one_enabled_result(), {}) is False

    def test_false_when_muted(self):
        from gui.utils import session_flags
        from gui.components.clamav_results_dialog import should_show_clamav_dialog
        session_flags.set_flag(session_flags.CLAMAV_MUTE_KEY)
        assert should_show_clamav_dialog("extract", _one_enabled_result(), {}) is False

    def test_false_when_no_enabled_result(self):
        from gui.components.clamav_results_dialog import should_show_clamav_dialog
        results = [{"clamav": {"enabled": False}}, {"clamav": {}}]
        assert should_show_clamav_dialog("extract", results, {}) is False

    def test_false_when_show_results_false_bool(self):
        from gui.components.clamav_results_dialog import should_show_clamav_dialog
        assert should_show_clamav_dialog("extract", _one_enabled_result(), {"show_results": False}) is False

    def test_false_when_show_results_string_false(self):
        from gui.components.clamav_results_dialog import should_show_clamav_dialog
        assert should_show_clamav_dialog("extract", _one_enabled_result(), {"show_results": "false"}) is False

    def test_false_when_show_results_string_zero(self):
        from gui.components.clamav_results_dialog import should_show_clamav_dialog
        assert should_show_clamav_dialog("extract", _one_enabled_result(), {"show_results": "0"}) is False

    def test_true_when_show_results_absent(self):
        from gui.components.clamav_results_dialog import should_show_clamav_dialog
        assert should_show_clamav_dialog("extract", _one_enabled_result(), {}) is True

    def test_true_when_show_results_string_true(self):
        from gui.components.clamav_results_dialog import should_show_clamav_dialog
        assert should_show_clamav_dialog("extract", _one_enabled_result(), {"show_results": "true"}) is True

    def test_false_empty_results(self):
        from gui.components.clamav_results_dialog import should_show_clamav_dialog
        assert should_show_clamav_dialog("extract", [], {}) is False


# ---------------------------------------------------------------------------
# 3. Dialog construction
# ---------------------------------------------------------------------------

class TestShowClamavResultsDialog:
    @pytest.fixture(autouse=True)
    def _reset_flags(self):
        from gui.utils import session_flags
        session_flags._flags.clear()
        yield
        session_flags._flags.clear()

    def _make_results(self, infected=1, errors=0) -> List[Dict[str, Any]]:
        infected_items = [{"path": f"f{i}.txt", "signature": "EICAR", "moved_to": "/q/f.txt"} for i in range(infected)]
        error_items = [{"path": f"e{i}.txt", "error": "timeout"} for i in range(errors)]
        return [{
            "ip_address": "1.2.3.4",
            "clamav": {
                "enabled": True,
                "files_scanned": 5,
                "clean": 4 - infected,
                "infected": infected,
                "errors": errors,
                "promoted": 4 - infected,
                "known_bad_moved": infected,
                "infected_items": infected_items,
                "error_items": error_items,
            }
        }]

    def test_returns_toplevel(self, tmp_path):
        import tkinter as tk
        from gui.components.clamav_results_dialog import show_clamav_results_dialog
        root = tk.Tk()
        root.withdraw()
        try:
            dlg = show_clamav_results_dialog(
                parent=root, theme=None, results=self._make_results(),
                on_mute=lambda: None, wait=False, modal=False,
            )
            assert dlg is not None
            assert dlg.winfo_exists()
            dlg.destroy()
        finally:
            root.destroy()

    def test_returns_none_on_render_failure(self):
        from gui.components.clamav_results_dialog import show_clamav_results_dialog
        with patch("gui.components.clamav_results_dialog.tk.Toplevel", side_effect=RuntimeError("boom")):
            result = show_clamav_results_dialog(
                parent=MagicMock(), theme=None,
                results=self._make_results(), on_mute=lambda: None,
            )
            assert result is None


# ---------------------------------------------------------------------------
# 4. Mute button
# ---------------------------------------------------------------------------

class TestMuteButton:
    @pytest.fixture(autouse=True)
    def _reset_flags(self):
        from gui.utils import session_flags
        session_flags._flags.clear()
        yield
        session_flags._flags.clear()

    def test_mute_sets_flag(self):
        import tkinter as tk
        from gui.utils import session_flags
        from gui.components.clamav_results_dialog import show_clamav_results_dialog

        muted = []

        def _on_mute():
            session_flags.set_flag(session_flags.CLAMAV_MUTE_KEY)
            muted.append(True)

        results = [{"ip_address": "1.2.3.4", "clamav": {"enabled": True, "files_scanned": 1,
                    "clean": 1, "infected": 0, "errors": 0, "infected_items": [], "error_items": []}}]

        root = tk.Tk()
        root.withdraw()
        try:
            dlg = show_clamav_results_dialog(
                parent=root, theme=None, results=results,
                on_mute=_on_mute, wait=False, modal=False,
            )
            assert dlg is not None
            # Find and invoke the mute button command directly (no click simulation needed)
            for child in dlg.winfo_children():
                for w in child.winfo_children():
                    if isinstance(w, tk.Button) and "mute" in str(w.cget("text")).lower():
                        w.invoke()
                        break
            assert muted, "on_mute was not called"
            assert session_flags.get_flag(session_flags.CLAMAV_MUTE_KEY) is True
        finally:
            try:
                dlg.destroy()
            except Exception:
                pass
            root.destroy()


# ---------------------------------------------------------------------------
# 5. Fail-safe (already covered by test_returns_none_on_render_failure above)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 6. Return dict regression
# ---------------------------------------------------------------------------

class TestReturnDictRegression:
    """_extract_single_server and _execute_extract_target must carry a 'clamav' key on success."""

    def _fake_summary(self):
        return {
            "totals": {"files_downloaded": 1, "bytes_downloaded": 100},
            "timed_out": False,
            "stop_reason": None,
            "clamav": {"enabled": True, "files_scanned": 1},
        }

    def test_dashboard_extract_single_server_carries_clamav(self, tmp_path):
        import threading
        from unittest.mock import MagicMock, patch
        from gui.components.dashboard import DashboardWidget

        dash = object.__new__(DashboardWidget)
        dash.settings_manager = MagicMock()
        dash.db_reader = MagicMock()

        server = {
            "ip_address": "1.2.3.4",
            "auth_method": "anonymous",
            "accessible_shares_list": "public",
        }

        cancel = threading.Event()
        with patch("gui.components.dashboard.create_quarantine_dir", return_value=tmp_path / "q"), \
             patch("gui.components.dashboard.extract_runner.run_extract", return_value=self._fake_summary()):
            result = dash._extract_single_server(
                server,
                max_file_mb=50,
                max_total_mb=200,
                max_time=60,
                max_files=10,
                extension_mode="allow_only",
                included_extensions=[],
                excluded_extensions=[],
                quarantine_base_path=None,
                cancel_event=cancel,
                clamav_config={"enabled": True},
            )

        assert result["status"] == "success"
        assert "clamav" in result
        assert result["clamav"]["enabled"] is True

    def test_batch_execute_extract_target_carries_clamav(self, tmp_path):
        import threading
        from unittest.mock import MagicMock, patch
        from gui.components.server_list_window.actions.batch import ServerListWindowBatchMixin

        obj = object.__new__(ServerListWindowBatchMixin)
        obj.window = MagicMock()
        obj.active_jobs = {"j1": {"dialog": None, "total": 1}}
        obj.db_reader = MagicMock()

        target = {
            "ip_address": "1.2.3.4",
            "host_type": "S",
            "row_key": "rk1",
            "shares": ["public"],
            "auth_method": "anonymous",
        }
        options = {
            "download_path": str(tmp_path),
            "max_total_size_mb": 200,
            "max_file_size_mb": 50,
            "max_files_per_target": 10,
            "max_time_seconds": 60,
            "max_directory_depth": 3,
            "included_extensions": [],
            "excluded_extensions": [],
            "download_delay_seconds": 0,
            "connection_timeout": 30,
            "extension_mode": "allow_only",
            "clamav_config": {"enabled": True},
        }
        cancel = threading.Event()

        with patch("gui.components.server_list_window.actions.batch.create_quarantine_dir", return_value=tmp_path / "q"), \
             patch("gui.components.server_list_window.actions.batch.extract_runner.run_extract", return_value=self._fake_summary()), \
             patch("gui.components.server_list_window.actions.batch.extract_runner.write_extract_log", return_value=tmp_path / "log.json"):
            with patch.object(obj, "_handle_extracted_update"):
                with patch.object(obj, "_update_batch_status_dialog"):
                    result = obj._execute_extract_target("j1", target, options, cancel)

        assert result["status"] == "success"
        assert "clamav" in result
        assert result["clamav"]["enabled"] is True


# ---------------------------------------------------------------------------
# 7–10. Wiring tests
# ---------------------------------------------------------------------------

def _make_clamav_result(ip="1.2.3.4"):
    return {
        "ip_address": ip,
        "action": "extract",
        "status": "success",
        "notes": "1 file(s)",
        "clamav": {
            "enabled": True,
            "files_scanned": 1,
            "clean": 1,
            "infected": 0,
            "errors": 0,
            "promoted": 1,
            "known_bad_moved": 0,
            "infected_items": [],
            "error_items": [],
        },
    }


class TestDashboardWiring:
    """Tests 7–8: dashboard _run_post_scan_batch_operations calls dialog correctly."""

    @pytest.fixture(autouse=True)
    def _reset_flags(self):
        from gui.utils import session_flags
        session_flags._flags.clear()
        yield
        session_flags._flags.clear()

    def _make_dashboard_stub(self, extract_results):
        """Build a minimal DashboardWidget stub for testing _maybe_show_clamav_dialog."""
        from gui.components.dashboard import DashboardWidget

        stub = object.__new__(DashboardWidget)
        stub.parent = MagicMock()
        stub.theme = None
        return stub

    def test_dialog_shown_when_enabled(self):
        results = [_make_clamav_result()]
        stub = self._make_dashboard_stub(results)

        with patch("gui.components.clamav_results_dialog.show_clamav_results_dialog") as mock_dialog, \
             patch("gui.components.clamav_results_dialog.should_show_clamav_dialog", return_value=True):
            stub._maybe_show_clamav_dialog(results, {"enabled": True, "show_results": True}, wait=True, modal=True)
            mock_dialog.assert_called_once()
            _, kwargs = mock_dialog.call_args
            assert kwargs["wait"] is True
            assert kwargs["modal"] is True

    def test_dialog_not_shown_when_muted(self):
        from gui.utils import session_flags
        session_flags.set_flag(session_flags.CLAMAV_MUTE_KEY)

        results = [_make_clamav_result()]
        stub = self._make_dashboard_stub(results)

        with patch("gui.components.clamav_results_dialog.show_clamav_results_dialog") as mock_dialog:
            stub._maybe_show_clamav_dialog(results, {"enabled": True, "show_results": True}, wait=True, modal=True)
            mock_dialog.assert_not_called()


class TestServerListWiring:
    """Tests 9–10: _finalize_batch_job calls dialog inside show_summary guard."""

    @pytest.fixture(autouse=True)
    def _reset_flags(self):
        from gui.utils import session_flags
        session_flags._flags.clear()
        yield
        session_flags._flags.clear()

    def _make_mixin_stub(self, results, clamav_config):
        from gui.components.server_list_window.actions.batch_status import ServerListWindowBatchStatusMixin

        stub = object.__new__(ServerListWindowBatchStatusMixin)
        stub.window = MagicMock()
        stub.theme = None
        stub.active_jobs = {}
        stub.batch_status_dialog = None
        stub._pending_table_refresh = False
        stub._pending_selection = []

        job_id = "extract-1"
        stub.active_jobs[job_id] = {
            "id": job_id,
            "type": "extract",
            "results": list(results),
            "completed": len(results),
            "total": len(results),
            "unit_label": "targets",
            "futures": [],
            "dialog": None,
            "executor": MagicMock(),
            "options": {"clamav_config": clamav_config},
        }

        stub._show_batch_summary = MagicMock()
        stub._update_action_buttons_state = MagicMock()
        stub._set_status = MagicMock()
        stub._flush_pending_refresh = MagicMock()
        stub._set_table_interaction_enabled = MagicMock()
        stub._update_stop_button_style = MagicMock()
        stub._finish_batch_status_dialog = MagicMock()
        stub._set_pry_status_button_visible = MagicMock()
        stub._widget_exists = MagicMock(return_value=True)

        return stub, job_id

    def test_dialog_shown_from_finalize(self):
        results = [_make_clamav_result()]
        stub, job_id = self._make_mixin_stub(results, {"enabled": True, "show_results": True})

        with patch("gui.components.clamav_results_dialog.show_clamav_results_dialog") as mock_dialog, \
             patch("gui.components.clamav_results_dialog.should_show_clamav_dialog", return_value=True):
            stub._finalize_batch_job(job_id, show_summary=True)
            mock_dialog.assert_called_once()
            _, kwargs = mock_dialog.call_args
            assert kwargs["wait"] is False
            assert kwargs["modal"] is False

    def test_dialog_not_shown_when_muted(self):
        from gui.utils import session_flags
        session_flags.set_flag(session_flags.CLAMAV_MUTE_KEY)

        results = [_make_clamav_result()]
        stub, job_id = self._make_mixin_stub(results, {"enabled": True, "show_results": True})

        with patch("gui.components.clamav_results_dialog.show_clamav_results_dialog") as mock_dialog:
            stub._finalize_batch_job(job_id, show_summary=True)
            mock_dialog.assert_not_called()

    def test_dialog_not_shown_when_show_summary_false(self):
        results = [_make_clamav_result()]
        stub, job_id = self._make_mixin_stub(results, {"enabled": True, "show_results": True})

        with patch("gui.components.clamav_results_dialog.show_clamav_results_dialog") as mock_dialog, \
             patch("gui.components.clamav_results_dialog.should_show_clamav_dialog", return_value=True):
            stub._finalize_batch_job(job_id, show_summary=False)
            mock_dialog.assert_not_called()
