"""C10C invocation and same-process Phase 1 worker acceptance tests."""

from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from experimental.analyst.inventory import inventory_tree
from experimental.analyst.lease import (
    claim_worker,
    current_lease,
    release_worker,
    request_cancel,
)
from experimental.analyst.models import ANALYST_DEFAULTS
from experimental.analyst.phase1 import Phase1Dependencies
from experimental.analyst.process_identity import current_process_identity
from experimental.analyst.store import (
    RunSpec,
    create_run,
    initialize_database,
    load_worker_run,
    open_connection,
)
from experimental.analyst.worker import (
    EXIT_FAILURE,
    EXIT_HELD_OR_BUSY,
    EXIT_SUCCESS,
    EXIT_USAGE,
    WorkerRunResult,
    main,
    run_phase1_worker,
    worker_signal_handlers,
)
from experimental.analyst.worker_contract import (
    WorkerOutcome,
    build_source_identity,
)
from experimental.analyst.worker_preflight import (
    PARSER_BUNDLE_KIND,
    PARSER_BUNDLE_VERSION,
    WorkerPreflightResult,
    WorkerPreflightStatus,
    current_parser_bundle,
    current_parser_bundle_mapping,
    current_detector_rules,
    preflight_worker,
)
from experimental.analyst.worksheet import prompt_template_hash, schema_hash


_NOW = "2026-08-16T18:00:00Z"
_SUCCESS = WorkerPreflightResult(WorkerPreflightStatus.SUCCESS)


def _queued_run(
    tmp_path: Path,
    *,
    run_id: str = "public-c10c",
    bodies: tuple[bytes, ...] = (b"public text",),
    suffix: str = ".txt",
    mode: str = "fast",
):
    source = tmp_path / "source"
    source.mkdir(parents=True)
    for index, body in enumerate(bodies):
        (source / f"public-{index}{suffix}").write_bytes(body)
    inventory = inventory_tree(source)
    db_path = tmp_path / "state" / "analyst.db"
    initialize_database(db_path)
    detector_version, detector_sha256 = current_detector_rules()
    spec = RunSpec(
        run_id=run_id,
        mode=mode,
        source_mode="unknown",
        source_root=str(source),
        output_root=str(tmp_path / "output"),
        source_identity=build_source_identity(inventory),
        report_label="Public C10C",
        model_tag=ANALYST_DEFAULTS.model_tag,
        model_digest=ANALYST_DEFAULTS.model_digest,
        worksheet_version=ANALYST_DEFAULTS.worksheet_version,
        prompt_sha256=prompt_template_hash(),
        response_schema_sha256=schema_hash(),
        detector_rules_version=detector_version,
        detector_rules_sha256=detector_sha256,
        parser_bundle=current_parser_bundle_mapping(),
        chunk_chars=ANALYST_DEFAULTS.chunk_chars,
        overlap_chars=ANALYST_DEFAULTS.overlap_chars,
        num_ctx=ANALYST_DEFAULTS.num_ctx,
        num_predict=ANALYST_DEFAULTS.num_predict,
        isolation_mode="strict",
        reduced_isolation_ack=False,
    )
    create_run(spec, inventory, path=db_path, now_utc=_NOW)
    return db_path, spec, inventory


def _success_extract(source_fd: int, expected, cancel_check):
    from experimental.analyst.extract import ExtractionResult

    assert cancel_check() is False
    body = os.pread(source_fd, expected.size, 0).decode("utf-8")
    return ExtractionResult("success", "text", "utf-8", body)


def _docx_bytes(text: str) -> bytes:
    word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    package = "http://schemas.openxmlformats.org/package/2006/relationships"
    office = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    entries = {
        "[Content_Types].xml": (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/></Types>'
        ),
        "_rels/.rels": (
            f'<Relationships xmlns="{package}"><Relationship Id="rId1" '
            f'Type="{office}/officeDocument" Target="word/document.xml"/>'
            "</Relationships>"
        ),
        "word/document.xml": (
            f'<w:document xmlns:w="{word}"><w:body><w:p><w:r><w:t>'
            f"{text}</w:t></w:r></w:p></w:body></w:document>"
        ),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body.encode("utf-8"))
    return output.getvalue()


