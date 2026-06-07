"""Unit tests for scripts/live_test_searxng.py.

All tests use mocked network/service calls.  No live execution occurs on import.
Import pattern: add repo root to sys.path, then import via package.
"""
from __future__ import annotations

import json
import signal
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import live_test_searxng as lts
from experimental.se_dork.models import (
    RunResult, RUN_STATUS_DONE, RUN_STATUS_CANCELLED, RUN_STATUS_ERROR,
    DEFAULT_MAX_RESULTS,
)
from experimental.se_dork.store import (
    init_db,
    insert_run,
    open_connection,
    update_run,
    update_run_progress,
)
from experimental.se_dork.models import RunOptions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_result(**kw) -> RunResult:
    # run_id=None by default so tests that mock run_dork_search don't trigger
    # DB-file-missing failures. Set run_id explicitly when testing DB checks.
    defaults = dict(
        run_id=None, fetched_count=10, deduped_count=3, status=RUN_STATUS_DONE,
        error=None, verified_count=10, probe_enabled=False,
        probe_total=0, probe_clean=0, probe_issue=0, probe_unprobed=0,
        pages_fetched=2, pacing_delay_seconds=0.0,
        hard_retry_count=0, hard_retry_delay_seconds=0.0,
    )
    defaults.update(kw)
    return RunResult(**defaults)


