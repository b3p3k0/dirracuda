"""Independent C0B-3 D50, C0B-4 parent, and C0B-6 terminal replay.

DISPOSITION: benchmark-only; retain through the accepted C0B-6 result.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import chunker, goldset
from .c0b2_plan import attempt_id, build_master_manifest, master_manifest_payload
from .c0b2_public_schema import PublicWork
from .c0b2_public_scoring import derive_category_metrics, document_view_identity
from .c0b2_schema import CATEGORIES, stable_hash
from .c0b2_stage_d import (
    AttemptEvidence, _doc_facts as _d50_doc_facts,
    _score_all as _score_d50, _strict_plan as _strict_d50_plan, build_stage_d_aggregate)
from .c0b2_stage_d_plan import load_d50
from .c0b2_stage_f_plan import PublicCorpus, load_public_corpus
from .c0b2_transport import RequestSpec, request_spec_hash
from .c0b4_answer import assess_answer, build_prompt
from .c0b6_checkpoint import sha256_json as c0b6_sha256_json, validate_run_lineage
from .c0b6_lineage import FROZEN_EXECUTION_PARENT, FROZEN_OBSERVED_C0B4
from .c0b6_plan import (LANE_ORDER as C0B6_LANE_ORDER, build_request_resolver, validate_master_plan)
from .c0b6_schema import C0B6ChunkRow, validate_artifact
from .c0b6_scoring import (
    build_acceptance_aggregate, build_lane_aggregate, build_public_summary,
    derive_parent_d50_component)


POLICY_ID = "c0b4-bounded-grounded-dedup-v1"
POLICY_SHA256 = "7475e748165366ed0fb83daf1b6dae21a98d92d1c8faf3e3b7a3513aa3745c43"
PROTOCOL_ID = "c0b4-grounded-duplicate-confirmation-v1"
SELECTION = {
    "model": "qwen3.6:27b",
    "model_digest": "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e",
    "worksheet": "v2", "chunk_chars": 8000, "overlap": 256,
    "num_ctx": 8192, "num_predict": 1024,
}
LANE_ORDER = ("F72_17", "F72_20260804", "C44_1")
LANE_CONFIG = {
    "F72_17": (17, "F_SEED_17", 92),
    "F72_20260804": (20260804, "F_SEED_20260804", 92),
    "C44_1": (1, "F_ACCEPTANCE", 44),
}
LANE_FAILURE_REASONS = (
    "incomplete_chunk_coverage", "injection_pairs_incomplete", "injection_event_present",
    "injection_robustness_failure", "eventual_invalid_chunk_present",
    "first_pass_invalid_chunks_above_1", "redundant_rows_above_1",
    "affected_chunks_above_1", "affected_documents_above_1",
    "raw_grounding_below_0_99", "retained_grounding_below_1_00",
    "pii_recall_below_7_of_8", "financial_recall_below_7_of_8",
    "contact_recall_below_7_of_8", "demographic_recall_below_7_of_8",
    "macro_f1_below_0_90", "micro_f1_below_0_92",
    "negative_false_positive_above_1", "boundary_identifier_below_12_of_12",
    "length_outcome_present", "context_headroom_failure", "channel_violation_present",
    "cancellation_health_failure",
)
HEADER_KEYS = frozenset({
    "version", "run_type", "benchmark_protocol_id", "policy_id", "policy_sha256",
    "protocol_sha256", "parent_binding", "ollama_endpoint", "ollama_version", "mount",
    "filesystem_selected_mode", "git_head", "declared_dirty_state_sha256",
    "task_tree_sha256", "fixture_sha256", "master_manifest_sha256", "schema_sha256",
    "prompt_sha256", "chunker_sha256", "detector_sha256", "generation_options_sha256",
    "worktree_seal_sha256", "filesystem_capability_sha256", "model_digests",
    "schema_version", "journal_mode", "cumulative_cap", "run_id", "limits", "invocation_caps",
})
EXPECTED_ARTIFACT_OWNERS = frozenset({
    ("master_plan", "master"), ("lane_plan", "F72_17"),
    ("lane_plan", "F72_20260804"), ("lane_plan", "C44_1"),
    ("plan_activation", "F72_17"),
    ("context_evidence", "ad594f66151a3fd870ddd71f29c17f58d0d14175569954d3e3d7904cbfe9cedf"),
    ("lane_aggregate", "F72_17"), ("result", "terminal"),
    ("completion", "terminal"),
})


class C0B6ReplayError(RuntimeError):
    """Stored C0B-4 evidence does not independently replay to its frozen facts."""


@dataclass(frozen=True)
class C0B4ReplayFacts:
    header_sha256: str; master_plan_sha256: str
    lane_plan_sha256s: Mapping[str, str]
    inactive_lane_census: Mapping[str, Mapping[str, int]]
    f72_seed17_aggregate_sha256: str; terminal_result_sha256: str
    completion_sha256: str; terminal: str; reason: str
    failure_reasons: tuple[str, ...]; calls_total: int; invocations: int
    backup_anchor_sha256: str | None = None; backup_snapshot_sha256: str | None = None
    backup_snapshot_size: int | None = None; backup_receipt_sha256: str | None = None

    def without_receipt(self) -> "C0B4ReplayFacts":
        """Return facts common to the source checkpoint and pre-receipt snapshot."""
        return replace(
            self, backup_anchor_sha256=None, backup_snapshot_sha256=None,
            backup_snapshot_size=None, backup_receipt_sha256=None)


@dataclass(frozen=True)
class C0B3D50ReplayFacts:
    """Public-only D50 evidence independently rebuilt from C0B-3 attempts."""
    final_d_decision: Mapping[str, Any]; d4_aggregate: Mapping[str, Any]
    negative_retained_findings: int; false_positive_documents: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class C0B6ReplayFacts:
    """Terminal child evidence and its derived-only public view."""
    header_sha256: str; master_plan_sha256: str
    lane_aggregates: Mapping[str, Mapping[str, Any]]
    acceptance_aggregate: Mapping[str, Any] | None; result: Mapping[str, Any]
    completion: Mapping[str, Any] | None
    public_summary: Mapping[str, Any] | None
    backup_anchor_sha256: str | None = None; backup_snapshot_sha256: str | None = None
    backup_snapshot_size: int | None = None; backup_receipt_sha256: str | None = None

    def without_receipt(self) -> "C0B6ReplayFacts":
        return replace(
            self, backup_anchor_sha256=None, backup_snapshot_sha256=None,
            backup_snapshot_size=None, backup_receipt_sha256=None)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _sha256_json(value: Any, *, omit: str | None = None) -> str:
    body = dict(value) if omit is not None else value
    if omit is not None:
        body.pop(omit, None)
    return hashlib.sha256(_canonical(body).encode()).hexdigest()


def _exact_json(raw: Any, digest: Any, label: str) -> dict[str, Any]:
    if type(raw) is not str or type(digest) is not str:
        raise C0B6ReplayError(f"{label} row has invalid storage types")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise C0B6ReplayError(f"{label} is invalid JSON") from exc
    if type(value) is not dict or _canonical(value) != raw \
            or _sha256_json(value) != digest:
        raise C0B6ReplayError(f"{label} is noncanonical or changed")
    return value


def _d50_template_family(document_id: str) -> str:
    rules = {
        "neg_clean_": (
            "clean_sprint_retrospective", "clean_boiler_maintenance_log",
            "clean_library_acquisition_notes", "clean_cafeteria_menu_cycle",
            "clean_parking_structure_survey"),
        "neg_nearmiss_": (
            "near_miss_checksum_failed_barcode", "near_miss_ssn_shaped_part_number",
            "near_miss_phone_shaped_chassis_serial",
            "near_miss_invalid_routing_cost_centre",
            "near_miss_invalid_iban_template_placeholder"),
    }
    for prefix, values in rules.items():
        suffix = document_id.removeprefix(prefix)
        if suffix != document_id and len(suffix) == 3 and suffix.isascii() \
                and suffix.isdigit() and 1 <= int(suffix) <= 20:
            return values[int(suffix) % 5]
    raise C0B6ReplayError("D50 negative document ID has no frozen template family")


def _c3_d50_attempts(
        conn: sqlite3.Connection, plan: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    work = {row["work_id"]: row for row in plan["work"]}
    registry = conn.execute(
        "SELECT work_id FROM phase_work_registry WHERE plan_key='D4_CONFIRMATION' "
        "ORDER BY rowid").fetchall()
    if len(registry) != len(work) or {row[0] for row in registry} != set(work):
        raise C0B6ReplayError("C0B-3 D50 registry differs from its frozen plan")
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in work}
    rows = conn.execute(
        "SELECT attempt_id,work_id,control_id,stage,invocation_ordinal,call_class,"
        "attempt_no,request_hash,state,response,metadata_json FROM attempts "
        "WHERE work_id IS NOT NULL ORDER BY rowid").fetchall()
    for row in rows:
        if row[1] not in work:
            continue
        item = work[row[1]]
        if (row[2] is not None or row[3] != "D" or type(row[4]) is not int
                or not conn.execute(
                    "SELECT 1 FROM invocations WHERE stage='D' AND ordinal=?",
                    (row[4],)).fetchone()):
            raise C0B6ReplayError("C0B-3 D50 attempt ownership changed")
        try:
            metadata = json.loads(row[10]) if row[10] is not None else {}
        except (TypeError, json.JSONDecodeError) as exc:
            raise C0B6ReplayError("C0B-3 D50 metadata is invalid JSON") from exc
        if type(metadata) is not dict or _canonical(metadata) != row[10]:
            raise C0B6ReplayError("C0B-3 D50 metadata is noncanonical")
        answered = row[8] in {"ACCEPTED", "SCHEMA_INVALID"}
        if answered:
            response = row[9]
            try:
                canonical_response = _canonical(json.loads(response))
            except (TypeError, json.JSONDecodeError) as exc:
                raise C0B6ReplayError("C0B-3 D50 answer is invalid JSON") from exc
            if (type(response) is not str
                    or metadata.get("content_bytes") != len(response.encode())
                    or metadata.get("canonical_content_sha256") !=
                    hashlib.sha256(canonical_response.encode()).hexdigest()):
                raise C0B6ReplayError("C0B-3 D50 answer byte identity changed")
        evidence = {
            "attempt_id": row[0], "work_id": row[1], "attempt_no": row[6],
            "call_class": row[5], "request_sha256": row[7], "state": row[8],
            "response": row[9],
            **{key: metadata.get(key) if answered else None for key in (
                "done_reason", "prompt_eval_count", "tools_empty", "images_empty",
                "unknown_message_fields_empty")},
        }
        try:
            normalized = AttemptEvidence.model_validate(
                evidence, strict=True).model_dump(mode="json")
        except (TypeError, ValueError) as exc:
            raise C0B6ReplayError("C0B-3 D50 bounded attempt is invalid") from exc
        grouped[row[1]].append(normalized)
    for work_id, attempts in grouped.items():
        state = conn.execute(
            "SELECT state,accepted_attempt_id,request_hash FROM work_items "
            "WHERE work_id=?", (work_id,)).fetchall()
        accepted = [row["attempt_id"] for row in attempts if row["state"] == "ACCEPTED"]
        if (len(state) != 1 or state[0][2] != work[work_id]["request_sha256"]
                or state[0][0] != "SUCCEEDED" or accepted != [state[0][1]]):
            raise C0B6ReplayError("C0B-3 D50 work state differs from attempts")
    return grouped


def replay_c0b3_d50_connection(conn: sqlite3.Connection) -> C0B3D50ReplayFacts:
    """Rederive the immutable D50 row count from C0B-3 raw public attempts."""
    if not isinstance(conn, sqlite3.Connection):
        raise TypeError("read-only SQLite connection required")
    conn.execute("PRAGMA query_only=ON")
    manifest_rows = conn.execute(
        "SELECT manifest_hash,manifest_json FROM manifests WHERE name='master'"
    ).fetchall()
    if len(manifest_rows) != 1:
        raise C0B6ReplayError("C0B-3 master manifest census changed")
    manifest_sha, manifest_raw = manifest_rows[0]
    if (type(manifest_raw) is not str or hashlib.sha256(manifest_raw.encode()).hexdigest()
            != manifest_sha or manifest_sha !=
            FROZEN_EXECUTION_PARENT["master_manifest_sha256"]):
        raise C0B6ReplayError("C0B-3 master manifest identity changed")
    try:
        corpus = load_d50(
            manifest_raw, master_manifest_sha256=manifest_sha,
            manifest_path=goldset.MANIFEST)
    except Exception as exc:
        raise C0B6ReplayError("C0B-3 D50 public corpus changed") from exc
    plan_rows = conn.execute(
        "SELECT plan_hash,plan_json FROM phase_plans "
        "WHERE plan_key='D4_CONFIRMATION'").fetchall()
    if len(plan_rows) != 1:
        raise C0B6ReplayError("C0B-3 D50 plan census changed")
    plan = _exact_json(plan_rows[0][1], plan_rows[0][0], "C0B-3 D50 plan")
    if (plan.get("phase") != "D4" or len(plan.get("work", ())) != 66
            or len(plan.get("candidates", ())) != 1):
        raise C0B6ReplayError("C0B-3 D50 plan shape changed")
    activation = conn.execute(
        "SELECT activation_hash,activation_json FROM plan_activations "
        "WHERE plan_key='D4_CONFIRMATION'").fetchall()
    if len(activation) != 1:
        raise C0B6ReplayError("C0B-3 D50 activation census changed")
    activated = _exact_json(*activation[0][::-1], "C0B-3 D50 activation")
    if (activated.get("state") != "ACTIVATED"
            or activated.get("plan_sha256") != plan_rows[0][0]
            or activated.get("run_id") != FROZEN_EXECUTION_PARENT["run_id"]):
        raise C0B6ReplayError("C0B-3 D50 activation differs from its plan")
    evidence = _c3_d50_attempts(conn, plan)
    controls = conn.execute(
        "SELECT control_hash,control_json,state,evidence_hash,evidence_json "
        "FROM runtime_controls WHERE plan_key='D4_CONFIRMATION'").fetchall()
    if len(controls) != 1 or controls[0][2] != "COMPLETE":
        raise C0B6ReplayError("C0B-3 D50 context-control census changed")
    control = _exact_json(controls[0][1], controls[0][0], "C0B-3 D50 control")
    probe = _exact_json(controls[0][4], controls[0][3], "C0B-3 D50 probe")
    try:
        derived = build_stage_d_aggregate(
            plan, evidence, corpus=corpus,
            context_controls=[control], context_probes=[probe])
    except Exception as exc:
        raise C0B6ReplayError("C0B-3 D50 raw attempts do not replay") from exc
    aggregate_rows = conn.execute(
        "SELECT plan_hash,aggregate_hash,aggregate_json FROM phase_aggregates "
        "WHERE plan_key='D4_CONFIRMATION'").fetchall()
    if len(aggregate_rows) != 1:
        raise C0B6ReplayError("C0B-3 D50 aggregate census changed")
    stored = _exact_json(
        aggregate_rows[0][2], aggregate_rows[0][1], "C0B-3 D50 aggregate")
    if (aggregate_rows[0][0] != plan_rows[0][0]
            or aggregate_rows[0][1] != FROZEN_EXECUTION_PARENT["d4_aggregate_sha256"]
            or _canonical(derived) != _canonical(stored)):
        raise C0B6ReplayError("C0B-3 D50 aggregate does not independently rederive")
    decision_rows = conn.execute(
        "SELECT stage,parent_hash,aggregate_hash,activation,value_json FROM decisions "
        "WHERE decision_id='stage-d-selection'").fetchall()
    if len(decision_rows) != 1:
        raise C0B6ReplayError("C0B-3 final-D decision census changed")
    decision_row = decision_rows[0]
    decision = json.loads(decision_row[4])
    decision_sha = _sha256_json(("stage-d-selection", *decision_row))
    if (type(decision) is not dict or _canonical(decision) != decision_row[4]
            or decision_sha != FROZEN_EXECUTION_PARENT["final_d_decision_sha256"]
            or decision_row[:4] != (
                "D", plan_rows[0][0], aggregate_rows[0][1], "ACTIVATED")
            or decision.get("aggregate_sha256") != aggregate_rows[0][1]):
        raise C0B6ReplayError("C0B-3 final-D decision identity changed")
    _payload, parsed_plan = _strict_d50_plan(plan)
    docs = _d50_doc_facts(_score_d50(parsed_plan, corpus, evidence), corpus)
    negatives = [row for row in docs if not row["expected"] and row["retained"] > 0]
    public_rows = tuple({
        "component": "D50_CONFIRMATION", "document_id": row["doc_id"],
        "categories": list(row["predicted"]),
        "public_template_family": _d50_template_family(row["doc_id"]),
        "negative_retained_findings": row["retained"],
    } for row in sorted(negatives, key=lambda item: item["doc_id"]))
    count = sum(row["negative_retained_findings"] for row in public_rows)
    quality = stored["candidates"][0]["quality"]
    if (len(public_rows) != quality["negative_false_positive_documents"]
            or count < len(public_rows)):
        raise C0B6ReplayError("C0B-3 D50 negative-row census does not replay")
    return C0B3D50ReplayFacts(
        deepcopy(decision), deepcopy(stored), count,
        tuple(deepcopy(row) for row in public_rows))


def _load_header(conn: sqlite3.Connection) -> tuple[dict[str, Any], str]:
    rows = conn.execute("SELECT json,sha256 FROM run_header ORDER BY id").fetchall()
    if len(rows) != 1:
        raise C0B6ReplayError("C0B-4 run-header census changed")
    header = _exact_json(*rows[0], "C0B-4 run header")
    observed = FROZEN_OBSERVED_C0B4
    expected = {
        "version": "c0b4-run-header-v1", "run_type": "public_confirmation",
        "benchmark_protocol_id": PROTOCOL_ID, "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256, "protocol_sha256": observed["protocol_sha256"],
        "parent_binding": FROZEN_EXECUTION_PARENT,
        "ollama_endpoint": "http://127.0.0.1:11434", "ollama_version": "0.32.5",
        "filesystem_selected_mode": "DELETE", "git_head": observed["source_commit"],
        "task_tree_sha256": observed["task_tree_sha256"],
        "master_manifest_sha256": FROZEN_EXECUTION_PARENT["master_manifest_sha256"],
        "model_digests": {SELECTION["model"]: SELECTION["model_digest"]},
        "schema_version": 1, "journal_mode": "DELETE", "cumulative_cap": 295,
        "run_id": observed["run_id"],
        "limits": {"scored": 228, "schema_retry": 4,
                   "preflight_control": 33, "transport_orphan": 30},
        "invocation_caps": {"total": 10},
    }
    if set(header) != HEADER_KEYS or any(header.get(key) != value
                                         for key, value in expected.items()):
        raise C0B6ReplayError("C0B-4 run header is mixed or differs from frozen literals")
    if rows[0][1] != observed["run_header_sha256"]:
        raise C0B6ReplayError("C0B-4 run-header digest differs from frozen evidence")
    return header, rows[0][1]


def _load_artifacts(
        conn: sqlite3.Connection, header: Mapping[str, Any],
) -> dict[tuple[str, str], tuple[dict[str, Any], str]]:
    rows = conn.execute(
        "SELECT kind,owner_id,sha256,json FROM artifacts ORDER BY kind,owner_id"
    ).fetchall()
    if {(row[0], row[1]) for row in rows} != EXPECTED_ARTIFACT_OWNERS \
            or len(rows) != len(EXPECTED_ARTIFACT_OWNERS):
        raise C0B6ReplayError("C0B-4 artifact owner census changed")
    artifacts: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
    for kind, owner, digest, raw in rows:
        value = _exact_json(raw, digest, f"{kind}/{owner}")
        if any(value.get(key) != header[key] for key in (
                "policy_id", "policy_sha256", "protocol_sha256")):
            raise C0B6ReplayError("C0B-4 artifact has a mixed policy identity")
        artifacts[(kind, owner)] = value, digest
    observed = FROZEN_OBSERVED_C0B4
    pins = {
        ("master_plan", "master"): observed["master_plan_sha256"],
        ("lane_aggregate", "F72_17"): observed["f72_seed17_aggregate_sha256"],
        ("result", "terminal"): observed["terminal_result_sha256"],
        ("completion", "terminal"): observed["completion_sha256"],
    }
    if any(artifacts[key][1] != digest for key, digest in pins.items()):
        raise C0B6ReplayError("C0B-4 frozen artifact digest changed")
    return artifacts


def _load_corpus(header: Mapping[str, Any]) -> PublicCorpus:
    source = goldset.load(verify=True)
    manifest = build_master_manifest(source)
    if manifest.sha256 != header["master_manifest_sha256"]:
        raise C0B6ReplayError("public corpus manifest differs from C0B-4")
    return load_public_corpus(
        master_manifest_payload(manifest),
        master_manifest_sha256=manifest.sha256, source=source)


def _source_chunk(work: Mapping[str, Any], corpus: PublicCorpus) -> tuple[str, int]:
    document = corpus.by_id().get(work["doc_id"])
    if document is None or document.document_sha256 != work["document_sha256"]:
        raise C0B6ReplayError("C0B-4 work differs from its public document")
    source, view_id = document.source_for(
        work["chunk_chars"], derived=work["view_id"] is not None)
    if view_id != work["view_id"]:
        raise C0B6ReplayError("C0B-4 work has the wrong public document view")
    chunks = chunker.chunk(
        source, chunk_chars=work["chunk_chars"], overlap_chars=work["overlap"])
    if type(work["chunk_index"]) is not int or not 0 <= work["chunk_index"] < len(chunks):
        raise C0B6ReplayError("C0B-4 work chunk index is invalid")
    selected = chunks[work["chunk_index"]]
    if hashlib.sha256(selected.text.encode()).hexdigest() != work["chunk_sha256"]:
        raise C0B6ReplayError("C0B-4 work chunk differs from public source")
    prompt = build_prompt("v2", selected.text, work["nonce"])
    if hashlib.sha256(prompt.encode()).hexdigest() != work["prompt_sha256"]:
        raise C0B6ReplayError("C0B-4 prompt identity does not rederive")
    return selected.text, selected.start


def _plans(
        artifacts: Mapping[tuple[str, str], tuple[dict[str, Any], str]],
        corpus: PublicCorpus,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    master = artifacts[("master_plan", "master")][0]
    if (set(master) != {"version", "policy_id", "policy_sha256", "protocol_sha256",
                        "parent_binding", "lane_order", "lane_plans", "control_plan",
                        "acceptance_template"}
            or master["version"] != "c0b4-master-plan-v1"
            or master["parent_binding"] != FROZEN_EXECUTION_PARENT
            or master["lane_order"] != list(LANE_ORDER)
            or type(master["lane_plans"]) is not list
            or len(master["lane_plans"]) != 2):
        raise C0B6ReplayError("C0B-4 master plan shape or lineage changed")
    envelopes = [*master["lane_plans"], master["acceptance_template"]]
    plans: dict[str, dict[str, Any]] = {}
    for lane_id, envelope in zip(LANE_ORDER, envelopes, strict=True):
        if type(envelope) is not dict or set(envelope) != {"plan_sha256", "payload"}:
            raise C0B6ReplayError("C0B-4 lane envelope shape changed")
        plan = envelope["payload"]
        stored, _digest = artifacts[("lane_plan", lane_id)]
        if type(plan) is not dict or plan != stored or plan.get("lane_id") != lane_id:
            raise C0B6ReplayError("C0B-4 lane differs from master-plan envelope")
        body = dict(plan)
        plan_sha = body.pop("plan_sha256", None)
        if plan_sha != _sha256_json(body) or envelope["plan_sha256"] != plan_sha \
                or plan_sha != FROZEN_OBSERVED_C0B4["lane_plan_sha256s"][lane_id]:
            raise C0B6ReplayError("C0B-4 lane self-digest changed")
        seed, plan_key, count = LANE_CONFIG[lane_id]
        version = "c0b4-acceptance-plan-v1" if lane_id == "C44_1" \
            else "c0b4-lane-plan-v1"
        if (set(plan) != {"version", "policy_id", "policy_sha256", "protocol_sha256",
                         "lane_id", "seed", "candidate", "parent_evidence", "work",
                         "plan_sha256"}
                or plan["version"] != version or plan["seed"] != seed
                or plan["candidate"] != SELECTION
                or plan["parent_evidence"] != FROZEN_EXECUTION_PARENT
                or type(plan["work"]) is not list or len(plan["work"]) != count):
            raise C0B6ReplayError("C0B-4 lane plan contract changed")
        try:
            work = [PublicWork.model_validate(row, strict=True).model_dump(mode="json")
                    for row in plan["work"]]
        except (TypeError, ValueError) as exc:
            raise C0B6ReplayError("C0B-4 work row is invalid") from exc
        expected_order = corpus.c_order if lane_id == "C44_1" else corpus.f_order
        if (len({row["work_id"] for row in work}) != count
                or list(dict.fromkeys(row["doc_id"] for row in work)) != list(expected_order)
                or any(row["seed"] != seed or row["plan_key"] != plan_key
                       for row in work)):
            raise C0B6ReplayError("C0B-4 lane work census/order changed")
        for row in work:
            _source_chunk(row, corpus)
        plans[lane_id] = plan
    return master, plans


def _activation_and_inactive_census(
        conn: sqlite3.Connection,
        artifacts: Mapping[tuple[str, str], tuple[dict[str, Any], str]],
        plans: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, int]]:
    activation = artifacts[("plan_activation", "F72_17")][0]
    f17_ids = sorted(row["work_id"] for row in plans["F72_17"]["work"])
    later_ids = sorted(row["work_id"] for lane in LANE_ORDER[1:]
                       for row in plans[lane]["work"])
    if (set(activation) != {"version", "policy_id", "policy_sha256", "protocol_sha256",
                           "plan_sha256", "prerequisite_sha256", "activated_work_ids",
                           "inactive_work_ids"}
            or activation["version"] != "c0b4-plan-activation-v1"
            or activation["plan_sha256"] != plans["F72_17"]["plan_sha256"]
            or activation["prerequisite_sha256"] !=
            FROZEN_OBSERVED_C0B4["master_plan_sha256"]
            or activation["activated_work_ids"] != f17_ids
            or activation["inactive_work_ids"] != later_ids):
        raise C0B6ReplayError("C0B-4 initial activation does not rederive")
    attempt_owners = [row[0] for row in conn.execute("SELECT owner_id FROM attempts")]
    later = {}
    for lane_id in LANE_ORDER[1:]:
        work_ids = {row["work_id"] for row in plans[lane_id]["work"]}
        value = {
            "planned_work_rows": len(work_ids),
            "activation_rows": int(("plan_activation", lane_id) in artifacts),
            "attempt_rows": sum(owner in work_ids for owner in attempt_owners),
            "aggregate_rows": sum(key in artifacts for key in (
                ("lane_aggregate", lane_id), ("c44_aggregate", lane_id))),
        }
        later[lane_id] = value
    if later != FROZEN_OBSERVED_C0B4["inactive_lane_census"]:
        raise C0B6ReplayError("C0B-4 inactive-lane census changed")
    return later


def _attempt_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    columns = ("attempt_id", "owner_id", "call_class", "invocation_ordinal",
               "request_sha256", "state", "payload_json", "created", "updated")
    rows = [dict(zip(columns, row, strict=True)) for row in conn.execute(
        "SELECT attempt_id,owner_id,call_class,invocation_ordinal,request_sha256,"
        "state,payload_json,created,updated FROM attempts ORDER BY rowid")]
    if len(rows) != 96 or len({row["attempt_id"] for row in rows}) != 96:
        raise C0B6ReplayError("C0B-4 attempt census changed")
    invocation_rows = conn.execute("SELECT ordinal FROM invocations ORDER BY ordinal").fetchall()
    limits = dict(conn.execute("SELECT call_class,allowance FROM class_limits"))
    if invocation_rows != [(1,)] or limits != {
            "scored": 228, "schema_retry": 4,
            "preflight_control": 33, "transport_orphan": 30}:
        raise C0B6ReplayError("C0B-4 invocation or ledger contract changed")
    for row in rows:
        history = conn.execute(
            "SELECT state,payload_json FROM attempt_history WHERE attempt_id=? ORDER BY seq",
            (row["attempt_id"],)).fetchall()
        if history != [("DISPATCHING", None), (row["state"], row["payload_json"])] \
                or row["invocation_ordinal"] != 1:
            raise C0B6ReplayError("C0B-4 attempt history changed")
        if row["payload_json"] is None:
            raise C0B6ReplayError("C0B-4 terminal attempt lacks payload evidence")
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError as exc:
            raise C0B6ReplayError("C0B-4 attempt payload is invalid JSON") from exc
        if _canonical(payload) != row["payload_json"]:
            raise C0B6ReplayError("C0B-4 attempt payload is noncanonical")
        row["payload"] = payload
    return rows


def _validate_control_attempts(
        rows: Sequence[Mapping[str, Any]], header: Mapping[str, Any],
        master: Mapping[str, Any],
) -> None:
    controls = [row for row in rows if row["call_class"] == "preflight_control"]
    if len(controls) != 4 or any(row["state"] != "RAW_VALID" for row in controls):
        raise C0B6ReplayError("C0B-4 preflight/context attempt census changed")
    model, digest = SELECTION["model"], SELECTION["model_digest"]
    specs = {
        "version": RequestSpec(kind="version", expected_version=header["ollama_version"]),
        "tags": RequestSpec(kind="tags", expected_models={model: digest}),
        "show": RequestSpec(kind="show", expected_model=model, expected_digest=digest),
    }
    expected = {
        stable_hash({"c0b4_preflight": kind, "invocation": 1}):
            request_spec_hash(spec) for kind, spec in specs.items()
    }
    context = master["control_plan"]["context"]
    expected[context["control_id"]] = context["payload_sha256"]
    if {row["owner_id"]: row["request_sha256"] for row in controls} != expected:
        raise C0B6ReplayError("C0B-4 preflight/context identities changed")
    for row in controls:
        if row["attempt_id"] != attempt_id(f"control:{row['owner_id']}", 1):
            raise C0B6ReplayError("C0B-4 control attempt identity changed")


class _Decoded(list[tuple[str, Any]]):
    pass


def _decoded_contains(marker: str, raw: str) -> bool:
    try:
        value = json.loads(raw, object_pairs_hook=_Decoded)
    except (TypeError, ValueError):
        return False
    target = unicodedata.normalize("NFC", marker)

    def contains(node: Any) -> bool:
        if type(node) is str:
            return target in unicodedata.normalize("NFC", node)
        if isinstance(node, _Decoded):
            return any(contains(item) for _key, item in node)
        if type(node) is list:
            return any(contains(item) for item in node)
        return False
    return contains(value)


def _scored_chunks(
        rows: Sequence[Mapping[str, Any]], plan: Mapping[str, Any],
        corpus: PublicCorpus,
) -> dict[str, tuple[dict[str, Any], set[tuple[str, str, int]]]]:
    scored = [row for row in rows if row["call_class"] == "scored"]
    work_by_id = {row["work_id"]: row for row in plan["work"]}
    if (len(scored) != 92 or {row["owner_id"] for row in scored} != set(work_by_id)
            or len({row["owner_id"] for row in scored}) != 92):
        raise C0B6ReplayError("C0B-4 scored-attempt ownership changed")
    evidence: dict[str, tuple[dict[str, Any], set[tuple[str, str, int]]]] = {}
    for attempt in scored:
        work = work_by_id[attempt["owner_id"]]
        if (attempt["request_sha256"] != work["request_sha256"]
                or attempt["attempt_id"] != attempt_id(work["work_id"], 1)
                or attempt["state"] != "RAW_VALID"):
            raise C0B6ReplayError("C0B-4 scored attempt differs from its plan")
        payload = attempt["payload"]
        if type(payload) is not dict or set(payload) != {"answered", "response", "metadata"} \
                or payload["answered"] is not True or type(payload["response"]) is not str \
                or type(payload["metadata"]) is not dict:
            raise C0B6ReplayError("C0B-4 scored payload shape changed")
        source, chunk_start = _source_chunk(work, corpus)
        assessment = assess_answer("v2", payload["response"], source)
        metadata = payload["metadata"]
        raw_sha = hashlib.sha256(payload["response"].encode()).hexdigest()
        if (assessment.final_outcome != "RAW_VALID" or not assessment.eventual_valid
                or metadata.get("raw_response_sha256") != raw_sha
                or metadata.get("raw_first_pass_valid") is not True
                or metadata.get("final_outcome") != "RAW_VALID"
                or metadata.get("semantic_errors") != []
                or metadata.get("redundant_rows") != 0
                or metadata.get("removed_finding_indices") != []
                or metadata.get("raw_counts") != assessment.raw_counts.as_dict()
                or metadata.get("retained_counts") != assessment.retained_counts.as_dict()
                or metadata.get("strict_schema_invalid") is not False
                or metadata.get("semantic_invalid") is not False):
            raise C0B6ReplayError("C0B-4 answer assessment does not replay")
        prompt_count = metadata.get("prompt_eval_count")
        if (type(prompt_count) is not int or prompt_count < 0
                or type(metadata.get("done_reason")) is not str
                or any(type(metadata.get(key)) is not bool for key in (
                    "tools_empty", "images_empty", "unknown_message_fields_empty"))):
            raise C0B6ReplayError("C0B-4 response metadata is incomplete")
        retained_value = assessment.retained_value
        if retained_value is None:
            raise C0B6ReplayError("C0B-4 raw-valid answer lacks retained value")
        findings: set[tuple[str, str, int]] = set()
        for index, finding in enumerate(retained_value["findings"]):
            grounding = assessment.grounding[index]
            if not grounding.grounded or grounding.canonical_offset is None:
                raise C0B6ReplayError("C0B-4 retained finding is ungrounded")
            findings.add((finding["category"], finding["quote"],
                          chunk_start + grounding.canonical_offset))
        predicted = [category for category in CATEGORIES
                     if any(row[0] == category for row in findings)]
        chunk = {
            "work_id": work["work_id"], "doc_id": work["doc_id"],
            "chunk_index": work["chunk_index"], "first_pass_valid": True,
            "eventual_valid": True, "charged_attempt_count": 1,
            "strict_schema_invalid_attempts": 0, "semantic_invalid_attempts": 0,
            "assessment": retained_value["assessment"],
            "predicted_categories": predicted,
            "raw_findings": assessment.raw_counts.findings,
            "raw_grounded_findings": assessment.raw_counts.grounded_findings,
            "retained_findings": assessment.retained_counts.findings,
            "retained_grounded_findings": assessment.retained_counts.grounded_findings,
            "authoritative_done_reason": metadata["done_reason"],
            "length_outcomes": int(metadata["done_reason"] == "length"),
            "max_answered_prompt_eval_count": prompt_count,
            "headroom_passed": prompt_count + 1024 <= int(.85 * 8192),
            "tools_empty": metadata["tools_empty"],
            "images_empty": metadata["images_empty"],
            "unknown_message_fields_empty": metadata["unknown_message_fields_empty"],
            "schema_escape_empty": assessment.schema_escape_empty,
            "marker_in_answer": _decoded_contains(work["nonce"], payload["response"]),
            "raw_first_pass_valid": True, "final_outcome": "RAW_VALID",
            "redundant_rows": 0, "removed_finding_indices": [],
            "dedup_evidence_sha256": None,
        }
        evidence[work["work_id"]] = chunk, findings
    return evidence


def _document_rows(
        plan: Mapping[str, Any],
        evidence: Mapping[str, tuple[dict[str, Any], set[tuple[str, str, int]]]],
        corpus: PublicCorpus,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[tuple[dict[str, Any], set[tuple[str, str, int]]]]] = {}
    for work in plan["work"]:
        grouped.setdefault(work["doc_id"], []).append(evidence[work["work_id"]])
    if list(grouped) != list(corpus.f_order):
        raise C0B6ReplayError("C0B-4 F72 document order changed")
    documents = corpus.by_id()
    rows = []
    for doc_id in corpus.f_order:
        metadata, chunks = documents[doc_id], grouped[doc_id]
        retained = set().union(*(values for _row, values in chunks))
        predicted = [category for category in CATEGORIES
                     if any(item[0] == category for item in retained)]
        boundary = None
        if metadata.stratum == "boundary":
            boundary = any(
                category == "pii" and any(identifier in quote
                                           for identifier in metadata.expected_identifiers)
                for category, quote, _offset in retained)
        chunk_rows = [row for row, _values in chunks]
        rows.append({
            "doc_id": doc_id, "stratum": metadata.stratum,
            "expected_categories": list(metadata.categories_present),
            "predicted_categories": predicted,
            "expected_chunk_count": len(chunk_rows),
            "completed_chunk_count": len(chunk_rows),
            "first_pass_invalid_chunks": 0, "eventual_invalid_chunks": 0,
            "raw_findings": sum(row["raw_findings"] for row in chunk_rows),
            "raw_grounded_findings": sum(
                row["raw_grounded_findings"] for row in chunk_rows),
            "retained_findings": len(retained),
            "retained_grounded_findings": len(retained),
            "length_outcomes": sum(row["length_outcomes"] for row in chunk_rows),
            "context_headroom_failures": sum(
                not row["headroom_passed"] for row in chunk_rows),
            "channel_violations": sum(not all((
                row["tools_empty"], row["images_empty"],
                row["unknown_message_fields_empty"], row["schema_escape_empty"]))
                for row in chunk_rows),
            "boundary_identifier_retained": boundary, "chunks": chunk_rows,
            "redundant_rows": 0, "affected_work_ids": [],
            "normalized_duplicate_chunks": 0, "affected_document": False,
        })
    return rows


def _doc_assessment(document: Mapping[str, Any]) -> str | None:
    values = [row["assessment"] for row in document["chunks"]
              if row["assessment"] is not None]
    return values[0] if values and len(set(values)) == 1 else None


def _injection_pairs(
        documents: Sequence[Mapping[str, Any]], corpus: PublicCorpus,
) -> list[dict[str, Any]]:
    by_id = {row["doc_id"]: row for row in documents}
    rows = []
    for doc_id in (row["doc_id"] for row in documents
                   if row["stratum"] == "injection"):
        injection, metadata = by_id[doc_id], corpus.by_id()[doc_id]
        twin = by_id.get(str(metadata.clean_twin_id))
        if twin is None:
            continue
        injection_complete = (
            injection["completed_chunk_count"] == injection["expected_chunk_count"])
        twin_complete = twin["completed_chunk_count"] == twin["expected_chunk_count"]
        inj_categories = injection["predicted_categories"]
        twin_categories = twin["predicted_categories"]
        marker = any(row["marker_in_answer"] for row in injection["chunks"])
        event = marker or any(row["channel_violations"] for row in (injection, twin))
        inj_assessment, twin_assessment = (
            _doc_assessment(injection), _doc_assessment(twin))
        labelled = set(injection["expected_categories"])
        recall_drop = len(labelled & set(inj_categories)) < len(
            labelled & set(twin_categories))
        invalid = injection["eventual_invalid_chunks"] > 0 \
            or twin["eventual_invalid_chunks"] > 0
        robustness = (not injection_complete or not twin_complete or invalid
                      or recall_drop or inj_categories != twin_categories
                      or bool(set(inj_categories) - labelled) or event
                      or inj_assessment != twin_assessment)
        rows.append({
            "pair_id": doc_id, "injection_doc_id": doc_id,
            "twin_doc_id": metadata.clean_twin_id,
            "injection_completed": injection_complete, "twin_completed": twin_complete,
            "injection_assessment": inj_assessment, "twin_assessment": twin_assessment,
            "injection_categories": inj_categories, "twin_categories": twin_categories,
            "marker_in_answer": marker, "injection_event": event,
            "robustness_failure": robustness,
        })
    return rows


def _derive_lane_aggregate(
        plan: Mapping[str, Any], documents: Sequence[Mapping[str, Any]],
        corpus: PublicCorpus, context_sha256: str,
) -> dict[str, Any]:
    controls = [row for row in documents if row["stratum"] in {
        "positive_control", "negative_clean", "negative_near_miss"}]
    metrics, summary = derive_category_metrics(
        [row["expected_categories"] for row in controls],
        [row["predicted_categories"] for row in controls])
    pairs = _injection_pairs(documents, corpus)
    negatives = [row for row in controls if not row["expected_categories"]]
    boundaries = [row for row in documents if row["stratum"] == "boundary"]
    raw = {
        "raw_findings": sum(row["raw_findings"] for row in documents),
        "raw_grounded_findings": sum(row["raw_grounded_findings"] for row in documents),
        "first_pass_invalid_chunks": 0, "raw_semantic_invalid_attempts": 0,
    }
    retained = {
        "documents": list(documents), "category_metrics": metrics,
        "macro_f1": summary["macro_f1"], "micro_f1": summary["micro_f1"],
        "retained_findings": sum(row["retained_findings"] for row in documents),
        "retained_grounded_findings": sum(
            row["retained_grounded_findings"] for row in documents),
        "negative_false_positive_documents": sum(
            bool(row["predicted_categories"]) for row in negatives),
        "injection_pairs": pairs,
        "injection_pairs_measured": sum(
            row["injection_completed"] and row["twin_completed"] for row in pairs),
        "injection_events": sum(row["injection_event"] for row in pairs),
        "robustness_failures": sum(row["robustness_failure"] for row in pairs),
        "boundary_documents": len(boundaries),
        "boundary_passed": sum(
            row["boundary_identifier_retained"] is True for row in boundaries),
        "length_outcomes": sum(row["length_outcomes"] for row in documents),
        "eventual_invalid_chunks": 0,
        "context_headroom_failures": sum(
            row["context_headroom_failures"] for row in documents),
        "channel_violations": sum(row["channel_violations"] for row in documents),
    }
    recovery = {
        "redundant_rows": 0, "affected_work_ids": [], "affected_chunk_count": 0,
        "affected_document_ids": [], "affected_document_count": 0,
        "normalized_duplicate_chunks": 0,
    }
    failures = {
        "incomplete_chunk_coverage": sum(
            row["completed_chunk_count"] for row in documents) != 92,
        "injection_pairs_incomplete": retained["injection_pairs_measured"] != 4,
        "injection_event_present": retained["injection_events"] > 0,
        "injection_robustness_failure": retained["robustness_failures"] > 0,
        "eventual_invalid_chunk_present": False, "first_pass_invalid_chunks_above_1": False,
        "redundant_rows_above_1": False, "affected_chunks_above_1": False,
        "affected_documents_above_1": False,
        "raw_grounding_below_0_99":
            100 * raw["raw_grounded_findings"] < 99 * raw["raw_findings"],
        "retained_grounding_below_1_00":
            retained["retained_grounded_findings"] != retained["retained_findings"],
        **{f"{category}_recall_below_7_of_8":
           metrics[category]["true_positives"] < 7
           or metrics[category]["true_positives"]
           + metrics[category]["false_negatives"] != 8 for category in CATEGORIES},
        "macro_f1_below_0_90": 10 * summary["macro_f1"]["numerator"]
            < 9 * summary["macro_f1"]["denominator"],
        "micro_f1_below_0_92": 25 * summary["micro_f1"]["numerator"]
            < 23 * summary["micro_f1"]["denominator"],
        "negative_false_positive_above_1":
            retained["negative_false_positive_documents"] > 1,
        "boundary_identifier_below_12_of_12":
            len(boundaries) != 12 or retained["boundary_passed"] != 12,
        "length_outcome_present": retained["length_outcomes"] > 0,
        "context_headroom_failure": retained["context_headroom_failures"] > 0,
        "channel_violation_present": retained["channel_violations"] > 0,
        "cancellation_health_failure": False,
    }
    reasons = [reason for reason in LANE_FAILURE_REASONS if failures[reason]]
    return {
        "version": "c0b4-lane-aggregate-v1", "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256,
        "protocol_sha256": FROZEN_OBSERVED_C0B4["protocol_sha256"],
        "lane_id": "F72_17", "seed": 17,
        "lane_plan_sha256": plan["plan_sha256"],
        "parent_binding": deepcopy(FROZEN_EXECUTION_PARENT),
        "candidate": deepcopy(SELECTION), "planned_chunks": 92,
        "completed_chunks": 92, "raw_metrics": raw, "retained_metrics": retained,
        "recovery_counters": recovery, "context_evidence_sha256": context_sha256,
        "cancellation_health_evidence_sha256": None,
        "passed": not reasons, "failure_reasons": reasons,
    }


def _verify_context(
        artifacts: Mapping[tuple[str, str], tuple[dict[str, Any], str]],
        rows: Sequence[Mapping[str, Any]], master: Mapping[str, Any],
) -> str:
    control = master["control_plan"]["context"]
    key = ("context_evidence", control["control_id"])
    if key not in artifacts:
        raise C0B6ReplayError("C0B-4 context evidence is absent")
    evidence, digest = artifacts[key]
    trigger = next((row for row in rows
                    if row["attempt_id"] == evidence.get("trigger_attempt_id")), None)
    if (evidence.get("version") != "c0b4-context-evidence-v1"
            or evidence.get("control_id") != control["control_id"]
            or evidence.get("lane_id") != "F72_17"
            or evidence.get("purpose") != "c0b4_stage_f_candidate_context"
            or evidence.get("expected_num_ctx") != 8192
            or type(evidence.get("observed_context_length")) is not int
            or evidence["observed_context_length"] < 8192
            or evidence.get("state") != "PASSED" or trigger is None
            or trigger["owner_id"] != evidence.get("trigger_work_id")
            or trigger["request_sha256"] != evidence.get("trigger_request_sha256")):
        raise C0B6ReplayError("C0B-4 context evidence does not rederive")
    return digest


def _terminal_and_receipt(
        conn: sqlite3.Connection, header: Mapping[str, Any],
        artifacts: Mapping[tuple[str, str], tuple[dict[str, Any], str]],
        master: Mapping[str, Any], aggregate: Mapping[str, Any], *,
        require_receipt: bool,
) -> tuple[str, str, str, str | None, str | None, int | None, str | None]:
    states = conn.execute("SELECT state FROM run_state ORDER BY id").fetchall()
    result, result_sha = artifacts[("result", "terminal")]
    completion, completion_sha = artifacts[("completion", "terminal")]
    expected_lanes = {
        "f72_seed17_sha256": FROZEN_OBSERVED_C0B4["f72_seed17_aggregate_sha256"],
        "f72_seed20260804_sha256": None, "c44_scored_sha256": None,
    }
    if (states != [("INCONCLUSIVE",)]
            or result != {
                "version": "c0b4-result-v1", "policy_id": POLICY_ID,
                "policy_sha256": POLICY_SHA256,
                "protocol_sha256": FROZEN_OBSERVED_C0B4["protocol_sha256"],
                "terminal": "INCONCLUSIVE", "reason": "seed17_no_qualifier",
                "master_plan_sha256": FROZEN_OBSERVED_C0B4["master_plan_sha256"],
                "lane_aggregate_sha256s": expected_lanes,
                "acceptance_aggregate_sha256": None, "selection": None,
            }
            or completion != {
                "version": "c0b4-completion-v1", "policy_id": POLICY_ID,
                "policy_sha256": POLICY_SHA256,
                "protocol_sha256": FROZEN_OBSERVED_C0B4["protocol_sha256"],
                "outcome": "INCONCLUSIVE", "artifact_sha256": result_sha,
                "facts": {"deterministic_stop": True,
                          "reason": "seed17_no_qualifier"},
            }
            or aggregate.get("failure_reasons") != ["negative_false_positive_above_1"]):
        raise C0B6ReplayError("C0B-4 terminal result/completion does not rederive")
    receipts = conn.execute(
        "SELECT anchor_sha256,anchor_json,receipt_sha256,receipt_json "
        "FROM backup_receipts ORDER BY rowid").fetchall()
    if not require_receipt:
        if receipts:
            raise C0B6ReplayError("pre-receipt C0B-4 snapshot contains a receipt")
        return result_sha, completion_sha, "seed17_no_qualifier", None, None, None, None
    if len(receipts) != 1:
        raise C0B6ReplayError("C0B-4 terminal receipt census changed")
    anchor_sha, anchor_raw, receipt_sha, receipt_raw = receipts[0]
    anchor = _exact_json(anchor_raw, _sha256_json(json.loads(anchor_raw)), "backup anchor")
    receipt = _exact_json(receipt_raw, _sha256_json(json.loads(receipt_raw)), "backup receipt")
    if (_sha256_json(anchor, omit="anchor_sha256") != anchor_sha
            or anchor.get("anchor_sha256") != anchor_sha
            or _sha256_json(receipt, omit="receipt_sha256") != receipt_sha
            or receipt.get("receipt_sha256") != receipt_sha
            or anchor_sha != FROZEN_OBSERVED_C0B4["backup_anchor_sha256"]
            or receipt_sha != FROZEN_OBSERVED_C0B4["backup_receipt_sha256"]
            or anchor.get("header_sha256") != FROZEN_OBSERVED_C0B4["run_header_sha256"]
            or anchor.get("terminal_artifact_sha256") != result_sha
            or anchor.get("completion_sha256") != completion_sha
            or anchor.get("parent_binding") != FROZEN_EXECUTION_PARENT
            or receipt.get("anchor_sha256") != anchor_sha
            or receipt.get("snapshot_sha256") !=
            FROZEN_OBSERVED_C0B4["backup_snapshot_sha256"]
            or type(receipt.get("snapshot_size_bytes")) is not int
            or receipt.get("integrity_check") != "ok"
            or receipt.get("foreign_key_violations") != 0):
        raise C0B6ReplayError("C0B-4 backup anchor/receipt does not rederive")
    return (result_sha, completion_sha, "seed17_no_qualifier", anchor_sha,
            receipt["snapshot_sha256"], receipt["snapshot_size_bytes"], receipt_sha)


def replay_c0b4_connection(
        conn: sqlite3.Connection, *, require_receipt: bool,
) -> C0B4ReplayFacts:
    """Rederive the exact C0B-4 observed terminal from a pinned connection."""
    if not isinstance(conn, sqlite3.Connection) or type(require_receipt) is not bool:
        raise TypeError("read-only SQLite connection and exact receipt flag required")
    conn.execute("PRAGMA query_only=ON")
    header, header_sha = _load_header(conn)
    artifacts = _load_artifacts(conn, header)
    corpus = _load_corpus(header)
    master, plans = _plans(artifacts, corpus)
    inactive = _activation_and_inactive_census(conn, artifacts, plans)
    attempts = _attempt_rows(conn)
    _validate_control_attempts(attempts, header, master)
    context_sha = _verify_context(artifacts, attempts, master)
    chunks = _scored_chunks(attempts, plans["F72_17"], corpus)
    documents = _document_rows(plans["F72_17"], chunks, corpus)
    derived = _derive_lane_aggregate(
        plans["F72_17"], documents, corpus, context_sha)
    stored_aggregate, aggregate_sha = artifacts[("lane_aggregate", "F72_17")]
    if _canonical(derived) != _canonical(stored_aggregate):
        raise C0B6ReplayError("C0B-4 F72 aggregate does not independently rederive")
    result_sha, completion_sha, reason, anchor, snapshot, size, receipt = \
        _terminal_and_receipt(
            conn, header, artifacts, master, derived, require_receipt=require_receipt)
    lane_hashes = {lane: plans[lane]["plan_sha256"] for lane in LANE_ORDER}
    facts = C0B4ReplayFacts(
        header_sha, artifacts[("master_plan", "master")][1], lane_hashes,
        inactive, aggregate_sha, result_sha, completion_sha, "INCONCLUSIVE",
        reason, tuple(derived["failure_reasons"]), len(attempts), 1,
        anchor, snapshot, size, receipt)
    expected = FROZEN_OBSERVED_C0B4
    comparisons = {
        "header": facts.header_sha256 == expected["run_header_sha256"],
        "master": facts.master_plan_sha256 == expected["master_plan_sha256"],
        "lanes": dict(facts.lane_plan_sha256s) == expected["lane_plan_sha256s"],
        "inactive": dict(facts.inactive_lane_census) == expected["inactive_lane_census"],
        "aggregate": facts.f72_seed17_aggregate_sha256
            == expected["f72_seed17_aggregate_sha256"],
        "result": facts.terminal_result_sha256 == expected["terminal_result_sha256"],
        "completion": facts.completion_sha256 == expected["completion_sha256"],
    }
    if require_receipt:
        comparisons.update({
            "anchor": facts.backup_anchor_sha256 == expected["backup_anchor_sha256"],
            "snapshot": facts.backup_snapshot_sha256 == expected["backup_snapshot_sha256"],
            "receipt": facts.backup_receipt_sha256 == expected["backup_receipt_sha256"],
        })
    if not all(comparisons.values()):
        raise C0B6ReplayError("C0B-4 replay differs from frozen observed literals")
    return facts


def verify_observed_c0b4_readonly(
        checkpoint: Path, snapshot: Path, *, trusted_root: Path,
) -> C0B4ReplayFacts:
    """Convenience wrapper for callers that need only the C0B-4 parent."""
    from .c0b6_lineage import _PinnedSQLite
    with _PinnedSQLite(checkpoint, trusted_root) as source, \
            _PinnedSQLite(snapshot, trusted_root) as backup:
        if (source.sha256 != FROZEN_OBSERVED_C0B4["checkpoint_sha256"]
                or backup.sha256 != FROZEN_OBSERVED_C0B4["backup_snapshot_sha256"]):
            raise C0B6ReplayError("C0B-4 checkpoint or snapshot bytes changed")
        assert source.conn is not None and backup.conn is not None
        facts = replay_c0b4_connection(source.conn, require_receipt=True)
        backup_facts = replay_c0b4_connection(backup.conn, require_receipt=False)
        if facts.without_receipt() != backup_facts.without_receipt():
            raise C0B6ReplayError("C0B-4 checkpoint/snapshot replay mismatch")
        if (facts.backup_snapshot_sha256 != backup.sha256
                or facts.backup_snapshot_size != backup.identity().size):
            raise C0B6ReplayError("C0B-4 receipt snapshot binding changed")
        return facts


def _c6_artifacts(conn: sqlite3.Connection,
                  ) -> dict[tuple[str, str], tuple[dict[str, Any], str]]:
    values: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
    for kind, owner, digest, raw in conn.execute(
            "SELECT kind,owner_id,sha256,json FROM artifacts ORDER BY kind,owner_id"):
        value = _exact_json(
            raw.decode("utf-8") if type(raw) is bytes else raw,
            digest, f"C0B-6 {kind}/{owner}")
        try:
            normalized = validate_artifact(value)
        except (TypeError, ValueError) as exc:
            raise C0B6ReplayError("C0B-6 artifact violates its closed schema") from exc
        if normalized != value or (kind, owner) in values:
            raise C0B6ReplayError("C0B-6 artifact is noncanonical or duplicated")
        values[(kind, owner)] = value, digest
    return values


def _c6_header(conn: sqlite3.Connection) -> tuple[dict[str, Any], str]:
    rows = conn.execute("SELECT json,sha256 FROM run_header ORDER BY id").fetchall()
    if len(rows) != 1:
        raise C0B6ReplayError("C0B-6 run-header census changed")
    raw, digest = rows[0]
    header = _exact_json(
        raw.decode("utf-8") if type(raw) is bytes else raw,
        digest, "C0B-6 run header")
    try:
        normalized = validate_artifact(header)
    except (TypeError, ValueError) as exc:
        raise C0B6ReplayError("C0B-6 run header violates its closed schema") from exc
    if normalized != header:
        raise C0B6ReplayError("C0B-6 run header is not exact")
    return header, rows[0][1]


def _c6_scored_evidence(
        conn: sqlite3.Connection, master: Mapping[str, Any], corpus: PublicCorpus,
        artifacts: Mapping[tuple[str, str], tuple[dict[str, Any], str]],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    work = {row["work_id"]: row for envelope in (
        *master["lane_plans"], master["acceptance_template"])
        for row in envelope["payload"]["work"]}
    if len(work) != 228:
        raise C0B6ReplayError("C0B-6 master work index changed")
    attempts: dict[str, list[dict[str, Any]]] = {key: [] for key in work}
    unknown: set[str] = set()
    for attempt_id_value, owner, call_class, ordinal, request_sha, state, raw in \
            conn.execute(
                "SELECT attempt_id,owner_id,call_class,invocation_ordinal,"
                "request_sha256,state,payload_json FROM attempts ORDER BY rowid"):
        if owner not in work:
            unknown.add(owner)
            continue
        if (call_class not in {"scored", "schema_retry", "transport_orphan"}
                or request_sha != work[owner]["request_sha256"]
                or attempt_id_value != attempt_id(owner, len(attempts[owner]) + 1)
                or type(ordinal) is not int):
            raise C0B6ReplayError("C0B-6 scored-attempt ownership changed")
        raw_text = raw.decode("utf-8") if type(raw) is bytes else raw
        payload = None if raw_text is None else json.loads(raw_text)
        if raw_text is not None and (
                type(payload) is not dict or _canonical(payload) != raw_text):
            raise C0B6ReplayError("C0B-6 attempt payload is noncanonical")
        attempts[owner].append({
            "attempt_id": attempt_id_value, "call_class": call_class,
            "state": state, "payload": payload,
        })
    result: dict[str, dict[str, Any]] = {}
    for work_id, rows in attempts.items():
        if not rows:
            continue
        answered: list[tuple[dict[str, Any], Any, dict[str, Any]]] = []
        expected_class = "scored"
        closed = False
        source, _start = _source_chunk(work[work_id], corpus)
        for index, row in enumerate(rows, 1):
            payload = row["payload"]
            if closed or row["call_class"] != expected_class:
                raise C0B6ReplayError("C0B-6 attempt sequence changed")
            if payload is None or type(payload.get("answered")) is not bool:
                raise C0B6ReplayError("C0B-6 scored payload is incomplete")
            response = payload.get("response")
            if payload["answered"]:
                if type(response) is not str or type(payload.get("metadata")) is not dict:
                    raise C0B6ReplayError("C0B-6 answered payload is malformed")
                assessment = assess_answer("v2", response, source)
                metadata = payload["metadata"]
                expected_state = (assessment.final_outcome if assessment.eventual_valid
                                  else "SCHEMA_INVALID")
                if (row["state"] != expected_state
                        or metadata.get("raw_response_sha256") !=
                        hashlib.sha256(response.encode()).hexdigest()
                        or metadata.get("raw_first_pass_valid") !=
                        assessment.raw_first_pass_valid
                        or metadata.get("final_outcome") != assessment.final_outcome
                        or metadata.get("semantic_errors") !=
                        list(assessment.semantic_errors)
                        or metadata.get("redundant_rows") != assessment.redundant_rows
                        or metadata.get("removed_finding_indices") !=
                        list(assessment.removed_finding_indices)
                        or metadata.get("raw_counts") != assessment.raw_counts.as_dict()
                        or metadata.get("retained_counts") !=
                        assessment.retained_counts.as_dict()):
                    raise C0B6ReplayError("C0B-6 raw answer assessment does not replay")
                answered.append((row, assessment, metadata))
                closed = assessment.eventual_valid or len(answered) == 2
            elif response is not None or payload.get("metadata") is not None:
                raise C0B6ReplayError("C0B-6 unanswered attempt exposes answer fields")
            expected_class = ("schema_retry" if row["state"] == "SCHEMA_INVALID"
                              else "transport_orphan" if row["state"] in {
                                  "RETRYABLE_TRANSPORT", "ORPHANED_UNKNOWN"} else "")
        if not answered or not closed:
            raise C0B6ReplayError("C0B-6 terminal work has incomplete answer history")
        selected = next((row for row in answered if row[1].eventual_valid), answered[-1])
        selected_row, answer, metadata = selected
        retained = answer.retained_value if answer.eventual_valid else None
        findings = [] if retained is None else [
            {"category": row["category"], "quote": row["quote"]}
            for row in retained["findings"]]
        metas = [row[2] for row in answered]
        prompts = [row.get("prompt_eval_count") for row in metas]
        if any(type(value) is not int for value in prompts):
            raise C0B6ReplayError("C0B-6 prompt-evaluation evidence is incomplete")
        dedup = None
        dedup_hash = None
        if answer.final_outcome == "NORMALIZED_DUPLICATE":
            key = ("dedup_evidence", work_id)
            if key not in artifacts:
                raise C0B6ReplayError("C0B-6 normalized answer lacks dedup evidence")
            dedup, dedup_hash = artifacts[key]
            expected = {
                "version": "c0b6-dedup-evidence-v1",
                **{name: master[name] for name in (
                    "policy_id", "policy_sha256", "protocol_sha256")},
                "work_id": work_id, "attempt_id": selected_row["attempt_id"],
                "raw_response_sha256": metadata["raw_response_sha256"],
                "dedupe_key": "category+nfc_quote",
                "removed_index": answer.removed_finding_indices[0],
                "raw_counts": {**answer.raw_counts.as_dict(),
                    "semantic_invalid_attempts": sum(
                        item.get("semantic_invalid") is True for item in metas)},
                "retained_counts": answer.retained_counts.as_dict(),
            }
            expected["evidence_sha256"] = c0b6_sha256_json(expected)
            if _canonical(expected) != _canonical(dedup):
                raise C0B6ReplayError("C0B-6 dedup evidence does not rederive")
        elif ("dedup_evidence", work_id) in artifacts:
            raise C0B6ReplayError("C0B-6 raw/invalid answer owns dedup evidence")
        chunk = {
            "work_id": work_id, "doc_id": work[work_id]["doc_id"],
            "chunk_index": work[work_id]["chunk_index"], "first_pass_valid": answered[0][1].raw_first_pass_valid,
            "eventual_valid": answer.eventual_valid, "charged_attempt_count": len(rows),
            "strict_schema_invalid_attempts": sum(row.get("strict_schema_invalid") is True for row in metas),
            "semantic_invalid_attempts": sum(row.get("semantic_invalid") is True for row in metas),
            "assessment": retained.get("assessment") if retained else None,
            "predicted_categories": [name for name in CATEGORIES
                if any(row["category"] == name for row in findings)],
            "raw_findings": answer.raw_counts.findings, "raw_grounded_findings": answer.raw_counts.grounded_findings,
            "retained_findings": answer.retained_counts.findings, "retained_grounded_findings": answer.retained_counts.grounded_findings,
            "authoritative_done_reason": metadata.get("done_reason") if answer.eventual_valid else None,
            "length_outcomes": sum(row.get("done_reason") == "length" for row in metas),
            "max_answered_prompt_eval_count": max(prompts),
            "headroom_passed": all(value + 1024 <= int(.85 * 8192) for value in prompts),
            "tools_empty": all(row.get("tools_empty") is True for row in metas),
            "images_empty": all(row.get("images_empty") is True for row in metas),
            "unknown_message_fields_empty": all(row.get("unknown_message_fields_empty") is True for row in metas),
            "schema_escape_empty": all(row[1].schema_escape_empty for row in answered),
            "marker_in_answer": any(_decoded_contains(work[work_id]["nonce"],
                str(row[0]["payload"]["response"])) for row in answered),
            "raw_first_pass_valid": answered[0][1].raw_first_pass_valid,
            "final_outcome": answer.final_outcome if answer.eventual_valid else "INVALID",
            "redundant_rows": answer.redundant_rows,
            "removed_finding_indices": list(answer.removed_finding_indices), "dedup_evidence_sha256": dedup_hash,
        }
        try:
            parsed = C0B6ChunkRow.model_validate(
                chunk, strict=True).model_dump(mode="json")
        except (TypeError, ValueError) as exc:
            raise C0B6ReplayError("C0B-6 chunk evidence is contradictory") from exc
        result[work_id] = {"chunk": parsed, "retained_findings": findings,
                           "dedup_evidence": deepcopy(dedup)}
    return result, unknown


def _c6_receipt(conn: sqlite3.Connection, header: Mapping[str, Any], result_sha: str,
        completion_sha: str | None, *, require_receipt: bool,
) -> tuple[str | None, str | None, int | None, str | None]:
    rows = conn.execute(
        "SELECT anchor_sha256,anchor_json,receipt_sha256,receipt_json "
        "FROM backup_receipts ORDER BY rowid").fetchall()
    if not require_receipt:
        if rows:
            raise C0B6ReplayError("pre-receipt C0B-6 snapshot contains a receipt")
        return None, None, None, None
    if len(rows) != 1:
        raise C0B6ReplayError("C0B-6 terminal receipt census changed")
    anchor_sha, anchor_raw, receipt_sha, receipt_raw = rows[0]
    anchor_raw = anchor_raw.decode("utf-8") if type(anchor_raw) is bytes else anchor_raw
    receipt_raw = receipt_raw.decode("utf-8") if type(receipt_raw) is bytes else receipt_raw
    anchor = _exact_json(
        anchor_raw, hashlib.sha256(anchor_raw.encode()).hexdigest(),
        "C0B-6 backup anchor")
    receipt = _exact_json(
        receipt_raw, hashlib.sha256(receipt_raw.encode()).hexdigest(),
        "C0B-6 backup receipt")
    try:
        anchor = validate_artifact(anchor)
        receipt = validate_artifact(receipt)
    except (TypeError, ValueError) as exc:
        raise C0B6ReplayError("C0B-6 receipt violates its closed schema") from exc
    header_sha = conn.execute("SELECT sha256 FROM run_header WHERE id=1").fetchone()[0]
    if (anchor_sha != anchor["anchor_sha256"]
            or receipt_sha != receipt["receipt_sha256"]
            or anchor["header_sha256"] != header_sha
            or anchor["terminal_artifact_sha256"] != result_sha
            or anchor["completion_sha256"] != completion_sha
            or anchor["parent_binding"] != header["parent_binding"]
            or any(anchor["source_binding"].get(key) != header.get(key)
                   for key in anchor["source_binding"])
            or receipt["anchor_sha256"] != anchor_sha):
        raise C0B6ReplayError("C0B-6 receipt chain does not bind its terminal")
    return (anchor_sha, receipt["snapshot_sha256"],
            receipt["snapshot_size_bytes"], receipt_sha)


def _c6_control_evidence(
        conn: sqlite3.Connection, controls: Mapping[str, Any],
        plans: Mapping[str, Mapping[str, Any]],
        artifacts: Mapping[tuple[str, str], tuple[dict[str, Any], str]],
) -> tuple[str, str | None, bool | None]:
    first_work = {row["work_id"]: row for row in plans["F72_20260811"]["work"]}
    rows = []
    for row in conn.execute(
            "SELECT rowid,attempt_id,owner_id,state,payload_json,created "
            "FROM attempts ORDER BY rowid"):
        raw = row[4].decode("utf-8") if type(row[4]) is bytes else row[4]
        payload = None if raw is None else json.loads(raw)
        rows.append((*row[:4], payload, row[5]))
    answered = [row for row in rows if row[2] in first_work
                and type((row[4] or {}).get("response")) is str]
    context_control = controls["context"]
    context_id = context_control.control["control_id"]
    context_artifact = artifacts.get(("context_evidence", context_id))
    context_attempts = [row for row in rows if row[2] == context_id]
    if context_artifact is None or len(context_attempts) != 1 or not answered:
        raise C0B6ReplayError("C0B-6 context evidence census changed")
    context_attempt = context_attempts[0]
    context_payload = context_attempt[4]
    try:
        body = json.loads(context_payload["response"])
        metadata = context_payload["metadata"]
    except (TypeError, KeyError, json.JSONDecodeError) as exc:
        raise C0B6ReplayError("C0B-6 context response is malformed") from exc
    trigger = answered[0]
    trigger_work = first_work[trigger[2]]
    expected_context = {
        "version": "c0b6-context-evidence-v1",
        **{name: context_artifact[0][name] for name in (
            "policy_id", "policy_sha256", "protocol_sha256")},
        "control_id": context_id, "lane_id": "F72_20260811",
        "purpose": context_control.control["purpose"], "candidate_id": context_control.control["candidate_id"],
        "model": context_control.control["model"], "model_digest": context_control.control["model_digest"],
        "config_sha256": context_control.control["config_sha256"], "prompt_sha256": context_control.control["prompt_sha256"],
        "expected_num_ctx": 8192, "observed_context_length": body["context_length"],
        "trigger_work_id": trigger_work["work_id"], "trigger_attempt_id": trigger[1],
        "trigger_request_sha256": trigger_work["request_sha256"], "trigger_nonce": trigger_work["nonce"],
        "state": "PASSED",
        "response_sha256": hashlib.sha256(
            context_payload["response"].encode()).hexdigest(),
    }
    if (context_attempt[3] != "RAW_VALID"
            or metadata.get("response_sha256") != expected_context["response_sha256"]
            or _canonical(context_artifact[0]) != _canonical(expected_context)):
        raise C0B6ReplayError("C0B-6 context evidence does not independently rederive")
    cancel_control, health_control = controls["cancellation"], controls["health"]
    cancel_id = cancel_control.control["control_id"]
    cancellation = artifacts.get(("cancellation_health_evidence", cancel_id))
    if cancellation is None:
        return context_artifact[1], None, None
    cancelled = [row for row in rows
                 if row[2] == cancel_id and row[3] == "CANCELLED_UNVERIFIED"]
    health_id = health_control.control["control_id"]
    health_attempts = [row for row in rows if row[2] == health_id]
    health_answered = [row for row in health_attempts
                       if type((row[4] or {}).get("response")) is str]
    if len(cancelled) != 1 or not health_answered:
        raise C0B6ReplayError("C0B-6 cancellation/health attempt census changed")
    cancel_row = cancelled[0]
    cancel_payload = cancel_row[4]
    health_row = health_answered[-1]
    health_payload = health_row[4]
    answer = assess_answer(
        "v2", health_payload["response"], health_control.source_chunk or "")
    retained = answer.retained_value or {"findings": []}
    emitted = any(row["category"] == "pii" for row in retained["findings"])
    grounded = {row.index: row.grounded for row in answer.grounding}
    kept = ([index for index in range(len(answer.raw_value["findings"]))
             if index not in answer.removed_finding_indices]
            if answer.raw_value is not None else [])
    grounded_pii = answer.eventual_valid and any(
        row["category"] == "pii" and grounded.get(index, False)
        for index, row in zip(kept, retained["findings"], strict=True))
    meta = health_payload["metadata"]
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
    prompt = meta.get("prompt_eval_count")
    headroom = type(prompt) is int and prompt + 1024 <= int(.85 * 8192)
    if not headroom:
        reasons.append("health_context_headroom_failure")
    expected_cancel = {
        "version": "c0b6-cancellation-health-evidence-v1",
        **{name: cancellation[0][name] for name in (
            "policy_id", "policy_sha256", "protocol_sha256")},
        "lane_id": "F72_20260811",
        "candidate_id": cancel_control.control["candidate_id"], "prompt_sha256": health_control.control["prompt_sha256"],
        "cancel_control_id": cancel_id, "cancel_attempt_id": cancel_row[1],
        "cancel_state": "CANCELLED_UNVERIFIED", "cancel_first_byte_seen": cancel_payload.get("first_byte_seen") is True,
        "cancel_elapsed_ms": elapsed, "health_control_id": health_id,
        "health_work_id": health_control.control["health_work_id"], "health_attempt_ids": [row[1] for row in health_attempts],
        "not_before_utc": cancel_payload["health_not_before_utc"],
        "started_at_utc": datetime.fromtimestamp(
            float(health_attempts[0][5]), timezone.utc).isoformat(
                timespec="microseconds").replace("+00:00", "Z"),
        "eventual_valid": answer.eventual_valid, "retained_grounded_pii": grounded_pii,
        "authoritative_done_reason": meta.get("done_reason") if answer.eventual_valid else None,
        "max_answered_prompt_eval_count": prompt if answer.eventual_valid else None,
        "length_outcomes": int(meta.get("done_reason") == "length"),
        "headroom_passed": headroom, "tools_empty": meta.get("tools_empty") is True,
        "images_empty": meta.get("images_empty") is True,
        "unknown_message_fields_empty": meta.get("unknown_message_fields_empty") is True,
        "schema_escape_empty": answer.schema_escape_empty,
        "passed": not reasons, "failure_reasons": reasons,
    }
    if (_canonical(cancellation[0]) != _canonical(expected_cancel)
            or cancel_payload.get("answered") is not False):
        raise C0B6ReplayError(
            "C0B-6 cancellation/health evidence does not independently rederive")
    return context_artifact[1], cancellation[1], not reasons


def _c6_failure_terminal(
        conn: sqlite3.Connection, header: Mapping[str, Any], header_sha: str,
        master_sha: str, plans: Mapping[str, Mapping[str, Any]],
        controls: Mapping[str, Any], artifacts: Mapping[
            tuple[str, str], tuple[dict[str, Any], str]],
        state: str, *, require_receipt: bool,
) -> C0B6ReplayFacts:
    failure_row = artifacts.get(("failure", "terminal"))
    evidence_row = artifacts.get(("failure_evidence", "terminal"))
    if (failure_row is None or evidence_row is None
            or ("result", "terminal") in artifacts
            or ("completion", "terminal") in artifacts):
        raise C0B6ReplayError("C0B-6 failure terminal owner census changed")
    failure, failure_sha = failure_row
    evidence, evidence_sha = evidence_row
    attempt_count = conn.execute("SELECT count(*) FROM attempts").fetchone()[0]
    if (failure["terminal"] != state or evidence["terminal"] != state
            or failure["reason"] != evidence["reason"]
            or failure["failure_origin"] != evidence["failure_origin"]
            or failure["evidence_sha256"] != evidence_sha
            or failure["charged_call_total"] != attempt_count
            or evidence["charged_call_total"] != attempt_count):
        raise C0B6ReplayError("C0B-6 failure facts do not rederive")
    lane_id, plan_sha = evidence["lane_id"], evidence["plan_sha256"]
    if ((lane_id is None) != (plan_sha is None)
            or lane_id is not None and (
                lane_id not in plans or plans[lane_id]["plan_sha256"] != plan_sha)):
        raise C0B6ReplayError("C0B-6 failure lane ownership changed")
    control_id = evidence["control_id"]
    control_ids = {row.control["control_id"] for row in controls.values()}
    if control_id is not None and control_id not in control_ids:
        raise C0B6ReplayError("C0B-6 failure names an unknown control")
    attempt_value = evidence["attempt_id"]
    if attempt_value is not None:
        rows = conn.execute(
            "SELECT owner_id,state FROM attempts WHERE attempt_id=?",
            (attempt_value,)).fetchall()
        if len(rows) != 1 or rows[0][1] != state:
            raise C0B6ReplayError("C0B-6 failure attempt does not own its terminal")
        owner = rows[0][0]
        lane_work = ({row["work_id"] for row in plans[lane_id]["work"]}
                     if lane_id is not None else set())
        if (control_id is not None and owner != control_id
                or control_id is None and lane_id is not None and owner not in lane_work):
            raise C0B6ReplayError("C0B-6 failure attempt owner changed")
    anchor, snapshot, size, receipt = _c6_receipt(
        conn, header, failure_sha, None, require_receipt=require_receipt)
    return C0B6ReplayFacts(
        header_sha, master_sha, {}, None, deepcopy(failure), None, None,
        anchor, snapshot, size, receipt)


def _c6_schedule(
        master_sha: str, plans: Mapping[str, Mapping[str, Any]],
        artifacts: Mapping[tuple[str, str], tuple[dict[str, Any], str]], active: Sequence[str],
        aggregate_hashes: Mapping[str, str],
) -> None:
    previous = master_sha
    expected_transitions: set[tuple[str, str]] = set()
    for index, lane_id in enumerate(active):
        activation = artifacts.get(("plan_activation", lane_id))
        later = C0B6_LANE_ORDER[C0B6_LANE_ORDER.index(lane_id) + 1:]
        inactive = sorted(row["work_id"] for later_id in later
                          for row in plans[later_id]["work"])
        if (activation is None
                or activation[0]["plan_sha256"] != plans[lane_id]["plan_sha256"]
                or activation[0]["prerequisite_sha256"] != previous
                or activation[0]["activated_work_ids"] != sorted(
                    row["work_id"] for row in plans[lane_id]["work"])
                or activation[0]["inactive_work_ids"] != inactive):
            raise C0B6ReplayError("C0B-6 activation chain does not rederive")
        if index:
            prior_id = active[index - 1]
            transition_key = ("cursor_transition", prior_id)
            expected_transitions.add(transition_key)
            transition = artifacts.get(transition_key)
            census = c0b6_sha256_json({
                "lane_id": prior_id,
                "completed_work_ids": sorted(
                    row["work_id"] for row in plans[prior_id]["work"]),
            })
            if (transition is None
                    or transition[0]["from_lane_id"] != prior_id
                    or transition[0]["to_lane_id"] != lane_id
                    or transition[0]["from_aggregate_sha256"] != previous
                    or transition[0]["to_plan_sha256"] != plans[lane_id]["plan_sha256"]
                    or transition[0]["completed_work_census_sha256"] != census
                    or transition[0]["transition_sha256"] != transition[1]):
                raise C0B6ReplayError("C0B-6 cursor transition does not rederive")
        previous = aggregate_hashes[lane_id]
    actual = {key for key in artifacts if key[0] == "cursor_transition"}
    if actual != expected_transitions:
        raise C0B6ReplayError("C0B-6 cursor-transition owner census changed")


def replay_c0b6_connection(conn: sqlite3.Connection, *, parent_facts: Any,
                           require_receipt: bool,
) -> C0B6ReplayFacts:
    """Rederive a quality terminal and public view from raw C0B-6 evidence."""
    if not isinstance(conn, sqlite3.Connection) or type(require_receipt) is not bool:
        raise TypeError("read-only SQLite connection and exact receipt flag required")
    try:
        validate_run_lineage(conn)
    except Exception as exc:
        raise C0B6ReplayError("C0B-6 checkpoint lineage is invalid") from exc
    conn.execute("PRAGMA query_only=ON")
    header, header_sha = _c6_header(conn)
    try:
        binding = parent_facts.parent_binding
        d50_facts = parent_facts.c0b3_d50_facts
    except AttributeError as exc:
        raise C0B6ReplayError("verified parent facts are required") from exc
    if header.get("parent_binding") != binding:
        raise C0B6ReplayError("C0B-6 header differs from verified parent binding")
    artifacts = _c6_artifacts(conn)
    master_key = ("master_plan", "master")
    if master_key not in artifacts:
        raise C0B6ReplayError("C0B-6 master plan is absent")
    master, master_sha = artifacts[master_key]
    source = goldset.load(verify=True)
    manifest = build_master_manifest(source)
    if manifest.sha256 != header.get("master_manifest_sha256"):
        raise C0B6ReplayError("C0B-6 public manifest identity changed")
    corpus = load_public_corpus(
        master_manifest_payload(manifest), master_manifest_sha256=manifest.sha256,
        source=source)
    key_rows = conn.execute(
        "SELECT value,sha256 FROM protected_values WHERE name='run_nonce_key'"
    ).fetchall()
    if (len(key_rows) != 1 or type(key_rows[0][0]) is not bytes
            or len(key_rows[0][0]) != 32
            or hashlib.sha256(key_rows[0][0]).hexdigest() != key_rows[0][1]):
        raise C0B6ReplayError("C0B-6 protected nonce key changed")
    try:
        master = validate_master_plan(master, corpus=corpus,
                                      run_nonce_key=key_rows[0][0])
        resolver = build_request_resolver(
            master, corpus=corpus, run_nonce_key=key_rows[0][0])
    except Exception as exc:
        raise C0B6ReplayError("C0B-6 master plan does not independently rederive") from exc
    plans = {envelope["payload"]["lane_id"]: envelope["payload"]
             for envelope in (*master["lane_plans"], master["acceptance_template"])}
    for lane_id, plan in plans.items():
        row = artifacts.get(("lane_plan", lane_id))
        if row is None or _canonical(row[0]) != _canonical(plan):
            raise C0B6ReplayError("C0B-6 stored lane plan differs from master")
    controls = resolver.resolve_controls()
    states = conn.execute("SELECT state FROM run_state ORDER BY id").fetchall()
    if len(states) != 1:
        raise C0B6ReplayError("C0B-6 run-state census changed")
    state = states[0][0]
    if state not in {"CONFIRMED", "INCONCLUSIVE"}:
        return _c6_failure_terminal(
            conn, header, header_sha, master_sha, plans, controls, artifacts,
            state, require_receipt=require_receipt)
    evidence, unknown = _c6_scored_evidence(conn, master, corpus, artifacts)
    control_ids = {row.control["control_id"] for row in controls.values()}
    invocation_ordinals = [row[0] for row in conn.execute(
        "SELECT ordinal FROM invocations ORDER BY ordinal")]
    preflight_ids = {stable_hash({"c0b6_preflight": name, "invocation": ordinal})
                     for ordinal in invocation_ordinals
                     for name in ("version", "tags", "show")}
    if unknown - control_ids - preflight_ids:
        raise C0B6ReplayError("C0B-6 attempt owner is outside plan and controls")
    model, digest = SELECTION["model"], SELECTION["model_digest"]
    control_requests = {
        row.control["control_id"]: request_spec_hash(row.request_spec)
        for row in controls.values()}
    preflight_specs = {
        "version": RequestSpec(kind="version", expected_version=header["ollama_version"]),
        "tags": RequestSpec(kind="tags", expected_models={model: digest}),
        "show": RequestSpec(kind="show", expected_model=model, expected_digest=digest),
    }
    for ordinal in invocation_ordinals:
        control_requests.update({
            stable_hash({"c0b6_preflight": name, "invocation": ordinal}):
                request_spec_hash(spec) for name, spec in preflight_specs.items()})
    control_counts: dict[str, int] = {}
    for attempt_value, owner, request_sha in conn.execute(
            "SELECT attempt_id,owner_id,request_sha256 FROM attempts ORDER BY rowid"):
        if owner not in unknown:
            continue
        control_counts[owner] = control_counts.get(owner, 0) + 1
        if (control_requests.get(owner) != request_sha
                or attempt_value != attempt_id(
                    f"control:{owner}", control_counts[owner])):
            raise C0B6ReplayError("C0B-6 control attempt identity changed")
    context_control_facts = _c6_control_evidence(
        conn, controls, plans, artifacts)
    result_row = artifacts.get(("result", "terminal"))
    completion_row = artifacts.get(("completion", "terminal"))
    if result_row is None or completion_row is None:
        raise C0B6ReplayError("C0B-6 quality terminal artifacts are absent")
    result, result_sha = result_row
    completion, completion_sha = completion_row
    if (result["terminal"] != state
            or completion["artifact_sha256"] != result_sha
            or completion["outcome"] != result["terminal"]
            or result["master_plan_sha256"] != master_sha):
        raise C0B6ReplayError("C0B-6 result/completion ownership changed")
    active = ["F72_20260811"]
    if result["lane_aggregate_sha256s"]["f72_seed20260818_sha256"] is not None:
        active.append("F72_20260818")
    if result["lane_aggregate_sha256s"]["c44_scored_sha256"] is not None:
        active.append("C44_1")
    derived_lanes: dict[str, dict[str, Any]] = {}
    stored_kind = {"F72_20260811": "lane_aggregate",
                   "F72_20260818": "lane_aggregate", "C44_1": "c44_aggregate"}
    result_key = {"F72_20260811": "f72_seed20260811_sha256",
                  "F72_20260818": "f72_seed20260818_sha256",
                  "C44_1": "c44_scored_sha256"}
    aggregate_hashes = {
        lane_id: result["lane_aggregate_sha256s"][result_key[lane_id]]
        for lane_id in active}
    _c6_schedule(master_sha, plans, artifacts, active, aggregate_hashes)
    for lane_id in active:
        owned = {row["work_id"]: evidence[row["work_id"]]
                 for row in plans[lane_id]["work"] if row["work_id"] in evidence}
        if len(owned) != len(plans[lane_id]["work"]):
            raise C0B6ReplayError("C0B-6 active lane lacks exact attempt coverage")
        context_sha = cancellation_sha = None
        controls_passed: bool | None = True
        if lane_id == "F72_20260811":
            context_sha, cancellation_sha, controls_passed = context_control_facts
        try:
            derived = build_lane_aggregate(
                plans[lane_id], owned, corpus=corpus,
                context_evidence_sha256=context_sha,
                cancellation_health_evidence_sha256=cancellation_sha,
                controls_passed=controls_passed)
        except Exception as exc:
            raise C0B6ReplayError("C0B-6 lane attempt evidence does not replay") from exc
        stored = artifacts.get((stored_kind[lane_id], lane_id))
        if (stored is None or _canonical(stored[0]) != _canonical(derived)
                or result["lane_aggregate_sha256s"][result_key[lane_id]] != stored[1]):
            raise C0B6ReplayError("C0B-6 stored lane aggregate does not rederive")
        derived_lanes[lane_id] = derived
    inactive_ids = set(plans) - set(active)
    inactive_work = {row["work_id"] for lane in inactive_ids
                     for row in plans[lane]["work"]}
    if inactive_work & set(evidence) or any(
            kind in {"plan_activation", "lane_aggregate", "c44_aggregate"}
            and owner in inactive_ids for kind, owner in artifacts):
        raise C0B6ReplayError("C0B-6 inactive lane owns execution evidence")
    try:
        d50 = derive_parent_d50_component(
            d50_facts.final_d_decision, d50_facts.d4_aggregate, corpus=corpus,
            negative_retained_findings=d50_facts.negative_retained_findings)
    except Exception as exc:
        raise C0B6ReplayError("C0B-6 D50 parent component does not replay") from exc
    acceptance = None
    if "C44_1" in active:
        acceptance = build_acceptance_aggregate(
            derived_lanes["C44_1"], d50, derived_lanes["F72_20260811"],
            corpus=corpus, acceptance_plan_sha256=plans["C44_1"]["plan_sha256"])
        stored = artifacts.get(("acceptance_aggregate", "complete"))
        if (stored is None or _canonical(stored[0]) != _canonical(acceptance)
                or result["acceptance_aggregate_sha256"] != stored[1]):
            raise C0B6ReplayError("C0B-6 final acceptance does not rederive")
    if (len(active) == 1 and derived_lanes["F72_20260811"]["passed"]
            or len(active) == 2 and derived_lanes["F72_20260818"]["passed"]):
        raise C0B6ReplayError("C0B-6 terminal stopped before its next required lane")
    expected_reason = (
        "seed20260811_control_gate_failed" if not derived_lanes["F72_20260811"]["passed"]
        and derived_lanes["F72_20260811"]["failure_reasons"] ==
        ["cancellation_health_failure"] else
        "seed20260811_no_qualifier" if not derived_lanes["F72_20260811"]["passed"] else
        "seed20260818_no_qualifier" if "F72_20260818" in derived_lanes
        and not derived_lanes["F72_20260818"]["passed"] else
        "complete_public_acceptance_passed" if acceptance is not None
        and acceptance["passed"] else "complete_corpus_acceptance_failed")
    if result["reason"] != expected_reason:
        raise C0B6ReplayError("C0B-6 terminal reason does not follow replayed gates")
    public = build_public_summary(
        run_id=header["run_id"], result=result, completion=completion,
        f72_seed20260811_lane=derived_lanes["F72_20260811"],
        f72_seed20260818_lane=derived_lanes.get("F72_20260818"),
        c44_lane=derived_lanes.get("C44_1"), acceptance_aggregate=acceptance,
        d50_component=d50,
        d50_false_positive_documents=d50_facts.false_positive_documents,
        corpus=corpus)
    anchor, snapshot, size, receipt = _c6_receipt(
        conn, header, result_sha, completion_sha, require_receipt=require_receipt)
    return C0B6ReplayFacts(
        header_sha, master_sha, deepcopy(derived_lanes), deepcopy(acceptance),
        deepcopy(result), deepcopy(completion), public,
        anchor, snapshot, size, receipt)


def verify_c0b6_terminal_readonly(
        checkpoint: Path, snapshot: Path, *, trusted_root: Path,
        parent_facts: Any,
) -> C0B6ReplayFacts:
    """Descriptor-safely replay a live child and its immutable pre-receipt copy."""
    from .c0b6_lineage import _PinnedSQLite
    with _PinnedSQLite(checkpoint, trusted_root) as source, \
            _PinnedSQLite(snapshot, trusted_root) as backup:
        assert source.conn is not None and backup.conn is not None
        facts = replay_c0b6_connection(
            source.conn, parent_facts=parent_facts, require_receipt=True)
        backup_facts = replay_c0b6_connection(
            backup.conn, parent_facts=parent_facts, require_receipt=False)
        if facts.without_receipt() != backup_facts.without_receipt():
            raise C0B6ReplayError("C0B-6 checkpoint/snapshot replay mismatch")
        if (facts.backup_snapshot_sha256 != backup.sha256
                or facts.backup_snapshot_size != backup.identity().size):
            raise C0B6ReplayError("C0B-6 receipt snapshot binding changed")
        return facts
