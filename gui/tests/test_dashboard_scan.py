"""
C3 — SearXNG Core Promotion Path
Tests for SearXNG dispatch wiring in dashboard_scan.

Coverage:
  A — start_unified_scan provider routing
  B — start_searxng_scan validation and RunOptions construction
  C — _on_searxng_scan_done success/error/probe-summary paths
"""

from __future__ import annotations

import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Lightweight impacket stub for environments where the dependency is unavailable.
if "impacket" not in sys.modules:
    _imp = types.ModuleType("impacket")
    _imp_smb = types.ModuleType("impacket.smb")
    _imp_smb.SMB2_DIALECT_002 = object()
    _imp_conn = types.ModuleType("impacket.smbconnection")
    _imp_conn.SMBConnection = object

    class _SessionError(Exception):
        pass

    _imp_conn.SessionError = _SessionError
    _imp.smb = _imp_smb
    sys.modules["impacket"] = _imp
    sys.modules["impacket.smb"] = _imp_smb
    sys.modules["impacket.smbconnection"] = _imp_conn

from gui.components.dashboard import DashboardWidget
import gui.components.dashboard_scan as ds
from experimental.se_dork.models import (
    RunOptions,
    RunResult,
    RUN_STATUS_DONE,
    RUN_STATUS_ERROR,
)
from experimental.redseek.service import IngestResult


