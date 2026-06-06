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


@pytest.fixture(autouse=True)
def _skip_real_pacing_waits(monkeypatch):
    monkeypatch.setattr("experimental.se_dork.service.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "experimental.se_dork.service.random.uniform",
        lambda low, high: (low + high) / 2,
    )


def test_progress_emits_page_store_classify_probe(monkeypatch, tmp_path):
    """Happy path: all four progress phases emit at least one message."""
    from experimental.se_dork.service import run_dork_search
    from experimental.se_dork.models import RunOptions

    ok = MagicMock(); ok.ok = True; ok.message = "OK"
    monkeypatch.setattr("experimental.se_dork.service.run_reachability_check", lambda url: ok)

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
        db_path=tmp_path / "se_dork.db",
        progress_cb=lambda m: msgs.append(m),
    )

    assert any("Querying SearXNG page" in m for m in msgs), f"page missing: {msgs}"
    assert any("stored" in m for m in msgs), f"store missing: {msgs}"
    assert any("classifying" in m for m in msgs), f"classify missing: {msgs}"
    assert any("probing" in m for m in msgs), f"probe missing: {msgs}"


def test_progress_emits_terminal_on_reachability_exception(monkeypatch):
    """Reachability exception must emit a terminal progress line before returning."""
    from experimental.se_dork.service import run_dork_search
    from experimental.se_dork.models import RunOptions

    def _failing_preflight(url):
        raise ConnectionError("host unreachable")
    monkeypatch.setattr("experimental.se_dork.service.run_reachability_check", _failing_preflight)

    msgs = []
    result = run_dork_search(
        RunOptions(instance_url="http://test.local", query="test"),
        progress_cb=lambda m: msgs.append(m),
    )

    assert result.status == "error"
    assert any("Reachability error" in m for m in msgs), (
        f"Expected terminal reachability error message in: {msgs}"
    )


def test_progress_emits_terminal_on_fetch_exception(monkeypatch, tmp_path):
    """Fetch failure must emit a terminal progress line before returning."""
    from experimental.se_dork.service import run_dork_search
    from experimental.se_dork.models import RunOptions

    ok = MagicMock(); ok.ok = True; ok.message = "OK"
    monkeypatch.setattr("experimental.se_dork.service.run_reachability_check", lambda url: ok)

    def _failing_urlopen(url, timeout=None):
        raise ConnectionError("network timeout")
    monkeypatch.setattr("urllib.request.urlopen", _failing_urlopen)

    msgs = []
    result = run_dork_search(
        RunOptions(instance_url="http://test.local", query="test"),
        db_path=tmp_path / "se_dork.db",
        progress_cb=lambda m: msgs.append(m),
    )

    assert result.status == "error"
    assert any("Fetch error" in m for m in msgs), (
        f"Expected terminal fetch error message in: {msgs}"
    )
