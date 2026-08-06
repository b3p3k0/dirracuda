"""Offline run creation and guarded public Stage-C runtime for C0B-2B1.

Public creation, status, and verification never contact Ollama.  The live entrypoint is
separate and imports the bounded transport only after the CLI confirmation gate.

DISPOSITION: benchmark-only diagnostic; remove after accepted C0B artifacts.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import sqlite3
import stat
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import FrameType
from typing import Any, Callable, Mapping
from urllib.parse import quote

from . import chunker, goldset, metrics, report
from .c0b2_checkpoint import (INVOCATION_CAPS, SCHEMA_VERSION, Checkpoint,
                              CheckpointError, RESUMABLE_STATES, RUN_ID_RE,
                              TERMINAL_STATES, canonical_json, sha256_json)
from .c0b2_fsprobe import (GlobalExecutionLock, backup_snapshot,
                           probe_filesystem, status_readonly,
                           verify_connection, verify_readonly)
from .c0b2_leakscan import (FROZEN_C0B2B1_PATHS, WorktreeSeal,
                            capture_worktree_seal)
from .c0b2_plan import (KEEP_ALIVE, MODELS, OPTIONS_C, WorkItem,
                        build_c_stage_plan, build_master_manifest,
                        master_manifest_payload, stage_plan_payload)
from .c0b2_schema import (prompt_template_hash, schema_hash, stable_hash)
from .c0b2_public_schema import (
    AcceptancePlan, BackupAnchor, BackupReceipt, BackupStatus, DInconclusiveResult,
    DPhasePlan, FInconclusiveResult, FMasterPlan, FSeedPlan, FSelectedResult,
    FailureArtifact, FailureEvidence, FAILURE_REASON_BY_TERMINAL, PLAN_ORDER,
    InconclusiveCompletion, PlanActivation, SelectedCompletion, validate_artifact,
)
from .c0b2_runtime_common import runtime_position, runtime_transaction

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = REPO_ROOT / "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B2.md"
OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
OLLAMA_VERSION = "0.32.5"
JOURNAL_MODE = "DELETE"
PUBLIC_LIMITS: Mapping[str, Mapping[str, int]] = {
    "C": {"scored": 264, "schema_retry": 12,
          "preflight_probe": 18, "transport_orphan": 106},
    "D": {"scored": 757, "schema_retry": 64,
          "preflight_probe": 36, "transport_orphan": 93},
    "F": {"scored": 1142, "schema_retry": 14,
          "preflight_probe": 59, "transport_orphan": 185},
}
PUBLIC_CUMULATIVE_CAP = 2750
_BACKUP_REQUIRED_STATES = frozenset({
    "PAUSED_STAGE_BOUNDARY", "SELECTED", "INCONCLUSIVE", "FAILED_SAFETY",
    "BLOCKED_PROVENANCE", "BLOCKED_BUDGET", "BLOCKED_FILESYSTEM", "ABANDONED",
})
class RuntimeGateError(RuntimeError):
    """A frozen public-run identity or stage gate did not hold."""


def new_public_run_id() -> str:
    stamp = time.strftime("c0b2-%Y%m%d-%H%M%S", time.gmtime())
    return f"{stamp}-{secrets.token_hex(12)}"
def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, check=True, capture_output=True,
        text=True, shell=False,
    )
    return result.stdout.strip()
def _task_tree_hash(repo_root: Path) -> str:
    rows: dict[str, str] = {}
    for relative in sorted(FROZEN_C0B2B1_PATHS):
        path = repo_root / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeGateError(f"frozen task path is not a regular file: {relative}")
        rows[relative] = _sha256_file(path)
    return stable_hash(rows)
def _require_clean_task_delta(seal: WorktreeSeal) -> None:
    dirty = tuple(entry.path for entry in seal.entries
                  if entry.path in FROZEN_C0B2B1_PATHS)
    if dirty:
        raise RuntimeGateError(
            "commit the frozen Stage-C implementation before create: " + ", ".join(dirty))
def _manifest_payload(manifest: Any) -> dict[str, Any]:
    return master_manifest_payload(manifest)
def _plan_payload(plan: Any) -> dict[str, Any]:
    return stage_plan_payload(plan)


def _model_generation_hash() -> str:
    return stable_hash([
        {"model": model, "model_digest": digest, "think": think,
         "options": dict(OPTIONS_C), "keep_alive": KEEP_ALIVE}
        for model, digest, think in MODELS
    ])


def _source_pins(repo_root: Path, seal: WorktreeSeal) -> dict[str, Any]:
    detector_hash = stable_hash({
        "metrics.py": _sha256_file(Path(metrics.__file__)),
        "stage_c.py": _sha256_file(
            repo_root / "scripts/analyst_benchmark/c0b2_stage_c.py"),
    })
    return {
        "protocol_sha256": _sha256_file(PROTOCOL_PATH),
        "git_head": _git(repo_root, "rev-parse", "HEAD"),
        "declared_dirty_state_sha256": seal.digest,
        "task_tree_sha256": _task_tree_hash(repo_root),
        "fixture_sha256": _sha256_file(goldset.MANIFEST),
        "schema_sha256": stable_hash({
            "v1": schema_hash("v1"), "v2": schema_hash("v2")}),
        "prompt_sha256": stable_hash({
            "v1": prompt_template_hash("v1"),
            "v2": prompt_template_hash("v2"),
        }),
        "chunker_sha256": _sha256_file(Path(chunker.__file__)),
        "detector_sha256": detector_hash,
        "generation_options_sha256": _model_generation_hash(),
        "worktree_seal_sha256": seal.digest,
        "model_digests": {model: digest for model, digest, _think in MODELS},
        "ollama_endpoint": OLLAMA_ENDPOINT,
        "ollama_version": OLLAMA_VERSION,
    }


def _header(repo_root: Path, seal: WorktreeSeal,
            filesystem: Any, manifest: Any) -> dict[str, Any]:
    if filesystem.selected_mode != JOURNAL_MODE:
        raise RuntimeGateError("canonical filesystem did not pass DELETE+FULL")
    return {
        "run_type": "public",
        "parent_selection_sha256": None,
        "filesystem_selected_mode": JOURNAL_MODE,
        **_source_pins(repo_root, seal),
        "master_manifest_sha256": manifest.sha256,
        "filesystem_capability_sha256": filesystem.capability_sha256,
        "mount": asdict(filesystem.fingerprint),
    }


def revalidate_source_pins(header: Mapping[str, Any], *,
                           repo_root: Path = REPO_ROOT) -> None:
    """Fail before HTTP if code, fixtures, endpoint, or declared dirt changed."""
    current = _source_pins(repo_root, capture_worktree_seal(repo_root))
    changed = tuple(key for key, value in current.items() if header.get(key) != value)
    if changed:
        raise RuntimeGateError("immutable public-run identity drift: " + ", ".join(changed))


def create_public_run(*, repo_root: Path = REPO_ROOT,
                      benchmark_root: Path | None = None,
                      run_id: str | None = None) -> str:
    """Create and snapshot a complete immutable Stage-C plan without network I/O."""
    root = Path(benchmark_root) if benchmark_root is not None else report.bench_root()
    identity = run_id or new_public_run_id()
    if not RUN_ID_RE.fullmatch(identity):
        raise ValueError("invalid public run id")
    for existing in (root, root / "runs"):
        if os.path.lexists(existing):
            st = existing.lstat()
            if (not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode)
                    or st.st_uid != os.getuid()
                    or stat.S_IMODE(st.st_mode) != 0o700):
                raise PermissionError("existing benchmark directories must be exact 0700")
    seal = capture_worktree_seal(repo_root)
    _require_clean_task_delta(seal)

    corpus = goldset.load(verify=True)
    manifest = build_master_manifest(corpus)
    manifest_body = _manifest_payload(manifest)
    if stable_hash(manifest_body) != manifest.sha256:
        raise RuntimeGateError("generated manifest hash is not canonical")
    filesystem = probe_filesystem(root)
    header = _header(repo_root, seal, filesystem, manifest)
    with GlobalExecutionLock(root) as lock:
        point = _resume_public_creation(root, identity, manifest_body, header)
        needs_fsync = False
        if point is None:
            key = secrets.token_bytes(32)
            plan = build_c_stage_plan(key, corpus)
            plan_body = _plan_payload(plan)
            if stable_hash(plan_body) != plan.sha256:
                raise RuntimeGateError("generated Stage-C plan hash is not canonical")
            storage = f".c0b2-initializing-{identity}-{secrets.token_hex(16)}"
            point = Checkpoint.create(
                root, identity, header=header, limits=PUBLIC_LIMITS,
                cumulative_cap=PUBLIC_CUMULATIVE_CAP, journal_mode=JOURNAL_MODE,
                initial_state="INITIALIZING", storage_id=storage)
            try:
                point.promote(identity)
            except Exception as primary:
                try:
                    point.discard_initializing(identity)
                except Exception as cleanup:
                    primary.add_note(f"initializing cleanup failed: {cleanup!r}")
                raise
            try:
                point.conn.execute("BEGIN IMMEDIATE")
                manifest_hash = point.freeze_manifest("master", manifest_body)
                point.freeze_manifest("run_nonce_key", {
                    "version": "c0b2-run-nonce-key-v1", "key_hex": key.hex()})
                plan_hash = point.freeze_plan("C", manifest_hash, plan_body)
                if manifest_hash != manifest.sha256 or plan_hash != plan.sha256:
                    raise RuntimeGateError("checkpoint creation evidence changed")
                for item in plan.work:
                    point.register_work(
                        item.work_id, "C", item.cell_id, item.request_sha256)
                point.conn.execute(
                    "UPDATE run_state SET state='PREPARED',updated=? WHERE id=1",
                    (time.time(),))
                point.conn.commit()
                needs_fsync = True
            except Exception:
                point.conn.rollback()
                if point.state() == "INITIALIZING":
                    point.discard_initializing(identity)
                else:
                    point.close()
                raise
        try:
            if needs_fsync:
                Checkpoint._fsync_file_and_parent(point.path)
            _validate_prepared_creation(point, manifest_body, header, identity)
            _ensure_initial_snapshot(point, lock, identity)
        finally:
            point.close()
    return identity


def _checkpoint_path(run_id: str, benchmark_root: Path | None = None) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid public run id")
    root = Path(benchmark_root) if benchmark_root is not None else report.bench_root()
    return root / "runs" / run_id / "checkpoint.sqlite3"


def _run_nonce_key(point: Checkpoint) -> bytes:
    try:
        _digest, raw = point.load_manifest("run_nonce_key")
        value = json.loads(raw)
        key_hex = value.get("key_hex") if isinstance(value, dict) else None
        if (set(value) != {"version", "key_hex"}
                or value["version"] != "c0b2-run-nonce-key-v1"
                or not isinstance(key_hex, str) or len(key_hex) != 64
                or any(char not in "0123456789abcdef" for char in key_hex)):
            raise ValueError
        key = bytes.fromhex(key_hex)
        parent, digest, plan_raw = point.load_plan("C")
        master_hash = point.load_manifest("master")[0]
        rebuilt = _plan_payload(build_c_stage_plan(key))
        if (parent != master_hash or canonical_json(rebuilt) != plan_raw
                or stable_hash(rebuilt) != digest):
            raise ValueError
        return key
    except Exception as exc:
        raise RuntimeGateError("run nonce key does not reproduce the frozen C plan") from exc


def _validate_prepared_creation(point: Checkpoint,
                                manifest_body: Mapping[str, Any],
                                header: Mapping[str, Any], run_id: str) -> None:
    expected_header = {**dict(header), "schema_version": SCHEMA_VERSION,
                       "journal_mode": JOURNAL_MODE,
                       "cumulative_cap": PUBLIC_CUMULATIVE_CAP, "run_id": run_id,
                       "limits": {stage: dict(classes)
                                  for stage, classes in PUBLIC_LIMITS.items()},
                       "invocation_caps": INVOCATION_CAPS}
    if (point.state() != "PREPARED" or point.conn.execute(
            "SELECT count(*) FROM attempts").fetchone()[0] != 0
            or point.header() != expected_header):
        raise RuntimeGateError("create retry refuses a non-PREPARED or used run")
    master_hash, master_raw = point.load_manifest("master")
    if canonical_json(manifest_body) != master_raw or stable_hash(manifest_body) != master_hash:
        raise RuntimeGateError("create retry master manifest changed")
    _run_nonce_key(point)
    plan = json.loads(point.load_plan("C")[2])
    expected = [(row["work_id"], "C", row["cell_id"], row["request_sha256"])
                for row in plan["work"]]
    rows = point.conn.execute(
        "SELECT work_id,stage,cell_id,request_hash FROM work_items ORDER BY rowid").fetchall()
    empty_tables = ("stage_aggregates", "acceptance_plan", "decisions", "attempts",
                    "model_backoff", "context_obligations", "invocations", "events",
                    "phase_plans", "plan_activations", "phase_aggregates",
                    "phase_work_registry", "runtime_controls", "runtime_control_events",
                    "public_artifacts", "backup_receipts")
    limits = point.header()["limits"]
    expected_classes = sorted(
        (stage, kind, count) for stage, classes in limits.items()
        for kind, count in classes.items())
    expected_stages = sorted((stage, sum(classes.values()))
                             for stage, classes in limits.items())
    if ({row[0] for row in point.conn.execute("SELECT name FROM manifests")}
            != {"master", "run_nonce_key"}
            or {row[0] for row in point.conn.execute("SELECT stage FROM plans")} != {"C"}
            or rows != expected or any(point.conn.execute(
                f"SELECT count(*) FROM {table}").fetchone()[0] for table in empty_tables)
            or point.conn.execute(
                "SELECT id,active_stage,active_plan_key FROM runtime_cursor").fetchall()
            != [(1, "C", "C")]
            or point.conn.execute(
                "SELECT stage,call_class,allowance FROM class_limits ORDER BY 1,2"
            ).fetchall() != expected_classes
            or point.conn.execute(
                "SELECT stage,hard_cap FROM stage_limits ORDER BY 1").fetchall()
            != expected_stages):
        raise RuntimeGateError("create retry work or receipt evidence changed")


def _resume_public_creation(root: Path, run_id: str,
                            manifest: Mapping[str, Any],
                            header: Mapping[str, Any]) -> Checkpoint | None:
    final = _checkpoint_path(run_id, root)
    if os.path.lexists(final.parent):
        if Checkpoint.recover_initializing_storage(root, run_id, run_id):
            return None
        point = Checkpoint.open(final, root)
        _validate_prepared_creation(point, manifest, header, run_id)
        return point
    runs = root / "runs"
    if not runs.exists():
        return None
    prefix = f".c0b2-initializing-{run_id}-"
    candidates = [entry for entry in runs.iterdir() if entry.name.startswith(prefix)]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise RuntimeGateError("create retry found ambiguous staging runs")
    Checkpoint.recover_initializing_storage(root, run_id, candidates[0].name)
    return None


def _ensure_initial_snapshot(point: Checkpoint, lock: GlobalExecutionLock,
                             run_id: str) -> Path:
    parent = point.root / "snapshots"
    if os.path.lexists(parent):
        parent_stat = parent.lstat()
        if (not stat.S_ISDIR(parent_stat.st_mode) or stat.S_ISLNK(parent_stat.st_mode)
                or parent_stat.st_uid != os.getuid()
                or stat.S_IMODE(parent_stat.st_mode) != 0o700):
            raise PermissionError("initial snapshot parent must be exact 0700")
    else:
        parent.mkdir(mode=0o700)
    root = parent / run_id
    if root.exists():
        st = root.lstat()
        if (not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode)
                or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700):
            raise PermissionError("unsafe initial snapshot directory")
        entries = list(root.iterdir())
        if entries:
            if (len(entries) != 1 or entries[0].is_symlink()
                    or not entries[0].name.startswith("snapshot-")
                    or not verify_readonly(entries[0]).ok
                    or status_readonly(entries[0]) != {
                        "state": "PREPARED", "calls_total": 0}
                    or not point.initial_snapshot_matches(entries[0])):
                raise RuntimeGateError("initial snapshot contains unexpected evidence")
            return entries[0]
    return backup_snapshot(point, root, lock=lock)


_RESULT_MODELS = {
    ("SELECTED", "F"): FSelectedResult,
    ("INCONCLUSIVE", "F"): FInconclusiveResult,
    ("INCONCLUSIVE", "D"): DInconclusiveResult,
}


def _public_artifact(value: Mapping[str, Any]) -> dict[str, Any]:
    terminal, stage = value.get("terminal"), value.get("stage")
    model = (FailureEvidence if value.get("version") == "c0b2-failure-evidence-v1"
             else FailureArtifact if terminal in {
        "FAILED_SAFETY", "BLOCKED_PROVENANCE", "BLOCKED_BUDGET",
        "BLOCKED_FILESYSTEM", "ABANDONED",
    } else _RESULT_MODELS.get((terminal, stage)))
    if model is None:
        raise ValueError("unsupported public result/failure artifact")
    return validate_artifact(model, value)


def freeze_public_artifact(point: Checkpoint, artifact_id: str,
                           value: Mapping[str, Any]) -> str:
    """Strict, transaction-joinable storage for D/F results and public failures."""
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError("public artifact ID must be nonempty")
    normalized = _public_artifact(value)
    raw, digest = canonical_json(normalized), sha256_json(normalized)
    frozen = (normalized["terminal"], digest, raw)
    with runtime_transaction(point):
        row = point.conn.execute(
            "SELECT terminal,artifact_hash,artifact_json FROM public_artifacts "
            "WHERE artifact_id=?", (artifact_id,),
        ).fetchone()
        if row and row != frozen:
            raise RuntimeGateError(f"public artifact {artifact_id} changed")
        if not row:
            point.conn.execute(
                "INSERT INTO public_artifacts VALUES(?,?,?,?,?)",
                (artifact_id, *frozen, time.time()))
    return digest


def load_public_artifact(point: Checkpoint, artifact_id: str) -> tuple[str, dict[str, Any]]:
    row = point.conn.execute(
        "SELECT artifact_hash,artifact_json FROM public_artifacts WHERE artifact_id=?",
        (artifact_id,),
    ).fetchone()
    if not row:
        raise CheckpointError(f"unknown public artifact {artifact_id}")
    value = _public_artifact(json.loads(str(row[1])))
    if canonical_json(value) != row[1] or sha256_json(value) != row[0]:
        raise RuntimeGateError("public artifact hash or canonical encoding changed")
    return str(row[0]), value


def finish_public_failure_attempt(point: Checkpoint, *, attempt_id: str,
                                  terminal: str) -> str:
    """Atomically bind an exact public control failure, artifact, and terminal."""
    if terminal not in {"FAILED_SAFETY", "BLOCKED_PROVENANCE"}:
        raise ValueError("attempt failure terminal is not supported")
    if point.header()["run_type"] != "public":
        raise CheckpointError("public failure finalization requires a public run")
    row = point.conn.execute(
        "SELECT work_id,control_id,stage,state FROM attempts WHERE attempt_id=?",
        (attempt_id,),
    ).fetchone()
    if not row or (row[0] is None) == (row[1] is None):
        raise CheckpointError("public failure attempt lacks one dispatch identity")
    if row[3] == terminal:
        if point.state() != terminal:
            raise CheckpointError("public failure attempt and run terminal differ")
        evidence_hash, _evidence = load_public_artifact(
            point, f"failure-evidence:{attempt_id}")
        artifact_hash, artifact = load_public_artifact(
            point, f"failure:{attempt_id}")
        if artifact["evidence_sha256"] != evidence_hash:
            raise RuntimeGateError("public failure retry differs from frozen evidence")
        return artifact_hash
    if row[3] != "DISPATCHING":
        raise CheckpointError("public failure attempt is not dispatching")
    position = runtime_position(point)
    if row[2] != position.active_stage:
        raise RuntimeGateError("failure attempt differs from the active runtime cursor")
    reason = FAILURE_REASON_BY_TERMINAL[terminal]
    evidence = validate_artifact(FailureEvidence, {
        "version": "c0b2-failure-evidence-v1", "terminal": terminal,
        "stage": row[2], "reason_code": reason, "attempt_id": attempt_id,
        "control_id": row[1], "plan_key": position.active_plan_key,
    })
    evidence_hash = sha256_json(evidence)
    artifact = validate_artifact(FailureArtifact, {
        "version": "c0b2-failure-v1", "terminal": terminal, "stage": row[2],
        "reason": reason, "evidence_sha256": evidence_hash,
        "charged_call_total": int(point.conn.execute(
            "SELECT count(*) FROM attempts").fetchone()[0]),
    })
    artifact_hash = sha256_json(artifact)
    point.conn.execute("BEGIN IMMEDIATE")
    try:
        if point.state() != "RUNNING":
            raise CheckpointError("public failure finalization requires RUNNING")
        if point.conn.execute(
                "SELECT state FROM attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()[0] != "DISPATCHING":
            raise CheckpointError("public failure attempt changed before finalization")
        freeze_public_artifact(
            point, f"failure-evidence:{attempt_id}", evidence)
        freeze_public_artifact(point, f"failure:{attempt_id}", artifact)
        point.conn.execute(
            "UPDATE attempts SET state=?,response=NULL,metadata_json=?,updated=? "
            "WHERE attempt_id=?",
            (terminal, canonical_json({"failure_artifact_sha256": artifact_hash}),
             time.time(), attempt_id),
        )
        if row[0] is not None:
            point.conn.execute(
                "UPDATE work_items SET state='PENDING',accepted_attempt_id=NULL "
                "WHERE work_id=?", (row[0],))
        point.conn.execute(
            "UPDATE run_state SET state=?,updated=? WHERE id=1",
            (terminal, time.time()),
        )
        point.conn.commit()
        return artifact_hash
    except Exception:
        point.conn.rollback()
        raise


def finish_public_run_failure(point: Checkpoint, *, terminal: str) -> str:
    """Atomically finalize a public failure that has no dispatch attempt."""
    if terminal not in {"BLOCKED_PROVENANCE", "BLOCKED_FILESYSTEM", "ABANDONED"}:
        raise ValueError("run failure terminal is not supported")
    if point.header()["run_type"] != "public" or point.conn.in_transaction:
        raise CheckpointError("public run failure requires committed public evidence")
    position = runtime_position(point)
    reason = FAILURE_REASON_BY_TERMINAL[terminal]
    evidence = validate_artifact(FailureEvidence, {
        "version": "c0b2-failure-evidence-v1", "terminal": terminal,
        "stage": position.active_stage, "reason_code": reason,
        "attempt_id": None, "control_id": None,
        "plan_key": position.active_plan_key,
    })
    evidence_hash = sha256_json(evidence)
    artifact = validate_artifact(FailureArtifact, {
        "version": "c0b2-failure-v1", "terminal": terminal,
        "stage": position.active_stage, "reason": reason,
        "evidence_sha256": evidence_hash,
        "charged_call_total": int(point.conn.execute(
            "SELECT count(*) FROM attempts").fetchone()[0]),
    })
    point.conn.execute("BEGIN IMMEDIATE")
    try:
        old_state = point.state()
        if old_state != terminal:
            if old_state not in RESUMABLE_STATES:
                raise CheckpointError(
                    f"illegal state transition {old_state} -> {terminal}")
            point.conn.execute(
                "UPDATE run_state SET state=?,updated=? WHERE id=1",
                (terminal, time.time()),
            )
        identity = f"{terminal}:{position.active_plan_key}"
        freeze_public_artifact(point, f"failure-evidence:{identity}", evidence)
        freeze_public_artifact(point, f"failure:{identity}", artifact)
        point.conn.commit()
        return sha256_json(artifact)
    except Exception:
        point.conn.rollback()
        raise


def finish_public_budget_failure(
        point: Checkpoint, payload: Mapping[str, str | None]) -> str:
    """Join a cap transaction and bind BLOCKED_BUDGET to exact public evidence."""
    if point.header()["run_type"] != "public" or not point.conn.in_transaction:
        raise CheckpointError("public budget finalization requires its cap transaction")
    expected_keys = {"stage", "attempt_id", "control_id", "work_id"}
    if (set(payload) != expected_keys or payload["attempt_id"] is not None
            or payload["stage"] not in {"C", "D", "F"}
            or (payload["control_id"] is not None
                and payload["work_id"] is not None)):
        raise ValueError("invalid public budget-failure payload")
    position = runtime_position(point)
    if payload["stage"] != position.active_stage or point.state() != "BLOCKED_BUDGET":
        raise RuntimeGateError("budget failure differs from the active runtime cursor")
    identity_hash = sha256_json(dict(payload))
    reason = FAILURE_REASON_BY_TERMINAL["BLOCKED_BUDGET"]
    evidence = validate_artifact(FailureEvidence, {
        "version": "c0b2-failure-evidence-v1", "terminal": "BLOCKED_BUDGET",
        "stage": payload["stage"], "reason_code": reason, "attempt_id": None,
        "control_id": payload["control_id"], "plan_key": position.active_plan_key,
    })
    evidence_hash = sha256_json(evidence)
    artifact = validate_artifact(FailureArtifact, {
        "version": "c0b2-failure-v1", "terminal": "BLOCKED_BUDGET",
        "stage": payload["stage"], "reason": reason,
        "evidence_sha256": evidence_hash,
        "charged_call_total": int(point.conn.execute(
            "SELECT count(*) FROM attempts").fetchone()[0]),
    })
    freeze_public_artifact(
        point, f"failure-evidence:budget:{identity_hash}", evidence)
    freeze_public_artifact(point, f"failure:budget:{identity_hash}", artifact)
    return sha256_json(artifact)


def _readonly_connection(path: Path) -> sqlite3.Connection:
    if any(Path(str(path) + suffix).exists()
           for suffix in ("-wal", "-shm", "-journal")):
        raise CheckpointError("read-only backup inspection refused with SQLite sidecars")
    conn = sqlite3.connect(
        f"file:{quote(str(path.resolve()), safe='/')}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _canonical_db_object(raw: str, digest: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeGateError(f"{label} is not valid JSON") from exc
    if (not isinstance(value, dict) or canonical_json(value) != raw
            or sha256_json(value) != digest):
        raise RuntimeGateError(f"{label} hash or canonical encoding changed")
    return value


def _typed_db_object(raw: str, digest: str, label: str, model: Any) -> dict[str, Any]:
    value = _canonical_db_object(raw, digest, label)
    try:
        normalized = validate_artifact(model, value)
    except (TypeError, ValueError) as exc:
        raise RuntimeGateError(f"{label} failed semantic validation") from exc
    if canonical_json(normalized) != raw:
        raise RuntimeGateError(f"{label} failed exact validation")
    return normalized


def _decision_hash(conn: sqlite3.Connection, decision_id: str, *,
                   stage: str | None = None, parent_hash: str | None = None,
                   aggregate_hash: str | None = None,
                   activation: str = "ACTIVATED") -> str:
    row = conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json "
        "FROM decisions WHERE decision_id=?", (decision_id,),
    ).fetchone()
    if not row:
        raise RuntimeGateError(f"required decision {decision_id} is missing")
    _canonical_db_object(str(row[4]), sha256_json(json.loads(row[4])), decision_id)
    if (row[3] != activation or stage is not None and row[0] != stage
            or parent_hash is not None and row[1] != parent_hash
            or aggregate_hash is not None and row[2] != aggregate_hash):
        raise RuntimeGateError(f"decision {decision_id} differs from its evidence owner")
    return sha256_json((decision_id, *row))


def _stored_public_artifact(
        conn: sqlite3.Connection, terminal: str, *, active_stage: str,
        active_plan_key: str, active_plan_hash: str,
        aggregate_hash: str | None, charged_total: int) -> str:
    rows = conn.execute(
        "SELECT terminal,artifact_hash,artifact_json FROM public_artifacts WHERE terminal=?",
        (terminal,),
    ).fetchall()
    try:
        parsed = [(row, _public_artifact(json.loads(str(row[2])))) for row in rows]
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeGateError("terminal public artifact failed validation") from exc
    if any(row[0] != value["terminal"] or value["terminal"] != terminal
           or value["stage"] != active_stage for row, value in parsed):
        raise RuntimeGateError("public artifact terminal/stage differs from checkpoint state")
    artifacts = [(row, value) for row, value in parsed
                 if value["version"] != "c0b2-failure-evidence-v1"]
    if len(artifacts) != 1:
        raise RuntimeGateError("terminal requires one exact result/failure artifact")
    row, value = artifacts[0]
    if canonical_json(value) != row[2] or sha256_json(value) != row[1]:
        raise RuntimeGateError("terminal public artifact changed")
    if value["version"] == "c0b2-failure-v1":
        evidence = [(item, body) for item, body in parsed
                    if body["version"] == "c0b2-failure-evidence-v1"]
        if (len(evidence) != 1 or evidence[0][0][1] != value["evidence_sha256"]
                or sha256_json(evidence[0][1]) != value["evidence_sha256"]
                or canonical_json(evidence[0][1]) != evidence[0][0][2]):
            raise RuntimeGateError("failure artifact lacks its exact evidence")
        fact = evidence[0][1]
        if (fact["terminal"] != terminal or fact["stage"] != active_stage
                or fact["plan_key"] != active_plan_key
                or value["charged_call_total"] != charged_total):
            raise RuntimeGateError("failure artifact differs from runtime evidence")
        attempt_id, control_id = fact["attempt_id"], fact["control_id"]
        if terminal == "FAILED_SAFETY" and attempt_id is None:
            raise RuntimeGateError("safety failure lacks its charged attempt")
        if terminal in {"BLOCKED_FILESYSTEM", "ABANDONED"} and (
                attempt_id is not None or control_id is not None):
            raise RuntimeGateError("attemptless run failure carries dispatch identity")
        if (terminal == "BLOCKED_PROVENANCE" and attempt_id is None
                and control_id is not None):
            raise RuntimeGateError("attemptless provenance failure carries a control")
        if terminal == "BLOCKED_BUDGET" and attempt_id is not None:
            raise RuntimeGateError("budget failure cannot carry a charged attempt")
        if attempt_id is not None:
            attempt = conn.execute(
                "SELECT control_id,stage,state FROM attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if attempt != (control_id, active_stage, terminal):
                raise RuntimeGateError("failure evidence differs from its charged attempt")
    else:
        if aggregate_hash is None:
            raise RuntimeGateError("public result lacks an authoritative aggregate")
        owner = (value.get("acceptance_aggregate_sha256")
                 if value["terminal"] == "SELECTED" else value.get("aggregate_sha256"))
        if owner != aggregate_hash:
            raise RuntimeGateError("public result differs from its aggregate owner")
        completion = conn.execute(
            "SELECT stage,parent_hash,aggregate_hash,activation,value_json FROM decisions "
            "WHERE decision_id='c0b2-completion'").fetchall()
        if len(completion) != 1:
            raise RuntimeGateError("public result lacks its exact completion decision")
        decision = completion[0]
        model = SelectedCompletion if terminal == "SELECTED" else InconclusiveCompletion
        try:
            body = validate_artifact(model, json.loads(str(decision[4])))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeGateError("completion decision failed validation") from exc
        expected_activation = "ACTIVATED" if terminal == "SELECTED" else "NOT_ACTIVATED"
        if (decision[:4] != (
                active_stage, active_plan_hash, aggregate_hash, expected_activation)
                or canonical_json(body) != decision[4]
                or body["artifact_sha256"] != row[1]
                or terminal == "INCONCLUSIVE"
                and body["facts"]["reason"] != value["reason"]):
            raise RuntimeGateError("completion decision differs from its public result")
    return str(row[1])


def _legacy_stage_c_artifact(conn: sqlite3.Connection, aggregate_hash: str) -> str:
    rows = conn.execute(
        "SELECT detail_json FROM events WHERE kind='FINAL_ARTIFACT' ORDER BY seq"
    ).fetchall()
    if len(rows) != 1:
        raise RuntimeGateError("Stage-C terminal requires one final artifact")
    detail = json.loads(str(rows[0][0]))
    artifact = detail.get("artifact") if isinstance(detail, dict) else None
    if (set(detail) != {"state", "sha256", "artifact"}
            or detail["state"] != "INCONCLUSIVE"
            or not isinstance(artifact, dict)
            or set(artifact) != {
                "version", "terminal", "stage", "aggregate_sha256", "reason"}
            or artifact != {
                "version": "c0b2-result-v1", "terminal": "INCONCLUSIVE",
                "stage": "C", "aggregate_sha256": aggregate_hash,
                "reason": "no_stage_c_survivor"}
            or hashlib.sha256(canonical_json(artifact).encode()).hexdigest()
            != detail["sha256"]):
        raise RuntimeGateError("Stage-C final artifact changed")
    return str(detail["sha256"])


def _plan_aggregate_hash(
        conn: sqlite3.Connection, plan_key: str, plan_hash: str) -> str:
    if plan_key == "C":
        rows = conn.execute(
            "SELECT plan_hash,aggregate_hash,aggregate_json FROM stage_aggregates "
            "WHERE stage='C'"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT plan_hash,aggregate_hash,aggregate_json FROM phase_aggregates "
            "WHERE plan_key=?", (plan_key,),
        ).fetchall()
    if len(rows) != 1 or str(rows[0][0]) != plan_hash:
        raise RuntimeGateError(
            f"decision owner {plan_key} lacks its exact aggregate")
    _canonical_db_object(
        str(rows[0][2]), str(rows[0][1]), f"aggregate owner {plan_key}")
    return str(rows[0][1])


def _phase_parent_owner(plan_key: str,
                        plan_hashes: Mapping[str, str]) -> str:
    d_predecessors = {
        "D1_OUTPUT": "C", "D2_CHUNK": "D1_OUTPUT",
        "D3_CONTEXT": "D2_CHUNK", "D4_CONFIRMATION": "D3_CONTEXT",
    }
    if plan_key in d_predecessors:
        owner = d_predecessors[plan_key]
    elif plan_key == "F_ACCEPTANCE":
        owner = "F_SEED_20260804"
    else:
        d_keys = [key for key in PLAN_ORDER
                  if key.startswith("D") and key in plan_hashes]
        if not d_keys:
            raise RuntimeGateError(f"phase plan {plan_key} lacks its D owner")
        owner = d_keys[-1]
    if owner not in plan_hashes:
        raise RuntimeGateError(
            f"phase plan {plan_key} skipped decision owner {owner}")
    return owner


_B4_ACTIVATION_KEYS = frozenset({
    "F_SEED_17", "F_SEED_20260804", "F_ACCEPTANCE",
})


def _validate_backup_activation(
        conn: sqlite3.Connection, header: Mapping[str, Any], key: str,
        plan: Mapping[str, Any], activation: Mapping[str, Any]) -> None:
    """Cross-bind a B2 activation to its run, plan, and exact work registry."""
    if key in _B4_ACTIVATION_KEYS:
        raise RuntimeGateError(
            f"backup activation {key} requires B4 typed evidence validation")
    if (activation["run_id"] != header["run_id"]
            or activation["budget_stage"] != plan["budget_stage"]):
        raise RuntimeGateError(
            f"phase activation {key} differs from its run or budget stage")
    if key == "F_SEED_1":
        expected_groups = [group["group_id"] for group in plan["groups"]]
        if activation["activated_group_ids"] != expected_groups:
            raise RuntimeGateError(
                "F seed-1 backup activation does not contain every group in order")
    expected_registry = [
        (item["work_id"], item["activation_group_id"], item["stage"],
         item["cell_id"], item["request_sha256"])
        for item in plan["work"]
    ]
    registry = conn.execute(
        "SELECT r.work_id,r.activation_group_id,w.stage,w.cell_id,w.request_hash "
        "FROM phase_work_registry r JOIN work_items w ON w.work_id=r.work_id "
        "WHERE r.plan_key=? ORDER BY r.rowid", (key,),
    ).fetchall()
    if registry != expected_registry:
        raise RuntimeGateError(
            f"phase activation {key} differs from its exact work registry")


def _anchor_from_connection(conn: sqlite3.Connection) -> dict[str, Any]:
    header_row = conn.execute("SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
    if not header_row:
        raise RuntimeGateError("run header is missing")
    header = _canonical_db_object(str(header_row[0]), str(header_row[1]), "run header")
    state = str(conn.execute("SELECT state FROM run_state WHERE id=1").fetchone()[0])
    if state not in _BACKUP_REQUIRED_STATES:
        raise RuntimeGateError(f"state {state} does not require a backup receipt")
    cursor = conn.execute(
        "SELECT active_stage,active_plan_key FROM runtime_cursor WHERE id=1").fetchone()
    if not cursor:
        raise RuntimeGateError("runtime cursor is missing")
    active_stage, active_key = str(cursor[0]), str(cursor[1])
    if active_stage == "F":
        master = conn.execute(
            "SELECT manifest_hash,manifest_json FROM manifests WHERE name='f_master'"
        ).fetchone()
        if not master:
            raise RuntimeGateError("F backup lacks its frozen master plan")
        _typed_db_object(
            str(master[1]), str(master[0]), "F master plan", FMasterPlan)
        f_master = str(master[0])
    else:
        f_master = None
    c_plan = conn.execute(
        "SELECT plan_hash,plan_json FROM plans WHERE stage='C'").fetchone()
    if not c_plan:
        raise RuntimeGateError("Stage-C plan is missing")
    _canonical_db_object(str(c_plan[1]), str(c_plan[0]), "Stage-C plan")
    plans = [{"plan_key": "C", "plan_sha256": str(c_plan[0]),
              "activation_sha256": None}]
    plan_hashes = {"C": str(c_plan[0])}
    phase_rows = conn.execute(
        "SELECT p.plan_key,p.plan_hash,p.plan_json,a.activation_hash,a.activation_json "
        "FROM phase_plans p JOIN plan_activations a ON a.plan_key=p.plan_key"
    ).fetchall()
    if any(str(row[0]) not in PLAN_ORDER or row[0] == "C" for row in phase_rows):
        raise RuntimeGateError("phase plan lineage contains an unknown key")
    phase_rows.sort(key=lambda row: PLAN_ORDER.index(str(row[0])))
    plan_parent_decisions = {
        "D1_OUTPUT": "stage-c-selection", "D2_CHUNK": "stage-d-d1-selection",
        "D3_CONTEXT": "stage-d-d2-selection",
        "D4_CONFIRMATION": "stage-d-d3-selection",
        "F_SEED_1": "stage-d-selection", "F_SEED_17": "stage-d-selection",
        "F_SEED_20260804": "stage-d-selection",
        "F_ACCEPTANCE": "stage-f-provisional-selection",
    }
    activation_parent_decisions = {
        **plan_parent_decisions,
        "F_SEED_17": "stage-f-seed-activation",
        "F_SEED_20260804": "stage-f-seed-activation",
    }
    for key, plan_hash, plan_raw, activation_hash, activation_raw in phase_rows:
        key = str(key)
        model = (DPhasePlan if str(key).startswith("D")
                 else AcceptancePlan if key == "F_ACCEPTANCE" else FSeedPlan)
        plan = _typed_db_object(
            str(plan_raw), str(plan_hash), f"phase plan {key}", model)
        activation = _typed_db_object(
            str(activation_raw), str(activation_hash),
            f"phase activation {key}", PlanActivation)
        owner_key = _phase_parent_owner(key, plan_hashes)
        owner_hash = plan_hashes[owner_key]
        owner_stage = "C" if owner_key == "C" else owner_key[0]
        owner_aggregate = _plan_aggregate_hash(
            conn, owner_key, owner_hash)
        plan_parent = _decision_hash(
            conn, plan_parent_decisions[key], stage=owner_stage,
            parent_hash=owner_hash, aggregate_hash=owner_aggregate)
        if activation_parent_decisions[key] == plan_parent_decisions[key]:
            activation_parent = plan_parent
        else:
            # B4 owns the seed-activation decision's typed evidence. B2 still
            # requires it to be an activated Stage-F decision before accepting
            # any externally restored later-seed lineage.
            activation_parent = _decision_hash(
                conn, activation_parent_decisions[key], stage="F")
        if (activation["plan_key"] != key
                or activation["plan_sha256"] != plan_hash
                or plan["parent_decision_sha256"] != plan_parent
                or activation["parent_decision_sha256"] != activation_parent):
            raise RuntimeGateError(f"phase activation {key} failed exact validation")
        _validate_backup_activation(conn, header, key, plan, activation)
        plans.append({"plan_key": str(key), "plan_sha256": str(plan_hash),
                      "activation_sha256": str(activation_hash)})
        plan_hashes[key] = str(plan_hash)
    if plans[-1]["plan_key"] != active_key:
        raise RuntimeGateError("backup plan lineage does not end at the active cursor")
    aggregate = None
    if state in {"PAUSED_STAGE_BOUNDARY", "SELECTED", "INCONCLUSIVE"}:
        if active_key == "C":
            aggregate = conn.execute(
                "SELECT aggregate_hash,aggregate_json FROM stage_aggregates "
                "WHERE stage='C'").fetchone()
        else:
            aggregate = conn.execute(
                "SELECT aggregate_hash,aggregate_json FROM phase_aggregates "
                "WHERE plan_key=?", (active_key,),
            ).fetchone()
        if not aggregate:
            raise RuntimeGateError("backup state lacks its authoritative aggregate")
        _canonical_db_object(str(aggregate[1]), str(aggregate[0]), "backup aggregate")
    if state == "PAUSED_STAGE_BOUNDARY":
        decision_id = "stage-c-selection" if active_stage == "C" else "stage-d-selection"
        owner_hash = _decision_hash(
            conn, decision_id, stage=active_stage,
            parent_hash=str(plans[-1]["plan_sha256"]),
            aggregate_hash=str(aggregate[0]))
    elif active_stage == "C" and state == "INCONCLUSIVE":
        owner_hash = _legacy_stage_c_artifact(conn, str(aggregate[0]))
    else:
        charged_total = int(conn.execute(
            "SELECT count(*) FROM attempts").fetchone()[0])
        owner_hash = _stored_public_artifact(
            conn, state, active_stage=active_stage, active_plan_key=active_key,
            active_plan_hash=str(plans[-1]["plan_sha256"]),
            aggregate_hash=str(aggregate[0]) if aggregate else None,
            charged_total=charged_total)
    return validate_artifact(BackupAnchor, {
        "version": "c0b2-backup-anchor-v1", "run_id": header["run_id"],
        "active_stage": active_stage, "state": state,
        "f_master_plan_sha256": f_master, "plans": plans,
        "aggregate_sha256": str(aggregate[0]) if aggregate else None,
        "decision_or_artifact_sha256": owner_hash,
        "charged_call_total": int(conn.execute(
            "SELECT count(*) FROM attempts").fetchone()[0]),
    })


def _current_backup_anchor(point: Checkpoint,
                           supplied: Mapping[str, Any] | None = None) -> dict[str, Any]:
    current = _anchor_from_connection(point.conn)
    if supplied is not None:
        exact = validate_artifact(BackupAnchor, supplied)
        if canonical_json(exact) != canonical_json(current):
            raise RuntimeGateError("supplied backup anchor differs from checkpoint evidence")
    return current


def _receipt_snapshot_path(run_dir: Path, relative: str) -> Path:
    value = PurePosixPath(relative)
    if (len(value.parts) < 2 or value.parts[0] != "backups"
            or value.is_absolute() or "." in value.parts or ".." in value.parts):
        raise RuntimeGateError("receipt path escapes this run's backup directory")
    return run_dir.joinpath(*value.parts)


def _owner_dir_fd(path: Path | str, *, dir_fd: int | None = None) -> int:
    flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    fd = os.open(path, flags, dir_fd=dir_fd)
    st = os.fstat(fd)
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o700):
        os.close(fd)
        raise PermissionError("backup path contains an unsafe directory")
    return fd


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _hash_fd(fd: int) -> str:
    digest, offset = hashlib.sha256(), 0
    while True:
        block = os.pread(fd, 1 << 20, offset)
        if not block:
            return digest.hexdigest()
        digest.update(block)
        offset += len(block)


def _verify_sqlite_fd(fd: int) -> None:
    conn = sqlite3.connect(f"file:/proc/self/fd/{fd}?mode=ro", uri=True, timeout=1.0)
    try:
        conn.execute("PRAGMA query_only=ON")
        result = verify_connection(conn)
    finally:
        conn.close()
    if not result.ok:
        raise RuntimeGateError(f"backup snapshot failed verification: {result.errors}")


def _assert_named_fd(parent_fd: int, name: str, fd: int) -> None:
    named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not _same_inode(named, os.fstat(fd)):
        raise RuntimeGateError("backup path no longer names its pinned inode")


def _assert_run_binding(run_dir: Path, run_fd: int, backup_fd: int) -> None:
    if not _same_inode(os.stat(run_dir, follow_symlinks=False), os.fstat(run_fd)):
        raise RuntimeGateError("canonical run path changed during backup operation")
    _assert_named_fd(run_fd, "backups", backup_fd)


@dataclass
class _PinnedBackupSnapshot:
    path: Path
    snapshot_hash: str
    size: int
    run_fd: int
    backup_fd: int
    snapshot_fd: int
    name: str

    def verify(self) -> None:
        if min(self.run_fd, self.backup_fd, self.snapshot_fd) < 0:
            raise RuntimeGateError("backup snapshot descriptors are closed")
        st = os.fstat(self.snapshot_fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o600 or st.st_size != self.size
                or _hash_fd(self.snapshot_fd) != self.snapshot_hash):
            raise RuntimeGateError("pinned backup snapshot identity changed")
        _verify_sqlite_fd(self.snapshot_fd)
        if (_hash_fd(self.snapshot_fd) != self.snapshot_hash
                or os.fstat(self.snapshot_fd).st_size != self.size):
            raise RuntimeGateError("pinned backup snapshot changed during verification")
        _assert_run_binding(self.path.parent.parent, self.run_fd, self.backup_fd)
        _assert_named_fd(self.backup_fd, self.name, self.snapshot_fd)

    def close(self) -> None:
        for field in ("snapshot_fd", "backup_fd", "run_fd"):
            fd = getattr(self, field)
            if fd >= 0:
                os.close(fd)
                setattr(self, field, -1)


def _write_backup_snapshot(point: Checkpoint, lock: GlobalExecutionLock,
                           anchor_hash: str) -> _PinnedBackupSnapshot:
    if not lock.held or lock.root != point.root:
        raise RuntimeGateError("backup receipt requires the matching global lock")
    run_dir = point.path.parent
    run_fd = _owner_dir_fd(run_dir)
    backup_fd = fd = -1
    transferred = False
    try:
        try:
            os.mkdir("backups", mode=0o700, dir_fd=run_fd)
            os.fsync(run_fd)
        except FileExistsError:
            pass
        backup_fd = _owner_dir_fd("backups", dir_fd=run_fd)
        _assert_run_binding(run_dir, run_fd, backup_fd)
        name = f"snapshot-{anchor_hash}-{secrets.token_hex(12)}.sqlite3"
        fd = os.open(
            name, os.O_CREAT | os.O_EXCL | os.O_RDWR
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            0o600, dir_fd=backup_fd)
        destination = sqlite3.connect(f"/proc/self/fd/{fd}")
        try:
            point.conn.backup(destination)
            destination.commit()
            verified = verify_connection(destination)
            if not verified.ok:
                raise RuntimeGateError(
                    f"backup snapshot failed verification: {verified.errors}")
        finally:
            destination.close()
        os.fchmod(fd, 0o600)
        snapshot_hash, size = _hash_fd(fd), os.fstat(fd).st_size
        _verify_sqlite_fd(fd)
        if _hash_fd(fd) != snapshot_hash or size <= 0:
            raise RuntimeGateError("backup snapshot changed during verification")
        _assert_run_binding(run_dir, run_fd, backup_fd)
        _assert_named_fd(backup_fd, name, fd)
        os.fsync(fd)
        os.fsync(backup_fd)
        pinned = _PinnedBackupSnapshot(
            run_dir / "backups" / name, snapshot_hash, size,
            run_fd, backup_fd, fd, name)
        transferred = True
        return pinned
    except Exception:
        if backup_fd >= 0 and 'name' in locals():
            try:
                os.unlink(name, dir_fd=backup_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        if not transferred:
            if fd >= 0:
                os.close(fd)
            if backup_fd >= 0:
                os.close(backup_fd)
            os.close(run_fd)


def _load_receipt(raw: str, digest: str, *, anchor_hash: str | None = None
                  ) -> dict[str, Any]:
    try:
        value = validate_artifact(BackupReceipt, json.loads(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeGateError("backup receipt failed semantic validation") from exc
    if canonical_json(value) != raw or sha256_json(value) != digest:
        raise RuntimeGateError("backup receipt hash or canonical encoding changed")
    if anchor_hash is not None and value["anchor_sha256"] != anchor_hash:
        raise RuntimeGateError("backup receipt differs from its anchor row")
    return value


def _verify_receipt_file(run_dir: Path, receipt: Mapping[str, Any]) -> Path:
    relative = str(receipt["snapshot_run_relative_path"])
    path = _receipt_snapshot_path(run_dir, relative)
    parts = PurePosixPath(relative).parts
    run_fd = _owner_dir_fd(run_dir)
    opened_dirs: list[tuple[int, str, int]] = []
    parent_fd = run_fd
    snapshot_fd = -1
    try:
        for component in parts[:-1]:
            child = _owner_dir_fd(component, dir_fd=parent_fd)
            opened_dirs.append((parent_fd, component, child))
            parent_fd = child
        snapshot_fd = os.open(
            parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0), dir_fd=parent_fd)
        st = os.fstat(snapshot_fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o600
                or st.st_size != receipt["snapshot_size_bytes"]
                or _hash_fd(snapshot_fd) != receipt["snapshot_sha256"]):
            raise RuntimeGateError("backup receipt snapshot identity changed")
        _verify_sqlite_fd(snapshot_fd)
        if (_hash_fd(snapshot_fd) != receipt["snapshot_sha256"]
                or os.fstat(snapshot_fd).st_size != receipt["snapshot_size_bytes"]):
            raise RuntimeGateError("backup receipt snapshot changed during verification")
        if not _same_inode(os.stat(run_dir, follow_symlinks=False), os.fstat(run_fd)):
            raise RuntimeGateError("canonical run path changed during backup verification")
        for directory_fd, name, child_fd in opened_dirs:
            _assert_named_fd(directory_fd, name, child_fd)
        _assert_named_fd(parent_fd, parts[-1], snapshot_fd)
        return path
    finally:
        if snapshot_fd >= 0:
            os.close(snapshot_fd)
        for _parent, _name, child in reversed(opened_dirs):
            os.close(child)
        os.close(run_fd)


def _receipt_return_hook(_receipt: Mapping[str, Any]) -> None:
    """Test seam for the post-commit/pre-return crash window."""


def _post_receipt_commit_hook(_path: Path, _receipt: Mapping[str, Any]) -> None:
    """Test seam for snapshot replacement after the receipt commit."""


def _pre_receipt_commit_hook(_path: Path, _receipt: Mapping[str, Any]) -> None:
    """Test seam for a crash after snapshot fsync but before receipt insertion."""


def ensure_backup_receipt(
        point: Checkpoint, lock: GlobalExecutionLock,
        anchor: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Idempotently bind an exact boundary/terminal anchor to a verified snapshot."""
    if point.conn.in_transaction:
        raise RuntimeGateError("backup receipt requires committed checkpoint evidence")
    if point.state() != "BLOCKED_PROVENANCE":
        try:
            _run_nonce_key(point)
        except RuntimeGateError:
            if point.state() != "PAUSED_STAGE_BOUNDARY":
                raise
            finish_public_run_failure(point, terminal="BLOCKED_PROVENANCE")
    normalized = _current_backup_anchor(point, anchor)
    anchor_raw, anchor_hash = canonical_json(normalized), sha256_json(normalized)
    row = point.conn.execute(
        "SELECT anchor_json,receipt_hash,receipt_json FROM backup_receipts "
        "WHERE anchor_hash=?", (anchor_hash,),
    ).fetchone()
    if row and row[0] != anchor_raw:
        raise RuntimeGateError("backup anchor hash collision or row tamper")
    if row:
        receipt = _load_receipt(
            str(row[2]), str(row[1]), anchor_hash=anchor_hash)
        _verify_receipt_file(point.path.parent, receipt)
        _receipt_return_hook(receipt)
        return receipt
    pinned = _write_backup_snapshot(point, lock, anchor_hash)
    try:
        relative = pinned.path.relative_to(point.path.parent).as_posix()
        receipt = validate_artifact(BackupReceipt, {
            "version": "c0b2-backup-receipt-v1", "anchor_sha256": anchor_hash,
            "snapshot_run_relative_path": relative,
            "snapshot_sha256": pinned.snapshot_hash,
            "snapshot_size_bytes": pinned.size,
            "integrity_check": "ok", "foreign_key_violations": 0,
            "created_at_utc": datetime.now(timezone.utc).isoformat(
                timespec="microseconds").replace("+00:00", "Z"),
        })
        _pre_receipt_commit_hook(pinned.path, receipt)
        pinned.verify()
        receipt_raw, receipt_hash = canonical_json(receipt), sha256_json(receipt)
        point.conn.execute("BEGIN IMMEDIATE")
        try:
            if canonical_json(_current_backup_anchor(point)) != anchor_raw:
                raise RuntimeGateError("backup anchor changed before receipt insertion")
            pinned.verify()
            point.conn.execute(
                "INSERT INTO backup_receipts VALUES(?,?,?,?,?)",
                (anchor_hash, anchor_raw, receipt_hash, receipt_raw, time.time()))
            pinned.verify()
            point.conn.commit()
            _post_receipt_commit_hook(pinned.path, receipt)
            pinned.verify()
        except Exception:
            point.conn.rollback()
            raise
    finally:
        pinned.close()
    _receipt_return_hook(receipt)
    return receipt