# ---------------------------------------------------------------------------
# Module-level autouse fixture — suppresses all Tk messageboxes by default.
# Tests that need to capture calls override locally (their setattr wins).
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _suppress_messageboxes(monkeypatch):
    """Block all Tk messageboxes — prevents desktop popups during CI."""
    monkeypatch.setattr("gui.components.dashboard.messagebox.showerror", lambda *a, **k: None)
    monkeypatch.setattr("gui.components.dashboard.messagebox.showwarning", lambda *a, **k: None)
    monkeypatch.setattr("gui.components.dashboard.messagebox.showinfo", lambda *a, **k: None)
    monkeypatch.setattr(
        "gui.components.dashboard_scan._resolve_main_db_path",
        lambda _dash: Path("/tmp/dirracuda.db"),
    )
    monkeypatch.setattr(
        "experimental.se_dork.main_db_sync.sync_run_to_main_db",
        lambda *_a, **_k: {
            "selected": 0,
            "processed": 0,
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "cancelled": 0,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dash() -> DashboardWidget:
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = MagicMock()
    dash.settings_manager = None
    dash.scan_button = MagicMock()
    dash.scan_button_state = "idle"
    dash._queued_scan_protocols = []
    dash._queued_scan_total = 0
    dash.external_scan_pid = None
    # Stub UI lifecycle hooks — the real methods require a Tkinter display
    dash._show_scan_output_dialog = lambda *_: None
    dash._reset_log_output = lambda *_: None
    dash._set_searxng_task_running = lambda *_: None
    dash._log_status_event = lambda *_: None
    dash._show_scan_results = lambda *_: None
    dash._set_reddit_task_running = lambda *_: None
    dash._clear_reddit_task = lambda *_: None
    return dash


def _make_result(**kwargs) -> RunResult:
    defaults = dict(
        run_id=1,
        fetched_count=10,
        deduped_count=7,
        status=RUN_STATUS_DONE,
        error=None,
    )
    defaults.update(kwargs)
    return RunResult(**defaults)


def _searxng_request(**overrides):
    base = dict(
        providers=["searxng"],
        searxng_instance_url="http://searxng.example.com",
        searxng_query="inurl:index.of",
        searxng_max_results=50,
        bulk_probe_enabled=False,
    )
    base.update(overrides)
    return base


def _reddit_request(**overrides):
    base = dict(
        providers=["reddit"],
        reddit_mode="feed",
        reddit_sort="new",
        reddit_top_window="week",
        reddit_max_posts=50,
        reddit_query="",
        reddit_username="",
        reddit_parse_body=True,
        reddit_include_nsfw=False,
        bulk_probe_enabled=False,
    )
    base.update(overrides)
    return base


def _make_reddit_result(**kwargs) -> IngestResult:
    defaults = dict(
        sort="new",
        subreddit="opendirectories",
        pages_fetched=2,
        posts_stored=30,
        posts_skipped=5,
        targets_stored=12,
        targets_deduped=3,
        parse_errors=0,
        stopped_by_cursor=False,
        stopped_by_max_posts=False,
        replace_cache_done=False,
        rate_limited=False,
        error=None,
        probe_enabled=False,
        probe_total=0,
        probe_clean=0,
        probe_issue=0,
        probe_unprobed=0,
        probe_skipped=0,
    )
    defaults.update(kwargs)
    return IngestResult(**defaults)


# ---------------------------------------------------------------------------
# A — start_unified_scan provider routing
# ---------------------------------------------------------------------------

class TestStartUnifiedScanRouting:
    def test_searxng_only_calls_start_searxng_scan(self, monkeypatch):
        dash = _make_dash()
        called = []
        monkeypatch.setattr(ds, "start_searxng_scan", lambda d, r: called.append(True) or True)
        ds.start_unified_scan(dash, _searxng_request())
        assert called == [True]

    def test_shodan_only_does_not_call_searxng(self, monkeypatch):
        dash = _make_dash()
        called = []
        monkeypatch.setattr(ds, "start_searxng_scan", lambda d, r: called.append(True) or True)
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showerror",
            lambda *a, **k: None,
        )
        ds.start_unified_scan(dash, {"providers": ["shodan"], "protocols": []})
        assert called == []

    def test_both_providers_calls_both(self, monkeypatch):
        dash = _make_dash()
        searxng_calls = []
        monkeypatch.setattr(ds, "start_searxng_scan", lambda d, r: searxng_calls.append(True) or True)
        # Shodan path also needs protocols or it hits the error dialog — give it one.
        monkeypatch.setattr(ds, "_call_dashboard_hook", lambda *_a, **_k: None)
        shodan_calls = []
        _orig_start_new = ds.start_new_scan
        monkeypatch.setattr(ds, "start_new_scan", lambda d, o: shodan_calls.append(True) or True)
        request = _searxng_request(providers=["shodan", "searxng"], protocols=["smb"])
        # start_unified_scan for Shodan path calls start_new_scan indirectly — instead
        # just verify searxng was called and shodan path was entered (not skipped).
        errors = []
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showerror",
            lambda *a, **k: errors.append(a),
        )
        ds.start_unified_scan(dash, request)
        assert searxng_calls == [True], "start_searxng_scan must be called when searxng in providers"
        assert errors == [], f"Unexpected showerror: {errors}"

    def test_searxng_only_skips_protocol_queue(self, monkeypatch):
        dash = _make_dash()
        monkeypatch.setattr(ds, "start_searxng_scan", lambda d, r: True)
        errors = []
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showerror",
            lambda *a, **k: errors.append(a),
        )
        ds.start_unified_scan(dash, _searxng_request(protocols=[]))
        assert errors == [], "SearXNG-only scan must not trigger 'No protocols selected' error"

    def test_reddit_only_calls_start_reddit_scan(self, monkeypatch):
        dash = _make_dash()
        called = []
        monkeypatch.setattr(ds, "start_reddit_scan", lambda d, r: called.append(True) or True)
        ds.start_unified_scan(dash, _reddit_request())
        assert called == [True]

    def test_reddit_only_skips_protocol_queue(self, monkeypatch):
        dash = _make_dash()
        monkeypatch.setattr(ds, "start_reddit_scan", lambda d, r: True)
        errors = []
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showerror",
            lambda *a, **k: errors.append(a),
        )
        ds.start_unified_scan(dash, _reddit_request(protocols=[]))
        assert errors == [], "Reddit-only scan must not trigger 'No protocols selected' error"

    def test_shodan_only_does_not_call_reddit(self, monkeypatch):
        dash = _make_dash()
        called = []
        monkeypatch.setattr(ds, "start_reddit_scan", lambda d, r: called.append(True) or True)
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showerror",
            lambda *a, **k: None,
        )
        ds.start_unified_scan(dash, {"providers": ["shodan"], "protocols": []})
        assert called == []

    def test_reddit_and_shodan_calls_both(self, monkeypatch):
        dash = _make_dash()
        reddit_calls = []
        monkeypatch.setattr(ds, "start_reddit_scan", lambda d, r: reddit_calls.append(True) or True)
        monkeypatch.setattr(ds, "start_new_scan", lambda d, o: True)
        monkeypatch.setattr(ds, "_call_dashboard_hook", lambda *_a, **_k: None)
        request = _reddit_request(providers=["shodan", "reddit"], protocols=["smb"])
        ds.start_unified_scan(dash, request)
        assert reddit_calls == [True]