def _generate_legacy_fixture(tmp_path: Path, kind: str) -> bytes:
    soffice = shutil.which("soffice")
    if soffice is None:
        pytest.skip("LibreOffice is unavailable for public legacy generation")
    output = tmp_path / f"{kind}-out"
    profile = tmp_path / f"{kind}-profile"
    output.mkdir()
    profile.mkdir()
    if kind == "doc":
        source = tmp_path / "public.html"
        source.write_text(
            "<html><body>"
            + "<p>Public worker public-c10c@example.test</p>" * 30
            + "</body></html>",
            encoding="utf-8",
        )
        conversion = "doc:MS Word 97"
    else:
        source = tmp_path / "public.csv"
        source.write_text(
            "Name,Value\nPublic worker,public-c10c@example.test\n",
            encoding="utf-8",
        )
        conversion = "xls:MS Excel 97"
    completed = subprocess.run(
        (
            soffice,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--convert-to",
            conversion,
            "--outdir",
            str(output),
            str(source),
        ),
        cwd=tmp_path,
        env={
            "HOME": str(tmp_path),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    document = output / f"public.{kind}"
    if completed.returncode != 0 or not document.is_file():
        pytest.skip(f"LibreOffice could not generate public {kind.upper()}")
    return document.read_bytes()


def _counts(db_path: Path) -> tuple[int, int, int]:
    conn = open_connection(db_path, read_only=True)
    try:
        return tuple(conn.execute(
            "SELECT "
            "(SELECT count(*) FROM analyst_gpu_lease WHERE run_id IS NOT NULL),"
            "(SELECT count(*) FROM analyst_ollama_contacts),"
            "(SELECT count(*) FROM analyst_files WHERE work_state!='pending')",
        ).fetchone())
    finally:
        conn.close()


@pytest.mark.parametrize(
    "argv",
    [
        (),
        ("--run-id",),
        ("public",),
        ("--run-id", "public", "extra"),
        ("--run-id", "one", "--run-id", "two"),
        ("--run-id=public",),
        ("--run-id", ""),
        ("--run-id", "bad id"),
        ("--run-id", "bad\nmarker"),
        ("--run-id", "x" * 129),
    ],
)
def test_worker_cli_invalid_invocation_is_fixed_and_non_echoing(
    argv: tuple[str, ...], capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "bad\nmarker"

    assert main(argv) == EXIT_USAGE

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == '{"outcome":"invalid_invocation"}\n'
    assert marker not in captured.err


def test_worker_cli_help_is_fixed_and_side_effect_free(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(("--help",)) == EXIT_SUCCESS

    captured = capsys.readouterr()
    assert captured.out == (
        "usage: python -m experimental.analyst.worker --run-id RUN_ID\n"
    )
    assert captured.err == ""


def test_worker_cli_valid_invocation_dispatches_full_worker_once(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "experimental.analyst.worker.run_worker",
        lambda run_id, _event: (
            calls.append(run_id) or WorkerRunResult(WorkerOutcome.COMPLETE)
        ),
    )

    assert main(("--run-id", "public-c10c")) == EXIT_SUCCESS

    captured = capsys.readouterr()
    assert captured.out == '{"outcome":"complete"}\n'
    assert captured.err == ""
    assert calls == ["public-c10c"]


def test_worker_cli_valid_invocation_does_not_touch_default_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert main(("--run-id", "public-c10c")) == EXIT_FAILURE

    assert capsys.readouterr().out == '{"outcome":"internal_error"}\n'
    assert tuple(tmp_path.rglob("*")) == ()


@pytest.mark.parametrize(
    ("argv", "returncode", "stdout", "stderr"),
    [
        (
            ("--run-id", "public-c10c"),
            EXIT_FAILURE,
            '{"outcome":"internal_error"}\n',
            "",
        ),
        (
            ("--run-id", "PUBLIC_SUBPROCESS_SECRET\n"),
            EXIT_USAGE,
            "",
            '{"outcome":"invalid_invocation"}\n',
        ),
        (
            ("--help",),
            EXIT_SUCCESS,
            "usage: python -m experimental.analyst.worker --run-id RUN_ID\n",
            "",
        ),
    ],
)
def test_worker_module_cli_exact_process_contract_and_zero_home_effects(
    tmp_path: Path,
    argv: tuple[str, ...],
    returncode: int,
    stdout: str,
    stderr: str,
) -> None:
    environment = os.environ.copy()
    environment.update({
        "HOME": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
    })

    completed = subprocess.run(
        (sys.executable, "-B", "-m", "experimental.analyst.worker", *argv),
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == returncode
    assert completed.stdout == stdout
    assert completed.stderr == stderr
    assert "PUBLIC_SUBPROCESS_SECRET" not in completed.stdout + completed.stderr
    assert tuple(tmp_path.rglob("*")) == ()


def test_parser_bundle_identity_is_canonical_bounded_and_path_free() -> None:
    identity = current_parser_bundle()
    value = json.loads(identity.canonical_json)

    assert identity.canonical_json == json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    assert hashlib.sha256(identity.canonical_json.encode()).hexdigest() == identity.sha256
    assert value["kind"] == PARSER_BUNDLE_KIND == "analyst-parser-bundle"
    assert value["version"] == PARSER_BUNDLE_VERSION == 1
    assert set(value) == {"dependencies", "files", "kind", "version"}
    assert all("/" not in name and "\\" not in name for name in value["files"])
    assert str(Path.cwd()) not in identity.canonical_json
    assert identity.canonical_json not in repr(identity)


def test_worker_preflight_cancelled_before_dependency_or_sandbox_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experimental.analyst import worker_preflight

    db_path, spec, _inventory = _queued_run(tmp_path)
    context = load_worker_run(spec.run_id, path=db_path)
    monkeypatch.setattr(
        worker_preflight,
        "current_parser_bundle",
        lambda: pytest.fail("cancelled preflight hashed parser files"),
    )

    result = preflight_worker(context, lambda: True)

    assert result.status is WorkerPreflightStatus.CANCELLED
    assert _counts(db_path) == (0, 0, 0)


def test_worker_preflight_parser_drift_stops_before_dependency_and_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experimental.analyst import worker_preflight
    from experimental.analyst.worker_preflight import ParserBundleIdentity

    db_path, spec, _inventory = _queued_run(tmp_path)
    context = load_worker_run(spec.run_id, path=db_path)
    forged_json = '{"kind":"analyst-parser-bundle","version":1}'
    forged = ParserBundleIdentity(
        forged_json, hashlib.sha256(forged_json.encode()).hexdigest(),
    )
    monkeypatch.setattr(worker_preflight, "current_parser_bundle", lambda: forged)
    monkeypatch.setattr(
        worker_preflight,
        "python_runtime_binds",
        lambda: pytest.fail("parser drift reached dependency checks"),
    )
    monkeypatch.setattr(
        worker_preflight,
        "strict_preflight",
        lambda **_kwargs: pytest.fail("parser drift reached sandbox preflight"),
    )

    result = preflight_worker(context, lambda: False)

    assert result.status is WorkerPreflightStatus.PARSER_DRIFT
    assert _counts(db_path) == (0, 0, 0)


def test_worker_preflight_detector_drift_stops_before_dependency_and_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace
    from experimental.analyst import worker_preflight

    db_path, spec, _inventory = _queued_run(tmp_path)
    context = replace(
        load_worker_run(spec.run_id, path=db_path),
        detector_rules_sha256="0" * 64,
    )
    monkeypatch.setattr(
        worker_preflight,
        "python_runtime_binds",
        lambda: pytest.fail("detector drift reached dependency checks"),
    )
    monkeypatch.setattr(
        worker_preflight,
        "strict_preflight",
        lambda **_kwargs: pytest.fail("detector drift reached sandbox preflight"),
    )

    result = preflight_worker(context, lambda: False)

    assert result.status is WorkerPreflightStatus.PARSER_DRIFT
    assert _counts(db_path) == (0, 0, 0)


@pytest.mark.parametrize(
    ("parser_version", "embedded_version", "accepted"),
    [
        ("1.28.0", "1.28.0", True),
        ("1.28.1", "1.28.0", False),
        ("1.28.0", "1.28.1", False),
        (None, "1.28.0", False),
        ("1.28.0", None, False),
    ],
)
def test_public_pdf_dependency_probe_requires_both_exact_native_versions(
    monkeypatch: pytest.MonkeyPatch,
    parser_version: str | None,
    embedded_version: str | None,
    accepted: bool,
) -> None:
    from experimental.analyst import worker_preflight
    from experimental.analyst.extract import ExtractionResult

    def extract_document(*, source_fd, expected, cancel_check):
        assert os.pread(source_fd, expected.size, 0).startswith(b"%PDF-1.4")
        assert cancel_check() is False
        text = "DIRRACUDA PUBLIC PDF PREFLIGHT"
        return ExtractionResult(
            "success",
            "pdf",
            "utf-8",
            text,
            page_char_counts=(len(text),),
            text_page_count=1,
            parser_version=parser_version,
            embedded_version=embedded_version,
        )

    monkeypatch.setattr(worker_preflight, "extract_document", extract_document)

    assert worker_preflight._probe_pdf_runtime(lambda: False) is accepted


def test_public_pdf_probe_mkstemp_failure_is_closed_dependency_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experimental.analyst import worker_preflight

    db_path, spec, _inventory = _queued_run(tmp_path)
    context = load_worker_run(spec.run_id, path=db_path)
    for name in (
        "python_runtime_binds",
        "pdf_runtime_binds",
        "ooxml_runtime_binds",
        "antiword_runtime_binds",
        "xls_runtime_binds",
    ):
        monkeypatch.setattr(worker_preflight, name, lambda: (object(),))
    monkeypatch.setattr(
        worker_preflight.tempfile,
        "mkstemp",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("public failure")),
    )
    monkeypatch.setattr(
        worker_preflight,
        "strict_preflight",
        lambda **_kwargs: pytest.fail("failed PDF probe reached sandbox preflight"),
    )

    result = preflight_worker(context, lambda: False)

    assert result.status is WorkerPreflightStatus.DEPENDENCY_UNAVAILABLE
    assert _counts(db_path) == (0, 0, 0)


def test_public_pdf_probe_cleanup_failure_closes_fd_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experimental.analyst import worker_preflight
    from experimental.analyst.extract import ExtractionResult

    created: list[tuple[int, Path]] = []
    real_mkstemp = worker_preflight.tempfile.mkstemp
    real_unlink = Path.unlink

    def tracked_mkstemp(**kwargs):
        fd, raw_path = real_mkstemp(dir=tmp_path, **kwargs)
        created.append((fd, Path(raw_path)))
        return fd, raw_path

    def failed_unlink(path: Path, *args, **kwargs):
        if created and path == created[0][1]:
            raise OSError("public cleanup failure")
        return real_unlink(path, *args, **kwargs)

    def extract_document(**_kwargs):
        text = "DIRRACUDA PUBLIC PDF PREFLIGHT"
        return ExtractionResult(
            "success",
            "pdf",
            "utf-8",
            text,
            page_char_counts=(len(text),),
            text_page_count=1,
            parser_version="1.28.0",
            embedded_version="1.28.0",
        )

    monkeypatch.setattr(worker_preflight.tempfile, "mkstemp", tracked_mkstemp)
    monkeypatch.setattr(Path, "unlink", failed_unlink)
    monkeypatch.setattr(worker_preflight, "extract_document", extract_document)

    try:
        assert worker_preflight._probe_pdf_runtime(lambda: False) is False
        assert len(created) == 1
        with pytest.raises(OSError):
            os.fstat(created[0][0])
    finally:
        if created:
            real_unlink(created[0][1], missing_ok=True)


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (WorkerPreflightStatus.CANCELLED, WorkerOutcome.INTERRUPTED),
        (WorkerPreflightStatus.RUN_STATE, WorkerOutcome.RUN_INVALID),
        (WorkerPreflightStatus.PARSER_DRIFT, WorkerOutcome.RUN_INVALID),
        (
            WorkerPreflightStatus.DEPENDENCY_UNAVAILABLE,
            WorkerOutcome.PREFLIGHT_FAILED,
        ),
        (WorkerPreflightStatus.SANDBOX_UNAVAILABLE, WorkerOutcome.PREFLIGHT_FAILED),
    ],
)
def test_worker_preflight_failure_never_claims_or_opens_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: WorkerPreflightStatus,
    outcome: WorkerOutcome,
) -> None:
    from experimental.analyst import phase1, source_reopen

    db_path, spec, _inventory = _queued_run(tmp_path)
    monkeypatch.setattr(
        source_reopen,
        "open_inventory_file",
        lambda *_args, **_kwargs: pytest.fail("failed preflight opened source"),
    )
    monkeypatch.setattr(
        phase1,
        "run_phase1",
        lambda *_args, **_kwargs: pytest.fail("failed preflight ran Phase 1"),
    )

    result = run_phase1_worker(
        spec.run_id,
        threading.Event(),
        path=db_path,
        preflight=lambda _context, _cancel: WorkerPreflightResult(status),
    )

    assert result == WorkerRunResult(outcome)
    assert _counts(db_path) == (0, 0, 0)


def test_worker_orders_context_preflight_identity_and_claim_before_phase1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experimental.analyst import lease, phase1, process_identity, store

    db_path, spec, _inventory = _queued_run(tmp_path)
    events: list[str] = []
    real_load = store.load_worker_run

    def load(run_id: str, *, path=None):
        events.append("context")
        return real_load(run_id, path=path)

    def preflight(_context, cancel_check):
        events.append("preflight")
        assert cancel_check() is False
        return _SUCCESS

    def identity():
        events.append("identity")
        return process_identity.ProcessIdentity(
            8111, 9222, "12345678-1234-5678-1234-567812345678",
        )

    def claim(*_args, **_kwargs):
        events.append("claim")
        return None

    monkeypatch.setattr(store, "load_worker_run", load)
    monkeypatch.setattr(process_identity, "current_process_identity", identity)
    monkeypatch.setattr(lease, "claim_worker", claim)
    monkeypatch.setattr(
        phase1,
        "run_phase1",
        lambda *_args, **_kwargs: pytest.fail("lease loser ran Phase 1"),
    )

    result = run_phase1_worker(
        spec.run_id, threading.Event(), path=db_path, preflight=preflight,
    )

    assert result.outcome is WorkerOutcome.LEASE_BUSY
    assert events == ["context", "preflight", "identity", "claim"]
    assert _counts(db_path) == (0, 0, 0)


def test_worker_pre_set_stop_is_zero_db_preflight_and_source(
    tmp_path: Path,
) -> None:
    db_path, spec, _inventory = _queued_run(tmp_path)
    stop = threading.Event()
    stop.set()

    result = run_phase1_worker(
        spec.run_id,
        stop,
        path=db_path,
        preflight=lambda *_args: pytest.fail("pre-set stop ran preflight"),
    )

    assert result.outcome is WorkerOutcome.INTERRUPTED
    assert _counts(db_path) == (0, 0, 0)


@pytest.mark.parametrize("dependencies", [object(), True, {}])
def test_worker_rejects_inexact_dependencies_before_database_access(
    tmp_path: Path,
    dependencies: object,
) -> None:
    db_path = tmp_path / "missing" / "analyst.db"

    result = run_phase1_worker(
        "public-c10c",
        threading.Event(),
        path=db_path,
        dependencies=dependencies,  # type: ignore[arg-type]
        preflight=lambda *_args: pytest.fail("invalid dependencies ran preflight"),
    )

    assert result.outcome is WorkerOutcome.INTERNAL_ERROR
    assert not db_path.exists()


def test_matching_active_lease_returns_busy_before_preflight(tmp_path: Path) -> None:
    db_path, spec, _inventory = _queued_run(tmp_path)
    fence = claim_worker(
        spec.run_id,
        current_process_identity(),
        owner_token="a" * 64,
        heartbeat_monotonic_ns=time.monotonic_ns(),
        path=db_path,
    )
    assert fence is not None

    result = run_phase1_worker(
        spec.run_id,
        threading.Event(),
        path=db_path,
        preflight=lambda *_args: pytest.fail("active worker reran preflight"),
    )

    assert result.outcome is WorkerOutcome.LEASE_BUSY
    assert current_lease(path=db_path) == fence
    assert _counts(db_path) == (1, 0, 0)
    release_worker(fence, path=db_path)


def test_worker_preflight_exception_is_content_free_and_zero_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "PUBLIC_C10C_EXCEPTION_BODY_MARKER"
    db_path, spec, _inventory = _queued_run(tmp_path)

    result = run_phase1_worker(
        spec.run_id,
        threading.Event(),
        path=db_path,
        preflight=lambda *_args: (_ for _ in ()).throw(RuntimeError(marker)),
    )

    captured = capsys.readouterr()
    assert result.outcome is WorkerOutcome.PREFLIGHT_FAILED
    assert marker not in repr(result)
    assert marker not in captured.out
    assert marker not in captured.err
    assert _counts(db_path) == (0, 0, 0)


def test_worker_success_returns_hidden_handoff_and_zero_contacts(
    tmp_path: Path,
) -> None:
    marker = b"PUBLIC_C10C_SOURCE_BODY_MARKER"
    db_path, spec, _inventory = _queued_run(
        tmp_path, bodies=(marker,), mode="deep",
    )

    result = run_phase1_worker(
        spec.run_id,
        threading.Event(),
        path=db_path,
        dependencies=Phase1Dependencies(extract=_success_extract),
        preflight=lambda _context, _cancel: _SUCCESS,
    )

    assert result.outcome is WorkerOutcome.PHASE1_HANDOFF
    assert result.handoff is not None
    assert result.handoff.file_count == 1
    assert marker.decode() not in repr(result)
    assert marker not in db_path.read_bytes()
    assert _counts(db_path)[:2] == (1, 0)
    assert release_worker(result.handoff.fence, path=db_path).value == "interrupted"
    assert _counts(db_path)[:2] == (0, 0)


def test_two_workers_race_to_one_phase1_lease_and_loser_opens_no_source(
    tmp_path: Path,
) -> None:
    db_path, spec, _inventory = _queued_run(
        tmp_path, bodies=(b"public text",), mode="deep",
    )
    barrier = threading.Barrier(2)
    extracted = 0
    lock = threading.Lock()
    results: list[WorkerRunResult] = []

    def preflight(_context, _cancel):
        barrier.wait(timeout=3)
        return _SUCCESS

    def extract(source_fd, expected, cancel_check):
        nonlocal extracted
        with lock:
            extracted += 1
        return _success_extract(source_fd, expected, cancel_check)

    def target() -> None:
        results.append(run_phase1_worker(
            spec.run_id,
            threading.Event(),
            path=db_path,
            dependencies=Phase1Dependencies(extract=extract),
            preflight=preflight,
        ))

    workers = [threading.Thread(target=target) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
        assert not worker.is_alive()

    assert sorted(result.outcome.value for result in results) == [
        "lease_busy", "phase1_handoff",
    ]
    assert extracted == 1
    winner = next(result for result in results if result.handoff is not None)
    assert winner.handoff is not None
    assert _counts(db_path)[:2] == (1, 0)
    release_worker(winner.handoff.fence, path=db_path)


def test_worker_durable_cancel_during_parser_returns_closed_cancelled(
    tmp_path: Path,
) -> None:
    from experimental.analyst.extract import ExtractionResult

    db_path, spec, _inventory = _queued_run(tmp_path)
    started = threading.Event()
    results: list[WorkerRunResult] = []

    def extract(_source_fd, _expected, cancel_check):
        started.set()
        deadline = time.monotonic() + 5
        while not cancel_check() and time.monotonic() < deadline:
            time.sleep(0.01)
        return ExtractionResult("cancelled")

    worker = threading.Thread(target=lambda: results.append(run_phase1_worker(
        spec.run_id,
        threading.Event(),
        path=db_path,
        dependencies=Phase1Dependencies(extract=extract),
        preflight=lambda _context, _cancel: _SUCCESS,
    )))
    worker.start()
    assert started.wait(3)
    assert request_cancel(spec.run_id, path=db_path) is not None
    worker.join(5)

    assert not worker.is_alive()
    assert [result.outcome for result in results] == [WorkerOutcome.CANCELLED]
    assert current_lease(path=db_path) is None
    assert _counts(db_path)[:2] == (0, 0)


def test_real_sigterm_during_parser_returns_interrupted_and_releases(
    tmp_path: Path,
) -> None:
    from experimental.analyst.extract import ExtractionResult

    db_path, spec, _inventory = _queued_run(tmp_path)
    started = threading.Event()

    def extract(_source_fd, _expected, cancel_check):
        started.set()
        deadline = time.monotonic() + 5
        while not cancel_check() and time.monotonic() < deadline:
            time.sleep(0.01)
        return ExtractionResult("cancelled")

    def send_signal() -> None:
        assert started.wait(3)
        os.kill(os.getpid(), signal.SIGTERM)

    sender = threading.Thread(target=send_signal)
    sender.start()
    stop = threading.Event()
    with worker_signal_handlers(stop):
        result = run_phase1_worker(
            spec.run_id,
            stop,
            path=db_path,
            dependencies=Phase1Dependencies(extract=extract),
            preflight=lambda _context, _cancel: _SUCCESS,
        )
    sender.join(3)

    assert not sender.is_alive()
    assert result.outcome is WorkerOutcome.INTERRUPTED
    assert stop.is_set()
    assert current_lease(path=db_path) is None
    assert _counts(db_path)[:2] == (0, 0)


@pytest.mark.parametrize("number", [signal.SIGINT, signal.SIGTERM])
def test_worker_signal_handlers_only_set_event_and_restore(
    number: signal.Signals,
) -> None:
    stop = threading.Event()
    previous = signal.getsignal(number)

    with worker_signal_handlers(stop):
        assert signal.getsignal(number) is not previous
        signal.raise_signal(number)
        assert stop.is_set()

    assert signal.getsignal(number) is previous


def test_worker_signal_handlers_reject_non_main_thread() -> None:
    errors: list[BaseException] = []

    def target() -> None:
        try:
            with worker_signal_handlers(threading.Event()):
                pass
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=target)
    worker.start()
    worker.join(3)

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)