def backup_status_readonly(db_path: Path) -> dict[str, Any]:
    path = Path(db_path)
    conn = _readonly_connection(path)
    try:
        state = str(conn.execute("SELECT state FROM run_state WHERE id=1").fetchone()[0])
        if state not in _BACKUP_REQUIRED_STATES:
            return validate_artifact(BackupStatus, {
                "required": False, "receipt_present": False,
                "anchor_sha256": None, "snapshot_sha256": None,
            })
        anchor = _anchor_from_connection(conn)
        anchor_hash = sha256_json(anchor)
        row = conn.execute(
            "SELECT anchor_json,receipt_hash,receipt_json FROM backup_receipts "
            "WHERE anchor_hash=?",
            (anchor_hash,),
        ).fetchone()
        snapshot_hash = None
        if row:
            if row[0] != canonical_json(anchor):
                raise RuntimeGateError("backup receipt row changed its anchor evidence")
            snapshot_hash = _load_receipt(
                str(row[2]), str(row[1]),
                anchor_hash=anchor_hash)["snapshot_sha256"]
        return validate_artifact(BackupStatus, {
            "required": True, "receipt_present": snapshot_hash is not None,
            "anchor_sha256": anchor_hash, "snapshot_sha256": snapshot_hash,
        })
    finally:
        conn.close()