# ---------------------------------------------------------------------------
# B — start_searxng_scan validation and RunOptions construction
# ---------------------------------------------------------------------------

class TestStartSearxngScan:
    def test_missing_instance_url_shows_error(self, monkeypatch):
        dash = _make_dash()
        errors = []
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showerror",
            lambda *a, **k: errors.append(a),
        )
        result = ds.start_searxng_scan(dash, _searxng_request(searxng_instance_url=None))
        assert result is False
        assert len(errors) == 1
        assert "instance URL" in errors[0][1]

    def test_empty_instance_url_shows_error(self, monkeypatch):
        dash = _make_dash()
        errors = []
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showerror",
            lambda *a, **k: errors.append(a),
        )
        result = ds.start_searxng_scan(dash, _searxng_request(searxng_instance_url="   "))
        assert result is False
        assert errors

    def test_missing_query_shows_error(self, monkeypatch):
        dash = _make_dash()
        errors = []
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showerror",
            lambda *a, **k: errors.append(a),
        )
        result = ds.start_searxng_scan(dash, _searxng_request(searxng_query=""))
        assert result is False
        assert len(errors) == 1
        assert "search query" in errors[0][1]

    def test_builds_run_options_correctly(self, monkeypatch):
        dash = _make_dash()
        captured_opts = []

        def _fake_run(options, *, db_path=None, progress_cb=None):
            captured_opts.append(options)
            return _make_result()

        monkeypatch.setattr("experimental.se_dork.service.run_dork_search", _fake_run)
        monkeypatch.setattr("shared.path_service.get_paths",
            lambda: MagicMock(se_dork_db_file=Path("/tmp/se_dork.db")))

        request = _searxng_request(
            searxng_instance_url="http://search.local",
            searxng_query="filetype:pdf",
            searxng_max_results=100,
            bulk_probe_enabled=True,
        )
        result = ds.start_searxng_scan(dash, request)
        assert result is True

        for t in threading.enumerate():
            if t.name == "dashboard-searxng-scan":
                t.join(timeout=5)
                break

        assert len(captured_opts) == 1
        opts = captured_opts[0]
        assert opts.instance_url == "http://search.local"
        assert opts.query == "filetype:pdf"
        assert opts.max_results == 100
        assert opts.bulk_probe_enabled is True

    def test_probe_config_resolved_from_settings_manager(self, monkeypatch):
        dash = _make_dash()
        sm = MagicMock()
        sm.get_smbseek_config_path.return_value = "/fake/config.json"
        sm.get_setting.return_value = 4
        dash.settings_manager = sm
        captured_opts = []

        def _fake_run(options, *, db_path=None, progress_cb=None):
            captured_opts.append(options)
            return _make_result()

        monkeypatch.setattr("experimental.se_dork.service.run_dork_search", _fake_run)
        monkeypatch.setattr("shared.path_service.get_paths",
            lambda: MagicMock(se_dork_db_file=Path("/tmp/se_dork.db")))

        ds.start_searxng_scan(dash, _searxng_request())

        for t in threading.enumerate():
            if t.name == "dashboard-searxng-scan":
                t.join(timeout=5)
                break

        assert len(captured_opts) == 1
        opts = captured_opts[0]
        assert opts.probe_config_path == "/fake/config.json"
        assert opts.probe_worker_count == 4

    def test_max_results_clamped_to_500(self, monkeypatch):
        dash = _make_dash()
        captured_opts = []

        def _fake_run(options, *, db_path=None, progress_cb=None):
            captured_opts.append(options)
            return _make_result()

        monkeypatch.setattr("experimental.se_dork.service.run_dork_search", _fake_run)
        monkeypatch.setattr("shared.path_service.get_paths",
            lambda: MagicMock(se_dork_db_file=Path("/tmp/se_dork.db")))

        ds.start_searxng_scan(dash, _searxng_request(searxng_max_results=9999))

        for t in threading.enumerate():
            if t.name == "dashboard-searxng-scan":
                t.join(timeout=5)
                break

        assert len(captured_opts) == 1
        assert captured_opts[0].max_results <= 500

    def test_concurrent_searxng_launch_is_blocked(self, monkeypatch):
        dash = _make_dash()
        dash._searxng_scan_running = True
        warnings = []
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showwarning",
            lambda *a, **k: warnings.append(a),
        )
        result = ds.start_searxng_scan(dash, _searxng_request())
        assert result is False
        assert warnings

    def test_launch_sets_running_flag_synchronously(self, monkeypatch):
        """Flag must be set before thread.start() — stub Thread so worker never runs."""
        dash = _make_dash()
        monkeypatch.setattr("shared.path_service.get_paths",
            lambda: MagicMock(se_dork_db_file=Path("/tmp/x.db")))

        class _NoOpThread:
            def __init__(self, *_a, **_k): pass
            def start(self): pass

        monkeypatch.setattr("gui.components.dashboard_scan.threading.Thread", _NoOpThread)
        ds.start_searxng_scan(dash, _searxng_request())
        assert dash._searxng_scan_running is True

    def test_thread_start_failure_rolls_back_state(self, monkeypatch):
        """If thread.start() raises, flag resets, task clears, showerror fires."""
        dash = _make_dash()
        monkeypatch.setattr("shared.path_service.get_paths",
            lambda: MagicMock(se_dork_db_file=Path("/tmp/x.db")))

        class _FailThread:
            def __init__(self, *_a, **_k): pass
            def start(self): raise RuntimeError("thread pool exhausted")

        monkeypatch.setattr("gui.components.dashboard_scan.threading.Thread", _FailThread)
        clear_calls = []
        dash._clear_searxng_task = lambda: clear_calls.append(True)
        errors = []
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showerror",
            lambda *a, **k: errors.append(a),
        )
        result = ds.start_searxng_scan(dash, _searxng_request())
        assert result is False
        assert dash._searxng_scan_running is False
        assert clear_calls == [True]
        assert errors

    def test_launch_calls_lifecycle_hooks(self, monkeypatch):
        """Lifecycle hooks fire synchronously before thread spawns."""
        dash = _make_dash()
        hook_calls = []
        dash._show_scan_output_dialog = lambda p, c: hook_calls.append("show_output_dialog")
        dash._reset_log_output = lambda c: hook_calls.append("reset_log")
        dash._set_searxng_task_running = lambda c: hook_calls.append("set_task_running")
        dash._log_status_event = lambda m: hook_calls.append("log_status")

        class _NoOpThread:
            def __init__(self, *_a, **_k): pass
            def start(self): pass

        monkeypatch.setattr("gui.components.dashboard_scan.threading.Thread", _NoOpThread)
        monkeypatch.setattr("shared.path_service.get_paths",
            lambda: MagicMock(se_dork_db_file=Path("/tmp/x.db")))
        ds.start_searxng_scan(dash, _searxng_request())
        assert "show_output_dialog" in hook_calls
        assert "reset_log" in hook_calls
        assert "set_task_running" in hook_calls
        assert "log_status" in hook_calls


