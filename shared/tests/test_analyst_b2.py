from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from experimental.analyst import service, store
from experimental.analyst.service import AnalystServiceError, ServiceFailure
from experimental.analyst.state import RunState
from experimental.analyst.store import AnalystStoreError


_NOW = "2026-09-20T12:00:00Z"


def _insert_run(
    path: Path,
    run_id: str,
    output_root: Path,
    *,
    state: RunState = RunState.COMPLETE,
) -> None:
    finished = _NOW if state in {RunState.COMPLETE, RunState.ABANDONED} else None
    completion = (
        "complete" if state is RunState.COMPLETE
        else "abandoned" if state is RunState.ABANDONED
        else None
    )
    finalization_token = "9" * 64 if state is RunState.COMPLETE else None

    def operation(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO analyst_runs("
            "run_id,state,created_at_utc,updated_at_utc,finished_at_utc,"
            "completion_code,mode,source_mode,source_root,output_root,"
            "source_identity_json,source_identity_sha256,report_label,model_tag,"
            "model_digest,worksheet_version,prompt_sha256,response_schema_sha256,"
            "detector_rules_version,detector_rules_sha256,parser_bundle_json,"
            "parser_bundle_sha256,chunk_chars,overlap_chars,num_ctx,num_predict,"
            "isolation_mode,reduced_isolation_ack,finalization_token) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id, state.value, _NOW, _NOW, finished, completion, "fast",
                "unknown", "/source", str(output_root), "{}", "a" * 64,
                "Delete me", "qwen3.6:27b", "b" * 64, "v2", "c" * 64,
                "d" * 64, "rules-v1", "e" * 64, "{}", "f" * 64, 8000,
                256, 8192, 1024, "strict", 0, finalization_token,
            ),
        )
        conn.execute(
            "INSERT INTO analyst_ollama_schedule(run_id,updated_at_utc) "
            "VALUES(?,?)",
            (run_id, _NOW),
        )

    store.run_immediate(operation, path=path)