def public_status(run_id: str, *, benchmark_root: Path | None = None) -> dict[str, Any]:
    path = _checkpoint_path(run_id, benchmark_root)
    status = status_readonly(path)
    if status["state"] == "INITIALIZING":
        raise RuntimeGateError("INITIALIZING public runs are create-recovery only")
    return {**status, "backup": backup_status_readonly(path)}


def public_verify(run_id: str, *, benchmark_root: Path | None = None) -> dict[str, Any]:
    path = _checkpoint_path(run_id, benchmark_root)
    if status_readonly(path)["state"] == "INITIALIZING":
        raise RuntimeGateError("INITIALIZING public runs are create-recovery only")
    result = verify_readonly(path)
    errors = list(result.errors)
    try:
        backup = backup_status_readonly(path)
    except (OSError, PermissionError, CheckpointError, RuntimeGateError,
            sqlite3.DatabaseError, TypeError, ValueError) as exc:
        return {
            "ok": False,
            "errors": errors + [f"backup_anchor_invalid:{type(exc).__name__}"],
            "backup": {"required": True, "receipt_present": False,
                       "anchor_sha256": None, "snapshot_sha256": None},
        }
    if backup["required"] and not backup["receipt_present"]:
        errors.append("backup_receipt_missing")
    elif backup["receipt_present"]:
        try:
            conn = _readonly_connection(path)
            try:
                row = conn.execute(
                    "SELECT anchor_json,receipt_hash,receipt_json FROM backup_receipts "
                    "WHERE anchor_hash=?", (backup["anchor_sha256"],),
                ).fetchone()
            finally:
                conn.close()
            if not row:
                raise RuntimeGateError("backup receipt disappeared during verification")
            _canonical_db_object(
                str(row[0]), str(backup["anchor_sha256"]),
                "backup receipt anchor")
            receipt = _load_receipt(
                str(row[2]), str(row[1]),
                anchor_hash=str(backup["anchor_sha256"]))
            _verify_receipt_file(path.parent, receipt)
        except (OSError, PermissionError, CheckpointError, RuntimeGateError,
                sqlite3.DatabaseError) as exc:
            errors.append(f"backup_snapshot_invalid:{type(exc).__name__}")
    return {"ok": not errors, "errors": errors, "backup": backup}


