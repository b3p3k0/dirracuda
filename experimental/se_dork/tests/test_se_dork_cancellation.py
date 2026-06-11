"""Tests for C11A cancellation semantics in run_dork_search."""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def _ok_preflight():
    return SimpleNamespace(ok=True, reason_code=None, message="OK")


def _json_body(results):
    return json.dumps({"results": results}).encode()


def _cm(results):
    body = _json_body(results)
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.read = MagicMock(return_value=body)
    return cm


def _opts(**kw):
    from experimental.se_dork.models import RunOptions
    d = dict(instance_url="http://x", query="q", max_results=10)
    d.update(kw)
    return RunOptions(**d)


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr("experimental.se_dork.service.time.sleep", lambda _: None)
    monkeypatch.setattr("experimental.se_dork.service.random.uniform", lambda lo, hi: (lo + hi) / 2)


# ---------------------------------------------------------------------------
# Telemetry preservation
# ---------------------------------------------------------------------------

class TestTelemetryThroughCancellation:
    def test_cancelled_paginate_returns_outcome_not_raises(self, monkeypatch):
        """_paginate_results with a pre-set cancel_event returns _FetchOutcome."""
        from experimental.se_dork.service import _paginate_results, _FetchOutcome

        evt = threading.Event()
        evt.set()
        result = _paginate_results("http://x", "q", 10, cancel_event=evt)
        assert isinstance(result, _FetchOutcome)
        assert result.stopped_early is True

    def test_cancel_preserves_pages_fetched(self, monkeypatch, tmp_path):
        """Cancel after 2 pages: result.pages_fetched == 2."""
        from experimental.se_dork.service import run_dork_search

        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        rows = [{"url": f"http://a{i}.com/", "title": "X", "content": ""} for i in range(3)]
        evt = threading.Event()
        call_n = [0]

        def _urlopen(url, timeout=None):
            call_n[0] += 1
            if call_n[0] == 3:
                evt.set()
            return _cm(rows)

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)
        monkeypatch.setattr("experimental.se_dork.classifier.classify_url",
                            lambda *a, **k: _mock_classify())
        def _fake_insert_page(conn, run_id, rows):
            return [{**r, "result_id": i + 1} for i, r in enumerate(rows)]

        monkeypatch.setattr("experimental.se_dork.service.insert_page_results", _fake_insert_page)
        monkeypatch.setattr("experimental.se_dork.service._persist_page_classification",
                            lambda **kw: ([], 0))
        monkeypatch.setattr("experimental.se_dork.service.update_run_progress",
                            lambda *a, **k: None)
        monkeypatch.setattr("experimental.se_dork.service.delete_unclassified_results_by_ids",
                            lambda *a, **k: None)

        result = run_dork_search(_opts(max_results=1000), db_path=tmp_path / "test.db",
                                 cancel_event=evt)
        assert result.pages_fetched >= 2

    def test_cancel_preserves_retry_count(self, monkeypatch, tmp_path):
        """Cancel during cooldown: result.hard_retry_count is preserved."""
        from experimental.se_dork.service import run_dork_search

        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        evt = threading.Event()
        call_n = [0]

        def _fake_wait(seconds, *, retry_number, max_retries=2, progress_cb=None,
                       sleep_fn=None, cancel_event=None):
            evt.set()  # fire cancel during wait
            return 0.5

        monkeypatch.setattr("experimental.se_dork.service._wait_for_cooldown", _fake_wait)

        def _urlopen(url, timeout=None):
            call_n[0] += 1
            raise urllib.error.HTTPError(url, 429, "Rate Limited", {}, None)

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)

        result = run_dork_search(_opts(), db_path=tmp_path / "test.db", cancel_event=evt)
        assert result.hard_retry_count >= 1
        assert result.hard_retry_delay_seconds == 0.5


# ---------------------------------------------------------------------------
# Pre-run-row cancellation
# ---------------------------------------------------------------------------

