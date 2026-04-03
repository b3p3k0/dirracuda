"""
Tests for HTTP operation stage behavior.

Focus:
- Stage-2 verification must stay locked to Shodan-reported candidate port.
- No implicit 80/443 fallback attempts are allowed.
"""
from __future__ import annotations

from typing import List, Tuple
from unittest.mock import MagicMock, patch

from commands.http.models import HttpCandidate
from commands.http.operation import run_access_stage, run_discover_stage


def _make_candidate(ip: str, port: int) -> HttpCandidate:
    return HttpCandidate(
        ip=ip,
        port=port,
        scheme="http",
        banner="server: test",
        title="Index of /",
        country="United States",
        country_code="US",
        shodan_data={"port": port},
    )


def _make_workflow(*, verify_http: bool = True, verify_https: bool = True):
    out = MagicMock()
    cfg = MagicMock()
    cfg.get_http_config.return_value = {
        "verification": {
            "request_timeout": 10,
            "subdir_timeout": 8,
            "allow_insecure_tls": True,
            "verify_http": verify_http,
            "verify_https": verify_https,
        }
    }
    cfg.get_max_concurrent_http_access_hosts.return_value = 4
    cfg.get_max_concurrent_http_discovery_hosts.return_value = 4
    cfg.get.return_value = 30
    wf = MagicMock()
    wf.output = out
    wf.config = cfg
    wf.db_path = ":memory:"
    return wf


def test_discover_stage_filters_by_endpoint_subset():
    wf = _make_workflow()
    candidates = [
        _make_candidate("198.51.100.10", 8080),
        _make_candidate("198.51.100.10", 8443),
        _make_candidate("198.51.100.11", 8080),
    ]
    filtered = [candidates[1], candidates[2]]

    with (
        patch("commands.http.operation.query_http_shodan", return_value=candidates),
        patch("commands.http.operation.port_check", return_value=(True, "ok")) as port_check_mock,
        patch("commands.http.operation.HttpPersistence") as persist_cls,
    ):
        persist_cls.return_value.filter_recent_candidates.return_value = (
            filtered,
            {
                "total": 3,
                "new": 1,
                "known": 2,
                "skipped_recent": 1,
                "retried_recent_failures": 1,
                "old_enough": 0,
                "to_scan": 2,
            },
        )
        reachable, total = run_discover_stage(wf)

    assert total == 2
    assert [(c.ip, c.port) for c in reachable] == [(c.ip, c.port) for c in filtered]
    tested_endpoints = [(call.args[0], int(call.args[1])) for call in port_check_mock.call_args_list]
    assert tested_endpoints == [(c.ip, c.port) for c in filtered]


def test_discover_stage_skips_when_all_recent_successes():
    wf = _make_workflow()
    candidates = [
        _make_candidate("203.0.113.1", 8080),
        _make_candidate("203.0.113.2", 8080),
    ]

    with (
        patch("commands.http.operation.query_http_shodan", return_value=candidates),
        patch("commands.http.operation.port_check") as port_check_mock,
        patch("commands.http.operation.HttpPersistence") as persist_cls,
    ):
        persist_cls.return_value.filter_recent_candidates.return_value = (
            [],
            {
                "total": 2,
                "new": 0,
                "known": 2,
                "skipped_recent": 2,
                "retried_recent_failures": 0,
                "old_enough": 0,
                "to_scan": 0,
            },
        )
        reachable, total = run_discover_stage(wf)

    assert reachable == []
    assert total == 0
    port_check_mock.assert_not_called()


def test_discover_stage_retries_recent_failure_endpoint():
    wf = _make_workflow()
    candidates = [
        _make_candidate("203.0.113.25", 8080),
        _make_candidate("203.0.113.25", 8443),
    ]
    filtered = [candidates[1]]

    with (
        patch("commands.http.operation.query_http_shodan", return_value=candidates),
        patch("commands.http.operation.port_check", return_value=(True, "ok")) as port_check_mock,
        patch("commands.http.operation.HttpPersistence") as persist_cls,
    ):
        persist_cls.return_value.filter_recent_candidates.return_value = (
            filtered,
            {
                "total": 2,
                "new": 0,
                "known": 2,
                "skipped_recent": 1,
                "retried_recent_failures": 1,
                "old_enough": 0,
                "to_scan": 1,
            },
        )
        reachable, total = run_discover_stage(wf)

    assert total == 1
    assert [(c.ip, c.port) for c in reachable] == [("203.0.113.25", 8443)]
    tested_endpoints = [(call.args[0], int(call.args[1])) for call in port_check_mock.call_args_list]
    assert tested_endpoints == [("203.0.113.25", 8443)]


def test_access_stage_attempts_only_candidate_port_no_canonical_fallback():
    wf = _make_workflow(verify_http=True, verify_https=True)
    candidates = [_make_candidate("203.0.113.10", 8080)]
    calls: List[Tuple[str, int, str]] = []

    def _try_request(ip, port, scheme, *_args, **_kwargs):
        calls.append((ip, int(port), str(scheme)))
        return 404, "", False, ""

    with (
        patch("commands.http.operation.try_http_request", side_effect=_try_request),
        patch("commands.http.operation.validate_index_page", return_value=False),
        patch("commands.http.operation.HttpPersistence") as persist_cls,
    ):
        accessible = run_access_stage(wf, candidates)

    assert accessible == 0
    # Must only hit candidate endpoint; no 80/443 supplemental attempts.
    assert calls == [
        ("203.0.113.10", 8080, "http"),
        ("203.0.113.10", 8080, "https"),
    ]
    persist_cls.return_value.persist_access_outcomes_batch.assert_called_once()


def test_access_stage_single_protocol_still_uses_candidate_port():
    wf = _make_workflow(verify_http=False, verify_https=True)
    candidates = [_make_candidate("203.0.113.11", 8443)]
    calls: List[Tuple[str, int, str]] = []

    def _try_request(ip, port, scheme, *_args, **_kwargs):
        calls.append((ip, int(port), str(scheme)))
        body = "<html><title>Index of /</title><a href=\"pub/\">pub/</a></html>"
        return 200, body, False, ""

    with (
        patch("commands.http.operation.try_http_request", side_effect=_try_request),
        patch("commands.http.operation.validate_index_page", return_value=True),
        patch("commands.http.operation.count_dir_entries", return_value=(1, 0, [])),
        patch("commands.http.operation.HttpPersistence"),
    ):
        accessible = run_access_stage(wf, candidates)

    assert accessible == 1
    assert calls == [("203.0.113.11", 8443, "https")]
