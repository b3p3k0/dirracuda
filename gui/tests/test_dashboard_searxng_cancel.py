"""Tests for C11A SearXNG satellite extraction and cancellation behavior."""
from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from experimental.se_dork.models import (
    RunResult,
    RUN_STATUS_CANCELLED,
    RUN_STATUS_DONE,
    RUN_STATUS_ERROR,
)


@pytest.fixture(autouse=True)
def _stub_satellite_mb(monkeypatch):
    """Prevent Tk messageboxes from the SearXNG satellite during all tests in this file."""
    mock_mb = MagicMock()
    mock_mb.showerror = lambda *a, **k: None
    mock_mb.showwarning = lambda *a, **k: None
    mock_mb.showinfo = lambda *a, **k: None
    monkeypatch.setattr("gui.components.dashboard_searxng_scan._mb", lambda: mock_mb)


def _make_result(status=RUN_STATUS_DONE, error=None, run_id=1, **kw):
    defaults = dict(
        run_id=run_id,
        fetched_count=0,
        deduped_count=0,
        status=status,
        error=error,
    )
    defaults.update(kw)
    return RunResult(**defaults)


def _make_dash():
    dash = MagicMock()
    dash._searxng_scan_running = False
    dash._searxng_cancel_event = None
    dash._provider_queue_active = False
    dash.parent = MagicMock()
    dash.parent.after = lambda delay, fn: fn()
    dash.settings_manager = None
    return dash


# ---------------------------------------------------------------------------
# Extraction regression
# ---------------------------------------------------------------------------

class TestExtractionRegression:
    def test_start_searxng_scan_exported_from_dashboard_scan(self):
        from gui.components.dashboard_scan import start_searxng_scan
        assert callable(start_searxng_scan)

    def test_on_searxng_scan_done_exported_from_dashboard_scan(self):
        from gui.components.dashboard_scan import _on_searxng_scan_done
        assert callable(_on_searxng_scan_done)

    def test_provider_registry_dispatch_finds_start_fn(self):
        from gui.components import dashboard_scan
        fn = getattr(dashboard_scan, "start_searxng_scan", None)
        assert callable(fn)

    def test_set_searxng_task_running_accepts_cancel_callback(self):
        from gui.components.dashboard_searxng_scan import _hook
        dash = MagicMock()
        dash._set_searxng_task_running = lambda country=None, *, cancel_callback=None: None
        # Should not raise
        _hook(dash, "_set_searxng_task_running", "US", cancel_callback=lambda: None)

    def test_thread_patch_path_is_satellite(self, monkeypatch):
        """Patching the satellite's threading.Thread intercepts start_searxng_scan."""
        from gui.components.dashboard_scan import start_searxng_scan

        class _NoThread:
            def __init__(self, *a, **k): pass
            def start(self): pass

        monkeypatch.setattr("gui.components.dashboard_searxng_scan.threading.Thread", _NoThread)
        monkeypatch.setattr(
            "gui.components.dashboard_searxng_scan._resolve_main_db_path",
            lambda _d: Path("/tmp/t.db"),
        )
        monkeypatch.setattr(
            "gui.components.dashboard_provider_queue.is_provider_queue_active",
            lambda _d: False,
        )

        dash = _make_dash()
        dash._set_searxng_task_running = lambda *_, **__: None
        dash._show_scan_output_dialog = lambda *_: None
        dash._reset_log_output = lambda *_: None
        dash._log_status_event = lambda *_: None
        result = start_searxng_scan(
            dash, {"searxng_instance_url": "http://x", "searxng_query": "q"}
        )
        assert result is True

    def test_satellite_db_path_resolver_patchable(self, monkeypatch, tmp_path):
        """Patching satellite's _resolve_main_db_path uses the tmp path."""
        from gui.components.dashboard_scan import start_searxng_scan

        captured_db = []

        class _SyncThread:
            def __init__(self, target, *a, **k):
                self._target = target
            def start(self):
                self._target()

        def _fake_run(options, *, db_path=None, progress_cb=None, cancel_event=None):
            captured_db.append(db_path)
            return RunResult(run_id=1, fetched_count=0, deduped_count=0,
                             status=RUN_STATUS_DONE, error=None)

        monkeypatch.setattr("gui.components.dashboard_searxng_scan.threading.Thread", _SyncThread)
        monkeypatch.setattr(
            "gui.components.dashboard_searxng_scan._resolve_main_db_path",
            lambda _d: tmp_path / "test.db",
        )
        monkeypatch.setattr("experimental.se_dork.service.run_dork_search", _fake_run)
        monkeypatch.setattr(
            "experimental.se_dork.main_db_sync.sync_run_to_main_db",
            lambda *_a, **_k: {"processed": 0, "inserted": 0, "updated": 0, "skipped": 0, "failed": 0},
        )
        monkeypatch.setattr(
            "gui.components.dashboard_provider_queue.is_provider_queue_active",
            lambda _d: False,
        )

        dash = _make_dash()
        dash._set_searxng_task_running = lambda *_, **__: None
        dash._show_scan_output_dialog = lambda *_: None
        dash._reset_log_output = lambda *_: None
        dash._log_status_event = lambda *_: None
        dash._clear_searxng_task = lambda: None
        dash._refresh_dashboard_data = lambda: None
        dash._show_scan_results = lambda *_: None
        dash._handle_scan_log_line = lambda *_: None

        start_searxng_scan(
            dash, {"searxng_instance_url": "http://x", "searxng_query": "q"}
        )

        assert captured_db and str(captured_db[0]) == str(tmp_path / "test.db"), \
            f"Expected {tmp_path / 'test.db'}, got {captured_db}"