def _populate_run(path: Path, run_id: str) -> None:
    attempt_id = "1" * 64

    def operation(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO analyst_files("
            "file_id,run_id,ordinal,relative_path,size,mtime_ns,ctime_ns,device,"
            "inode,mode,sha256,stage,work_state,terminal_code,updated_at_utc) "
            "VALUES(1,?,0,'file.txt',3,1,1,1,1,384,?,'model_response_valid',"
            "'terminal','complete_model_reviewed',?)",
            (run_id, "2" * 64, _NOW),
        )
        conn.execute(
            "INSERT INTO analyst_inventory_exclusions("
            "run_id,ordinal,relative_path,reason) VALUES(?,0,'link','symlink')",
            (run_id,),
        )
        conn.execute(
            "INSERT INTO analyst_provenance_units("
            "file_id,ordinal,kind,label,start_char,end_char) "
            "VALUES(1,0,'paragraph','p1',0,3)"
        )
        conn.execute(
            "INSERT INTO analyst_chunks("
            "chunk_id,file_id,chunk_index,start_char,end_char,chunk_sha256,state) "
            "VALUES(1,1,0,0,3,?,'pending')",
            ("3" * 64,),
        )
        conn.execute(
            "INSERT INTO analyst_model_attempts("
            "attempt_id,chunk_id,attempt_no,request_sha256,state,charged_at_utc,"
            "finished_at_utc) VALUES(?,1,1,?,'valid',?,?)",
            (attempt_id, "4" * 64, _NOW, _NOW),
        )
        conn.execute(
            "UPDATE analyst_chunks SET state='model_response_valid',"
            "accepted_attempt_id=?,document_type='text',subject='subject',"
            "assessment='findings_present',raw_finding_count=1,"
            "removed_duplicate_count=0,dropped_ungrounded_count=0 "
            "WHERE chunk_id=1",
            (attempt_id,),
        )
        conn.execute(
            "INSERT INTO analyst_detector_hits("
            "file_id,ordinal,kind,value,start_char,end_char) "
            "VALUES(1,0,'phone','123',0,3)"
        )
        conn.execute(
            "INSERT INTO analyst_model_findings("
            "chunk_id,ordinal,category,quote,model_offset,canonical_offset,"
            "canonical_end,match_count,model_offset_exact) "
            "VALUES(1,0,'contact','123',0,0,3,1,1)"
        )
        conn.execute(
            "INSERT INTO analyst_ollama_contacts("
            "contact_id,run_id,contact_no,kind,chunk_id,semantic_attempt_no,"
            "request_sha256,lease_generation,state,charged_at_utc,finished_at_utc,"
            "attempt_id,resource_failures_before,resource_failures_after) "
            "VALUES(?,?,1,'chat',1,1,?,1,'success',?,?,?,0,0)",
            ("5" * 64, run_id, "6" * 64, _NOW, _NOW, attempt_id),
        )
        conn.execute(
            "INSERT INTO analyst_read("
            "run_id,report_schema_version,read_mode,risk_level,host_summary,"
            "contacts_json,files_read,files_total,flagged_files,created_at_utc) "
            "VALUES(?,1,'quick','LOW','summary','[]',1,1,1,?)",
            (run_id, _NOW),
        )
        conn.execute(
            "INSERT INTO analyst_read_exposures(run_id,ordinal,severity,text) "
            "VALUES(?,1,'LOW','exposure')",
            (run_id,),
        )
        conn.execute(
            "INSERT INTO analyst_read_contact("
            "contact_id,run_id,attempt_no,request_sha256,lease_generation,state,"
            "charged_at_utc,finished_at_utc,resource_failures_before,"
            "resource_failures_after) VALUES(?,?,1,?,1,'success',?,?,0,0)",
            ("7" * 64, run_id, "8" * 64, _NOW, _NOW),
        )
        conn.execute(
            "INSERT INTO analyst_discovery_contact("
            "contact_id,contact_no,endpoint,request_sha256,state,models_found,"
            "charged_at_utc,finished_at_utc) VALUES(?,1,'local',?,'success',1,?,?)",
            ("a" * 64, "b" * 64, _NOW, _NOW),
        )
        conn.execute(
            "INSERT INTO analyst_discovered_model("
            "endpoint,model_tag,model_digest,first_seen_utc,last_seen_utc) "
            "VALUES('local','model',?,?,?)",
            ("c" * 64, _NOW, _NOW),
        )

    store.run_immediate(operation, path=path)


def test_store_delete_run_removes_all_run_rows_and_returns_output_root(
    tmp_path: Path,
) -> None:
    path = store.initialize_database(tmp_path / "state" / "analyst.db")
    run_id = "d" * 32
    output_root = tmp_path / f"report-{run_id[:12]}"
    _insert_run(path, run_id, output_root)
    _populate_run(path, run_id)

    assert store.delete_run(run_id, path=path) == str(output_root)

    conn = store.open_connection(path, read_only=True)
    try:
        for table in (
            "analyst_runs", "analyst_files", "analyst_inventory_exclusions",
            "analyst_provenance_units", "analyst_chunks",
            "analyst_model_attempts", "analyst_detector_hits",
            "analyst_model_findings", "analyst_ollama_contacts",
            "analyst_ollama_schedule", "analyst_read", "analyst_read_exposures",
            "analyst_read_contact",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM analyst_gpu_lease"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM analyst_discovery_contact"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM analyst_discovered_model"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_store_delete_run_refuses_nonterminal_and_leased_runs(
    tmp_path: Path,
) -> None:
    path = store.initialize_database(tmp_path / "state" / "analyst.db")
    active_id = "e" * 32
    leased_id = "f" * 32
    _insert_run(path, active_id, tmp_path / active_id, state=RunState.READY)
    _insert_run(path, leased_id, tmp_path / leased_id)

    with pytest.raises(AnalystStoreError, match="does not exist"):
        store.delete_run("0" * 32, path=path)
    with pytest.raises(ValueError, match="run_id must be nonempty text"):
        store.delete_run("", path=path)
    with pytest.raises(AnalystStoreError, match="not deletable"):
        store.delete_run(active_id, path=path)

    def lease(conn: sqlite3.Connection) -> None:
        conn.execute(
            "UPDATE analyst_gpu_lease SET generation=1,run_id=?,owner_token=?,"
            "pid=1,start_ticks=1,boot_id='boot',heartbeat_monotonic_ns=1,"
            "claimed_at_utc=?,heartbeat_at_utc=? WHERE slot=1",
            (leased_id, "1" * 64, _NOW, _NOW),
        )

    store.run_immediate(lease, path=path)
    with pytest.raises(AnalystStoreError, match="active worker lease"):
        store.delete_run(leased_id, path=path)


def test_service_delete_removes_owned_run_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = store.initialize_database(tmp_path / "state" / "analyst.db")
    run_id = "1" * 32
    output_root = tmp_path / f"report-{run_id[:12]}"
    output_root.mkdir()
    (output_root / "report.html").write_text("report", encoding="utf-8")
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    run_log = logs_dir / f"{run_id}-worker.log"
    run_log.write_text("log", encoding="utf-8")
    other_log = logs_dir / f"{'2' * 32}-worker.log"
    other_log.write_text("other", encoding="utf-8")
    _insert_run(path, run_id, output_root)
    monkeypatch.setattr(
        service, "get_paths", lambda: SimpleNamespace(analyst_logs_dir=logs_dir),
    )

    service.delete_run(run_id, path=path)

    assert not output_root.exists()
    assert not run_log.exists()
    assert other_log.exists()


def test_service_delete_does_not_follow_symlinks_or_remove_unmatched_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = store.initialize_database(tmp_path / "state" / "analyst.db")
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker"
    marker.write_text("keep", encoding="utf-8")
    symlink_id = "3" * 32
    linked_root = tmp_path / f"report-{symlink_id[:12]}"
    linked_root.symlink_to(outside, target_is_directory=True)
    linked_log = logs_dir / f"{symlink_id}-worker.log"
    linked_log.symlink_to(marker)
    unmatched_id = "4" * 32
    unmatched_root = tmp_path / "report-without-run-id"
    unmatched_root.mkdir()
    unmatched_marker = unmatched_root / "report.html"
    unmatched_marker.write_text("keep", encoding="utf-8")
    _insert_run(path, symlink_id, linked_root)
    _insert_run(path, unmatched_id, unmatched_root)
    monkeypatch.setattr(
        service, "get_paths", lambda: SimpleNamespace(analyst_logs_dir=logs_dir),
    )

    service.delete_run(symlink_id, path=path)
    service.delete_run(unmatched_id, path=path)

    assert linked_root.is_symlink()
    assert linked_log.is_symlink()
    assert marker.read_text(encoding="utf-8") == "keep"
    assert unmatched_marker.read_text(encoding="utf-8") == "keep"


def test_service_delete_maps_store_failure_before_disk_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = tmp_path / "report-555555555555"
    output_root.mkdir()
    removed = []
    monkeypatch.setattr(
        service, "store_delete_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AnalystStoreError("no")),
    )
    monkeypatch.setattr(service, "_remove_report_dir", lambda *_args: removed.append(1))

    with pytest.raises(AnalystServiceError) as caught:
        service.delete_run("5" * 32, path=tmp_path / "missing.db")

    assert caught.value.code is ServiceFailure.DELETE
    assert removed == []
    assert output_root.exists()
