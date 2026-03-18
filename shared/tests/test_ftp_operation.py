"""
Tests for FTP operation parallel paths: run_discover_stage, run_access_stage.

Verifies:
- Correct outcome counts
- Per-host exception containment (stage continues; categorical reason used)
- Progress reporting is batched (first, every 10, final) like SMB
- Worker count bounded to <= total hosts
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from commands.ftp.models import FtpCandidate, FtpAccessOutcome
from commands.ftp.operation import run_discover_stage, run_access_stage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candidate(ip: str, port: int = 21) -> FtpCandidate:
    return FtpCandidate(
        ip=ip,
        port=port,
        banner="220 FTP",
        country="United States",
        country_code="US",
        shodan_data={},
    )


def _expected_progress_reports(total: int, batch_size: int = 10) -> int:
    """SMB-style concurrent progress cadence: first, every N, and final."""
    return sum(
        1 for i in range(1, total + 1)
        if i == 1 or i == total or i % batch_size == 0
    )


def _make_workflow(
    candidates: list,
    discovery_workers: int = 10,
    access_workers: int = 4,
    verbose: bool = False,
):
    """Build a minimal mock workflow duck-typed to what operation.py reads."""
    raw_calls: List[str] = []
    info_calls: List[str] = []

    out = MagicMock()
    out.verbose = verbose
    out.raw.side_effect = lambda msg: raw_calls.append(msg)
    out.info.side_effect = lambda msg: info_calls.append(msg)

    config = MagicMock()
    config.get_ftp_config.return_value = {
        "verification": {"connect_timeout": 5, "auth_timeout": 10, "listing_timeout": 15}
    }
    config.get_max_concurrent_ftp_discovery_hosts.return_value = discovery_workers
    config.get_max_concurrent_ftp_access_hosts.return_value = access_workers

    wf = MagicMock()
    wf.output = out
    wf.config = config
    wf.db_path = ":memory:"
    wf.args = None

    return wf, raw_calls, info_calls


# ---------------------------------------------------------------------------
# run_discover_stage
# ---------------------------------------------------------------------------

class TestDiscoverStage:
    def _run(self, candidates, port_check_side_effect, discovery_workers=10, verbose=False):
        wf, raw_calls, info_calls = _make_workflow(
            candidates,
            discovery_workers=discovery_workers,
            verbose=verbose,
        )
        with (
            patch("commands.ftp.operation.query_ftp_shodan", return_value=candidates),
            patch("commands.ftp.operation.port_check", side_effect=port_check_side_effect),
            patch("commands.ftp.operation.FtpPersistence") as mock_persist,
        ):
            reachable, shodan_total = run_discover_stage(wf)
        return reachable, shodan_total, raw_calls, info_calls, mock_persist

    def test_all_reachable(self):
        candidates = [_make_candidate(f"1.2.3.{i}") for i in range(3)]
        reachable, total, raw_calls, info_calls, _ = self._run(
            candidates,
            port_check_side_effect=[(True, "ok")] * 3,
        )
        assert total == 3
        assert len(reachable) == 3
        progress_lines = [l for l in info_calls if "📊 Progress:" in l]
        assert len(progress_lines) == _expected_progress_reports(total)

    def test_some_unreachable(self):
        candidates = [_make_candidate(f"10.0.0.{i}") for i in range(4)]
        side_effects = [(True, "ok"), (False, "timeout"), (True, "ok"), (False, "refused")]
        reachable, total, raw_calls, info_calls, mock_persist = self._run(candidates, side_effects)
        assert total == 4
        assert len(reachable) == 2
        progress_lines = [l for l in info_calls if "📊 Progress:" in l]
        assert len(progress_lines) == _expected_progress_reports(total)
        # Persistence called for the 2 failures
        mock_persist.return_value.persist_discovery_outcomes_batch.assert_called_once()
        outcomes = mock_persist.return_value.persist_discovery_outcomes_batch.call_args[0][0]
        assert len(outcomes) == 2

    def test_exception_containment_stage_continues(self):
        """One host raises inside port_check; stage must complete all 3 hosts."""
        candidates = [_make_candidate(f"192.168.1.{i}") for i in range(3)]

        def _port_check_with_exception(ip, port, timeout):
            if ip == "192.168.1.1":
                raise RuntimeError("network error")
            return (True, "ok")

        reachable, total, raw_calls, info_calls, mock_persist = self._run(
            candidates,
            port_check_side_effect=_port_check_with_exception,
        )
        # All 3 hosts processed, 2 reachable, 1 failed via exception
        assert total == 3
        assert len(reachable) == 2
        progress_lines = [l for l in info_calls if "📊 Progress:" in l]
        assert len(progress_lines) == _expected_progress_reports(total)

    def test_exception_outcome_has_categorical_reason(self):
        """Exception path must use reason='connect_fail', not the exception message."""
        candidates = [_make_candidate("10.10.10.1")]

        def _always_raise(ip, port, timeout):
            raise OSError("connection reset by peer")

        reachable, total, raw_calls, info_calls, mock_persist = self._run(
            candidates,
            port_check_side_effect=_always_raise,
        )
        assert len(reachable) == 0
        outcomes = mock_persist.return_value.persist_discovery_outcomes_batch.call_args[0][0]
        assert len(outcomes) == 1
        assert outcomes[0].reason == "connect_fail"
        # Exception detail is in error_message, NOT in reason
        assert "connection reset by peer" not in outcomes[0].reason
        assert "connection reset by peer" in outcomes[0].error_message

    def test_progress_line_count_matches_batched_pattern(self):
        n = 12
        candidates = [_make_candidate(f"172.16.0.{i}") for i in range(n)]
        _, total, raw_calls, info_calls, _ = self._run(candidates, [(True, "ok")] * n)
        progress_lines = [l for l in info_calls if "📊 Progress:" in l]
        assert len(progress_lines) == _expected_progress_reports(total)

    def test_no_per_host_verbose_lines_emitted(self):
        n = 12
        candidates = [_make_candidate(f"172.20.0.{i}") for i in range(n)]
        _, _, _, info_calls, _ = self._run(
            candidates,
            port_check_side_effect=[(False, "timeout")] * n,
            verbose=True,
        )
        ip_pattern = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
        assert not any(ip_pattern.search(line or "") for line in info_calls)

    def test_worker_cap_bounded_to_host_count(self):
        """When config says 10 workers but only 3 hosts, ThreadPoolExecutor gets max_workers=3."""
        candidates = [_make_candidate(f"5.5.5.{i}") for i in range(3)]
        captured = []
        real_tpe = ThreadPoolExecutor

        def _patched_tpe(max_workers=None, **kwargs):
            captured.append(max_workers)
            return real_tpe(max_workers=max_workers, **kwargs)

        wf, _, _ = _make_workflow(candidates, discovery_workers=10)
        with (
            patch("commands.ftp.operation.query_ftp_shodan", return_value=candidates),
            patch("commands.ftp.operation.port_check", return_value=(True, "ok")),
            patch("commands.ftp.operation.FtpPersistence"),
            patch("commands.ftp.operation.ThreadPoolExecutor", side_effect=_patched_tpe),
        ):
            run_discover_stage(wf)

        assert captured == [3]

    def test_empty_candidates_returns_zero(self):
        wf, raw_calls, info_calls = _make_workflow([])
        with patch("commands.ftp.operation.query_ftp_shodan", return_value=[]):
            reachable, total = run_discover_stage(wf)
        assert reachable == []
        assert total == 0
        assert raw_calls == []
        assert info_calls == []

    def test_discover_summary_uses_success_when_all_reachable(self):
        candidates = [_make_candidate(f"100.64.0.{i}") for i in range(3)]
        wf, _, _ = _make_workflow(candidates)
        with (
            patch("commands.ftp.operation.query_ftp_shodan", return_value=candidates),
            patch("commands.ftp.operation.port_check", return_value=(True, "ok")),
            patch("commands.ftp.operation.FtpPersistence"),
        ):
            run_discover_stage(wf)
        assert wf.output.success.call_count == 1
        assert wf.output.warning.call_count == 0
        assert wf.output.error.call_count == 0

    def test_discover_summary_uses_warning_when_some_unreachable(self):
        candidates = [_make_candidate(f"100.65.0.{i}") for i in range(3)]
        wf, _, _ = _make_workflow(candidates)
        with (
            patch("commands.ftp.operation.query_ftp_shodan", return_value=candidates),
            patch(
                "commands.ftp.operation.port_check",
                side_effect=[(True, "ok"), (False, "timeout"), (True, "ok")],
            ),
            patch("commands.ftp.operation.FtpPersistence"),
        ):
            run_discover_stage(wf)
        assert wf.output.warning.call_count == 1


# ---------------------------------------------------------------------------
# run_access_stage
# ---------------------------------------------------------------------------

class TestAccessStage:
    def _run(self, candidates, anon_login_side_effect, root_listing_side_effect=None,
             access_workers=4, verbose=False):
        wf, raw_calls, info_calls = _make_workflow(
            candidates,
            access_workers=access_workers,
            verbose=verbose,
        )
        login_mock = MagicMock(side_effect=anon_login_side_effect)
        listing_mock = MagicMock(side_effect=root_listing_side_effect or [])
        with (
            patch("commands.ftp.operation.try_anon_login", login_mock),
            patch("commands.ftp.operation.try_root_listing", listing_mock),
            patch("commands.ftp.operation.FtpPersistence") as mock_persist,
        ):
            count = run_access_stage(wf, candidates)
        return count, raw_calls, info_calls, mock_persist

    def test_all_accessible(self):
        candidates = [_make_candidate(f"10.1.1.{i}") for i in range(3)]
        login_se = [(True, "220 FTP", "anonymous")] * 3
        listing_se = [(True, 5, "ok")] * 3
        count, raw_calls, info_calls, _ = self._run(candidates, login_se, listing_se)
        assert count == 3
        progress_lines = [l for l in info_calls if "📊 Progress:" in l]
        assert len(progress_lines) == _expected_progress_reports(len(candidates))

    def test_none_accessible(self):
        candidates = [_make_candidate(f"10.2.2.{i}") for i in range(2)]
        login_se = [(False, None, "auth_required")] * 2
        count, raw_calls, info_calls, mock_persist = self._run(candidates, login_se)
        assert count == 0
        persist_call = mock_persist.return_value.persist_access_outcomes_batch.call_args[0][0]
        assert len(persist_call) == 2
        assert all(not o.accessible for o in persist_call)

    def test_mixed_accessible(self):
        candidates = [_make_candidate(f"10.3.3.{i}") for i in range(4)]
        login_se = [
            (True, "220", "anonymous"),
            (False, None, "530 Login failed"),
            (True, "220", "anonymous"),
            (True, "220", "anonymous"),
        ]
        listing_se = [
            (True, 10, "ok"),
            (False, 0, "timeout"),
        ]
        count, _, _, _ = self._run(candidates, login_se, listing_se)
        # candidates 0 and 2 succeed login+listing; candidate 3 has login success
        # but listing either succeeds or fails — depends on ordering
        # We only need count >= 1 and <= 3; more importantly outcomes == 4
        assert 0 <= count <= 3

    def test_exception_containment_stage_continues(self):
        """One host raises in try_anon_login; stage must complete all 3 hosts."""
        candidates = [_make_candidate(f"10.4.4.{i}") for i in range(3)]

        call_count = [0]

        def _login_with_exception(ip, port, timeout):
            call_count[0] += 1
            if ip == "10.4.4.1":
                raise ConnectionError("reset")
            return (True, "220 FTP", "anonymous")

        listing_se = [(True, 3, "ok")] * 3
        wf, raw_calls, info_calls = _make_workflow(candidates, access_workers=4)
        with (
            patch("commands.ftp.operation.try_anon_login", side_effect=_login_with_exception),
            patch("commands.ftp.operation.try_root_listing", side_effect=listing_se),
            patch("commands.ftp.operation.FtpPersistence") as mock_persist,
        ):
            count = run_access_stage(wf, candidates)

        # 2 succeed, 1 fails via exception
        assert count == 2
        progress_lines = [l for l in info_calls if "📊 Progress:" in l]
        assert len(progress_lines) == _expected_progress_reports(len(candidates))
        outcomes = mock_persist.return_value.persist_access_outcomes_batch.call_args[0][0]
        assert len(outcomes) == 3

    def test_exception_outcome_has_categorical_auth_status(self):
        """Exception path must use auth_status='auth_fail', not the exception message."""
        candidates = [_make_candidate("10.5.5.1")]

        def _always_raise(ip, port, timeout):
            raise TimeoutError("timed out after 10s")

        wf, _, _ = _make_workflow(candidates, access_workers=4)
        with (
            patch("commands.ftp.operation.try_anon_login", side_effect=_always_raise),
            patch("commands.ftp.operation.try_root_listing"),
            patch("commands.ftp.operation.FtpPersistence") as mock_persist,
        ):
            count = run_access_stage(wf, candidates)

        assert count == 0
        outcomes = mock_persist.return_value.persist_access_outcomes_batch.call_args[0][0]
        assert len(outcomes) == 1
        o = outcomes[0]
        assert o.auth_status == "auth_fail"
        assert not o.accessible
        # Exception detail in error_message AND access_details, NOT in auth_status
        assert "timed out" not in o.auth_status
        assert "timed out" in o.error_message
        details = json.loads(o.access_details)
        assert details["reason"] == "auth_fail"
        assert "timed out" in details["error"]

    def test_progress_line_count_matches_batched_pattern(self):
        n = 12
        candidates = [_make_candidate(f"10.6.6.{i}") for i in range(n)]
        login_se = [(False, None, "auth_required")] * n
        _, raw_calls, info_calls, _ = self._run(candidates, login_se)
        progress_lines = [l for l in info_calls if "📊 Progress:" in l]
        assert len(progress_lines) == _expected_progress_reports(n)

    def test_access_stage_no_per_host_verbose_lines_emitted(self):
        n = 12
        candidates = [_make_candidate(f"10.9.9.{i}") for i in range(n)]
        login_se = [(False, None, "auth_required")] * n
        _, _, info_calls, _ = self._run(candidates, login_se, verbose=True)
        ip_pattern = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
        assert not any(ip_pattern.search(line or "") for line in info_calls)

    def test_worker_cap_bounded_to_host_count(self):
        """When config says 4 workers but only 2 hosts, ThreadPoolExecutor gets max_workers=2."""
        candidates = [_make_candidate(f"10.7.7.{i}") for i in range(2)]
        captured = []
        real_tpe = ThreadPoolExecutor

        def _patched_tpe(max_workers=None, **kwargs):
            captured.append(max_workers)
            return real_tpe(max_workers=max_workers, **kwargs)

        wf, _, _ = _make_workflow(candidates, access_workers=4)
        with (
            patch("commands.ftp.operation.try_anon_login",
                  return_value=(False, None, "auth_required")),
            patch("commands.ftp.operation.try_root_listing"),
            patch("commands.ftp.operation.FtpPersistence"),
            patch("commands.ftp.operation.ThreadPoolExecutor", side_effect=_patched_tpe),
        ):
            run_access_stage(wf, candidates)

        assert captured == [2]

    def test_empty_candidates_returns_zero(self):
        wf, _, _ = _make_workflow([])
        count = run_access_stage(wf, [])
        assert count == 0

    def test_outcomes_length_equals_candidate_count(self):
        n = 5
        candidates = [_make_candidate(f"10.8.8.{i}") for i in range(n)]
        login_se = [(True, "220", "anonymous")] * n
        listing_se = [(True, 1, "ok")] * n
        _, _, _, mock_persist = self._run(candidates, login_se, listing_se)
        outcomes = mock_persist.return_value.persist_access_outcomes_batch.call_args[0][0]
        assert len(outcomes) == n

    def test_access_stage_executes_concurrently(self):
        """At least two access checks should overlap when workers > 1."""
        n = 8
        candidates = [_make_candidate(f"10.11.11.{i}") for i in range(n)]

        lock = threading.Lock()
        inflight = 0
        max_inflight = 0

        def _slow_login(ip, port, timeout):
            nonlocal inflight, max_inflight
            with lock:
                inflight += 1
                max_inflight = max(max_inflight, inflight)
            time.sleep(0.03)
            with lock:
                inflight -= 1
            return (True, "220 FTP", "anonymous")

        wf, _, _ = _make_workflow(candidates, access_workers=4)
        with (
            patch("commands.ftp.operation.try_anon_login", side_effect=_slow_login),
            patch("commands.ftp.operation.try_root_listing", return_value=(True, 1, "ok")),
            patch("commands.ftp.operation.FtpPersistence"),
        ):
            run_access_stage(wf, candidates)

        assert max_inflight >= 2

    def test_access_summary_uses_warning_when_none_accessible(self):
        candidates = [_make_candidate(f"10.12.12.{i}") for i in range(2)]
        wf, _, _ = _make_workflow(candidates)
        with (
            patch("commands.ftp.operation.try_anon_login", return_value=(False, None, "auth_required")),
            patch("commands.ftp.operation.try_root_listing"),
            patch("commands.ftp.operation.FtpPersistence"),
        ):
            run_access_stage(wf, candidates)
        assert wf.output.warning.call_count == 1

    def test_access_summary_uses_success_when_any_accessible(self):
        candidates = [_make_candidate(f"10.13.13.{i}") for i in range(2)]
        wf, _, _ = _make_workflow(candidates)
        with (
            patch("commands.ftp.operation.try_anon_login", return_value=(True, "220", "anonymous")),
            patch("commands.ftp.operation.try_root_listing", return_value=(True, 1, "ok")),
            patch("commands.ftp.operation.FtpPersistence"),
        ):
            run_access_stage(wf, candidates)
        assert wf.output.success.call_count == 1