# ---------------------------------------------------------------------------
# Sync condition
# ---------------------------------------------------------------------------

class TestSyncCondition:
    def _run_and_get_sync_calls(self, monkeypatch, tmp_path, result):
        from gui.components.dashboard_scan import start_searxng_scan

        sync_calls = []

        class _SyncThread:
            def __init__(self, target, *a, **k): self._t = target
            def start(self): self._t()

        monkeypatch.setattr("gui.components.dashboard_searxng_scan.threading.Thread", _SyncThread)
        monkeypatch.setattr(
            "gui.components.dashboard_searxng_scan._resolve_main_db_path",
            lambda _d: tmp_path / "test.db",
        )
        monkeypatch.setattr(
            "experimental.se_dork.service.run_dork_search",
            lambda *a, **k: result,
        )
        monkeypatch.setattr(
            "experimental.se_dork.main_db_sync.sync_run_to_main_db",
            lambda run_id, **k: sync_calls.append(run_id) or {"processed": 0, "inserted": 0,
                                                               "updated": 0, "skipped": 0, "failed": 0},
        )
        monkeypatch.setattr(
            "gui.components.dashboard_provider_queue.is_provider_queue_active",
            lambda _d: False,
        )

        dash = _make_dash()
        dash._set_searxng_task_running = lambda *_, **__: None
        dash._show_scan_output_dialog = lambda *_: None
        dash._reset_log_output = lambda *_: None
        dash._log_status_event = lambda *_: None
        dash._clear_searxng_task = lambda: None
        dash._refresh_dashboard_data = lambda: None
        dash._show_scan_results = lambda *_: None
        dash._handle_scan_log_line = lambda *_: None

        start_searxng_scan(
            dash, {"searxng_instance_url": "http://x", "searxng_query": "q"}
        )
        return sync_calls

    def test_cancelled_run_triggers_sync(self, monkeypatch, tmp_path):
        result = _make_result(status=RUN_STATUS_CANCELLED, run_id=5)
        calls = self._run_and_get_sync_calls(monkeypatch, tmp_path, result)
        assert 5 in calls

    def test_done_run_triggers_sync(self, monkeypatch, tmp_path):
        result = _make_result(status=RUN_STATUS_DONE, run_id=3)
        calls = self._run_and_get_sync_calls(monkeypatch, tmp_path, result)
        assert 3 in calls

    def test_error_run_skips_sync(self, monkeypatch, tmp_path):
        result = _make_result(status=RUN_STATUS_ERROR, error="oops", run_id=2)
        calls = self._run_and_get_sync_calls(monkeypatch, tmp_path, result)
        assert calls == []