@pytest.mark.scenario
def test_parse_longer_than_ten_seconds_keeps_successor_heartbeat_fresh(
    tmp_path: Path,
) -> None:
    db_path, spec, _inventory = _queued_run(
        tmp_path, bodies=(b"public text",), mode="deep",
    )
    started = threading.Event()
    results: list[WorkerRunResult] = []

    def extract(source_fd, expected, cancel_check):
        started.set()
        deadline = time.monotonic() + 10.2
        while time.monotonic() < deadline:
            assert cancel_check() is False
            time.sleep(0.02)
        return _success_extract(source_fd, expected, cancel_check)

    worker = threading.Thread(target=lambda: results.append(run_phase1_worker(
        spec.run_id,
        threading.Event(),
        path=db_path,
        dependencies=Phase1Dependencies(extract=extract),
        preflight=lambda _context, _cancel: _SUCCESS,
    )))
    before = time.monotonic()
    worker.start()
    assert started.wait(3)
    observations: list[tuple[float, int]] = []
    while worker.is_alive():
        lease = current_lease(path=db_path)
        if lease is not None and (
            not observations
            or lease.heartbeat_monotonic_ns != observations[-1][1]
        ):
            observations.append((time.monotonic(), lease.heartbeat_monotonic_ns))
        time.sleep(0.05)
    worker.join(3)

    assert time.monotonic() - before > 10.0
    assert len(observations) >= 6
    assert all(
        later[1] > earlier[1]
        for earlier, later in zip(observations, observations[1:])
    )
    assert max(
        later[0] - earlier[0]
        for earlier, later in zip(observations, observations[1:])
    ) <= 2.25
    assert [item.outcome for item in results] == [WorkerOutcome.PHASE1_HANDOFF]
    assert results[0].handoff is not None
    assert _counts(db_path)[:2] == (1, 0)
    release_worker(results[0].handoff.fence, path=db_path)