def render_public(value: Mapping[str, Any]) -> str:
    """Canonical content-free CLI output."""
    return canonical_json(dict(value))


def _public_result(point: Checkpoint, run_id: str, *,
                   retry_not_before: float = 0.0,
                   survivor_count: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "run_id": run_id,
        "stage": "C",
        "state": point.state(),
        "calls_total": point.usage()["total"],
    }
    if retry_not_before > 0:
        result["retry_not_before"] = int(retry_not_before)
    if survivor_count is not None:
        result["survivor_count"] = survivor_count
    return result


def _readonly_public_result(run_id: str, *,
                            benchmark_root: Path | None = None) -> dict[str, Any]:
    status = public_status(run_id, benchmark_root=benchmark_root)
    return {"run_id": run_id, "stage": "C", **status}


def _ensure_final_snapshot(point: Checkpoint, lock: GlobalExecutionLock,
                           run_id: str) -> Path:
    """Compatibility wrapper around the exact anchor/receipt engine."""
    receipt = ensure_backup_receipt(point, lock)
    return _receipt_snapshot_path(
        point.path.parent, receipt["snapshot_run_relative_path"])


def _attempt_number(point: Checkpoint, *, work_id: str | None = None,
                    control_id: str | None = None,
                    first_class: str = "preflight_probe") -> tuple[int, str]:
    if (work_id is None) == (control_id is None):
        raise ValueError("exactly one attempt identity is required")
    column, identity = (("work_id", work_id) if work_id is not None
                        else ("control_id", control_id))
    row = point.conn.execute(
        f"SELECT attempt_no,state FROM attempts WHERE {column}=? "
        "ORDER BY attempt_no DESC LIMIT 1", (identity,)).fetchone()
    if row is None:
        return 1, "scored" if work_id is not None else first_class
    classes = {
        "SCHEMA_INVALID": "schema_retry",
        "RETRYABLE_TRANSPORT": "transport_orphan",
        "ORPHANED_UNKNOWN": "transport_orphan",
        "CANCELLED_UNVERIFIED": "transport_orphan",
    }
    call_class = classes.get(str(row[1]))
    if call_class is None:
        raise CheckpointError(f"attempt outcome {row[1]} is not retryable")
    return int(row[0]) + 1, call_class