# ---------------------------------------------------------------------------
# Completion behavior
# ---------------------------------------------------------------------------

class TestCompletionBehavior:
    def _invoke_done(self, monkeypatch, dash, result, **kwargs):
        from gui.components.dashboard_scan import _on_searxng_scan_done
        _on_searxng_scan_done(
            dash, result,
            instance_url="http://x",
            query="test",
            searxng_only=True,
            sync_summary=None,
            db_path="/tmp/test.db",
            queue_managed=False,
            provider_generation=0,
            **kwargs,
        )

    def test_cancelled_run_no_error_popup(self, monkeypatch):
        errors = []
        monkeypatch.setattr("gui.components.dashboard_searxng_scan._mb",
                            lambda: MagicMock(showerror=lambda *a, **k: errors.append(a)))
        dash = _make_dash()
        self._invoke_done(monkeypatch, dash, _make_result(status=RUN_STATUS_CANCELLED))
        assert errors == []

    def test_cancelled_run_no_success_popup(self):
        dash = _make_dash()
        from gui.components.dashboard_scan import _on_searxng_scan_done
        _on_searxng_scan_done(
            dash, _make_result(status=RUN_STATUS_CANCELLED),
            db_path="/tmp/test.db",
            searxng_only=True,
            sync_summary=None,
        )
        # _show_scan_results must not have been called
        assert not any(
            c[0][0] == "_show_scan_results"
            for c in dash.method_calls
            if c[0] and isinstance(c, tuple)
        )

    def test_cancelled_run_emits_rollup(self):
        dash = _make_dash()
        logged = []
        dash._handle_scan_log_line = lambda s: logged.append(s)
        from gui.components.dashboard_scan import _on_searxng_scan_done
        _on_searxng_scan_done(
            dash, _make_result(status=RUN_STATUS_CANCELLED),
            db_path="/tmp/test.db",
            query="test query",
            sync_summary=None,
        )
        combined = "\n".join(logged)
        assert "⚠ Scan cancelled" in combined, f"Expected rollup in: {logged}"

    def test_queue_not_advanced_on_cancel(self, monkeypatch):
        advanced = []
        monkeypatch.setattr(
            "gui.components.dashboard_provider_queue.complete_provider",
            lambda *a, **k: advanced.append(a),
        )
        dash = _make_dash()
        from gui.components.dashboard_scan import _on_searxng_scan_done
        _on_searxng_scan_done(
            dash, _make_result(status=RUN_STATUS_CANCELLED),
            db_path="/tmp/test.db",
            queue_managed=True,
            provider_generation=1,
            sync_summary=None,
        )
        assert advanced == []


# ---------------------------------------------------------------------------
# Cancel callback routing
# ---------------------------------------------------------------------------

class TestCancelCallbacks:
    def test_standalone_cancel_callback_signals_only_event(self):
        from gui.components.dashboard_searxng_scan import _build_cancel_callback

        evt = threading.Event()
        dash = MagicMock()
        dash._searxng_cancel_event = evt

        cb = _build_cancel_callback(dash, queue_managed=False)
        assert cb is not None
        cb()
        assert evt.is_set()

    def test_queue_cancel_callback_calls_cancel_provider_queue(self, monkeypatch):
        from gui.components.dashboard_searxng_scan import _build_cancel_callback

        calls = []
        monkeypatch.setattr(
            "gui.components.dashboard_provider_queue.cancel_provider_queue",
            lambda d, *, notify: calls.append(notify),
        )
        dash = MagicMock()
        cb = _build_cancel_callback(dash, queue_managed=True)
        assert cb is not None
        cb()
        assert calls == [True]


# ---------------------------------------------------------------------------
# Event lifecycle
# ---------------------------------------------------------------------------