@pytest.mark.scenario
@pytest.mark.parametrize("format_name", ["pdf", "docx", "doc", "xls"])
def test_generated_public_format_runs_through_complete_worker_path(
    tmp_path: Path,
    format_name: str,
) -> None:
    from experimental.analyst import extract, worker_preflight

    marker = "public-c10c@example.test"
    try:
        if format_name == "pdf":
            extract.pdf_runtime_binds()
            body = worker_preflight._minimal_pdf(marker)
        elif format_name == "docx":
            extract.ooxml_runtime_binds()
            body = _docx_bytes(marker)
        elif format_name == "doc":
            extract.antiword_runtime_binds()
            body = _generate_legacy_fixture(tmp_path, "doc")
        else:
            extract.antiword_runtime_binds()
            extract.xls_runtime_binds()
            body = _generate_legacy_fixture(tmp_path, "xls")
    except extract.OptionalDependencyUnavailable as exc:
        pytest.skip(f"public {format_name} dependency unavailable: {exc.detail}")
    db_path, spec, _inventory = _queued_run(
        tmp_path / "worker",
        bodies=(body,),
        suffix=f".{format_name}",
        mode="deep",
    )

    result = run_phase1_worker(
        spec.run_id,
        threading.Event(),
        path=db_path,
        preflight=lambda _context, _cancel: _SUCCESS,
    )

    assert result.outcome is WorkerOutcome.PHASE1_HANDOFF
    assert result.handoff is not None
    assert result.handoff.file_count == 1
    assert result.handoff.chunk_count == 1
    conn = open_connection(db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT format_name,stage,work_state FROM analyst_files",
        ).fetchone()
        assert tuple(row) == (format_name, "selected_for_model", "pending")
    finally:
        conn.close()
    assert _counts(db_path)[:2] == (1, 0)
    release_worker(result.handoff.fence, path=db_path)


@pytest.mark.scenario
@pytest.mark.parametrize(
    ("suffix", "body"),
    [
        (".txt", b"Public C10C text parser smoke\n"),
        (".rtf", br"{\rtf1\ansi Public C10C RTF parser smoke\par}"),
    ],
)
def test_public_parser_smoke_after_real_strict_preflight(
    tmp_path: Path,
    suffix: str,
    body: bytes,
) -> None:
    db_path, spec, _inventory = _queued_run(
        tmp_path, bodies=(body,), suffix=suffix, mode="fast",
    )
    context = load_worker_run(spec.run_id, path=db_path)
    capability = preflight_worker(context, lambda: False)
    if not capability.ok:
        pytest.skip(f"strict public sandbox preflight: {capability.status.value}")

    result = run_phase1_worker(
        spec.run_id,
        threading.Event(),
        path=db_path,
        preflight=lambda _context, _cancel: _SUCCESS,
    )

    assert result.outcome is WorkerOutcome.PHASE1_HANDOFF
    assert result.handoff is not None
    assert result.handoff.files == ()
    assert _counts(db_path) == (1, 0, 1)
    release_worker(result.handoff.fence, path=db_path)