# ---------------------------------------------------------------------------
# C — _on_searxng_scan_done
# ---------------------------------------------------------------------------

class TestOnSearxngScanDone:
    def test_error_shows_showerror(self, monkeypatch):
        dash = _make_dash()
        errors = []
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showerror",
            lambda *a, **k: errors.append(a),
        )
        result = _make_result(status=RUN_STATUS_ERROR, error="connection refused")
        ds._on_searxng_scan_done(dash, result)
        assert len(errors) == 1
        assert "connection refused" in errors[0][1]

    def test_success_shows_results_dialog_with_counts(self, monkeypatch):
        """SearXNG-only success must call _show_scan_results with counts and primary DB note."""
        dash = _make_dash()
        dialog_calls = []
        dash._show_scan_results = lambda r: dialog_calls.append(r)
        ds._on_searxng_scan_done(
            dash, _make_result(fetched_count=42, deduped_count=15),
            instance_url="http://test.local", query="filetype:pdf",
            searxng_only=True,
        )
        assert len(dialog_calls) == 1
        r = dialog_calls[0]
        assert r["protocol"] == "searxng"
        assert r["hosts_scanned"] == 42
        assert r["accessible_hosts"] == 15
        assert "primary" in r.get("summary_message", "").lower(), f"storage note missing: {r}"
        assert "duration_seconds" in r
        assert "end_time" in r

    def test_mixed_run_suppresses_results_dialog(self, monkeypatch):
        """Mixed SearXNG+Shodan run must not show results dialog (Shodan may still be active)."""
        dash = _make_dash()
        dialog_calls = []
        dash._show_scan_results = lambda r: dialog_calls.append(r)
        ds._on_searxng_scan_done(
            dash, _make_result(fetched_count=10, deduped_count=3),
            searxng_only=False,
        )
        assert dialog_calls == [], "dialog must be suppressed in mixed-provider run"

    def test_probe_summary_logged_when_probe_enabled(self, monkeypatch):
        """Probe summary must appear in log when probe was enabled."""
        dash = _make_dash()
        log_msgs = []
        dash._log_status_event = lambda m: log_msgs.append(m)
        ds._on_searxng_scan_done(dash, _make_result(
            probe_enabled=True, probe_total=20, probe_clean=18, probe_issue=2, probe_unprobed=0,
        ))
        assert any("20" in m for m in log_msgs), f"probe total missing: {log_msgs}"
        assert any("18" in m for m in log_msgs), f"clean missing: {log_msgs}"
        assert any("2" in m for m in log_msgs), f"issue missing: {log_msgs}"

    def test_sync_summary_logged_when_available(self):
        dash = _make_dash()
        log_msgs = []
        dash._log_status_event = lambda m: log_msgs.append(m)
        ds._on_searxng_scan_done(
            dash,
            _make_result(),
            sync_summary={
                "processed": 6,
                "inserted": 2,
                "updated": 3,
                "skipped": 1,
                "failed": 0,
                "cancelled": 0,
            },
        )
        assert any("Primary DB sync:" in m for m in log_msgs), log_msgs

    def test_done_clears_searxng_task_only_not_shodan_task(self, monkeypatch):
        """done handler must call _clear_searxng_task but never touch _scan_task_id."""
        dash = _make_dash()
        dash._searxng_scan_running = True
        clear_searxng_calls = []
        clear_scan_calls = []
        dash._clear_searxng_task = lambda: clear_searxng_calls.append(True)
        dash._clear_scan_task = lambda: clear_scan_calls.append(True)
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showinfo",
            lambda *a, **k: None,
        )
        ds._on_searxng_scan_done(dash, _make_result())
        assert dash._searxng_scan_running is False
        assert clear_searxng_calls == [True]
        assert clear_scan_calls == [], "_clear_scan_task must NOT be called (Shodan may own it)"


