"""C10A worker-context and descriptor-safe source-reopen acceptance tests."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from experimental.analyst.inventory import InventoryFile, inventory_tree
from experimental.analyst.checkpoint import (
    CheckpointError,
    ProvenanceUnit,
    advance_file_stage,
    claim_next_file,
)
from experimental.analyst.lease import claim_worker
from experimental.analyst.models import ANALYST_DEFAULTS, FileStage
from experimental.analyst.process_identity import ProcessIdentity
from experimental.analyst.source_reopen import (
    SourceReopenCancelled,
    SourceReopenError,
    SourceRootIdentity,
    open_inventory_file,
)
from experimental.analyst.sandbox import SandboxResult
from experimental.analyst.state import RunState
from experimental.analyst.store import (
    AnalystStoreError,
    ForkRequired,
    RunSpec,
    create_run,
    initialize_database,
    load_worker_run,
    open_connection,
)
from experimental.analyst.worker_contract import (
    HEARTBEAT_INTERVAL_SECONDS,
    MAX_PHASE1_TASKS,
    SOURCE_IDENTITY_KIND,
    SOURCE_IDENTITY_VERSION,
    WORKER_POLL_SECONDS,
    WorkerContractError,
    WorkerOutcome,
    WorkerRunContext,
    build_source_identity,
    parse_source_identity,
)
from experimental.analyst.worksheet import prompt_template_hash, schema_hash


def _inventory(root: Path, relative: str = "nested/public.txt"):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("PUBLIC_C10A_MARKER\n", encoding="utf-8")
    inventory = inventory_tree(root)
    assert len(inventory.files) == 1
    return inventory, inventory.files[0], target


def _fd_count() -> int:
    return len(os.listdir(f"/proc/{os.getpid()}/fd"))


def _forged_file(path: str, observed: os.stat_result) -> InventoryFile:
    return InventoryFile(
        relative_path=path,
        size=observed.st_size,
        mtime_ns=observed.st_mtime_ns,
        ctime_ns=observed.st_ctime_ns,
        device=observed.st_dev,
        inode=observed.st_ino,
        mode=stat.S_IMODE(observed.st_mode),
        sha256="0" * 64,
    )


def _valid_run(tmp_path: Path, *, run_id: str = "public-c10a"):
    root = tmp_path / "source"
    inventory, _expected, _target = _inventory(root)
    db_path = tmp_path / "state" / "analyst.db"
    initialize_database(db_path)
    spec = RunSpec(
        run_id=run_id,
        mode="fast",
        source_mode="unknown",
        source_root=str(root),
        output_root=str(tmp_path / "output"),
        source_identity=build_source_identity(inventory),
        report_label="Public C10A run",
        model_tag=ANALYST_DEFAULTS.model_tag,
        model_digest=ANALYST_DEFAULTS.model_digest,
        worksheet_version=ANALYST_DEFAULTS.worksheet_version,
        prompt_sha256=prompt_template_hash(),
        response_schema_sha256=schema_hash(),
        detector_rules_version="rules-v1",
        detector_rules_sha256="d" * 64,
        parser_bundle={"bundle": "public-c10a", "version": 1},
        chunk_chars=ANALYST_DEFAULTS.chunk_chars,
        overlap_chars=ANALYST_DEFAULTS.overlap_chars,
        num_ctx=ANALYST_DEFAULTS.num_ctx,
        num_predict=ANALYST_DEFAULTS.num_predict,
        isolation_mode="strict",
        reduced_isolation_ack=False,
    )
    create_run(spec, inventory, path=db_path, now_utc="2026-08-16T12:00:00Z")
    return db_path, spec, inventory


def _raw_update(db_path: Path, assignment: str, values: tuple[object, ...]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"UPDATE analyst_runs SET {assignment}", values)
        conn.commit()
    finally:
        conn.close()


def _canonical(value: object) -> tuple[str, str]:
    body = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    )
    return body, hashlib.sha256(body.encode("utf-8")).hexdigest()


def _expected_for_fd(fd: int) -> InventoryFile:
    observed = os.fstat(fd)
    body = os.pread(fd, observed.st_size, 0)
    return InventoryFile(
        relative_path="public.bin",
        size=observed.st_size,
        mtime_ns=observed.st_mtime_ns,
        ctime_ns=observed.st_ctime_ns,
        device=observed.st_dev,
        inode=observed.st_ino,
        mode=stat.S_IMODE(observed.st_mode),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def _claimed_file(tmp_path: Path, *, run_id: str = "public-candidate"):
    db_path, _spec, _inventory_result = _valid_run(tmp_path, run_id=run_id)
    fence = claim_worker(
        run_id,
        ProcessIdentity(7001, 8002, "12345678-1234-5678-1234-567812345678"),
        owner_token="a" * 64,
        heartbeat_monotonic_ns=1,
        now_utc="2026-08-16T12:00:01Z",
        path=db_path,
    )
    assert fence is not None
    claim = claim_next_file(
        fence, now_utc="2026-08-16T12:00:02Z", path=db_path,
    )
    assert claim is not None
    return db_path, fence, claim


def _refinement_evidence(format_name: str):
    if format_name == "docx":
        provenance = (ProvenanceUnit("paragraph", "main#p1", 0, 3),)
        counts = {
            "text_bytes": 3, "text_chars": 3, "logical_unit_count": 1,
            "primary_unit_count": 1, "member_count": 1, "expanded_bytes": 3,
        }
    elif format_name == "xlsx":
        provenance = (ProvenanceUnit("cell", "sheet-1!A1", 0, 3),)
        counts = {
            "text_bytes": 3, "text_chars": 3, "logical_unit_count": 1,
            "primary_unit_count": 1, "member_count": 1, "expanded_bytes": 3,
        }
    elif format_name == "pptx":
        provenance = (ProvenanceUnit("slide", "slide-1", 0, 3),)
        counts = {
            "text_bytes": 3, "text_chars": 3, "logical_unit_count": 1,
            "primary_unit_count": 1, "member_count": 1, "expanded_bytes": 3,
        }
    elif format_name == "doc":
        provenance = (ProvenanceUnit("output_line", "output-line-1", 0, 3),)
        counts = {
            "text_bytes": 3, "text_chars": 3, "logical_unit_count": 1,
        }
    elif format_name == "xls":
        provenance = (ProvenanceUnit("cell", "sheet-1!A1", 0, 3),)
        counts = {
            "text_bytes": 3, "text_chars": 3, "logical_unit_count": 1,
            "primary_unit_count": 1, "worksheet_count": 1,
            "skipped_sheet_count": 0, "dense_cell_count": 1,
        }
    else:
        raise AssertionError(format_name)
    parser = {
        "parser": {
            "docx": "defusedxml", "xlsx": "defusedxml", "pptx": "defusedxml",
            "doc": "antiword", "xls": "python_calamine",
        }[format_name],
    }
    return parser, counts, provenance


def test_source_root_identity_from_inventory_is_exact(tmp_path: Path) -> None:
    root = tmp_path / "root"
    inventory, _expected, _target = _inventory(root)

    identity = SourceRootIdentity.from_inventory(inventory)

    assert (identity.device, identity.inode, identity.mount_id) == (
        inventory.root_device,
        inventory.root_inode,
        inventory.root_mount_id,
    )
    with pytest.raises(TypeError):
        SourceRootIdentity.from_inventory(object())  # type: ignore[arg-type]
    for field in ("device", "inode", "mount_id"):
        with pytest.raises(ValueError):
            replace(identity, **{field: -1})
        with pytest.raises(ValueError):
            replace(identity, **{field: True})


def test_open_inventory_file_exact_success_owns_and_closes_fd(tmp_path: Path) -> None:
    root = tmp_path / "root"
    inventory, expected, _target = _inventory(root)
    identity = SourceRootIdentity.from_inventory(inventory)
    before = _fd_count()

    handle = open_inventory_file(root, identity, expected)
    fd = handle.fileno()
    assert os.get_inheritable(fd) is False
    assert os.read(fd, expected.size) == b"PUBLIC_C10A_MARKER\n"
    handle.close()
    handle.close()

    with pytest.raises(SourceReopenError, match="closed"):
        handle.fileno()
    assert _fd_count() == before


def test_open_inventory_file_context_manager_closes_after_exception(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    inventory, expected, _target = _inventory(root)
    identity = SourceRootIdentity.from_inventory(inventory)
    captured_fd = -1

    with pytest.raises(RuntimeError, match="test exit"):
        with open_inventory_file(root, identity, expected) as handle:
            captured_fd = handle.fileno()
            raise RuntimeError("test exit")

    with pytest.raises(OSError):
        os.fstat(captured_fd)


@pytest.mark.parametrize("placement", ["root", "component", "leaf"])
def test_open_inventory_file_rejects_symlink_at_every_path_layer(
    tmp_path: Path, placement: str,
) -> None:
    real_root = tmp_path / "real-root"
    inventory, expected, target = _inventory(real_root)
    identity = SourceRootIdentity.from_inventory(inventory)

    if placement == "root":
        selected_root = tmp_path / "root-link"
        selected_root.symlink_to(real_root, target_is_directory=True)
    elif placement == "component":
        selected_root = tmp_path / "component-root"
        selected_root.mkdir()
        (selected_root / "nested").symlink_to(
            target.parent, target_is_directory=True,
        )
        selected_stat = selected_root.stat()
        identity = replace(
            identity, device=selected_stat.st_dev, inode=selected_stat.st_ino,
        )
    else:
        selected_root = tmp_path / "leaf-root"
        (selected_root / "nested").mkdir(parents=True)
        (selected_root / expected.relative_path).symlink_to(target)
        selected_stat = selected_root.stat()
        identity = replace(
            identity, device=selected_stat.st_dev, inode=selected_stat.st_ino,
        )

    before = _fd_count()
    with pytest.raises(SourceReopenError):
        open_inventory_file(selected_root, identity, expected)
    assert _fd_count() == before


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "/absolute.txt",
        ".",
        "..",
        "./public.txt",
        "nested/../public.txt",
        "nested//public.txt",
        "nested\\public.txt",
        "nested/public.txt\x00suffix",
    ],
)
def test_open_inventory_file_rejects_noncanonical_relative_paths(
    tmp_path: Path, relative: str,
) -> None:
    root = tmp_path / "root"
    inventory, expected, _target = _inventory(root)
    identity = SourceRootIdentity.from_inventory(inventory)

    with pytest.raises(ValueError):
        open_inventory_file(root, identity, replace(expected, relative_path=relative))


@pytest.mark.parametrize(
    "root_factory",
    [
        lambda root: Path("relative/root"),
        lambda root: Path(str(root) + "/../root"),
        lambda root: Path("//tmp/double-root"),
        lambda root: Path(str(root) + "\\alias"),
        lambda root: Path(str(root) + "\x00suffix"),
    ],
)
def test_open_inventory_file_rejects_noncanonical_roots(
    tmp_path: Path, root_factory,
) -> None:
    root = tmp_path / "root"
    inventory, expected, _target = _inventory(root)
    identity = SourceRootIdentity.from_inventory(inventory)

    with pytest.raises((TypeError, ValueError)):
        open_inventory_file(root_factory(root), identity, expected)


@pytest.mark.parametrize("field", ["device", "inode", "mount_id"])
def test_open_inventory_file_rejects_root_identity_drift(
    tmp_path: Path, field: str,
) -> None:
    root = tmp_path / "root"
    inventory, expected, _target = _inventory(root)
    identity = SourceRootIdentity.from_inventory(inventory)
    drifted = replace(identity, **{field: getattr(identity, field) + 1})
    before = _fd_count()

    with pytest.raises(SourceReopenError, match="root identity"):
        open_inventory_file(root, drifted, expected)
    assert _fd_count() == before


def test_open_inventory_file_rejects_nested_mount_crossing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experimental.analyst import source_reopen

    root = tmp_path / "root"
    inventory, expected, target = _inventory(root)
    identity = SourceRootIdentity.from_inventory(inventory)
    nested_inode = target.parent.stat().st_ino
    real_mount_id = source_reopen._mount_id

    def drift_nested(fd: int) -> int:
        value = real_mount_id(fd)
        return value + 1 if os.fstat(fd).st_ino == nested_inode else value

    monkeypatch.setattr(source_reopen, "_mount_id", drift_nested)
    with pytest.raises(SourceReopenError, match="nested mount"):
        open_inventory_file(root, identity, expected)


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_open_inventory_file_rejects_special_leaf(
    tmp_path: Path, kind: str,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "special"
    if kind == "directory":
        target.mkdir()
    else:
        os.mkfifo(target)
    observed = target.stat()
    inventory = inventory_tree(root)
    root_identity = SourceRootIdentity.from_inventory(inventory)
    expected = _forged_file("special", observed)

    with pytest.raises(SourceReopenError):
        open_inventory_file(root, root_identity, expected)


@pytest.mark.parametrize("mutation", ["size", "mtime", "mode"])
def test_open_inventory_file_rejects_metadata_drift(
    tmp_path: Path, mutation: str,
) -> None:
    root = tmp_path / "root"
    inventory, expected, target = _inventory(root)
    identity = SourceRootIdentity.from_inventory(inventory)
    if mutation == "size":
        target.write_bytes(target.read_bytes() + b"x")
    elif mutation == "mtime":
        os.utime(target, ns=(target.stat().st_atime_ns, expected.mtime_ns + 1_000_000))
    else:
        target.chmod(0o600 if expected.mode != 0o600 else 0o640)

    with pytest.raises(SourceReopenError, match="file identity"):
        open_inventory_file(root, identity, expected)


def test_open_inventory_file_rejects_hash_drift_even_when_metadata_is_expected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    inventory, expected, target = _inventory(root)
    identity = SourceRootIdentity.from_inventory(inventory)
    target.write_bytes(b"X" * expected.size)
    observed = target.stat()
    forged = replace(
        expected,
        mtime_ns=observed.st_mtime_ns,
        ctime_ns=observed.st_ctime_ns,
        sha256=expected.sha256,
    )

    with pytest.raises(SourceReopenError, match="content changed"):
        open_inventory_file(root, identity, forged)


def test_open_inventory_file_detects_leaf_binding_swap_after_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experimental.analyst import source_reopen

    root = tmp_path / "root"
    inventory, expected, target = _inventory(root)
    identity = SourceRootIdentity.from_inventory(inventory)
    real_hash = source_reopen._hash_fd

    def hash_then_swap(fd: int, cancel_check) -> str:
        digest = real_hash(fd, cancel_check)
        target.rename(target.with_suffix(".old"))
        target.write_bytes(b"Y" * expected.size)
        return digest

    monkeypatch.setattr(source_reopen, "_hash_fd", hash_then_swap)
    with pytest.raises(SourceReopenError, match="(?:identity|binding) changed"):
        open_inventory_file(root, identity, expected)


def test_open_inventory_file_cancellation_is_closed_and_leak_free(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    inventory, expected, _target = _inventory(root)
    identity = SourceRootIdentity.from_inventory(inventory)
    before = _fd_count()

    with pytest.raises(SourceReopenCancelled, match="cancelled"):
        open_inventory_file(root, identity, expected, cancel_check=lambda: True)

    assert _fd_count() == before


def test_open_inventory_file_rejects_forged_identity_types_without_opening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experimental.analyst import source_reopen

    root = tmp_path / "root"
    inventory, expected, _target = _inventory(root)
    calls = 0
    real_open = source_reopen.os.open

    def counting_open(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_open(*args, **kwargs)

    monkeypatch.setattr(source_reopen.os, "open", counting_open)
    with pytest.raises(TypeError):
        open_inventory_file(root, object(), expected)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        open_inventory_file(root, SourceRootIdentity.from_inventory(inventory), object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        open_inventory_file(
            root,
            SourceRootIdentity.from_inventory(inventory),
            expected,
            cancel_check=True,  # type: ignore[arg-type]
        )
    assert calls == 0


def test_inventory_sha_fixture_is_exact(tmp_path: Path) -> None:
    root = tmp_path / "root"
    inventory, expected, _target = _inventory(root)

    assert expected.sha256 == hashlib.sha256(b"PUBLIC_C10A_MARKER\n").hexdigest()
    assert SourceRootIdentity.from_inventory(inventory).mount_id >= 0


def test_worker_contract_constants_and_outcomes_are_exact() -> None:
    assert SOURCE_IDENTITY_KIND == "analyst-source-root"
    assert SOURCE_IDENTITY_VERSION == 1
    assert MAX_PHASE1_TASKS == 4
    assert WORKER_POLL_SECONDS == 1.0
    assert HEARTBEAT_INTERVAL_SECONDS == 2.0
    assert {item.value for item in WorkerOutcome} == {
        "phase1_handoff",
        "cancelled",
        "lease_busy",
        "preflight_failed",
        "run_invalid",
        "internal_error",
    }


def test_build_and_parse_source_identity_round_trip_exactly(tmp_path: Path) -> None:
    root = tmp_path / "root"
    inventory, _expected, _target = _inventory(root)

    value = build_source_identity(inventory)

    assert tuple(value) == (
        "kind", "root_device", "root_inode", "root_mount_id", "version",
    )
    assert value == {
        "kind": "analyst-source-root",
        "root_device": inventory.root_device,
        "root_inode": inventory.root_inode,
        "root_mount_id": inventory.root_mount_id,
        "version": 1,
    }
    assert parse_source_identity(value) == SourceRootIdentity.from_inventory(inventory)
    with pytest.raises(TypeError):
        build_source_identity(object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "other"),
        ("kind", True),
        ("version", 2),
        ("version", True),
        ("root_device", -1),
        ("root_device", True),
        ("root_inode", 0),
        ("root_inode", True),
        ("root_mount_id", 0),
        ("root_mount_id", True),
    ],
)
def test_parse_source_identity_rejects_value_and_type_drift(
    tmp_path: Path, field: str, value: object,
) -> None:
    root = tmp_path / "root"
    inventory, _expected, _target = _inventory(root)
    source = build_source_identity(inventory)
    source[field] = value

    with pytest.raises(WorkerContractError):
        parse_source_identity(source)


def test_parse_source_identity_rejects_missing_extra_and_nonmapping(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    inventory, _expected, _target = _inventory(root)
    source = build_source_identity(inventory)
    missing = dict(source)
    missing.pop("kind")
    extra = {**source, "extra": 1}

    for value in (missing, extra, [], "source"):
        with pytest.raises(WorkerContractError):
            parse_source_identity(value)  # type: ignore[arg-type]


def test_build_source_identity_rejects_unrunnable_root_numbers() -> None:
    from experimental.analyst.inventory import InventoryResult

    for inode, mount_id in ((0, 1), (1, 0), (-1, 1), (1, -1)):
        inventory = InventoryResult(1, inode, mount_id, (), ())
        with pytest.raises(WorkerContractError):
            build_source_identity(inventory)


def test_load_worker_run_returns_exact_typed_read_only_context(
    tmp_path: Path,
) -> None:
    db_path, spec, inventory = _valid_run(tmp_path)
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    context = load_worker_run(spec.run_id, path=db_path)

    assert isinstance(context, WorkerRunContext)
    assert context.run_id == spec.run_id
    assert context.observed_state is RunState.READY
    assert context.observed_revision == 0
    assert context.mode == "fast"
    assert context.source_mode == "unknown"
    assert context.source_root == spec.source_root
    assert context.output_root == spec.output_root
    assert context.root_identity == SourceRootIdentity.from_inventory(inventory)
    assert context.model_tag == ANALYST_DEFAULTS.model_tag
    assert context.model_digest == ANALYST_DEFAULTS.model_digest
    assert context.worksheet_version == ANALYST_DEFAULTS.worksheet_version
    assert context.prompt_sha256 == prompt_template_hash()
    assert context.response_schema_sha256 == schema_hash()
    assert context.detector_rules_version == "rules-v1"
    assert context.chunk_chars == 8000
    assert context.overlap_chars == 256
    assert context.num_ctx == 8192
    assert context.num_predict == 1024
    assert context.isolation_mode == "strict"
    assert context.reduced_isolation_ack is False
    assert context.host_type is None
    assert context.protocol_server_id is None
    assert context.ip_address is None
    assert context.port is None
    assert context.extract_summary_row_id is None
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before

    rendered = repr(context)
    for private in (
        spec.source_root,
        spec.output_root,
        spec.report_label,
        context.parser_bundle_json,
    ):
        assert private not in rendered


def test_load_worker_run_missing_row_is_content_free(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "analyst.db"
    initialize_database(db_path)

    with pytest.raises(AnalystStoreError, match="does not exist") as captured:
        load_worker_run("missing-public-run", path=db_path)

    assert "missing-public-run" not in str(captured.value)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("model_tag", "qwen3.6:35b"),
        ("model_digest", "f" * 64),
        ("worksheet_version", "v1"),
        ("chunk_chars", 7999),
        ("overlap_chars", 255),
        ("num_ctx", 8191),
        ("num_predict", 1023),
    ],
)
def test_load_worker_run_rejects_default_type_and_isolation_drift(
    tmp_path: Path, column: str, value: object,
) -> None:
    db_path, spec, _inventory_result = _valid_run(tmp_path)
    _raw_update(db_path, f"{column}=?", (value,))
    before = db_path.read_bytes()

    with pytest.raises(ForkRequired):
        load_worker_run(spec.run_id, path=db_path)

    assert db_path.read_bytes() == before


def test_schema_rejects_impossible_worker_enum_and_split_isolation_drift(
    tmp_path: Path,
) -> None:
    for assignment, values in (
        ("mode=?", ("wide",)),
        ("source_mode=?", ("legacy",)),
        ("isolation_mode=?", ("reduced",)),
        ("reduced_isolation_ack=?", (1,)),
    ):
        db_path, _spec, _inventory_result = _valid_run(
            tmp_path / hashlib.sha256(assignment.encode()).hexdigest()[:8],
        )
        with pytest.raises(sqlite3.IntegrityError):
            _raw_update(db_path, assignment, values)


def test_load_worker_run_rejects_schema_valid_reduced_isolation_pair(
    tmp_path: Path,
) -> None:
    db_path, spec, _inventory_result = _valid_run(tmp_path)
    _raw_update(
        db_path,
        "isolation_mode=?,reduced_isolation_ack=?",
        ("reduced", 1),
    )

    with pytest.raises(ForkRequired):
        load_worker_run(spec.run_id, path=db_path)


@pytest.mark.parametrize(
    "value",
    [
        "relative/source",
        "/tmp/../source",
        "/tmp/source\\alias",
        "//tmp/source",
        "/tmp/source\x00tail",
    ],
)
def test_load_worker_run_rejects_noncanonical_source_root(
    tmp_path: Path, value: str,
) -> None:
    db_path, spec, _inventory_result = _valid_run(tmp_path)
    _raw_update(db_path, "source_root=?", (value,))

    with pytest.raises(ForkRequired):
        load_worker_run(spec.run_id, path=db_path)


@pytest.mark.parametrize("identity_column", ["source", "parser"])
def test_load_worker_run_rejects_identity_hash_mismatch(
    tmp_path: Path, identity_column: str,
) -> None:
    db_path, spec, _inventory_result = _valid_run(tmp_path)
    column = f"{identity_column}_identity_sha256" if identity_column == "source" else "parser_bundle_sha256"
    _raw_update(db_path, f"{column}=?", ("0" * 64,))

    with pytest.raises(ForkRequired):
        load_worker_run(spec.run_id, path=db_path)


@pytest.mark.parametrize(
    "body",
    [
        "[]",
        "{",
        '{"kind":"analyst-source-root","kind":"duplicate"}',
        '{"number":NaN}',
        '{ "kind": "analyst-source-root" }',
        '{"kind":"analyst-source-root","root_device":1,"root_inode":2,'
        '"root_mount_id":3,"version":1,"extra":true}',
    ],
)
def test_load_worker_run_rejects_forged_source_json_even_with_matching_hash(
    tmp_path: Path, body: str,
) -> None:
    db_path, spec, _inventory_result = _valid_run(tmp_path)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    _raw_update(
        db_path,
        "source_identity_json=?,source_identity_sha256=?",
        (body, digest),
    )

    with pytest.raises(ForkRequired):
        load_worker_run(spec.run_id, path=db_path)


@pytest.mark.parametrize(
    "body",
    [
        "[]",
        "{",
        '{"bundle":"a","bundle":"b"}',
        '{"number":Infinity}',
        '{ "bundle": "public-c10a", "version": 1 }',
    ],
)
def test_load_worker_run_rejects_forged_parser_json_even_with_matching_hash(
    tmp_path: Path, body: str,
) -> None:
    db_path, spec, _inventory_result = _valid_run(tmp_path)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    _raw_update(
        db_path,
        "parser_bundle_json=?,parser_bundle_sha256=?",
        (body, digest),
    )

    with pytest.raises(ForkRequired):
        load_worker_run(spec.run_id, path=db_path)


def test_load_worker_run_rejects_legacy_synthetic_source_identity(
    tmp_path: Path,
) -> None:
    db_path, spec, _inventory_result = _valid_run(tmp_path)
    body, digest = _canonical({"kind": "public-synthetic", "version": 1})
    _raw_update(
        db_path,
        "source_identity_json=?,source_identity_sha256=?",
        (body, digest),
    )

    with pytest.raises(ForkRequired):
        load_worker_run(spec.run_id, path=db_path)


def test_worker_run_context_rejects_bool_numeric_counterfeit(tmp_path: Path) -> None:
    db_path, spec, _inventory_result = _valid_run(tmp_path)
    context = load_worker_run(spec.run_id, path=db_path)

    for field in (
        "observed_revision", "chunk_chars", "overlap_chars", "num_ctx", "num_predict",
    ):
        with pytest.raises(WorkerContractError):
            replace(context, **{field: True})


def test_worker_run_context_rejects_forged_root_type(tmp_path: Path) -> None:
    db_path, spec, _inventory_result = _valid_run(tmp_path)
    context = load_worker_run(spec.run_id, path=db_path)

    with pytest.raises(WorkerContractError):
        replace(context, root_identity=object())


@pytest.mark.parametrize(
    ("body", "expected_format"),
    [
        (b"public plain text\n", "text"),
        (b"{\\rtf1 public}", "rtf"),
        (b"%PDF-1.7\npublic", "pdf"),
        (b"PK\x03\x04" + b"\x00" * 32, "ooxml"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32, "legacy_office"),
    ],
)
@pytest.mark.parametrize(
    "reason",
    ["parse_timeout", "parse_oom", "parser_output_limit", "sandbox_error", "cancelled"],
)
def test_extract_preserves_sniffed_format_on_every_sandbox_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    expected_format: str,
    reason: str,
) -> None:
    from experimental.analyst import extract

    source = tmp_path / "public.bin"
    source.write_bytes(body)
    monkeypatch.setattr(extract, "python_runtime_binds", lambda: ())
    monkeypatch.setattr(extract, "pdf_runtime_binds", lambda: ())
    monkeypatch.setattr(extract, "ooxml_runtime_binds", lambda: ())
    monkeypatch.setattr(extract, "antiword_runtime_binds", lambda: ())
    monkeypatch.setattr(extract, "xls_runtime_binds", lambda: ())
    monkeypatch.setattr(
        extract,
        "run_sandboxed",
        lambda **_kwargs: SandboxResult(reason, None, b"", b"", "public-unit"),
    )
    fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC)
    try:
        result = extract.extract_document(source_fd=fd, expected=_expected_for_fd(fd))
    finally:
        os.close(fd)

    assert result.reason == reason
    assert result.format_name == expected_format
    assert result.text is None


@pytest.mark.parametrize(
    ("body", "expected_format", "failed_runtime"),
    [
        (b"public plain text\n", "text", "python_runtime_binds"),
        (b"{\\rtf1 public}", "rtf", "python_runtime_binds"),
        (b"%PDF-1.7\npublic", "pdf", "pdf_runtime_binds"),
        (b"PK\x03\x04" + b"\x00" * 32, "ooxml", "ooxml_runtime_binds"),
        (
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32,
            "legacy_office",
            "antiword_runtime_binds",
        ),
    ],
)
def test_extract_preserves_candidate_on_generic_runtime_bind_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    expected_format: str,
    failed_runtime: str,
) -> None:
    from experimental.analyst import extract

    source = tmp_path / "public.bin"
    source.write_bytes(body)
    for name in (
        "python_runtime_binds", "pdf_runtime_binds", "ooxml_runtime_binds",
        "antiword_runtime_binds", "xls_runtime_binds",
    ):
        monkeypatch.setattr(extract, name, lambda: ())

    def unavailable():
        raise RuntimeError("PRIVATE_RUNTIME_EXCEPTION_MUST_NOT_ESCAPE")

    monkeypatch.setattr(extract, failed_runtime, unavailable)
    if expected_format == "legacy_office":
        monkeypatch.setattr(extract, "xls_runtime_binds", unavailable)
    fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC)
    try:
        result = extract.extract_document(source_fd=fd, expected=_expected_for_fd(fd))
    finally:
        os.close(fd)

    assert (result.reason, result.format_name, result.detail) == (
        "sandbox_unavailable", expected_format, None,
    )
    assert "PRIVATE_RUNTIME" not in repr(result)


@pytest.mark.parametrize(
    ("body", "expected_format"),
    [
        (b"public plain text\n", "text"),
        (b"{\\rtf1 public}", "rtf"),
        (b"%PDF-1.7\npublic", "pdf"),
        (b"PK\x03\x04" + b"\x00" * 32, "ooxml"),
    ],
)
def test_extract_preserves_candidate_on_malformed_parser_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    expected_format: str,
) -> None:
    from experimental.analyst import extract

    source = tmp_path / "public.bin"
    source.write_bytes(body)
    monkeypatch.setattr(extract, "python_runtime_binds", lambda: ())
    monkeypatch.setattr(extract, "pdf_runtime_binds", lambda: ())
    monkeypatch.setattr(extract, "ooxml_runtime_binds", lambda: ())
    monkeypatch.setattr(
        extract,
        "run_sandboxed",
        lambda **_kwargs: SandboxResult(
            "success", 0, b"COMPROMISED_PUBLIC_FRAME", b"", "public-unit",
        ),
    )
    fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC)
    try:
        result = extract.extract_document(source_fd=fd, expected=_expected_for_fd(fd))
    finally:
        os.close(fd)

    assert result.reason == "parse_error"
    assert result.format_name == expected_format
    assert result.text is None


def test_extract_preserves_legacy_candidate_on_malformed_doc_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experimental.analyst import extract

    source = tmp_path / "public.doc"
    source.write_bytes(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32,
    )
    monkeypatch.setattr(extract, "antiword_runtime_binds", lambda: ())
    monkeypatch.setattr(
        extract,
        "run_sandboxed",
        lambda **_kwargs: SandboxResult(
            "success", 0, b"COMPROMISED_DOC_FRAME", b"", "public-unit",
        ),
    )
    fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC)
    try:
        result = extract.extract_document(source_fd=fd, expected=_expected_for_fd(fd))
    finally:
        os.close(fd)

    assert (result.reason, result.format_name, result.text) == (
        "parse_error", "legacy_office", None,
    )


def test_extract_preserves_legacy_candidate_on_malformed_xls_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experimental.analyst import extract
    from experimental.analyst.legacy_contract import FRAME_MAGIC as LEGACY_MAGIC

    source = tmp_path / "public.xls"
    source.write_bytes(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32,
    )
    monkeypatch.setattr(extract, "antiword_runtime_binds", lambda: ())
    monkeypatch.setattr(extract, "xls_runtime_binds", lambda: ())
    doc_header = json.dumps(
        {
            "antiword_version": "0.37",
            "detail": "not_word_binary",
            "format": None,
            "logical_unit_count": 0,
            "package_revision": "0.37-17",
            "status": "unsupported_format",
            "text_bytes": 0,
            "text_chars": 0,
            "units": [],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    responses = iter(
        (
            SandboxResult(
                "success", 0, LEGACY_MAGIC + doc_header + b"\n", b"", "doc-unit",
            ),
            SandboxResult(
                "success", 0, b"COMPROMISED_XLS_FRAME", b"", "xls-unit",
            ),
        )
    )
    monkeypatch.setattr(extract, "run_sandboxed", lambda **_kwargs: next(responses))
    fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC)
    try:
        result = extract.extract_document(source_fd=fd, expected=_expected_for_fd(fd))
    finally:
        os.close(fd)

    assert (result.reason, result.format_name, result.text) == (
        "parse_error", "legacy_office", None,
    )


@pytest.mark.parametrize(
    ("candidate", "authenticated"),
    [
        ("ooxml", "docx"),
        ("ooxml", "xlsx"),
        ("ooxml", "pptx"),
        ("legacy_office", "doc"),
        ("legacy_office", "xls"),
    ],
)
def test_checkpoint_refines_only_authenticated_candidate_subtypes(
    tmp_path: Path, candidate: str, authenticated: str,
) -> None:
    db_path, fence, claim = _claimed_file(
        tmp_path, run_id=f"public-{authenticated}",
    )
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.FORMAT_IDENTIFIED,
        format_name=candidate,
        path=db_path,
    )
    parser, counts, provenance = _refinement_evidence(authenticated)

    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.TEXT_EXTRACTED,
        authenticated_format_name=authenticated,
        encoding="utf-8",
        parser_identity=parser,
        extraction_meta=counts,
        provenance=provenance,
        path=db_path,
    )

    conn = open_connection(db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT stage,format_name,encoding FROM analyst_files WHERE file_id=?",
            (claim.file_id,),
        ).fetchone()
        assert tuple(row) == ("text_extracted", authenticated, "utf-8")
        units = conn.execute(
            "SELECT kind,label,start_char,end_char FROM analyst_provenance_units "
            "WHERE file_id=? ORDER BY ordinal",
            (claim.file_id,),
        ).fetchall()
        assert tuple(tuple(unit) for unit in units) == tuple(
            (unit.kind, unit.label, unit.start, unit.end) for unit in provenance
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("candidate", "contradiction"),
    [
        ("ooxml", None),
        ("ooxml", "doc"),
        ("ooxml", "xls"),
        ("ooxml", "text"),
        ("legacy_office", None),
        ("legacy_office", "docx"),
        ("legacy_office", "xlsx"),
        ("legacy_office", "pptx"),
        ("legacy_office", "pdf"),
    ],
)
def test_checkpoint_candidate_refinement_contradiction_is_atomic(
    tmp_path: Path, candidate: str, contradiction: str | None,
) -> None:
    db_path, fence, claim = _claimed_file(
        tmp_path,
        run_id=f"public-bad-{candidate.replace('_', '-')}-{contradiction or 'none'}",
    )
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.FORMAT_IDENTIFIED,
        format_name=candidate,
        path=db_path,
    )
    parser, counts, provenance = _refinement_evidence(
        contradiction if contradiction in {"docx", "xlsx", "pptx", "doc", "xls"}
        else "docx"
    )

    with pytest.raises((CheckpointError, ValueError)):
        advance_file_stage(
            fence,
            claim.file_id,
            FileStage.TEXT_EXTRACTED,
            authenticated_format_name=contradiction,
            encoding="utf-8",
            parser_identity=parser,
            extraction_meta=counts,
            provenance=provenance,
            path=db_path,
        )

    conn = open_connection(db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT stage,format_name,parser_identity_json,extraction_meta_json "
            "FROM analyst_files WHERE file_id=?",
            (claim.file_id,),
        ).fetchone()
        assert tuple(row) == ("format_identified", candidate, None, None)
        assert conn.execute(
            "SELECT count(*) FROM analyst_provenance_units WHERE file_id=?",
            (claim.file_id,),
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_checkpoint_exact_format_rejects_conflicting_authentication(
    tmp_path: Path,
) -> None:
    db_path, fence, claim = _claimed_file(tmp_path, run_id="public-exact-format")
    advance_file_stage(
        fence,
        claim.file_id,
        FileStage.FORMAT_IDENTIFIED,
        format_name="docx",
        path=db_path,
    )
    parser, counts, provenance = _refinement_evidence("docx")

    with pytest.raises(CheckpointError, match="contradicts"):
        advance_file_stage(
            fence,
            claim.file_id,
            FileStage.TEXT_EXTRACTED,
            authenticated_format_name="xlsx",
            encoding="utf-8",
            parser_identity=parser,
            extraction_meta=counts,
            provenance=provenance,
            path=db_path,
        )


def test_checkpoint_format_candidate_cannot_claim_authenticated_success_directly(
    tmp_path: Path,
) -> None:
    db_path, fence, claim = _claimed_file(tmp_path, run_id="public-candidate-shape")

    with pytest.raises(ValueError):
        advance_file_stage(
            fence,
            claim.file_id,
            FileStage.FORMAT_IDENTIFIED,
            format_name="ooxml",
            authenticated_format_name="docx",
            path=db_path,
        )
