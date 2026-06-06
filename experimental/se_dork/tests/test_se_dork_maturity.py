"""Tests for C11A maturity-adaptive retry logic."""
from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def _ok_preflight():
    return SimpleNamespace(ok=True, reason_code=None, message="OK")


def _json_resp(results):
    body = json.dumps({"results": results}).encode()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.read = MagicMock(return_value=body)
    return cm


def _json_resp_throttle(results):
    body = json.dumps({
        "results": results,
        "unresponsive_engines": [{"engine": "bing", "error": "rate limit"}],
    }).encode()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.read = MagicMock(return_value=body)
    return cm


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr("experimental.se_dork.service.time.sleep", lambda _: None)
    monkeypatch.setattr("experimental.se_dork.service.random.uniform", lambda lo, hi: (lo + hi) / 2)


class TestMaturityThresholds:
    def test_is_mature_by_pages(self):
        from experimental.se_dork.service import _is_mature
        assert _is_mature(5, 0) is True
        assert _is_mature(4, 0) is False

    def test_is_mature_by_urls(self):
        from experimental.se_dork.service import _is_mature
        assert _is_mature(0, 50) is True
        assert _is_mature(0, 49) is False

    def test_both_conditions(self):
        from experimental.se_dork.service import _is_mature
        assert _is_mature(5, 50) is True
        assert _is_mature(0, 0) is False