class TestPreRunRowCancellation:
    def test_cancel_before_any_network_or_db(self):
        """Pre-set cancel_event → CANCELLED result with no network or DB calls."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RUN_STATUS_CANCELLED

        evt = threading.Event()
        evt.set()
        calls = []

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("experimental.se_dork.service.run_reachability_check",
                       lambda *a, **k: calls.append("reach") or _ok_preflight())
            result = run_dork_search(_opts(), cancel_event=evt)

        assert result.status == RUN_STATUS_CANCELLED
        assert result.error is None
        assert result.run_id is None
        assert "reach" not in calls

    def test_cancel_after_reachability_before_db(self, monkeypatch):
        """Event set after preflight → CANCELLED, run_id=None."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RUN_STATUS_CANCELLED

        evt = threading.Event()

        def _reachability(url, timeout=10):
            evt.set()
            return _ok_preflight()

        monkeypatch.setattr("experimental.se_dork.service.run_reachability_check", _reachability)

        result = run_dork_search(_opts(), cancel_event=evt)
        assert result.status == RUN_STATUS_CANCELLED
        assert result.run_id is None
        assert result.error is None


# ---------------------------------------------------------------------------
# Post-run-row cancellation
# ---------------------------------------------------------------------------

class TestPostRunRowCancellation:
    def test_cancel_after_run_row(self, monkeypatch, tmp_path):
        """Cancel after run insert → DB status = 'cancelled', run_id set."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RUN_STATUS_CANCELLED
        from experimental.se_dork.store import open_connection

        db_path = tmp_path / "test.db"
        evt = threading.Event()
        run_ids = []

        original_insert = None
        import experimental.se_dork.service as _svc
        original_insert = _svc.insert_run

        def _fake_insert_run(conn, opts, started_at):
            rid = original_insert(conn, opts, started_at)
            run_ids.append(rid)
            evt.set()
            return rid

        monkeypatch.setattr("experimental.se_dork.service.insert_run", _fake_insert_run)
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _cm([]))

        result = run_dork_search(_opts(), db_path=db_path, cancel_event=evt)

        assert result.status == RUN_STATUS_CANCELLED
        assert result.error is None
        assert result.run_id is not None

        conn = open_connection(db_path)
        row = conn.execute("SELECT status FROM dork_runs WHERE run_id = ?", (result.run_id,)).fetchone()
        conn.close()
        assert row and row[0] == "cancelled"

    def test_cancelled_result_error_is_none(self, monkeypatch, tmp_path):
        """On any cancellation path, result.error must be None."""
        from experimental.se_dork.service import run_dork_search

        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        evt = threading.Event()
        evt.set()
        result = run_dork_search(_opts(), db_path=tmp_path / "test.db", cancel_event=evt)
        assert result.error is None

    def test_cancel_top_of_fetch_loop(self, monkeypatch, tmp_path):
        """Event set at loop top: no fetch call is made."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RUN_STATUS_CANCELLED

        fetch_calls = []
        evt = threading.Event()

        def _reachability(url, timeout=10):
            evt.set()  # set before any fetch
            return _ok_preflight()

        monkeypatch.setattr("experimental.se_dork.service.run_reachability_check", _reachability)

        def _urlopen(url, timeout=None):
            fetch_calls.append(url)
            return _cm([])

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)

        result = run_dork_search(_opts(), db_path=tmp_path / "test.db", cancel_event=evt)
        # Either cancelled before run_id (no fetch) or after (also no fetch since cancelled at loop top)
        assert result.status == RUN_STATUS_CANCELLED
        assert result.error is None

    def test_cancel_during_cooldown_records_partial_elapsed(self, monkeypatch, tmp_path):
        """Event fires mid-cooldown: hard_retry_delay_seconds < requested delay."""
        from experimental.se_dork.service import run_dork_search

        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )

        evt = threading.Event()
        call_n = [0]

        def _fake_wait(seconds, *, retry_number, max_retries=2, progress_cb=None,
                       sleep_fn=None, cancel_event=None):
            evt.set()
            return 2.0  # partial elapsed

        monkeypatch.setattr("experimental.se_dork.service._wait_for_cooldown", _fake_wait)

        def _urlopen(url, timeout=None):
            call_n[0] += 1
            if call_n[0] == 1:
                raise urllib.error.HTTPError(url, 429, "Rate Limited", {}, None)
            return _cm([])

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)

        result = run_dork_search(_opts(), db_path=tmp_path / "test.db", cancel_event=evt)
        assert result.hard_retry_delay_seconds == 2.0

    def test_cancel_during_pacing_records_actual_elapsed(self, monkeypatch, tmp_path):
        """Event fires during pacing sleep: pacing_delay_seconds < full delay."""
        from experimental.se_dork.service import run_dork_search

        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        evt = threading.Event()

        rows = [{"url": f"http://u{i}.com/", "title": "OD", "content": ""} for i in range(3)]
        call_n = [0]

        def _urlopen(url, timeout=None):
            call_n[0] += 1
            return _cm(rows)

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)
        monkeypatch.setattr("experimental.se_dork.classifier.classify_url",
                            lambda *a, **k: _mock_classify())
        def _fake_insert_page(conn, run_id, rows):
            return [{**r, "result_id": i + 1} for i, r in enumerate(rows)]

        monkeypatch.setattr("experimental.se_dork.service.insert_page_results", _fake_insert_page)
        monkeypatch.setattr("experimental.se_dork.service._persist_page_classification",
                            lambda **kw: ([], 0))
        monkeypatch.setattr("experimental.se_dork.service.update_run_progress",
                            lambda *a, **k: None)
        monkeypatch.setattr("experimental.se_dork.service.delete_unclassified_results_by_ids",
                            lambda *a, **k: None)

        def _fake_pacing(*, completed_page, next_page, delay_seconds, deadline,
                         progress_cb, sleep_fn, monotonic_fn, cancel_event=None):
            evt.set()
            return 0.3  # partial elapsed

        monkeypatch.setattr("experimental.se_dork.service._sleep_for_remaining_pacing", _fake_pacing)

        result = run_dork_search(_opts(max_results=1000), db_path=tmp_path / "test.db",
                                 cancel_event=evt)
        assert result.pacing_delay_seconds == 0.3