class TestEventLifecycle:
    def _do_run(self, monkeypatch, tmp_path, result):
        from gui.components.dashboard_scan import start_searxng_scan

        class _SyncThread:
            def __init__(self, target, *a, **k): self._t = target
            def start(self): self._t()

        monkeypatch.setattr("gui.components.dashboard_searxng_scan.threading.Thread", _SyncThread)
        monkeypatch.setattr(
            "gui.components.dashboard_searxng_scan._resolve_main_db_path",
            lambda _d: tmp_path / "test.db",
        )
        monkeypatch.setattr("experimental.se_dork.service.run_dork_search",
                            lambda *a, **k: result)
        monkeypatch.setattr(
            "experimental.se_dork.main_db_sync.sync_run_to_main_db",
            lambda *_a, **_k: {"processed": 0, "inserted": 0, "updated": 0, "skipped": 0, "failed": 0},
        )
        monkeypatch.setattr(
            "gui.components.dashboard_provider_queue.is_provider_queue_active",
            lambda _d: False,
        )

        dash = _make_dash()
        dash._set_searxng_task_running = lambda *_, **__: None
        dash._show_scan_output_dialog = lambda *_: None
        dash._reset_log_output = lambda *_: None
        dash._log_status_event = lambda *_: None
        dash._clear_searxng_task = lambda: None
        dash._refresh_dashboard_data = lambda: None
        dash._show_scan_results = lambda *_: None
        dash._handle_scan_log_line = lambda *_: None

        start_searxng_scan(dash, {"searxng_instance_url": "http://x", "searxng_query": "q"})
        return dash

    def test_cancel_event_cleared_on_success(self, monkeypatch, tmp_path):
        dash = self._do_run(monkeypatch, tmp_path, _make_result(status=RUN_STATUS_DONE))
        assert dash._searxng_cancel_event is None

    def test_cancel_event_cleared_on_error(self, monkeypatch, tmp_path):
        dash = self._do_run(monkeypatch, tmp_path,
                            _make_result(status=RUN_STATUS_ERROR, error="boom"))
        assert dash._searxng_cancel_event is None

    def test_cancel_event_cleared_on_cancel(self, monkeypatch, tmp_path):
        dash = self._do_run(monkeypatch, tmp_path,
                            _make_result(status=RUN_STATUS_CANCELLED))
        assert dash._searxng_cancel_event is None

    def test_cancel_event_cleared_on_thread_launch_failure(self, monkeypatch, tmp_path):
        from gui.components.dashboard_scan import start_searxng_scan

        class _FailThread:
            def __init__(self, *a, **k): pass
            def start(self): raise RuntimeError("cannot start thread")

        monkeypatch.setattr("gui.components.dashboard_searxng_scan.threading.Thread", _FailThread)
        monkeypatch.setattr(
            "gui.components.dashboard_searxng_scan._resolve_main_db_path",
            lambda _d: tmp_path / "test.db",
        )
        monkeypatch.setattr(
            "gui.components.dashboard_provider_queue.is_provider_queue_active",
            lambda _d: False,
        )
        monkeypatch.setattr(
            "gui.components.dashboard_provider_queue.report_launch_error",
            lambda *a, **k: None,
        )

        dash = _make_dash()
        dash._set_searxng_task_running = lambda *_, **__: None
        dash._show_scan_output_dialog = lambda *_: None
        dash._reset_log_output = lambda *_: None
        dash._log_status_event = lambda *_: None
        dash._clear_searxng_task = lambda: None

        start_searxng_scan(dash, {"searxng_instance_url": "http://x", "searxng_query": "q"})
        assert dash._searxng_cancel_event is None

    def test_cancel_event_cleared_on_after_scheduling_failure(self, monkeypatch, tmp_path):
        """If parent.after() raises inside the worker, _searxng_cancel_event must still be cleared."""
        from gui.components.dashboard_scan import start_searxng_scan

        class _SyncThread:
            def __init__(self, target, *a, **k): self._t = target
            def start(self): self._t()

        monkeypatch.setattr("gui.components.dashboard_searxng_scan.threading.Thread", _SyncThread)
        monkeypatch.setattr(
            "gui.components.dashboard_searxng_scan._resolve_main_db_path",
            lambda _d: tmp_path / "test.db",
        )
        monkeypatch.setattr("experimental.se_dork.service.run_dork_search",
                            lambda *a, **k: _make_result(status=RUN_STATUS_DONE))
        monkeypatch.setattr(
            "experimental.se_dork.main_db_sync.sync_run_to_main_db",
            lambda *_a, **_k: {"processed": 0, "inserted": 0, "updated": 0,
                                "skipped": 0, "failed": 0},
        )
        monkeypatch.setattr(
            "gui.components.dashboard_provider_queue.is_provider_queue_active",
            lambda _d: False,
        )

        dash = _make_dash()
        dash.parent.after = lambda delay, fn: (_ for _ in ()).throw(RuntimeError("Tk not available"))
        dash._set_searxng_task_running = lambda *_, **__: None
        dash._show_scan_output_dialog = lambda *_: None
        dash._reset_log_output = lambda *_: None
        dash._log_status_event = lambda *_: None
        dash._clear_searxng_task = lambda: None

        start_searxng_scan(dash, {"searxng_instance_url": "http://x", "searxng_query": "q"})
        assert dash._searxng_cancel_event is None