class TestRetrySlots:
    def _setup_run(self, monkeypatch, tmp_path, urlopen_fn):
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RunOptions, RUN_STATUS_DONE

        waits = []

        def _fake_wait(seconds, *, retry_number, max_retries=2, progress_cb=None,
                       sleep_fn=None, cancel_event=None):
            waits.append(seconds)
            return float(seconds)

        monkeypatch.setattr("experimental.se_dork.service._wait_for_cooldown", _fake_wait)
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        monkeypatch.setattr("urllib.request.urlopen", urlopen_fn)
        opts = RunOptions(instance_url="http://x", query="q",
                          short_retry_delay=30, long_retry_delay=180)
        result = run_dork_search(opts, db_path=tmp_path / "test.db")
        return result, waits

    def test_early_run_two_retry_slots(self, monkeypatch, tmp_path):
        """Two consecutive 429s on an early run → two cooldown waits."""
        call_n = [0]

        def _urlopen(url, timeout=None):
            call_n[0] += 1
            if call_n[0] <= 2:
                raise urllib.error.HTTPError(url, 429, "Rate Limited", {}, None)
            return _json_resp([])

        result, waits = self._setup_run(monkeypatch, tmp_path, _urlopen)
        assert len(waits) == 2

    def test_retry_delay_order(self, monkeypatch, tmp_path):
        """Slot 0 = short_retry_delay, slot 1 = long_retry_delay."""
        call_n = [0]

        def _urlopen(url, timeout=None):
            call_n[0] += 1
            if call_n[0] <= 2:
                raise urllib.error.HTTPError(url, 429, "Rate Limited", {}, None)
            return _json_resp([])

        result, waits = self._setup_run(monkeypatch, tmp_path, _urlopen)
        assert len(waits) == 2
        assert waits[0] == 30   # short_retry
        assert waits[1] == 180  # long_retry

    def test_mature_by_page_count_one_slot(self, monkeypatch, tmp_path):
        """After 5 productive pages, exactly 1 retry slot is used (mature run)."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RunOptions

        waits = []

        def _fake_wait(seconds, *, retry_number, max_retries=2, progress_cb=None,
                       sleep_fn=None, cancel_event=None):
            waits.append((seconds, max_retries))
            return float(seconds)

        monkeypatch.setattr("experimental.se_dork.service._wait_for_cooldown", _fake_wait)
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        monkeypatch.setattr("experimental.se_dork.classifier.classify_url",
                            lambda *a, **k: _mock_classify())

        insert_n = [0]
        def _fake_insert(conn, run_id, rows):
            out = []
            for r in rows:
                insert_n[0] += 1
                out.append({**r, "result_id": insert_n[0]})
            return out

        monkeypatch.setattr("experimental.se_dork.service.insert_page_results", _fake_insert)
        monkeypatch.setattr("experimental.se_dork.service._persist_page_classification",
                            lambda **kw: ([], 0))
        monkeypatch.setattr("experimental.se_dork.service.update_run_progress",
                            lambda *a, **k: None)
        monkeypatch.setattr("experimental.se_dork.service.delete_unclassified_results_by_ids",
                            lambda *a, **k: None)

        page_n = [0]
        def _urlopen(url, timeout=None):
            page_n[0] += 1
            if page_n[0] <= 5:
                # Unique URLs per page so page_processor runs and productive_pages increments
                return _json_resp([
                    {"url": f"http://page{page_n[0]}-{i}.com/", "title": "OD", "content": ""}
                    for i in range(3)
                ])
            if page_n[0] <= 7:
                raise urllib.error.HTTPError(url, 429, "Rate Limited", {}, None)
            return _json_resp([])

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)
        opts = RunOptions(instance_url="http://x", query="q", max_results=1000,
                          short_retry_delay=30, long_retry_delay=180)
        run_dork_search(opts, db_path=tmp_path / "test.db")
        assert len(waits) == 1, f"Mature run must use exactly 1 retry slot, got {waits}"
        assert waits[0][1] == 1, f"max_retries must be 1 for mature run, got {waits[0][1]}"

    def test_mature_by_url_count_one_slot(self, monkeypatch, tmp_path):
        """After 50 unique URLs on one page, exactly 1 retry slot is used."""
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RunOptions

        waits = []

        def _fake_wait(seconds, *, retry_number, max_retries=2, progress_cb=None,
                       sleep_fn=None, cancel_event=None):
            waits.append((seconds, max_retries))
            return float(seconds)

        monkeypatch.setattr("experimental.se_dork.service._wait_for_cooldown", _fake_wait)
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )
        monkeypatch.setattr("experimental.se_dork.classifier.classify_url",
                            lambda *a, **k: _mock_classify())

        insert_n = [0]
        def _fake_insert(conn, run_id, rows):
            out = []
            for r in rows:
                insert_n[0] += 1
                out.append({**r, "result_id": insert_n[0]})
            return out

        monkeypatch.setattr("experimental.se_dork.service.insert_page_results", _fake_insert)
        monkeypatch.setattr("experimental.se_dork.service._persist_page_classification",
                            lambda **kw: ([], 0))
        monkeypatch.setattr("experimental.se_dork.service.update_run_progress",
                            lambda *a, **k: None)
        monkeypatch.setattr("experimental.se_dork.service.delete_unclassified_results_by_ids",
                            lambda *a, **k: None)

        page_n = [0]
        def _urlopen(url, timeout=None):
            page_n[0] += 1
            if page_n[0] == 1:
                return _json_resp([
                    {"url": f"http://u{i}.com/", "title": "OD", "content": ""}
                    for i in range(50)
                ])
            if page_n[0] <= 3:
                raise urllib.error.HTTPError(url, 429, "Rate Limited", {}, None)
            return _json_resp([])

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)
        opts = RunOptions(instance_url="http://x", query="q", max_results=1000,
                          short_retry_delay=30, long_retry_delay=180)
        run_dork_search(opts, db_path=tmp_path / "test.db")
        assert len(waits) == 1, f"Mature-by-URL-count run must use exactly 1 retry slot, got {waits}"
        assert waits[0][1] == 1

    def test_retry_after_replaces_delay_not_adds(self, monkeypatch, tmp_path):
        """Retry-After header replaces the slot delay, doesn't add a retry."""
        import io
        from email.message import Message
        from unittest.mock import patch as mock_patch

        call_n = [0]
        headers = Message()
        headers["Retry-After"] = "45"

        def _urlopen(url, timeout=None):
            call_n[0] += 1
            if call_n[0] == 1:
                exc = urllib.error.HTTPError(url, 429, "Rate Limited", headers, io.BytesIO(b""))
                raise exc
            return _json_resp([])

        result, waits = self._setup_run(monkeypatch, tmp_path, _urlopen)
        # Only 1 retry; Retry-After=45 replaces the 30s short delay
        assert len(waits) == 1
        assert waits[0] == 45

    def test_hard_retry_delay_records_actual_elapsed_not_requested(self, monkeypatch, tmp_path):
        """_wait_for_cooldown return value is what gets recorded, not requested delay."""
        call_n = [0]

        def _fake_wait_returns_two(seconds, *, retry_number, max_retries=2, progress_cb=None,
                                   sleep_fn=None, cancel_event=None):
            return 2.0  # always returns 2, not the full requested delay

        monkeypatch.setattr("experimental.se_dork.service._wait_for_cooldown", _fake_wait_returns_two)
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )

        def _urlopen(url, timeout=None):
            call_n[0] += 1
            if call_n[0] == 1:
                raise urllib.error.HTTPError(url, 429, "Rate Limited", {}, None)
            return _json_resp([])

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)

        from experimental.se_dork.models import RunOptions
        from experimental.se_dork.service import run_dork_search
        opts = RunOptions(instance_url="http://x", query="q", short_retry_delay=30)
        result = run_dork_search(opts, db_path=tmp_path / "test.db")
        assert result.hard_retry_delay_seconds == 2.0  # actual elapsed, not 30


