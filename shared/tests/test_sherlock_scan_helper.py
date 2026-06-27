"""
C4 unit tests for the reusable scan-and-persist helper
(gui.utils.sherlock_scan.run_sherlock_scan).

Covers honest counting (skips / not-persisted / errors), correct snapshot_id
threading into store, and the no-network/no-content guarantee via a fake reader.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.utils.sherlock_scan import SherlockScanSummary, run_sherlock_scan
from shared.sherlock import MatchResult, Severity, SherlockHit, default_settings


def _hit_result(n: int) -> MatchResult:
    hits = [
        SherlockHit(
            severity=Severity.HIGH,
            category="Credentials",
            label="Password files",
            pattern="*password*",
            display_path=f"share/secret_{i}_password.txt",
        )
        for i in range(n)
    ]
    return MatchResult(hits=hits, highest_severity=Severity.HIGH if hits else None, hit_count=n)


class FakeReader:
    """In-memory reader: never touches network/filesystem/content."""

    def __init__(self, snapshots, store_returns=None, store_raises_for=None):
        # snapshots: {ip: (snapshot_id, payload)} or {ip: None} for missing
        self.snapshots = snapshots
        self.store_returns = store_returns or {}
        self.store_raises_for = store_raises_for or set()
        self.store_calls = []

    def get_latest_probe_snapshot_with_id_for_host(self, ip, host_type, *, protocol_server_id=None, port=None):
        return self.snapshots.get(ip)

    def store_sherlock_result(self, ip, host_type, snapshot_id, match_result, *, protocol_server_id=None, port=None):
        self.store_calls.append((ip, snapshot_id, match_result.hit_count))
        if ip in self.store_raises_for:
            raise RuntimeError("boom")
        return self.store_returns.get(ip, 1)


def _target(ip, host_type="S", sid=1, port=None):
    return {"ip_address": ip, "host_type": host_type, "protocol_server_id": sid, "port": port}


def test_finding_counted_and_snapshot_id_threaded():
    reader = FakeReader({"1.1.1.1": (42, {"shares": []})})
    summary = run_sherlock_scan(reader, default_settings(), [_target("1.1.1.1")])
    assert summary == SherlockScanSummary(scanned=1, with_findings=0, clean=1, skipped_no_snapshot=0, not_persisted=0, errors=0)
    # store called with the snapshot id returned by the reader.
    assert reader.store_calls[0][1] == 42


def test_hits_increment_with_findings(monkeypatch):
    reader = FakeReader({"1.1.1.1": (7, {"shares": [{"share": "S", "root_files": ["my_password.txt"]}]})})
    summary = run_sherlock_scan(reader, default_settings(), [_target("1.1.1.1")])
    assert summary.scanned == 1
    assert summary.with_findings == 1
    assert summary.clean == 0


def test_missing_snapshot_skipped_and_counted():
    reader = FakeReader({"1.1.1.1": None})
    summary = run_sherlock_scan(reader, default_settings(), [_target("1.1.1.1")])
    assert summary.skipped_no_snapshot == 1
    assert summary.scanned == 0
    assert reader.store_calls == []


def test_guarded_store_noop_counts_not_persisted():
    reader = FakeReader({"1.1.1.1": (1, {"shares": []})}, store_returns={"1.1.1.1": None})
    summary = run_sherlock_scan(reader, default_settings(), [_target("1.1.1.1")])
    assert summary.not_persisted == 1
    assert summary.scanned == 0
    assert summary.with_findings == 0
    assert summary.clean == 0


def test_per_target_exception_counted_and_batch_continues():
    reader = FakeReader(
        {"1.1.1.1": (1, {"shares": []}), "2.2.2.2": (2, {"shares": []})},
        store_raises_for={"1.1.1.1"},
    )
    targets = [_target("1.1.1.1"), _target("2.2.2.2", sid=2)]
    summary = run_sherlock_scan(reader, default_settings(), targets)
    assert summary.errors == 1
    assert summary.scanned == 1  # second host still processed


def test_mixed_counts():
    reader = FakeReader(
        {
            "ok": (1, {"shares": []}),
            "missing": None,
            "noop": (2, {"shares": []}),
        },
        store_returns={"noop": None},
    )
    targets = [_target("ok"), _target("missing", sid=2), _target("noop", sid=3)]
    summary = run_sherlock_scan(reader, default_settings(), targets)
    assert summary.scanned == 1
    assert summary.skipped_no_snapshot == 1
    assert summary.not_persisted == 1


def test_no_network_or_content_apis_touched(monkeypatch):
    """The helper must only use the reader + pure matcher - never sockets/files."""
    import socket

    def _boom(*a, **k):
        raise AssertionError("network/content access is forbidden in Sherlock scan")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom, raising=False)
    monkeypatch.setattr("builtins.open", _boom)

    reader = FakeReader({"1.1.1.1": (1, {"shares": [{"share": "S", "root_files": ["id_rsa"]}]})})
    summary = run_sherlock_scan(reader, default_settings(), [_target("1.1.1.1")])
    assert summary.scanned == 1
    assert summary.with_findings == 1
