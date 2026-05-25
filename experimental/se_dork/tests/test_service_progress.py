"""Tests that run_dork_search emits progress via callback at each pipeline phase."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


class _FakeResponse:
    def __init__(self, results):
        self._data = json.dumps({"results": results}).encode()
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return self._data


def _wire_store(monkeypatch):
    """Patch all store functions to no-op stubs."""
    for name in (
        "init_db", "update_run", "update_run_verified_count",
        "update_result_verdict", "delete_non_open_results", "update_result_probe",
    ):
        monkeypatch.setattr(f"experimental.se_dork.service.{name}", lambda *a, **k: None)
    monkeypatch.setattr("experimental.se_dork.service.open_connection", lambda *a: MagicMock())
    monkeypatch.setattr("experimental.se_dork.service.insert_run", lambda *a, **k: 1)
    monkeypatch.setattr("experimental.se_dork.service.insert_result", lambda *a, **k: True)
    monkeypatch.setattr("experimental.se_dork.service.count_open_index_results", lambda *a, **k: 3)
    _rows = [{"result_id": i, "url": f"http://ex{i}.com/files/"} for i in range(3)]
    monkeypatch.setattr("experimental.se_dork.service.get_pending_results", lambda *a, **k: _rows)
    monkeypatch.setattr("experimental.se_dork.service.get_results_for_run", lambda *a, **k: _rows)


def test_progress_emits_page_store_classify_probe(monkeypatch):
    """Happy path: all four progress phases emit at least one message."""
    from experimental.se_dork.service import run_dork_search
    from experimental.se_dork.models import RunOptions

    ok = MagicMock(); ok.ok = True; ok.message = "OK"
    monkeypatch.setattr("experimental.se_dork.service.run_preflight", lambda url: ok)
    _wire_store(monkeypatch)

    _calls = []
    def _fake_urlopen(url, timeout=None):
        results = (
            [{"url": f"http://ex{i}.com/", "title": "OD", "content": ""} for i in range(3)]
            if not _calls else []
        )
        _calls.append(url)
        return _FakeResponse(results)
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    fv = MagicMock(); fv.verdict = "OPEN_INDEX"; fv.reason_code = "OPEN_INDEX"; fv.http_status = 200
    monkeypatch.setattr("experimental.se_dork.classifier.classify_url", lambda *a, **k: fv)

    fo = MagicMock()
    fo.probe_status = "clean"; fo.probe_indicator_matches = 0; fo.probe_preview = None
    fo.probe_checked_at = "2026-01-01T00:00:00"; fo.probe_error = None
    fo.probe_snapshot_payload = None
    monkeypatch.setattr("experimental.se_dork.probe.probe_url", lambda *a, **k: fo)
    monkeypatch.setattr("experimental.se_dork.probe.build_indicator_patterns", lambda *a, **k: [])

    msgs = []
    run_dork_search(
        RunOptions(instance_url="http://test.local", query="test",
                   max_results=5, bulk_probe_enabled=True),
        progress_cb=lambda m: msgs.append(m),
    )

    assert any("Querying SearXNG page" in m for m in msgs), f"page missing: {msgs}"
    assert any("Storing" in m for m in msgs), f"store missing: {msgs}"
    assert any("Classifying" in m for m in msgs), f"classify missing: {msgs}"
    assert any("Probing" in m for m in msgs), f"probe missing: {msgs}"


def test_progress_emits_terminal_on_preflight_exception(monkeypatch):
    """Preflight exception must emit a terminal progress line before returning."""
    from experimental.se_dork.service import run_dork_search
    from experimental.se_dork.models import RunOptions

    def _failing_preflight(url):
        raise ConnectionError("host unreachable")
    monkeypatch.setattr("experimental.se_dork.service.run_preflight", _failing_preflight)

    msgs = []
    result = run_dork_search(
        RunOptions(instance_url="http://test.local", query="test"),
        progress_cb=lambda m: msgs.append(m),
    )

    assert result.status == "error"
    assert any("Preflight error" in m for m in msgs), (
        f"Expected terminal preflight error message in: {msgs}"
    )


def test_progress_emits_terminal_on_fetch_exception(monkeypatch):
    """Fetch failure must emit a terminal progress line before returning."""
    from experimental.se_dork.service import run_dork_search
    from experimental.se_dork.models import RunOptions

    ok = MagicMock(); ok.ok = True; ok.message = "OK"
    monkeypatch.setattr("experimental.se_dork.service.run_preflight", lambda url: ok)
    _wire_store(monkeypatch)

    def _failing_urlopen(url, timeout=None):
        raise ConnectionError("network timeout")
    monkeypatch.setattr("urllib.request.urlopen", _failing_urlopen)

    msgs = []
    result = run_dork_search(
        RunOptions(instance_url="http://test.local", query="test"),
        progress_cb=lambda m: msgs.append(m),
    )

    assert result.status == "error"
    assert any("Fetch error" in m for m in msgs), (
        f"Expected terminal fetch error message in: {msgs}"
    )
