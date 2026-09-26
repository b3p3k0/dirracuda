"""Guarded creation, execution, and verification for C0B-6.

The runtime reuses frozen corpus, transport, filesystem, and scored-answer primitives;
every policy-sensitive plan, event, aggregate, checkpoint, and receipt stays in the
C0B-6 family.

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
from pathlib import Path
from typing import Any, Callable, Mapping

from . import chunker, goldset, metrics, report
from .c0b2_executor import (
    SERVER_CONTROL_MODEL,
    ControlRequest,
    FakeResponse,
    ProvenanceFailure,
    RetryableTransport,
    SafetyLimit,
    WorkRequest,
)
from .c0b2_fsprobe import (
    FilesystemProbe,
    GlobalExecutionLock,
    MountFingerprint,
    probe_filesystem,
)
from .c0b2_leakscan import (
    FROZEN_C0B6_PUBLIC_PATHS,
    WorktreeSeal,
    capture_worktree_seal,
    read_regular_file,
)
from .c0b2_plan import build_master_manifest, master_manifest_payload, stable_hash
from .c0b2_schema import CATEGORIES, schema_hash
from .c0b2_stage_f import _decoded_contains
from .c0b2_stage_f_plan import load_public_corpus
from .c0b2_transport import BoundedOllamaTransport, RequestSpec, request_spec_hash
from .c0b4_answer import assess_answer, prompt_template_hash
from .c0b4_filesystem import revalidate_frozen_filesystem
from .c0b6_backup import ensure_backup_receipt, verify_backup_readonly
from .c0b6_checkpoint import (
    CUMULATIVE_CAP,
    HEADER_VERSION,
    INVOCATION_CAPS,
    LEDGER_LIMITS,
    PROTOCOL_ID,
    RUN_ID_RE,
    SCHEMA_VERSION,
    TERMINAL_STATES,
    C0B6BudgetError,
    C0B6Checkpoint,
    canonical_json,
    sha256_json,
    status_readonly,
    validate_run_lineage,
)
from .c0b6_executor import (
    ScoredWork,
    execute_scored,
    persist_scored_finish,
    reconcile_runtime_events,
)
from .c0b6_lineage import (
    FROZEN_PARENT_BINDING,
    ParentPaths,
    assert_parents_unchanged,
    verify_parents_readonly,
)
from .c0b6_plan import (
    LANE_ORDER,
    SELECTION,
    build_master_plan,
    build_request_resolver,
    lane_from_master,
    validate_master_plan,
)
from .c0b6_policy import (
    POLICY_ID,
    POLICY_SHA256,
    header_identity,
    protocol_sha256,
)
from .c0b6_schema import (
    C0B6ChunkRow,
    CancellationHealthEvidence,
    ContextEvidence,
    DedupEvidence,
    validate_artifact,
)
from .c0b6_scoring import (
    build_acceptance_aggregate,
    build_lane_aggregate,
    build_precontrol_lane_aggregate,
    build_public_summary,
    derive_parent_d50_component,
)
from .c0b6_replay import (
    replay_c0b6_connection,
    verify_c0b6_terminal_readonly,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
OLLAMA_VERSION = "0.32.5"
JOURNAL_MODE = "DELETE"


class C0B6RuntimeError(RuntimeError):
    """A runtime gate failed without exposing source or response content."""


class C0B6FilesystemError(C0B6RuntimeError):
    """The frozen benchmark mount or journaling capability changed."""


def _git(repo_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "--no-replace-objects", *args], cwd=repo_root, check=True,
        capture_output=True, text=True, shell=False).stdout.strip()


def _file_sha256(path: Path, *, root: Path) -> str:
    _verified, body = read_regular_file(path, trusted_root=root)
    return hashlib.sha256(body).hexdigest()


def _task_tree_sha256(repo_root: Path) -> str:
    rows = {relative: _file_sha256(repo_root / relative, root=repo_root)
            for relative in sorted(FROZEN_C0B6_PUBLIC_PATHS)}
    return stable_hash(rows)


def _require_clean_source(seal: WorktreeSeal) -> None:
    if any(row.path in FROZEN_C0B6_PUBLIC_PATHS for row in seal.entries):
        raise C0B6RuntimeError("commit the frozen C0B-6 implementation before create")


def _generation_options_sha256() -> str:
    rows = []
    for lane_id, seed in zip(LANE_ORDER, (20260811, 20260818, 1), strict=True):
        rows.append({
            "lane_id": lane_id,
            "model": SELECTION["model"],
            "seed": seed,
            "keep_alive": "15m",
            "think": False,
            "options": {
                "min_p": 0.0,
                "num_ctx": 8192,
                "num_predict": 1024,
                "repeat_last_n": 0,
                "repeat_penalty": 1.0,
                "seed": seed,
                "temperature": 0.0,
                "top_k": 1,
                "top_p": 1.0,
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
                        "c0b4_answer.py", "c0b6_scoring.py")},
    })
    return {
        "protocol_sha256": protocol_sha256(repo_root),
        "git_head": _git(repo_root, "rev-parse", "HEAD"),
        "declared_dirty_state_sha256": seal.digest,
        "task_tree_sha256": _task_tree_sha256(repo_root),
        "fixture_sha256": _file_sha256(goldset.MANIFEST, root=repo_root),
        "schema_sha256": stable_hash({
            "worksheet_v2": schema_hash("v2"),
            "c0b6": _file_sha256(
                repo_root / "scripts/analyst_benchmark/c0b6_schema.py",
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


def _find_snapshot(run_dir: Path, expected_sha256: str) -> Path:
    backup = run_dir / "backups"
    matches = []
    for child in backup.iterdir():
        try:
            digest = _file_sha256(child, root=run_dir)
        except Exception:
            continue
        if digest == expected_sha256:
            matches.append(child)
    if len(matches) != 1:
        raise C0B6RuntimeError("frozen parent snapshot is absent or ambiguous")
    return matches[0]


def _parent_paths(root: Path) -> ParentPaths:
    c3_run = root / "runs" / FROZEN_PARENT_BINDING["execution_parent"]["run_id"]
    c4_run = root / "runs" / FROZEN_PARENT_BINDING["observed_c0b4"]["run_id"]
    return ParentPaths(
        c0b3_checkpoint=c3_run / "checkpoint.sqlite3",
        c0b3_snapshot=_find_snapshot(
            c3_run, FROZEN_PARENT_BINDING["execution_parent"]["backup_snapshot_sha256"]),
        c0b4_checkpoint=c4_run / "checkpoint.sqlite3",
        c0b4_snapshot=_find_snapshot(
            c4_run, FROZEN_PARENT_BINDING["observed_c0b4"]["backup_snapshot_sha256"]),
    )


def _header(*, run_id: str, source: Mapping[str, Any],
            filesystem: FilesystemProbe, manifest_sha256: str) -> dict[str, Any]:
    if filesystem.selected_mode != JOURNAL_MODE:
        raise C0B6RuntimeError("benchmark filesystem did not pass DELETE+FULL")
    value = {
        "version": HEADER_VERSION,
        "run_type": "public_confirmation",
        "benchmark_protocol_id": PROTOCOL_ID,
        **header_identity(str(source["protocol_sha256"])),
        "parent_binding": FROZEN_PARENT_BINDING,
        **dict(source),
        "filesystem_selected_mode": JOURNAL_MODE,
        "master_manifest_sha256": manifest_sha256,
        "filesystem_capability_sha256": filesystem.capability_sha256,
        "mount": asdict(filesystem.fingerprint),
        "schema_version": SCHEMA_VERSION,
        "journal_mode": JOURNAL_MODE,
        "cumulative_cap": CUMULATIVE_CAP,
        "run_id": run_id,
        "limits": LEDGER_LIMITS,
        "invocation_caps": INVOCATION_CAPS,
    }
    return validate_artifact(value)


def _new_run_id() -> str:
    return f"{time.strftime('c0b6-%Y%m%d-%H%M%S', time.gmtime())}-{secrets.token_hex(12)}"


def _assert_child_allowance(root: Path) -> None:
    runs = Path(root) / "runs"
    if not runs.exists():
        return
    names = [child.name for child in runs.iterdir()]
    if any(RUN_ID_RE.fullmatch(name) for name in names):
        raise C0B6RuntimeError("the one-child C0B-6 allowance is already owned")
    if any(name.startswith(".c0b6-initializing-") for name in names):
        raise C0B6RuntimeError("staged C0B-6 evidence requires review")


def _corpus(header: Mapping[str, Any] | None = None) -> Any:
    source = goldset.load(verify=True)
    manifest = build_master_manifest(source)
    if header is not None and manifest.sha256 != header["master_manifest_sha256"]:
        raise C0B6RuntimeError("public corpus manifest changed")
    return load_public_corpus(
        master_manifest_payload(manifest),
        master_manifest_sha256=manifest.sha256, source=source)


def create_confirmation_run(
        *, repo_root: Path = REPO_ROOT, benchmark_root: Path | None = None,
        run_id: str | None = None,
        filesystem_probe: Callable[..., FilesystemProbe] = probe_filesystem,
        nonce_key: bytes | None = None,
) -> str:
    """Create the complete C0B-6 child without contacting Ollama."""
    root = Path(benchmark_root) if benchmark_root is not None else report.bench_root()
    identity = run_id or _new_run_id()
    if not RUN_ID_RE.fullmatch(identity):
        raise ValueError("invalid C0B-6 run id")
    paths = _parent_paths(root)
    parents = verify_parents_readonly(paths)
    seal = capture_worktree_seal(repo_root)
    _require_clean_source(seal)
    corpus_source = goldset.load(verify=True)
    manifest = build_master_manifest(corpus_source)
    corpus = load_public_corpus(
        master_manifest_payload(manifest),
        master_manifest_sha256=manifest.sha256, source=corpus_source)
    key = nonce_key if nonce_key is not None else secrets.token_bytes(32)
    if type(key) is not bytes or len(key) != 32:
        raise ValueError("C0B-6 nonce key must contain exactly 32 bytes")
    source = _source_pins(repo_root, seal)
    master = build_master_plan(
        corpus=corpus, run_nonce_key=key,
        protocol_sha256=source["protocol_sha256"])
    filesystem = filesystem_probe(root, modes=(JOURNAL_MODE,))
    header = _header(
        run_id=identity, source=source, filesystem=filesystem,
        manifest_sha256=manifest.sha256)

    def initialize(point: C0B6Checkpoint) -> None:
        point.store_artifact("master_plan", "master", master)
        for envelope in [*master["lane_plans"], master["acceptance_template"]]:
            lane = envelope["payload"]
            point.store_artifact("lane_plan", lane["lane_id"], lane)
        point.set_nonce_key(key)

    with GlobalExecutionLock(root):
        _assert_child_allowance(root)
        point = C0B6Checkpoint.create(
            root, identity, header=header,
            parent_paths=(paths.c0b3_checkpoint, paths.c0b3_snapshot,
                          paths.c0b4_checkpoint, paths.c0b4_snapshot),
            initializer=initialize)
        point.close()
    assert_parents_unchanged(parents)
    return identity


def _checkpoint_path(run_id: str, root: Path | None = None) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid C0B-6 run id")
    base = Path(root) if root is not None else report.bench_root()
    return base / "runs" / run_id / "checkpoint.sqlite3"


def confirmation_status(run_id: str, *, benchmark_root: Path | None = None
                        ) -> dict[str, Any]:
    value = status_readonly(_checkpoint_path(run_id, benchmark_root))
    return {**value, "benchmark_protocol_id": PROTOCOL_ID,
            "policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256}


def confirmation_verify(
        run_id: str, *, benchmark_root: Path | None = None,
        repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Verify current bytes and independently replay terminal public evidence."""
    root = Path(benchmark_root) if benchmark_root is not None else report.bench_root()
    path = _checkpoint_path(run_id, root)
    status = status_readonly(path)
    errors = list(status.get("errors", ()))
    summary = None
    backup = {"required": status.get("state") in TERMINAL_STATES,
              "receipt_present": False, "anchor_sha256": None,
              "snapshot_sha256": None}
    try:
        if errors:
            raise C0B6RuntimeError("checkpoint structural verification failed")
        with C0B6Checkpoint.open(path) as point:
            revalidate_source_pins(point.header(), repo_root=repo_root)
            parents = verify_parents_readonly(ParentPaths(*point.parent_paths()))
            if point.state() not in TERMINAL_STATES:
                corpus = _corpus(point.header())
                master = validate_master_plan(
                    point.read_artifact("master_plan", "master"), corpus=corpus,
                    run_nonce_key=point.read_nonce_key())
                _validate_execution_history(
                    point, _Resolver(master, corpus, point.read_nonce_key()))
            else:
                checked = verify_backup_readonly(
                    path, semantic_verifier=lambda _conn: None)
                if not checked.get("ok"):
                    raise C0B6RuntimeError("terminal backup verification failed")
                facts = verify_c0b6_terminal_readonly(
                    path, Path(checked["snapshot"]), trusted_root=root,
                    parent_facts=parents)
                summary = facts.public_summary
                backup.update(
                    receipt_present=True,
                    anchor_sha256=facts.backup_anchor_sha256,
                    snapshot_sha256=facts.backup_snapshot_sha256)
    except Exception as exc:
        errors.append(type(exc).__name__)
    return {
        **status, "ok": not errors, "errors": errors,
        "benchmark_protocol_id": PROTOCOL_ID, "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256, "backup": backup,
        "public_summary": summary,
    }


