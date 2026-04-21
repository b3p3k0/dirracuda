"""Targeted tests for startup DB-unification helpers."""

from __future__ import annotations

import json
from pathlib import Path

from gui.utils import db_unification


class _FakeReader:
    def __init__(self):
        self.state = {}
        self.reports = []
        self.snapshot_calls = []
        self.latest_calls = []

    def get_migration_state(self, key, default=None):
        return self.state.get(key, default)

    def set_migration_state(self, key, value):
        self.state[key] = value

    def append_migration_report(self, migration_name, source, reason_code, *, item_key=None, detail=None):
        self.reports.append(
            {
                "migration_name": migration_name,
                "source": source,
                "reason_code": reason_code,
                "item_key": item_key,
                "detail": detail,
            }
        )

    def upsert_probe_snapshot_for_host(self, ip_address, host_type, payload, *, port=None, source=None):
        self.snapshot_calls.append(
            {
                "ip_address": ip_address,
                "host_type": host_type,
                "payload": payload,
                "port": port,
                "source": source,
            }
        )
        return 42

    def set_latest_probe_snapshot_for_host(self, ip_address, host_type, snapshot_id, *, port=None):
        self.latest_calls.append(
            {
                "ip_address": ip_address,
                "host_type": host_type,
                "snapshot_id": snapshot_id,
                "port": port,
            }
        )


def _legacy_dirs(base: Path):
    return {
        "S": base / "probes",
        "F": base / "ftp_probes",
        "H": base / "http_probes",
    }


def test_probe_backfill_imports_legacy_snapshot(tmp_path, monkeypatch):
    dirs = _legacy_dirs(tmp_path)
    dirs["S"].mkdir(parents=True)
    payload = {"shares": [{"share": "C$"}]}
    (dirs["S"] / "10.1.2.3.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(db_unification, "_legacy_probe_dirs", lambda: dirs)

    reader = _FakeReader()
    result = db_unification.run_probe_snapshot_backfill(reader)

    assert result["status"] == "done"
    assert result["imported"] == 1
    assert result["skipped"] == 0
    assert result["errors"] == 0
    assert result["prompt_cleanup"] is True
    assert reader.snapshot_calls[0]["ip_address"] == "10.1.2.3"
    assert reader.snapshot_calls[0]["host_type"] == "S"
    assert reader.latest_calls[0]["snapshot_id"] == 42
    assert reader.state["db_unification.probe_backfill.completed"] == "1"


def test_probe_backfill_skips_invalid_payload_and_reports(tmp_path, monkeypatch):
    dirs = _legacy_dirs(tmp_path)
    dirs["S"].mkdir(parents=True)
    (dirs["S"] / "10.2.3.4.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(db_unification, "_legacy_probe_dirs", lambda: dirs)

    reader = _FakeReader()
    result = db_unification.run_probe_snapshot_backfill(reader)

    assert result["imported"] == 0
    assert result["skipped"] == 1
    assert result["errors"] == 0
    assert reader.reports and reader.reports[0]["reason_code"] == "invalid_payload"


def test_apply_probe_cleanup_choice_discard_removes_files(tmp_path, monkeypatch):
    dirs = _legacy_dirs(tmp_path)
    for folder in dirs.values():
        folder.mkdir(parents=True)
    (dirs["S"] / "a.json").write_text("{}", encoding="utf-8")
    (dirs["F"] / "b.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(db_unification, "_legacy_probe_dirs", lambda: dirs)

    reader = _FakeReader()
    result = db_unification.apply_probe_cleanup_choice(reader, keep_files=False)

    assert result == {"deleted": 2, "kept": False}
    assert reader.state["db_unification.probe_cleanup.prompted"] == "1"
    assert reader.state["db_unification.probe_cleanup.choice"] == "discard"
    assert not (dirs["S"] / "a.json").exists()
    assert not (dirs["F"] / "b.json").exists()


def test_run_targeted_sidecar_import_idempotent_returns_already_done():
    reader = _FakeReader()
    reader.state["db_unification.sidecar_import.completed"] = "1"
    result = db_unification.run_targeted_sidecar_import(reader)
    assert result == {"status": "already_done", "imported": 0, "skipped": 0, "errors": 0}