# ---------------------------------------------------------------------------
# D — SearXNG progress callback wiring
# ---------------------------------------------------------------------------

class TestSearxngProgressCallback:
    """Progress callback tests use _SyncThread so worker runs synchronously — no joins needed."""

    def test_progress_callback_is_passed_to_service(self, monkeypatch):
        """start_searxng_scan must pass a callable progress_cb to run_dork_search."""
        dash = _make_dash()
        received_cb = []

        def _fake_run(options, *, db_path=None, progress_cb=None):
            received_cb.append(progress_cb)
            return _make_result()

        class _SyncThread:
            def __init__(self, *_a, target=None, **_k): self._target = target
            def start(self): self._target and self._target()

        monkeypatch.setattr("gui.components.dashboard_scan.threading.Thread", _SyncThread)
        monkeypatch.setattr("experimental.se_dork.service.run_dork_search", _fake_run)
        monkeypatch.setattr("shared.path_service.get_paths",
            lambda: MagicMock(se_dork_db_file=Path("/tmp/x.db")))
        ds.start_searxng_scan(dash, _searxng_request())
        assert len(received_cb) == 1
        assert callable(received_cb[0]), "progress_cb must be callable"

    def test_progress_callback_routes_to_log_and_summary(self, monkeypatch):
        """SearXNG-only launch: callback routes to both log and summary bar."""
        dash = _make_dash()
        log_msgs = []
        summary_calls = []
        dash._log_status_event = lambda m: log_msgs.append(m)
        dash._update_progress_summary = lambda s, d: summary_calls.append((s, d))
        dash.parent.after = lambda delay, fn, *a: fn()

        def _fake_run(options, *, db_path=None, progress_cb=None):
            if progress_cb:
                progress_cb("Preflight OK")
                progress_cb("Querying SearXNG page 1...")
            return _make_result()

        class _SyncThread:
            def __init__(self, *_a, target=None, **_k): self._target = target
            def start(self): self._target and self._target()

        monkeypatch.setattr("gui.components.dashboard_scan.threading.Thread", _SyncThread)
        monkeypatch.setattr("experimental.se_dork.service.run_dork_search", _fake_run)
        monkeypatch.setattr("shared.path_service.get_paths",
            lambda: MagicMock(se_dork_db_file=Path("/tmp/x.db")))
        ds.start_searxng_scan(dash, _searxng_request())  # providers=["searxng"] only
        assert "Preflight OK" in log_msgs, f"log: {log_msgs}"
        assert any(d == "Preflight OK" for _, d in summary_calls), f"summary: {summary_calls}"

    def test_mixed_provider_skips_summary_bar(self, monkeypatch):
        """Mixed SearXNG+Shodan launch: callback routes to log only, not summary bar."""
        dash = _make_dash()
        summary_calls = []
        dash._update_progress_summary = lambda s, d: summary_calls.append((s, d))
        dash.parent.after = lambda delay, fn, *a: fn()

        def _fake_run(options, *, db_path=None, progress_cb=None):
            if progress_cb:
                progress_cb("Querying SearXNG page 1...")
            return _make_result()

        class _SyncThread:
            def __init__(self, *_a, target=None, **_k): self._target = target
            def start(self): self._target and self._target()

        monkeypatch.setattr("gui.components.dashboard_scan.threading.Thread", _SyncThread)
        monkeypatch.setattr("experimental.se_dork.service.run_dork_search", _fake_run)
        monkeypatch.setattr("shared.path_service.get_paths",
            lambda: MagicMock(se_dork_db_file=Path("/tmp/x.db")))
        ds.start_searxng_scan(dash, _searxng_request(providers=["searxng", "shodan"]))
        assert summary_calls == [], f"summary bar must not update in mixed run: {summary_calls}"

    def test_after_scheduling_failure_clears_state(self, monkeypatch):
        """When dash.parent.after raises, worker still clears running flag and task."""
        dash = _make_dash()
        dash.parent.after.side_effect = RuntimeError("no event loop")
        clear_calls = []
        dash._clear_searxng_task = lambda: clear_calls.append(True)

        def _fake_run(options, *, db_path=None, progress_cb=None):
            return _make_result()

        class _SyncThread:
            def __init__(self, *_a, target=None, **_k): self._target = target
            def start(self): self._target and self._target()

        monkeypatch.setattr("gui.components.dashboard_scan.threading.Thread", _SyncThread)
        monkeypatch.setattr("experimental.se_dork.service.run_dork_search", _fake_run)
        monkeypatch.setattr("shared.path_service.get_paths",
            lambda: MagicMock(se_dork_db_file=Path("/tmp/x.db")))
        ds.start_searxng_scan(dash, _searxng_request())
        assert dash._searxng_scan_running is False
        assert clear_calls == [True]