# ---------------------------------------------------------------------------
# C11B — RunOptions tuning field propagation
# ---------------------------------------------------------------------------

class TestRunOptionsTuningPropagation:
    """Verify that scan_request tuning keys are coerced and forwarded to RunOptions."""

    def _capture_options(self, scan_request: dict, monkeypatch):
        """Run start_searxng_scan with a monkeypatched run_dork_search and return
        the RunOptions instance that was constructed."""
        from gui.components.dashboard_searxng_scan import start_searxng_scan
        from experimental.se_dork.models import RunResult, RUN_STATUS_DONE

        captured = {}

        def _fake_run(options, db_path=None, progress_cb=None, *, cancel_event=None):
            captured["options"] = options
            return RunResult(
                run_id=None, fetched_count=0, deduped_count=0,
                status=RUN_STATUS_DONE, error=None,
            )

        monkeypatch.setattr("experimental.se_dork.service.run_dork_search", _fake_run)
        monkeypatch.setattr(
            "experimental.se_dork.main_db_sync.sync_run_to_main_db",
            lambda run_id, db_path: None,
        )

        dash = _make_dash()
        dash.parent.after = lambda delay, fn: fn()
        start_searxng_scan(dash, scan_request)
        return captured.get("options")

    def test_request_timeout_propagated(self, monkeypatch, tmp_path):
        opts = self._capture_options({
            "searxng_instance_url": "http://x",
            "searxng_query": "q",
            "searxng_request_timeout": 20,
        }, monkeypatch)
        assert opts is not None
        assert opts.request_timeout == 20

    def test_short_retry_propagated(self, monkeypatch, tmp_path):
        opts = self._capture_options({
            "searxng_instance_url": "http://x",
            "searxng_query": "q",
            "searxng_short_retry_delay": 45,
        }, monkeypatch)
        assert opts is not None
        assert opts.short_retry_delay == 45

    def test_long_retry_propagated(self, monkeypatch, tmp_path):
        opts = self._capture_options({
            "searxng_instance_url": "http://x",
            "searxng_query": "q",
            "searxng_long_retry_delay": 240,
        }, monkeypatch)
        assert opts is not None
        assert opts.long_retry_delay == 240

    def test_defaults_when_keys_absent(self, monkeypatch, tmp_path):
        opts = self._capture_options({
            "searxng_instance_url": "http://x",
            "searxng_query": "q",
        }, monkeypatch)
        assert opts is not None
        assert opts.request_timeout == 15
        assert opts.short_retry_delay == 30
        assert opts.long_retry_delay == 180

    def test_step_snapped_not_just_clamped(self, monkeypatch, tmp_path):
        # 12 with step=5 → 10, not just range-clamped 12
        opts = self._capture_options({
            "searxng_instance_url": "http://x",
            "searxng_query": "q",
            "searxng_short_retry_delay": 12,
        }, monkeypatch)
        assert opts is not None
        assert opts.short_retry_delay == 10

    def test_out_of_range_clamped(self, monkeypatch, tmp_path):
        opts = self._capture_options({
            "searxng_instance_url": "http://x",
            "searxng_query": "q",
            "searxng_long_retry_delay": 400,
        }, monkeypatch)
        assert opts is not None
        assert opts.long_retry_delay == 300