def _stage_c_evidence(point: Checkpoint, work_ids: tuple[str, ...]) -> dict[str, list[Any]]:
    from .c0b2_stage_c import AttemptEvidence, StageCError

    evidence: dict[str, list[Any]] = {}
    for work_id in work_ids:
        rows: list[Any] = []
        for attempt_no, call_class, state, response, metadata_raw in point.conn.execute(
                "SELECT attempt_no,call_class,state,response,metadata_json FROM attempts "
                "WHERE work_id=? ORDER BY attempt_no", (work_id,)):
            try:
                metadata = json.loads(metadata_raw) if metadata_raw is not None else {}
            except (TypeError, json.JSONDecodeError) as exc:
                raise StageCError("attempt metadata is not valid JSON") from exc
            answered = state in {"ACCEPTED", "SCHEMA_INVALID"}
            if answered:
                flags = tuple(metadata.get(name) for name in (
                    "tools_empty", "images_empty", "unknown_message_fields_empty"))
                done_reason = metadata.get("done_reason")
                if (any(type(flag) is not bool for flag in flags)
                        or type(done_reason) is not str or not done_reason):
                    raise StageCError("accepted attempt lacks bounded channel metadata")
            else:
                flags = (True, True, True)
                done_reason = None
            rows.append(AttemptEvidence(
                int(attempt_no), str(call_class), str(state), response, done_reason,
                flags[0], flags[1], flags[2]))
        evidence[work_id] = rows
    return evidence