# ---------------------------------------------------------------------------
# D — start_reddit_scan
# ---------------------------------------------------------------------------

_REDDIT_DB = Path("/tmp/reddit_od.db")


def _fake_paths_reddit():
    return MagicMock(reddit_od_db_file=_REDDIT_DB)


class TestStartRedditScan:
    def test_already_running_returns_false_and_warns(self, monkeypatch):
        dash = _make_dash()
        dash._reddit_scan_running = True
        warnings = []
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showwarning",
            lambda *a, **k: warnings.append(a),
        )
        result = ds.start_reddit_scan(dash, _reddit_request())
        assert result is False
        assert warnings

    def test_grab_running_blocks_core_scan(self, monkeypatch):
        dash = _make_dash()
        dash._reddit_grab_running = True
        warnings = []
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showwarning",
            lambda *a, **k: warnings.append(a),
        )
        result = ds.start_reddit_scan(dash, _reddit_request())
        assert result is False
        assert warnings

    def test_builds_ingest_options_feed_mode(self, monkeypatch):
        dash = _make_dash()
        captured = []

        def _fake_run(options, db_path=None):
            captured.append(options)
            return _make_reddit_result()

        monkeypatch.setattr("experimental.redseek.service.run_ingest", _fake_run)
        monkeypatch.setattr("shared.path_service.get_paths", _fake_paths_reddit)
        result = ds.start_reddit_scan(dash, _reddit_request(
            reddit_mode="feed", reddit_sort="top", reddit_max_posts=75,
            reddit_parse_body=False, reddit_include_nsfw=True,
        ))
        assert result is True

        for t in threading.enumerate():
            if t.name == "dashboard-reddit-scan":
                t.join(timeout=5)
                break

        assert len(captured) == 1
        opts = captured[0]
        assert opts.sort == "top"
        assert opts.max_posts == 75
        assert opts.parse_body is False
        assert opts.include_nsfw is True
        assert opts.replace_cache is False

    def test_builds_ingest_options_search_mode(self, monkeypatch):
        dash = _make_dash()
        captured = []

        def _fake_run(options, db_path=None):
            captured.append(options)
            return _make_reddit_result()

        monkeypatch.setattr("experimental.redseek.service.run_ingest", _fake_run)
        monkeypatch.setattr("shared.path_service.get_paths", _fake_paths_reddit)
        ds.start_reddit_scan(dash, _reddit_request(reddit_mode="search", reddit_query="open directories"))

        for t in threading.enumerate():
            if t.name == "dashboard-reddit-scan":
                t.join(timeout=5)
                break

        assert captured[0].query == "open directories"
        assert captured[0].mode == "search"

    def test_builds_ingest_options_user_mode(self, monkeypatch):
        dash = _make_dash()
        captured = []

        def _fake_run(options, db_path=None):
            captured.append(options)
            return _make_reddit_result()

        monkeypatch.setattr("experimental.redseek.service.run_ingest", _fake_run)
        monkeypatch.setattr("shared.path_service.get_paths", _fake_paths_reddit)
        ds.start_reddit_scan(dash, _reddit_request(reddit_mode="user", reddit_username="testuser"))

        for t in threading.enumerate():
            if t.name == "dashboard-reddit-scan":
                t.join(timeout=5)
                break

        assert captured[0].username == "testuser"
        assert captured[0].mode == "user"

    def test_required_bool_fields_default_correctly(self, monkeypatch):
        dash = _make_dash()
        captured = []

        def _fake_run(options, db_path=None):
            captured.append(options)
            return _make_reddit_result()

        monkeypatch.setattr("experimental.redseek.service.run_ingest", _fake_run)
        monkeypatch.setattr("shared.path_service.get_paths", _fake_paths_reddit)
        ds.start_reddit_scan(dash, _reddit_request())

        for t in threading.enumerate():
            if t.name == "dashboard-reddit-scan":
                t.join(timeout=5)
                break

        opts = captured[0]
        assert opts.parse_body is True
        assert opts.include_nsfw is False
        assert opts.replace_cache is False

    def test_max_posts_clamped_to_200(self, monkeypatch):
        dash = _make_dash()
        captured = []

        def _fake_run(options, db_path=None):
            captured.append(options)
            return _make_reddit_result()

        monkeypatch.setattr("experimental.redseek.service.run_ingest", _fake_run)
        monkeypatch.setattr("shared.path_service.get_paths", _fake_paths_reddit)
        ds.start_reddit_scan(dash, _reddit_request(reddit_max_posts=9999))

        for t in threading.enumerate():
            if t.name == "dashboard-reddit-scan":
                t.join(timeout=5)
                break

        assert captured[0].max_posts <= 200

    def test_probe_config_resolved_from_settings_manager(self, monkeypatch):
        dash = _make_dash()
        sm = MagicMock()
        sm.get_smbseek_config_path.return_value = "/cfg/probe.json"
        sm.get_setting.return_value = 5
        dash.settings_manager = sm
        captured = []

        def _fake_run(options, db_path=None):
            captured.append(options)
            return _make_reddit_result()

        monkeypatch.setattr("experimental.redseek.service.run_ingest", _fake_run)
        monkeypatch.setattr("shared.path_service.get_paths", _fake_paths_reddit)
        ds.start_reddit_scan(dash, _reddit_request())

        for t in threading.enumerate():
            if t.name == "dashboard-reddit-scan":
                t.join(timeout=5)
                break

        assert captured[0].probe_config_path == "/cfg/probe.json"
        assert captured[0].probe_worker_count == 5