def render_public(value: Mapping[str, Any]) -> str:
    """Return canonical, content-free CLI output."""
    return canonical_json(dict(value))


def revalidate_source_pins(header: Mapping[str, Any], *,
                           repo_root: Path = REPO_ROOT) -> None:
    seal = capture_worktree_seal(repo_root)
    _require_clean_source(seal)
    current = _source_pins(repo_root, seal)
    if any(header.get(key) != item for key, item in current.items()):
        raise C0B6RuntimeError("immutable C0B-6 source identity drift")
    try:
        revalidate_frozen_filesystem(
            MountFingerprint(**header["mount"]),
            Path(header["mount"]["canonical_path"]), header["journal_mode"],
            header["filesystem_capability_sha256"])
    except Exception as exc:
        raise C0B6FilesystemError("filesystem capability changed") from exc


def _identity(header: Mapping[str, Any]) -> dict[str, str]:
    return {key: str(header[key]) for key in (
        "policy_id", "policy_sha256", "protocol_sha256")}


def _utc(now: Callable[[], float] = time.time) -> str:
    return datetime.fromtimestamp(now(), timezone.utc).isoformat(
        timespec="microseconds").replace("+00:00", "Z")


def _artifact(point: C0B6Checkpoint, kind: str,
              owner: str) -> dict[str, Any] | None:
    return point.read_artifact(kind, owner)


def _public_result(point: C0B6Checkpoint) -> dict[str, Any]:
    return {
        "run_id": point.header()["run_id"],
        "state": point.state(),
        "charged_calls": len(point.list_attempts()),
        "benchmark_protocol_id": PROTOCOL_ID,
        "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256,
    }


class _Resolver:
    def __init__(self, master: Mapping[str, Any], corpus: Any, key: bytes):
        self.prepared = build_request_resolver(
            master, corpus=corpus, run_nonce_key=key)
        self.work_ids = frozenset(self.prepared.work)
        self.controls_resolved = self.prepared.resolve_controls()
        self.control_ids = frozenset(
            row.control["control_id"] for row in self.controls_resolved.values())
        self.controls: dict[str, RequestSpec] = {
            row.control["control_id"]: row.request_spec
            for row in self.controls_resolved.values()}

    def __call__(self, request: WorkRequest | ControlRequest) -> RequestSpec:
        if isinstance(request, WorkRequest):
            return self.prepared.request_spec_for_work(request.work_id)
        try:
            return self.controls[request.control_id]
        except KeyError:
            raise C0B6RuntimeError("request names an unregistered control") from None


def _control_request(control_id: str, spec: RequestSpec,
                     attempt_no: int) -> ControlRequest:
    model = (SERVER_CONTROL_MODEL if spec.kind in {"version", "tags"}
             else str(spec.expected_model))
    return ControlRequest(
        "F", control_id, model, request_spec_hash(spec), attempt_no,
        "preflight_control")


def _attempt_no(point: C0B6Checkpoint, owner_id: str) -> int:
    return 1 + sum(row["owner_id"] == owner_id for row in point.list_attempts())


def _validate_scored_response(response: FakeResponse, assessment: Any) -> None:
    if (not isinstance(response, FakeResponse) or type(response.content) is not str
            or not isinstance(response.metadata, Mapping)
            or response.outcome not in {"ACCEPTED", "SCHEMA_INVALID"}
            or response.accepted != (response.outcome == "ACCEPTED")):
        raise SafetyLimit("transport_response_contract")
    meta = response.metadata
    if (type(meta.get("done_reason")) is not str
            or not 1 <= len(meta["done_reason"]) <= 80
            or type(meta.get("prompt_eval_count")) is not int
            or meta["prompt_eval_count"] < 0
            or any(type(meta.get(key)) is not bool for key in (
                "tools_empty", "images_empty", "unknown_message_fields_empty"))):
        raise SafetyLimit("transport_evidence_metadata")
    strict, semantic = meta.get("strict_schema_invalid"), meta.get("semantic_invalid")
    if type(strict) is not bool or type(semantic) is not bool or strict and semantic:
        raise SafetyLimit("transport_assessment_contract")
    expected = ("ACCEPTED", False, False) if assessment.final_outcome == "RAW_VALID" \
        else ("SCHEMA_INVALID", False, True) if \
        assessment.final_outcome == "NORMALIZED_DUPLICATE" else None
    agrees = ((response.outcome, strict, semantic) == expected if expected else
              response.outcome == "SCHEMA_INVALID" and (strict or semantic))
    if not agrees:
        raise SafetyLimit("transport_assessment_mismatch")


