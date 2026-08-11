"""Guarded creation, execution, and verification for C0B-4.

The module is deliberately independent of the near-limit C0B-2/C0B-3 runtimes.  It
reuses their frozen pure corpus, transport, filesystem, and metric primitives while the
child checkpoint and all policy-sensitive artifacts stay in the C0B-4 namespace.

DISPOSITION: benchmark-only; remove after the accepted confirmation is handed to C1.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from . import chunker, goldset, metrics, report
from .c0b2_fsprobe import (
    FilesystemProbe,
    GlobalExecutionLock,
    probe_filesystem,
)
from .c0b2_leakscan import (
    FROZEN_C0B4_PUBLIC_PATHS,
    WorktreeSeal,
    capture_worktree_seal,
    read_regular_file,
)
from .c0b2_plan import build_master_manifest, master_manifest_payload, stable_hash
from .c0b2_executor import (
    SERVER_CONTROL_MODEL, ControlRequest, FakeResponse, ProvenanceFailure,
    RetryableTransport, SafetyLimit, WorkRequest,
)
from .c0b2_public_schema import BackupReceipt, validate_artifact
from .c0b2_schema import schema_hash
from .c0b2_stage_f import _decoded_contains
from .c0b2_stage_f_plan import load_public_corpus
from .c0b2_transport import BoundedOllamaTransport, RequestSpec, request_spec_hash
from .c0b4_answer import assess_answer, prompt_template_hash
from .c0b4_backup import ensure_backup_receipt, verify_backup_readonly
from .c0b4_checkpoint import (
    CUMULATIVE_CAP,
    FROZEN_PARENT_BINDING,
    HEADER_VERSION,
    INVOCATION_CAPS,
    LEDGER_LIMITS,
    PARENT_RUN_ID,
    POLICY_ID,
    POLICY_SHA256,
    PROTOCOL_ID,
    RUN_ID_RE,
    SCHEMA_VERSION,
    TERMINAL_STATES,
    C0B4Checkpoint,
    C0B4BudgetError,
    canonical_json,
    sha256_json,
    status_readonly,
    validate_run_lineage,
    verify_parent_readonly,
    verify_readonly,
    _open_owner_file,
    _verify_sqlite_fd,
)
from .c0b4_executor import (
    ScoredWork, _require_transport_agreement,
    _runtime_event, _validate_response_shape, execute_scored,
    persist_scored_finish as _persist_finish,
    reconcile_runtime_events as _reconcile_runtime_events,
)
from .c0b4_filesystem import revalidate_frozen_filesystem
from .c0b4_plan import (
    LANE_ORDER, SELECTION, build_master_plan, build_request_resolver,
    validate_master_plan,
)
from .c0b4_policy import header_identity, protocol_sha256
from .c0b4_schema import C0B4ChunkRow, DedupEvidence, validate_artifact as validate_c0b4
from .c0b4_scoring import (
    build_acceptance_aggregate, build_lane_aggregate,
    build_precontrol_lane_aggregate,
    derive_parent_d50_component,
)
from .c0b2_schema import CATEGORIES

REPO_ROOT = Path(__file__).resolve().parents[2]
OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
OLLAMA_VERSION = "0.32.5"
JOURNAL_MODE = "DELETE"
PARENT_RECEIPT_SHA256 = FROZEN_PARENT_BINDING["backup_receipt_sha256"]


class C0B4RuntimeError(RuntimeError):
    """A runtime gate failed without exposing source or response content."""


class C0B4FilesystemError(C0B4RuntimeError):
    """The frozen benchmark mount or journaling capability changed."""


def _git(repo_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo_root, check=True, capture_output=True,
        text=True, shell=False).stdout.strip()


def _file_sha256(path: Path, *, root: Path) -> str:
    _verified, body = read_regular_file(path, trusted_root=root)
    return hashlib.sha256(body).hexdigest()


def _task_tree_sha256(repo_root: Path) -> str:
    rows = {
        relative: _file_sha256(repo_root / relative, root=repo_root)
        for relative in sorted(FROZEN_C0B4_PUBLIC_PATHS)
    }
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _require_clean_source(seal: WorktreeSeal) -> None:
    dirty = tuple(row.path for row in seal.entries
                  if row.path in FROZEN_C0B4_PUBLIC_PATHS)
    if dirty:
        raise C0B4RuntimeError(
            "commit the frozen C0B-4 implementation before create")


def _generation_options_sha256() -> str:
    rows = []
    for lane_id, seed in zip(LANE_ORDER, (17, 20260804, 1), strict=True):
        rows.append({
            "lane_id": lane_id, "model": SELECTION["model"], "seed": seed,
            "keep_alive": "15m", "think": False,
            "options": {
                "min_p": 0.0, "num_ctx": 8192, "num_predict": 1024,
                "repeat_last_n": 0, "repeat_penalty": 1.0, "seed": seed,
                "temperature": 0.0, "top_k": 1, "top_p": 1.0,
            },
        })
    return stable_hash(rows)


def _source_pins(repo_root: Path, seal: WorktreeSeal) -> dict[str, Any]:
    detector_hash = stable_hash({
        "metrics.py": _file_sha256(Path(metrics.__file__), root=repo_root),
        **{name: _file_sha256(
            repo_root / "scripts/analyst_benchmark" / name, root=repo_root)
           for name in ("c0b2_public_scoring.py", "c0b2_stage_c.py",
                        "c0b2_stage_d.py", "c0b2_stage_f.py",
                        "c0b4_answer.py", "c0b4_scoring.py")},
    })
    return {
        "protocol_sha256": protocol_sha256(repo_root),
        "git_head": _git(repo_root, "rev-parse", "HEAD"),
        "declared_dirty_state_sha256": seal.digest,
        "task_tree_sha256": _task_tree_sha256(repo_root),
        "fixture_sha256": _file_sha256(goldset.MANIFEST, root=repo_root),
        "schema_sha256": stable_hash({
            "worksheet_v2": schema_hash("v2"),
            "c0b4": _file_sha256(
                repo_root / "scripts/analyst_benchmark/c0b4_schema.py",
                root=repo_root),
        }),
        "prompt_sha256": prompt_template_hash("v2"),
        "chunker_sha256": _file_sha256(Path(chunker.__file__), root=repo_root),
        "detector_sha256": detector_hash,
        "generation_options_sha256": _generation_options_sha256(),
        "worktree_seal_sha256": seal.digest,
        "model_digests": {SELECTION["model"]: SELECTION["model_digest"]},
        "ollama_endpoint": OLLAMA_ENDPOINT,
        "ollama_version": OLLAMA_VERSION,
    }


def _parent_paths(root: Path) -> tuple[Path, Path]:
    run_dir = Path(root) / "runs" / PARENT_RUN_ID
    checkpoint = run_dir / "checkpoint.sqlite3"
    fd = os.open(checkpoint, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        conn = sqlite3.connect(
            f"file:/proc/self/fd/{fd}?mode=ro&immutable=1", uri=True, timeout=1.0)
        try:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute(
                "SELECT receipt_json FROM backup_receipts WHERE receipt_hash=?",
                (PARENT_RECEIPT_SHA256,)).fetchone()
        finally:
            conn.close()
    finally:
        os.close(fd)
    if not row:
        raise C0B4RuntimeError("frozen parent receipt is absent")
    try:
        receipt = validate_artifact(BackupReceipt, json.loads(row[0]))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise C0B4RuntimeError("frozen parent receipt is invalid") from exc
    if (receipt["snapshot_sha256"] != FROZEN_PARENT_BINDING["backup_snapshot_sha256"]
            or sha256_json(receipt) != PARENT_RECEIPT_SHA256):
        raise C0B4RuntimeError("frozen parent receipt identity changed")
    relative = PurePosixPath(receipt["snapshot_run_relative_path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise C0B4RuntimeError("frozen parent snapshot path is unsafe")
    return checkpoint, run_dir.joinpath(*relative.parts)


def _header(*, run_id: str, source: Mapping[str, Any], filesystem: FilesystemProbe,
            manifest_sha256: str) -> dict[str, Any]:
    if filesystem.selected_mode != JOURNAL_MODE:
        raise C0B4RuntimeError("benchmark filesystem did not pass DELETE+FULL")
    value = {
        "version": HEADER_VERSION, "run_type": "public_confirmation",
        **header_identity(str(source["protocol_sha256"])),
        "parent_binding": dict(FROZEN_PARENT_BINDING),
        **dict(source), "filesystem_selected_mode": JOURNAL_MODE,
        "master_manifest_sha256": manifest_sha256,
        "filesystem_capability_sha256": filesystem.capability_sha256,
        "mount": asdict(filesystem.fingerprint),
        "schema_version": SCHEMA_VERSION, "journal_mode": JOURNAL_MODE,
        "cumulative_cap": CUMULATIVE_CAP, "run_id": run_id,
        "limits": dict(LEDGER_LIMITS), "invocation_caps": dict(INVOCATION_CAPS),
    }
    # Policy constants are duplicated in the storage boundary intentionally and must agree.
    if value["policy_id"] != POLICY_ID or value["policy_sha256"] != POLICY_SHA256:
        raise C0B4RuntimeError("policy modules disagree")
    return value


def _new_run_id() -> str:
    stamp = time.strftime("c0b4-%Y%m%d-%H%M%S", time.gmtime())
    return f"{stamp}-{secrets.token_hex(12)}"


def create_confirmation_run(
        *, repo_root: Path = REPO_ROOT, benchmark_root: Path | None = None,
        run_id: str | None = None,
        parent_verifier: Callable[[Path, Mapping[str, Any]], None] | None = None,
        filesystem_probe: Callable[..., FilesystemProbe] = probe_filesystem,
        nonce_key: bytes | None = None,
) -> str:
    """Create the complete child plan without contacting Ollama."""
    root = Path(benchmark_root) if benchmark_root is not None else report.bench_root()
    identity = run_id or _new_run_id()
    if not RUN_ID_RE.fullmatch(identity):
        raise ValueError("invalid C0B-4 run id")
    parent_checkpoint, parent_snapshot = _parent_paths(root)
    # Verify before the filesystem probe can create a temporary child-side directory.
    verify_parent_readonly(
        parent_checkpoint, parent_snapshot, FROZEN_PARENT_BINDING,
        verifier=parent_verifier)
    seal = capture_worktree_seal(repo_root)
    _require_clean_source(seal)
    corpus_source = goldset.load(verify=True)
    manifest = build_master_manifest(corpus_source)
    manifest_body = master_manifest_payload(manifest)
    corpus = load_public_corpus(
        manifest_body, master_manifest_sha256=manifest.sha256,
        source=corpus_source)
    key = nonce_key if nonce_key is not None else secrets.token_bytes(32)
    if type(key) is not bytes or len(key) != 32:
        raise ValueError("C0B-4 nonce key must contain exactly 32 bytes")
    source = _source_pins(repo_root, seal)
    master = build_master_plan(
        corpus=corpus, run_nonce_key=key,
        protocol_sha256=source["protocol_sha256"])
    filesystem = filesystem_probe(root, modes=(JOURNAL_MODE,))
    header = _header(
        run_id=identity, source=source, filesystem=filesystem,
        manifest_sha256=manifest.sha256)

    def initialize(point: C0B4Checkpoint) -> None:
        point.store_artifact("master_plan", "master", master)
        for envelope in [*master["lane_plans"], master["acceptance_template"]]:
            lane = envelope["payload"]
            point.store_artifact("lane_plan", lane["lane_id"], lane)
        point.set_nonce_key(key)

    with GlobalExecutionLock(root):
        point = C0B4Checkpoint.create(
            root, identity, header=header,
            parent_checkpoint=parent_checkpoint, parent_snapshot=parent_snapshot,
            parent_verifier=parent_verifier, initializer=initialize)
        point.close()
    return identity


def _checkpoint_path(run_id: str, root: Path | None = None) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid C0B-4 run id")
    base = Path(root) if root is not None else report.bench_root()
    return base / "runs" / run_id / "checkpoint.sqlite3"


def confirmation_status(run_id: str, *, benchmark_root: Path | None = None
                        ) -> dict[str, Any]:
    value = status_readonly(_checkpoint_path(run_id, benchmark_root))
    return {**value, "benchmark_protocol_id": PROTOCOL_ID,
            "policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256}


def confirmation_verify(run_id: str, *, benchmark_root: Path | None = None
                        ) -> dict[str, Any]:
    root = Path(benchmark_root) if benchmark_root is not None else report.bench_root()
    path = _checkpoint_path(run_id, root)
    result = verify_readonly(path)
    errors = list(result.get("errors", ()))
    state = result.get("state")
    backup = {"required": state in TERMINAL_STATES, "receipt_present": False,
              "anchor_sha256": None, "snapshot_sha256": None}
    if not errors and state not in TERMINAL_STATES:
        try:
            _rederive_readonly(path, root)
        except Exception as exc:
            errors.append(f"rederive:{type(exc).__name__}")
    if not errors and state in TERMINAL_STATES:
        checked = verify_backup_readonly(
            path, semantic_verifier=lambda conn: _rederive_connection(conn, root))
        if checked.get("ok"):
            backup.update(
                receipt_present=True,
                anchor_sha256=checked.get("anchor_sha256"),
                snapshot_sha256=checked.get("snapshot_sha256"))
        else:
            errors.extend(f"backup:{item}" for item in checked.get("errors", ()))
    return {**result, "ok": not errors, "errors": errors,
            "benchmark_protocol_id": PROTOCOL_ID,
            "policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256,
            "backup": backup}


def render_public(value: Mapping[str, Any]) -> str:
    """Return canonical content-free CLI output."""
    return canonical_json(dict(value))


def revalidate_source_pins(header: Mapping[str, Any], *, repo_root: Path = REPO_ROOT
                           ) -> None:
    seal = capture_worktree_seal(repo_root)
    _require_clean_source(seal)
    current = _source_pins(repo_root, seal)
    changed = tuple(key for key, item in current.items() if header.get(key) != item)
    if changed:
        raise C0B4RuntimeError("immutable C0B-4 source identity drift")
    mount = header["mount"]
    from .c0b2_fsprobe import MountFingerprint
    try:
        revalidate_frozen_filesystem(
            MountFingerprint(**mount), Path(mount["canonical_path"]),
            header["journal_mode"], header["filesystem_capability_sha256"])
    except Exception as exc:
        raise C0B4FilesystemError("filesystem capability changed") from exc


def _utc(now: Callable[[], float] = time.time) -> str:
    return datetime.fromtimestamp(now(), timezone.utc).isoformat(
        timespec="microseconds").replace("+00:00", "Z")


def _identity(header: Mapping[str, Any]) -> dict[str, str]:
    return {key: str(header[key]) for key in (
        "policy_id", "policy_sha256", "protocol_sha256")}


def _public_result(point: C0B4Checkpoint) -> dict[str, Any]:
    return {
        "run_id": point.header()["run_id"], "state": point.state(),
        "charged_calls": len(point.list_attempts()),
        "benchmark_protocol_id": PROTOCOL_ID,
        "policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256,
    }


def _artifact(point: C0B4Checkpoint, kind: str,
              owner_id: str) -> dict[str, Any] | None:
    return point.read_artifact(kind, owner_id)


def _corpus(header: Mapping[str, Any]) -> Any:
    source = goldset.load(verify=True)
    manifest = build_master_manifest(source)
    if manifest.sha256 != header["master_manifest_sha256"]:
        raise C0B4RuntimeError("public corpus manifest changed")
    return load_public_corpus(
        master_manifest_payload(manifest),
        master_manifest_sha256=manifest.sha256, source=source)


class _Resolver:
    def __init__(self, master: Mapping[str, Any], corpus: Any, key: bytes):
        self.prepared = build_request_resolver(
            master, corpus=corpus, run_nonce_key=key)
        envelopes = [*master["lane_plans"], master["acceptance_template"]]
        self.work_ids = frozenset(
            row["work_id"] for envelope in envelopes
            for row in envelope["payload"]["work"])
        self.control_ids = frozenset(
            row["control_id"] for row in master["control_plan"].values())
        self.controls: dict[str, RequestSpec] = {}

    def __call__(self, request: WorkRequest | ControlRequest) -> RequestSpec:
        if isinstance(request, WorkRequest):
            return self.prepared.request_spec_for_work(request.work_id)
        try:
            return self.controls[request.control_id]
        except KeyError:
            raise C0B4RuntimeError("request names an unregistered control") from None


def _control_request(control_id: str, spec: RequestSpec, attempt_no: int
                     ) -> ControlRequest:
    model = (SERVER_CONTROL_MODEL if spec.kind in {"version", "tags"}
             else str(spec.expected_model))
    return ControlRequest(
        "F", control_id, model, request_spec_hash(spec), attempt_no,
        "preflight_control")


def _attempt_no(point: C0B4Checkpoint, owner_id: str) -> int:
    return 1 + sum(row["owner_id"] == owner_id for row in point.list_attempts())


def _call_control(
        point: C0B4Checkpoint, resolver: _Resolver, transport: Callable[..., Any],
        *, ordinal: int, control_id: str, spec: RequestSpec,
        call_class: str = "preflight_control",
        cancel: threading.Event | None = None,
        assessment_source: str | None = None,
        response_validator: Callable[[FakeResponse], None] | None = None,
        before_call: Callable[[], bool] | None = None,
) -> tuple[str, FakeResponse | None, str]:
    if before_call is not None and not before_call():
        return "PAUSED_SOFT_WALL", None, ""
    resolver.controls[control_id] = spec
    prior = [row for row in point.list_attempts()
             if row["owner_id"] == control_id]
    if prior and prior[-1]["state"] in {
            "RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN", "CANCELLED"}:
        call_class = "transport_orphan"
    if call_class == "schema_retry" and any(
            row["call_class"] == "schema_retry" for row in prior):
        return "BLOCKED_BUDGET", None, ""
    request = _control_request(control_id, spec, _attempt_no(point, control_id))
    point.precharge(
        attempt_id=request.attempt_id, owner_id=control_id,
        call_class=call_class, invocation_ordinal=ordinal,
        request_sha256=request.request_hash)
    event = cancel or threading.Event()
    try:
        response = transport(request, event)
    except RetryableTransport:
        point.record_attempt(request.attempt_id, "RETRYABLE_TRANSPORT",
                             {"answered": False})
        return "RETRYABLE_TRANSPORT", None, request.attempt_id
    except SafetyLimit:
        point.record_attempt(request.attempt_id, "FAILED_SAFETY",
                             {"answered": False})
        return "FAILED_SAFETY", None, request.attempt_id
    except ProvenanceFailure:
        point.record_attempt(request.attempt_id, "BLOCKED_PROVENANCE",
                             {"answered": False})
        return "BLOCKED_PROVENANCE", None, request.attempt_id
    if not isinstance(response, FakeResponse):
        point.record_attempt(request.attempt_id, "FAILED_SAFETY",
                             {"answered": True})
        return "FAILED_SAFETY", None, request.attempt_id
    try:
        if response_validator is not None:
            response_validator(response)
    except Exception:
        point.record_attempt(request.attempt_id, "FAILED_SAFETY", {
            "answered": True, "response": response.content,
            "metadata": dict(response.metadata),
        })
        return "FAILED_SAFETY", None, request.attempt_id
    recorded = "RAW_VALID"
    if assessment_source is not None:
        try:
            _validate_response_shape(response)
            assessment = assess_answer("v2", response.content, assessment_source)
            _require_transport_agreement(response, assessment)
        except Exception:
            point.record_attempt(request.attempt_id, "FAILED_SAFETY", {
                "answered": True, "response": response.content,
                "metadata": {"raw_response_sha256": hashlib.sha256(
                    response.content.encode()).hexdigest()},
            })
            return "FAILED_SAFETY", None, request.attempt_id
        recorded = (assessment.final_outcome if assessment.eventual_valid
                    else "SCHEMA_INVALID")
    point.record_attempt(request.attempt_id, recorded, {
        "answered": True, "response": response.content,
        "metadata": dict(response.metadata),
    })
    return recorded, response, request.attempt_id


def _preflight_specs(header: Mapping[str, Any]) -> tuple[tuple[str, RequestSpec], ...]:
    model, digest = SELECTION["model"], SELECTION["model_digest"]
    return (
        ("version", RequestSpec(kind="version",
                                expected_version=header["ollama_version"])),
        ("tags", RequestSpec(kind="tags", expected_models={model: digest})),
        ("show", RequestSpec(kind="show", expected_model=model,
                             expected_digest=digest)),
    )


def _run_preflight(point: C0B4Checkpoint, resolver: _Resolver,
                   transport: Callable[..., Any], ordinal: int,
                   before_call: Callable[[], bool] | None = None,
                   ) -> tuple[str, str | None]:
    for kind, spec in _preflight_specs(point.header()):
        owner = stable_hash({"c0b4_preflight": kind, "invocation": ordinal})
        outcome, _response, attempt_id = _call_control(
            point, resolver, transport, ordinal=ordinal,
            control_id=owner, spec=spec, before_call=before_call)
        if outcome != "RAW_VALID":
            return outcome, attempt_id
    return "RAW_VALID", None


def _assessment_rows(point: C0B4Checkpoint, work: Mapping[str, Any]
                     ) -> list[tuple[dict[str, Any], Any]]:
    rows = []
    for attempt in point.list_attempts():
        if attempt["owner_id"] != work["work_id"]:
            continue
        payload = attempt["payload"] or {}
        raw = payload.get("response")
        if type(raw) is str:
            rows.append((attempt, assess_answer("v2", raw, work["source"])))
    return rows


def _work_evidence(point: C0B4Checkpoint, work: Mapping[str, Any], *,
                   persist_dedup: bool = True,
                   ) -> dict[str, Any]:
    rows = _assessment_rows(point, work)
    if not rows:
        raise C0B4RuntimeError("terminal work lacks bounded HTTP evidence")
    accepted = next((row for row in rows if row[1].eventual_valid), None)
    authoritative = accepted or rows[-1]
    attempt, assessment = authoritative
    metadata = (attempt["payload"] or {}).get("metadata", {})
    answered_meta = [(row[0]["payload"] or {}).get("metadata", {}) for row in rows]
    eventual = assessment.eventual_valid
    retained = assessment.retained_value if eventual else None
    findings = [] if retained is None else [
        {"category": row["category"], "quote": row["quote"]}
        for row in retained["findings"]]
    categories = [name for name in CATEGORIES
                  if any(row["category"] == name for row in findings)]
    strict_count = sum(row.get("strict_schema_invalid") is True
                       for row in answered_meta)
    semantic_count = sum(row.get("semantic_invalid") is True
                         for row in answered_meta)
    prompt_counts = [row.get("prompt_eval_count") for row in answered_meta
                     if type(row.get("prompt_eval_count")) is int]
    if not prompt_counts:
        raise C0B4RuntimeError("answered work lacks prompt-evaluation evidence")
    outcome = assessment.final_outcome if eventual else "INVALID"
    dedup = None
    dedup_hash = None
    if outcome == "NORMALIZED_DUPLICATE":
        dedup = {
            "version": "c0b4-dedup-evidence-v1", **_identity(point.header()),
            "work_id": work["work_id"], "attempt_id": attempt["attempt_id"],
            "raw_response_sha256": metadata["raw_response_sha256"],
            "dedupe_key": "category+nfc_quote",
            "removed_index": assessment.removed_finding_indices[0],
            "raw_counts": {**assessment.raw_counts.as_dict(),
                           "semantic_invalid_attempts": semantic_count},
            "retained_counts": assessment.retained_counts.as_dict(),
        }
        dedup["evidence_sha256"] = sha256_json(dedup)
        dedup = DedupEvidence.model_validate(
            dedup, strict=True).model_dump(mode="json")
        stored_dedup = _artifact(point, "dedup_evidence", work["work_id"])
        if stored_dedup is None and not persist_dedup:
            raise C0B4RuntimeError("normalized attempt lacks dedup artifact")
        dedup_hash = (_store_once(
            point, "dedup_evidence", work["work_id"], dedup)
                      if stored_dedup is None else sha256_json(stored_dedup))
        if stored_dedup is not None and canonical_json(stored_dedup) != canonical_json(dedup):
            raise C0B4RuntimeError("stored dedup evidence does not rederive")
    raw_counts = assessment.raw_counts
    retained_counts = assessment.retained_counts
    chunk = {
        "work_id": work["work_id"], "doc_id": work["doc_id"],
        "chunk_index": work["chunk_index"],
        "first_pass_valid": rows[0][1].raw_first_pass_valid,
        "eventual_valid": eventual, "charged_attempt_count": sum(
            row["owner_id"] == work["work_id"] for row in point.list_attempts()),
        "strict_schema_invalid_attempts": strict_count,
        "semantic_invalid_attempts": semantic_count,
        "assessment": retained.get("assessment") if retained else None,
        "predicted_categories": categories,
        "raw_findings": raw_counts.findings,
        "raw_grounded_findings": raw_counts.grounded_findings,
        "retained_findings": retained_counts.findings,
        "retained_grounded_findings": retained_counts.grounded_findings,
        "authoritative_done_reason": metadata.get("done_reason") if eventual else None,
        "length_outcomes": sum(row.get("done_reason") == "length"
                               for row in answered_meta),
        "max_answered_prompt_eval_count": max(prompt_counts),
        "headroom_passed": all(
            count + SELECTION["num_predict"] <= int(.85 * SELECTION["num_ctx"])
            for count in prompt_counts),
        "tools_empty": all(row.get("tools_empty") is True for row in answered_meta),
        "images_empty": all(row.get("images_empty") is True for row in answered_meta),
        "unknown_message_fields_empty": all(
            row.get("unknown_message_fields_empty") is True for row in answered_meta),
        "schema_escape_empty": all(row[1].schema_escape_empty for row in rows),
        "marker_in_answer": any(_decoded_contains(
            work["nonce"], str((row[0]["payload"] or {}).get("response", "")))
            for row in rows),
        "raw_first_pass_valid": rows[0][1].raw_first_pass_valid,
        "final_outcome": outcome, "redundant_rows": assessment.redundant_rows,
        "removed_finding_indices": list(assessment.removed_finding_indices),
        "dedup_evidence_sha256": dedup_hash,
    }
    chunk = C0B4ChunkRow.model_validate(chunk, strict=True).model_dump(mode="json")
    return {"chunk": chunk, "retained_findings": findings,
            "dedup_evidence": dedup}


def _store_once(point: C0B4Checkpoint, kind: str, owner: str,
                value: Mapping[str, Any]) -> str:
    existing = _artifact(point, kind, owner)
    if existing is not None:
        if canonical_json(existing) != canonical_json(dict(value)):
            raise C0B4RuntimeError("durable artifact differs on resume")
        return sha256_json(existing)
    return point.store_artifact(kind, owner, value)


def _resolved_work(resolver: _Resolver,
                   work: Mapping[str, Any]) -> dict[str, Any]:
    resolved = resolver.prepared.resolve_work(work["work_id"])
    return {**dict(work), "source": resolved["chunk_text"]}


def _ensure_context(
        point: C0B4Checkpoint, resolver: _Resolver, transport: Callable[..., Any],
        *, ordinal: int, controls: Mapping[str, Any],
        trigger_work: Mapping[str, Any], trigger_attempt_id: str,
        before_call: Callable[[], bool] | None = None,
) -> tuple[str, str | None]:
    control = controls["context"]
    frozen = control.control
    existing = _artifact(point, "context_evidence", frozen["control_id"])
    if existing is not None:
        return "RAW_VALID", sha256_json(existing)
    built: dict[str, dict[str, Any]] = {}

    def validate_response(response: FakeResponse) -> None:
        body = json.loads(response.content)
        evidence = {
            "version": "c0b4-context-evidence-v1", **_identity(point.header()),
            "control_id": frozen["control_id"], "lane_id": "F72_17",
            "purpose": frozen["purpose"], "candidate_id": frozen["candidate_id"],
            "model": frozen["model"], "model_digest": frozen["model_digest"],
            "config_sha256": frozen["config_sha256"],
            "prompt_sha256": frozen["prompt_sha256"], "expected_num_ctx": 8192,
            "observed_context_length": body["context_length"],
            "trigger_work_id": trigger_work["work_id"],
            "trigger_attempt_id": trigger_attempt_id,
            "trigger_request_sha256": trigger_work["request_sha256"],
            "trigger_nonce": trigger_work["nonce"], "state": "PASSED",
            "response_sha256": response.metadata["response_sha256"],
        }
        built["evidence"] = validate_c0b4(evidence)

    durable = [row for row in point.list_attempts()
               if row["owner_id"] == frozen["control_id"]
               and row["state"] == "RAW_VALID"]
    if durable:
        if len(durable) != 1:
            raise C0B4RuntimeError("context response census is not exact")
        payload = durable[0]["payload"] or {}
        validate_response(FakeResponse(
            payload.get("response"), payload.get("metadata") or {}))
        return "RAW_VALID", _store_once(
            point, "context_evidence", frozen["control_id"], built["evidence"])

    outcome, response, attempt_id = _call_control(
        point, resolver, transport, ordinal=ordinal,
        control_id=frozen["control_id"], spec=control.request_spec,
        response_validator=validate_response,
        before_call=before_call)
    if outcome != "RAW_VALID" or response is None:
        return outcome, attempt_id
    return "RAW_VALID", _store_once(
        point, "context_evidence", frozen["control_id"], built["evidence"])


def _next_work_disposition(point: C0B4Checkpoint,
                           work: Mapping[str, Any],
                           lane_work_ids: frozenset[str] | None = None,
                           ) -> tuple[str, int, int]:
    attempts = [row for row in point.list_attempts()
                if row["owner_id"] == work["work_id"]]
    answered = [row for row in attempts if type(
        (row["payload"] or {}).get("response")) is str]
    accepted = any(row["state"] in {"RAW_VALID", "NORMALIZED_DUPLICATE"}
                   for row in attempts)
    if accepted:
        return "complete", len(attempts) + 1, len(answered)
    if answered:
        last_assessment = assess_answer(
            "v2", answered[-1]["payload"]["response"], work["source"])
        if len(answered) >= 2 or not last_assessment.schema_retry_allowed:
            return "complete", len(attempts) + 1, len(answered)
    if not attempts:
        call_class = "scored"
    elif attempts[-1]["state"] in {"RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN"}:
        call_class = "transport_orphan"
    elif answered:
        if lane_work_ids is not None and any(
                row["call_class"] == "schema_retry"
                and row["owner_id"] in lane_work_ids
                for row in point.list_attempts()):
            return "budget", len(attempts) + 1, len(answered)
        call_class = "schema_retry"
    else:
        call_class = "transport_orphan"
    return call_class, len(attempts) + 1, len(answered)


def _run_lane_work(
        point: C0B4Checkpoint, resolver: _Resolver, transport: Callable[..., Any],
        *, ordinal: int, lane: Mapping[str, Any], master: Mapping[str, Any],
        corpus: Any, key: bytes, controls: Mapping[str, Any],
        clock_started: float, monotonic: Callable[[], float],
        soft_wall_seconds: float, cancellation: threading.Event,
) -> tuple[str, str | None]:
    lane_id = lane["lane_id"]
    lane_work_ids = frozenset(row["work_id"] for row in lane["work"])
    durable = [row for row in point.list_attempts()
               if row["owner_id"] in lane_work_ids]
    prior_failure = next((row for row in durable if row["state"] in {
        "FAILED_SAFETY", "BLOCKED_PROVENANCE"}), None)
    if prior_failure is not None:
        return prior_failure["state"], prior_failure["attempt_id"]
    if lane_id == "F72_17" and _artifact(
            point, "context_evidence",
            controls["context"].control["control_id"]) is None:
        trigger = next((row for row in durable if type(
            (row["payload"] or {}).get("response")) is str), None)
        if trigger is not None:
            planned = next(row for row in lane["work"]
                           if row["work_id"] == trigger["owner_id"])
            trigger_work = _resolved_work(resolver, planned)
            state, owner = _ensure_context(
                point, resolver, transport, ordinal=ordinal, controls=controls,
                trigger_work=trigger_work,
                trigger_attempt_id=trigger["attempt_id"],
                before_call=lambda: monotonic() - clock_started < soft_wall_seconds)
            if state != "RAW_VALID":
                return ("PAUSED_RESOURCE" if state == "RETRYABLE_TRANSPORT"
                        else state), owner
    for planned in lane["work"]:
        if cancellation.is_set():
            return "CANCELLED_PENDING_RESUME", None
        if monotonic() - clock_started >= soft_wall_seconds:
            return "PAUSED_SOFT_WALL", None
        work = _resolved_work(resolver, planned)
        disposition, attempt_no, answered = _next_work_disposition(
            point, work, lane_work_ids)
        if disposition == "budget":
            return "BLOCKED_BUDGET", None
        if disposition == "complete":
            continue
        scored = ScoredWork(
            work["work_id"], work["request_sha256"], work["model"],
            work["worksheet"], work["source"])

        def precharge(request: WorkRequest) -> None:
            point.precharge(
                attempt_id=request.attempt_id, owner_id=work["work_id"],
                call_class=disposition, invocation_ordinal=ordinal,
                request_sha256=request.request_hash)
            _runtime_event(point, event="DISPATCHING", lane_id=lane_id,
                           attempt_id=request.attempt_id, work=work)

        result = execute_scored(
            scored, attempt_no=attempt_no, call_class=disposition,
            answered_attempts_before=min(answered, 1), precharge=precharge,
            finish=lambda request, finish: _persist_finish(
                point, lane_id, work, request, finish),
            transport=transport, cancellation=cancellation)
        if result.outcome == "FAILED_SAFETY":
            return "FAILED_SAFETY", result.attempt_id
        if result.outcome == "BLOCKED_PROVENANCE":
            return "BLOCKED_PROVENANCE", result.attempt_id
        if result.outcome == "RETRYABLE_TRANSPORT":
            return "PAUSED_RESOURCE", result.attempt_id
        if result.outcome == "CANCELLED":
            return "CANCELLED_PENDING_RESUME", result.attempt_id
        # The context proof owns the first bounded HTTP terminal and must be
        # durable before a schema retry or any later scored request.
        if lane_id == "F72_17" and _artifact(
                point, "context_evidence",
                controls["context"].control["control_id"]) is None:
            state, owner = _ensure_context(
                point, resolver, transport, ordinal=ordinal, controls=controls,
                trigger_work=work, trigger_attempt_id=result.attempt_id,
                before_call=lambda: monotonic() - clock_started < soft_wall_seconds)
            if state != "RAW_VALID":
                return ("PAUSED_RESOURCE" if state == "RETRYABLE_TRANSPORT"
                        else state), owner
        if result.retry_class is None:
            _work_evidence(point, work)
        else:
            # Restart the durable cursor so the just-finished invalid answer is
            # re-derived before its one allowed schema retry is dispatched.
            return _run_lane_work(
                point, resolver, transport, ordinal=ordinal, lane=lane,
                master=master, corpus=corpus, key=key, controls=controls,
                clock_started=clock_started, monotonic=monotonic,
                soft_wall_seconds=soft_wall_seconds, cancellation=cancellation)
    return "RAW_VALID", None


class _FirstByteEvent(threading.Event):
    def __init__(self, monotonic: Callable[[], float]):
        super().__init__()
        self.monotonic, self.first_set = monotonic, None

    def set(self) -> None:
        if self.first_set is None:
            self.first_set = self.monotonic()
        super().set()


def _derive_cancel_health(point: C0B4Checkpoint,
                          controls: Mapping[str, Any]) -> dict[str, Any]:
    cancel, health = controls["cancellation"], controls["health"]
    cancelled = [row for row in point.list_attempts()
                 if row["owner_id"] == cancel.control["control_id"]
                 and row["state"] == "CANCELLED_UNVERIFIED"]
    if len(cancelled) != 1:
        raise C0B4RuntimeError("cancellation evidence census is not exact")
    cancel_row = cancelled[0]
    cancel_payload = cancel_row["payload"] or {}
    health_attempts = [row for row in point.list_attempts()
                       if row["owner_id"] == health.control["control_id"]]
    health_rows = []
    for row in health_attempts:
        raw = (row["payload"] or {}).get("response")
        if type(raw) is str:
            health_rows.append((row, assess_answer(
                "v2", raw, health.source_chunk or "")))
    if not health_rows:
        raise C0B4RuntimeError("health evidence is absent")
    last_attempt, answer = health_rows[-1]
    meta = last_attempt["payload"]["metadata"]
    retained = answer.retained_value or {"findings": []}
    emitted_pii = any(row["category"] == "pii" for row in retained["findings"])
    grounding_by_index = {row.index: row.grounded for row in answer.grounding}
    kept_indices = ([index for index in range(len(answer.raw_value["findings"]))
                     if index not in answer.removed_finding_indices]
                    if answer.raw_value is not None else [])
    grounded_pii = answer.eventual_valid and any(
        row["category"] == "pii" and grounding_by_index.get(index, False)
        for index, row in zip(kept_indices, retained["findings"], strict=True))
    elapsed_ms = cancel_payload["cancel_elapsed_ms"]
    failure_reasons = []
    if cancel_payload.get("first_byte_seen") is not True:
        failure_reasons.append("cancel_not_observed")
    elif elapsed_ms > 5000:
        failure_reasons.append("cancel_after_5_seconds")
    if not answer.eventual_valid:
        failure_reasons.append("health_eventual_invalid")
    elif not emitted_pii:
        failure_reasons.append("health_pii_missing")
    elif not grounded_pii:
        failure_reasons.append("health_grounding_failure")
    if meta.get("done_reason") == "length":
        failure_reasons.append("health_length_outcome")
    if not all(meta.get(key) is True for key in (
            "tools_empty", "images_empty", "unknown_message_fields_empty")) \
            or not answer.schema_escape_empty:
        failure_reasons.append("health_channel_violation")
    prompt_count = meta.get("prompt_eval_count")
    headroom = (type(prompt_count) is int and
                prompt_count + 1024 <= int(.85 * 8192))
    if not headroom:
        failure_reasons.append("health_context_headroom_failure")
    not_before_utc = cancel_payload["health_not_before_utc"]
    started = health_attempts[0].get("created")
    started_utc = (datetime.fromtimestamp(started, timezone.utc).isoformat(
        timespec="microseconds").replace("+00:00", "Z")
                   if type(started) in (int, float) else not_before_utc)
    return validate_c0b4({
        "version": "c0b4-cancellation-health-evidence-v1",
        **_identity(point.header()), "lane_id": "F72_17",
        "candidate_id": cancel.control["candidate_id"],
        "prompt_sha256": health.control["prompt_sha256"],
        "cancel_control_id": cancel.control["control_id"],
        "cancel_attempt_id": cancel_row["attempt_id"],
        "cancel_state": "CANCELLED_UNVERIFIED",
        "cancel_first_byte_seen": cancel_payload.get("first_byte_seen") is True,
        "cancel_elapsed_ms": elapsed_ms,
        "health_control_id": health.control["control_id"],
        "health_work_id": health.control["health_work_id"],
        "health_attempt_ids": [row["attempt_id"] for row in health_attempts],
        "not_before_utc": not_before_utc, "started_at_utc": started_utc,
        "eventual_valid": answer.eventual_valid,
        "retained_grounded_pii": grounded_pii,
        "authoritative_done_reason": meta.get("done_reason")
            if answer.eventual_valid else None,
        "max_answered_prompt_eval_count": prompt_count
            if answer.eventual_valid else None,
        "length_outcomes": int(meta.get("done_reason") == "length"),
        "headroom_passed": headroom,
        "tools_empty": meta.get("tools_empty") is True,
        "images_empty": meta.get("images_empty") is True,
        "unknown_message_fields_empty":
            meta.get("unknown_message_fields_empty") is True,
        "schema_escape_empty": answer.schema_escape_empty,
        "passed": not failure_reasons, "failure_reasons": failure_reasons,
    })


def _derive_context(point: C0B4Checkpoint, resolver: _Resolver,
                    controls: Mapping[str, Any]) -> dict[str, Any]:
    """Rederive the context proof from its durable control and first trigger."""
    frozen = controls["context"].control
    context_rows = [row for row in point.list_attempts()
                    if row["owner_id"] == frozen["control_id"]
                    and row["state"] == "RAW_VALID"
                    and type((row["payload"] or {}).get("response")) is str]
    f17 = point.read_artifact("lane_plan", "F72_17")
    work_ids = {row["work_id"] for row in f17["work"]}
    attempts = {row["attempt_id"]: row for row in point.list_attempts()}
    ordered = [attempts[row[0]] for row in point.conn.execute(
        "SELECT attempt_id FROM attempt_history "
        "WHERE state!='DISPATCHING' ORDER BY seq") if row[0] in attempts]
    trigger = next((row for row in ordered if row["owner_id"] in work_ids
                    and type((row["payload"] or {}).get("response")) is str), None)
    if len(context_rows) != 1 or trigger is None:
        raise C0B4RuntimeError("context evidence census is not exact")
    attempt = context_rows[0]
    work = resolver.prepared.resolve_work(trigger["owner_id"])["work"]
    try:
        response = json.loads(attempt["payload"]["response"])
        response_sha = attempt["payload"]["metadata"]["response_sha256"]
        observed = response["context_length"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise C0B4RuntimeError("context response is not rederivable") from exc
    return validate_c0b4({
        "version": "c0b4-context-evidence-v1", **_identity(point.header()),
        "control_id": frozen["control_id"], "lane_id": "F72_17",
        "purpose": frozen["purpose"], "candidate_id": frozen["candidate_id"],
        "model": frozen["model"], "model_digest": frozen["model_digest"],
        "config_sha256": frozen["config_sha256"],
        "prompt_sha256": frozen["prompt_sha256"], "expected_num_ctx": 8192,
        "observed_context_length": observed,
        "trigger_work_id": work["work_id"],
        "trigger_attempt_id": trigger["attempt_id"],
        "trigger_request_sha256": work["request_sha256"],
        "trigger_nonce": work["nonce"], "state": "PASSED",
        "response_sha256": response_sha,
    })


def _validate_health_response(response: FakeResponse) -> None:
    """Require metadata needed for post-response health evidence construction."""
    metadata = response.metadata
    if (type(metadata.get("done_reason")) is not str
            or not 1 <= len(metadata["done_reason"]) <= 80
            or type(metadata.get("prompt_eval_count")) is not int
            or metadata["prompt_eval_count"] < 0
            or any(type(metadata.get(key)) is not bool for key in (
                "tools_empty", "images_empty", "unknown_message_fields_empty"))):
        raise C0B4RuntimeError("health response metadata is incomplete")


def _rederive_connection(conn: sqlite3.Connection, root: Path) -> None:
    """Independently replay one already-pinned read-only connection."""
    point = object.__new__(C0B4Checkpoint)
    point.conn, point.root, point.path = conn, root, Path()
    attempts = point.list_attempts()
    point.list_attempts = lambda attempt_id=None: [dict(row) for row in attempts
        if attempt_id is None or row["attempt_id"] == attempt_id]
    header = point.header()
    validate_run_lineage(conn, header)
    corpus = _corpus(header)
    master = validate_master_plan(
        point.read_artifact("master_plan", "master"), corpus=corpus,
        run_nonce_key=point.read_nonce_key())
    resolver = _Resolver(master, corpus, point.read_nonce_key())
    controls = resolver.prepared.resolve_controls()
    context = point.read_artifact(
        "context_evidence", controls["context"].control["control_id"])
    if context is not None and canonical_json(context) != canonical_json(
            _derive_context(point, resolver, controls)):
        raise C0B4RuntimeError("context evidence does not rederive")
    cancel = point.read_artifact(
        "cancellation_health_evidence",
        controls["cancellation"].control["control_id"])
    if cancel is not None and canonical_json(cancel) != canonical_json(
            _derive_cancel_health(point, controls)):
        raise C0B4RuntimeError("cancellation evidence does not rederive")
    aggregates = {}
    for lane_id in LANE_ORDER:
        lane = point.read_artifact("lane_plan", lane_id)
        kind = "c44_aggregate" if lane_id == "C44_1" else "lane_aggregate"
        stored = point.read_artifact(kind, lane_id)
        if stored is None:
            continue
        evidence = _lane_evidence(point, lane, resolver, persist_dedup=False)
        if lane_id == "F72_17":
            if context is None:
                raise C0B4RuntimeError("seed-17 context evidence is absent")
            exact = (build_precontrol_lane_aggregate(
                lane, evidence, corpus=corpus,
                context_evidence_sha256=sha256_json(context)) if cancel is None else
                build_lane_aggregate(
                    lane, evidence, corpus=corpus,
                    context_evidence_sha256=sha256_json(context),
                    cancellation_health_evidence_sha256=sha256_json(cancel),
                    controls_passed=cancel["passed"]))
        else:
            exact = build_lane_aggregate(lane, evidence, corpus=corpus)
        if exact is None or canonical_json(stored) != canonical_json(exact):
            raise C0B4RuntimeError("lane aggregate does not rederive")
        aggregates[lane_id] = exact
    acceptance = point.read_artifact("acceptance_aggregate", "complete")
    if acceptance is not None:
        exact = build_acceptance_aggregate(
            aggregates["C44_1"], _parent_d50(root, corpus),
            aggregates["F72_17"], corpus=corpus,
            acceptance_plan_sha256=point.read_artifact(
                "lane_plan", "C44_1")["plan_sha256"],
            cancellation_health_passed=cancel["passed"],
            provenance_passed=True, safety_passed=True)
        if canonical_json(acceptance) != canonical_json(exact):
            raise C0B4RuntimeError("acceptance aggregate does not rederive")


def _rederive_readonly(path: Path, root: Path) -> None:
    fd = _open_owner_file(path)
    try:
        conn = _verify_sqlite_fd(fd)
        try:
            _rederive_connection(conn, root)
        finally:
            conn.close()
    finally:
        os.close(fd)


def _run_cancel_health(
        point: C0B4Checkpoint, resolver: _Resolver, transport: Callable[..., Any],
        *, ordinal: int, controls: Mapping[str, Any], monotonic: Callable[[], float],
        sleep: Callable[[float], None], now: Callable[[], float],
        before_call: Callable[[], bool] | None = None,
) -> tuple[str, str | None]:
    cancel = controls["cancellation"]
    health = controls["health"]
    existing = _artifact(
        point, "cancellation_health_evidence", cancel.control["control_id"])
    if existing is not None:
        return "RAW_VALID", sha256_json(existing)
    cancel_rows = [row for row in point.list_attempts()
                   if row["owner_id"] == cancel.control["control_id"]]
    failed_cancel = next((row for row in reversed(cancel_rows)
                          if row["state"] in {
                              "FAILED_SAFETY", "BLOCKED_PROVENANCE"}), None)
    if failed_cancel is not None:
        return failed_cancel["state"], failed_cancel["attempt_id"]
    cancelled = next((row for row in reversed(cancel_rows)
                      if row["state"] == "CANCELLED_UNVERIFIED"), None)
    if cancelled is None:
        if before_call is not None and not before_call():
            return "PAUSED_SOFT_WALL", None
        event = _FirstByteEvent(monotonic)
        resolver.controls[cancel.control["control_id"]] = cancel.request_spec
        request = _control_request(
            cancel.control["control_id"], cancel.request_spec,
            _attempt_no(point, cancel.control["control_id"]))
        call_class = ("transport_orphan" if cancel_rows and
                      cancel_rows[-1]["state"] in {
                          "RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN"}
                      else "preflight_control")
        point.precharge(
            attempt_id=request.attempt_id, owner_id=cancel.control["control_id"],
            call_class=call_class, invocation_ordinal=ordinal,
            request_sha256=request.request_hash)
        try:
            transport(request, event)
        except RetryableTransport:
            if event.first_set is None:
                point.record_attempt(request.attempt_id, "RETRYABLE_TRANSPORT",
                                     {"answered": False})
                return "PAUSED_RESOURCE", request.attempt_id
            elapsed_ms = int((monotonic() - event.first_set) * 1000)
            point.record_cancelled_attempt(
                request.attempt_id, first_byte_seen=True,
                cancel_elapsed_ms=elapsed_ms)
            cancelled = point.list_attempts(request.attempt_id)[0]
        except SafetyLimit:
            point.record_attempt(
                request.attempt_id, "FAILED_SAFETY", {"answered": False})
            return "FAILED_SAFETY", request.attempt_id
        except ProvenanceFailure:
            point.record_attempt(
                request.attempt_id, "BLOCKED_PROVENANCE", {"answered": False})
            return "BLOCKED_PROVENANCE", request.attempt_id
        else:
            point.record_attempt(
                request.attempt_id, "FAILED_SAFETY", {"answered": True})
            return "FAILED_SAFETY", request.attempt_id
    cancel_payload = cancelled["payload"] or {}
    elapsed_ms = cancel_payload["cancel_elapsed_ms"]
    not_before_utc = cancel_payload["health_not_before_utc"]
    not_before = datetime.fromisoformat(
        not_before_utc.replace("Z", "+00:00")).timestamp()
    remaining = max(0.0, not_before - now())
    if remaining:
        sleep(remaining)
    while True:
        durable_health = [row for row in point.list_attempts()
                          if row["owner_id"] == health.control["control_id"]]
        failed_health = next((row for row in reversed(durable_health)
                              if row["state"] in {
                                  "FAILED_SAFETY", "BLOCKED_PROVENANCE"}), None)
        if failed_health is not None:
            return failed_health["state"], failed_health["attempt_id"]
        health_rows = []
        for row in durable_health:
            raw = (row["payload"] or {}).get("response")
            if type(raw) is str:
                health_rows.append((row, assess_answer(
                    "v2", raw, health.source_chunk or "")))
        if health_rows:
            answer = health_rows[-1][1]
            if (answer.eventual_valid or len(health_rows) >= 2
                    or not answer.schema_retry_allowed):
                break
            call_class = "schema_retry"
        else:
            call_class = "preflight_control"
        outcome, response, attempt_id = _call_control(
            point, resolver, transport, ordinal=ordinal,
            control_id=health.control["control_id"], spec=health.request_spec,
            call_class=call_class,
            assessment_source=health.source_chunk or "",
            response_validator=_validate_health_response,
            before_call=before_call)
        if outcome == "RETRYABLE_TRANSPORT":
            return "PAUSED_RESOURCE", attempt_id
        if outcome in {"FAILED_SAFETY", "BLOCKED_PROVENANCE",
                       "BLOCKED_BUDGET", "PAUSED_SOFT_WALL"} or response is None:
            return outcome, attempt_id
    evidence = _derive_cancel_health(point, controls)
    return "RAW_VALID", _store_once(
        point, "cancellation_health_evidence",
        cancel.control["control_id"], evidence)


def _lane_evidence(point: C0B4Checkpoint,
                   lane: Mapping[str, Any], resolver: _Resolver, *,
                   persist_dedup: bool = True) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for work in lane["work"]:
        resolved = _resolved_work(resolver, work)
        disposition, _attempt_no_value, _answered = _next_work_disposition(
            point, resolved, frozenset(row["work_id"] for row in lane["work"]))
        if disposition != "complete":
            raise C0B4RuntimeError("lane aggregation preceded complete evidence")
        evidence[work["work_id"]] = _work_evidence(
            point, resolved, persist_dedup=persist_dedup)
    return evidence


def _activate_lane(point: C0B4Checkpoint, master: Mapping[str, Any],
                   lane: Mapping[str, Any], prerequisite_sha256: str) -> str:
    later = list(LANE_ORDER[LANE_ORDER.index(lane["lane_id"]) + 1:])
    inactive = sorted(
        row["work_id"] for envelope in master["lane_plans"] + [
            master["acceptance_template"]]
        if envelope["payload"]["lane_id"] in later
        for row in envelope["payload"]["work"])
    value = validate_c0b4({
        "version": "c0b4-plan-activation-v1", **_identity(point.header()),
        "plan_sha256": lane["plan_sha256"],
        "prerequisite_sha256": prerequisite_sha256,
        "activated_work_ids": sorted(row["work_id"] for row in lane["work"]),
        "inactive_work_ids": inactive,
    })
    return _store_once(point, "plan_activation", lane["lane_id"], value)


def _cursor_transition(point: C0B4Checkpoint, *, lane: Mapping[str, Any],
                       next_lane: Mapping[str, Any], aggregate_sha256: str) -> str:
    existing = _artifact(point, "cursor_transition", lane["lane_id"])
    value: dict[str, Any] = {
        "version": "c0b4-cursor-transition-v1", **_identity(point.header()),
        "from_lane_id": lane["lane_id"], "to_lane_id": next_lane["lane_id"],
        "from_aggregate_sha256": aggregate_sha256,
        "to_plan_sha256": next_lane["plan_sha256"],
        "completed_work_census_sha256": sha256_json({
            "lane_id": lane["lane_id"],
            "completed_work_ids": sorted(row["work_id"] for row in lane["work"]),
        }),
        "transitioned_at_utc": (existing["transitioned_at_utc"]
                                if existing is not None else _utc()),
    }
    value["transition_sha256"] = sha256_json(value)
    return _store_once(point, "cursor_transition", lane["lane_id"],
                       validate_c0b4(value))


def _parent_d50(root: Path, corpus: Any) -> dict[str, Any]:
    checkpoint, _snapshot = _parent_paths(root)
    fd = os.open(checkpoint, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        conn = sqlite3.connect(
            f"file:/proc/self/fd/{fd}?mode=ro&immutable=1", uri=True, timeout=1.0)
        try:
            conn.execute("PRAGMA query_only=ON")
            decision = conn.execute(
                "SELECT value_json FROM decisions WHERE decision_id='stage-d-selection'"
            ).fetchone()
            aggregate = conn.execute(
                "SELECT aggregate_json FROM phase_aggregates "
                "WHERE plan_key='D4_CONFIRMATION'").fetchone()
        finally:
            conn.close()
    finally:
        os.close(fd)
    if not decision or not aggregate:
        raise C0B4RuntimeError("parent D50 inputs are absent")
    return derive_parent_d50_component(
        json.loads(decision[0]), json.loads(aggregate[0]), corpus=corpus)


def _finish_quality(
        point: C0B4Checkpoint, lock: GlobalExecutionLock,
        *, master: Mapping[str, Any], terminal: str, reason: str,
        seed17_hash: str, seed_later_hash: str | None = None,
        c44_hash: str | None = None, acceptance_hash: str | None = None,
) -> dict[str, Any]:
    result = {
        "version": "c0b4-result-v1", **_identity(point.header()),
        "terminal": terminal, "reason": reason,
        "master_plan_sha256": sha256_json(master),
        "lane_aggregate_sha256s": {
            "f72_seed17_sha256": seed17_hash,
            "f72_seed20260804_sha256": seed_later_hash,
            "c44_scored_sha256": c44_hash,
        },
        "acceptance_aggregate_sha256": acceptance_hash,
        "selection": dict(SELECTION) if terminal == "CONFIRMED" else None,
    }
    result = validate_c0b4(result)
    result_hash = sha256_json(result)
    completion = {
        "version": "c0b4-completion-v1", **_identity(point.header()),
        "outcome": terminal, "artifact_sha256": result_hash,
        "facts": ({"confirmed": True} if terminal == "CONFIRMED" else
                  {"deterministic_stop": True, "reason": reason}),
    }
    completion = validate_c0b4(completion)
    artifact_hash, completion_hash = point.finalize(
        terminal, result, completion=completion)
    ensure_backup_receipt(
        point, lock, terminal_artifact_sha256=artifact_hash,
        completion_sha256=completion_hash)
    return _public_result(point)


def _finish_failure(
        point: C0B4Checkpoint, lock: GlobalExecutionLock, *, terminal: str,
        lane_id: str | None = None, plan_sha256: str | None = None,
        attempt_id: str | None = None, control_id: str | None = None,
) -> dict[str, Any]:
    reasons = {
        "FAILED_SAFETY": "safety_envelope_failure",
        "BLOCKED_PROVENANCE": "provenance_identity_failure",
        "BLOCKED_BUDGET": "call_allowance_exhausted",
        "BLOCKED_FILESYSTEM": "filesystem_capability_or_integrity_failure",
        "ABANDONED": "operator_abandoned",
    }
    if attempt_id is not None and control_id is None:
        attempts = point.list_attempts(attempt_id)
        if len(attempts) == 1:
            owner = attempts[0]["owner_id"]
            master = point.read_artifact("master_plan", "master")
            controls = {row["control_id"]
                        for row in master["control_plan"].values()}
            if owner in controls:
                control_id = owner
    evidence: dict[str, Any] = {
        "version": "c0b4-failure-evidence-v1", **_identity(point.header()),
        "terminal": terminal, "reason": reasons[terminal],
        "lane_id": lane_id, "plan_sha256": plan_sha256,
        "attempt_id": attempt_id, "control_id": control_id,
        "charged_call_total": len(point.list_attempts()),
    }
    evidence["evidence_sha256"] = sha256_json(evidence)
    evidence = validate_c0b4(evidence)
    evidence_hash = _store_once(
        point, "failure_evidence", "terminal", evidence)
    failure = validate_c0b4({
        "version": "c0b4-failure-v1", **_identity(point.header()),
        "terminal": terminal, "reason": reasons[terminal],
        "evidence_sha256": evidence_hash,
        "charged_call_total": len(point.list_attempts()),
    })
    artifact_hash, _none = point.finalize(terminal, failure)
    ensure_backup_receipt(
        point, lock, terminal_artifact_sha256=artifact_hash,
        completion_sha256=None)
    return _public_result(point)


def _ensure_terminal_backup(point: C0B4Checkpoint,
                            lock: GlobalExecutionLock) -> dict[str, Any]:
    quality = point.state() in {"CONFIRMED", "INCONCLUSIVE"}
    terminal = _artifact(point, "result" if quality else "failure", "terminal")
    if terminal is None:
        raise C0B4RuntimeError("terminal state lacks its owner artifact")
    completion = _artifact(point, "completion", "terminal") if quality else None
    ensure_backup_receipt(
        point, lock, terminal_artifact_sha256=sha256_json(terminal),
        completion_sha256=sha256_json(completion) if completion else None)
    return _public_result(point)


def _pause(point: C0B4Checkpoint, state: str) -> dict[str, Any]:
    point.transition(state)
    return _public_result(point)


def run_confirmation(
        run_id: str, *, resume: bool = False, repo_root: Path = REPO_ROOT,
        benchmark_root: Path | None = None,
        transport_factory: Callable[[Callable[..., Any], Mapping[str, Any]],
                                    Callable[..., Any]] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        soft_wall_seconds: float = 4 * 60 * 60,
        cancellation: threading.Event | None = None,
        parent_d50_loader: Callable[[Path, Any], Mapping[str, Any]] | None = None,
        stop_at_stage_boundary: bool = True,
) -> dict[str, Any]:
    """Run or resume the exact serial C0B-4 public confirmation schedule."""
    if type(resume) is not bool or soft_wall_seconds <= 0:
        raise ValueError("resume and soft-wall inputs are invalid")
    root = Path(benchmark_root) if benchmark_root is not None else report.bench_root()
    cancel = cancellation or threading.Event()
    with GlobalExecutionLock(root) as lock:
        with C0B4Checkpoint.open(_checkpoint_path(run_id, root), root) as point:
            if point.state() in TERMINAL_STATES:
                return _ensure_terminal_backup(point, lock)
            validate_run_lineage(
                point.conn, point.header(), require_event_completeness=False)
            old_state = point.state()
            if old_state != "PREPARED" and not resume:
                raise C0B4RuntimeError("non-prepared confirmation requires --resume")
            # All immutable evidence is rechecked before recovery, state mutation,
            # invocation charge, or transport construction.
            try:
                revalidate_source_pins(point.header(), repo_root=repo_root)
                point._assert_parent_unchanged()
                corpus = _corpus(point.header())
                master = point.read_artifact("master_plan", "master")
                key = point.read_nonce_key()
                master = validate_master_plan(
                    master, corpus=corpus, run_nonce_key=key)
            except C0B4FilesystemError:
                return _finish_failure(point, lock, terminal="BLOCKED_FILESYSTEM")
            except Exception:
                return _finish_failure(point, lock, terminal="BLOCKED_PROVENANCE")
            resolver = _Resolver(master, corpus, key)
            point.recover_dispatching()
            _reconcile_runtime_events(point, resolver)
            validate_run_lineage(point.conn, point.header())
            if cancel.is_set():
                if point.state() == "PREPARED":
                    point.transition("RUNNING")
                return _pause(point, "CANCELLED_PENDING_RESUME")
            seed17 = point.read_artifact("lane_aggregate", "F72_17")
            if seed17 is not None and not seed17["passed"]:
                reason = ("seed17_control_gate_failed"
                          if seed17["cancellation_health_evidence_sha256"]
                          is not None else "seed17_no_qualifier")
                return _finish_quality(
                    point, lock, master=master, terminal="INCONCLUSIVE",
                    reason=reason, seed17_hash=sha256_json(seed17))
            seed_later = point.read_artifact(
                "lane_aggregate", "F72_20260804")
            if seed_later is not None and not seed_later["passed"]:
                return _finish_quality(
                    point, lock, master=master, terminal="INCONCLUSIVE",
                    reason="seed20260804_no_qualifier",
                    seed17_hash=sha256_json(seed17),
                    seed_later_hash=sha256_json(seed_later))
            block_lane = lambda lane: _finish_failure(
                point, lock, terminal="BLOCKED_PROVENANCE",
                lane_id=lane["lane_id"], plan_sha256=lane["plan_sha256"])
            point.transition("RUNNING")
            try:
                ordinal = point.claim_invocation()
            except C0B4BudgetError:
                return _finish_failure(point, lock, terminal="BLOCKED_BUDGET")
            prior_hash = sha256_json(master)
            for lane_index, lane_id in enumerate(LANE_ORDER):
                lane = point.read_artifact("lane_plan", lane_id)
                kind = "c44_aggregate" if lane_id == "C44_1" else "lane_aggregate"
                if point.read_artifact(kind, lane_id) is None:
                    if lane_index:
                        previous = point.read_artifact(
                            "lane_plan", LANE_ORDER[lane_index - 1])
                        try:
                            _cursor_transition(
                                point, lane=previous, next_lane=lane,
                                aggregate_sha256=prior_hash)
                        except Exception:
                            return block_lane(lane)
                    try:
                        _activate_lane(point, master, lane, prior_hash)
                    except Exception:
                        return block_lane(lane)
                    break
                prior_hash = sha256_json(point.read_artifact(kind, lane_id))
            transport = (transport_factory(resolver, point.header())
                         if transport_factory is not None else
                         BoundedOllamaTransport(
                             resolver, endpoint=point.header()["ollama_endpoint"]))
            started = monotonic()
            within_wall = lambda: monotonic() - started < soft_wall_seconds
            try:
                preflight, attempt_id = _run_preflight(
                    point, resolver, transport, ordinal,
                    before_call=within_wall)
            except C0B4BudgetError:
                return _finish_failure(point, lock, terminal="BLOCKED_BUDGET")
            if preflight == "RETRYABLE_TRANSPORT":
                return _pause(point, "PAUSED_PREFLIGHT")
            if preflight == "PAUSED_SOFT_WALL":
                return _pause(point, preflight)
            if preflight in {"FAILED_SAFETY", "BLOCKED_PROVENANCE"}:
                terminal = preflight
                return _finish_failure(
                    point, lock, terminal=terminal, attempt_id=attempt_id)
            controls = resolver.prepared.resolve_controls()
            lane_hashes: dict[str, str] = {}
            prerequisite_hash = sha256_json(master)
            for lane_index, lane_id in enumerate(LANE_ORDER):
                lane = point.read_artifact("lane_plan", lane_id)
                kind = "c44_aggregate" if lane_id == "C44_1" else "lane_aggregate"
                aggregate = _artifact(point, kind, lane_id)
                newly_built = False
                if aggregate is None:
                    try:
                        outcome, attempt_id = _run_lane_work(
                            point, resolver, transport, ordinal=ordinal, lane=lane,
                            master=master, corpus=corpus, key=key, controls=controls,
                            clock_started=started, monotonic=monotonic,
                            soft_wall_seconds=soft_wall_seconds, cancellation=cancel)
                    except C0B4BudgetError:
                        return _finish_failure(
                            point, lock, terminal="BLOCKED_BUDGET",
                            lane_id=lane_id, plan_sha256=lane["plan_sha256"])
                    if outcome.startswith("PAUSED_") or outcome == "CANCELLED_PENDING_RESUME":
                        return _pause(point, outcome)
                    if outcome == "BLOCKED_BUDGET":
                        return _finish_failure(
                            point, lock, terminal=outcome, lane_id=lane_id,
                            plan_sha256=lane["plan_sha256"])
                    if outcome in {"FAILED_SAFETY", "BLOCKED_PROVENANCE"}:
                        return _finish_failure(
                            point, lock, terminal=outcome, lane_id=lane_id,
                            plan_sha256=lane["plan_sha256"], attempt_id=attempt_id)
                    try:
                        evidence = _lane_evidence(point, lane, resolver)
                    except Exception:
                        return block_lane(lane)
                    if lane_id == "F72_17":
                        context = point.read_artifact(
                            "context_evidence", controls["context"].control["control_id"])
                        context_hash = sha256_json(context)
                        try:
                            preliminary = build_precontrol_lane_aggregate(
                                lane, evidence, corpus=corpus,
                                context_evidence_sha256=context_hash)
                        except Exception:
                            return block_lane(lane)
                        if preliminary is not None:
                            aggregate = preliminary
                            aggregate_hash = _store_once(
                                point, kind, lane_id, aggregate)
                            return _finish_quality(
                                point, lock, master=master, terminal="INCONCLUSIVE",
                                reason="seed17_no_qualifier",
                                seed17_hash=aggregate_hash)
                        try:
                            control_state, control_owner = _run_cancel_health(
                                point, resolver, transport, ordinal=ordinal,
                                controls=controls, monotonic=monotonic,
                                sleep=sleep, now=now, before_call=within_wall)
                        except Exception:
                            return block_lane(lane)
                        if control_state.startswith("PAUSED_"):
                            return _pause(point, control_state)
                        if control_state == "BLOCKED_BUDGET":
                            return _finish_failure(
                                point, lock, terminal=control_state,
                                lane_id=lane_id, plan_sha256=lane["plan_sha256"])
                        if control_state in {"FAILED_SAFETY", "BLOCKED_PROVENANCE"}:
                            return _finish_failure(
                                point, lock, terminal=control_state,
                                lane_id=lane_id, plan_sha256=lane["plan_sha256"],
                                attempt_id=control_owner)
                        control = point.read_artifact(
                            "cancellation_health_evidence",
                            controls["cancellation"].control["control_id"])
                        try:
                            aggregate = build_lane_aggregate(
                                lane, evidence, corpus=corpus,
                                context_evidence_sha256=context_hash,
                                cancellation_health_evidence_sha256=
                                    sha256_json(control),
                                controls_passed=control["passed"])
                        except Exception:
                            return block_lane(lane)
                    else:
                        try:
                            aggregate = build_lane_aggregate(
                                lane, evidence, corpus=corpus)
                        except Exception:
                            return block_lane(lane)
                    try:
                        _store_once(point, kind, lane_id, aggregate)
                    except Exception:
                        return block_lane(lane)
                    newly_built = True
                else:
                    try:
                        evidence = _lane_evidence(
                            point, lane, resolver, persist_dedup=False)
                        if lane_id == "F72_17":
                            context = point.read_artifact(
                                "context_evidence",
                                controls["context"].control["control_id"])
                            context_hash = sha256_json(context)
                            control = _artifact(
                                point, "cancellation_health_evidence",
                                controls["cancellation"].control["control_id"])
                            if control is None:
                                expected = build_precontrol_lane_aggregate(
                                    lane, evidence, corpus=corpus,
                                    context_evidence_sha256=context_hash)
                                if expected is None:
                                    raise C0B4RuntimeError(
                                        "passing lane lacks control evidence")
                            else:
                                expected = build_lane_aggregate(
                                    lane, evidence, corpus=corpus,
                                    context_evidence_sha256=context_hash,
                                    cancellation_health_evidence_sha256=
                                        sha256_json(control),
                                    controls_passed=control["passed"])
                        else:
                            expected = build_lane_aggregate(
                                lane, evidence, corpus=corpus)
                        if canonical_json(expected) != canonical_json(aggregate):
                            raise C0B4RuntimeError(
                                "stored aggregate does not rederive")
                    except Exception:
                        return _finish_failure(
                            point, lock, terminal="BLOCKED_PROVENANCE",
                            lane_id=lane_id, plan_sha256=lane["plan_sha256"])
                aggregate_hash = sha256_json(aggregate)
                lane_hashes[lane_id] = aggregate_hash
                if lane_id == "F72_17" and not aggregate["passed"]:
                    reason = ("seed17_control_gate_failed"
                              if aggregate[
                                  "cancellation_health_evidence_sha256"] is not None
                              else "seed17_no_qualifier")
                    return _finish_quality(
                        point, lock, master=master, terminal="INCONCLUSIVE",
                        reason=reason,
                        seed17_hash=aggregate_hash)
                if lane_id == "F72_20260804" and not aggregate["passed"]:
                    return _finish_quality(
                        point, lock, master=master, terminal="INCONCLUSIVE",
                        reason="seed20260804_no_qualifier",
                        seed17_hash=lane_hashes["F72_17"],
                        seed_later_hash=aggregate_hash)
                prerequisite_hash = aggregate_hash
                if newly_built and stop_at_stage_boundary and lane_id != "C44_1":
                    return _pause(point, "PAUSED_STAGE_BOUNDARY")
                if lane_index + 1 < len(LANE_ORDER):
                    next_lane = point.read_artifact(
                        "lane_plan", LANE_ORDER[lane_index + 1])
                    try:
                        _cursor_transition(
                            point, lane=lane, next_lane=next_lane,
                            aggregate_sha256=aggregate_hash)
                        _activate_lane(point, master, next_lane, aggregate_hash)
                    except Exception:
                        return block_lane(next_lane)
            c44 = point.read_artifact("c44_aggregate", "C44_1")
            seed17 = point.read_artifact("lane_aggregate", "F72_17")
            loader = parent_d50_loader or _parent_d50
            try:
                d50 = dict(loader(root, corpus))
                acceptance = build_acceptance_aggregate(
                    c44, d50, seed17, corpus=corpus,
                    acceptance_plan_sha256=point.read_artifact(
                        "lane_plan", "C44_1")["plan_sha256"],
                    cancellation_health_passed=True,
                    provenance_passed=True, safety_passed=True)
                acceptance_hash = _store_once(
                    point, "acceptance_aggregate", "complete", acceptance)
            except Exception:
                return block_lane(point.read_artifact("lane_plan", "C44_1"))
            terminal = "CONFIRMED" if acceptance["passed"] else "INCONCLUSIVE"
            reason = ("complete_public_acceptance_passed" if acceptance["passed"]
                      else "complete_corpus_acceptance_failed")
            return _finish_quality(
                point, lock, master=master, terminal=terminal, reason=reason,
                seed17_hash=lane_hashes["F72_17"],
                seed_later_hash=lane_hashes["F72_20260804"],
                c44_hash=lane_hashes["C44_1"], acceptance_hash=acceptance_hash)


def abandon_confirmation_run(
        run_id: str, *, repo_root: Path = REPO_ROOT,
        benchmark_root: Path | None = None,
) -> dict[str, Any]:
    """Irreversibly abandon a nonterminal run with exact evidence and backup."""
    root = Path(benchmark_root) if benchmark_root is not None else report.bench_root()
    with GlobalExecutionLock(root) as lock:
        with C0B4Checkpoint.open(_checkpoint_path(run_id, root), root) as point:
            if point.state() in TERMINAL_STATES:
                return _ensure_terminal_backup(point, lock)
            validate_run_lineage(
                point.conn, point.header(), require_event_completeness=False)
            try:
                revalidate_source_pins(point.header(), repo_root=repo_root)
                point._assert_parent_unchanged()
                corpus = _corpus(point.header())
                master = validate_master_plan(
                    point.read_artifact("master_plan", "master"), corpus=corpus,
                    run_nonce_key=point.read_nonce_key())
            except C0B4FilesystemError:
                return _finish_failure(
                    point, lock, terminal="BLOCKED_FILESYSTEM")
            except Exception:
                return _finish_failure(point, lock, terminal="BLOCKED_PROVENANCE")
            resolver = _Resolver(master, corpus, point.read_nonce_key())
            point.recover_dispatching()
            _reconcile_runtime_events(point, resolver)
            validate_run_lineage(point.conn, point.header())
            return _finish_failure(point, lock, terminal="ABANDONED")