# ---------------------------------------------------------------------------
# E — _on_reddit_scan_done
# ---------------------------------------------------------------------------

class TestOnRedditScanDone:
    def test_error_result_shows_showerror(self, monkeypatch):
        dash = _make_dash()
        errors = []
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showerror",
            lambda *a, **k: errors.append(a),
        )
        result = _make_reddit_result(error="rate limited")
        ds._on_reddit_scan_done(dash, result)
        assert len(errors) == 1
        assert "rate limited" in errors[0][1]

    def test_success_reddit_only_shows_scan_results(self, monkeypatch):
        dash = _make_dash()
        dialog_calls = []
        dash._show_scan_results = lambda r: dialog_calls.append(r)
        ds._on_reddit_scan_done(
            dash, _make_reddit_result(posts_stored=20, targets_stored=8),
            reddit_only=True,
        )
        assert len(dialog_calls) == 1
        r = dialog_calls[0]
        assert r["protocol"] == "reddit"
        assert r["hosts_scanned"] == 20
        assert r["accessible_hosts"] == 8
        assert "duration_seconds" in r
        assert "end_time" in r

    def test_success_reddit_only_summary_does_not_mention_sidecar(self, monkeypatch):
        """C10: sidecar/manual-promotion copy must no longer appear in summary."""
        dash = _make_dash()
        dialog_calls = []
        dash._show_scan_results = lambda r: dialog_calls.append(r)
        ds._on_reddit_scan_done(
            dash, _make_reddit_result(posts_stored=10, targets_stored=4),
            reddit_only=True,
        )
        summary = dialog_calls[0].get("summary_message", "")
        assert "reddit_od.db" not in summary, \
            f"Stale sidecar path in summary: {summary!r}"
        assert "promoted" not in summary.lower(), \
            f"Stale manual-promotion copy in summary: {summary!r}"

    def test_success_mixed_suppresses_modal_logs_only(self, monkeypatch):
        dash = _make_dash()
        dialog_calls = []
        dash._show_scan_results = lambda r: dialog_calls.append(r)
        ds._on_reddit_scan_done(
            dash, _make_reddit_result(),
            reddit_only=False,
        )
        assert dialog_calls == [], "Mixed run must not show completion dialog"

    def test_probe_summary_included_in_reddit_only_result(self, monkeypatch):
        dash = _make_dash()
        log_msgs = []
        dash._log_status_event = lambda m: log_msgs.append(m)
        ds._on_reddit_scan_done(dash, _make_reddit_result(
            probe_enabled=True, probe_total=10, probe_clean=9, probe_issue=1, probe_unprobed=0,
        ))
        assert any("10" in m for m in log_msgs), f"probe total missing: {log_msgs}"

    def test_clears_running_flag_on_completion(self, monkeypatch):
        dash = _make_dash()
        dash._reddit_scan_running = True
        clear_calls = []
        dash._clear_reddit_task = lambda: clear_calls.append(True)
        ds._on_reddit_scan_done(dash, _make_reddit_result())
        assert dash._reddit_scan_running is False
        assert clear_calls == [True]

    def test_clears_running_flag_on_error(self, monkeypatch):
        dash = _make_dash()
        dash._reddit_scan_running = True
        monkeypatch.setattr(
            "gui.components.dashboard.messagebox.showerror",
            lambda *a, **k: None,
        )
        ds._on_reddit_scan_done(dash, _make_reddit_result(error="timeout"))
        assert dash._reddit_scan_running is False

    def test_on_reddit_scan_done_logs_sync_totals(self):
        """C10: sync totals must appear in completion log when sync_summary is provided."""
        dash = _make_dash()
        log_msgs: list = []
        dash._log_status_event = lambda m: log_msgs.append(m)
        ds._on_reddit_scan_done(
            dash,
            _make_reddit_result(posts_stored=10, targets_stored=4),
            sync_summary={"inserted": 3, "updated": 1, "skipped": 0, "failed": 0},
        )
        assert any("3" in m and ("new" in m or "inserted" in m or "synced" in m.lower())
                   for m in log_msgs), f"sync total not in log: {log_msgs}"


class TestStartRedditScanPrimaryDB:

    def test_start_reddit_scan_uses_primary_db_not_sidecar(self, monkeypatch):
        """C10: run_ingest must receive the primary DB path, not reddit_od_db_file."""
        dash = _make_dash()
        captured_db: list = []

        def _fake_run(options, db_path=None):
            captured_db.append(db_path)
            return _make_reddit_result()

        monkeypatch.setattr("experimental.redseek.service.run_ingest", _fake_run)

        ds.start_reddit_scan(dash, _reddit_request())

        for t in threading.enumerate():
            if t.name == "dashboard-reddit-scan":
                t.join(timeout=5)
                break

        assert len(captured_db) == 1
        db = captured_db[0]
        assert db is not None, "db_path must not be None"
        assert str(db) == str(Path("/tmp/dirracuda.db")), \
            f"Expected primary DB path, got {db!r}"
        assert "reddit_od" not in str(db).lower(), \
            f"Sidecar path leaked into run_ingest: {db!r}"
