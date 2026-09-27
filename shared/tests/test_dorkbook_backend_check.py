"""The optional live-check CLI must report upstream failures truthfully.

Every network entry point is monkeypatched; these tests never contact an
aggregator or follow a result URL.
"""

from email.message import Message
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from scripts import check_dorkbook_backends as checker


@pytest.fixture
def run_check(monkeypatch, tmp_path):
    output = tmp_path / "report.json"
    calls = []
    monkeypatch.setattr(checker.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(checker, "DEFAULT_BUILTIN_DORKS", [SimpleNamespace(
        provider="self_hosted", topic="General", builtin_key="test_default",
        query='intitle:"Index of /"',
    )])
    monkeypatch.setattr(checker.sys, "argv", [
        "check_dorkbook_backends.py", "--instance-url", "https://owned.invalid",
        "--confirm-live", "--output", str(output),
    ])

    def run(payload, *, degoog=False, error=None):
        endpoint = "https://owned.invalid" + ("/api/search" if degoog else "/search")
        monkeypatch.setattr(checker, "run_reachability_check", lambda *_args: SimpleNamespace(
            ok=True, search_endpoint=endpoint,
        ))

        def urlopen(url, **_kwargs):
            calls.append(url)
            if error:
                raise error
            response = io.BytesIO(json.dumps(payload).encode())
            response.status = 200
            return response

        monkeypatch.setattr(checker.urllib.request, "urlopen", urlopen)
        result = checker.main()
        report = json.loads(output.read_text())
        return result, report, calls

    return run


def test_healthy_empty_results_are_not_a_yield_failure(run_check):
    result, report, calls = run_check({"results": [], "unresponsive_engines": []})
    assert result == 0
    assert [check["status"] for check in report["checks"]] == ["PASS", "PASS"]
    assert [check["page"] for check in report["checks"]] == [1, 2]
    assert len(calls) == 2


def test_degoog_normalization_records_engines_without_target_urls(run_check):
    payload = {"results": [{
        "title": "Index of /", "url": "https://target.invalid/private-path/",
        "source": "Brave", "sources": ["Brave", "DuckDuckGo"],
    }], "engineTimings": [{"name": "Brave", "status": "ok"}]}
    result, report, calls = run_check(payload, degoog=True)
    assert result == 0
    assert report["checks"][0]["engines"] == ["Brave", "DuckDuckGo"]
    assert "target.invalid" not in json.dumps(report)
    assert all(url.startswith("https://owned.invalid/api/search?") for url in calls)


@pytest.mark.parametrize("degoog,payload", [
    (False, {"results": [], "unresponsive_engines": [["bing", "timeout"]]}),
    (True, {"results": [], "engineTimings": [
        {"name": "Brave", "status": "error", "error": "upstream timeout"},
    ]}),
    (True, {"results": [], "errors": ["upstream unavailable"]}),
])
def test_engine_errors_cannot_be_reported_as_success(run_check, degoog, payload):
    result, report, _calls = run_check(payload, degoog=degoog)
    assert result == 1
    assert report["checks"][0]["status"] != "PASS"
    assert report["checks"][0]["engine_errors"]


@pytest.mark.parametrize("degoog,payload", [
    (False, {"results": [], "unresponsive_engines": [["bing", "too many requests"]]}),
    (True, {"results": [], "engineTimings": [
        {"name": "Brave", "status": "error", "error": "HTTP 429"},
    ]}),
    (True, {"results": [], "errors": ["HTTP 429"]}),
    (True, {"results": [], "unresponsive_engines": [["bing", "timeout"]],
            "errors": ["HTTP 429"]}),
])
def test_upstream_limits_stop_before_next_query_or_page(run_check, degoog, payload):
    result, report, calls = run_check(payload, degoog=degoog)
    assert result == 1
    assert report["checks"][0]["status"] == "UPSTREAM_LIMITED"
    assert len(calls) == len(report["checks"]) == 1


def test_http_limit_records_retry_after_and_stops(run_check):
    headers = Message()
    headers["Retry-After"] = "120"
    error = HTTPError("https://owned.invalid/search", 429, "Too many requests", headers, None)
    result, report, calls = run_check(None, error=error)
    assert result == 1
    assert len(calls) == 1
    assert report["checks"][0]["retry_after"] == "120"
    assert report["checks"][0]["http_status"] == 429


@pytest.mark.parametrize("degoog,payload", [
    (False, []),
    (False, {"results": None}),
    (True, {"results": [], "engineTimings": "invalid"}),
])
def test_malformed_payload_is_recorded_instead_of_crashing(run_check, degoog, payload):
    result, report, _calls = run_check(payload, degoog=degoog)
    assert result == 1
    assert report["checks"][0]["status"] == "ERROR"


@pytest.mark.parametrize("degoog,payload", [
    (False, {"results": [{"url": "https://target.invalid", "engines": None}]}),
    (True, {"results": [], "engineTimings": None}),
])
def test_optional_null_metadata_does_not_crash(run_check, degoog, payload):
    result, report, _calls = run_check(payload, degoog=degoog)
    assert result == 0
    assert report["checks"][0]["status"] == "PASS"
    assert report["checks"][0]["engine_errors"] == []


def test_cli_confirmation_is_required_before_network_access(monkeypatch, tmp_path):
    monkeypatch.setattr(checker.sys, "argv", [
        "check_dorkbook_backends.py", "--instance-url", "https://owned.invalid",
        "--output", str(tmp_path / "report.json"),
    ])
    monkeypatch.setattr(checker, "run_reachability_check",
                        lambda *_args: pytest.fail("must not contact an aggregator"))
    with pytest.raises(SystemExit) as exc:
        checker.main()
    assert exc.value.code == 2