def _call_control(
        point: C0B6Checkpoint, resolver: _Resolver, transport: Callable[..., Any],
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
    prior = [row for row in point.list_attempts() if row["owner_id"] == control_id]
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
    try:
        response = transport(request, cancel or threading.Event())
    except RetryableTransport:
        point.record_attempt(request.attempt_id, "RETRYABLE_TRANSPORT",
                             {"answered": False})
        return "RETRYABLE_TRANSPORT", None, request.attempt_id
    except SafetyLimit:
        point.record_attempt(request.attempt_id, "FAILED_SAFETY", {"answered": False})
        return "FAILED_SAFETY", None, request.attempt_id
    except ProvenanceFailure:
        point.record_attempt(request.attempt_id, "BLOCKED_PROVENANCE",
                             {"answered": False})
        return "BLOCKED_PROVENANCE", None, request.attempt_id
    if not isinstance(response, FakeResponse):
        point.record_attempt(request.attempt_id, "FAILED_SAFETY", {"answered": True})
        return "FAILED_SAFETY", None, request.attempt_id
    try:
        assessment = (assess_answer("v2", response.content, assessment_source)
                      if assessment_source is not None else None)
        if assessment is not None:
            _validate_scored_response(response, assessment)
        if response_validator is not None:
            response_validator(response)
    except Exception:
        point.record_attempt(request.attempt_id, "FAILED_SAFETY", {
            "answered": True, "response": response.content,
            "metadata": dict(response.metadata)})
        return "FAILED_SAFETY", None, request.attempt_id
    outcome = ("RAW_VALID" if assessment is None else
               assessment.final_outcome if assessment.eventual_valid else
               "SCHEMA_INVALID")
    point.record_attempt(request.attempt_id, outcome, {
        "answered": True, "response": response.content,
        "metadata": dict(response.metadata)})
    return outcome, response, request.attempt_id


def _preflight_specs(header: Mapping[str, Any]) -> tuple[tuple[str, RequestSpec], ...]:
    model, digest = SELECTION["model"], SELECTION["model_digest"]
    return (
        ("version", RequestSpec(kind="version", expected_version=header["ollama_version"])),
        ("tags", RequestSpec(kind="tags", expected_models={model: digest})),
        ("show", RequestSpec(kind="show", expected_model=model,
                             expected_digest=digest)),
    )


def _run_preflight(point: C0B6Checkpoint, resolver: _Resolver,
                   transport: Callable[..., Any], ordinal: int,
                   before_call: Callable[[], bool] | None = None,
                   ) -> tuple[str, str | None]:
    for kind, spec in _preflight_specs(point.header()):
        owner = stable_hash({"c0b6_preflight": kind, "invocation": ordinal})
        outcome, _response, attempt = _call_control(
            point, resolver, transport, ordinal=ordinal, control_id=owner,
            spec=spec, before_call=before_call)
        if outcome != "RAW_VALID":
            return outcome, attempt
    return "RAW_VALID", None


def _validate_execution_history(point: C0B6Checkpoint, resolver: _Resolver, *,
                                require_complete_events: bool = False) -> None:
    """Bind every durable attempt/event to the frozen request tree before contact."""
    attempts = point.list_attempts()
    counts: dict[str, int] = {}
    preflight: dict[str, RequestSpec] = {}
    for ordinal in {row["invocation_ordinal"] for row in attempts}:
        for name, spec in _preflight_specs(point.header()):
            preflight[stable_hash({
                "c0b6_preflight": name, "invocation": ordinal})] = spec
    attempt_by_id = {}
    for row in attempts:
        owner = row["owner_id"]
        counts[owner] = counts.get(owner, 0) + 1
        if owner in resolver.work_ids:
            work = resolver.prepared.resolve_work(owner)["work"]
            expected_request = work["request_sha256"]
            expected_attempt = WorkRequest(
                "F", owner, work["model"], expected_request,
                counts[owner]).attempt_id
        elif owner in resolver.controls:
            spec = resolver.controls[owner]
            expected_request = request_spec_hash(spec)
            expected_attempt = _control_request(
                owner, spec, counts[owner]).attempt_id
        elif owner in preflight:
            spec = preflight[owner]
            expected_request = request_spec_hash(spec)
            expected_attempt = _control_request(
                owner, spec, counts[owner]).attempt_id
        else:
            raise C0B6RuntimeError("attempt owner is outside the frozen request tree")
        if (row["request_sha256"] != expected_request
                or row["attempt_id"] != expected_attempt
                or row["attempt_id"] in attempt_by_id):
            raise C0B6RuntimeError("attempt identity differs from frozen request")
        attempt_by_id[row["attempt_id"]] = row
    existing: set[tuple[str, str]] = set()
    terminal_event = {
        "RAW_VALID": "RAW_VALID", "NORMALIZED_DUPLICATE": "NORMALIZED_DUPLICATE",
        "SCHEMA_INVALID": "INVALID", "INVALID": "INVALID",
        "RETRYABLE_TRANSPORT": "ORPHANED", "ORPHANED_UNKNOWN": "ORPHANED",
        "CANCELLED": "CANCELLED",
    }
    for event in point.list_runtime_events():
        attempt = attempt_by_id.get(event["source_attempt_id"])
        if attempt is None or attempt["owner_id"] not in resolver.work_ids:
            raise C0B6RuntimeError("runtime event names no scored attempt")
        resolved = resolver.prepared.resolve_work(attempt["owner_id"])
        work, lane = resolved["work"], resolved["lane"]["lane_id"]
        key = event["event"], event["source_attempt_id"]
        if (key in existing or event["lane_id"] != lane
                or event["request_sha256"] != work["request_sha256"]
                or event["nonce"] != work["nonce"]
                or event["event"] != "DISPATCHING"
                and terminal_event.get(attempt["state"]) != event["event"]):
            raise C0B6RuntimeError("runtime event differs from its attempt")
        existing.add(key)
    if require_complete_events:
        for attempt in attempts:
            if attempt["owner_id"] not in resolver.work_ids:
                continue
            expected = {("DISPATCHING", attempt["attempt_id"])}
            if event := terminal_event.get(attempt["state"]):
                expected.add((event, attempt["attempt_id"]))
            if not expected.issubset(existing):
                raise C0B6RuntimeError("runtime event history is incomplete")


def _assessment_rows(point: C0B6Checkpoint, work: Mapping[str, Any]
                     ) -> list[tuple[dict[str, Any], Any]]:
    rows = []
    for attempt in point.list_attempts():
        if attempt["owner_id"] != work["work_id"]:
            continue
        raw = (attempt["payload"] or {}).get("response")
        if type(raw) is str:
            rows.append((attempt, assess_answer("v2", raw, work["source"])))
    return rows


def _store_once(point: C0B6Checkpoint, kind: str, owner: str,
                value: Mapping[str, Any]) -> str:
    existing = _artifact(point, kind, owner)
    if existing is not None:
        if canonical_json(existing) != canonical_json(dict(value)):
            raise C0B6RuntimeError("durable artifact differs on resume")
        return sha256_json(existing)
    return point.store_artifact(kind, owner, value)


def _work_evidence(point: C0B6Checkpoint, work: Mapping[str, Any], *,
                   persist_dedup: bool = True) -> dict[str, Any]:
    rows = _assessment_rows(point, work)
    if not rows:
        raise C0B6RuntimeError("terminal work lacks bounded HTTP evidence")
    accepted = next((row for row in rows if row[1].eventual_valid), None)
    attempt, answer = accepted or rows[-1]
    metadata = (attempt["payload"] or {}).get("metadata", {})
    metas = [(row[0]["payload"] or {}).get("metadata", {}) for row in rows]
    retained = answer.retained_value if answer.eventual_valid else None
    findings = [] if retained is None else [
        {"category": row["category"], "quote": row["quote"]}
        for row in retained["findings"]]
    categories = [name for name in CATEGORIES
                  if any(row["category"] == name for row in findings)]
    prompts = [row.get("prompt_eval_count") for row in metas
               if type(row.get("prompt_eval_count")) is int]
    if not prompts:
        raise C0B6RuntimeError("answered work lacks prompt-evaluation evidence")
    outcome = answer.final_outcome if answer.eventual_valid else "INVALID"
    dedup_hash, dedup = None, None
    if outcome == "NORMALIZED_DUPLICATE":
        dedup = {
            "version": "c0b6-dedup-evidence-v1", **_identity(point.header()),
            "work_id": work["work_id"], "attempt_id": attempt["attempt_id"],
            "raw_response_sha256": metadata["raw_response_sha256"],
            "dedupe_key": "category+nfc_quote",
            "removed_index": answer.removed_finding_indices[0],
            "raw_counts": {**answer.raw_counts.as_dict(),
                "semantic_invalid_attempts": sum(
                    row.get("semantic_invalid") is True for row in metas)},
            "retained_counts": answer.retained_counts.as_dict(),
        }
        dedup["evidence_sha256"] = sha256_json(dedup)
        dedup = DedupEvidence.model_validate(dedup, strict=True).model_dump(mode="json")
        existing = _artifact(point, "dedup_evidence", work["work_id"])
        if existing is None and not persist_dedup:
            raise C0B6RuntimeError("normalized attempt lacks dedup artifact")
        dedup_hash = (_store_once(point, "dedup_evidence", work["work_id"], dedup)
                      if existing is None else sha256_json(existing))
        if existing is not None and canonical_json(existing) != canonical_json(dedup):
            raise C0B6RuntimeError("stored dedup evidence does not rederive")
    chunk = {
        "work_id": work["work_id"], "doc_id": work["doc_id"],
        "chunk_index": work["chunk_index"],
        "first_pass_valid": rows[0][1].raw_first_pass_valid,
        "eventual_valid": answer.eventual_valid,
        "charged_attempt_count": sum(
            row["owner_id"] == work["work_id"] for row in point.list_attempts()),
        "strict_schema_invalid_attempts": sum(
            row.get("strict_schema_invalid") is True for row in metas),
        "semantic_invalid_attempts": sum(
            row.get("semantic_invalid") is True for row in metas),
        "assessment": retained.get("assessment") if retained else None,
        "predicted_categories": categories,
        "raw_findings": answer.raw_counts.findings,
        "raw_grounded_findings": answer.raw_counts.grounded_findings,
        "retained_findings": answer.retained_counts.findings,
        "retained_grounded_findings": answer.retained_counts.grounded_findings,
        "authoritative_done_reason": metadata.get("done_reason")
            if answer.eventual_valid else None,
        "length_outcomes": sum(row.get("done_reason") == "length" for row in metas),
        "max_answered_prompt_eval_count": max(prompts),
        "headroom_passed": all(
            count + SELECTION["num_predict"] <= int(.85 * SELECTION["num_ctx"])
            for count in prompts),
        "tools_empty": all(row.get("tools_empty") is True for row in metas),
        "images_empty": all(row.get("images_empty") is True for row in metas),
        "unknown_message_fields_empty": all(
            row.get("unknown_message_fields_empty") is True for row in metas),
        "schema_escape_empty": all(row[1].schema_escape_empty for row in rows),
        "marker_in_answer": any(_decoded_contains(
            work["nonce"], str((row[0]["payload"] or {}).get("response", "")))
            for row in rows),
        "raw_first_pass_valid": rows[0][1].raw_first_pass_valid,
        "final_outcome": outcome, "redundant_rows": answer.redundant_rows,
        "removed_finding_indices": list(answer.removed_finding_indices),
        "dedup_evidence_sha256": dedup_hash,
    }
    parsed = C0B6ChunkRow.model_validate(chunk, strict=True).model_dump(mode="json")
    return {"chunk": parsed, "retained_findings": findings,
            "dedup_evidence": dedup}


def _resolved_work(resolver: _Resolver,
                   work: Mapping[str, Any]) -> dict[str, Any]:
    resolved = resolver.prepared.resolve_work(work["work_id"])
    return {**dict(work), "source": resolved["chunk_text"]}


def _first_answered_lane_work(
        point: C0B6Checkpoint, resolver: _Resolver,
        lane: Mapping[str, Any]) -> tuple[dict[str, Any], str] | None:
    """Rebuild the context trigger from ordered attempts, never its artifact."""
    planned = {row["work_id"]: row for row in lane["work"]}
    trigger = next((row for row in point.list_attempts()
                    if row["owner_id"] in planned
                    and type((row["payload"] or {}).get("response")) is str), None)
    if trigger is None:
        return None
    return (_resolved_work(resolver, planned[trigger["owner_id"]]),
            trigger["attempt_id"])


def _next_work_disposition(point: C0B6Checkpoint, work: Mapping[str, Any],
                           lane_work_ids: frozenset[str]) -> tuple[str, int, int]:
    attempts = [row for row in point.list_attempts()
                if row["owner_id"] == work["work_id"]]
    answered = [row for row in attempts
                if type((row["payload"] or {}).get("response")) is str]
    if any(row["state"] in {"RAW_VALID", "NORMALIZED_DUPLICATE"}
           for row in attempts):
        return "complete", len(attempts) + 1, len(answered)
    if answered:
        answer = assess_answer("v2", answered[-1]["payload"]["response"], work["source"])
        if len(answered) >= 2 or not answer.schema_retry_allowed:
            return "complete", len(attempts) + 1, len(answered)
    if not attempts:
        disposition = "scored"
    elif attempts[-1]["state"] in {"RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN"}:
        disposition = "transport_orphan"
    elif answered:
        if any(row["call_class"] == "schema_retry"
               and row["owner_id"] in lane_work_ids for row in point.list_attempts()):
            return "budget", len(attempts) + 1, len(answered)
        disposition = "schema_retry"
    else:
        disposition = "transport_orphan"
    return disposition, len(attempts) + 1, len(answered)


def _ensure_context(
        point: C0B6Checkpoint, resolver: _Resolver, transport: Callable[..., Any],
        *, ordinal: int, controls: Mapping[str, Any],
        trigger_work: Mapping[str, Any], trigger_attempt_id: str,
        before_call: Callable[[], bool] | None = None,
) -> tuple[str, str | None]:
    control = controls["context"]
    frozen = control.control
    existing = _artifact(point, "context_evidence", frozen["control_id"])
    built: dict[str, dict[str, Any]] = {}

    def validate_response(response: FakeResponse) -> None:
        body = json.loads(response.content)
        built["evidence"] = ContextEvidence.model_validate({
            "version": "c0b6-context-evidence-v1", **_identity(point.header()),
            "control_id": frozen["control_id"], "lane_id": "F72_20260811",
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
        }, strict=True).model_dump(mode="json")

    durable = [row for row in point.list_attempts()
               if row["owner_id"] == frozen["control_id"]
               and row["state"] == "RAW_VALID"]
    if durable:
        if len(durable) != 1:
            raise C0B6RuntimeError("context response census is not exact")
        payload = durable[0]["payload"] or {}
        validate_response(FakeResponse(
            payload.get("response"), payload.get("metadata") or {}))
    else:
        outcome, response, attempt = _call_control(
            point, resolver, transport, ordinal=ordinal,
            control_id=frozen["control_id"], spec=control.request_spec,
            response_validator=validate_response, before_call=before_call)
        if outcome != "RAW_VALID" or response is None:
            return outcome, attempt
    digest = _store_once(
        point, "context_evidence", frozen["control_id"], built["evidence"])
    if existing is not None and canonical_json(existing) != canonical_json(
            built["evidence"]):
        raise C0B6RuntimeError("context evidence does not rederive")
    return "RAW_VALID", digest


def _run_lane_work(
        point: C0B6Checkpoint, resolver: _Resolver, transport: Callable[..., Any],
        *, ordinal: int, lane: Mapping[str, Any], controls: Mapping[str, Any],
        clock_started: float, monotonic: Callable[[], float],
        soft_wall_seconds: float, cancellation: threading.Event,
) -> tuple[str, str | None]:
    lane_id = lane["lane_id"]
    lane_ids = frozenset(row["work_id"] for row in lane["work"])
    prior = [row for row in point.list_attempts() if row["owner_id"] in lane_ids]
    failure = next((row for row in prior if row["state"] in {
        "FAILED_SAFETY", "BLOCKED_PROVENANCE"}), None)
    if failure:
        return failure["state"], failure["attempt_id"]
    if lane_id == "F72_20260811":
        trigger = _first_answered_lane_work(point, resolver, lane)
        if _artifact(point, "context_evidence",
                     controls["context"].control["control_id"]) is not None \
                and trigger is None:
            raise C0B6RuntimeError("context evidence lacks an answered trigger")
        if trigger is not None:
            trigger_work, trigger_attempt_id = trigger
            state, owner = _ensure_context(
                point, resolver, transport, ordinal=ordinal, controls=controls,
                trigger_work=trigger_work,
                trigger_attempt_id=trigger_attempt_id,
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
            point, work, lane_ids)
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
            from .c0b6_executor import runtime_event
            runtime_event(point, event="DISPATCHING", lane_id=lane_id,
                          attempt_id=request.attempt_id, work=work)

        result = execute_scored(
            scored, attempt_no=attempt_no, call_class=disposition,
            answered_attempts_before=min(answered, 1), precharge=precharge,
            finish=lambda request, finish: persist_scored_finish(
                point, lane_id, work, request, finish),
            transport=transport, cancellation=cancellation)
        if result.outcome in {"FAILED_SAFETY", "BLOCKED_PROVENANCE"}:
            return result.outcome, result.attempt_id
        if result.outcome == "RETRYABLE_TRANSPORT":
            return "PAUSED_RESOURCE", result.attempt_id
        if result.outcome == "CANCELLED":
            return "CANCELLED_PENDING_RESUME", result.attempt_id
        if lane_id == "F72_20260811" and _artifact(
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
            return _run_lane_work(
                point, resolver, transport, ordinal=ordinal, lane=lane,
                controls=controls, clock_started=clock_started,
                monotonic=monotonic, soft_wall_seconds=soft_wall_seconds,
                cancellation=cancellation)
    return "RAW_VALID", None


class _FirstByteEvent(threading.Event):
    def __init__(self, monotonic: Callable[[], float]):
        super().__init__()
        self.monotonic, self.first_set = monotonic, None

    def set(self) -> None:
        if self.first_set is None:
            self.first_set = self.monotonic()
        super().set()


def _validate_health_response(response: FakeResponse) -> None:
    metadata = response.metadata
    if (type(metadata.get("done_reason")) is not str
            or type(metadata.get("prompt_eval_count")) is not int
            or any(type(metadata.get(key)) is not bool for key in (
                "tools_empty", "images_empty", "unknown_message_fields_empty"))):
        raise C0B6RuntimeError("health response metadata is incomplete")


def _derive_cancel_health(point: C0B6Checkpoint,
                          controls: Mapping[str, Any]) -> dict[str, Any]:
    cancel, health = controls["cancellation"], controls["health"]
    cancelled = [row for row in point.list_attempts()
                 if row["owner_id"] == cancel.control["control_id"]
                 and row["state"] == "CANCELLED_UNVERIFIED"]
    if len(cancelled) != 1:
        raise C0B6RuntimeError("cancellation evidence census is not exact")
    cancel_row, cancel_payload = cancelled[0], cancelled[0]["payload"] or {}
    health_attempts = [row for row in point.list_attempts()
                       if row["owner_id"] == health.control["control_id"]]
    answered = [(row, assess_answer(
        "v2", row["payload"]["response"], health.source_chunk or ""))
        for row in health_attempts
        if type((row["payload"] or {}).get("response")) is str]
    if not answered:
        raise C0B6RuntimeError("health evidence is absent")
    last, answer = answered[-1]
    meta, retained = last["payload"]["metadata"], answer.retained_value or {"findings": []}
    emitted = any(row["category"] == "pii" for row in retained["findings"])
    grounded = {row.index: row.grounded for row in answer.grounding}
    kept = ([index for index in range(len(answer.raw_value["findings"]))
             if index not in answer.removed_finding_indices]
            if answer.raw_value is not None else [])
    grounded_pii = answer.eventual_valid and any(
        row["category"] == "pii" and grounded.get(index, False)
        for index, row in zip(kept, retained["findings"], strict=True))
    elapsed = cancel_payload["cancel_elapsed_ms"]
    reasons = []
    if cancel_payload.get("first_byte_seen") is not True:
        reasons.append("cancel_not_observed")
    elif elapsed > 5000:
        reasons.append("cancel_after_5_seconds")
    if not answer.eventual_valid:
        reasons.append("health_eventual_invalid")
    elif not emitted:
        reasons.append("health_pii_missing")
    elif not grounded_pii:
        reasons.append("health_grounding_failure")
    if meta.get("done_reason") == "length":
        reasons.append("health_length_outcome")
    if not all(meta.get(key) is True for key in (
            "tools_empty", "images_empty", "unknown_message_fields_empty")) \
            or not answer.schema_escape_empty:
        reasons.append("health_channel_violation")
    prompt_count = meta.get("prompt_eval_count")
    headroom = type(prompt_count) is int and prompt_count + 1024 <= int(.85 * 8192)
    if not headroom:
        reasons.append("health_context_headroom_failure")
    started = health_attempts[0].get("created")
    value = {
        "version": "c0b6-cancellation-health-evidence-v1",
        **_identity(point.header()), "lane_id": "F72_20260811",
        "candidate_id": cancel.control["candidate_id"],
        "prompt_sha256": health.control["prompt_sha256"],
        "cancel_control_id": cancel.control["control_id"],
        "cancel_attempt_id": cancel_row["attempt_id"],
        "cancel_state": "CANCELLED_UNVERIFIED",
        "cancel_first_byte_seen": cancel_payload.get("first_byte_seen") is True,
        "cancel_elapsed_ms": elapsed,
        "health_control_id": health.control["control_id"],
        "health_work_id": health.control["health_work_id"],
        "health_attempt_ids": [row["attempt_id"] for row in health_attempts],
        "not_before_utc": cancel_payload["health_not_before_utc"],
        "started_at_utc": (_utc(lambda: float(started))
                           if type(started) in (int, float)
                           else cancel_payload["health_not_before_utc"]),
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
        "unknown_message_fields_empty": meta.get("unknown_message_fields_empty") is True,
        "schema_escape_empty": answer.schema_escape_empty,
        "passed": not reasons, "failure_reasons": reasons,
    }
    return CancellationHealthEvidence.model_validate(
        value, strict=True).model_dump(mode="json")


def _run_cancel_health(
        point: C0B6Checkpoint, resolver: _Resolver, transport: Callable[..., Any],
        *, ordinal: int, controls: Mapping[str, Any],
        monotonic: Callable[[], float], sleep: Callable[[float], None],
        now: Callable[[], float],
        before_call: Callable[[], bool] | None = None,
) -> tuple[str, str | None]:
    cancel, health = controls["cancellation"], controls["health"]
    existing = _artifact(
        point, "cancellation_health_evidence", cancel.control["control_id"])
    if existing is not None:
        if canonical_json(existing) != canonical_json(
                _derive_cancel_health(point, controls)):
            raise C0B6RuntimeError("cancellation evidence does not rederive")
        return "RAW_VALID", sha256_json(existing)
    cancel_rows = [row for row in point.list_attempts()
                   if row["owner_id"] == cancel.control["control_id"]]
    failed = next((row for row in reversed(cancel_rows) if row["state"] in {
        "FAILED_SAFETY", "BLOCKED_PROVENANCE"}), None)
    if failed:
        return failed["state"], failed["attempt_id"]
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
            point.record_cancelled_attempt(
                request.attempt_id, first_byte_seen=True,
                cancel_elapsed_ms=int((monotonic() - event.first_set) * 1000))
            cancelled = next(
                row for row in point.list_attempts()
                if row["attempt_id"] == request.attempt_id)
        except SafetyLimit:
            point.record_attempt(request.attempt_id, "FAILED_SAFETY", {"answered": False})
            return "FAILED_SAFETY", request.attempt_id
        except ProvenanceFailure:
            point.record_attempt(request.attempt_id, "BLOCKED_PROVENANCE",
                                 {"answered": False})
            return "BLOCKED_PROVENANCE", request.attempt_id
        else:
            point.record_attempt(request.attempt_id, "FAILED_SAFETY", {"answered": True})
            return "FAILED_SAFETY", request.attempt_id
    not_before = datetime.fromisoformat(
        cancelled["payload"]["health_not_before_utc"].replace("Z", "+00:00")).timestamp()
    if (remaining := max(0.0, not_before - now())):
        sleep(remaining)
    while True:
        durable = [row for row in point.list_attempts()
                   if row["owner_id"] == health.control["control_id"]]
        failed = next((row for row in reversed(durable) if row["state"] in {
            "FAILED_SAFETY", "BLOCKED_PROVENANCE"}), None)
        if failed:
            return failed["state"], failed["attempt_id"]
        answered = [(row, assess_answer(
            "v2", row["payload"]["response"], health.source_chunk or ""))
            for row in durable if type((row["payload"] or {}).get("response")) is str]
        if answered and (answered[-1][1].eventual_valid or len(answered) >= 2
                         or not answered[-1][1].schema_retry_allowed):
            break
        call_class = "schema_retry" if answered else "preflight_control"
        outcome, response, attempt = _call_control(
            point, resolver, transport, ordinal=ordinal,
            control_id=health.control["control_id"], spec=health.request_spec,
            call_class=call_class, assessment_source=health.source_chunk or "",
            response_validator=_validate_health_response, before_call=before_call)
        if outcome == "RETRYABLE_TRANSPORT":
            return "PAUSED_RESOURCE", attempt
        if outcome in {"FAILED_SAFETY", "BLOCKED_PROVENANCE", "BLOCKED_BUDGET",
                       "PAUSED_SOFT_WALL"} or response is None:
            return outcome, attempt
    evidence = _derive_cancel_health(point, controls)
    return "RAW_VALID", _store_once(
        point, "cancellation_health_evidence",
        cancel.control["control_id"], evidence)


def _verify_existing_control_evidence(
        point: C0B6Checkpoint, resolver: _Resolver,
        controls: Mapping[str, Any]) -> None:
    """Independently rebuild durable controls before any resumed HTTP contact."""
    context_id = controls["context"].control["control_id"]
    if _artifact(point, "context_evidence", context_id) is not None:
        lane = point.read_artifact("lane_plan", "F72_20260811")
        trigger = _first_answered_lane_work(point, resolver, lane)
        if trigger is None:
            raise C0B6RuntimeError("context evidence lacks an answered trigger")
        state, _owner = _ensure_context(
            point, resolver,
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                C0B6RuntimeError("control replay attempted HTTP")),
            ordinal=0, controls=controls, trigger_work=trigger[0],
            trigger_attempt_id=trigger[1])
        if state != "RAW_VALID":
            raise C0B6RuntimeError("context evidence is not reusable")
    cancel_id = controls["cancellation"].control["control_id"]
    existing = _artifact(point, "cancellation_health_evidence", cancel_id)
    cancellation_rows = [row for row in point.list_attempts()
                         if row["owner_id"] == cancel_id]
    if cancellation_rows:
        cancelled = [row for row in cancellation_rows
                     if row["state"] == "CANCELLED_UNVERIFIED"]
        if not cancelled and any(row["state"] not in {
                "RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN"}
                for row in cancellation_rows):
            raise C0B6RuntimeError("pending cancellation census is not exact")
        if cancelled:
            if len(cancelled) != 1:
                raise C0B6RuntimeError("pending cancellation census is not exact")
            payload = cancelled[0]["payload"]
            if (type(payload) is not dict
                    or set(payload) != {
                        "answered", "first_byte_seen", "cancel_elapsed_ms",
                        "health_not_before_utc"}
                    or payload["answered"] is not False
                    or type(payload["first_byte_seen"]) is not bool
                    or type(payload["cancel_elapsed_ms"]) is not int
                    or payload["cancel_elapsed_ms"] < 0
                    or type(payload["health_not_before_utc"]) is not str):
                raise C0B6RuntimeError("pending cancellation payload is not exact")
            try:
                parsed = datetime.fromisoformat(
                    payload["health_not_before_utc"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise C0B6RuntimeError(
                    "pending cancellation deadline is invalid") from exc
            if (not payload["health_not_before_utc"].endswith("Z")
                    or parsed.tzinfo is None):
                raise C0B6RuntimeError("pending cancellation deadline is invalid")
    if existing is not None and canonical_json(existing) != canonical_json(
            _derive_cancel_health(point, controls)):
        raise C0B6RuntimeError("cancellation evidence does not rederive")


def _lane_evidence(point: C0B6Checkpoint, lane: Mapping[str, Any],
                   resolver: _Resolver, *, persist_dedup: bool = True
                   ) -> dict[str, Any]:
    evidence = {}
    ids = frozenset(row["work_id"] for row in lane["work"])
    for planned in lane["work"]:
        work = _resolved_work(resolver, planned)
        if _next_work_disposition(point, work, ids)[0] != "complete":
            raise C0B6RuntimeError("lane aggregation preceded complete evidence")
        evidence[work["work_id"]] = _work_evidence(
            point, work, persist_dedup=persist_dedup)
    return evidence


def _activate_lane(point: C0B6Checkpoint, master: Mapping[str, Any],
                   lane: Mapping[str, Any], prerequisite_sha256: str) -> str:
    envelopes = list(master["lane_plans"]) + [master["acceptance_template"]]
    later = LANE_ORDER[LANE_ORDER.index(lane["lane_id"]) + 1:]
    value = validate_artifact({
        "version": "c0b6-plan-activation-v1", **_identity(point.header()),
        "plan_sha256": lane["plan_sha256"],
        "prerequisite_sha256": prerequisite_sha256,
        "activated_work_ids": sorted(row["work_id"] for row in lane["work"]),
        "inactive_work_ids": sorted(
            row["work_id"] for envelope in envelopes
            if envelope["payload"]["lane_id"] in later
            for row in envelope["payload"]["work"]),
    })
    return _store_once(point, "plan_activation", lane["lane_id"], value)


def _cursor_transition(point: C0B6Checkpoint, *, lane: Mapping[str, Any],
                       next_lane: Mapping[str, Any], aggregate_sha256: str) -> str:
    existing = _artifact(point, "cursor_transition", lane["lane_id"])
    value: dict[str, Any] = {
        "version": "c0b6-cursor-transition-v1", **_identity(point.header()),
        "from_lane_id": lane["lane_id"], "to_lane_id": next_lane["lane_id"],
        "from_aggregate_sha256": aggregate_sha256,
        "to_plan_sha256": next_lane["plan_sha256"],
        "completed_work_census_sha256": sha256_json({
            "lane_id": lane["lane_id"],
            "completed_work_ids": sorted(row["work_id"] for row in lane["work"])}),
        "transitioned_at_utc": (existing["transitioned_at_utc"]
                                if existing else _utc()),
    }
    value["transition_sha256"] = sha256_json(value)
    return _store_once(
        point, "cursor_transition", lane["lane_id"], validate_artifact(value))


def _finish_quality(
        point: C0B6Checkpoint, lock: GlobalExecutionLock, *,
        master: Mapping[str, Any], terminal: str, reason: str, first_hash: str,
        second_hash: str | None = None, c44_hash: str | None = None,
        acceptance_hash: str | None = None,
        repo_root: Path | None = None,
) -> dict[str, Any]:
    result = validate_artifact({
        "version": "c0b6-result-v1", **_identity(point.header()),
        "terminal": terminal, "reason": reason,
        "master_plan_sha256": sha256_json(master),
        "lane_aggregate_sha256s": {
            "f72_seed20260811_sha256": first_hash,
            "f72_seed20260818_sha256": second_hash,
            "c44_scored_sha256": c44_hash},
        "acceptance_aggregate_sha256": acceptance_hash,
        "selection": dict(SELECTION) if terminal == "CONFIRMED" else None,
    })
    result_hash = sha256_json(result)
    completion = validate_artifact({
        "version": "c0b6-completion-v1", **_identity(point.header()),
        "outcome": terminal, "artifact_sha256": result_hash,
        "facts": ({"confirmed": True} if terminal == "CONFIRMED" else
                  {"deterministic_stop": True, "reason": reason}),
    })
    if repo_root is not None:
        _recheck_before_terminal(point, repo_root=repo_root)
    artifact_hash, completion_hash = point.finalize(
        terminal, result, completion=completion)
    if repo_root is not None:
        _recheck_before_terminal(point, repo_root=repo_root)
    ensure_backup_receipt(
        point, lock, terminal_artifact_sha256=artifact_hash,
        completion_sha256=completion_hash)
    return _public_result(point)


def _finish_failure(
        point: C0B6Checkpoint, lock: GlobalExecutionLock, *, terminal: str,
        failure_origin: str,
        lane_id: str | None = None, plan_sha256: str | None = None,
        attempt_id: str | None = None, control_id: str | None = None,
        repo_root: Path | None = None,
) -> dict[str, Any]:
    reasons = {
        "FAILED_SAFETY": "safety_envelope_failure",
        "BLOCKED_PROVENANCE": "provenance_identity_failure",
        "BLOCKED_BUDGET": "call_allowance_exhausted",
        "BLOCKED_FILESYSTEM": "filesystem_capability_or_integrity_failure",
        "ABANDONED": "operator_abandoned",
    }
    evidence: dict[str, Any] = {
        "version": "c0b6-failure-evidence-v1", **_identity(point.header()),
        "terminal": terminal, "reason": reasons[terminal],
        "failure_origin": failure_origin, "lane_id": lane_id,
        "plan_sha256": plan_sha256, "attempt_id": attempt_id,
        "control_id": control_id, "charged_call_total": len(point.list_attempts()),
    }
    evidence["evidence_sha256"] = sha256_json(evidence)
    evidence_hash = _store_once(
        point, "failure_evidence", "terminal", validate_artifact(evidence))
    failure = validate_artifact({
        "version": "c0b6-failure-v1", **_identity(point.header()),
        "terminal": terminal, "reason": reasons[terminal],
        "failure_origin": failure_origin,
        "evidence_sha256": evidence_hash,
        "charged_call_total": len(point.list_attempts()),
    })
    if repo_root is not None:
        _recheck_before_terminal(point, repo_root=repo_root)
    artifact_hash, _ = point.finalize(terminal, failure)
    if repo_root is not None:
        _recheck_before_terminal(point, repo_root=repo_root)
    ensure_backup_receipt(
        point, lock, terminal_artifact_sha256=artifact_hash,
        completion_sha256=None)
    return _public_result(point)


def _origin_for_terminal(terminal: str, provenance_origin: str) -> str:
    """Map a terminal to one frozen, content-free diagnostic origin."""
    return {
        "FAILED_SAFETY": "safety_transport",
        "BLOCKED_BUDGET": "budget_claim",
    }.get(terminal, provenance_origin)


def _ensure_terminal_backup(point: C0B6Checkpoint,
                            lock: GlobalExecutionLock) -> dict[str, Any]:
    quality = point.state() in {"CONFIRMED", "INCONCLUSIVE"}
    terminal = _artifact(point, "result" if quality else "failure", "terminal")
    if terminal is None:
        raise C0B6RuntimeError("terminal state lacks its owner artifact")
    completion = _artifact(point, "completion", "terminal") if quality else None
    ensure_backup_receipt(
        point, lock, terminal_artifact_sha256=sha256_json(terminal),
        completion_sha256=sha256_json(completion) if completion else None)
    return _public_result(point)


def _recheck_before_terminal(point: C0B6Checkpoint, *, repo_root: Path) -> None:
    revalidate_source_pins(point.header(), repo_root=repo_root)
    verify_parents_readonly(ParentPaths(*point.parent_paths()))


def _pause(point: C0B6Checkpoint, state: str) -> dict[str, Any]:
    point.transition(state)
    return _public_result(point)


def _blocked_parent_drift(point: C0B6Checkpoint) -> dict[str, Any]:
    """Report immutable-parent drift without falsely mutating trusted lineage."""
    return {**_public_result(point), "state": "BLOCKED_PROVENANCE",
            "durable_terminal": False, "reason": "immutable_parent_drift",
            "failure_origin": "parent_replay"}


def _parent_component(parents: Any, corpus: Any) -> dict[str, Any]:
    facts = parents.c0b3_d50_facts
    return derive_parent_d50_component(
        facts.final_d_decision, facts.d4_aggregate, corpus=corpus,
        negative_retained_findings=facts.negative_retained_findings)


def _lane_kind(lane_id: str) -> str:
    return "c44_aggregate" if lane_id == "C44_1" else "lane_aggregate"


def _derive_lane(
        point: C0B6Checkpoint, resolver: _Resolver, transport: Callable[..., Any],
        *, lane: Mapping[str, Any], controls: Mapping[str, Any], corpus: Any,
        ordinal: int, monotonic: Callable[[], float], sleep: Callable[[float], None],
        now: Callable[[], float], within_wall: Callable[[], bool],
        allow_control_contact: bool,
) -> tuple[dict[str, Any], str | None, str | None]:
    evidence = _lane_evidence(
        point, lane, resolver, persist_dedup=allow_control_contact)
    if lane["lane_id"] != "F72_20260811":
        return build_lane_aggregate(lane, evidence, corpus=corpus), None, None
    trigger = _first_answered_lane_work(point, resolver, lane)
    if trigger is None:
        raise C0B6RuntimeError("first lane lacks context evidence")
    state, owner = _ensure_context(
        point, resolver, transport, ordinal=ordinal, controls=controls,
        trigger_work=trigger[0], trigger_attempt_id=trigger[1],
        before_call=within_wall)
    if state != "RAW_VALID":
        return {}, state, owner
    context = _artifact(
        point, "context_evidence", controls["context"].control["control_id"])
    if context is None:
        raise C0B6RuntimeError("first lane lacks context evidence")
    context_hash = sha256_json(context)
    preliminary = build_precontrol_lane_aggregate(
        lane, evidence, corpus=corpus,
        context_evidence_sha256=context_hash)
    if preliminary is not None:
        return preliminary, None, None
    cancel_id = controls["cancellation"].control["control_id"]
    if not allow_control_contact and _artifact(
            point, "cancellation_health_evidence", cancel_id) is None:
        raise C0B6RuntimeError("passing first lane lacks cancellation evidence")
    state, owner = _run_cancel_health(
        point, resolver, transport, ordinal=ordinal, controls=controls,
        monotonic=monotonic, sleep=sleep, now=now, before_call=within_wall)
    if state != "RAW_VALID":
        return {}, state, owner
    control = _artifact(point, "cancellation_health_evidence", cancel_id)
    return build_lane_aggregate(
        lane, evidence, corpus=corpus,
        context_evidence_sha256=context_hash,
        cancellation_health_evidence_sha256=sha256_json(control),
        controls_passed=control["passed"]), None, None


def _terminal_from_existing(point: C0B6Checkpoint, lock: GlobalExecutionLock,
                            master: Mapping[str, Any]) -> dict[str, Any] | None:
    first = _artifact(point, "lane_aggregate", "F72_20260811")
    if first is not None and not first["passed"]:
        reason = ("seed20260811_control_gate_failed"
                  if first["failure_reasons"] == ["cancellation_health_failure"]
                  else "seed20260811_no_qualifier")
        return _finish_quality(
            point, lock, master=master, terminal="INCONCLUSIVE", reason=reason,
            first_hash=sha256_json(first))
    second = _artifact(point, "lane_aggregate", "F72_20260818")
    if second is not None and not second["passed"]:
        return _finish_quality(
            point, lock, master=master, terminal="INCONCLUSIVE",
            reason="seed20260818_no_qualifier", first_hash=sha256_json(first),
            second_hash=sha256_json(second))
    return None


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
        parent_d50_loader: Callable[[Any, Any], Mapping[str, Any]] | None = None,
        stop_at_stage_boundary: bool = True,
) -> dict[str, Any]:
    """Run or explicitly resume the exact serial C0B-6 schedule."""
    if type(resume) is not bool or soft_wall_seconds <= 0:
        raise ValueError("resume and soft-wall inputs are invalid")
    root = Path(benchmark_root) if benchmark_root is not None else report.bench_root()
    cancel = cancellation or threading.Event()
    with GlobalExecutionLock(root) as lock:
        with C0B6Checkpoint.open(_checkpoint_path(run_id, root), writable=True) as point:
            if point.state() in TERMINAL_STATES:
                _recheck_before_terminal(point, repo_root=repo_root)
                return _ensure_terminal_backup(point, lock)
            validate_run_lineage(point.conn, point.header())
            if point.state() != "PREPARED" and not resume:
                raise C0B6RuntimeError("non-prepared confirmation requires --resume")
            try:
                revalidate_source_pins(point.header(), repo_root=repo_root)
                paths = ParentPaths(*point.parent_paths())
            except C0B6FilesystemError:
                return _finish_failure(
                    point, lock, terminal="BLOCKED_FILESYSTEM",
                    failure_origin="filesystem_revalidation")
            except Exception:
                return _finish_failure(
                    point, lock, terminal="BLOCKED_PROVENANCE",
                    failure_origin="source_revalidation")
            try:
                parents = verify_parents_readonly(paths)
            except Exception:
                return _blocked_parent_drift(point)
            try:
                corpus = _corpus(point.header())
                master = validate_master_plan(
                    point.read_artifact("master_plan", "master"), corpus=corpus,
                    run_nonce_key=point.read_nonce_key())
            except Exception:
                return _finish_failure(
                    point, lock, terminal="BLOCKED_PROVENANCE",
                    failure_origin="master_replay")
            try:
                resolver = _Resolver(master, corpus, point.read_nonce_key())
                _validate_execution_history(point, resolver)
                point.recover_dispatching()
                reconcile_runtime_events(point, resolver)
                _validate_execution_history(
                    point, resolver, require_complete_events=True)
                validate_run_lineage(point.conn, point.header())
            except Exception:
                return _finish_failure(
                    point, lock, terminal="BLOCKED_PROVENANCE",
                    failure_origin="resume_history")
            controls = resolver.controls_resolved
            try:
                _verify_existing_control_evidence(point, resolver, controls)
            except Exception:
                return _finish_failure(
                    point, lock, terminal="BLOCKED_PROVENANCE",
                    failure_origin="resume_control_replay",
                    repo_root=repo_root)
            if cancel.is_set():
                if point.state() == "PREPARED":
                    point.transition("RUNNING")
                return _pause(point, "CANCELLED_PENDING_RESUME")
            point.transition("RUNNING")
            try:
                ordinal = point.claim_invocation()
            except C0B6BudgetError:
                return _finish_failure(
                    point, lock, terminal="BLOCKED_BUDGET",
                    failure_origin="budget_claim", repo_root=repo_root)
            transport = (transport_factory(resolver, point.header())
                         if transport_factory is not None else
                         BoundedOllamaTransport(
                             resolver, endpoint=point.header()["ollama_endpoint"]))
            started = monotonic()
            within_wall = lambda: monotonic() - started < soft_wall_seconds
            try:
                preflight, attempt = _run_preflight(
                    point, resolver, transport, ordinal, before_call=within_wall)
            except C0B6BudgetError:
                return _finish_failure(
                    point, lock, terminal="BLOCKED_BUDGET",
                    failure_origin="budget_claim", repo_root=repo_root)
            if preflight == "RETRYABLE_TRANSPORT":
                return _pause(point, "PAUSED_PREFLIGHT")
            if preflight == "PAUSED_SOFT_WALL":
                return _pause(point, preflight)
            if preflight in {"FAILED_SAFETY", "BLOCKED_PROVENANCE"}:
                return _finish_failure(
                    point, lock, terminal=preflight, attempt_id=attempt,
                    failure_origin=_origin_for_terminal(preflight, "preflight"),
                    repo_root=repo_root)
            lane_hashes: dict[str, str] = {}
            prerequisite = sha256_json(master)
            for index, lane_id in enumerate(LANE_ORDER):
                lane = point.read_artifact("lane_plan", lane_id)
                kind = _lane_kind(lane_id)
                aggregate = _artifact(point, kind, lane_id)
                if index:
                    previous = point.read_artifact("lane_plan", LANE_ORDER[index - 1])
                    try:
                        _cursor_transition(
                            point, lane=previous, next_lane=lane,
                            aggregate_sha256=prerequisite)
                    except Exception:
                        return _finish_failure(
                            point, lock, terminal="BLOCKED_PROVENANCE",
                            failure_origin="cursor_transition",
                            lane_id=lane_id, plan_sha256=lane["plan_sha256"],
                            repo_root=repo_root)
                try:
                    _activate_lane(point, master, lane, prerequisite)
                except Exception:
                    return _finish_failure(
                        point, lock, terminal="BLOCKED_PROVENANCE",
                        failure_origin="lane_activation",
                        lane_id=lane_id, plan_sha256=lane["plan_sha256"],
                        repo_root=repo_root)
                newly_built = aggregate is None
                if newly_built:
                    try:
                        outcome, attempt = _run_lane_work(
                            point, resolver, transport, ordinal=ordinal, lane=lane,
                            controls=controls, clock_started=started,
                            monotonic=monotonic, soft_wall_seconds=soft_wall_seconds,
                            cancellation=cancel)
                    except C0B6BudgetError:
                        outcome, attempt = "BLOCKED_BUDGET", None
                    if outcome.startswith("PAUSED_") or \
                            outcome == "CANCELLED_PENDING_RESUME":
                        return _pause(point, outcome)
                    if outcome in {"BLOCKED_BUDGET", "FAILED_SAFETY",
                                   "BLOCKED_PROVENANCE"}:
                        return _finish_failure(
                            point, lock, terminal=outcome, lane_id=lane_id,
                            failure_origin=_origin_for_terminal(
                                outcome, "lane_execution"),
                            plan_sha256=lane["plan_sha256"], attempt_id=attempt,
                            repo_root=repo_root)
                try:
                    derived, control_state, control_owner = _derive_lane(
                        point, resolver, transport, lane=lane, controls=controls,
                        corpus=corpus, ordinal=ordinal, monotonic=monotonic,
                        sleep=sleep, now=now, within_wall=within_wall,
                        allow_control_contact=newly_built)
                    if control_state is not None:
                        if control_state.startswith("PAUSED_"):
                            return _pause(point, control_state)
                        return _finish_failure(
                            point, lock, terminal=control_state, lane_id=lane_id,
                            failure_origin=_origin_for_terminal(
                                control_state, "lane_derivation"),
                            plan_sha256=lane["plan_sha256"],
                            attempt_id=control_owner, repo_root=repo_root)
                    if aggregate is not None and canonical_json(
                            aggregate) != canonical_json(derived):
                        raise C0B6RuntimeError("stored aggregate does not rederive")
                    aggregate = derived
                    _store_once(point, kind, lane_id, aggregate)
                except Exception:
                    return _finish_failure(
                        point, lock, terminal="BLOCKED_PROVENANCE",
                        failure_origin="lane_derivation",
                        lane_id=lane_id, plan_sha256=lane["plan_sha256"],
                        repo_root=repo_root)
                aggregate_hash = sha256_json(aggregate)
                lane_hashes[lane_id], prerequisite = aggregate_hash, aggregate_hash
                if lane_id.startswith("F72") and not aggregate["passed"]:
                    reason = ("seed20260811_control_gate_failed"
                              if lane_id == "F72_20260811" and
                              aggregate["failure_reasons"] == [
                                  "cancellation_health_failure"] else
                              "seed20260811_no_qualifier" if
                              lane_id == "F72_20260811" else
                              "seed20260818_no_qualifier")
                    return _finish_quality(
                        point, lock, master=master, terminal="INCONCLUSIVE",
                        reason=reason,
                        first_hash=lane_hashes["F72_20260811"],
                        second_hash=lane_hashes.get("F72_20260818"),
                        repo_root=repo_root)
                if newly_built and stop_at_stage_boundary and lane_id != "C44_1":
                    return _pause(point, "PAUSED_STAGE_BOUNDARY")
            first = _artifact(point, "lane_aggregate", "F72_20260811")
            second = _artifact(point, "lane_aggregate", "F72_20260818")
            c44 = _artifact(point, "c44_aggregate", "C44_1")
            loader = parent_d50_loader or _parent_component
            try:
                d50 = dict(loader(parents, corpus))
                acceptance = build_acceptance_aggregate(
                    c44, d50, first, corpus=corpus,
                    acceptance_plan_sha256=point.read_artifact(
                        "lane_plan", "C44_1")["plan_sha256"],
                    cancellation_health_passed=True,
                    provenance_passed=True, safety_passed=True)
                acceptance_hash = _store_once(
                    point, "acceptance_aggregate", "complete", acceptance)
            except Exception:
                lane = point.read_artifact("lane_plan", "C44_1")
                return _finish_failure(
                    point, lock, terminal="BLOCKED_PROVENANCE", lane_id="C44_1",
                    failure_origin="acceptance_derivation",
                    plan_sha256=lane["plan_sha256"], repo_root=repo_root)
            terminal = "CONFIRMED" if acceptance["passed"] else "INCONCLUSIVE"
            return _finish_quality(
                point, lock, master=master, terminal=terminal,
                reason=("complete_public_acceptance_passed" if acceptance["passed"]
                        else "complete_corpus_acceptance_failed"),
                first_hash=sha256_json(first), second_hash=sha256_json(second),
                c44_hash=sha256_json(c44), acceptance_hash=acceptance_hash,
                repo_root=repo_root)


def abandon_confirmation_run(
        run_id: str, *, repo_root: Path = REPO_ROOT,
        benchmark_root: Path | None = None,
) -> dict[str, Any]:
    root = Path(benchmark_root) if benchmark_root is not None else report.bench_root()
    with GlobalExecutionLock(root) as lock:
        with C0B6Checkpoint.open(_checkpoint_path(run_id, root), writable=True) as point:
            if point.state() in TERMINAL_STATES:
                _recheck_before_terminal(point, repo_root=repo_root)
                return _ensure_terminal_backup(point, lock)
            try:
                revalidate_source_pins(point.header(), repo_root=repo_root)
            except C0B6FilesystemError:
                return _finish_failure(
                    point, lock, terminal="BLOCKED_FILESYSTEM",
                    failure_origin="filesystem_revalidation")
            except Exception:
                return _finish_failure(
                    point, lock, terminal="BLOCKED_PROVENANCE",
                    failure_origin="source_revalidation")
            try:
                verify_parents_readonly(ParentPaths(*point.parent_paths()))
            except Exception:
                return _blocked_parent_drift(point)
            try:
                corpus = _corpus(point.header())
                master = validate_master_plan(
                    point.read_artifact("master_plan", "master"), corpus=corpus,
                    run_nonce_key=point.read_nonce_key())
                resolver = _Resolver(master, corpus, point.read_nonce_key())
                point.recover_dispatching()
                reconcile_runtime_events(point, resolver)
            except Exception:
                return _finish_failure(
                    point, lock, terminal="BLOCKED_PROVENANCE",
                    failure_origin="resume_history")
            _recheck_before_terminal(point, repo_root=repo_root)
            return _finish_failure(
                point, lock, terminal="ABANDONED",
                failure_origin="operator_abandon", repo_root=repo_root)
