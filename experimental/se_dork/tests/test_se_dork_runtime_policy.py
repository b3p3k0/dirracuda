"""Tests for C11A runtime policy clamping in run_dork_search."""
from __future__ import annotations

import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def _ok_preflight():
    return SimpleNamespace(ok=True, reason_code=None, message="Instance OK.")


def _searxng_response_cm(results):
    import json
    body = json.dumps({"results": results}).encode()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.read = MagicMock(return_value=body)
    return cm


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr("experimental.se_dork.service.time.sleep", lambda _: None)
    monkeypatch.setattr("experimental.se_dork.service.random.uniform", lambda lo, hi: (lo + hi) / 2)


class TestCoercePolicySeconds:
    def test_clamps_low(self):
        from experimental.se_dork.service import _coerce_policy_seconds
        assert _coerce_policy_seconds(1, default=15, lo=5, hi=60) == 5

    def test_clamps_high(self):
        from experimental.se_dork.service import _coerce_policy_seconds
        assert _coerce_policy_seconds(100, default=15, lo=5, hi=60) == 60

    def test_returns_default_on_none(self):
        from experimental.se_dork.service import _coerce_policy_seconds
        assert _coerce_policy_seconds(None, default=15, lo=5, hi=60) == 15

    def test_returns_default_on_nonnumeric(self):
        from experimental.se_dork.service import _coerce_policy_seconds
        assert _coerce_policy_seconds("abc", default=30, lo=5, hi=60) == 30

    def test_handles_float_string(self):
        from experimental.se_dork.service import _coerce_policy_seconds
        assert _coerce_policy_seconds("12.7", default=15, lo=5, hi=60) == 12

    def test_handles_overflow(self):
        from experimental.se_dork.service import _coerce_policy_seconds
        assert _coerce_policy_seconds(float("inf"), default=15, lo=5, hi=60) == 15

    def test_passes_through_valid_value(self):
        from experimental.se_dork.service import _coerce_policy_seconds
        assert _coerce_policy_seconds(20, default=15, lo=5, hi=60) == 20


class TestRequestTimeoutClamping:
    def _run_with_opts(self, opts, tmp_path, monkeypatch):
        from experimental.se_dork.service import run_dork_search
        captured = []

        def _fake_reachability(url, timeout=10):
            captured.append(("reachability", timeout))
            return _ok_preflight()

        def _fake_urlopen(url, timeout=None):
            captured.append(("urlopen", timeout))
            return _searxng_response_cm([])  # empty → stops pagination

        monkeypatch.setattr("experimental.se_dork.service.run_reachability_check", _fake_reachability)
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        run_dork_search(opts, db_path=tmp_path / "test.db")
        return captured

    def test_request_timeout_clamped_low(self, monkeypatch, tmp_path):
        from experimental.se_dork.models import RunOptions
        opts = RunOptions(instance_url="http://x", query="q", request_timeout=2)
        captured = self._run_with_opts(opts, tmp_path, monkeypatch)
        reach = [c for c in captured if c[0] == "reachability"]
        assert reach and reach[0][1] == 5

    def test_request_timeout_clamped_high(self, monkeypatch, tmp_path):
        from experimental.se_dork.models import RunOptions
        opts = RunOptions(instance_url="http://x", query="q", request_timeout=90)
        captured = self._run_with_opts(opts, tmp_path, monkeypatch)
        reach = [c for c in captured if c[0] == "reachability"]
        assert reach and reach[0][1] == 60

    def test_request_timeout_passed_to_reachability(self, monkeypatch, tmp_path):
        from experimental.se_dork.models import RunOptions
        opts = RunOptions(instance_url="http://x", query="q", request_timeout=20)
        captured = self._run_with_opts(opts, tmp_path, monkeypatch)
        reach = [c for c in captured if c[0] == "reachability"]
        assert reach and reach[0][1] == 20

    def test_defaults_when_not_provided(self, monkeypatch, tmp_path):
        from experimental.se_dork.models import RunOptions
        opts = RunOptions(instance_url="http://x", query="q")
        captured = self._run_with_opts(opts, tmp_path, monkeypatch)
        reach = [c for c in captured if c[0] == "reachability"]
        assert reach and reach[0][1] == 15


class TestRetryClamping:
    def test_short_retry_clamped_low(self, monkeypatch, tmp_path):
        """short_retry_delay=1 → clamped to 5; cooldown wait receives 5."""
        import json
        from experimental.se_dork.models import RunOptions
        from experimental.se_dork.service import run_dork_search

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

        call_n = [0]
        def _fake_urlopen(url, timeout=None):
            call_n[0] += 1
            if call_n[0] == 1:
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
            body = json.dumps({"results": []}).encode()
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cm.read = MagicMock(return_value=body)
            return cm

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        opts = RunOptions(instance_url="http://x", query="q", short_retry_delay=1)
        run_dork_search(opts, db_path=tmp_path / "test.db")
        assert waits and waits[0] == 5

    def test_long_retry_clamped_high(self, monkeypatch, tmp_path):
        """long_retry_delay=500 → clamped to 300."""
        import json
        from experimental.se_dork.models import RunOptions
        from experimental.se_dork.service import run_dork_search

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

        call_n = [0]
        def _fake_urlopen(url, timeout=None):
            call_n[0] += 1
            if call_n[0] <= 2:
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
            body = json.dumps({"results": []}).encode()
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cm.read = MagicMock(return_value=body)
            return cm

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        opts = RunOptions(instance_url="http://x", query="q", long_retry_delay=500)
        run_dork_search(opts, db_path=tmp_path / "test.db")
        # Second retry should use long_retry clamped to 300
        assert len(waits) >= 2 and waits[1] == 300
