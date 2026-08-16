"""C13 desktop service and verified completed-report state tests."""

from __future__ import annotations

import os
import json
import stat
import threading
from pathlib import Path

import pytest

from experimental.analyst.lease import claim_worker
from experimental.analyst.process_identity import current_process_identity
from experimental.analyst.report_browser import (
    ExportFormat,
    FindingExportSelection,
    ReviewDecision,
    export_model_findings,
    list_completed_reports,
    load_completed_detector_page,
    load_completed_inventory_page,
    load_completed_model_page,
    open_completed_report,
    review_model_finding,
)
from experimental.analyst.report import finalize_report
from experimental.analyst.service import (
    AnalystServiceError,
    DirectoryRunRequest,
    ServiceFailure,
    cancel_run,
    completed_report_html,
    create_directory_run,
    launch_run,
    list_run_summaries,
    reconcile_for_hydration,
    resume_run,
)
from experimental.analyst.state import RunState
from experimental.analyst.store import open_connection
from experimental.analyst.worker import run_worker
from experimental.analyst.worker_contract import WorkerOutcome
from experimental.analyst.worker_preflight import (
    WorkerPreflightResult,
    WorkerPreflightStatus,
)
from shared.path_service import get_paths


_RUN_ID = "a" * 32
_LOG_TOKEN = "b" * 16
_SUCCESS = WorkerPreflightResult(WorkerPreflightStatus.SUCCESS)


def _request(tmp_path: Path, *, files: tuple[bytes, ...] = ()):
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    for index, body in enumerate(files):
        (source / f"public-{index}.txt").write_bytes(body)
    return DirectoryRunRequest(source, output, "Public Directory", "fast")


def _create(tmp_path: Path, *, files: tuple[bytes, ...] = ()):
    paths = get_paths(home_root=tmp_path / "home")
    request = _request(tmp_path, files=files)
    run_id, inventory = create_directory_run(
        request,
        path=paths.analyst_db_file,
        run_id_factory=lambda _size: _RUN_ID,
    )
    return paths, request, run_id, inventory


def test_directory_run_persists_exact_frozen_identity_and_safe_output(tmp_path):
    paths, request, run_id, inventory = _create(
        tmp_path, files=(b"public", b"public two"),
    )
    assert run_id == _RUN_ID
    assert len(inventory.files) == 2
    conn = open_connection(paths.analyst_db_file, read_only=True)
    try:
        row = conn.execute("SELECT * FROM analyst_runs WHERE run_id=?", (run_id,)).fetchone()
    finally:
        conn.close()
    assert row["state"] == "ready"
    assert row["source_mode"] == "unknown"
    assert row["source_root"] == str(request.source_root)
    assert row["report_label"] == "Public Directory"
    assert row["isolation_mode"] == "strict"
    assert row["output_root"].startswith(str(request.output_base / "_analyst") + "/")
    assert "public-directory-" in row["output_root"]
    assert row["model_tag"] == "qwen3.6:27b"


@pytest.mark.parametrize(
    "source,output,label,mode",
    [
        (Path("relative"), Path("/tmp/out"), "label", "fast"),
        (Path("/tmp/source"), Path("relative"), "label", "fast"),
        (Path("/tmp/source"), Path("/tmp/out"), "", "fast"),
        (Path("/tmp/source"), Path("/tmp/out"), "bad\nlabel", "fast"),
        (Path("/tmp/source"), Path("/tmp/out"), "label", "wide"),
    ],
)
def test_directory_request_rejects_invalid_values(source, output, label, mode):
    with pytest.raises(ValueError):
        DirectoryRunRequest(source, output, label, mode)


def test_inventory_failure_creates_no_database(tmp_path):
    db = tmp_path / "state" / "analyst.db"
    output = tmp_path / "output"
    output.mkdir()
    request = DirectoryRunRequest(
        tmp_path / "missing", output, "Public", "fast",
    )
    with pytest.raises(AnalystServiceError) as caught:
        create_directory_run(request, path=db)
    assert caught.value.code is ServiceFailure.INVENTORY
    assert not db.exists()


