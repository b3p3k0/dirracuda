"""C12 streaming report, output-safety, and finalization acceptance."""

from __future__ import annotations

import csv
import hashlib
import json
import multiprocessing
import os
import stat
import threading
from pathlib import Path

import pytest

from experimental.analyst.checkpoint import begin_finalization
from experimental.analyst.lease import (
    ReconcileResult,
    claim_worker,
    current_lease,
    pulse_worker,
    reconcile_lease,
)
from experimental.analyst.phase1 import Phase1Dependencies, run_phase1
from experimental.analyst.phase2 import run_phase2
from experimental.analyst.phase2_contract import Phase2Handoff
from experimental.analyst.report import (
    ReportDependencies,
    ReportFailure,
    ReportFinalizationError,
    finalize_report,
    verify_completed_report,
)
from experimental.analyst.report_contract import (
    ArtifactIdentity,
    REPORT_ARTIFACT_NAMES,
    ReportManifest,
    canonical_json_bytes,
    csv_safe,
)
from experimental.analyst.report_writer import publish_report
from experimental.analyst.process_identity import current_process_identity
from experimental.analyst.store import load_worker_run, open_connection
from experimental.analyst.worker import run_worker
from experimental.analyst.worker_contract import WorkerOutcome
from shared.tests.test_analyst_c10b import _run, _success_extract
from shared.tests.test_analyst_c10c import _SUCCESS, _queued_run
from shared.tests.test_analyst_c11_engine import (
    FakeClient,
    FakeClock,
    _chat_result,
    _dependencies,
)
from experimental.analyst.ollama_contract import OllamaStatus


_CRASH_EXIT = 73


def _crash_after_report_artifacts(db_path: str) -> None:
    path = Path(db_path)
    fence = current_lease(path=path)
    if fence is None:
        os._exit(71)

    def crash_publisher(*args, **kwargs):
        publish_report(*args, **kwargs)
        os._exit(_CRASH_EXIT)

    finalize_report(
        Phase2Handoff(fence, 0, 0, 0),
        path=path,
        dependencies=ReportDependencies(publisher=crash_publisher),
    )
    os._exit(72)


def _row(path: Path, sql: str) -> tuple[object, ...]:
    conn = open_connection(path, read_only=True)
    try:
        return tuple(conn.execute(sql).fetchone())
    finally:
        conn.close()


def _empty_phase2(tmp_path: Path) -> tuple[Path, Phase2Handoff]:
    path, spec, _inventory, fence = _run(tmp_path, bodies=(), mode="deep")
    return path, Phase2Handoff(fence, 0, 0, 0)


def _reviewed_phase2(
    tmp_path: Path, text: str, response: str,
) -> tuple[Path, Phase2Handoff]:
    path, spec, _inventory, fence = _run(
        tmp_path, bodies=(text,), mode="deep",
    )
    context = load_worker_run(spec.run_id, path=path)
    phase1 = run_phase1(
        context, fence, threading.Event(), path=path,
        dependencies=Phase1Dependencies(extract=_success_extract),
    )
    phase2 = run_phase2(
        context, phase1, threading.Event(), path=path,
        dependencies=_dependencies(
            FakeClient(chats=(_chat_result(OllamaStatus.SUCCESS, response),)),
            FakeClock(),
        ),
    )
    return path, phase2


def _manifest_from_directory(directory: Path) -> ReportManifest:
    return ReportManifest(tuple(
        ArtifactIdentity(
            name,
            (directory / name).stat().st_size,
            hashlib.sha256((directory / name).read_bytes()).hexdigest(),
        )
        for name in REPORT_ARTIFACT_NAMES
    ))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=1+1", "'=1+1"),
        ("＋SUM(A1:A2)", "'＋SUM(A1:A2)"),
        ("＠cmd", "'＠cmd"),
        ('"quoted', "'\"quoted"),
        (",separator", "',separator"),
        ("ordinary", "ordinary"),
        ("'already-text", "'already-text"),
    ],
)
def test_csv_safe_prefix_guard_is_display_only(value: str, expected: str) -> None:
    assert csv_safe(value) == expected
    assert json.loads(canonical_json_bytes({"value": value}))["value"] == value


def test_manifest_is_ordered_exact_and_byte_stable() -> None:
    manifest = ReportManifest(tuple(
        ArtifactIdentity(name, index, f"{index + 1:064x}")
        for index, name in enumerate(REPORT_ARTIFACT_NAMES)
    ))
    assert manifest.canonical_bytes == canonical_json_bytes({
        "artifacts": [
            {"name": item.name, "sha256": item.sha256, "size": item.size}
            for item in manifest.artifacts
        ],
        "schema": "dirracuda-analyst-report-v1",
    })
    assert len(manifest.sha256) == 64


