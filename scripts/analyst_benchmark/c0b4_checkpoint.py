"""Isolated, fail-closed checkpoint storage for the C0B-4 confirmation run.

The C0B-4 database is deliberately not a migration of a C0B-2/C0B-3 database.
Creation verifies and pins the terminal C0B-3 parent before creating any child path,
then publishes an owner-only INITIALIZING directory with rename-no-replace.

DISPOSITION: benchmark-only; remove after the accepted confirmation is handed to C1.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .c0b2_checkpoint import _owner_directory, _rename_noreplace, _secure_dir
from .c0b2_schema import FrozenMount
from .c0b4_policy import require_current_header
from .c0b4_schema import ParentBinding, SourceBinding, validate_artifact

PROTOCOL_ID = "c0b4-grounded-duplicate-confirmation-v1"
POLICY_ID = "c0b4-bounded-grounded-dedup-v1"
POLICY_SHA256 = "7475e748165366ed0fb83daf1b6dae21a98d92d1c8faf3e3b7a3513aa3745c43"
HEADER_VERSION = "c0b4-run-header-v1"
SCHEMA_VERSION = 1
LEDGER_LIMITS = {
    "scored": 228,
    "schema_retry": 4,
    "preflight_control": 33,
    "transport_orphan": 30,
}
CUMULATIVE_CAP = 295
INVOCATION_CAPS = {"total": 10}

PARENT_RUN_ID = "c0b3-20260809-154924-19afcaab26984160f20ec075"
FROZEN_PARENT_BINDING: dict[str, Any] = {
    "run_id": PARENT_RUN_ID,
    "source_commit": "dcd7e0b9504ded47dad82f25814aea54d666b268",
    "checkpoint_sha256": "f8cbd0419f62656476b38c60b628b1ce20f67b097d2ce7e8bc38381d80d852e3",
    "run_header_sha256": "80424fbfb492cae4264798d6294337c3beaca21f2172da302114adf05d8210b2",
    "benchmark_protocol_id": "c0b3-assistive-confirmation-v1",
    "protocol_sha256": "031b41f6cf0f153b94c47dc55907eae77fd6600379c009434dbc752deb33022d",
    "policy_id": "c0b3-assistive-bounded-fp-v1",
    "policy_sha256": "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235",
    "task_tree_sha256": "a936817083810cebc4f017d34f4d0be5e0821c1c1e0c9ffff218df69b9782bb0",
    "final_d_decision_sha256": "5c00ef2b06c014f7617bdb367034dc7be99fd462467961c7a15d3eac5b53d894",
    "d4_aggregate_sha256": "7cf23921758c6be35038456e7f4e568cef4f20618bf8ce9a9dddac5af7bab945",
    "f_master_plan_sha256": "093af02da48d938278e791955dc196ec1c8e0dacb434ddbe204186f2fbb963de",
    "seed1_aggregate_sha256": "cd87e163b2ac08b9f4de9f90291247411e80830a23a9bf635f8e6e2ba9eb11e1",
    "terminal_result_sha256": "ee2c8ed8c923deba3fb30eec3dcf5af87da69de9678bf6f45303e5ffeb1d9bcc",
    "completion_sha256": "6958b94d19d2a404003fba3e2d628a6828810cd503e8ced5bfc76f4f4ead5c00",
    "master_manifest_sha256": "df609a7c5c0baaf3215bb74ef8a3598c5f8ad5b75a16caad41cf3cd1523d5e12",
    "seed17_old_plan_sha256": "2175e51108362a273f13292b95fafd724cfc90b6817b15197c93fe2055d41f31",
    "seed17_old_plan_census": {"planned_work_rows": 92, "registered_work_rows": 0,
                                "attempt_rows": 0, "activation_rows": 0},
    "seed20260804_old_plan_sha256": "0a8e56835af83659ae6274772401da742feefb6e4d4121ed7c995cafbe9dcb21",
    "seed20260804_old_plan_census": {"planned_work_rows": 92, "registered_work_rows": 0,
                                      "attempt_rows": 0, "activation_rows": 0},
    "backup_anchor_sha256": "b37396143265013ed01361d7ec31edff3d84c358d2f6c8ce932df39b21e61c56",
    "backup_snapshot_sha256": "262498adb36c12ef44fdeb779283e17305378cfcac33b4e87c740017453a799c",
    "backup_receipt_sha256": "398755d38227c30c527c787c3205407ed0ba47f18ccfab4b865584fb74ec14f9",
}
# Mechanically prove that the duplicated storage-boundary constants match the
# independently strict artifact contract at import time.
ParentBinding.model_validate(FROZEN_PARENT_BINDING, strict=True)

HEADER_KEYS = frozenset({
    "version", "run_type", "benchmark_protocol_id", "policy_id", "policy_sha256",
    "protocol_sha256", "parent_binding", "ollama_endpoint", "ollama_version",
    "filesystem_selected_mode", "git_head", "declared_dirty_state_sha256",
    "task_tree_sha256", "fixture_sha256", "master_manifest_sha256", "schema_sha256",
    "prompt_sha256", "chunker_sha256", "detector_sha256", "generation_options_sha256",
    "worktree_seal_sha256", "filesystem_capability_sha256", "model_digests", "mount",
    "schema_version", "journal_mode", "cumulative_cap", "run_id", "limits",
    "invocation_caps",
})
RESUMABLE_STATES = frozenset({
    "PREPARED", "RUNNING", "PAUSED_SOFT_WALL", "PAUSED_RESOURCE",
    "PAUSED_PREFLIGHT", "PAUSED_STAGE_BOUNDARY", "CANCELLED_PENDING_RESUME",
})
TERMINAL_STATES = frozenset({
    "CONFIRMED", "INCONCLUSIVE", "FAILED_SAFETY", "BLOCKED_PROVENANCE",
    "BLOCKED_BUDGET", "BLOCKED_FILESYSTEM", "ABANDONED",
})
ALL_STATES = RESUMABLE_STATES | TERMINAL_STATES | {"INITIALIZING"}
ATTEMPT_OUTCOMES = frozenset({
    "RAW_VALID", "NORMALIZED_DUPLICATE", "SCHEMA_INVALID", "INVALID",
    "RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN", "CANCELLED", "CANCELLED_UNVERIFIED",
    "FAILED_SAFETY", "BLOCKED_PROVENANCE",
})
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class C0B4CheckpointError(RuntimeError):
    """C0B-4 checkpoint evidence or lifecycle is invalid."""


class C0B4BudgetError(C0B4CheckpointError):
    """A frozen C0B-4 call or invocation allowance is exhausted."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def sha256_json(value: Any, *, omit: str | None = None) -> str:
    preimage = dict(value) if omit is not None else value
    if omit is not None:
        preimage.pop(omit, None)
    return hashlib.sha256(canonical_json(preimage).encode()).hexdigest()


def _utc_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(
        timespec="microseconds").replace("+00:00", "Z")


_STORED_ARTIFACT_VERSIONS = {
    "master_plan": frozenset({"c0b4-master-plan-v1"}),
    "lane_plan": frozenset({"c0b4-lane-plan-v1", "c0b4-acceptance-plan-v1"}),
    "plan_activation": frozenset({"c0b4-plan-activation-v1"}),
    "cursor_transition": frozenset({"c0b4-cursor-transition-v1"}),
    "context_evidence": frozenset({"c0b4-context-evidence-v1"}),
    "cancellation_health_evidence": frozenset({
        "c0b4-cancellation-health-evidence-v1"}),
    "dedup_evidence": frozenset({"c0b4-dedup-evidence-v1"}),
    "lane_aggregate": frozenset({"c0b4-lane-aggregate-v1"}),
    "c44_aggregate": frozenset({"c0b4-c44-scored-v1"}),
    "acceptance_aggregate": frozenset({"c0b4-acceptance-aggregate-v1"}),
    "result": frozenset({"c0b4-result-v1"}),
    "completion": frozenset({"c0b4-completion-v1"}),
    "failure_evidence": frozenset({"c0b4-failure-evidence-v1"}),
    "failure": frozenset({"c0b4-failure-v1"}),
}