def _make_args(**kw) -> SimpleNamespace:
    defaults = dict(
        confirm_live=True, instance_url="http://x:8090", query="q",
        max_results=100, request_timeout=15, short_retry_delay=30,
        long_retry_delay=180, probe=False, keep_db=False,
        cancel_after_classify=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# TestConfirmLiveGate
# ---------------------------------------------------------------------------
class TestConfirmLiveGate:
    def test_missing_flag_exits_2_immediately(self):
        with pytest.raises(SystemExit) as exc:
            lts._parse_args([])
        assert exc.value.code == 2

    def test_missing_flag_reads_no_settings(self):
        with patch.object(lts, "get_config_store") as mock_cs:
            with pytest.raises(SystemExit):
                lts._parse_args([])
        mock_cs.assert_not_called()

    def test_missing_flag_creates_no_tempdir(self):
        with patch.object(lts.tempfile, "mkdtemp") as mock_mkd:
            with pytest.raises(SystemExit):
                lts._parse_args([])
        mock_mkd.assert_not_called()

    def test_missing_flag_calls_no_service(self):
        with patch.object(lts, "run_dork_search") as mock_rds:
            with pytest.raises(SystemExit):
                lts._parse_args([])
        mock_rds.assert_not_called()

    def test_flag_present_proceeds_past_gate(self):
        args = lts._parse_args(["--confirm-live"])
        assert args.confirm_live is True


# ---------------------------------------------------------------------------
# TestParseArgs
# ---------------------------------------------------------------------------
class TestParseArgs:
    def test_probe_default_off(self):
        assert lts._parse_args(["--confirm-live"]).probe is False

    def test_keep_db_default_off(self):
        assert lts._parse_args(["--confirm-live"]).keep_db is False

    def test_cancel_after_classify_value(self):
        args = lts._parse_args(["--confirm-live", "--cancel-after-classify", "3"])
        assert args.cancel_after_classify == 3

    def test_explicit_max_results_out_of_range_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            lts._parse_args(["--confirm-live", "--max-results", "9999"])
        assert exc.value.code == 2

    def test_explicit_timeout_out_of_range_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            lts._parse_args(["--confirm-live", "--timeout", "999"])
        assert exc.value.code == 2

    def test_cancel_after_classify_zero_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            lts._parse_args(["--confirm-live", "--cancel-after-classify", "0"])
        assert exc.value.code == 2

    def test_cancel_after_classify_negative_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            lts._parse_args(["--confirm-live", "--cancel-after-classify", "-1"])
        assert exc.value.code == 2

    def test_stale_prefs_coerced_not_rejected(self, tmp_path):
        prefs = {"unified_scan_dialog": {
            "searxng_instance_url": "http://x",
            "searxng_max_results": 9999,
        }}
        pf = tmp_path / "prefs.json"
        pf.write_text(json.dumps(prefs))
        mock_store = MagicMock()
        mock_store.load_user_prefs.return_value = prefs
        with patch.object(lts, "get_config_store", return_value=mock_store):
            args = lts._parse_args(["--confirm-live"])
            opts = lts._resolve_run_options(args)
        assert opts.max_results == 1000  # clamped


# ---------------------------------------------------------------------------
# TestLoadUserPrefs
# ---------------------------------------------------------------------------
class TestLoadUserPrefs:
    def test_missing_file_returns_empty_dict(self):
        mock_store = MagicMock()
        mock_store.load_user_prefs.side_effect = FileNotFoundError("no file")
        with patch.object(lts, "get_config_store", return_value=mock_store):
            result = lts._load_user_prefs()
        assert result == {}

    def test_dotted_key_navigation(self):
        data = {"a": {"b": {"c": 42}}}
        assert lts._load_pref(data, "a.b.c", 0) == 42

    def test_none_value_returns_default(self):
        assert lts._load_pref({"a": None}, "a", "default") == "default"

    def test_empty_string_returns_default(self):
        assert lts._load_pref({"a": ""}, "a", "default") == "default"

    def test_missing_key_returns_default(self):
        assert lts._load_pref({}, "a.b", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# TestResolveRunOptions
# ---------------------------------------------------------------------------
class TestResolveRunOptions:
    def _mock_store(self, prefs: dict) -> MagicMock:
        m = MagicMock()
        m.load_user_prefs.return_value = prefs
        return m

    def test_cli_instance_url_overrides_prefs(self):
        prefs = {"unified_scan_dialog": {"searxng_instance_url": "http://other"}}
        args = lts._parse_args(["--confirm-live", "--instance-url", "http://cli"])
        with patch.object(lts, "get_config_store",
                          return_value=self._mock_store(prefs)):
            opts = lts._resolve_run_options(args)
        assert opts.instance_url == "http://cli"

    def test_prefs_instance_url_used_when_no_override(self):
        prefs = {"unified_scan_dialog": {"searxng_instance_url": "http://from-prefs"}}
        args = lts._parse_args(["--confirm-live"])
        with patch.object(lts, "get_config_store",
                          return_value=self._mock_store(prefs)):
            opts = lts._resolve_run_options(args)
        assert opts.instance_url == "http://from-prefs"

    def test_absent_instance_url_exits_2(self):
        args = lts._parse_args(["--confirm-live"])
        with patch.object(lts, "get_config_store",
                          return_value=self._mock_store({})):
            with pytest.raises(SystemExit) as exc:
                lts._resolve_run_options(args)
        assert exc.value.code == 2

    def test_max_results_default_is_DEFAULT_MAX_RESULTS(self):
        prefs = {"unified_scan_dialog": {"searxng_instance_url": "http://x"}}
        args = lts._parse_args(["--confirm-live"])
        with patch.object(lts, "get_config_store",
                          return_value=self._mock_store(prefs)):
            opts = lts._resolve_run_options(args)
        assert opts.max_results == DEFAULT_MAX_RESULTS

    def test_run_dork_search_receives_exact_temp_db_path(self, tmp_path):
        prefs = {"unified_scan_dialog": {"searxng_instance_url": "http://x"}}
        args = _make_args()
        opts = RunOptions(instance_url="http://x", query="q")
        expected_db = tmp_path / "se_dork_live.db"
        mock_rds = MagicMock(return_value=_make_result())
        with patch.object(lts, "run_dork_search", mock_rds), \
             patch.object(lts.tempfile, "mkdtemp", return_value=str(tmp_path)), \
             patch.object(lts, "get_config_store",
                          return_value=self._mock_store(prefs)):
            lts.main(["--confirm-live", "--instance-url", "http://x",
                       "--max-results", "10"])
        call_kwargs = mock_rds.call_args
        assert call_kwargs.kwargs["db_path"] == expected_db

    def test_run_dork_search_db_path_never_none(self, tmp_path):
        prefs = {"unified_scan_dialog": {"searxng_instance_url": "http://x"}}
        mock_rds = MagicMock(return_value=_make_result())
        with patch.object(lts, "run_dork_search", mock_rds), \
             patch.object(lts.tempfile, "mkdtemp", return_value=str(tmp_path)), \
             patch.object(lts, "get_config_store",
                          return_value=self._mock_store(prefs)):
            lts.main(["--confirm-live", "--instance-url", "http://x",
                       "--max-results", "10"])
        for c in mock_rds.call_args_list:
            assert c.kwargs.get("db_path") is not None


# ---------------------------------------------------------------------------
# TestCancelTrigger
# ---------------------------------------------------------------------------
class TestCancelTrigger:
    def test_fires_after_n_classified_pages(self):
        evt = threading.Event()
        trigger = lts._make_cancel_trigger(evt, 2)
        trigger("Page 1: classified 5; retained 2 open indexes.")
        assert not evt.is_set()
        trigger("Page 2: classified 3; retained 1 open indexes.")
        assert evt.is_set()

    def test_stored_message_not_counted(self):
        evt = threading.Event()
        trigger = lts._make_cancel_trigger(evt, 1)
        trigger("Page 1: stored 5 rows.")
        assert not evt.is_set()

    def test_start_message_not_counted(self):
        evt = threading.Event()
        trigger = lts._make_cancel_trigger(evt, 1)
        trigger("Querying SearXNG page 1...")
        assert not evt.is_set()

    def test_same_page_classified_twice_counts_once(self):
        evt = threading.Event()
        trigger = lts._make_cancel_trigger(evt, 2)
        trigger("Page 1: classified 5; retained 2 open indexes.")
        trigger("Page 1: classified 5; retained 2 open indexes.")
        assert not evt.is_set()  # still only 1 unique page

    def test_n_minus_one_does_not_fire(self):
        evt = threading.Event()
        trigger = lts._make_cancel_trigger(evt, 3)
        trigger("Page 1: classified 5; retained 2 open indexes.")
        trigger("Page 2: classified 3; retained 1 open indexes.")
        assert not evt.is_set()


# ---------------------------------------------------------------------------
# TestExtractStageEvents
# ---------------------------------------------------------------------------
class TestExtractStageEvents:
    def _ev(self, msgs, probe=False):
        return lts._extract_stage_events(msgs, probe)

    def test_normal_page_flow_no_probe(self):
        msgs = [
            "Querying SearXNG page 1...",
            "Page 1: received 5 results, 5 new (5 unique total).",
            "Page 1: stored 5 rows.",
            "Page 1: classified 5; retained 2 open indexes.",
        ]
        ev = self._ev(msgs)
        assert ev["page_start_idx"] == {1: 0}
        assert ev["page_received_idx"] == {1: 1}
        assert ev["page_received_new"] == {1: 5}
        assert ev["page_stored_idx"] == {1: 2}
        assert ev["page_classified_idx"] == {1: 3}
        assert ev["page_retained_count"] == {1: 2}

    def test_normal_page_flow_with_probe(self):
        msgs = [
            "Querying SearXNG page 1...",
            "Page 1: received 5 results, 5 new (5 unique total).",
            "Page 1: stored 5 rows.",
            "Page 1: classified 5; retained 2 open indexes.",
            "Page 1: probed 2 (1 clean, 0 flagged, 1 unprobed).",
        ]
        ev = self._ev(msgs, probe=True)
        assert ev["page_probed_idx"] == {1: 4}

    def test_duplicate_only_page_uses_received_as_terminal(self):
        msgs = [
            "Querying SearXNG page 2...",
            "Page 2: received 5 results, 0 new (10 unique total).",
        ]
        ev = self._ev(msgs)
        assert ev["page_received_new"][2] == 0
        assert 2 not in ev["page_stored_idx"]
        assert 2 not in ev["page_classified_idx"]

    def test_duplicate_only_page_no_stored_or_classified(self):
        msgs = ["Page 2: received 3 results, 0 new (10 unique total)."]
        ev = self._ev(msgs)
        assert 2 not in ev["page_stored_idx"]

    def test_positive_new_page_requires_stored_and_classified(self):
        msgs = ["Page 1: received 5 results, 3 new (8 unique total)."]
        ev = self._ev(msgs)
        assert ev["page_received_new"][1] == 3

    def test_retry_not_in_start_idx(self):
        msgs = ["Querying SearXNG page 1 (retry)..."]
        ev = self._ev(msgs)
        assert 1 not in ev["page_start_idx"]

    def test_retry_in_retry_set(self):
        msgs = ["Querying SearXNG page 1 (retry)..."]
        ev = self._ev(msgs)
        assert 1 in ev["retry_pages"]

    def test_run_complete_captured(self):
        msgs = ["Run complete: fetched 10, verified 10, retained 3 open indexes."]
        ev = self._ev(msgs)
        assert ev["run_complete_idx"] == 0

    def test_cancelled_captured(self):
        msgs = ["Cancelled."]
        ev = self._ev(msgs)
        assert ev["cancelled_idx"] == 0


# ---------------------------------------------------------------------------
# TestCheckStageOrder
# ---------------------------------------------------------------------------
class TestCheckStageOrder:
    def _order(self, msgs, probe=False):
        ev = lts._extract_stage_events(msgs, probe)
        return lts._check_stage_order(ev, probe)

    def _page(self, n, recv_new=5, retained=0, probed=False):
        """Build canonical progress messages for one page."""
        msgs = [f"Querying SearXNG page {n}..."]
        msgs.append(f"Page {n}: received 5 results, {recv_new} new ({n*5} unique total).")
        if recv_new > 0:
            msgs.append(f"Page {n}: stored 5 rows.")
            msgs.append(f"Page {n}: classified 5; retained {retained} open indexes.")
            if probed:
                msgs.append(f"Page {n}: probed {retained} (0 clean, 0 flagged, {retained} unprobed).")
        return msgs

    def test_correct_order_no_failures(self):
        msgs = self._page(1, retained=2) + ["Run complete: fetched 5, verified 5, retained 2 open indexes."]
        assert self._order(msgs) == []

    def _ev_base(self, **kw):
        base = {
            "page_start_idx": {1: 0}, "page_received_idx": {1: 1},
            "page_received_new": {1: 3}, "page_stored_idx": {1: 2},
            "page_classified_idx": {1: 3}, "page_retained_count": {1: 0},
            "page_probed_idx": {},
            "run_complete_idx": None, "cancelled_idx": None, "retry_pages": set(),
        }
        base.update(kw)
        return base

    def test_stored_before_start_is_failure(self):
        ev = self._ev_base(page_start_idx={1: 5}, page_stored_idx={1: 2})
        fails = lts._check_stage_order(ev, False)
        assert any("start" in f and "received" in f for f in fails)

    def test_received_after_stored_is_failure(self):
        ev = self._ev_base(page_received_idx={1: 5}, page_stored_idx={1: 2})
        fails = lts._check_stage_order(ev, False)
        assert any("received" in f and "stored" in f for f in fails)

    def test_classified_before_stored_is_failure(self):
        ev = self._ev_base(page_stored_idx={1: 5}, page_classified_idx={1: 3})
        fails = lts._check_stage_order(ev, False)
        assert any("stored" in f and "classified" in f for f in fails)

    def test_probed_before_classified_is_failure(self):
        ev = self._ev_base(
            page_classified_idx={1: 5}, page_retained_count={1: 2},
            page_probed_idx={1: 3},
        )
        fails = lts._check_stage_order(ev, True)
        assert any("classified" in f and "probed" in f for f in fails)

    def test_cross_page_violation_detected(self):
        ev = self._ev_base(
            page_start_idx={1: 0, 2: 5},
            page_received_idx={1: 1, 2: 6}, page_received_new={1: 3, 2: 3},
            page_stored_idx={1: 2, 2: 7},
            page_classified_idx={1: 10, 2: 8},
            page_retained_count={1: 0, 2: 0},
        )
        fails = lts._check_stage_order(ev, False)
        assert any("Cross-page" in f for f in fails)

    def test_duplicate_only_page_passes_without_stored_classified(self):
        msgs = (
            self._page(1, retained=0)
            + [
                "Querying SearXNG page 2...",
                "Page 2: received 5 results, 0 new (5 unique total).",
            ]
        )
        assert self._order(msgs) == []

    def test_duplicate_only_cross_page_ordering(self):
        msgs = (
            self._page(1, retained=0)
            + [
                "Querying SearXNG page 2...",
                "Page 2: received 5 results, 0 new (5 unique total).",
            ]
            + self._page(3, retained=0)
        )
        assert self._order(msgs) == []

    def test_zero_retained_no_probed_message_not_failure(self):
        # probe_enabled, page has zero retained rows → no probed msg required
        msgs = self._page(1, retained=0)
        assert self._order(msgs, probe=True) == []

    def test_retained_rows_require_probe_when_probe_enabled(self):
        # probe_enabled, page has retained rows → probed event is mandatory
        msgs = self._page(1, retained=2)  # no probed msg → FAIL
        fails = self._order(msgs, probe=True)
        assert any("missing probed event" in f for f in fails)

    def test_retry_does_not_cause_false_cross_page_failure(self):
        msgs = (
            self._page(1, retained=0)
            + [
                "Querying SearXNG page 2 (retry)...",
            ]
            + self._page(2, retained=0)
        )
        assert self._order(msgs) == []


# ---------------------------------------------------------------------------
# TestCheckDbIntegrity
# ---------------------------------------------------------------------------
class TestCheckDbIntegrity:
    def _make_db(self, tmp_path) -> tuple:
        db = tmp_path / "t.db"
        init_db(db)
        opts = RunOptions(instance_url="http://x", query="q")
        conn = open_connection(db)
        run_id = insert_run(conn, opts, "2026-01-01 00:00:00")
        conn.execute(
            "UPDATE dork_runs SET finished_at=?, fetched_count=?, "
            "deduped_count=?, verified_count=?, status=? WHERE run_id=?",
            ("2026-01-01 00:01:00", 0, 0, 0, "done", run_id)
        )
        conn.commit()
        conn.close()
        return db, run_id

    def test_passes_on_valid_temp_db(self, tmp_path):
        db, run_id = self._make_db(tmp_path)
        result = _make_result(run_id=run_id, fetched_count=0, deduped_count=0,
                               verified_count=0, status="done")
        checks = lts._check_db(db, result, False)
        failures = [c for c in checks if c.passed is False]
        assert failures == [], [c.detail for c in failures]

    def test_fails_on_missing_table(self, tmp_path):
        db = tmp_path / "empty.db"
        sqlite3.connect(str(db)).close()
        # run_id=None so consistency queries are skipped; we only test schema detection.
        result = _make_result(run_id=None)
        checks = lts._check_db(db, result, False)
        assert any(c.passed is False and "schema" in c.label.lower() for c in checks)

    def test_fk_violation_detected(self, tmp_path):
        db, _ = self._make_db(tmp_path)
        # Insert a result row referencing a nonexistent run_id (FK off at insert)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO dork_results (run_id, url, url_normalized, probe_status) "
            "VALUES (9999, 'http://x', 'http://x', 'unprobed')"
        )
        conn.commit()
        conn.close()
        result = _make_result(run_id=1)
        checks = lts._check_db(db, result, False)
        assert any(
            c.passed is False and "foreign_key_check" in c.label for c in checks
        )

    def test_row_count_mismatch_detected(self, tmp_path):
        db, run_id = self._make_db(tmp_path)
        result = _make_result(run_id=run_id, deduped_count=99)
        checks = lts._check_db(db, result, False)
        assert any(c.passed is False and "row count" in c.label for c in checks)

    def test_status_field_mismatch_detected(self, tmp_path):
        db, run_id = self._make_db(tmp_path)
        result = _make_result(run_id=run_id, status="cancelled")
        checks = lts._check_db(db, result, False)
        assert any(
            c.passed is False and "status" in c.label.lower() for c in checks
        )

    def test_zero_retained_rows_is_not_a_failure(self, tmp_path):
        db, run_id = self._make_db(tmp_path)
        result = _make_result(run_id=run_id, deduped_count=0, fetched_count=0,
                               verified_count=0)
        checks = lts._check_db(db, result, False)
        assert not any(c.passed is False for c in checks)

    def test_probe_parity_uses_probe_checked_at_filter(self, tmp_path):
        db, run_id = self._make_db(tmp_path)
        conn = open_connection(db)
        # Row attempted: probe_checked_at IS NOT NULL
        conn.execute(
            "INSERT INTO dork_results "
            "(run_id, url, url_normalized, probe_status, probe_checked_at) "
            "VALUES (?, 'http://a', 'http://a', 'clean', '2026-01-01 00:01:00')",
            (run_id,)
        )
        # Row NOT attempted: probe_checked_at IS NULL (default unprobed)
        conn.execute(
            "INSERT INTO dork_results "
            "(run_id, url, url_normalized, probe_status) "
            "VALUES (?, 'http://b', 'http://b', 'unprobed')",
            (run_id,)
        )
        conn.execute(
            "UPDATE dork_runs SET fetched_count=2, deduped_count=2, "
            "verified_count=2 WHERE run_id=?",
            (run_id,)
        )
        conn.commit()
        conn.close()
        # probe_total=1 (only the attempted row), deduped_count=2
        result = _make_result(run_id=run_id, fetched_count=2, deduped_count=2,
                               verified_count=2, probe_enabled=True, probe_total=1,
                               probe_clean=1, probe_issue=0, probe_unprobed=0)
        checks = lts._check_db(db, result, True)
        assert not any(c.passed is False for c in checks)


# ---------------------------------------------------------------------------
# TestTempDirLifecycle
# ---------------------------------------------------------------------------
class TestTempDirLifecycle:
    def _run_main(self, tmp_path, extra_args=None):
        mock_result = _make_result()
        with patch.object(lts, "run_dork_search", return_value=mock_result), \
             patch.object(lts.tempfile, "mkdtemp", return_value=str(tmp_path)), \
             patch.object(lts, "get_config_store",
                          return_value=MagicMock(
                              load_user_prefs=lambda: {}
                          )):
            return lts.main(
                ["--confirm-live", "--instance-url", "http://x",
                 "--max-results", "10"] + (extra_args or [])
            )

    def test_default_deletes_dir(self, tmp_path):
        self._run_main(tmp_path)
        assert not tmp_path.exists()

    def test_keep_db_retains_dir(self, tmp_path):
        self._run_main(tmp_path, ["--keep-db"])
        assert tmp_path.exists()

    def test_keep_db_prints_exact_db_path(self, tmp_path, capsys):
        self._run_main(tmp_path, ["--keep-db"])
        out = capsys.readouterr().out
        assert str(tmp_path / "se_dork_live.db") in out

    def test_cleanup_failure_warns_not_raises(self, tmp_path):
        with patch.object(lts.shutil, "rmtree", side_effect=OSError("busy")), \
             patch.object(lts, "run_dork_search", return_value=_make_result()), \
             patch.object(lts.tempfile, "mkdtemp", return_value=str(tmp_path)), \
             patch.object(lts, "get_config_store",
                          return_value=MagicMock(load_user_prefs=lambda: {})):
            rc = lts.main(["--confirm-live", "--instance-url", "http://x",
                            "--max-results", "10"])
        assert rc == 1  # promoted from 0

    def test_cleanup_not_called_while_worker_alive(self, tmp_path):
        import time
        cleanup_times = []
        thread_done_times = []

        def slow_search(*a, **kw):
            time.sleep(0.05)
            thread_done_times.append(time.monotonic())
            return _make_result()

        original_rmtree = lts.shutil.rmtree

        def tracking_rmtree(path, **kw):
            cleanup_times.append(time.monotonic())
            original_rmtree(path, **kw)

        with patch.object(lts, "run_dork_search", side_effect=slow_search), \
             patch.object(lts.tempfile, "mkdtemp", return_value=str(tmp_path)), \
             patch.object(lts.shutil, "rmtree", side_effect=tracking_rmtree), \
             patch.object(lts, "get_config_store",
                          return_value=MagicMock(load_user_prefs=lambda: {})):
            lts.main(["--confirm-live", "--instance-url", "http://x",
                       "--max-results", "10"])

        assert cleanup_times and thread_done_times, "rmtree or worker never ran"
        assert cleanup_times[0] >= thread_done_times[0], \
            "cleanup started before worker thread finished"


# ---------------------------------------------------------------------------
# TestSignalHandling
# ---------------------------------------------------------------------------
class TestSignalHandling:
    def _make_handler(self):
        cancel_event = threading.Event()
        state = {"interrupted": False, "sigint_count": 0}

        def _handle(signum, frame):
            state["sigint_count"] += 1
            state["interrupted"] = True
            cancel_event.set()
            if state["sigint_count"] >= 2:
                signal.default_int_handler(signum, frame)

        return _handle, cancel_event, state

    def test_first_sigint_sets_cancel_event(self):
        handler, evt, _ = self._make_handler()
        handler(signal.SIGINT, None)
        assert evt.is_set()

    def test_first_sigint_sets_interrupted_flag(self):
        handler, _, state = self._make_handler()
        handler(signal.SIGINT, None)
        assert state["interrupted"] is True

    def test_first_sigint_produces_exit_3(self, tmp_path):
        barrier = threading.Barrier(2)
        cancelled_evt = threading.Event()

        def slow_search(*a, **kw):
            barrier.wait()
            cancelled_evt.wait(timeout=5)
            return _make_result(status=RUN_STATUS_CANCELLED, error=None)

        with patch.object(lts, "run_dork_search", side_effect=slow_search), \
             patch.object(lts.tempfile, "mkdtemp", return_value=str(tmp_path)), \
             patch.object(lts, "get_config_store",
                          return_value=MagicMock(load_user_prefs=lambda: {})):

            def _send_sigint():
                barrier.wait()
                import os
                os.kill(os.getpid(), signal.SIGINT)
                cancelled_evt.set()

            t = threading.Thread(target=_send_sigint, daemon=True)
            t.start()
            rc = lts.main(["--confirm-live", "--instance-url", "http://x",
                            "--max-results", "10"])
            t.join(timeout=5)

        assert rc == 3

    def test_second_sigint_invokes_default_int_handler(self):
        handler, _, _ = self._make_handler()
        with patch.object(signal, "default_int_handler") as mock_dih:
            handler(signal.SIGINT, None)  # first
            try:
                handler(signal.SIGINT, None)  # second
            except Exception:
                pass
        mock_dih.assert_called_once()

    def test_previous_handler_restored_after_run(self, tmp_path):
        original = signal.getsignal(signal.SIGINT)
        with patch.object(lts, "run_dork_search", return_value=_make_result()), \
             patch.object(lts.tempfile, "mkdtemp", return_value=str(tmp_path)), \
             patch.object(lts, "get_config_store",
                          return_value=MagicMock(load_user_prefs=lambda: {})):
            lts.main(["--confirm-live", "--instance-url", "http://x",
                       "--max-results", "10"])
        assert signal.getsignal(signal.SIGINT) is original


# ---------------------------------------------------------------------------
# TestExitCodePriority
# ---------------------------------------------------------------------------
class TestExitCodePriority:
    def _run(self, tmp_path, result_override=None, extra=None,
             inject_messages=None):
        r = result_override or _make_result()

        def fake_rds(opts, db_path=None, progress_cb=None, cancel_event=None):
            if inject_messages and progress_cb:
                for msg in inject_messages:
                    progress_cb(msg)
            return r

        with patch.object(lts, "run_dork_search", side_effect=fake_rds), \
             patch.object(lts.tempfile, "mkdtemp", return_value=str(tmp_path)), \
             patch.object(lts, "get_config_store",
                          return_value=MagicMock(load_user_prefs=lambda: {})):
            return lts.main(
                ["--confirm-live", "--instance-url", "http://x",
                 "--max-results", "10"] + (extra or [])
            )

    def _done_messages(self, pages=1):
        messages = []
        for page in range(1, pages + 1):
            messages.extend([
                f"Querying SearXNG page {page}...",
                f"Page {page}: received 5 results, 5 new ({page * 5} unique total).",
                f"Page {page}: stored 5 rows.",
                f"Page {page}: classified 5; retained 0 open indexes.",
            ])
        messages.append(
            f"Run complete: fetched {pages * 5}, verified {pages * 5}, "
            "retained 0 open indexes."
        )
        return messages

    def _persist_run(self, db_path, status, *, pages=1):
        init_db(db_path)
        conn = open_connection(db_path)
        try:
            run_id = insert_run(
                conn,
                RunOptions(instance_url="http://x", query="q"),
                "2026-06-07 12:00:00",
            )
            update_run_progress(
                conn, run_id, pages * 5, 0, pages * 5,
            )
            update_run(
                conn,
                run_id,
                "2026-06-07 12:01:00",
                pages * 5,
                0,
                status,
            )
            conn.commit()
            return run_id
        finally:
            conn.close()

    def _valid_service(self, status, messages, *, pages=1):
        def fake_rds(opts, db_path=None, progress_cb=None, cancel_event=None):
            run_id = self._persist_run(db_path, status, pages=pages)
            for msg in messages:
                progress_cb(msg)
            return _make_result(
                run_id=run_id,
                status=status,
                error=None,
                fetched_count=pages * 5,
                deduped_count=0,
                verified_count=pages * 5,
                pages_fetched=pages,
            )
        return fake_rds

    def _run_service(self, tmp_path, service, extra=None):
        with patch.object(lts, "run_dork_search", side_effect=service), \
             patch.object(lts.tempfile, "mkdtemp", return_value=str(tmp_path)), \
             patch.object(lts, "get_config_store",
                          return_value=MagicMock(load_user_prefs=lambda: {})):
            return lts.main(
                ["--confirm-live", "--instance-url", "http://x",
                 "--max-results", "10"] + (extra or [])
            )

    def _cancelled_messages(self, pages):
        messages = self._done_messages(pages)[:-1]
        messages.append("Cancelled.")
        return messages

    def test_all_pass_returns_0(self, tmp_path):
        service = self._valid_service(
            RUN_STATUS_DONE, self._done_messages(), pages=1,
        )
        assert self._run_service(tmp_path, service) == 0

    def test_cancel_after_classify_asserts_cancelled(self, tmp_path):
        service = self._valid_service(
            RUN_STATUS_CANCELLED, self._cancelled_messages(2), pages=2,
        )
        assert self._run_service(
            tmp_path, service, ["--cancel-after-classify", "2"],
        ) == 0

    def test_cancelled_without_run_id_is_failure(self, tmp_path):
        r = _make_result(status=RUN_STATUS_CANCELLED, run_id=None)
        assert self._run(
            tmp_path,
            result_override=r,
            extra=["--cancel-after-classify", "1"],
            inject_messages=self._cancelled_messages(1),
        ) == 1

    def test_cancelled_without_progress_is_failure(self, tmp_path):
        r = _make_result(status=RUN_STATUS_CANCELLED, run_id=None)
        assert self._run(
            tmp_path, result_override=r,
            extra=["--cancel-after-classify", "1"],
        ) == 1

    def test_cancelled_before_requested_boundary_is_failure(self, tmp_path):
        service = self._valid_service(
            RUN_STATUS_CANCELLED, self._cancelled_messages(1), pages=1,
        )
        assert self._run_service(
            tmp_path, service, ["--cancel-after-classify", "2"],
        ) == 1

    def test_done_trace_page_count_mismatch_is_failure(self, tmp_path):
        service = self._valid_service(
            RUN_STATUS_DONE, self._done_messages(1), pages=2,
        )
        assert self._run_service(tmp_path, service) == 1

    def test_done_complete_before_page_terminal_is_failure(self, tmp_path):
        messages = self._done_messages()
        messages.insert(0, messages.pop())
        service = self._valid_service(RUN_STATUS_DONE, messages, pages=1)
        assert self._run_service(tmp_path, service) == 1

    def _legacy_done_messages(self):
        return [
            "Querying SearXNG page 1...",
            "Page 1: received 5 results, 5 new (5 unique total).",
            "Page 1: stored 5 rows.",
            "Page 1: classified 5; retained 0 open indexes.",
            "Run complete: fetched 5, verified 5, retained 0 open indexes.",
        ]

    def test_cleanup_failure_promotes_0_to_1(self, tmp_path):
        service = self._valid_service(
            RUN_STATUS_DONE, self._done_messages(), pages=1,
        )
        with patch.object(lts.shutil, "rmtree", side_effect=OSError):
            rc = self._run_service(tmp_path, service)
        assert rc == 1

    def test_cleanup_failure_does_not_change_existing_1(self, tmp_path):
        bad = _make_result(status=RUN_STATUS_ERROR, error="boom")
        with patch.object(lts.shutil, "rmtree", side_effect=OSError):
            rc = self._run(tmp_path, result_override=bad)
        assert rc == 1

    def test_normal_run_asserts_done_not_cancelled(self, tmp_path):
        r = _make_result(status=RUN_STATUS_CANCELLED)
        assert self._run(tmp_path, result_override=r) == 1

    def test_unexpected_cancelled_on_plain_run_is_failure(self, tmp_path):
        r = _make_result(status=RUN_STATUS_CANCELLED)
        # No --cancel-after-classify flag — cancelled is unexpected
        assert self._run(tmp_path, result_override=r) == 1

    def test_done_with_run_id_none_is_failure(self, tmp_path):
        # status=done but run_id=None — false PASS guard
        r = _make_result(status=RUN_STATUS_DONE, run_id=None)
        assert self._run(tmp_path, result_override=r,
                         inject_messages=self._legacy_done_messages()) == 1

    def test_done_with_no_progress_messages_is_failure(self, tmp_path):
        # status=done but no page-start events — false PASS guard
        r = _make_result(status=RUN_STATUS_DONE, run_id=None)
        # No inject_messages → no page events → FAIL on mandatory done checks
        assert self._run(tmp_path, result_override=r) == 1
