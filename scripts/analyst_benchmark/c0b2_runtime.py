"""Offline run creation and guarded public Stage-C runtime for C0B-2B1.

Public creation, status, and verification never contact Ollama.  The live entrypoint is
separate and imports the bounded transport only after the CLI confirmation gate.

DISPOSITION: benchmark-only diagnostic; remove after accepted C0B artifacts.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import signal
import subprocess
import threading
import time
from dataclasses import asdict
from pathlib import Path
from types import FrameType
from typing import Any, Callable, Mapping

from . import chunker, goldset, metrics, report
from .c0b2_checkpoint import (Checkpoint, CheckpointError, RUN_ID_RE,
                              TERMINAL_STATES, canonical_json)
from .c0b2_fsprobe import (GlobalExecutionLock, backup_snapshot,
                           probe_filesystem, status_readonly, verify_readonly)
from .c0b2_leakscan import (FROZEN_C0B2B1_PATHS, WorktreeSeal,
                            capture_worktree_seal)
from .c0b2_plan import (KEEP_ALIVE, MODELS, OPTIONS_C, WorkItem,
                        build_c_stage_plan, build_master_manifest,
                        master_manifest_payload, stage_plan_payload)
from .c0b2_schema import (prompt_template_hash, schema_hash, stable_hash)

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
_SNAPSHOT_REQUIRED_STATES = frozenset({
    "PAUSED_STAGE_BOUNDARY", "INCONCLUSIVE", "SELECTED",
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
    seal = capture_worktree_seal(repo_root)
    _require_clean_task_delta(seal)

    corpus = goldset.load(verify=True)
    manifest = build_master_manifest(corpus)
    plan = build_c_stage_plan(secrets.token_bytes(32), corpus)
    manifest_body = _manifest_payload(manifest)
    plan_body = _plan_payload(plan)
    if stable_hash(manifest_body) != manifest.sha256 or stable_hash(plan_body) != plan.sha256:
        raise RuntimeGateError("generated manifest/plan hash is not canonical")
    filesystem = probe_filesystem(root)
    header = _header(repo_root, seal, filesystem, manifest)

    point = Checkpoint.create(
        root, identity, header=header, limits=PUBLIC_LIMITS,
        cumulative_cap=PUBLIC_CUMULATIVE_CAP, journal_mode=JOURNAL_MODE,
    )
    try:
        manifest_hash = point.freeze_manifest("master", manifest_body)
        if manifest_hash != manifest.sha256:
            raise RuntimeGateError("checkpoint master-manifest hash changed")
        plan_hash = point.freeze_plan("C", manifest_hash, plan_body)
        if plan_hash != plan.sha256:
            raise RuntimeGateError("checkpoint Stage-C plan hash changed")
        for item in plan.work:
            point.register_work(
                item.work_id, "C", item.cell_id, item.request_sha256)
        with GlobalExecutionLock(root) as lock:
            backup_snapshot(point, root / "snapshots" / identity, lock=lock)
    finally:
        point.close()
    return identity


def _checkpoint_path(run_id: str, benchmark_root: Path | None = None) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid public run id")
    root = Path(benchmark_root) if benchmark_root is not None else report.bench_root()
    return root / "runs" / run_id / "checkpoint.sqlite3"


def public_status(run_id: str, *, benchmark_root: Path | None = None) -> dict[str, Any]:
    return status_readonly(_checkpoint_path(run_id, benchmark_root))


def public_verify(run_id: str, *, benchmark_root: Path | None = None) -> dict[str, Any]:
    result = verify_readonly(_checkpoint_path(run_id, benchmark_root))
    return {"ok": result.ok, "errors": list(result.errors)}


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
    """Idempotently ensure a verified snapshot contains the frozen final state."""
    state = point.state()
    if state not in _SNAPSHOT_REQUIRED_STATES:
        raise CheckpointError(f"state {state} does not require a final snapshot")
    snapshot_root = point.root / "snapshots" / run_id
    if snapshot_root.is_dir() and not snapshot_root.is_symlink():
        for candidate in sorted(snapshot_root.glob("snapshot-*.sqlite3")):
            try:
                if (verify_readonly(candidate).ok
                        and status_readonly(candidate).get("state") == state):
                    return candidate
            except (OSError, CheckpointError):
                continue
    return backup_snapshot(point, snapshot_root, lock=lock)


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
            point.transition("BLOCKED_PROVENANCE")
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
    if state in _SNAPSHOT_REQUIRED_STATES:
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
            if state in _SNAPSHOT_REQUIRED_STATES:
                _ensure_final_snapshot(point, lock, run_id)
                return _public_result(point, run_id)
            if state in TERMINAL_STATES:
                return _public_result(point, run_id)
            return _run_public_stage_c_locked(
                point, lock, run_id, transport_factory=transport_factory)
        finally:
            point.close()
