"""C12: dashboard probe results propagate row_key, and the batch summary wrapper
enriches Sherlock Risk + computes show_risk for all three protocols."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Import the dashboard module so _d("...") can resolve patched symbols.
import gui.components.dashboard  # noqa: F401
from gui.components import dashboard_batch_ops as ops


_LABELS = {"S": "SMB", "F": "FTP", "H": "HTTP"}


class _FakeReader:
    def __init__(self, risk_map=None):
        self._risk_map = risk_map or {}

    def upsert_probe_snapshot_for_host(self, *_a, **_k):
        return 101

    def upsert_probe_cache_for_host(self, *_a, **_k):
        return None

    def get_http_server_detail(self, *_a, **_k):
        return {}

    def get_sherlock_risk_summary_map(self):
        return self._risk_map


class _FakeDash:
    def __init__(self, reader=None):
        self.db_reader = reader or _FakeReader()
        self.indicator_patterns = []
        self.settings_manager = None  # run_after_probe disabled -> hook no-ops
        self.parent = object()
        self.theme = None

    def _protocol_label_from_host_type(self, ht):
        return _LABELS.get(str(ht or "").upper(), "")

    def _protocol_label_for_result(self, result):
        explicit = str(result.get("protocol") or "").strip().upper()
        return explicit or self._protocol_label_from_host_type(result.get("host_type"))

    def _build_probe_notes(self, share_count, _issue, _analysis, _result):
        return f"{share_count} share(s)"


class _FakePatterns:
    @staticmethod
    def attach_indicator_analysis(snapshot, _patterns):
        return {"is_suspicious": False, "matches": []}


def _patch_probe(monkeypatch, snapshot):
    monkeypatch.setattr("gui.components.dashboard.dispatch_probe_run", lambda *a, **k: snapshot)
    monkeypatch.setattr("gui.components.dashboard.probe_patterns", _FakePatterns)
    monkeypatch.setattr(
        "gui.components.dashboard_batch_ops.summarize_probe_snapshot",
        lambda _snap: {"directory_names": [], "display_entries": [], "total_file_count": 0},
    )


def _probe(server):
    return ops.probe_single_server(
        _FakeDash(),
        server,
        max_dirs=3,
        max_files=5,
        timeout_seconds=10,
        max_depth=1,
        cancel_event=threading.Event(),
    )


def test_smb_probe_result_carries_row_key(monkeypatch):
    _patch_probe(monkeypatch, {"shares": ["public"]})
    result = _probe({"ip_address": "10.0.0.12", "host_type": "S", "protocol_server_id": 7})
    assert result["status"] == "success"
    assert result["row_key"] == "S:7"


def test_ftp_probe_result_carries_row_key(monkeypatch):
    # FTP branch never reads protocol_server_id itself, but it must still flow
    # through from the server dict into the result row_key.
    _patch_probe(monkeypatch, {})
    result = _probe(
        {"ip_address": "10.0.0.21", "host_type": "F", "protocol_server_id": 12, "port": 21}
    )
    assert result["status"] == "success"
    assert result["row_key"] == "F:12"


def test_http_probe_result_carries_row_key(monkeypatch):
    _patch_probe(monkeypatch, {})
    result = _probe(
        {"ip_address": "10.0.0.33", "host_type": "H", "protocol_server_id": 9,
         "port": 80, "scheme": "http"}
    )
    assert result["status"] == "success"
    assert result["row_key"] == "H:9"


def test_probe_result_row_key_none_when_protocol_server_id_missing(monkeypatch):
    _patch_probe(monkeypatch, {"shares": []})
    result = _probe({"ip_address": "10.0.0.99", "host_type": "S"})
    assert result["status"] == "success"
    assert result["row_key"] is None


def test_show_batch_summary_enriches_and_flags_risk(monkeypatch):
    captured = {}

    def _fake_dialog(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(ops, "show_batch_summary_dialog", _fake_dialog)

    risk = {"severity": "high", "count": 4, "stale": False, "display_color_tag": "none"}
    dash = _FakeDash(reader=_FakeReader({"S:7": risk}))
    results = [
        {"ip_address": "10.0.0.12", "row_key": "S:7", "action": "probe", "status": "success", "notes": "hit"},
        {"ip_address": "10.0.0.44", "row_key": "S:8", "action": "probe", "status": "success", "notes": "clean"},
    ]

    ops.show_batch_summary(dash, results, job_type="probe")

    assert captured["show_risk"] is True
    assert captured["sherlock_settings"] is not None
    enriched = captured["results"]
    assert enriched[0]["sherlock_risk"] is risk
    assert "sherlock_risk" not in enriched[1]


def test_show_batch_summary_non_probe_skips_risk(monkeypatch):
    captured = {}
    monkeypatch.setattr(ops, "show_batch_summary_dialog", lambda **k: captured.update(k))

    dash = _FakeDash(reader=_FakeReader({"S:7": {"severity": "high", "count": 4, "stale": False}}))
    ops.show_batch_summary(
        dash,
        [{"ip_address": "10.0.0.12", "row_key": "S:7", "action": "extract", "status": "success"}],
        job_type="extract",
    )

    assert captured["show_risk"] is False
    assert captured["sherlock_settings"] is None
