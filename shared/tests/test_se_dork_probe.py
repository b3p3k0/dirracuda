"""Tests for SearXNG sidecar probe adapter."""

from __future__ import annotations

from unittest.mock import patch

from experimental.se_dork.probe import PROBE_STATUS_CLEAN, PROBE_STATUS_UNPROBED, probe_url


def test_probe_url_keeps_full_snapshot_for_cacheable_result():
    snapshot = {
        "run_at": "2026-05-03T12:00:00",
        "limits": {"max_directories": 3, "max_files": 5, "timeout_seconds": 10},
        "shares": [
            {
                "share": "http_root",
                "directories": [{"name": "pub", "files": ["index.html"]}],
            }
        ],
        "errors": [],
    }

    with patch("experimental.se_dork.probe.dispatch_probe_run", return_value=snapshot), \
         patch("experimental.se_dork.probe.probe_patterns.attach_indicator_analysis", return_value={"matches": []}):
        result = probe_url("http://example.local/files/", indicator_patterns=[])

    assert result.probe_status == PROBE_STATUS_CLEAN
    assert result.probe_preview == "pub"
    assert result.probe_snapshot_payload is snapshot


def test_probe_url_unprobed_has_no_snapshot_payload():
    result = probe_url("mailto:test@example.local")

    assert result.probe_status == PROBE_STATUS_UNPROBED
    assert result.probe_snapshot_payload is None