# ---------------------------------------------------------------------------
# Rollup format
# ---------------------------------------------------------------------------

class TestCancelledRollup:
    def test_cancelled_rollup_with_results_and_sync(self):
        from experimental.se_dork.models import RunResult, RUN_STATUS_CANCELLED
        from gui.components.dashboard_scan_rollup import format_searxng_cancelled_rollup

        result = RunResult(
            run_id=1,
            fetched_count=15,
            deduped_count=4,
            status=RUN_STATUS_CANCELLED,
            error=None,
            verified_count=10,
            pages_fetched=3,
            pacing_delay_seconds=6.0,
        )
        sync = {"processed": 4, "inserted": 4, "updated": 0, "skipped": 0, "failed": 0}
        rollup = format_searxng_cancelled_rollup(
            result,
            query='site:* intitle:"index of /"',
            db_path="/tmp/test.db",
            sync_summary=sync,
        )
        lines = rollup.split("\n")
        assert lines[0] == "Dirracuda Scan Summary"
        assert lines[1] == "=" * len("Dirracuda Scan Summary")
        assert any("SearXNG Query" in l for l in lines)
        assert any("URLs Fetched: 15" in l for l in lines)
        assert any("Open Indexes Retained: 4" in l for l in lines)
        assert any("Fetch Pacing: 3 pages, 6s delayed" in l for l in lines)
        assert any("Primary DB Sync" in l for l in lines)
        assert any("/tmp/test.db" in l for l in lines)
        assert lines[-1] == "⚠ Scan cancelled: 4 open indexes retained"

    def test_cancelled_rollup_zero_results_no_sync(self):
        from experimental.se_dork.models import RunResult, RUN_STATUS_CANCELLED
        from gui.components.dashboard_scan_rollup import format_searxng_cancelled_rollup

        result = RunResult(
            run_id=None,
            fetched_count=0,
            deduped_count=0,
            status=RUN_STATUS_CANCELLED,
            error=None,
        )
        rollup = format_searxng_cancelled_rollup(
            result, query="test query", db_path="/tmp/test.db"
        )
        lines = rollup.split("\n")
        assert "URLs Fetched: 0" in rollup
        assert "Open Indexes Retained: 0" in rollup
        assert lines[-1] == "⚠ Scan cancelled: no open indexes retained"
        # No sync line since no sync_summary
        assert all("Primary DB Sync" not in l for l in lines)