def test_empty_run_publishes_owner_only_report_then_clears_lease(
    tmp_path: Path,
) -> None:
    path, handoff = _empty_phase2(tmp_path)
    result = finalize_report(handoff, path=path)
    output = tmp_path / "output"

    assert tuple(sorted(item.name for item in result.manifest.artifacts)) == tuple(
        sorted(REPORT_ARTIFACT_NAMES)
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE((output / name).stat().st_mode) == 0o600
               for name in REPORT_ARTIFACT_NAMES)
    assert _manifest_from_directory(output).sha256 == result.manifest.sha256
    assert _row(
        path,
        "SELECT state,completion_code,report_manifest_sha256 FROM analyst_runs",
    ) == ("complete", "complete_no_supported_content", result.manifest.sha256)
    assert current_lease(path=path) is None
    run_json = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert run_json["coverage"]["discovered_files"] == 0
    assert run_json["coverage"]["terminal_counts"] == {}
    html = (output / "report.html").read_text(encoding="utf-8")
    assert html.index("<h2>Coverage</h2>") < html.index("<h2>Findings</h2>")
    assert "script-src &#x27;none&#x27;" in html
    assert verify_completed_report(handoff.fence.run_id, path=path).sha256 \
        == result.manifest.sha256


def test_completed_report_verification_detects_content_mode_and_symlink_tamper(
    tmp_path: Path,
) -> None:
    path, handoff = _empty_phase2(tmp_path)
    finalize_report(handoff, path=path)
    artifact = tmp_path / "output" / "run.json"
    artifact.write_bytes(artifact.read_bytes() + b" ")

    with pytest.raises(ReportFinalizationError) as content:
        verify_completed_report(handoff.fence.run_id, path=path)
    assert content.value.code is ReportFailure.OUTPUT

    # Restore through a fresh run and then prove metadata tamper is also closed.
    artifact.write_bytes(artifact.read_bytes()[:-1])
    artifact.chmod(0o644)
    with pytest.raises(ReportFinalizationError) as mode:
        verify_completed_report(handoff.fence.run_id, path=path)
    assert mode.value.code is ReportFailure.OUTPUT


def test_raw_jsonl_round_trips_while_csv_and_html_are_safe(
    tmp_path: Path,
) -> None:
    marker = "=1+1<script>alert(7)</script>"
    response = json.dumps({
        "document_type": "Public note",
        "subject": "Synthetic",
        "assessment": "findings_present",
        "findings": [{"category": "financial", "quote": marker, "offset": 0}],
    }, separators=(",", ":"))
    path, handoff = _reviewed_phase2(tmp_path, marker, response)
    result = finalize_report(handoff, path=path)
    output = tmp_path / "output"

    rows = [json.loads(line) for line in
            (output / "findings.jsonl").read_text(encoding="utf-8").splitlines()]
    model = next(row for row in rows if row["evidence_kind"] == "model")
    assert model["quote"] == marker
    with (output / "findings.csv").open(newline="", encoding="utf-8") as stream:
        csv_rows = list(csv.DictReader(stream))
    assert next(row for row in csv_rows if row["evidence_kind"] == "model")["quote"] \
        == "'" + marker
    rendered = (output / "report.html").read_text(encoding="utf-8")
    assert marker not in rendered
    assert "&lt;script&gt;alert(7)&lt;/script&gt;" in rendered
    assert "<script>" not in rendered
    assert _manifest_from_directory(output).sha256 == result.manifest.sha256


def test_finalizing_fence_can_advance_without_opening_cancellation(
    tmp_path: Path,
) -> None:
    path, handoff = _empty_phase2(tmp_path)
    begin_finalization(handoff.fence, "f" * 64, path=path)
    pulse = pulse_worker(
        handoff.fence,
        heartbeat_monotonic_ns=handoff.fence.heartbeat_monotonic_ns + 1,
        path=path,
    )
    assert pulse.cancel_requested is False
    assert pulse.fence.heartbeat_monotonic_ns > handoff.fence.heartbeat_monotonic_ns


def test_symlink_output_fails_closed_and_releases_interrupted_run(
    tmp_path: Path,
) -> None:
    path, handoff = _empty_phase2(tmp_path)
    victim = tmp_path / "victim"
    victim.mkdir()
    (tmp_path / "output").symlink_to(victim, target_is_directory=True)

    with pytest.raises(ReportFinalizationError) as captured:
        finalize_report(handoff, path=path)

    assert captured.value.code is ReportFailure.OUTPUT
    assert list(victim.iterdir()) == []
    assert _row(path, "SELECT state,finalization_token FROM analyst_runs") == (
        "interrupted", None,
    )
    assert current_lease(path=path) is None


def test_existing_artifact_symlink_is_never_replaced_or_followed(
    tmp_path: Path,
) -> None:
    path, handoff = _empty_phase2(tmp_path)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    victim = tmp_path / "victim.txt"
    victim.write_text("unchanged", encoding="utf-8")
    (output / "run.json").symlink_to(victim)

    with pytest.raises(ReportFinalizationError) as captured:
        finalize_report(handoff, path=path)

    assert captured.value.code is ReportFailure.OUTPUT
    assert victim.read_text(encoding="utf-8") == "unchanged"
    assert (output / "run.json").is_symlink()