class _LiveSignalGuard:
    """Make the first signal durable/cancellable; leave a forced second possible."""

    def __init__(self, cancellation: Any, cancel_transport: Callable[[], None]):
        self.cancellation = cancellation
        self.cancel_transport = cancel_transport
        self.old: dict[int, Any] = {}
        self.count = 0

    def __enter__(self) -> "_LiveSignalGuard":
        if threading.current_thread() is threading.main_thread():
            for number in (signal.SIGINT, signal.SIGTERM):
                self.old[number] = signal.getsignal(number)
                signal.signal(number, self._handle)
        return self

    def _handle(self, _number: int, _frame: FrameType | None) -> None:
        self.count += 1
        if self.count == 1:
            self.cancellation.first_signal()
            self.cancel_transport()
            return
        self.cancellation.second_signal()
        raise KeyboardInterrupt

    def __exit__(self, *_args: object) -> None:
        for number, handler in self.old.items():
            signal.signal(number, handler)


def _run_public_stage_c_locked(
        point: Checkpoint, lock: GlobalExecutionLock, run_id: str,
        *, transport_factory: Callable[[Callable[[Any], Any], Mapping[str, Any]], Any] | None,
) -> dict[str, Any]:
    """Execute one already-authorized Stage-C invocation under the global lock."""
    from .c0b2_executor import (SERVER_CONTROL_MODEL, CancellationController,
                                ControlRequest, DurableExecutor,
                                InvocationCancelled, WorkRequest, control_id,
                                resource_probe_id,
                                stage_c_context_control_id)
    from .c0b2_stage_c import (build_stage_c_aggregate,
                               build_stage_c_selection, load_c44,
                               resolve_work)
    from .c0b2_transport import (BoundedOllamaTransport, RequestSpec,
                                 request_spec_hash)

    header = point.header()
    if header.get("run_type") != "public":
        raise RuntimeGateError("public command cannot open a private run")
    try:
        revalidate_source_pins(header)
        manifest_hash, _manifest_raw = point.load_manifest("master")
        plan_parent, plan_hash, plan_raw = point.load_plan("C")
        plan_value = json.loads(plan_raw)
        if (plan_parent != manifest_hash
                or plan_value.get("manifest_sha256") != manifest_hash):
            raise RuntimeGateError("Stage-C plan is not chained to the master manifest")
        corpus = load_c44(plan_value)
        if (corpus.plan_sha256 != plan_hash
                or corpus.master_manifest_sha256 != manifest_hash):
            raise RuntimeGateError("Stage-C fixture evidence differs from frozen inputs")
    except Exception:
        if point.state() not in TERMINAL_STATES and point.state() != "PAUSED_STAGE_BOUNDARY":
            finish_public_run_failure(point, terminal="BLOCKED_PROVENANCE")
        raise

    work_items = tuple(WorkItem(**item) for item in plan_value["work"])
    resolved = {item.work_id: resolve_work(
        plan_value, item.work_id, corpus=corpus) for item in work_items}
    control_specs: dict[str, RequestSpec] = {}

    def resolver(request: Any) -> RequestSpec:
        if isinstance(request, WorkRequest):
            item = resolved.get(request.work_id)
            if item is None:
                raise RuntimeGateError("transport requested work outside the frozen plan")
            return RequestSpec(
                kind="chat", payload=item.payload, worksheet=item.item.worksheet,
                expected_model=item.item.model,
                expected_digest=item.item.model_digest)
        spec = control_specs.get(request.control_id)
        if spec is None:
            raise RuntimeGateError("transport requested an unknown frozen control")
        return spec

    transport = (transport_factory(resolver, header) if transport_factory is not None
                 else BoundedOllamaTransport(
                     resolver, endpoint=header["ollama_endpoint"]))
    cancellation = CancellationController()
    context_specs: dict[str, RequestSpec] = {}
    context_hashes: dict[str, str] = {}
    for model, digest, think in MODELS:
        config_hash = stable_hash({
            "OPTIONS_C": dict(OPTIONS_C), "think": think,
            "keep_alive": KEEP_ALIVE,
        })
        spec = RequestSpec(
            kind="ps", expected_model=model, expected_digest=digest,
            min_context=8192, purpose="stage_c_context",
            config_sha256=config_hash)
        context_specs[model] = spec
        context_hashes[model] = request_spec_hash(spec)
        control_specs[stage_c_context_control_id(model, digest)] = spec

    executor = DurableExecutor(
        point, lock, transport, cancellation=cancellation,
        context_request_hashes=context_hashes)
    ordinal = 0

    def finish_or_wait(result: Any) -> dict[str, Any] | None:
        if result.outcome == "RETRY_WAIT":
            if executor.interruptible_backoff(result.retry_not_before):
                return None
        elif result.outcome in {"ACCEPTED", "SCHEMA_INVALID"}:
            return None
        return _public_result(
            point, run_id, retry_not_before=result.retry_not_before)

    def run_standard_control(kind: str, model: str, spec: RequestSpec) -> dict[str, Any] | None:
        identity = control_id("C", ordinal, kind, model)
        control_specs[identity] = spec
        request = ControlRequest(
            "C", identity, model, request_spec_hash(spec), 1,
            "preflight_probe")
        while True:
            result = executor.run_control(request, kind=kind)
            stopped = finish_or_wait(result)
            if result.outcome != "RETRY_WAIT" or stopped is not None:
                return stopped

    def drain_context() -> dict[str, Any] | None:
        while True:
            obligation = point.pending_context_obligation("C")
            if obligation is None:
                return None
            spec = context_specs[obligation.model]
            attempt_no, call_class = _attempt_number(
                point, control_id=obligation.control_id)
            request = ControlRequest(
                "C", obligation.control_id, obligation.model,
                request_spec_hash(spec), attempt_no, call_class)
            while True:
                result = executor.run_context_probe(request)
                stopped = finish_or_wait(result)
                if result.outcome != "RETRY_WAIT" or stopped is not None:
                    break
            if stopped is not None:
                return stopped

    def run_resource_obligation() -> dict[str, Any] | None:
        row = point.conn.execute(
            "SELECT model FROM model_backoff WHERE failures>=6 "
            "ORDER BY model LIMIT 1").fetchone()
        if row is None:
            return None
        model = str(row[0])
        probe = next((value for value in resolved.values()
                      if value.item.model == model
                      and value.item.worksheet == "v2"
                      and value.item.doc_id == "pos_pii_001"), None)
        if probe is None:
            raise RuntimeGateError("frozen Stage-C resource probe payload is missing")
        spec = RequestSpec(
            kind="chat", payload=probe.payload, worksheet="v2",
            expected_model=model, expected_digest=probe.item.model_digest)
        request_hash = request_spec_hash(spec)
        identity = resource_probe_id("C", ordinal, model, request_hash)
        control_specs[identity] = spec
        request = ControlRequest(
            "C", identity, model, request_hash, 1, "transport_orphan")
        while True:
            result = executor.run_resource_probe(request)
            stopped = finish_or_wait(result)
            if result.outcome != "RETRY_WAIT" or stopped is not None:
                return stopped

    with _LiveSignalGuard(cancellation, transport.cancel_current):
        try:
            _orphans, ordinal = executor.recover_and_start("C")
        except InvocationCancelled:
            return _public_result(point, run_id)
        controls = [
            ("version", SERVER_CONTROL_MODEL,
             RequestSpec(kind="version", expected_version=header["ollama_version"])),
            ("tags", SERVER_CONTROL_MODEL,
             RequestSpec(kind="tags", expected_models=header["model_digests"])),
            *(("show", model, RequestSpec(
                kind="show", expected_model=model, expected_digest=digest))
              for model, digest, _think in MODELS),
        ]
        for kind, model, spec in controls:
            stopped = run_standard_control(kind, model, spec)
            if stopped is not None:
                return stopped

        stopped = run_resource_obligation()
        if stopped is not None:
            return stopped
        stopped = drain_context()
        if stopped is not None:
            return stopped

        for item in work_items:
            while point.work(item.work_id)[0] not in {
                    "SUCCEEDED", "COMPLETED_INVALID"}:
                attempt_no, call_class = _attempt_number(point, work_id=item.work_id)
                request = WorkRequest(
                    "C", item.work_id, item.model, item.request_sha256,
                    attempt_no, call_class)
                result = executor.run(request)
                stopped = finish_or_wait(result)
                if stopped is not None:
                    return stopped
                if result.outcome in {"ACCEPTED", "SCHEMA_INVALID"}:
                    stopped = drain_context()
                    if stopped is not None:
                        return stopped

        if cancellation.event.is_set():
            point.cancel()
            return _public_result(point, run_id)
        pending = point.conn.execute(
            "SELECT count(*) FROM work_items WHERE stage='C' AND state NOT IN "
            "('SUCCEEDED','COMPLETED_INVALID')").fetchone()[0]
        dispatching = point.conn.execute(
            "SELECT count(*) FROM attempts WHERE stage='C' AND state='DISPATCHING'"
        ).fetchone()[0]
        if pending or dispatching or point.pending_context_obligation("C") is not None:
            raise CheckpointError("Stage-C finalization requires complete durable evidence")

        evidence = _stage_c_evidence(
            point, tuple(item.work_id for item in work_items))
        aggregate = build_stage_c_aggregate(
            plan_value, evidence, corpus=corpus)
        aggregate_hash = point.freeze_aggregate("C", plan_hash, aggregate)
        selection = build_stage_c_selection(aggregate)
        if selection["aggregate_sha256"] != aggregate_hash:
            raise RuntimeGateError("Stage-C selection changed the frozen aggregate hash")
        survivors = len(selection["survivors"])
        if survivors:
            point.freeze_stage_boundary_decision(
                "stage-c-selection", "C", plan_hash, aggregate_hash, selection)
        else:
            executor.finalize_stage_c_inconclusive(selection, {
                "version": "c0b2-result-v1", "terminal": "INCONCLUSIVE",
                "stage": "C", "aggregate_sha256": aggregate_hash,
                "reason": "no_stage_c_survivor",
            })
        _ensure_final_snapshot(point, lock, run_id)
        return _public_result(point, run_id, survivor_count=survivors)