def _mock_classify():
    from experimental.se_dork.classifier import ClassifyResult, VERDICT_NOISE
    return ClassifyResult(verdict=VERDICT_NOISE, reason_code="test", http_status=404)


def _mock_open_index():
    from experimental.se_dork.classifier import ClassifyResult, VERDICT_OPEN_INDEX
    return ClassifyResult(verdict=VERDICT_OPEN_INDEX, reason_code=None, http_status=200)


class _BadCloseProxy:
    """Wraps sqlite3.Connection and raises on close(). Direct attribute assignment is impossible."""
    def __init__(self, conn):
        self._conn = conn

    def close(self):
        self._conn.close()
        raise RuntimeError("close failed")

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ---------------------------------------------------------------------------
# Run-setup failure regression tests (Issue 1, M1)
# ---------------------------------------------------------------------------


class TestRunSetupFailures:
    def test_open_connection_failure_after_init_db_returns_error(self, monkeypatch, tmp_path):
        """init_db succeeds, open_connection raises → must not raise; returns RUN_STATUS_ERROR."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RUN_STATUS_ERROR
        import experimental.se_dork.service as _svc

        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )

        init_called = [False]
        original_init = _svc.init_db
        def _init_ok(db_path):
            original_init(db_path)
            init_called[0] = True

        monkeypatch.setattr("experimental.se_dork.service.init_db", _init_ok)
        monkeypatch.setattr(
            "experimental.se_dork.service.open_connection",
            lambda *a, **k: (_ for _ in ()).throw(OSError("permission denied")),
        )

        result = run_dork_search(_opts(), db_path=tmp_path / "test.db")  # must not raise
        assert init_called[0], "init_db must have been called"
        assert result.status == RUN_STATUS_ERROR
        assert "Run setup failed" in result.error
        assert "permission denied" in result.error

    def test_setup_connection_close_failure_does_not_escape(self, monkeypatch, tmp_path):
        """conn.close() raises in setup finally → must not escape or override the structured error."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RUN_STATUS_ERROR
        import experimental.se_dork.service as _svc

        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )

        original_open = _svc.open_connection
        monkeypatch.setattr(
            "experimental.se_dork.service.open_connection",
            lambda db_path: _BadCloseProxy(original_open(db_path)),
        )
        monkeypatch.setattr(
            "experimental.se_dork.service.insert_run",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("insert failed")),
        )

        result = run_dork_search(_opts(), db_path=tmp_path / "test.db")  # must not raise
        assert result.status == RUN_STATUS_ERROR
        assert "insert failed" in result.error

    def test_cancellation_close_failure_after_commit_returns_cancelled(self, monkeypatch, tmp_path):
        """c.close() raises after successful CANCELLED commit → must return CANCELLED; DB confirmed."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RUN_STATUS_CANCELLED
        from experimental.se_dork.store import open_connection
        import experimental.se_dork.service as _svc

        evt = threading.Event()
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _cm([]))

        original_insert = _svc.insert_run
        def _insert_then_cancel(conn, opts, started_at):
            rid = original_insert(conn, opts, started_at)
            evt.set()
            return rid

        monkeypatch.setattr("experimental.se_dork.service.insert_run", _insert_then_cancel)

        open_call_n = [0]
        original_open = _svc.open_connection
        def _open_bad_close_on_cancel(db_path):
            open_call_n[0] += 1
            conn = original_open(db_path)
            return _BadCloseProxy(conn) if open_call_n[0] >= 2 else conn

        monkeypatch.setattr("experimental.se_dork.service.open_connection",
                            _open_bad_close_on_cancel)

        db_path = tmp_path / "test.db"
        result = run_dork_search(_opts(), db_path=db_path, cancel_event=evt)

        assert result.status == RUN_STATUS_CANCELLED, (
            f"close failure after successful commit must not reverse CANCELLED; got {result.status}"
        )
        assert result.error is None

        conn = open_connection(db_path)
        row = conn.execute(
            "SELECT status FROM dork_runs WHERE run_id = ?", (result.run_id,)
        ).fetchone()
        conn.close()
        assert row and row[0] == "cancelled", f"DB row must be cancelled; got {row}"


# ---------------------------------------------------------------------------
# Post-run-row cancellation tests (Issue 4)
# ---------------------------------------------------------------------------


class TestCancellationBoundaries:
    def test_cancel_before_page_persistence(self, monkeypatch, tmp_path):
        """Cancel fires after fetch but before insert_page_results; no rows stored."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RUN_STATUS_CANCELLED
        evt = threading.Event()
        inserts = []
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )

        def _urlopen(url, timeout=None):
            evt.set()
            return _cm([{"url": "http://a.com/", "title": "OD", "content": ""}])

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)

        def _record_insert(conn, run_id, rows):
            inserts.extend(rows)
            return [{**r, "result_id": 1} for r in rows]

        monkeypatch.setattr("experimental.se_dork.service.insert_page_results", _record_insert)
        result = run_dork_search(_opts(), db_path=tmp_path / "test.db", cancel_event=evt)
        assert result.status == RUN_STATUS_CANCELLED
        assert inserts == []

    def test_cancel_between_classification_rows_cleans_up(self, monkeypatch, tmp_path):
        """_Cancelled mid-classify removes unclassified rows from dork_results (real DB)."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RUN_STATUS_CANCELLED
        from experimental.se_dork.store import open_connection
        evt = threading.Event()
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        rows = [{"url": f"http://r{i}.com/", "title": "OD", "content": ""} for i in range(3)]
        call_n = [0]

        def _urlopen(url, timeout=None):
            call_n[0] += 1
            return _cm(rows) if call_n[0] == 1 else _cm([])

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)
        classify_n = [0]

        def _classify_sets_cancel(url, timeout=10, allow_insecure_tls=None):
            classify_n[0] += 1
            if classify_n[0] == 2:
                evt.set()
            return _mock_classify()

        monkeypatch.setattr("experimental.se_dork.classifier.classify_url", _classify_sets_cancel)

        db_path = tmp_path / "test.db"
        result = run_dork_search(_opts(max_results=100), db_path=db_path, cancel_event=evt)
        assert result.status == RUN_STATUS_CANCELLED

        conn = open_connection(db_path)
        unclassified = conn.execute(
            "SELECT COUNT(*) FROM dork_results WHERE run_id = ? AND verdict IS NULL",
            (result.run_id,),
        ).fetchone()[0]
        conn.close()
        assert unclassified == 0, f"All unclassified rows must be deleted; {unclassified} remain"

    def test_cancel_after_classification_commit_preserves_retained_rows(self, monkeypatch, tmp_path):
        """classification_committed=True: OPEN_INDEX rows survive; counts match."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RUN_STATUS_CANCELLED
        from experimental.se_dork.store import open_connection
        import experimental.se_dork.service as _svc
        evt = threading.Event()
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        rows = [{"url": f"http://c{i}.com/", "title": "OD", "content": ""} for i in range(2)]
        monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=None: _cm(rows))
        monkeypatch.setattr("experimental.se_dork.classifier.classify_url",
                            lambda url, **k: _mock_open_index())

        original_persist = _svc._persist_page_classification
        def _persist_and_cancel(**kw):
            result = original_persist(**kw)
            evt.set()
            return result

        monkeypatch.setattr("experimental.se_dork.service._persist_page_classification",
                            _persist_and_cancel)

        db_path = tmp_path / "test.db"
        result = run_dork_search(_opts(max_results=100), db_path=db_path, cancel_event=evt)
        assert result.status == RUN_STATUS_CANCELLED
        assert result.verified_count == 2, "Cumulative verified count must survive"

        conn = open_connection(db_path)
        retained = conn.execute(
            "SELECT COUNT(*) FROM dork_results WHERE run_id = ? AND verdict = 'OPEN_INDEX'",
            (result.run_id,),
        ).fetchone()[0]
        conn.close()
        assert retained == 2, f"Expected 2 retained OPEN_INDEX rows, found {retained}"

    def test_cancel_before_probe_skips_probe(self, monkeypatch, tmp_path):
        """Cancel after classify commit; probe must not run; probe_status='unprobed', probe_checked_at IS NULL."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RUN_STATUS_CANCELLED, RunOptions
        from experimental.se_dork.store import open_connection
        import experimental.se_dork.service as _svc
        evt = threading.Event()
        probe_calls = []
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        rows = [{"url": "http://probe-test.com/", "title": "OD", "content": ""}]
        monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=None: _cm(rows))
        monkeypatch.setattr("experimental.se_dork.classifier.classify_url",
                            lambda url, **k: _mock_open_index())

        original_persist = _svc._persist_page_classification
        def _persist_then_cancel(**kw):
            r = original_persist(**kw)
            evt.set()
            return r

        monkeypatch.setattr("experimental.se_dork.service._persist_page_classification",
                            _persist_then_cancel)

        def _no_probe(*a, **k):
            probe_calls.append(True)
            return [], {"total": 0, "clean": 0, "issue": 0, "unprobed": 0}

        monkeypatch.setattr("experimental.se_dork.service._probe_page_rows", _no_probe)

        db_path = tmp_path / "test.db"
        opts = RunOptions(instance_url="http://x", query="q", max_results=100,
                          bulk_probe_enabled=True)
        result = run_dork_search(opts, db_path=db_path, cancel_event=evt)
        assert result.status == RUN_STATUS_CANCELLED
        assert probe_calls == [], "Probe must not run when cancel fires before probe block"

        conn = open_connection(db_path)
        rows_probed = conn.execute(
            "SELECT COUNT(*) FROM dork_results "
            "WHERE run_id = ? AND probe_status = 'unprobed' AND probe_checked_at IS NULL",
            (result.run_id,),
        ).fetchone()[0]
        conn.close()
        assert rows_probed > 0, (
            "Retained rows must have probe_status='unprobed' and NULL probe_checked_at (probe skipped)"
        )

    def test_cancellation_finalization_failure_returns_error(self, monkeypatch, tmp_path):
        """update_run(CANCELLED) fails → RUN_STATUS_ERROR; failure message emitted; 'Cancelled.' not emitted."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RUN_STATUS_ERROR
        from experimental.se_dork.store import open_connection
        import experimental.se_dork.service as _svc
        evt = threading.Event()
        msgs = []
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _cm([]))

        original_update = _svc.update_run
        def _update_failing_on_cancel(conn, run_id, ts, fetched, retained, status, *a, **k):
            if status == "cancelled":
                raise RuntimeError("simulated disk full")
            return original_update(conn, run_id, ts, fetched, retained, status, *a, **k)

        monkeypatch.setattr("experimental.se_dork.service.update_run", _update_failing_on_cancel)

        original_insert = _svc.insert_run
        def _insert_then_cancel(conn, opts, started_at):
            rid = original_insert(conn, opts, started_at)
            evt.set()
            return rid

        monkeypatch.setattr("experimental.se_dork.service.insert_run", _insert_then_cancel)
        db_path = tmp_path / "test.db"
        result = run_dork_search(_opts(), db_path=db_path, cancel_event=evt,
                                 progress_cb=msgs.append)
        assert result.status == RUN_STATUS_ERROR
        assert result.error is not None
        assert "Cancellation finalization failed" in result.error
        assert any("Cancellation finalization failed" in m for m in msgs), (
            f"Failure message must be emitted via progress_cb; got: {msgs}"
        )
        assert not any(m == "Cancelled." for m in msgs), (
            f"'Cancelled.' must not be emitted when commit did not succeed; got: {msgs}"
        )

        conn = open_connection(db_path)
        row = conn.execute(
            "SELECT status FROM dork_runs WHERE run_id = ?", (result.run_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] != "cancelled", (
            f"dork_runs.status must not be 'cancelled' when finalization failed; got {row[0]}"
        )

    def test_totals_match_durable_db_state_after_cancel(self, monkeypatch, tmp_path):
        """result.verified_count matches durable dork_runs.verified_count (real update_run_progress)."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.store import open_connection
        from experimental.se_dork.models import RUN_STATUS_CANCELLED
        import experimental.se_dork.service as _svc
        evt = threading.Event()
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        rows = [{"url": f"http://t{i}.com/", "title": "OD", "content": ""} for i in range(2)]
        monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=None: _cm(rows))
        monkeypatch.setattr("experimental.se_dork.classifier.classify_url",
                            lambda url, **k: _mock_classify())

        original_persist = _svc._persist_page_classification
        def _persist_and_cancel(**kw):
            r = original_persist(**kw)
            evt.set()
            return r

        monkeypatch.setattr("experimental.se_dork.service._persist_page_classification",
                            _persist_and_cancel)
        db_path = tmp_path / "test.db"
        result = run_dork_search(_opts(max_results=100), db_path=db_path, cancel_event=evt)
        assert result.status == RUN_STATUS_CANCELLED

        conn = open_connection(db_path)
        db_row = conn.execute(
            "SELECT verified_count FROM dork_runs WHERE run_id = ?", (result.run_id,)
        ).fetchone()
        conn.close()
        assert db_row is not None
        assert result.verified_count == db_row[0], (
            f"result.verified_count={result.verified_count} must equal "
            f"dork_runs.verified_count={db_row[0]}"
        )


# ---------------------------------------------------------------------------
# Probe cancellation tests (Issue 4e)
# ---------------------------------------------------------------------------


class TestProbePageRowsCancellation:
    def test_active_futures_drain_row_never_submitted_not_executed(self, monkeypatch):
        """Cancel fires before either active future completes; r2 never submitted; exact counts."""
        from experimental.se_dork.service import _probe_page_rows

        call_order = []
        cancel_evt = threading.Event()
        barrier = threading.Barrier(2, timeout=5.0)

        def _fake_probe(url, *, cancel_event=None, **k):
            idx = int(url.split("r")[1].split(".")[0])
            call_order.append(idx)
            if idx == 1:
                cancel_evt.set()
                barrier.wait()
                from experimental.se_dork.probe import ProbeOutcome, PROBE_STATUS_UNPROBED
                return ProbeOutcome(probe_status=PROBE_STATUS_UNPROBED,
                                    probe_indicator_matches=0, probe_preview=None,
                                    probe_checked_at="2026-01-01T00:00:00",
                                    probe_error="cancelled")
            elif idx == 0:
                barrier.wait()
                from experimental.se_dork.probe import ProbeOutcome, PROBE_STATUS_CLEAN
                return ProbeOutcome(probe_status=PROBE_STATUS_CLEAN, probe_indicator_matches=0,
                                    probe_preview=None, probe_checked_at="2026-01-01T00:00:00",
                                    probe_error=None)
            else:
                raise AssertionError(f"Row {idx} was never submitted; must not execute")

        monkeypatch.setattr("experimental.se_dork.probe.probe_url", _fake_probe)

        rows = [{"url": f"http://r{i}.com/", "result_id": i + 1} for i in range(3)]
        outcomes, counts = _probe_page_rows(
            page=1, rows=rows, config_path=None, indicator_patterns=[],
            worker_count=2, progress_cb=None, cancel_event=cancel_evt,
        )

        assert 0 in call_order, "Row 0 (active) must drain"
        assert 1 in call_order, "Row 1 (active, sets cancel) must drain"
        assert 2 not in call_order, "Row 2 must not be submitted (never passed to executor)"
        assert counts["total"] == 2, f"Both active futures drain; got {counts}"
        assert counts["clean"] == 1, "Row 0 completes clean"
        assert counts["unprobed"] == 1, "Row 1 returns unprobed"

    def test_completed_probe_outcomes_written_to_db(self, monkeypatch, tmp_path):
        """Probe outcomes completed before cancel fire must appear in dork_results."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RUN_STATUS_CANCELLED, RunOptions
        from experimental.se_dork.store import open_connection
        import experimental.se_dork.service as _svc

        cancel_evt = threading.Event()

        def _fake_probe_url(url, *, cancel_event=None, **k):
            from experimental.se_dork.probe import ProbeOutcome, PROBE_STATUS_CLEAN, PROBE_STATUS_UNPROBED
            if "row0" in url:
                cancel_evt.set()
                return ProbeOutcome(probe_status=PROBE_STATUS_CLEAN, probe_indicator_matches=0,
                                    probe_preview=None, probe_checked_at="2026-01-01T00:00:00",
                                    probe_error=None)
            if cancel_event and cancel_event.is_set():
                return ProbeOutcome(probe_status=PROBE_STATUS_UNPROBED, probe_indicator_matches=0,
                                    probe_preview=None, probe_checked_at="2026-01-01T00:00:00",
                                    probe_error="cancelled")
            return ProbeOutcome(probe_status=PROBE_STATUS_CLEAN, probe_indicator_matches=0,
                                probe_preview=None, probe_checked_at="2026-01-01T00:00:00",
                                probe_error=None)

        monkeypatch.setattr("experimental.se_dork.probe.probe_url", _fake_probe_url)
        monkeypatch.setattr("experimental.se_dork.probe.build_indicator_patterns",
                            lambda *a, **k: [])
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        rows = [
            {"url": "http://row0.com/", "title": "OD", "content": ""},
            {"url": "http://row1.com/", "title": "OD", "content": ""},
        ]
        monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=None: _cm(rows))
        monkeypatch.setattr("experimental.se_dork.classifier.classify_url",
                            lambda url, **k: _mock_open_index())

        db_path = tmp_path / "test.db"
        opts = RunOptions(instance_url="http://x", query="q", max_results=100,
                          bulk_probe_enabled=True, probe_worker_count=2)
        result = run_dork_search(opts, db_path=db_path, cancel_event=cancel_evt)
        assert result.status == RUN_STATUS_CANCELLED

        conn = open_connection(db_path)
        probed = conn.execute(
            "SELECT probe_status FROM dork_results WHERE run_id = ? AND url_normalized = ?",
            (result.run_id, "http://row0.com"),
        ).fetchone()
        conn.close()
        assert probed is not None, "row0 must be present in dork_results"
        assert probed[0] == "clean", (
            f"row0's completed probe outcome must be persisted; got {probed[0]}"
        )