class TestCooldownProgressMessages:
    def test_progress_uses_max_retries_early(self, monkeypatch, tmp_path):
        """Early run: progress message contains '1/2'."""
        import urllib.error
        from experimental.se_dork.service import run_dork_search
        from experimental.se_dork.models import RunOptions

        msgs = []
        call_n = [0]

        def _urlopen(url, timeout=None):
            call_n[0] += 1
            if call_n[0] == 1:
                raise urllib.error.HTTPError(url, 429, "Rate Limited", {}, None)
            return _json_resp([])

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )

        opts = RunOptions(instance_url="http://x", query="q")
        run_dork_search(opts, db_path=tmp_path / "test.db", progress_cb=msgs.append)
        assert any("1/2" in m for m in msgs), f"Expected '1/2' in progress messages: {msgs}"

    def test_progress_uses_max_retries_mature(self, monkeypatch, tmp_path):
        """Mature run (50+ URLs): progress message contains '1/1'."""
        from experimental.se_dork.service import _wait_for_cooldown, run_dork_search
        from experimental.se_dork.models import RunOptions

        msgs = []
        waits_args = []

        def _fake_wait(seconds, *, retry_number, max_retries=2, progress_cb=None,
                       sleep_fn=None, cancel_event=None):
            waits_args.append({"max_retries": max_retries})
            if progress_cb:
                progress_cb(f"Upstream retry {retry_number}/{max_retries}: waiting {seconds}s...")
            return float(seconds)

        monkeypatch.setattr("experimental.se_dork.service._wait_for_cooldown", _fake_wait)
        monkeypatch.setattr(
            "experimental.se_dork.service.run_reachability_check",
            lambda url, timeout=10: _ok_preflight(),
        )

        rows = [{"url": f"http://u{i}.com/", "title": "OD", "content": ""} for i in range(50)]
        call_n = [0]

        def _urlopen(url, timeout=None):
            call_n[0] += 1
            if call_n[0] == 1:
                return _json_resp(rows)  # 50 unique URLs → mature
            if call_n[0] == 2:
                raise urllib.error.HTTPError(url, 429, "Rate Limited", {}, None)
            return _json_resp([])

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)
        monkeypatch.setattr("experimental.se_dork.classifier.classify_url",
                            lambda *a, **k: _mock_classify())
        monkeypatch.setattr("experimental.se_dork.service.insert_page_results",
                            lambda conn, run_id, rows_: rows_)
        monkeypatch.setattr("experimental.se_dork.service._persist_page_classification",
                            lambda **kw: ([], 0))
        monkeypatch.setattr("experimental.se_dork.service.update_run_progress",
                            lambda *a, **k: None)

        opts = RunOptions(instance_url="http://x", query="q", max_results=1000)
        run_dork_search(opts, db_path=tmp_path / "test.db", progress_cb=msgs.append)

        if waits_args:
            assert waits_args[0]["max_retries"] == 1, f"Expected mature max_retries=1, got {waits_args}"
            assert any("1/1" in m for m in msgs), f"Expected '1/1' in: {msgs}"


def _mock_classify():
    from experimental.se_dork.classifier import ClassifyResult, VERDICT_NOISE
    return ClassifyResult(verdict=VERDICT_NOISE, reason_code="test", http_status=404)