def run_public_stage_c(
        run_id: str, *, resume: bool = False,
        benchmark_root: Path | None = None,
        transport_factory: Callable[[Callable[[Any], Any], Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Run one live Stage-C invocation after the CLI's explicit confirmation gate."""
    root = Path(benchmark_root) if benchmark_root is not None else report.bench_root()
    path = _checkpoint_path(run_id, root)
    state = public_status(run_id, benchmark_root=root)["state"]
    if state in _BACKUP_REQUIRED_STATES:
        with GlobalExecutionLock(root) as lock:
            point = Checkpoint.open(path, root)
            try:
                _ensure_final_snapshot(point, lock, run_id)
                return _public_result(point, run_id)
            finally:
                point.close()
    if state in TERMINAL_STATES:
        return _readonly_public_result(run_id, benchmark_root=root)
    if resume and state == "PREPARED":
        raise RuntimeGateError("prepared run requires the run command, not resume")
    if not resume and state != "PREPARED":
        raise RuntimeGateError("existing run requires the resume command")

    with GlobalExecutionLock(root) as lock:
        point = Checkpoint.open(path, root)
        try:
            state = point.state()
            if state in _BACKUP_REQUIRED_STATES:
                _ensure_final_snapshot(point, lock, run_id)
                return _public_result(point, run_id)
            if state in TERMINAL_STATES:
                return _public_result(point, run_id)
            return _run_public_stage_c_locked(
                point, lock, run_id, transport_factory=transport_factory)
        finally:
            point.close()
