"""C3 — Web UI post-scan probe resolves HTTP TLS once and threads it to every target."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from experimental.webui import post_scan_probe


def test_post_scan_probe_resolves_tls_once_and_threads_to_targets(monkeypatch, tmp_path):
    db_file = tmp_path / "dirracuda.db"
    db_file.write_text("", encoding="utf-8")

    calls = {"resolve": 0, "tls_seen": []}

    def _fake_resolve(config_path=None):
        calls["resolve"] += 1
        return False

    def _fake_target_probe(*, allow_insecure_tls=None, **kwargs):
        calls["tls_seen"].append(allow_insecure_tls)

    monkeypatch.setattr(post_scan_probe, "resolve_http_allow_insecure_tls", _fake_resolve)
    monkeypatch.setattr(post_scan_probe, "_run_target_probe", _fake_target_probe)
    monkeypatch.setattr(post_scan_probe, "_resolve_db_path", lambda cfg, db: db_file)
    monkeypatch.setattr(post_scan_probe, "DatabaseReader", lambda *_a, **_k: object())
    monkeypatch.setattr(
        post_scan_probe.probe_patterns, "load_ransomware_indicators", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        post_scan_probe.probe_patterns, "compile_indicator_patterns", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        post_scan_probe,
        "_cohort_targets",
        lambda *a, **k: [
            {"ip_address": "203.0.113.1"},
            {"ip_address": "203.0.113.2"},
            {"ip_address": "203.0.113.3"},
        ],
    )

    result = post_scan_probe.run_post_scan_probe(
        protocol="http",
        config_path=tmp_path / "config.json",
        scan_start_iso="2026-06-11T00:00:00",
        scan_end_iso="2026-06-11T01:00:00",
        cancel_event=threading.Event(),
    )

    assert result.succeeded == 3
    # Resolved exactly once for the whole cohort.
    assert calls["resolve"] == 1
    # Every target received the same resolved policy.
    assert calls["tls_seen"] == [False, False, False]
