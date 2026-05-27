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

    def upsert_probe_snapshot_for_host(
        self,
        ip_address,
        host_type,
        payload,
        *,
        protocol_server_id=None,
        port=None,
        source=None,
    ):
        self.snapshot_calls.append(
            {
                "ip_address": ip_address,
                "host_type": host_type,
                "payload": payload,
                "protocol_server_id": protocol_server_id,
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
        last_probe_at=None,
        latest_snapshot_id=None,
        accessible_dirs_count=None,
        accessible_dirs_list=None,
        accessible_files_count=None,
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
                "last_probe_at": last_probe_at,
                "latest_snapshot_id": latest_snapshot_id,
                "accessible_dirs_count": accessible_dirs_count,
                "accessible_dirs_list": accessible_dirs_list,
                "accessible_files_count": accessible_files_count,
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


def _create_se_dork_sidecar_with_probe(db_path: Path, *, snapshot: bool = False) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        snapshot_column = ", probe_snapshot_json TEXT" if snapshot else ""
        conn.execute(
            f"""
            CREATE TABLE dork_results (
                result_id INTEGER PRIMARY KEY,
                url TEXT,
                probe_status TEXT,
                probe_indicator_matches INTEGER,
                probe_preview TEXT,
                probe_checked_at TEXT,
                probe_error TEXT
                {snapshot_column}
            )
            """
        )
        snapshot_cols = ", probe_snapshot_json" if snapshot else ""
        snapshot_values = ", ?" if snapshot else ""
        snapshot_payload = {
            "run_at": "2026-05-03T10:20:30",
            "shares": [
                {
                    "share": "http_root",
                    "root_files": ["index.html"],
                    "directories": [{"name": "pub", "files": ["readme.txt"]}],
                }
            ],
        }
        conn.execute(
            f"""
            INSERT INTO dork_results(
                result_id, url, probe_status, probe_indicator_matches,
                probe_preview, probe_checked_at, probe_error
                {snapshot_cols}
            ) VALUES (?, ?, ?, ?, ?, ?, ?{snapshot_values})
            """,
            (
                1,
                "https://example.local/files/",
                "issue",
                2,
                "pub,movies",
                "2026-05-03T10:20:30",
                None,
            ) + ((json.dumps(snapshot_payload),) if snapshot else ()),
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


def test_targeted_se_dork_import_copies_cacheable_probe_summary(tmp_path, monkeypatch):
    home = tmp_path / "home"
    sidecar_root = home / ".dirracuda"
    sidecar_root.mkdir(parents=True)
    _create_se_dork_sidecar_with_probe(sidecar_root / "se_dork.db")

    monkeypatch.setattr(db_unification.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(db_unification, "_resolve_ipv4", lambda _host: "93.184.216.34")

    reader = _FakeReader()
    result = db_unification.run_targeted_sidecar_import(reader)

    assert result["status"] == "done"
    assert result["imported"] == 1
    assert reader.manual_server_calls == [{
        "host_type": "H",
        "ip_address": "93.184.216.34",
        "port": 443,
        "scheme": "https",
        "probe_host": "example.local",
        "probe_path": "/files/",
    }]
    assert reader.probe_cache_calls == [{
        "ip_address": "93.184.216.34",
        "host_type": "H",
        "status": "issue",
        "indicator_matches": 2,
        "snapshot_path": None,
        "protocol_server_id": 7,
        "port": 443,
        "last_probe_at": "2026-05-03T10:20:30",
        "latest_snapshot_id": None,
        "accessible_dirs_count": 2,
        "accessible_dirs_list": "pub,movies",
        "accessible_files_count": None,
    }]


def test_targeted_se_dork_import_copies_probe_snapshot_and_links_cache(tmp_path, monkeypatch):
    home = tmp_path / "home"
    sidecar_root = home / ".dirracuda"
    sidecar_root.mkdir(parents=True)
    _create_se_dork_sidecar_with_probe(sidecar_root / "se_dork.db", snapshot=True)

    monkeypatch.setattr(db_unification.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(db_unification, "_resolve_ipv4", lambda _host: "93.184.216.34")

    reader = _FakeReader()
    result = db_unification.run_targeted_sidecar_import(reader)

    assert result["status"] == "done"
    assert result["imported"] == 1
    assert reader.snapshot_calls == [{
        "ip_address": "93.184.216.34",
        "host_type": "H",
        "payload": {
            "run_at": "2026-05-03T10:20:30",
            "shares": [
                {
                    "share": "http_root",
                    "root_files": ["index.html"],
                    "directories": [{"name": "pub", "files": ["readme.txt"]}],
                }
            ],
        },
        "protocol_server_id": 7,
        "port": 443,
        "source": "sidecar:se_dork",
    }]
    assert reader.probe_cache_calls[0]["latest_snapshot_id"] == 42
    assert reader.probe_cache_calls[0]["accessible_dirs_count"] == 1
    assert reader.probe_cache_calls[0]["accessible_files_count"] == 2


def test_targeted_se_dork_import_ignores_invalid_snapshot_json(tmp_path, monkeypatch):
    home = tmp_path / "home"
    sidecar_root = home / ".dirracuda"
    sidecar_root.mkdir(parents=True)
    sidecar_db = sidecar_root / "se_dork.db"
    _create_se_dork_sidecar_with_probe(sidecar_db, snapshot=True)
    with sqlite3.connect(str(sidecar_db)) as conn:
        conn.execute("UPDATE dork_results SET probe_snapshot_json = ?", ("{bad json",))
        conn.commit()

    monkeypatch.setattr(db_unification.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(db_unification, "_resolve_ipv4", lambda _host: "93.184.216.34")

    reader = _FakeReader()
    result = db_unification.run_targeted_sidecar_import(reader)

    assert result["status"] == "done"
    assert result["imported"] == 1
    assert reader.snapshot_calls == []
    assert reader.probe_cache_calls[0]["latest_snapshot_id"] is None


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
        "detect_pending_sidecar_migration",
        lambda _reader: (_ for _ in ()).throw(RuntimeError("detection failure")),
    )

    result = db_unification.run_startup_db_unification("/tmp/dirracuda.db")

    assert result["success"] is False
    assert "probe backfill failed: probe failure" in result["errors"]
    # Detection failure is warn-only — must NOT appear in result["errors"]
    assert not any("detection" in e for e in result["errors"])
    assert reader.state["db_unification.probe_backfill.last_error"] == "probe failure"


def test_detect_pending_returns_false_when_deferred():
    reader = _FakeReader()
    reader.state[db_unification._SIDECAR_PROMPT_STATE_KEY] = "deferred"
    assert db_unification.detect_pending_sidecar_migration(reader) is False


def test_detect_pending_returns_false_when_completed():
    reader = _FakeReader()
    reader.state[db_unification._SIDECAR_PROMPT_STATE_KEY] = "completed"
    assert db_unification.detect_pending_sidecar_migration(reader) is False


def test_detect_pending_returns_false_when_failed():
    reader = _FakeReader()
    reader.state[db_unification._SIDECAR_PROMPT_STATE_KEY] = "failed"
    assert db_unification.detect_pending_sidecar_migration(reader) is False


def test_detect_pending_maps_old_import_done_key_to_completed():
    reader = _FakeReader()
    reader.state[db_unification._SIDECAR_IMPORT_DONE_KEY] = "1"
    result = db_unification.detect_pending_sidecar_migration(reader)
    assert result is False
    assert reader.state[db_unification._SIDECAR_PROMPT_STATE_KEY] == "completed"


def test_detect_pending_returns_false_when_no_sidecar_data(monkeypatch):
    reader = _FakeReader()
    monkeypatch.setattr(db_unification, "_has_pending_sidecar_data", lambda: False)
    assert db_unification.detect_pending_sidecar_migration(reader) is False


def test_detect_pending_returns_true_when_data_present(monkeypatch):
    reader = _FakeReader()
    monkeypatch.setattr(db_unification, "_has_pending_sidecar_data", lambda: True)
    assert db_unification.detect_pending_sidecar_migration(reader) is True


def test_defer_sidecar_migration_sets_state():
    reader = _FakeReader()
    db_unification.defer_sidecar_migration(reader)
    assert reader.state[db_unification._SIDECAR_PROMPT_STATE_KEY] == "deferred"


def test_execute_sidecar_migration_now_success_returns_completed_status(monkeypatch):
    reader = _FakeReader()
    monkeypatch.setattr(
        db_unification,
        "run_targeted_sidecar_import",
        lambda _r: {"status": "done", "imported": 3, "skipped": 0, "errors": 0},
    )
    result = db_unification.execute_sidecar_migration_now(reader)
    # "completed" must win over inner "done"
    assert result["status"] == "completed"
    assert result["imported"] == 3
    assert reader.state[db_unification._SIDECAR_PROMPT_STATE_KEY] == "completed"


def test_execute_sidecar_migration_now_partial_errors_still_completed(monkeypatch):
    # Partial row errors (errors > 0) with no exception → state = "completed" (intentional)
    reader = _FakeReader()
    monkeypatch.setattr(
        db_unification,
        "run_targeted_sidecar_import",
        lambda _r: {"status": "done", "imported": 2, "skipped": 1, "errors": 1},
    )
    result = db_unification.execute_sidecar_migration_now(reader)
    assert result["status"] == "completed"
    assert reader.state[db_unification._SIDECAR_PROMPT_STATE_KEY] == "completed"


def test_execute_sidecar_migration_now_failure_sets_failed_state(monkeypatch):
    reader = _FakeReader()
    monkeypatch.setattr(
        db_unification,
        "run_targeted_sidecar_import",
        lambda _r: (_ for _ in ()).throw(RuntimeError("import boom")),
    )
    result = db_unification.execute_sidecar_migration_now(reader)
    assert result["status"] == "failed"
    assert "import boom" in result["error"]
    assert reader.state[db_unification._SIDECAR_PROMPT_STATE_KEY] == "failed"
