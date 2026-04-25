"""Targeted tests for startup DB-unification helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from gui.utils import db_unification


class _FakeReader:
    def __init__(self):
        self.state = {}
        self.reports = []
        self.snapshot_calls = []
        self.latest_calls = []
        self.manual_server_calls = []
        self.probe_cache_calls = []
        self.user_flag_calls = []

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

    def upsert_manual_server_record(self, payload):
        self.manual_server_calls.append(dict(payload))
        return {"protocol_server_id": 7}

    def upsert_probe_cache_for_host(
        self,
        ip_address,
        host_type,
        *,
        status=None,
        indicator_matches=0,
        snapshot_path=None,
        protocol_server_id=None,
        port=None,
    ):
        self.probe_cache_calls.append(
            {
                "ip_address": ip_address,
                "host_type": host_type,
                "status": status,
                "indicator_matches": indicator_matches,
                "snapshot_path": snapshot_path,
                "protocol_server_id": protocol_server_id,
                "port": port,
            }
        )
        return None

    def upsert_user_flags_for_host(
        self,
        ip_address,
        host_type,
        *,
        notes=None,
        protocol_server_id=None,
        port=None,
    ):
        self.user_flag_calls.append(
            {
                "ip_address": ip_address,
                "host_type": host_type,
                "notes": notes,
                "protocol_server_id": protocol_server_id,
                "port": port,
            }
        )
        return None


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


def test_probe_backfill_idempotent_on_rerun(tmp_path, monkeypatch):
    dirs = _legacy_dirs(tmp_path)
    dirs["F"].mkdir(parents=True)
    payload = {"ip_address": "10.10.10.10", "port": 21, "entries": []}
    (dirs["F"] / "10.10.10.10.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(db_unification, "_legacy_probe_dirs", lambda: dirs)

    reader = _FakeReader()
    first = db_unification.run_probe_snapshot_backfill(reader)
    second = db_unification.run_probe_snapshot_backfill(reader)

    assert first["status"] == "done"
    assert first["imported"] == 1
    assert second["status"] == "already_done"
    assert second["imported"] == 0
    assert len(reader.snapshot_calls) == 1


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


def _create_se_dork_sidecar(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE dork_results (
                result_id INTEGER PRIMARY KEY,
                url TEXT,
                probe_status TEXT,
                probe_indicator_matches INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO dork_results(result_id, url, probe_status, probe_indicator_matches) VALUES (?, ?, ?, ?)",
            (1, "https://unresolvable.invalid/path", "clean", 0),
        )
        conn.commit()
    finally:
        conn.close()


def _create_redseek_sidecar(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE reddit_targets (
                id INTEGER PRIMARY KEY,
                host TEXT,
                protocol TEXT,
                notes TEXT,
                target_normalized TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO reddit_targets(id, host, protocol, notes, target_normalized) VALUES (?, ?, ?, ?, ?)",
            (2, "no-such-host.invalid", "HTTP", "note", "http://no-such-host.invalid"),
        )
        conn.commit()
    finally:
        conn.close()


def test_targeted_sidecar_import_skips_unresolved_records_and_reports(tmp_path, monkeypatch):
    home = tmp_path / "home"
    sidecar_root = home / ".dirracuda"
    sidecar_root.mkdir(parents=True)
    _create_se_dork_sidecar(sidecar_root / "se_dork.db")
    _create_redseek_sidecar(sidecar_root / "reddit_od.db")

    monkeypatch.setattr(db_unification.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(db_unification, "_resolve_ipv4", lambda _host: None)

    reader = _FakeReader()
    result = db_unification.run_targeted_sidecar_import(reader)

    assert result["status"] == "done"
    assert result["imported"] == 0
    assert result["skipped"] == 2
    assert result["errors"] == 0
    assert reader.state["db_unification.sidecar_import.completed"] == "1"
    reason_codes = [entry["reason_code"] for entry in reader.reports]
    assert reason_codes.count("unresolved_host") == 2


def test_startup_unification_reports_failures_without_raising(monkeypatch):
    reader = _FakeReader()
    monkeypatch.setattr(db_unification, "DatabaseReader", lambda _db_path: reader)
    monkeypatch.setattr(
        db_unification,
        "run_probe_snapshot_backfill",
        lambda _reader: (_ for _ in ()).throw(RuntimeError("probe failure")),
    )
    monkeypatch.setattr(
        db_unification,
        "run_targeted_sidecar_import",
        lambda _reader: (_ for _ in ()).throw(RuntimeError("sidecar failure")),
    )

    result = db_unification.run_startup_db_unification("/tmp/dirracuda.db")

    assert result["success"] is False
    assert "probe backfill failed: probe failure" in result["errors"]
    assert "sidecar import failed: sidecar failure" in result["errors"]
    assert reader.state["db_unification.probe_backfill.last_error"] == "probe failure"
    assert reader.state["db_unification.sidecar_import.last_error"] == "sidecar failure"