def test_symlink_output_base_fails_before_inventory_or_database(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    marker = source / "private-marker.txt"
    marker.write_text("PRIVATE SOURCE MARKER", encoding="utf-8")
    real_output = tmp_path / "real-output"
    real_output.mkdir()
    linked_output = tmp_path / "linked-output"
    linked_output.symlink_to(real_output, target_is_directory=True)
    db = tmp_path / "state" / "analyst.db"
    request = DirectoryRunRequest(source, linked_output, "Public", "fast")
    with pytest.raises(AnalystServiceError) as caught:
        create_directory_run(request, path=db)
    assert caught.value.code is ServiceFailure.CONTRACT
    assert not db.exists()


def test_detached_launch_has_exact_argv_controls_and_private_log(tmp_path):
    paths, _request_value, run_id, _inventory = _create(tmp_path)
    observed = {}

    class Process:
        pid = 4321

    def fake_popen(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        observed["mode"] = stat.S_IMODE(os.fstat(kwargs["stdout"].fileno()).st_mode)
        return Process()

    result = launch_run(
        run_id,
        path=paths.analyst_db_file,
        paths=paths,
        popen_factory=fake_popen,
        log_token_factory=lambda _size: _LOG_TOKEN,
    )
    assert result.pid == 4321
    assert observed["argv"] == (
        str(paths.repo_root / "venv" / "bin" / "python"),
        "-B", "-m", "experimental.analyst.worker", "--run-id", run_id,
    )
    assert observed["kwargs"]["cwd"] == str(paths.repo_root)
    assert observed["kwargs"]["shell"] is False
    assert observed["kwargs"]["close_fds"] is True
    assert observed["kwargs"]["start_new_session"] is True
    assert observed["kwargs"]["stdin"] == -3
    assert observed["kwargs"]["stderr"] == -2
    assert observed["mode"] == 0o600
    assert stat.S_IMODE(paths.analyst_logs_dir.stat().st_mode) == 0o700
    assert str(result.log_file) not in repr(result)


def test_launch_failure_leaves_ready_run_and_removes_empty_log(tmp_path):
    paths, _request_value, run_id, _inventory = _create(tmp_path)

    def fail(*_args, **_kwargs):
        raise OSError("private marker")

    with pytest.raises(AnalystServiceError) as caught:
        launch_run(
            run_id,
            path=paths.analyst_db_file,
            paths=paths,
            popen_factory=fail,
            log_token_factory=lambda _size: _LOG_TOKEN,
        )
    assert caught.value.code is ServiceFailure.LAUNCH
    assert not list(paths.analyst_logs_dir.glob("*.log"))
    assert list_run_summaries(path=paths.analyst_db_file)[0].state is RunState.READY


def test_worker_log_directory_symlink_fails_closed(tmp_path):
    paths, _request_value, run_id, _inventory = _create(tmp_path)
    paths.logs_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    paths.analyst_logs_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(AnalystServiceError) as caught:
        launch_run(
            run_id,
            path=paths.analyst_db_file,
            paths=paths,
            popen_factory=lambda *_args, **_kwargs: pytest.fail("worker launched"),
        )
    assert caught.value.code is ServiceFailure.LAUNCH
    assert not list(outside.iterdir())


def test_cancel_persists_intent_before_signal(tmp_path, monkeypatch):
    paths, _request_value, run_id, _inventory = _create(tmp_path)
    fence = claim_worker(
        run_id,
        current_process_identity(),
        owner_token="c" * 64,
        heartbeat_monotonic_ns=1,
        path=paths.analyst_db_file,
    )
    assert fence is not None
    observed = []

    def fake_signal(actual):
        conn = open_connection(paths.analyst_db_file, read_only=True)
        try:
            state = conn.execute(
                "SELECT state FROM analyst_runs WHERE run_id=?", (run_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        observed.append((actual, state))
        return True

    monkeypatch.setattr("experimental.analyst.lease.signal_cancel", fake_signal)
    result = cancel_run(run_id, path=paths.analyst_db_file)
    assert result.signal_sent is True
    assert observed == [(fence, "cancel_requested")]


def test_ready_resume_launches_and_hydration_is_content_free(tmp_path):
    paths, _request_value, run_id, _inventory = _create(tmp_path)
    calls = []

    class Process:
        pid = 123

    # resume_run intentionally uses the production launcher; patch its module seam.
    import experimental.analyst.service as service

    original = service.subprocess.Popen
    service.subprocess.Popen = lambda argv, **kwargs: (calls.append(argv) or Process())
    try:
        result = resume_run(run_id, path=paths.analyst_db_file, paths=paths)
    finally:
        service.subprocess.Popen = original
    assert result.run_id == run_id
    assert len(calls) == 1
    assert reconcile_for_hydration(path=paths.analyst_db_file) == "no_lease"
    summary = list_run_summaries(path=paths.analyst_db_file)[0]
    assert summary.task_id == "analyst:" + run_id
    assert "Public Directory" not in repr(summary)


def test_empty_public_run_completes_and_browser_verifies_lazy_pages(tmp_path):
    paths, _request_value, run_id, _inventory = _create(tmp_path)
    result = run_worker(
        run_id,
        threading.Event(),
        path=paths.analyst_db_file,
        preflight=lambda _context, _cancel: _SUCCESS,
    )
    assert result.outcome is WorkerOutcome.COMPLETE
    listed = list_completed_reports(path=paths.analyst_db_file)
    assert listed == ((run_id, "Public Directory", listed[0][2]),)
    handle = open_completed_report(run_id, path=paths.analyst_db_file)
    assert handle.discovered_files == 0
    assert handle.detector_hits == 0
    assert handle.model_findings == 0
    assert load_completed_inventory_page(handle, path=paths.analyst_db_file) == ()
    assert load_completed_detector_page(handle, path=paths.analyst_db_file) == ()
    assert load_completed_model_page(handle, path=paths.analyst_db_file) == ()
    html = completed_report_html(run_id, path=paths.analyst_db_file)
    assert html.name == "report.html"
    assert html.stat().st_mode & 0o777 == 0o600


def test_completed_handle_fails_after_manifest_or_db_identity_tamper(tmp_path):
    paths, _request_value, run_id, _inventory = _create(tmp_path)
    assert run_worker(
        run_id, threading.Event(), path=paths.analyst_db_file,
        preflight=lambda _context, _cancel: _SUCCESS,
    ).outcome is WorkerOutcome.COMPLETE
    handle = open_completed_report(run_id, path=paths.analyst_db_file)
    conn = open_connection(paths.analyst_db_file)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE analyst_runs SET report_manifest_sha256=? WHERE run_id=?",
            ("f" * 64, run_id),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    with pytest.raises(Exception):
        load_completed_inventory_page(handle, path=paths.analyst_db_file)
    with pytest.raises(AnalystServiceError) as caught:
        completed_report_html(run_id, path=paths.analyst_db_file)
    assert caught.value.code is ServiceFailure.REPORT


def test_review_and_explicit_jsonl_csv_export_are_durable_and_safe(tmp_path):
    from shared.tests.test_analyst_c12 import _reviewed_phase2

    marker = "=1+1<script>alert(7)</script>"
    response = json.dumps({
        "document_type": "Public note",
        "subject": "Synthetic",
        "assessment": "findings_present",
        "findings": [{"category": "financial", "quote": marker, "offset": 0}],
    }, separators=(",", ":"))
    db, handoff = _reviewed_phase2(tmp_path, marker, response)
    finalize_report(handoff, path=db)
    handle = open_completed_report(handoff.fence.run_id, path=db)
    page = load_completed_model_page(handle, path=db)
    assert len(page) == 1
    finding_id, row = page[0]
    assert row.review_state == "unreviewed"

    review_model_finding(
        handle, finding_id, ReviewDecision.ACCEPTED,
        now_utc="2026-08-16T20:00:00Z", path=db,
    )
    updated = load_completed_model_page(handle, path=db)[0][1]
    assert updated.review_state == "accepted"
    with pytest.raises(ValueError):
        review_model_finding(
            handle, finding_id, ReviewDecision.REJECTED,
            now_utc="PRIVATE_SOURCE_MARKER", path=db,
        )

    jsonl = tmp_path / "selected.jsonl"
    count = export_model_findings(
        handle,
        FindingExportSelection(False, (finding_id,)),
        jsonl,
        ExportFormat.JSONL,
        path=db,
    )
    assert count == 1
    assert marker in jsonl.read_text(encoding="utf-8")
    assert stat.S_IMODE(jsonl.stat().st_mode) == 0o600

    csv_path = tmp_path / "selected.csv"
    assert export_model_findings(
        handle,
        FindingExportSelection(True, ()),
        csv_path,
        ExportFormat.CSV,
        path=db,
    ) == 1
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "'=1+1<script>alert(7)</script>" in csv_text
    assert "accepted" in csv_text


def test_export_rejects_foreign_id_and_symlink_target_without_replacement(tmp_path):
    paths, _request_value, run_id, _inventory = _create(tmp_path)
    assert run_worker(
        run_id, threading.Event(), path=paths.analyst_db_file,
        preflight=lambda _context, _cancel: _SUCCESS,
    ).outcome is WorkerOutcome.COMPLETE
    handle = open_completed_report(run_id, path=paths.analyst_db_file)
    with pytest.raises(Exception):
        export_model_findings(
            handle,
            FindingExportSelection(False, (999,)),
            tmp_path / "missing.jsonl",
            ExportFormat.JSONL,
            path=paths.analyst_db_file,
        )
    target = tmp_path / "outside"
    target.write_text("UNCHANGED", encoding="utf-8")
    link = tmp_path / "selected.jsonl"
    link.symlink_to(target)
    with pytest.raises(Exception):
        export_model_findings(
            handle,
            FindingExportSelection(True, ()),
            link,
            ExportFormat.JSONL,
            path=paths.analyst_db_file,
        )
    assert target.read_text(encoding="utf-8") == "UNCHANGED"
