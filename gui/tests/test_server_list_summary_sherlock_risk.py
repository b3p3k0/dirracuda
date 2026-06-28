"""C12: Server List batch summary wrapper enriches Sherlock Risk + flags show_risk."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.sherlock import default_settings
from gui.components.server_list_window.actions import batch_status as bs


class _FakeReader:
    def __init__(self, risk_map):
        self._risk_map = risk_map

    def get_sherlock_risk_summary_map(self):
        return self._risk_map


class _Win(bs.ServerListWindowBatchStatusMixin):
    def __init__(self, reader):
        self.window = object()
        self.theme = None
        self.db_reader = reader

    def _load_sherlock_settings(self):
        return default_settings()


def test_probe_summary_enriches_and_flags_risk(monkeypatch):
    captured = {}
    monkeypatch.setattr(bs, "show_batch_summary_dialog", lambda **k: captured.update(k))

    risk = {"severity": "med", "count": 2, "stale": False, "display_color_tag": "none"}
    win = _Win(_FakeReader({"F:5": risk}))
    results = [
        {"ip_address": "10.0.0.5", "row_key": "F:5", "action": "probe", "status": "success", "notes": "x"},
        {"ip_address": "10.0.0.6", "row_key": "F:6", "action": "probe", "status": "success", "notes": "y"},
    ]

    win._show_batch_summary("probe", results)

    assert captured["show_risk"] is True
    assert captured["sherlock_settings"] is not None
    assert results[0]["sherlock_risk"] is risk
    assert "sherlock_risk" not in results[1]


def test_probe_summary_no_findings_keeps_layout(monkeypatch):
    captured = {}
    monkeypatch.setattr(bs, "show_batch_summary_dialog", lambda **k: captured.update(k))

    win = _Win(_FakeReader({}))  # nothing persisted
    win._show_batch_summary(
        "probe",
        [{"ip_address": "10.0.0.5", "row_key": "F:5", "action": "probe", "status": "success", "notes": "x"}],
    )

    assert captured["show_risk"] is False
    assert captured["sherlock_settings"] is None


def test_failed_probe_row_stays_blank_despite_fresh_risk(monkeypatch):
    captured = {}
    monkeypatch.setattr(bs, "show_batch_summary_dialog", lambda **k: captured.update(k))

    # Host has an old, still-fresh Sherlock result, but THIS probe failed (wrote
    # no new snapshot). The row must stay blank and not raise the Risk column.
    risk = {"severity": "high", "count": 4, "stale": False, "display_color_tag": "none"}
    win = _Win(_FakeReader({"F:5": risk}))
    results = [
        {"ip_address": "10.0.0.5", "row_key": "F:5", "action": "probe", "status": "failed", "notes": "timeout"},
        {"ip_address": "10.0.0.6", "row_key": "F:6", "action": "probe", "status": "cancelled", "notes": "stopped"},
    ]

    win._show_batch_summary("probe", results)

    assert captured["show_risk"] is False
    assert captured["sherlock_settings"] is None
    assert all("sherlock_risk" not in r for r in results)


def test_non_probe_summary_skips_enrichment(monkeypatch):
    captured = {}
    called = {"map": 0}

    class _Counting(_FakeReader):
        def get_sherlock_risk_summary_map(self):
            called["map"] += 1
            return {}

    monkeypatch.setattr(bs, "show_batch_summary_dialog", lambda **k: captured.update(k))
    win = _Win(_Counting({}))
    win._show_batch_summary(
        "extract",
        [{"ip_address": "10.0.0.5", "row_key": "F:5", "action": "extract", "status": "success"}],
    )

    assert captured["show_risk"] is False
    assert called["map"] == 0  # no DB read for non-probe jobs