def _validate_stored_artifact(kind: str, value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = _STORED_ARTIFACT_VERSIONS.get(kind)
    if allowed is None or value.get("version") not in allowed:
        raise C0B4CheckpointError("stored artifact kind/version pair is not allowed")
    return validate_artifact(value)


def _lineage_corpus(header: Mapping[str, Any]) -> Any:
    from . import goldset
    from .c0b2_plan import build_master_manifest, master_manifest_payload
    from .c0b2_stage_f_plan import load_public_corpus
    source = goldset.load(verify=True)
    manifest = build_master_manifest(source)
    if manifest.sha256 != header["master_manifest_sha256"]:
        raise C0B4CheckpointError("run lineage public manifest changed")
    return load_public_corpus(
        master_manifest_payload(manifest),
        master_manifest_sha256=manifest.sha256, source=source)


def _parent_d50_sha256(conn: sqlite3.Connection, header: Mapping[str, Any],
                       corpus: Any) -> str:
    from .c0b4_scoring import derive_parent_d50_component
    row = conn.execute(
        "SELECT db_path FROM parent_files WHERE id=1").fetchone()
    if not row:
        raise C0B4CheckpointError("run lineage parent path is absent")
    path = Path(row[0])
    fd = _open_owner_file(path)
    try:
        _assert_named(path, fd)
        if _hash_fd(fd) != header["parent_binding"]["checkpoint_sha256"]:
            raise C0B4CheckpointError("run lineage parent checkpoint changed")
        parent = _verify_sqlite_fd(fd)
        try:
            decision = parent.execute(
                "SELECT value_json FROM decisions "
                "WHERE decision_id='stage-d-selection'").fetchone()
            aggregate = parent.execute(
                "SELECT aggregate_json FROM phase_aggregates "
                "WHERE plan_key='D4_CONFIRMATION'").fetchone()
        finally:
            parent.close()
    finally:
        os.close(fd)
    if not decision or not aggregate:
        raise C0B4CheckpointError("run lineage parent D50 evidence is absent")
    try:
        d50 = derive_parent_d50_component(
            json.loads(decision[0]), json.loads(aggregate[0]), corpus=corpus)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise C0B4CheckpointError("run lineage parent D50 does not rederive") from exc
    return sha256_json(d50)


def _base_attempt_catalog(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """Map every frozen work/control owner to its exact request identity."""
    master_row = conn.execute(
        "SELECT json FROM artifacts WHERE kind='master_plan' AND owner_id='master'").fetchone()
    if not master_row:
        raise C0B4CheckpointError("attempt catalog lacks its master plan")
    master = _validate_stored_artifact("master_plan", json.loads(master_row[0]))
    catalog: dict[str, tuple[str, str]] = {}
    for envelope in [*master["lane_plans"], master["acceptance_template"]]:
        for work in envelope["payload"]["work"]:
            catalog[work["work_id"]] = ("work", work["request_sha256"])
    controls = master["control_plan"]
    catalog.update({
        controls["context"]["control_id"]:
            ("context", controls["context"]["payload_sha256"]),
        controls["cancellation"]["control_id"]:
            ("cancellation", controls["cancellation"]["request_sha256"]),
        controls["health"]["control_id"]:
            ("health", controls["health"]["request_sha256"]),
    })
    return catalog


def _preflight_attempt_catalog(header: Mapping[str, Any],
                               ordinal: int) -> dict[str, tuple[str, str]]:
    from .c0b2_plan import stable_hash
    from .c0b2_transport import RequestSpec, request_spec_hash
    model = "qwen3.6:27b"
    digest = header["model_digests"][model]
    specs = {
        "version": RequestSpec(kind="version", expected_version=header["ollama_version"]),
        "tags": RequestSpec(kind="tags", expected_models={model: digest}),
        "show": RequestSpec(kind="show", expected_model=model, expected_digest=digest),
    }
    return {
        stable_hash({"c0b4_preflight": kind, "invocation": ordinal}):
            ("preflight", request_spec_hash(spec))
        for kind, spec in specs.items()
    }


def _expected_attempt(
        header: Mapping[str, Any], base_catalog: Mapping[str, tuple[str, str]],
        *, owner_id: str, invocation_ordinal: int,
        prior: list[Mapping[str, Any]],
) -> tuple[str, str, str]:
    from .c0b2_plan import attempt_id as stable_attempt_id
    try:
        owner_kind, request_sha256 = (base_catalog[owner_id]
                                      if owner_id in base_catalog else
                                      _preflight_attempt_catalog(
                                          header, invocation_ordinal)[owner_id])
    except KeyError as exc:
        raise C0B4CheckpointError("attempt owner is outside the frozen catalog") from exc
    attempt_no = len(prior) + 1
    identity_owner = owner_id if owner_kind == "work" else f"control:{owner_id}"
    expected_id = stable_attempt_id(identity_owner, attempt_no)
    if owner_kind == "preflight":
        if prior:
            raise C0B4CheckpointError("standard preflight owner was reused")
        call_class = "preflight_control"
    elif not prior:
        call_class = "scored" if owner_kind == "work" else "preflight_control"
    else:
        previous = prior[-1]
        if previous["state"] in {
                "RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN", "CANCELLED"}:
            call_class = "transport_orphan"
        else:
            payload = (json.loads(previous["payload_json"])
                       if previous.get("payload_json") else {})
            if (previous["state"] not in {"SCHEMA_INVALID", "INVALID"}
                    or type(payload.get("response")) is not str
                    or owner_kind not in {"work", "health"}
                    or any(row["call_class"] == "schema_retry" for row in prior)):
                raise C0B4CheckpointError("attempt owner has no legal retry")
            call_class = "schema_retry"
    return expected_id, request_sha256, call_class


def _validate_new_attempt_identity(
        conn: sqlite3.Connection, header: Mapping[str, Any], *, attempt_id: str,
        owner_id: str, call_class: str, invocation_ordinal: int,
        request_sha256: str) -> None:
    prior_columns = ("state", "payload_json", "call_class")
    prior = [dict(zip(prior_columns, row, strict=True)) for row in conn.execute(
        "SELECT state,payload_json,call_class FROM attempts "
        "WHERE owner_id=? ORDER BY rowid", (owner_id,))]
    catalog = _base_attempt_catalog(conn)
    expected_id, expected_request, expected_class = _expected_attempt(
        header, catalog, owner_id=owner_id,
        invocation_ordinal=invocation_ordinal, prior=prior)
    if (type(attempt_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", attempt_id)
            or attempt_id != expected_id or request_sha256 != expected_request
            or call_class != expected_class):
        raise C0B4CheckpointError(
            "attempt identity differs from its frozen owner/request/class")
    if call_class == "schema_retry":
        kind = catalog[owner_id][0]
        partition = {owner_id}
        if kind == "work":
            master = _validate_stored_artifact("master_plan", json.loads(conn.execute(
                "SELECT json FROM artifacts WHERE kind='master_plan' "
                "AND owner_id='master'").fetchone()[0]))
            partition = {row["work_id"] for envelope in [
                *master["lane_plans"], master["acceptance_template"]]
                if any(row["work_id"] == owner_id for row in envelope["payload"]["work"])
                for row in envelope["payload"]["work"]}
        used = sum(row[0] in partition for row in conn.execute(
            "SELECT owner_id FROM attempts WHERE call_class='schema_retry'"))
        if kind not in {"work", "health"} or used:
            raise C0B4CheckpointError("schema retry partition allowance exhausted")


def _validate_cancel_health_timing(
        cancel_attempt: Mapping[str, Any],
        health_attempts: list[Mapping[str, Any]],
        evidence: Mapping[str, Any]) -> None:
    """Bind the health delay to the durable cancellation timestamp."""
    updated = cancel_attempt.get("updated")
    if (type(updated) not in (int, float) or not health_attempts
            or any(type(row.get("created")) not in (int, float)
                   for row in health_attempts)):
        raise C0B4CheckpointError("cancellation health timing is incomplete")
    try:
        payload = json.loads(cancel_attempt["payload_json"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise C0B4CheckpointError(
            "cancellation timing payload is invalid") from exc
    not_before = float(updated) + 2.0
    expected_utc = _utc_timestamp(not_before)
    if (payload.get("health_not_before_utc") != expected_utc
            or evidence.get("not_before_utc") != expected_utc
            or min(float(row["created"]) for row in health_attempts) < not_before):
        raise C0B4CheckpointError(
            "health request preceded its durable cancellation delay")


def _require_passed_prerequisite(
        aggregates: Mapping[str, tuple[Mapping[str, Any], str]], lane_id: str) -> None:
    if lane_id not in aggregates or aggregates[lane_id][0].get("passed") is not True:
        raise C0B4CheckpointError("lane activation prerequisite did not pass")


def validate_run_lineage(conn: sqlite3.Connection, header: Mapping[str, Any], *,
                         require_event_completeness: bool = True,
                         require_terminal_receipt: bool = False,
                         allow_pending_failure_evidence: bool = False) -> None:
    """Rederive the immutable plan tree and validate every stored cross-link."""
    from .c0b4_plan import LANE_ORDER, validate_master_plan
    rows = conn.execute(
        "SELECT kind,owner_id,sha256,json FROM artifacts "
        "ORDER BY kind,owner_id").fetchall()
    artifacts: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
    for kind, owner_id, digest, raw in rows:
        try:
            value = _validate_stored_artifact(kind, json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise C0B4CheckpointError("run lineage contains an invalid artifact") from exc
        if (canonical_json(value) != raw or sha256_json(value) != digest
                or any(value.get(key) != header[key] for key in (
                    "policy_id", "policy_sha256", "protocol_sha256"))):
            raise C0B4CheckpointError(
                "run lineage artifact bytes or policy/protocol binding changed")
        artifacts[(kind, owner_id)] = (value, digest)
    try:
        master, master_hash = artifacts[("master_plan", "master")]
        nonce = conn.execute(
            "SELECT value,sha256 FROM protected_values "
            "WHERE name='nonce_key'").fetchone()
        if (not nonce or not isinstance(nonce[0], bytes) or len(nonce[0]) != 32
                or hashlib.sha256(nonce[0]).hexdigest() != nonce[1]):
            raise C0B4CheckpointError("run lineage nonce changed")
        if master["parent_binding"] != header["parent_binding"]:
            raise C0B4CheckpointError("run lineage master parent binding changed")
        corpus = _lineage_corpus(header)
        exact_master = validate_master_plan(
            master, corpus=corpus, run_nonce_key=bytes(nonce[0]))
        if canonical_json(exact_master) != canonical_json(master):
            raise C0B4CheckpointError("run lineage master plan changed")
        envelopes = [*master["lane_plans"], master["acceptance_template"]]
        plans = {row["payload"]["lane_id"]: row for row in envelopes}
        if list(plans) != list(LANE_ORDER):
            raise C0B4CheckpointError("run lineage lane order changed")
        for lane_id, envelope in plans.items():
            stored, _stored_hash = artifacts[("lane_plan", lane_id)]
            if (stored != envelope["payload"]
                    or stored["plan_sha256"] != envelope["plan_sha256"]
                    or stored["parent_evidence"] != master["parent_binding"]):
                raise C0B4CheckpointError("run lineage lane differs from its master")
    except KeyError as exc:
        raise C0B4CheckpointError("run lineage frozen plan tree is incomplete") from exc

    aggregate_keys = {
        "F72_17": ("lane_aggregate", "lane_plan_sha256"),
        "F72_20260804": ("lane_aggregate", "lane_plan_sha256"),
        "C44_1": ("c44_aggregate", "acceptance_plan_sha256"),
    }
    aggregates: dict[str, tuple[dict[str, Any], str]] = {}
    work_by_id = {row["work_id"]: (envelope["payload"]["lane_id"], row)
                  for envelope in envelopes for row in envelope["payload"]["work"]}
    all_work_ids = set(work_by_id)
    controls = master["control_plan"]
    exact_owner = {
        "acceptance_aggregate": "complete", "result": "terminal",
        "completion": "terminal", "failure_evidence": "terminal",
        "failure": "terminal",
    }
    for (kind, owner_id), (value, digest) in artifacts.items():
        if kind in exact_owner and owner_id != exact_owner[kind]:
            raise C0B4CheckpointError("run lineage artifact has an extra owner")
        if (kind == "master_plan" and owner_id != "master") or (
                kind == "lane_plan" and owner_id not in plans):
            raise C0B4CheckpointError("run lineage plan has an extra owner")
        if kind in {"lane_aggregate", "c44_aggregate"}:
            expected_kind, plan_field = aggregate_keys.get(owner_id, (None, None))
            if (kind != expected_kind or value["lane_id"] != owner_id
                    or value[plan_field] != plans[owner_id]["plan_sha256"]
                    or value["parent_binding"] != master["parent_binding"]):
                raise C0B4CheckpointError("run lineage aggregate differs from its plan")
            aggregates[owner_id] = (value, digest)
        elif kind == "dedup_evidence" and (
                value["work_id"] != owner_id or owner_id not in all_work_ids):
            raise C0B4CheckpointError("run lineage dedup evidence is misowned")
        elif kind == "context_evidence" and (
                value["control_id"] != owner_id
                or owner_id != controls["context"]["control_id"]):
            raise C0B4CheckpointError("run lineage context evidence is misowned")
        elif kind == "cancellation_health_evidence" and (
                value["cancel_control_id"] != owner_id
                or owner_id != controls["cancellation"]["control_id"]):
            raise C0B4CheckpointError("run lineage cancellation evidence is misowned")

    columns = ("attempt_id", "owner_id", "call_class", "invocation_ordinal",
               "request_sha256", "state", "payload_json", "created", "updated")
    attempt_rows = [dict(zip(columns, row, strict=True)) for row in conn.execute(
        "SELECT attempt_id,owner_id,call_class,invocation_ordinal,request_sha256,"
        "state,payload_json,created,updated FROM attempts ORDER BY rowid")]
    attempts = {row["attempt_id"]: row for row in attempt_rows}
    invocations = {row[0] for row in conn.execute("SELECT ordinal FROM invocations")}
    if (len(attempts) != len(attempt_rows) or len(attempts) > CUMULATIVE_CAP
            or sum(row["state"] == "DISPATCHING" for row in attempt_rows) > 1
            or invocations != set(range(1, len(invocations) + 1))
            or len(invocations) > INVOCATION_CAPS["total"]):
        raise C0B4CheckpointError("run lineage attempt census changed")
    for call_class, allowance in LEDGER_LIMITS.items():
        if sum(row["call_class"] == call_class for row in attempt_rows) > allowance:
            raise C0B4CheckpointError("run lineage call-class allowance changed")
    base_catalog = _base_attempt_catalog(conn)
    prior_by_owner: dict[str, list[Mapping[str, Any]]] = {}
    for attempt in attempt_rows:
        owner = attempt["owner_id"]
        prior = prior_by_owner.setdefault(owner, [])
        expected_id, expected_request, expected_class = _expected_attempt(
            header, base_catalog, owner_id=owner,
            invocation_ordinal=attempt["invocation_ordinal"], prior=prior)
        payload = attempt["payload_json"]
        history = conn.execute(
            "SELECT state,payload_json FROM attempt_history "
            "WHERE attempt_id=? ORDER BY seq", (attempt["attempt_id"],)).fetchall()
        if (type(attempt["attempt_id"]) is not str
                or attempt["attempt_id"] != expected_id
                or not re.fullmatch(r"[0-9a-f]{64}", attempt["attempt_id"])
                or attempt["call_class"] != expected_class
                or attempt["invocation_ordinal"] not in invocations
                or attempt["state"] not in ATTEMPT_OUTCOMES | {"DISPATCHING"}
                or type(attempt["request_sha256"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", attempt["request_sha256"])
                or attempt["request_sha256"] != expected_request
                or payload is not None and canonical_json(json.loads(payload)) != payload
                or not history or history[0] != ("DISPATCHING", None)
                or history[-1] != (attempt["state"], payload)
                or len(history) not in (1, 2)):
            raise C0B4CheckpointError("run lineage attempt/history changed")
        for _state, history_payload in history:
                if history_payload is not None and canonical_json(
                        json.loads(history_payload)) != history_payload:
                    raise C0B4CheckpointError("run lineage attempt history is noncanonical")
        prior.append(attempt)
    health_id = controls["health"]["control_id"]
    retry_partitions: dict[str, int] = {}
    for attempt in attempt_rows:
        if attempt["call_class"] != "schema_retry":
            continue
        partition = (work_by_id[attempt["owner_id"]][0]
                     if attempt["owner_id"] in work_by_id
                     else "health" if attempt["owner_id"] == health_id else None)
        if partition is None:
            raise C0B4CheckpointError("schema retry has an ineligible owner")
        retry_partitions[partition] = retry_partitions.get(partition, 0) + 1
    if any(count > 1 for count in retry_partitions.values()):
        raise C0B4CheckpointError("schema retry partition allowance changed")
    for ordinal in invocations:
        invocation_rows = [row for row in attempt_rows
                           if row["invocation_ordinal"] == ordinal]
        expected = list(_preflight_attempt_catalog(header, ordinal))
        observed = [row["owner_id"] for row in invocation_rows
                    if row["owner_id"] in expected]
        if (observed != expected[:len(observed)]
                or any(row["owner_id"] not in expected for row in invocation_rows)
                and (len(invocation_rows) < 3
                     or [row["owner_id"] for row in invocation_rows[:3]] != expected
                     or any(row["state"] != "RAW_VALID"
                            for row in invocation_rows[:3]))):
            raise C0B4CheckpointError("invocation preflight barrier changed")
    if conn.execute(
            "SELECT count(*) FROM attempt_history h LEFT JOIN attempts a "
            "ON a.attempt_id=h.attempt_id WHERE a.attempt_id IS NULL").fetchone() != (0,):
        raise C0B4CheckpointError("run lineage contains orphan attempt history")
    seen_events: set[tuple[str, str]] = set()
    event_state = {
        "RAW_VALID": {"RAW_VALID"},
        "NORMALIZED_DUPLICATE": {"NORMALIZED_DUPLICATE"},
        "INVALID": {"SCHEMA_INVALID", "INVALID"},
        "ORPHANED": {"RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN"},
        "CANCELLED": {"CANCELLED"},
    }
    for row_kind, raw in conn.execute(
            "SELECT kind,detail_json FROM events ORDER BY seq"):
        try:
            event = validate_artifact(json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise C0B4CheckpointError("run lineage runtime event is invalid") from exc
        attempt = attempts.get(event["source_attempt_id"])
        work = work_by_id.get(attempt["owner_id"]) if attempt else None
        semantic_key = event["event"], event["source_attempt_id"]
        if (event.get("version") != "c0b4-runtime-event-v1"
                or canonical_json(event) != raw or row_kind != event["event"]
                or semantic_key in seen_events
                or any(event.get(key) != header[key] for key in (
                    "policy_id", "policy_sha256", "protocol_sha256"))
                or attempt is None or work is None
                or event["lane_id"] != work[0]
                or event["request_sha256"] != attempt["request_sha256"]
                or event["request_sha256"] != work[1]["request_sha256"]
                or event["nonce"] != work[1]["nonce"]
                or (event["event"] != "DISPATCHING"
                    and attempt["state"] not in event_state[event["event"]])):
            raise C0B4CheckpointError("run lineage runtime event is misbound")
        seen_events.add(semantic_key)
    if require_event_completeness:
        terminal_event = {
            "RAW_VALID": "RAW_VALID", "NORMALIZED_DUPLICATE": "NORMALIZED_DUPLICATE",
            "SCHEMA_INVALID": "INVALID", "INVALID": "INVALID",
            "RETRYABLE_TRANSPORT": "ORPHANED", "ORPHANED_UNKNOWN": "ORPHANED",
            "CANCELLED": "CANCELLED",
        }
        for attempt in attempt_rows:
            if attempt["owner_id"] not in work_by_id:
                continue
            expected = {("DISPATCHING", attempt["attempt_id"])}
            event = terminal_event.get(attempt["state"])
            if event is not None:
                expected.add((event, attempt["attempt_id"]))
            if not expected.issubset(seen_events):
                raise C0B4CheckpointError("run lineage runtime event history is incomplete")

    for (kind, owner_id), (value, _digest) in artifacts.items():
        if kind == "plan_activation":
            if owner_id not in plans or value["plan_sha256"] != plans[owner_id]["plan_sha256"]:
                raise C0B4CheckpointError("run lineage activation is misowned")
            if owner_id != "F72_17":
                _require_passed_prerequisite(
                    aggregates, LANE_ORDER[LANE_ORDER.index(owner_id) - 1])
            later = list(LANE_ORDER[LANE_ORDER.index(owner_id) + 1:])
            active = sorted(row["work_id"] for row in plans[owner_id]["payload"]["work"])
            inactive = sorted(row["work_id"] for lane_id in later
                              for row in plans[lane_id]["payload"]["work"])
            prerequisite = (master_hash if owner_id == "F72_17" else
                            aggregates[LANE_ORDER[LANE_ORDER.index(owner_id) - 1]][1])
            if (value["activated_work_ids"] != active
                    or value["inactive_work_ids"] != inactive
                    or value["prerequisite_sha256"] != prerequisite):
                raise C0B4CheckpointError("run lineage activation facts changed")
        elif kind == "cursor_transition":
            if owner_id not in LANE_ORDER[:2] or value["from_lane_id"] != owner_id:
                raise C0B4CheckpointError("run lineage cursor is misowned")
            next_lane = LANE_ORDER[LANE_ORDER.index(owner_id) + 1]
            _require_passed_prerequisite(aggregates, owner_id)
            census = sha256_json({
                "lane_id": owner_id,
                "completed_work_ids": sorted(
                    row["work_id"] for row in plans[owner_id]["payload"]["work"]),
            })
            if (value["to_lane_id"] != next_lane
                    or value["from_aggregate_sha256"] != aggregates[owner_id][1]
                    or value["to_plan_sha256"] != plans[next_lane]["plan_sha256"]
                    or value["completed_work_census_sha256"] != census):
                raise C0B4CheckpointError("run lineage cursor facts changed")

    for lane_id, (_aggregate, _digest) in aggregates.items():
        if ("plan_activation", lane_id) not in artifacts:
            raise C0B4CheckpointError("run lineage aggregate lacks its activation")
        lane_index = LANE_ORDER.index(lane_id)
        if lane_index and ("cursor_transition", LANE_ORDER[lane_index - 1]) not in artifacts:
            raise C0B4CheckpointError("run lineage reached lane lacks its cursor")
    context = artifacts.get(("context_evidence", controls["context"]["control_id"]))
    cancel = artifacts.get((
        "cancellation_health_evidence", controls["cancellation"]["control_id"]))
    if context is not None:
        value = context[0]
        trigger = attempts.get(value["trigger_attempt_id"])
        trigger_work = work_by_id.get(value["trigger_work_id"])
        context_attempts = [row for row in attempts.values()
                            if row["owner_id"] == value["control_id"]]
        context_responses = []
        for row in context_attempts:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            context_responses.append((payload.get("metadata") or {}).get(
                "response_sha256"))
        if (not trigger or not trigger_work
                or trigger["owner_id"] != value["trigger_work_id"]
                or trigger["request_sha256"] != value["trigger_request_sha256"]
                or trigger_work[0] != "F72_17"
                or trigger_work[1]["nonce"] != value["trigger_nonce"]
                or value["response_sha256"] not in context_responses):
            raise C0B4CheckpointError("run lineage context evidence attempt changed")
    if cancel is not None:
        value = cancel[0]
        cancel_attempt = attempts.get(value["cancel_attempt_id"])
        health_attempts = [attempts.get(item) for item in value["health_attempt_ids"]]
        all_health_attempts = [
            row for row in attempts.values()
            if row["owner_id"] == value["health_control_id"]]
        if (not cancel_attempt
                or cancel_attempt["owner_id"] != value["cancel_control_id"]
                or cancel_attempt["request_sha256"] !=
                   controls["cancellation"]["request_sha256"]
                or cancel_attempt["state"] != "CANCELLED_UNVERIFIED"
                or value["health_control_id"] != controls["health"]["control_id"]
                or value["health_work_id"] != controls["health"]["health_work_id"]
                or any(not item or item["owner_id"] != value["health_control_id"]
                       or item["request_sha256"] != controls["health"]["request_sha256"]
                       for item in health_attempts)):
            raise C0B4CheckpointError("run lineage cancellation evidence attempts changed")
        _validate_cancel_health_timing(
            cancel_attempt, all_health_attempts, value)
    f17 = aggregates.get("F72_17")
    if f17 is not None and (
            f17[0]["context_evidence_sha256"] != (context[1] if context else None)
            or f17[0]["cancellation_health_evidence_sha256"] !=
               (cancel[1] if cancel else None)):
        raise C0B4CheckpointError("run lineage lane control evidence changed")

    acceptance = artifacts.get(("acceptance_aggregate", "complete"))
    if acceptance is not None:
        value = acceptance[0]
        if (value["acceptance_plan_sha256"] != plans["C44_1"]["plan_sha256"]
                or value["component_hashes"]["c44_rerun_aggregate_sha256"] !=
                   aggregates["C44_1"][1]
                or value["component_hashes"]["f72_seed17_aggregate_sha256"] !=
                   aggregates["F72_17"][1]
                or value["component_hashes"]["d50_confirmation_aggregate_sha256"] !=
                   _parent_d50_sha256(conn, header, corpus)):
            raise C0B4CheckpointError("run lineage acceptance inputs changed")
    result = artifacts.get(("result", "terminal"))
    completion = artifacts.get(("completion", "terminal"))
    failure = artifacts.get(("failure", "terminal"))
    if result is not None:
        validate_quality_terminal_ownership(conn, result[0])
        if (completion is None or completion[0]["artifact_sha256"] != result[1]
                or completion[0]["outcome"] != result[0]["terminal"]):
            raise C0B4CheckpointError("run lineage quality completion changed")
    elif completion is not None:
        raise C0B4CheckpointError("run lineage completion lacks a result")
    if failure is not None:
        validate_failure_terminal_ownership(conn, failure[0])
    failure_evidence = artifacts.get(("failure_evidence", "terminal"))
    if failure_evidence is not None and failure_evidence[0]["control_id"] is not None:
        control_ids = {value["control_id"] for value in controls.values()}
        if (failure_evidence[0]["control_id"] not in control_ids
                or failure_evidence[0]["lane_id"] != "F72_17"):
            raise C0B4CheckpointError("run lineage failure control is misowned")
    state = conn.execute("SELECT state FROM run_state WHERE id=1").fetchone()
    if not state or state[0] not in ALL_STATES:
        raise C0B4CheckpointError("run lineage state is invalid")
    state = state[0]
    terminal_keys = {key for key in artifacts if key[0] in {
        "result", "completion", "failure", "failure_evidence"}}
    expected = ({("failure_evidence", "terminal")}
                if allow_pending_failure_evidence and state not in TERMINAL_STATES else
                set() if state not in TERMINAL_STATES else
                {("result", "terminal"), ("completion", "terminal")}
                if state in {"CONFIRMED", "INCONCLUSIVE"} else
                {("failure", "terminal"), ("failure_evidence", "terminal")})
    terminal_value = result[0] if result is not None else failure[0] if failure else None
    if (terminal_keys != expected
            or terminal_value is not None and terminal_value["terminal"] != state):
        raise C0B4CheckpointError("run state and terminal artifacts differ")
    if require_terminal_receipt:
        receipts = int(conn.execute("SELECT count(*) FROM backup_receipts").fetchone()[0])
        if receipts != int(state in TERMINAL_STATES):
            raise C0B4CheckpointError("run terminal backup receipt census changed")


def validate_quality_terminal_ownership(
        conn: sqlite3.Connection, artifact: Mapping[str, Any]) -> None:
    """Verify exact stored owners and the result's deterministic stop semantics."""
    hashes = artifact["lane_aggregate_sha256s"]
    references = [
        ("master_plan", "master", artifact["master_plan_sha256"], "master"),
        ("lane_aggregate", "F72_17", hashes["f72_seed17_sha256"], "f17"),
        ("lane_aggregate", "F72_20260804",
         hashes["f72_seed20260804_sha256"], "f_later"),
        ("c44_aggregate", "C44_1", hashes["c44_scored_sha256"], "c44"),
        ("acceptance_aggregate", "complete",
         artifact["acceptance_aggregate_sha256"], "acceptance"),
    ]
    owned: dict[str, dict[str, Any]] = {}
    for kind, owner_id, digest, label in references:
        if digest is None:
            continue
        row = conn.execute(
            "SELECT json,sha256 FROM artifacts "
            "WHERE kind=? AND owner_id=? AND sha256=?",
            (kind, owner_id, digest)).fetchone()
        if not row:
            raise C0B4CheckpointError(
                "quality result references absent or misowned evidence")
        try:
            value = _validate_stored_artifact(kind, json.loads(row[0]))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise C0B4CheckpointError(
                "quality result references invalid evidence") from exc
        if canonical_json(value) != row[0] or sha256_json(value) != row[1]:
            raise C0B4CheckpointError("quality result references changed evidence")
        owned[label] = value
    reason = artifact["reason"]
    f17 = owned["f17"]
    if reason == "seed17_no_qualifier":
        valid = (f17["passed"] is False
                 and f17["cancellation_health_evidence_sha256"] is None
                 and "cancellation_health_failure" not in f17["failure_reasons"])
    elif reason == "seed17_control_gate_failed":
        valid = (f17["passed"] is False
                 and f17["cancellation_health_evidence_sha256"] is not None
                 and f17["failure_reasons"] == ["cancellation_health_failure"])
    elif reason == "seed20260804_no_qualifier":
        valid = f17["passed"] is True and owned["f_later"]["passed"] is False
    else:
        later, c44, acceptance = owned["f_later"], owned["c44"], owned["acceptance"]
        linked = (
            acceptance["component_hashes"]["f72_seed17_aggregate_sha256"] ==
            hashes["f72_seed17_sha256"]
            and acceptance["component_hashes"]["c44_rerun_aggregate_sha256"] ==
            hashes["c44_scored_sha256"])
        if reason == "complete_corpus_acceptance_failed":
            valid = (f17["passed"] is True and later["passed"] is True
                     and acceptance["passed"] is False and linked)
        else:
            valid = (reason == "complete_public_acceptance_passed"
                     and f17["passed"] is True and later["passed"] is True
                     and c44["component_passed"] is True
                     and acceptance["passed"] is True and linked)
    if not valid:
        raise C0B4CheckpointError(
            "quality result reason differs from its owned aggregate facts")


def validate_failure_terminal_ownership(
        conn: sqlite3.Connection, artifact: Mapping[str, Any]) -> None:
    """Bind a failure result to exact durable evidence and charged work."""
    row = conn.execute(
        "SELECT json,sha256 FROM artifacts WHERE kind='failure_evidence' "
        "AND owner_id='terminal' AND sha256=?",
        (artifact["evidence_sha256"],)).fetchone()
    if not row:
        raise C0B4CheckpointError("failure result lacks its exact evidence")
    evidence = _validate_stored_artifact("failure_evidence", json.loads(row[0]))
    charged = int(conn.execute("SELECT count(*) FROM attempts").fetchone()[0])
    if (canonical_json(evidence) != row[0] or sha256_json(evidence) != row[1]
            or evidence["terminal"] != artifact["terminal"]
            or evidence["reason"] != artifact["reason"]
            or evidence["charged_call_total"] != charged
            or artifact["charged_call_total"] != charged):
        raise C0B4CheckpointError("failure result/evidence/ledger facts differ")
    lane_id, plan_hash = evidence["lane_id"], evidence["plan_sha256"]
    if (lane_id is None) != (plan_hash is None):
        raise C0B4CheckpointError("failure lane and plan ownership differ")
    lane = None
    if lane_id is not None:
        lane_row = conn.execute(
            "SELECT json,sha256 FROM artifacts WHERE kind='lane_plan' AND owner_id=?",
            (lane_id,)).fetchone()
        if not lane_row or lane_row[1] != plan_hash:
            raise C0B4CheckpointError("failure evidence references an absent lane plan")
        lane = _validate_stored_artifact("lane_plan", json.loads(lane_row[0]))
    attempt_id = evidence["attempt_id"]
    if attempt_id is not None:
        attempt = conn.execute(
            "SELECT owner_id,state FROM attempts WHERE attempt_id=?",
            (attempt_id,)).fetchone()
        if (not attempt or attempt[1] != artifact["terminal"]
                or artifact["terminal"] not in {"FAILED_SAFETY", "BLOCKED_PROVENANCE"}):
            raise C0B4CheckpointError("failure evidence names no coherent attempt")
        control_id = evidence["control_id"]
        if control_id is not None and attempt[0] != control_id:
            raise C0B4CheckpointError("failure control differs from attempt owner")
        if control_id is None and lane is not None and attempt[0] not in {
                work["work_id"] for work in lane["work"]}:
            raise C0B4CheckpointError("failure attempt differs from its lane")
    elif evidence["control_id"] is not None:
        raise C0B4CheckpointError("failure control lacks a durable attempt")
    if artifact["terminal"] == "BLOCKED_BUDGET":
        invocations = int(conn.execute("SELECT count(*) FROM invocations").fetchone()[0])
        exhausted = charged >= CUMULATIVE_CAP or invocations >= INVOCATION_CAPS["total"]
        exhausted = exhausted or any(
            int(conn.execute(
                "SELECT count(*) FROM attempts WHERE call_class=?", (call_class,)
            ).fetchone()[0]) >= allowance
            for call_class, allowance in LEDGER_LIMITS.items())
        if not exhausted:
            raise C0B4CheckpointError("budget terminal lacks exhausted allowance")


def _hash_fd(fd: int) -> str:
    digest, offset = hashlib.sha256(), 0
    while True:
        block = os.pread(fd, 1 << 20, offset)
        if not block:
            return digest.hexdigest()
        digest.update(block)
        offset += len(block)


def _open_owner_file(path: Path) -> int:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_CLOEXEC", 0))
    st = os.fstat(fd)
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600):
        os.close(fd)
        raise PermissionError("pinned file must be owner-only regular mode 0600")
    return fd


def _assert_named(path: Path, fd: int) -> None:
    named = os.stat(path, follow_symlinks=False)
    pinned = os.fstat(fd)
    if (named.st_dev, named.st_ino) != (pinned.st_dev, pinned.st_ino):
        raise C0B4CheckpointError("pinned file path changed during verification")


def _verify_sqlite_fd(fd: int) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:/proc/self/fd/{fd}?mode=ro&immutable=1",
                           uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        conn.close()
        raise C0B4CheckpointError("SQLite integrity check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        conn.close()
        raise C0B4CheckpointError("SQLite foreign-key check failed")
    return conn


def _classify_staged_checkpoint(db_fd: int, header: Mapping[str, Any]) -> str:
    """Return PREPARED/INITIALIZING only after an exact descriptor-pinned audit."""
    try:
        staged = _verify_sqlite_fd(db_fd)
        try:
            header_rows = staged.execute(
                "SELECT json,sha256 FROM run_header ORDER BY id").fetchall()
            state_rows = staged.execute(
                "SELECT state FROM run_state ORDER BY id").fetchall()
            artifacts = staged.execute(
                "SELECT kind,owner_id,sha256,json FROM artifacts "
                "ORDER BY kind,owner_id").fetchall()
            protected = staged.execute(
                "SELECT name,value,sha256 FROM protected_values ORDER BY name").fetchall()
            limits = dict(staged.execute(
                "SELECT call_class,allowance FROM class_limits").fetchall())
            activity = sum(int(staged.execute(
                f"SELECT count(*) FROM {table}").fetchone()[0]) for table in (
                    "invocations", "attempts", "attempt_history", "events",
                    "backup_receipts"))
        finally:
            staged.close()
    except (C0B4CheckpointError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise C0B4CheckpointError(
            "staged C0B-4 checkpoint cannot be safely classified; HI review required"
        ) from exc
    expected_header = canonical_json(dict(header)), sha256_json(dict(header))
    if header_rows != [expected_header] or limits != LEDGER_LIMITS:
        raise C0B4CheckpointError(
            "staged C0B-4 identity or ledger changed; HI review required")
    if state_rows == [("INITIALIZING",)]:
        if artifacts or protected or activity:
            raise C0B4CheckpointError(
                "partially frozen C0B-4 staging data requires HI review")
        return "INITIALIZING"
    if state_rows != [("PREPARED",)]:
        raise C0B4CheckpointError("unexpected staged C0B-4 state requires HI review")
    expected_owners = {
        ("master_plan", "master"),
        ("lane_plan", "F72_17"),
        ("lane_plan", "F72_20260804"),
        ("lane_plan", "C44_1"),
    }
    if {(row[0], row[1]) for row in artifacts} != expected_owners or activity:
        raise C0B4CheckpointError(
            "prepared C0B-4 artifact census changed; HI review required")
    for kind, owner_id, digest, raw in artifacts:
        try:
            value = json.loads(raw)
            normalized = _validate_stored_artifact(kind, value)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise C0B4CheckpointError(
                "prepared C0B-4 artifact is invalid; HI review required") from exc
        if (canonical_json(normalized) != raw or sha256_json(normalized) != digest
                or (kind == "lane_plan" and normalized["lane_id"] != owner_id)):
            raise C0B4CheckpointError(
                "prepared C0B-4 artifact changed or is misowned; HI review required")
    if (len(protected) != 1 or protected[0][0] != "nonce_key"
            or not isinstance(protected[0][1], bytes) or len(protected[0][1]) != 32
            or hashlib.sha256(protected[0][1]).hexdigest() != protected[0][2]):
        raise C0B4CheckpointError(
            "prepared C0B-4 nonce changed; HI review required")
    staged = _verify_sqlite_fd(db_fd)
    try:
        validate_run_lineage(staged, header)
    finally:
        staged.close()
    return "PREPARED"


def _cleanup_stale_initializing(runs: Path, run_id: str,
                                header: Mapping[str, Any]) -> bool:
    """Promote one exact complete staging run; prune only exact incomplete debris."""
    prefix = f".c0b4-initializing-{run_id}-"
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    runs_fd = os.open(runs, flags)
    try:
        for name in sorted(os.listdir(runs_fd)):
            suffix = name.removeprefix(prefix)
            if (not name.startswith(prefix) or len(suffix) != 32
                    or any(char not in "0123456789abcdef" for char in suffix)):
                continue
            stage_fd = os.open(name, flags, dir_fd=runs_fd)
            try:
                stage_stat = os.fstat(stage_fd)
                names = set(os.listdir(stage_fd))
                if (stage_stat.st_uid != os.getuid()
                        or stat.S_IMODE(stage_stat.st_mode) != 0o700
                        or names - {"checkpoint.sqlite3", "checkpoint.sqlite3-journal"}):
                    raise PermissionError("unsafe C0B-4 staging debris requires HI review")
                for child in sorted(names):
                    child_fd = os.open(child, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                                       dir_fd=stage_fd)
                    try:
                        child_stat = os.fstat(child_fd)
                        if (not stat.S_ISREG(child_stat.st_mode)
                                or child_stat.st_uid != os.getuid()
                                or stat.S_IMODE(child_stat.st_mode) != 0o600):
                            raise PermissionError("unsafe C0B-4 staging file requires HI review")
                    finally:
                        os.close(child_fd)
                if "checkpoint.sqlite3" in names:
                    db_fd = os.open("checkpoint.sqlite3", os.O_RDONLY
                                    | getattr(os, "O_NOFOLLOW", 0), dir_fd=stage_fd)
                    try:
                        state = _classify_staged_checkpoint(db_fd, header)
                    finally:
                        os.close(db_fd)
                    if state == "PREPARED":
                        if names != {"checkpoint.sqlite3"}:
                            raise C0B4CheckpointError(
                                "prepared C0B-4 staging journal requires HI review")
                        os.fsync(stage_fd)
                        _rename_noreplace(runs_fd, name, run_id)
                        os.fsync(runs_fd)
                        return True
                else:
                    raise C0B4CheckpointError(
                        "unclassifiable C0B-4 staging debris requires HI review")
                for child in sorted(names):
                    os.unlink(child, dir_fd=stage_fd)
                os.fsync(stage_fd)
            finally:
                os.close(stage_fd)
            os.rmdir(name, dir_fd=runs_fd)
            os.fsync(runs_fd)
    finally:
        os.close(runs_fd)
    return False


def validate_header(header: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(header)
    if frozenset(value) != HEADER_KEYS:
        raise C0B4CheckpointError("C0B-4 run header has an inexact key set")
    expected = {
        "version": HEADER_VERSION, "run_type": "public_confirmation",
        "benchmark_protocol_id": PROTOCOL_ID, "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256, "filesystem_selected_mode": "DELETE",
        "journal_mode": "DELETE", "schema_version": SCHEMA_VERSION,
        "cumulative_cap": CUMULATIVE_CAP, "limits": LEDGER_LIMITS,
        "invocation_caps": INVOCATION_CAPS,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise C0B4CheckpointError("C0B-4 run header identity or ledger is not frozen")
    require_current_header(value)
    if value.get("parent_binding") != FROZEN_PARENT_BINDING:
        raise C0B4CheckpointError("C0B-4 parent binding is not the frozen C0B-3 parent")
    if (type(value["schema_version"]) is not int
            or type(value["cumulative_cap"]) is not int
            or any(type(item) is not int for item in value["limits"].values())
            or type(value["invocation_caps"]["total"]) is not int):
        raise C0B4CheckpointError("C0B-4 numeric header fields do not allow coercion")
    if not RUN_ID_RE.fullmatch(str(value.get("run_id", ""))):
        raise C0B4CheckpointError("invalid C0B-4 run id")
    if (value["ollama_endpoint"] != "http://127.0.0.1:11434"
            or value["ollama_version"] != "0.32.5"
            or value["model_digests"] != {
                "qwen3.6:27b":
                "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e"}):
        raise C0B4CheckpointError("C0B-4 Ollama/model identity is not frozen")
    FrozenMount.model_validate(value["mount"], strict=True)
    SourceBinding.model_validate({
        key: value[key] for key in SourceBinding.model_fields
    }, strict=True)
    canonical_json(value)
    return value


def _default_parent_verifier(db_path: Path, binding: Mapping[str, Any]) -> None:
    from .c0b2_runtime import public_verify
    root = db_path.parents[2]
    result = public_verify(str(binding["run_id"]), benchmark_root=root)
    if not result.get("ok"):
        raise C0B4CheckpointError(f"full C0B-3 parent verification failed: {result.get('errors')}")


def verify_parent_readonly(
    db_path: Path, snapshot_path: Path, binding: Mapping[str, Any],
    *, verifier: Callable[[Path, Mapping[str, Any]], None] | None = None,
) -> None:
    """Fully verify and descriptor-pin both immutable parent files."""
    if dict(binding) != FROZEN_PARENT_BINDING:
        raise C0B4CheckpointError("unexpected parent binding")
    db_path, snapshot_path = Path(db_path), Path(snapshot_path)
    db_fd = snapshot_fd = -1
    try:
        db_fd, snapshot_fd = _open_owner_file(db_path), _open_owner_file(snapshot_path)
        before = (_hash_fd(db_fd), _hash_fd(snapshot_fd))
        if before != (binding["checkpoint_sha256"], binding["backup_snapshot_sha256"]):
            raise C0B4CheckpointError("parent database or snapshot digest changed")
        (verifier or _default_parent_verifier)(db_path, binding)
        conn = _verify_sqlite_fd(db_fd)
        try:
            row = conn.execute("SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
            if not row or row[1] != binding["run_header_sha256"]:
                raise C0B4CheckpointError("parent run-header pin changed")
            stored = json.loads(row[0])
            if (sha256_json(stored) != row[1] or stored.get("run_id") != binding["run_id"]
                    or stored.get("git_head") != binding["source_commit"]
                    or stored.get("benchmark_protocol_id") != binding["benchmark_protocol_id"]
                    or stored.get("protocol_sha256") != binding["protocol_sha256"]
                    or stored.get("policy_id") != binding["policy_id"]
                    or stored.get("policy_sha256") != binding["policy_sha256"]):
                raise C0B4CheckpointError("parent run-header lineage changed")
        finally:
            conn.close()
        snapshot = _verify_sqlite_fd(snapshot_fd)
        snapshot.close()
        _assert_named(db_path, db_fd)
        _assert_named(snapshot_path, snapshot_fd)
        if (_hash_fd(db_fd), _hash_fd(snapshot_fd)) != before:
            raise C0B4CheckpointError("parent files changed during verification")
    finally:
        if snapshot_fd >= 0:
            os.close(snapshot_fd)
        if db_fd >= 0:
            os.close(db_fd)


_SCHEMA = """
CREATE TABLE run_header(id INTEGER PRIMARY KEY CHECK(id=1), json TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE run_state(id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE parent_files(id INTEGER PRIMARY KEY CHECK(id=1), db_path TEXT NOT NULL, snapshot_path TEXT NOT NULL);
CREATE TABLE class_limits(call_class TEXT PRIMARY KEY, allowance INTEGER NOT NULL CHECK(allowance>=0));
CREATE TABLE invocations(ordinal INTEGER PRIMARY KEY, created REAL NOT NULL);
CREATE TABLE attempts(attempt_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, call_class TEXT NOT NULL REFERENCES class_limits(call_class), invocation_ordinal INTEGER REFERENCES invocations(ordinal), request_sha256 TEXT NOT NULL, state TEXT NOT NULL, payload_json TEXT, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE attempt_history(seq INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id), state TEXT NOT NULL, payload_json TEXT, created REAL NOT NULL);
CREATE TABLE artifacts(kind TEXT NOT NULL, owner_id TEXT NOT NULL, sha256 TEXT NOT NULL, json TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(kind,owner_id));
CREATE TABLE protected_values(name TEXT PRIMARY KEY, value BLOB NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, detail_json TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE backup_receipts(anchor_sha256 TEXT PRIMARY KEY, anchor_json TEXT NOT NULL, receipt_sha256 TEXT NOT NULL UNIQUE, receipt_json TEXT NOT NULL, created REAL NOT NULL);
"""


class C0B4Checkpoint:
    def __init__(self, path: Path, root: Path, conn: sqlite3.Connection):
        self.path, self.root, self.conn = Path(path), Path(root), conn
        self._identity = os.stat(self.path, follow_symlinks=False)

    @classmethod
    def create(cls, root: Path, run_id: str, *, header: Mapping[str, Any],
               parent_checkpoint: Path, parent_snapshot: Path,
               parent_verifier: Callable[[Path, Mapping[str, Any]], None] | None = None,
               initializer: Callable[["C0B4Checkpoint"], None] | None = None,
               pre_promotion_hook: Callable[["C0B4Checkpoint"], None] | None = None,
               ) -> "C0B4Checkpoint":
        frozen = validate_header(header)
        if frozen["run_id"] != run_id:
            raise C0B4CheckpointError("requested run id differs from header")
        # This must happen before even root.mkdir(): failure has zero child side effects.
        verify_parent_readonly(parent_checkpoint, parent_snapshot,
                               frozen["parent_binding"], verifier=parent_verifier)
        root = _secure_dir(Path(root), create=True)
        runs = _secure_dir(root / "runs", create=True)
        if _cleanup_stale_initializing(runs, run_id, frozen):
            return cls.open(runs / run_id / "checkpoint.sqlite3", root)
        staging_name = f".c0b4-initializing-{run_id}-{secrets.token_hex(16)}"
        staging, final = runs / staging_name, runs / run_id
        staging.mkdir(mode=0o700)
        db_path = staging / "checkpoint.sqlite3"
        conn: sqlite3.Connection | None = None
        published = False
        try:
            fd = os.open(db_path, os.O_CREAT | os.O_EXCL | os.O_RDWR
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
            conn = sqlite3.connect(db_path, isolation_level=None, timeout=5.0)
            cls._configure(conn)
            conn.executescript(_SCHEMA)
            now = time.time()
            conn.execute("INSERT INTO run_header VALUES(1,?,?)",
                         (canonical_json(frozen), sha256_json(frozen)))
            conn.execute("INSERT INTO run_state VALUES(1,'INITIALIZING',?)", (now,))
            conn.execute("INSERT INTO parent_files VALUES(1,?,?)",
                         (str(Path(parent_checkpoint).resolve()),
                          str(Path(parent_snapshot).resolve())))
            conn.executemany("INSERT INTO class_limits VALUES(?,?)", LEDGER_LIMITS.items())
            point = cls(db_path, root, conn)
            if initializer is None:
                raise C0B4CheckpointError("C0B-4 creation requires a frozen-work initializer")
            conn.execute("BEGIN IMMEDIATE")
            try:
                initializer(point)
                point._validate_initialized_work()
                conn.execute("UPDATE run_state SET state='PREPARED',updated=? WHERE id=1",
                             (time.time(),))
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            conn.execute("PRAGMA optimize")
            os.chmod(db_path, 0o600)
            cls._fsync(db_path)
            if pre_promotion_hook is not None:
                pre_promotion_hook(point)
            # SQLite's DELETE journal uses the pathname opened by the connection.
            # Close before renaming so later writes never target the vanished staging path.
            conn.close()
            conn = None
            runs_fd = os.open(runs, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                _rename_noreplace(runs_fd, staging_name, run_id)
                os.fsync(runs_fd)
                published = True
            finally:
                os.close(runs_fd)
            conn = sqlite3.connect(final / "checkpoint.sqlite3",
                                   isolation_level=None, timeout=5.0)
            cls._configure(conn)
            point.conn = conn
            point.path = final / "checkpoint.sqlite3"
            point._assert_parent_unchanged()
            return point
        except Exception:
            if conn is not None:
                conn.close()
            target = final if published else staging
            try:
                (target / "checkpoint.sqlite3").unlink()
                target.rmdir()
            except OSError:
                pass
            raise

    @staticmethod
    def _configure(conn: sqlite3.Connection) -> None:
        mode = str(conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).upper()
        if mode != "DELETE":
            raise C0B4CheckpointError(f"DELETE journal mode unavailable: {mode}")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA mmap_size=0")
        conn.execute("PRAGMA busy_timeout=5000")

    @staticmethod
    def _fsync(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent)
        finally:
            os.close(parent)

    @classmethod
    def open(cls, path: Path, root: Path) -> "C0B4Checkpoint":
        path, root = Path(path), _owner_directory(Path(root))
        runs, run_dir = _owner_directory(root / "runs"), _owner_directory(path.parent)
        if (path.name != "checkpoint.sqlite3" or run_dir.parent != runs
                or path.parent != run_dir):
            raise PermissionError("C0B-4 checkpoint is outside its benchmark root")
        fd = _open_owner_file(path)
        try:
            readonly = _verify_sqlite_fd(fd)
            try:
                row = readonly.execute("SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
                if not row:
                    raise C0B4CheckpointError("missing C0B-4 run header")
                header = validate_header(json.loads(row[0]))
                if canonical_json(header) != row[0] or sha256_json(header) != row[1]:
                    raise C0B4CheckpointError("C0B-4 run header changed")
                if header["run_id"] != path.parent.name:
                    raise C0B4CheckpointError("C0B-4 storage/run identity mismatch")
                validate_run_lineage(
                    readonly, header, require_event_completeness=False)
            finally:
                readonly.close()
            _assert_named(path, fd)
        finally:
            os.close(fd)
        conn = sqlite3.connect(path, isolation_level=None, timeout=5.0)
        cls._configure(conn)
        point = cls(path, root, conn)
        point._assert_parent_unchanged()
        return point

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "C0B4Checkpoint":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def header(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
        value = validate_header(json.loads(row[0]))
        if canonical_json(value) != row[0] or sha256_json(value) != row[1]:
            raise C0B4CheckpointError("stored C0B-4 header is noncanonical or changed")
        return value

    def state(self) -> str:
        return str(self.conn.execute("SELECT state FROM run_state WHERE id=1").fetchone()[0])

    def _assert_parent_unchanged(self) -> None:
        db_path, snapshot_path = self.conn.execute(
            "SELECT db_path,snapshot_path FROM parent_files WHERE id=1").fetchone()
        binding = self.header()["parent_binding"]
        for path, expected in ((Path(db_path), binding["checkpoint_sha256"]),
                               (Path(snapshot_path), binding["backup_snapshot_sha256"])):
            fd = _open_owner_file(path)
            try:
                _assert_named(path, fd)
                if _hash_fd(fd) != expected:
                    raise C0B4CheckpointError("immutable parent evidence changed")
            finally:
                os.close(fd)

    def transition(self, new_state: str) -> None:
        if new_state not in RESUMABLE_STATES:
            raise C0B4CheckpointError("unknown or internal C0B-4 state")
        old = self.state()
        if old in TERMINAL_STATES:
            raise C0B4CheckpointError("terminal C0B-4 state is immutable")
        allowed = ({"RUNNING"} if old == "PREPARED"
                   else (RESUMABLE_STATES - {"PREPARED"}))
        if new_state != old and new_state not in allowed:
            raise C0B4CheckpointError(f"illegal state transition {old} -> {new_state}")
        self._assert_parent_unchanged()
        self.conn.execute("UPDATE run_state SET state=?,updated=? WHERE id=1",
                          (new_state, time.time()))

    def claim_invocation(self) -> int:
        self._assert_parent_unchanged()
        if self.state() != "RUNNING":
            raise C0B4CheckpointError("invocation claim requires RUNNING")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            ordinal = int(self.conn.execute(
                "SELECT coalesce(max(ordinal),0)+1 FROM invocations").fetchone()[0])
            if ordinal > INVOCATION_CAPS["total"]:
                raise C0B4BudgetError("C0B-4 invocation cap exhausted")
            self.conn.execute("INSERT INTO invocations VALUES(?,?)", (ordinal, time.time()))
            self.conn.commit()
            return ordinal
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

    def precharge(self, *, attempt_id: str, owner_id: str, call_class: str,
                  invocation_ordinal: int, request_sha256: str) -> None:
        self._assert_parent_unchanged()
        if self.state() != "RUNNING" or call_class not in LEDGER_LIMITS:
            raise C0B4CheckpointError("invalid precharge state or call class")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            if self.conn.execute(
                    "SELECT 1 FROM attempts WHERE state='DISPATCHING' LIMIT 1").fetchone():
                raise C0B4CheckpointError("only one C0B-4 request may be in flight")
            if not self.conn.execute(
                    "SELECT 1 FROM invocations WHERE ordinal=?",
                    (invocation_ordinal,)).fetchone():
                raise C0B4CheckpointError("attempt names an unknown invocation")
            _validate_new_attempt_identity(
                self.conn, self.header(), attempt_id=attempt_id, owner_id=owner_id,
                call_class=call_class, invocation_ordinal=invocation_ordinal,
                request_sha256=request_sha256)
            used = int(self.conn.execute(
                "SELECT count(*) FROM attempts WHERE call_class=?", (call_class,)).fetchone()[0])
            total = int(self.conn.execute("SELECT count(*) FROM attempts").fetchone()[0])
            if used >= LEDGER_LIMITS[call_class] or total >= CUMULATIVE_CAP:
                raise C0B4BudgetError("C0B-4 call ledger exhausted")
            now = time.time()
            self.conn.execute("INSERT INTO attempts VALUES(?,?,?,?,?,'DISPATCHING',NULL,?,?)",
                              (attempt_id, owner_id, call_class, invocation_ordinal,
                               request_sha256, now, now))
            self.conn.execute("INSERT INTO attempt_history VALUES(NULL,?,'DISPATCHING',NULL,?)",
                              (attempt_id, now))
            self.conn.commit()
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

    def _record_attempt_with_payload(
            self, attempt_id: str, state: str,
            payload_at: Callable[[float], Mapping[str, Any] | None],
    ) -> tuple[float, Mapping[str, Any] | None]:
        self._assert_parent_unchanged()
        if state not in ATTEMPT_OUTCOMES:
            raise C0B4CheckpointError("unknown C0B-4 attempt outcome")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            now = time.time()
            payload = payload_at(now)
            raw = canonical_json(dict(payload)) if payload is not None else None
            changed = self.conn.execute(
                "UPDATE attempts SET state=?,payload_json=?,updated=? "
                "WHERE attempt_id=? AND state='DISPATCHING'",
                (state, raw, now, attempt_id)).rowcount
            if changed != 1:
                raise C0B4CheckpointError("unknown or already-final C0B-4 attempt")
            self.conn.execute("INSERT INTO attempt_history VALUES(NULL,?,?,?,?)",
                              (attempt_id, state, raw, now))
            self.conn.commit()
            return now, payload
        except Exception:
            self.conn.rollback()
            raise

    def record_attempt(self, attempt_id: str, state: str,
                       payload: Mapping[str, Any] | None = None) -> float:
        """Atomically finish an attempt and return its durable updated timestamp."""
        updated, _payload = self._record_attempt_with_payload(
            attempt_id, state, lambda _updated: payload)
        return updated

    def record_cancelled_attempt(
            self, attempt_id: str, *, first_byte_seen: bool,
            cancel_elapsed_ms: int) -> str:
        """Persist cancellation and its exact two-second health deadline together."""
        if (type(first_byte_seen) is not bool or type(cancel_elapsed_ms) is not int
                or cancel_elapsed_ms < 0):
            raise C0B4CheckpointError("cancellation timing values are invalid")

        def payload_at(updated: float) -> Mapping[str, Any]:
            return {
                "answered": False, "first_byte_seen": first_byte_seen,
                "cancel_elapsed_ms": cancel_elapsed_ms,
                "health_not_before_utc": _utc_timestamp(updated + 2.0),
            }

        _updated, payload = self._record_attempt_with_payload(
            attempt_id, "CANCELLED_UNVERIFIED", payload_at)
        return str(payload["health_not_before_utc"])

    def store_artifact(self, kind: str, owner_id: str, value: Mapping[str, Any]) -> str:
        self._assert_parent_unchanged()
        normalized = _validate_stored_artifact(kind, value)
        raw, digest = canonical_json(normalized), sha256_json(normalized)
        self.conn.execute("INSERT INTO artifacts VALUES(?,?,?,?,?)",
                          (kind, owner_id, digest, raw, time.time()))
        return digest

    def read_artifact(self, kind: str, owner_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT json,sha256 FROM artifacts WHERE kind=? AND owner_id=?",
            (kind, owner_id)).fetchone()
        if not row:
            return None
        value = json.loads(row[0])
        if canonical_json(value) != row[0] or sha256_json(value) != row[1]:
            raise C0B4CheckpointError("requested C0B-4 artifact changed")
        return _validate_stored_artifact(kind, value)

    def read_nonce_key(self) -> bytes:
        row = self.conn.execute(
            "SELECT value,sha256 FROM protected_values WHERE name='nonce_key'").fetchone()
        if (not row or not isinstance(row[0], bytes) or len(row[0]) != 32
                or hashlib.sha256(row[0]).hexdigest() != row[1]):
            raise C0B4CheckpointError("protected nonce key changed")
        return bytes(row[0])

    def list_attempts(self, attempt_id: str | None = None) -> list[dict[str, Any]]:
        columns = ("attempt_id", "owner_id", "call_class", "invocation_ordinal",
                   "request_sha256", "state", "payload_json", "created", "updated")
        sql = ("SELECT attempt_id,owner_id,call_class,invocation_ordinal,request_sha256,"
               "state,payload_json,created,updated FROM attempts")
        rows = self.conn.execute(
            sql + (" WHERE attempt_id=?" if attempt_id is not None else "")
            + " ORDER BY created,attempt_id",
            (attempt_id,) if attempt_id is not None else ()).fetchall()
        values = [dict(zip(columns, row, strict=True)) for row in rows]
        for value in values:
            raw = value.pop("payload_json")
            payload = json.loads(raw) if raw is not None else None
            if raw is not None and canonical_json(payload) != raw:
                raise C0B4CheckpointError("attempt payload is noncanonical")
            value["payload"] = payload
        return values

    def list_runtime_events(self) -> list[dict[str, Any]]:
        """Return canonical, schema-validated runtime events in durable order."""
        values = []
        header = self.header()
        for kind, raw in self.conn.execute(
                "SELECT kind,detail_json FROM events ORDER BY seq"):
            try:
                value = validate_artifact(json.loads(raw))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise C0B4CheckpointError("runtime event is invalid") from exc
            if (value.get("version") != "c0b4-runtime-event-v1"
                    or value["event"] != kind or canonical_json(value) != raw
                    or any(value.get(key) != header[key] for key in (
                        "policy_id", "policy_sha256", "protocol_sha256"))):
                raise C0B4CheckpointError("runtime event changed")
            values.append(value)
        return values

    def recover_dispatching(self) -> list[dict[str, Any]]:
        """Durably classify crash-left in-flight work without spending a new call."""
        self._assert_parent_unchanged()
        rows = self.conn.execute(
            "SELECT attempt_id FROM attempts WHERE state='DISPATCHING' ORDER BY attempt_id").fetchall()
        now = time.time()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            for (attempt_id,) in rows:
                self.conn.execute(
                    "UPDATE attempts SET state='ORPHANED_UNKNOWN',updated=? WHERE attempt_id=?",
                    (now, attempt_id))
                self.conn.execute(
                    "INSERT INTO attempt_history VALUES(NULL,?,'ORPHANED_UNKNOWN',NULL,?)",
                    (attempt_id, now))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return [self.list_attempts(str(row[0]))[0] for row in rows]

    def store_runtime_event(self, value: Mapping[str, Any]) -> str:
        event = validate_artifact(value)
        if event.get("version") != "c0b4-runtime-event-v1":
            raise C0B4CheckpointError("runtime event has the wrong artifact family")
        digest = event["event_sha256"]
        self._assert_parent_unchanged()
        self.conn.execute("INSERT INTO events(kind,detail_json,created) VALUES(?,?,?)",
                          (event["event"], canonical_json(event), time.time()))
        return digest

    def set_nonce_key(self, value: bytes) -> str:
        """Store the single protected nonce key while the run is INITIALIZING."""
        if self.state() != "INITIALIZING" or type(value) is not bytes or len(value) != 32:
            raise C0B4CheckpointError("nonce key must be 32 bytes set during INITIALIZING")
        digest = hashlib.sha256(value).hexdigest()
        self.conn.execute("INSERT INTO protected_values VALUES('nonce_key',?,?)",
                          (sqlite3.Binary(value), digest))
        return digest

    def _validate_initialized_work(self) -> None:
        """Refuse publication until all immutable work and nonce evidence exists."""
        if self.state() != "INITIALIZING":
            raise C0B4CheckpointError("initialization validation requires INITIALIZING")
        owners = set(self.conn.execute(
            "SELECT kind,owner_id FROM artifacts").fetchall())
        if owners != {
                ("master_plan", "master"),
                ("lane_plan", "F72_17"),
                ("lane_plan", "F72_20260804"),
                ("lane_plan", "C44_1")}:
            raise C0B4CheckpointError("initializer must freeze one master and three lane plans")
        nonce = self.conn.execute(
            "SELECT value,sha256 FROM protected_values WHERE name='nonce_key'").fetchone()
        if (not nonce or not isinstance(nonce[0], bytes) or len(nonce[0]) != 32
                or hashlib.sha256(nonce[0]).hexdigest() != nonce[1]):
            raise C0B4CheckpointError("initializer did not freeze the protected nonce key")
        validate_run_lineage(self.conn, self.header())

    def finalize(self, terminal: str, terminal_artifact: Mapping[str, Any], *,
                 completion: Mapping[str, Any] | None = None) -> tuple[str, str | None]:
        """Atomically persist exact terminal ownership and its state."""
        if terminal not in TERMINAL_STATES or self.state() not in RESUMABLE_STATES:
            raise C0B4CheckpointError("invalid C0B-4 terminal transition")
        quality = terminal in {"CONFIRMED", "INCONCLUSIVE"}
        if quality != (completion is not None):
            raise C0B4CheckpointError("quality completion ownership differs from terminal")
        artifact = validate_artifact(terminal_artifact)
        header = self.header()
        if any(artifact.get(key) != header[key] for key in (
                "policy_id", "policy_sha256", "protocol_sha256")):
            raise C0B4CheckpointError("terminal artifact differs from run lineage")
        if artifact.get("terminal") != terminal:
            raise C0B4CheckpointError("terminal artifact state differs from requested state")
        artifact_raw, artifact_hash = canonical_json(artifact), sha256_json(artifact)
        normalized_completion = validate_artifact(completion) if completion is not None else None
        if normalized_completion is not None and (
                normalized_completion.get("outcome") != terminal
                or normalized_completion.get("artifact_sha256") != artifact_hash
                or any(normalized_completion.get(key) != header[key] for key in (
                    "policy_id", "policy_sha256", "protocol_sha256"))):
            raise C0B4CheckpointError("completion does not own the terminal artifact")
        if normalized_completion is not None:
            facts = normalized_completion["facts"]
            expected_facts = ({"confirmed": True} if terminal == "CONFIRMED" else
                              {"deterministic_stop": True,
                               "reason": artifact["reason"]})
            if facts != expected_facts:
                raise C0B4CheckpointError("completion facts differ from owned result")
        completion_raw = (canonical_json(normalized_completion)
                          if normalized_completion is not None else None)
        completion_hash = (sha256_json(normalized_completion)
                           if normalized_completion is not None else None)
        validate_run_lineage(
            self.conn, header,
            allow_pending_failure_evidence=not quality)
        if quality:
            validate_quality_terminal_ownership(self.conn, artifact)
        else:
            validate_failure_terminal_ownership(self.conn, artifact)
        self._assert_parent_unchanged()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            if self.conn.execute(
                    "SELECT 1 FROM attempts WHERE state='DISPATCHING' LIMIT 1").fetchone():
                raise C0B4CheckpointError(
                    "terminal transition cannot own an in-flight attempt")
            self.conn.execute("INSERT INTO artifacts VALUES(?,?,?,?,?)",
                              ("result" if quality else "failure", "terminal",
                               artifact_hash, artifact_raw, time.time()))
            if completion is not None:
                self.conn.execute("INSERT INTO artifacts VALUES(?,?,?,?,?)",
                                  ("completion", "terminal", completion_hash,
                                   completion_raw, time.time()))
            self.conn.execute("UPDATE run_state SET state=?,updated=? WHERE id=1",
                              (terminal, time.time()))
            self.conn.commit()
            return artifact_hash, completion_hash
        except Exception:
            self.conn.rollback()
            raise


def status_readonly(path: Path, *, require_terminal_receipt: bool = False
                    ) -> dict[str, Any]:
    fd = _open_owner_file(Path(path))
    try:
        conn = _verify_sqlite_fd(fd)
        try:
            raw, digest = conn.execute("SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
            header = validate_header(json.loads(raw))
            if canonical_json(header) != raw or sha256_json(header) != digest:
                raise C0B4CheckpointError("stored C0B-4 header changed")
            state = conn.execute("SELECT state FROM run_state WHERE id=1").fetchone()[0]
            if state not in ALL_STATES or state == "INITIALIZING":
                raise C0B4CheckpointError("published C0B-4 state is invalid")
            limits = dict(conn.execute("SELECT call_class,allowance FROM class_limits"))
            if limits != LEDGER_LIMITS:
                raise C0B4CheckpointError("C0B-4 class ledger changed")
            calls = conn.execute("SELECT count(*) FROM attempts").fetchone()[0]
            invocations = conn.execute("SELECT count(*) FROM invocations").fetchone()[0]
            if calls > CUMULATIVE_CAP or invocations > INVOCATION_CAPS["total"]:
                raise C0B4CheckpointError("C0B-4 cumulative ledger exceeds its cap")
            for call_class, allowance in LEDGER_LIMITS.items():
                used = conn.execute(
                    "SELECT count(*) FROM attempts WHERE call_class=?",
                    (call_class,)).fetchone()[0]
                if used > allowance:
                    raise C0B4CheckpointError("C0B-4 class ledger exceeds its allowance")
            for artifact_raw, artifact_sha in conn.execute(
                    "SELECT json,sha256 FROM artifacts"):
                artifact = json.loads(artifact_raw)
                if (canonical_json(artifact) != artifact_raw
                        or hashlib.sha256(artifact_raw.encode()).hexdigest() != artifact_sha):
                    raise C0B4CheckpointError("stored C0B-4 artifact changed")
            for kind, artifact_raw in conn.execute("SELECT kind,json FROM artifacts"):
                _validate_stored_artifact(kind, json.loads(artifact_raw))
            plan_owners = set(conn.execute(
                "SELECT kind,owner_id FROM artifacts "
                "WHERE kind IN ('master_plan','lane_plan')").fetchall())
            if plan_owners != {
                    ("master_plan", "master"),
                    ("lane_plan", "F72_17"),
                    ("lane_plan", "F72_20260804"),
                    ("lane_plan", "C44_1")}:
                raise C0B4CheckpointError("frozen plan census changed")
            nonce = conn.execute(
                "SELECT value,sha256 FROM protected_values WHERE name='nonce_key'").fetchone()
            if (not nonce or not isinstance(nonce[0], bytes) or len(nonce[0]) != 32
                    or hashlib.sha256(nonce[0]).hexdigest() != nonce[1]):
                raise C0B4CheckpointError("protected nonce key changed")
            if conn.execute("SELECT count(*) FROM protected_values").fetchone() != (1,):
                raise C0B4CheckpointError("unexpected protected values exist")
            validate_run_lineage(
                conn, header,
                require_terminal_receipt=require_terminal_receipt)
            dispatching = conn.execute(
                "SELECT count(*) FROM attempts WHERE state='DISPATCHING'").fetchone()[0]
            if dispatching > 1:
                raise C0B4CheckpointError("more than one request is in flight")
            if state in TERMINAL_STATES and dispatching:
                raise C0B4CheckpointError("terminal C0B-4 state has an in-flight attempt")
            for attempt_id, attempt_state, attempt_payload in conn.execute(
                    "SELECT attempt_id,state,payload_json FROM attempts"):
                if attempt_state not in ATTEMPT_OUTCOMES | {"DISPATCHING"}:
                    raise C0B4CheckpointError("attempt has an unknown outcome")
                if attempt_payload is not None and canonical_json(
                        json.loads(attempt_payload)) != attempt_payload:
                    raise C0B4CheckpointError("attempt payload is noncanonical")
                history = conn.execute(
                    "SELECT state,payload_json FROM attempt_history "
                    "WHERE attempt_id=? ORDER BY seq",
                    (attempt_id,)).fetchall()
                if (not history or history[0] != ("DISPATCHING", None)
                        or history[-1] != (attempt_state, attempt_payload)
                        or len(history) not in (1, 2)):
                    raise C0B4CheckpointError("attempt history is incomplete or reordered")
                for _history_state, history_payload in history:
                    if history_payload is not None and canonical_json(
                            json.loads(history_payload)) != history_payload:
                        raise C0B4CheckpointError("attempt history payload is noncanonical")
            db_path, snapshot_path = conn.execute(
                "SELECT db_path,snapshot_path FROM parent_files WHERE id=1").fetchone()
            for parent_path, expected in (
                    (Path(db_path), header["parent_binding"]["checkpoint_sha256"]),
                    (Path(snapshot_path), header["parent_binding"]["backup_snapshot_sha256"])):
                parent_fd = _open_owner_file(parent_path)
                try:
                    _assert_named(parent_path, parent_fd)
                    if _hash_fd(parent_fd) != expected:
                        raise C0B4CheckpointError("immutable parent evidence changed")
                finally:
                    os.close(parent_fd)
            _assert_named(Path(path), fd)
            return {"run_id": header["run_id"], "state": state,
                    "calls_total": calls, "invocations": invocations,
                    "header_sha256": digest}
        finally:
            conn.close()
    finally:
        os.close(fd)


def verify_readonly(path: Path) -> dict[str, Any]:
    try:
        return {"ok": True, "errors": [], **status_readonly(
            path, require_terminal_receipt=True)}
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError,
            C0B4CheckpointError) as exc:
        return {"ok": False, "errors": [type(exc).__name__]}