def test_counterfeit_handoff_stops_before_any_artifact(
    tmp_path: Path,
) -> None:
    path, handoff = _empty_phase2(tmp_path)
    counterfeit = Phase2Handoff(handoff.fence, 1, 1, 0)

    with pytest.raises(ReportFinalizationError) as captured:
        finalize_report(counterfeit, path=path)

    assert captured.value.code is ReportFailure.CONTRACT
    assert not (tmp_path / "output").exists()
    assert _row(path, "SELECT state FROM analyst_runs") == ("interrupted",)


def test_writer_failure_never_commits_complete_and_hides_exception_text(
    tmp_path: Path,
) -> None:
    path, handoff = _empty_phase2(tmp_path)
    marker = "PRIVATE_EXCEPTION_MARKER"

    def fail_writer(*args, **kwargs):
        raise OSError(marker)

    with pytest.raises(ReportFinalizationError) as captured:
        finalize_report(
            handoff, path=path,
            dependencies=ReportDependencies(publisher=fail_writer),
        )

    assert captured.value.code is ReportFailure.INTERNAL
    assert marker not in repr(captured.value)
    assert _row(path, "SELECT state,report_manifest_sha256 FROM analyst_runs") == (
        "interrupted", None,
    )


def test_full_worker_runs_both_phases_and_report_without_stranding_handoff(
    tmp_path: Path,
) -> None:
    path, spec, _inventory = _queued_run(
        tmp_path, bodies=(b"public complete worker text",), mode="deep",
    )
    result = run_worker(
        spec.run_id,
        threading.Event(),
        path=path,
        phase1_dependencies=Phase1Dependencies(extract=_success_extract),
        phase2_dependencies=_dependencies(FakeClient(), FakeClock()),
        preflight=lambda _context, _cancel: _SUCCESS,
    )

    assert result.outcome is WorkerOutcome.COMPLETE
    assert result.handoff is None
    assert _row(path, "SELECT state,completion_code FROM analyst_runs") == (
        "complete", "complete",
    )
    assert current_lease(path=path) is None
    assert set(item.name for item in _manifest_from_directory(tmp_path / "output").artifacts) \
        == set(REPORT_ARTIFACT_NAMES)


def test_crash_after_durable_artifacts_never_publishes_db_and_resume_replaces(
    tmp_path: Path,
) -> None:
    path, handoff = _empty_phase2(tmp_path)
    process = multiprocessing.get_context("fork").Process(
        target=_crash_after_report_artifacts, args=(str(path),),
    )
    process.start()
    process.join(20)
    assert process.exitcode == _CRASH_EXIT

    output = tmp_path / "output"
    crashed_manifest = _manifest_from_directory(output)
    assert _row(
        path, "SELECT state,report_manifest_sha256 FROM analyst_runs",
    ) == ("finalizing", None)
    assert reconcile_lease(
        path=path,
        now_monotonic_ns=handoff.fence.heartbeat_monotonic_ns + 100,
        identity_reader=lambda _pid: None,
    ) is ReconcileResult.CLEARED_INTERRUPTED
    resumed = claim_worker(
        handoff.fence.run_id,
        current_process_identity(),
        owner_token="b" * 64,
        heartbeat_monotonic_ns=handoff.fence.heartbeat_monotonic_ns + 200,
        path=path,
    )
    assert resumed is not None

    result = finalize_report(Phase2Handoff(resumed, 0, 0, 0), path=path)

    assert result.manifest.sha256 == crashed_manifest.sha256
    assert _row(path, "SELECT state,report_manifest_sha256 FROM analyst_runs") == (
        "complete", result.manifest.sha256,
    )


def test_46724_file_report_streams_pages_with_successor_heartbeats(
    tmp_path: Path,
) -> None:
    path, handoff = _empty_phase2(tmp_path)
    count = 46_724
    conn = open_connection(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "INSERT INTO analyst_files("
            "run_id,ordinal,relative_path,size,mtime_ns,ctime_ns,device,inode,mode,"
            "sha256,stage,work_state,terminal_code,updated_at_utc) VALUES("
            "?,?,?,?,?,?,?,?,?,?,'discovered','terminal','empty',?)",
            (
                (
                    handoff.fence.run_id, index, f"public/path-{index:05d}.txt",
                    0, 1, 1, 1, index + 1, 0o600,
                    hashlib.sha256(str(index).encode("ascii")).hexdigest(),
                    "2026-08-16T20:00:00Z",
                )
                for index in range(count)
            ),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()

    class AdvancingClock:
        value = handoff.fence.heartbeat_monotonic_ns

        def __call__(self) -> int:
            self.value += 2_000_000_001
            return self.value

    result = finalize_report(
        handoff,
        path=path,
        dependencies=ReportDependencies(monotonic_ns=AdvancingClock()),
    )

    payload = json.loads((tmp_path / "output" / "run.json").read_text("utf-8"))
    assert payload["coverage"]["discovered_files"] == count
    assert payload["coverage"]["terminal_counts"] == {"empty": count}
    html = (tmp_path / "output" / "report.html").read_text("utf-8")
    assert html.count("<summary>Inventory page ") == (count + 499) // 500
    assert result.manifest.sha256 == _manifest_from_directory(tmp_path / "output").sha256
