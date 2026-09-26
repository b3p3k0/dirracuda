"""Pure scoring for the C0B-6 assistive review-budget confirmation.

All aggregates are rebuilt from bounded public evidence.  The module owns the two
independent false-positive budgets: affected negative documents and retained suggestion
rows on those documents.  It performs no filesystem, checkpoint, or network access.

DISPOSITION: benchmark-only; remove after the accepted confirmation is handed to C1.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from fractions import Fraction
from typing import Any, Mapping, Sequence

from .c0b2_public_schema import sha256_json
from .c0b2_public_scoring import (
    derive_category_metrics, fraction_value, ordered_reasons,
)
from .c0b2_schema import CATEGORIES, canonical_json
from .c0b2_stage_f import (
    ChunkRow, StageFError, _injection_pairs,
    build_d50_component as build_legacy_d50_component,
    validate_acceptance_component_artifact,
)
from .c0b2_stage_f_plan import PublicCorpus
from .metrics import ground_finding
from .c0b6_lineage import FROZEN_PARENT_BINDING, validate_parent_binding
from .c0b6_plan import LANE_CONFIG, SELECTION, candidate_id
from .c0b6_policy import (
    ACCEPTANCE_FAILURE_REASONS, C44_FAILURE_REASONS, LANE_FAILURE_REASONS,
    POLICY_ID, POLICY_SHA256,
)
from .c0b6_schema import (
    AcceptanceAggregate, C0B6ChunkRow, C0B6PublicWork, C44ScoredAggregate,
    Completion, DedupEvidence, LaneAggregate, PublicSummary, Result,
)

PARENT_BINDING = FROZEN_PARENT_BINDING
F72_FAILURE_REASONS = LANE_FAILURE_REASONS
_FP_COMPONENTS = (
    "C44_RERUN", "D50_CONFIRMATION", "F72_SEED20260811",
    "F72_SEED20260818",
)
_TEMPLATE_RULES = {
    "neg_clean_": {
        0: "clean_sprint_retrospective", 1: "clean_boiler_maintenance_log",
        2: "clean_library_acquisition_notes", 3: "clean_cafeteria_menu_cycle",
        4: "clean_parking_structure_survey",
    },
    "neg_nearmiss_": {
        0: "near_miss_checksum_failed_barcode",
        1: "near_miss_ssn_shaped_part_number",
        2: "near_miss_phone_shaped_chassis_serial",
        3: "near_miss_invalid_routing_cost_centre",
        4: "near_miss_invalid_iban_template_placeholder",
    },
}


class C0B6ScoringError(RuntimeError):
    """Planned work, evidence, or a derived aggregate is not exact."""


def _sha256(value: Any, label: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise C0B6ScoringError(f"{label} must be lowercase SHA-256")
    return value


def _identity(value: Mapping[str, Any]) -> dict[str, str]:
    if (value.get("policy_id"), value.get("policy_sha256")) != (
            POLICY_ID, POLICY_SHA256):
        raise C0B6ScoringError("artifact policy identity is not C0B-6")
    return {
        "policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256,
        "protocol_sha256": _sha256(value.get("protocol_sha256"), "protocol"),
    }


def _model(model: Any, value: Mapping[str, Any], label: str) -> dict[str, Any]:
    try:
        return model.model_validate(value, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise C0B6ScoringError(f"{label} violates its strict C0B-6 schema") from exc


def _plan_digest(plan: Mapping[str, Any]) -> str:
    body = dict(plan)
    stored = body.pop("plan_sha256", None)
    digest = sha256_json(body)
    if stored != digest:
        raise C0B6ScoringError("lane plan self-digest is invalid")
    return digest


def _validate_plan(plan: Mapping[str, Any], corpus: PublicCorpus) -> dict[str, Any]:
    if type(plan) is not dict or plan.get("lane_id") not in LANE_CONFIG:
        raise C0B6ScoringError("lane plan is not an exact C0B-6 lane")
    lane_id = plan["lane_id"]
    expected_version = (
        "c0b6-acceptance-plan-v1" if lane_id == "C44_1"
        else "c0b6-lane-plan-v1")
    expected_keys = {
        "version", "policy_id", "policy_sha256", "protocol_sha256", "lane_id",
        "seed", "candidate", "parent_evidence", "work", "plan_sha256",
    }
    if set(plan) != expected_keys or plan["version"] != expected_version:
        raise C0B6ScoringError("lane plan has an invalid shape or version")
    _identity(plan)
    parent = validate_parent_binding(plan["parent_evidence"])
    if corpus.master_manifest_sha256 != \
            parent["execution_parent"]["master_manifest_sha256"]:
        raise C0B6ScoringError("lane corpus differs from parent manifest")
    if canonical_json(plan["candidate"]) != canonical_json(SELECTION):
        raise C0B6ScoringError("lane candidate differs from finalist")
    seed, plan_key, _domain, count = LANE_CONFIG[lane_id]
    if plan["seed"] != seed or type(plan["work"]) is not list:
        raise C0B6ScoringError("lane seed or work type is invalid")
    work = [_model(C0B6PublicWork, row, "lane work") for row in plan["work"]]
    order = corpus.c_order if lane_id == "C44_1" else corpus.f_order
    if (len(work) != count or len({row["work_id"] for row in work}) != count
            or any(row["seed"] != seed or row["plan_key"] != plan_key
                   for row in work)
            or list(dict.fromkeys(row["doc_id"] for row in work)) != list(order)):
        raise C0B6ScoringError("lane work census/order differs from corpus split")
    _plan_digest(plan)
    return deepcopy(plan)


def _source_chunk(work: Mapping[str, Any], corpus: PublicCorpus) -> tuple[str, int]:
    document = corpus.by_id().get(work["doc_id"])
    if document is None or document.document_sha256 != work["document_sha256"]:
        raise C0B6ScoringError("work differs from public document")
    source, view_id = document.source_for(
        work["chunk_chars"], derived=work["view_id"] is not None)
    if view_id != work["view_id"]:
        raise C0B6ScoringError("work differs from public boundary view")
    from . import chunker
    chunks = chunker.chunk(
        source, chunk_chars=work["chunk_chars"], overlap_chars=work["overlap"])
    if work["chunk_index"] >= len(chunks):
        raise C0B6ScoringError("work chunk is absent")
    selected = chunks[work["chunk_index"]]
    if hashlib.sha256(selected.text.encode()).hexdigest() != work["chunk_sha256"]:
        raise C0B6ScoringError("work chunk hash differs from public source")
    return selected.text, selected.start


def _normalized_chunk(work: Mapping[str, Any], evidence: Mapping[str, Any],
                      corpus: PublicCorpus) -> tuple[
                          dict[str, Any], set[tuple[str, str, int]], bool]:
    if type(evidence) is not dict or set(evidence) != {
            "chunk", "retained_findings", "dedup_evidence"}:
        raise C0B6ScoringError("work evidence has an inexact shape")
    raw = evidence["chunk"]
    extras = {
        "raw_first_pass_valid", "final_outcome", "redundant_rows",
        "removed_finding_indices", "dedup_evidence_sha256",
    }
    if type(raw) is not dict or set(raw) != set(ChunkRow.model_fields) | extras:
        raise C0B6ScoringError("C0B-6 chunk row has an invalid shape")
    hardened = _model(C0B6ChunkRow, raw, "chunk")
    old = {key: hardened[key] for key in ChunkRow.model_fields}
    if old["work_id"] != work["work_id"] or old["doc_id"] != work["doc_id"]:
        raise C0B6ScoringError("chunk evidence differs from planned work")
    normalized = raw["final_outcome"] == "NORMALIZED_DUPLICATE"
    dedup = evidence["dedup_evidence"]
    if normalized:
        dedup = _model(DedupEvidence, dedup, "dedup evidence")
        if (dedup["work_id"] != work["work_id"]
                or dedup["evidence_sha256"] != raw["dedup_evidence_sha256"]
                or dedup["removed_index"] != raw["removed_finding_indices"][0]
                or dedup["raw_counts"] != {
                    "findings": old["raw_findings"],
                    "grounded_findings": old["raw_grounded_findings"],
                    "first_pass_valid": old["first_pass_valid"],
                    "semantic_invalid_attempts": old["semantic_invalid_attempts"],
                }
                or dedup["retained_counts"] != {
                    "findings": old["retained_findings"],
                    "grounded_findings": old["retained_grounded_findings"],
                    "eventual_valid": old["eventual_valid"],
                }):
            raise C0B6ScoringError("dedup evidence differs from chunk facts")
    elif dedup is not None:
        raise C0B6ScoringError("non-normalized chunk carries dedup artifact")
    findings = evidence["retained_findings"]
    if type(findings) is not list or len(findings) > 16:
        raise C0B6ScoringError("retained finding list is malformed or unbounded")
    source, chunk_start = _source_chunk(work, corpus)
    retained: set[tuple[str, str, int]] = set()
    canonical = len(findings) == old["retained_findings"]
    for finding in findings:
        if (type(finding) is not dict or set(finding) != {"category", "quote"}
                or finding["category"] not in CATEGORIES
                or type(finding["quote"]) is not str):
            raise C0B6ScoringError("retained finding has an invalid shape")
        grounded = ground_finding(finding["quote"], 0, source)
        if not grounded.grounded or grounded.canonical_offset is None:
            canonical = False
            continue
        item = (finding["category"], finding["quote"],
                chunk_start + grounded.canonical_offset)
        if item in retained:
            canonical = False
        retained.add(item)
    canonical = canonical and len(retained) == old["retained_findings"]
    predicted = [category for category in CATEGORIES
                 if any(row[0] == category for row in retained)]
    if predicted != old["predicted_categories"]:
        canonical = False
    return hardened, retained, canonical


def _documents(plan: Mapping[str, Any], evidence_by_work: Mapping[str, Any],
               corpus: PublicCorpus) -> tuple[list[dict[str, Any]], bool]:
    work = plan["work"]
    if (type(evidence_by_work) is not dict
            or set(evidence_by_work) != {row["work_id"] for row in work}):
        raise C0B6ScoringError("evidence must exactly cover planned work")
    grouped: dict[str, list[tuple[
        dict[str, Any], set[tuple[str, str, int]], bool]]] = {}
    for item in work:
        grouped.setdefault(item["doc_id"], []).append(
            _normalized_chunk(item, evidence_by_work[item["work_id"]], corpus))
    order = corpus.c_order if plan["lane_id"] == "C44_1" else corpus.f_order
    if list(grouped) != list(order):
        raise C0B6ScoringError("document evidence differs from manifest order")
    metadata = corpus.by_id()
    rows: list[dict[str, Any]] = []
    for doc_id in order:
        document, chunks = metadata[doc_id], grouped[doc_id]
        retained = set().union(*(values for _row, values, _exact in chunks))
        predicted = [category for category in CATEGORIES
                     if any(item[0] == category for item in retained)]
        boundary = None
        if document.stratum == "boundary":
            boundary = any(
                category == "pii" and any(identifier in quote
                                           for identifier in document.expected_identifiers)
                for category, quote, _offset in retained)
        chunk_rows = [row for row, _values, _exact in chunks]
        rows.append({
            "doc_id": doc_id, "stratum": document.stratum,
            "expected_categories": list(document.categories_present),
            "predicted_categories": predicted,
            "expected_chunk_count": len(chunk_rows),
            "completed_chunk_count": len(chunk_rows),
            "first_pass_invalid_chunks": sum(
                not row["raw_first_pass_valid"] for row in chunk_rows),
            "eventual_invalid_chunks": sum(
                not row["eventual_valid"] for row in chunk_rows),
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
            "redundant_rows": sum(row["redundant_rows"] for row in chunk_rows),
            "affected_work_ids": sorted(
                row["work_id"] for row in chunk_rows if row["redundant_rows"]),
            "normalized_duplicate_chunks": sum(
                row["final_outcome"] == "NORMALIZED_DUPLICATE"
                for row in chunk_rows),
            "affected_document": any(row["redundant_rows"] for row in chunk_rows),
        })
    return rows, all(exact for chunks in grouped.values()
                     for _row, _values, exact in chunks)


def _recovery_counters(documents: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    work_ids = sorted({work_id for row in documents
                       for work_id in row["affected_work_ids"]})
    doc_ids = sorted(row["doc_id"] for row in documents if row["affected_document"])
    return {
        "redundant_rows": sum(row["redundant_rows"] for row in documents),
        "affected_work_ids": work_ids, "affected_chunk_count": len(work_ids),
        "affected_document_ids": doc_ids, "affected_document_count": len(doc_ids),
        "normalized_duplicate_chunks": sum(
            row["normalized_duplicate_chunks"] for row in documents),
    }


def _build_lane_aggregate(
        plan: Mapping[str, Any], evidence_by_work: Mapping[str, Any], *,
        corpus: PublicCorpus, context_evidence_sha256: str | None = None,
        cancellation_health_evidence_sha256: str | None = None,
        controls_passed: bool | None = True,
        precontrol_probe: bool = False,
) -> dict[str, Any] | None:
    parsed = _validate_plan(plan, corpus)
    if controls_passed is not None and type(controls_passed) is not bool:
        raise C0B6ScoringError("control pass flag must be Boolean or not-run")
    lane_id, assigned = parsed["lane_id"], parsed["lane_id"] == "F72_20260811"
    if assigned:
        _sha256(context_evidence_sha256, "context evidence")
        _sha256(cancellation_health_evidence_sha256, "cancellation evidence",
                nullable=True)
    elif context_evidence_sha256 is not None or \
            cancellation_health_evidence_sha256 is not None:
        raise C0B6ScoringError("unassigned lane carries control evidence")
    elif controls_passed is None:
        raise C0B6ScoringError("unassigned lane cannot have not-run controls")
    documents, canonical = _documents(parsed, evidence_by_work, corpus)
    if not canonical and lane_id != "C44_1":
        raise C0B6ScoringError("F72 retained evidence is noncanonical")
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
        "raw_grounded_findings": sum(
            row["raw_grounded_findings"] for row in documents),
        "first_pass_invalid_chunks": sum(
            row["first_pass_invalid_chunks"] for row in documents),
        "raw_semantic_invalid_attempts": sum(
            chunk["semantic_invalid_attempts"] for row in documents
            for chunk in row["chunks"]),
    }
    retained = {
        "documents": documents, "category_metrics": metrics,
        "macro_f1": summary["macro_f1"], "micro_f1": summary["micro_f1"],
        "retained_findings": sum(row["retained_findings"] for row in documents),
        "retained_grounded_findings": sum(
            row["retained_grounded_findings"] for row in documents),
        "negative_false_positive_documents": sum(
            row["retained_findings"] > 0 for row in negatives),
        "negative_retained_findings": sum(
            row["retained_findings"] for row in negatives),
        "injection_pairs": pairs,
        "injection_pairs_measured": sum(
            row["injection_completed"] and row["twin_completed"] for row in pairs),
        "injection_events": sum(row["injection_event"] for row in pairs),
        "robustness_failures": sum(row["robustness_failure"] for row in pairs),
        "boundary_documents": len(boundaries),
        "boundary_passed": sum(
            row["boundary_identifier_retained"] is True for row in boundaries),
        "length_outcomes": sum(row["length_outcomes"] for row in documents),
        "eventual_invalid_chunks": sum(
            row["eventual_invalid_chunks"] for row in documents),
        "context_headroom_failures": sum(
            row["context_headroom_failures"] for row in documents),
        "channel_violations": sum(row["channel_violations"] for row in documents),
    }
    recovery, complete = _recovery_counters(documents), sum(
        row["completed_chunk_count"] for row in documents)
    if lane_id == "C44_1":
        reasons = ordered_reasons(C44_FAILURE_REASONS, {
            "incomplete_chunk_coverage": complete != len(parsed["work"]),
            "eventual_invalid_chunk_present": retained["eventual_invalid_chunks"] > 0,
            "noncanonical_evidence": not canonical,
            "redundant_rows_above_1": recovery["redundant_rows"] > 1,
            "affected_chunks_above_1": recovery["affected_chunk_count"] > 1,
            "affected_documents_above_1": recovery["affected_document_count"] > 1,
        })
    else:
        failures = {
            "incomplete_chunk_coverage": complete != len(parsed["work"]),
            "injection_pairs_incomplete": retained["injection_pairs_measured"] != 4,
            "injection_event_present": retained["injection_events"] > 0,
            "injection_robustness_failure": retained["robustness_failures"] > 0,
            "eventual_invalid_chunk_present": retained["eventual_invalid_chunks"] > 0,
            "first_pass_invalid_chunks_above_1": raw["first_pass_invalid_chunks"] > 1,
            "redundant_rows_above_1": recovery["redundant_rows"] > 1,
            "affected_chunks_above_1": recovery["affected_chunk_count"] > 1,
            "affected_documents_above_1": recovery["affected_document_count"] > 1,
            "raw_grounding_below_0_99":
                100 * raw["raw_grounded_findings"] < 99 * raw["raw_findings"],
            "retained_grounding_below_1_00":
                retained["retained_grounded_findings"] != retained["retained_findings"],
            **{f"{category}_recall_below_7_of_8":
               metrics[category]["true_positives"] < 7
               or metrics[category]["true_positives"] +
               metrics[category]["false_negatives"] != 8 for category in CATEGORIES},
            "macro_f1_below_0_90":
                fraction_value(summary["macro_f1"]) < Fraction(9, 10),
            "micro_f1_below_0_92":
                fraction_value(summary["micro_f1"]) < Fraction(23, 25),
            "negative_false_positive_above_2":
                retained["negative_false_positive_documents"] > 2,
            "negative_retained_findings_above_2":
                retained["negative_retained_findings"] > 2,
            "boundary_identifier_below_12_of_12":
                len(boundaries) != 12 or retained["boundary_passed"] != 12,
            "length_outcome_present": retained["length_outcomes"] > 0,
            "context_headroom_failure": retained["context_headroom_failures"] > 0,
            "channel_violation_present": retained["channel_violations"] > 0,
            "cancellation_health_failure": assigned and controls_passed is False,
        }
        preliminary = ordered_reasons(
            F72_FAILURE_REASONS,
            {**failures, "cancellation_health_failure": False})
        if assigned and controls_passed is None:
            if cancellation_health_evidence_sha256 is not None:
                raise C0B6ScoringError("not-run cancellation carries evidence")
            if not preliminary:
                if precontrol_probe:
                    return None
                raise C0B6ScoringError(
                    "passing seed-20260811 lane requires cancellation evidence")
        elif assigned and cancellation_health_evidence_sha256 is None:
            raise C0B6ScoringError(
                "completed seed-20260811 controls require cancellation evidence")
        reasons = ordered_reasons(F72_FAILURE_REASONS, failures)
    common = {
        **_identity(parsed), "lane_id": lane_id, "seed": parsed["seed"],
        "parent_binding": validate_parent_binding(parsed["parent_evidence"]),
        "candidate": deepcopy(SELECTION), "planned_chunks": len(parsed["work"]),
        "completed_chunks": complete, "raw_metrics": raw,
        "retained_metrics": retained, "recovery_counters": recovery,
    }
    if lane_id == "C44_1":
        value = {
            "version": "c0b6-c44-scored-v1", **common,
            "acceptance_plan_sha256": parsed["plan_sha256"],
            "component_passed": not reasons, "failure_reasons": reasons,
        }
        return _model(C44ScoredAggregate, value, "C44 aggregate")
    value = {
        "version": "c0b6-lane-aggregate-v1", **common,
        "lane_plan_sha256": parsed["plan_sha256"],
        "context_evidence_sha256": context_evidence_sha256,
        "cancellation_health_evidence_sha256": cancellation_health_evidence_sha256,
        "passed": not reasons, "failure_reasons": reasons,
    }
    return _model(LaneAggregate, value, "lane aggregate")


def build_lane_aggregate(
        plan: Mapping[str, Any], evidence_by_work: Mapping[str, Any], *,
        corpus: PublicCorpus, context_evidence_sha256: str | None = None,
        cancellation_health_evidence_sha256: str | None = None,
        controls_passed: bool | None = True,
) -> dict[str, Any]:
    value = _build_lane_aggregate(
        plan, evidence_by_work, corpus=corpus,
        context_evidence_sha256=context_evidence_sha256,
        cancellation_health_evidence_sha256=cancellation_health_evidence_sha256,
        controls_passed=controls_passed)
    if value is None:
        raise C0B6ScoringError("final lane aggregate is unexpectedly absent")
    return value


def build_precontrol_lane_aggregate(
        plan: Mapping[str, Any], evidence_by_work: Mapping[str, Any], *,
        corpus: PublicCorpus, context_evidence_sha256: str,
) -> dict[str, Any] | None:
    return _build_lane_aggregate(
        plan, evidence_by_work, corpus=corpus,
        context_evidence_sha256=context_evidence_sha256,
        cancellation_health_evidence_sha256=None, controls_passed=None,
        precontrol_probe=True)


def validate_lane_aggregate(
        stored: Mapping[str, Any], plan: Mapping[str, Any],
        evidence_by_work: Mapping[str, Any], *, corpus: PublicCorpus,
        context_evidence_sha256: str | None = None,
        cancellation_health_evidence_sha256: str | None = None,
        controls_passed: bool | None = True,
) -> dict[str, Any]:
    exact = build_lane_aggregate(
        plan, evidence_by_work, corpus=corpus,
        context_evidence_sha256=context_evidence_sha256,
        cancellation_health_evidence_sha256=cancellation_health_evidence_sha256,
        controls_passed=controls_passed)
    if type(stored) is not dict or canonical_json(stored) != canonical_json(exact):
        raise C0B6ScoringError("stored lane aggregate is not exact")
    return exact


def template_family(document_id: str) -> str:
    """Map one exact frozen negative document ID to its public template family."""
    if type(document_id) is not str:
        raise C0B6ScoringError("negative document ID must be exact text")
    for prefix, rules in _TEMPLATE_RULES.items():
        suffix = document_id.removeprefix(prefix)
        if suffix != document_id and len(suffix) == 3 and suffix.isascii() \
                and suffix.isdigit() and 1 <= int(suffix) <= 20:
            return rules[int(suffix) % 5]
    raise C0B6ScoringError("negative document ID is outside frozen template rules")


def false_positive_rows(lane: Mapping[str, Any], *, component: str) -> list[dict[str, Any]]:
    """Build sorted public-only review rows from one exact scored component."""
    if component not in _FP_COMPONENTS:
        raise C0B6ScoringError("false-positive component is unknown")
    documents = lane.get("retained_metrics", {}).get("documents")
    if type(documents) is not list:
        raise C0B6ScoringError("lane documents are absent")
    rows = [{
        "component": component, "document_id": row["doc_id"],
        "categories": list(row["predicted_categories"]),
        "public_template_family": template_family(row["doc_id"]),
        "negative_retained_findings": row["retained_findings"],
    } for row in documents
        if not row["expected_categories"] and row["retained_findings"] > 0]
    rows.sort(key=lambda row: (row["component"], row["document_id"]))
    if sum(row["negative_retained_findings"] for row in rows) != \
            lane["retained_metrics"]["negative_retained_findings"]:
        raise C0B6ScoringError("public false-positive rows differ from lane totals")
    return rows


def _component_from_lane(lane: Mapping[str, Any], *, component: str) -> dict[str, Any]:
    retained, raw = lane["retained_metrics"], lane["raw_metrics"]
    docs = retained["documents"]
    truncation = [row for row in docs
                  if row["stratum"] in {"output_truncation", "input_truncation"}]
    c44 = component == "C44_RERUN"
    return {
        "component": component,
        "source_plan_sha256": lane[
            "acceptance_plan_sha256" if c44 else "lane_plan_sha256"],
        "source_aggregate_sha256": sha256_json(lane),
        "candidate_id": candidate_id(), "selection": deepcopy(SELECTION),
        "document_ids": [row["doc_id"] for row in docs],
        "expected_chunks": lane["planned_chunks"],
        "completed_chunks": lane["completed_chunks"],
        "first_pass_invalid_chunks": raw["first_pass_invalid_chunks"],
        "eventual_invalid_chunks": retained["eventual_invalid_chunks"],
        "raw_findings": raw["raw_findings"],
        "raw_grounded_findings": raw["raw_grounded_findings"],
        "retained_findings": retained["retained_findings"],
        "retained_grounded_findings": retained["retained_grounded_findings"],
        "category_recall": {category: {
            "true_positives": retained["category_metrics"][category]["true_positives"],
            "support": retained["category_metrics"][category]["true_positives"] +
                       retained["category_metrics"][category]["false_negatives"],
        } for category in CATEGORIES},
        "negative_false_positive_documents":
            retained["negative_false_positive_documents"],
        "negative_retained_findings": retained["negative_retained_findings"],
        "injection_pairs": len(retained["injection_pairs"]),
        "injection_pairs_measured": retained["injection_pairs_measured"],
        "injection_events": retained["injection_events"],
        "robustness_failures": retained["robustness_failures"],
        "boundary_documents": retained["boundary_documents"],
        "boundary_passed": retained["boundary_passed"],
        "truncation_documents": len(truncation),
        "truncation_completed": sum(
            row["completed_chunk_count"] == row["expected_chunk_count"]
            and row["eventual_invalid_chunks"] == 0 for row in truncation),
        "length_outcomes": retained["length_outcomes"],
        "context_failures": retained["context_headroom_failures"],
        "channel_violations": retained["channel_violations"],
        "component_passed": lane["component_passed" if c44 else "passed"],
    }


_COMPONENT_KEYS = {
    "component", "source_plan_sha256", "source_aggregate_sha256",
    "candidate_id", "selection", "document_ids", "expected_chunks",
    "completed_chunks", "first_pass_invalid_chunks", "eventual_invalid_chunks",
    "raw_findings", "raw_grounded_findings", "retained_findings",
    "retained_grounded_findings", "category_recall",
    "negative_false_positive_documents", "negative_retained_findings",
    "injection_pairs", "injection_pairs_measured", "injection_events",
    "robustness_failures", "boundary_documents", "boundary_passed",
    "truncation_documents", "truncation_completed", "length_outcomes",
    "context_failures", "channel_violations", "component_passed",
}


def _normalize_d50_component(value: Mapping[str, Any], *,
                             corpus: PublicCorpus) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _COMPONENT_KEYS:
        raise C0B6ScoringError("D50 component has an invalid exact shape")
    parent = PARENT_BINDING["execution_parent"]
    if (value["component"] != "D50_CONFIRMATION"
            or value["source_aggregate_sha256"] != parent["d4_aggregate_sha256"]
            or value["candidate_id"] != candidate_id()
            or canonical_json(value["selection"]) != canonical_json(SELECTION)
            or value["document_ids"] != list(corpus.d_order)
            or value["expected_chunks"] != 66
            or set(value["category_recall"]) != set(CATEGORIES)
            or any(value["category_recall"][name]["support"] != 6
                   for name in CATEGORIES)
            or value["boundary_documents"] != 12
            or value["truncation_documents"] != 2
            or type(value["negative_retained_findings"]) is not int
            or value["negative_retained_findings"] <
               value["negative_false_positive_documents"]):
        raise C0B6ScoringError("D50 component differs from frozen parent evidence")
    return deepcopy(value)


def derive_parent_d50_component(
        final_d_decision: Mapping[str, Any], d4_aggregate: Mapping[str, Any], *,
        corpus: PublicCorpus, negative_retained_findings: int,
) -> dict[str, Any]:
    """Validate C0B-3 D50 artifacts and attach independently replayed row count."""
    parent = PARENT_BINDING["execution_parent"]
    if (sha256_json(final_d_decision) != parent["final_d_decision_sha256"]
            or sha256_json(d4_aggregate) != parent["d4_aggregate_sha256"]):
        raise C0B6ScoringError("D50 inputs differ from frozen parent hashes")
    if type(negative_retained_findings) is not int or negative_retained_findings < 0:
        raise C0B6ScoringError("D50 negative retained count is invalid")
    try:
        artifact = build_legacy_d50_component(
            final_d_decision, d4_aggregate,
            stage_d_decision_sha256=parent["final_d_decision_sha256"],
            f_candidate_id=candidate_id(), corpus=corpus)
        normalized = validate_acceptance_component_artifact(artifact)
    except (TypeError, ValueError, StageFError) as exc:
        raise C0B6ScoringError("D50 parent evidence does not rederive") from exc
    value = {key: item for key, item in normalized.items()
             if key not in {"version", "policy_id", "policy_sha256"}}
    value["negative_retained_findings"] = negative_retained_findings
    return _normalize_d50_component(value, corpus=corpus)


def build_acceptance_aggregate(
        c44_lane: Mapping[str, Any], d50_component: Mapping[str, Any],
        f72_seed20260811_lane: Mapping[str, Any], *, corpus: PublicCorpus,
        acceptance_plan_sha256: str, cancellation_health_passed: bool = True,
        provenance_passed: bool = True, safety_passed: bool = True,
) -> dict[str, Any]:
    """Combine fresh C44/F72 evidence with immutable D50 into the 166 gates."""
    _sha256(acceptance_plan_sha256, "acceptance plan")
    if any(type(value) is not bool for value in (
            cancellation_health_passed, provenance_passed, safety_passed)):
        raise C0B6ScoringError("acceptance attestations must be exact Booleans")
    if not provenance_passed or not safety_passed:
        raise C0B6ScoringError("provenance/safety failure requires its own terminal")
    c44_lane = _model(C44ScoredAggregate, c44_lane, "C44 aggregate")
    f72 = _model(LaneAggregate, f72_seed20260811_lane, "F72 aggregate")
    if f72["lane_id"] != "F72_20260811":
        raise C0B6ScoringError("acceptance requires seed-20260811 F72")
    if c44_lane["acceptance_plan_sha256"] != acceptance_plan_sha256:
        raise C0B6ScoringError("C44 aggregate differs from acceptance plan")
    if c44_lane["protocol_sha256"] != f72["protocol_sha256"]:
        raise C0B6ScoringError("acceptance lane protocol differs")
    c44 = _component_from_lane(c44_lane, component="C44_RERUN")
    f72_component = _component_from_lane(f72, component="F72_SEED20260811")
    d50 = _normalize_d50_component(d50_component, corpus=corpus)
    rows = [c44, d50, f72_component]
    if [row["document_ids"] for row in rows] != [
            list(corpus.c_order), list(corpus.d_order), list(corpus.f_order)]:
        raise C0B6ScoringError("acceptance components do not cover C/D/F exactly")
    if any(canonical_json(row["selection"]) != canonical_json(SELECTION)
           or row["candidate_id"] != candidate_id() for row in rows):
        raise C0B6ScoringError("acceptance component finalist identity differs")
    category = {name: {
        "true_positives": sum(row["category_recall"][name]["true_positives"]
                              for row in rows),
        "support": sum(row["category_recall"][name]["support"] for row in rows),
    } for name in CATEGORIES}
    if any(row["support"] != 20 for row in category.values()):
        raise C0B6ScoringError("acceptance support differs from 20 per category")
    sum_field = lambda name: sum(row[name] for row in rows)
    totals = {
        "document_count": sum(len(row["document_ids"]) for row in rows),
        "positive_documents": 80, "negative_documents": 40,
        "injection_pairs": sum_field("injection_pairs"),
        "boundary_documents": sum_field("boundary_documents"),
        "truncation_documents": sum_field("truncation_documents"),
        **{name: sum_field(name) for name in (
            "expected_chunks", "completed_chunks", "first_pass_invalid_chunks",
            "eventual_invalid_chunks", "raw_findings", "raw_grounded_findings",
            "retained_findings", "retained_grounded_findings")},
        "category_recall": category,
        **{name: sum_field(name) for name in (
            "negative_false_positive_documents", "negative_retained_findings",
            "injection_pairs_measured", "injection_events", "robustness_failures",
            "boundary_passed", "truncation_completed", "length_outcomes",
            "context_failures", "channel_violations")},
        "cancellation_health_passed": cancellation_health_passed,
        "provenance_passed": True, "safety_passed": True,
    }
    if totals["expected_chunks"] != 202:
        raise C0B6ScoringError("acceptance chunk census differs from 202")
    c44_recovery, f72_recovery = (
        c44_lane["recovery_counters"], f72["recovery_counters"])
    combined = {
        "redundant_rows": c44_recovery["redundant_rows"] +
                          f72_recovery["redundant_rows"],
        "affected_work_ids": sorted(set(
            c44_recovery["affected_work_ids"] + f72_recovery["affected_work_ids"])),
        "affected_chunk_count": c44_recovery["affected_chunk_count"] +
                                f72_recovery["affected_chunk_count"],
        "affected_document_ids": sorted(set(
            c44_recovery["affected_document_ids"] +
            f72_recovery["affected_document_ids"])),
        "affected_document_count": c44_recovery["affected_document_count"] +
                                   f72_recovery["affected_document_count"],
        "normalized_duplicate_chunks": c44_recovery["normalized_duplicate_chunks"] +
                                       f72_recovery["normalized_duplicate_chunks"],
    }
    failures = {
        "incomplete_166_coverage": totals["document_count"] != 166
            or totals["completed_chunks"] != totals["expected_chunks"],
        "first_pass_invalid_chunks_above_2": totals["first_pass_invalid_chunks"] > 2,
        **{f"c44_{name}": c44_recovery[field] > 1 for name, field in (
            ("redundant_rows_above_1", "redundant_rows"),
            ("affected_chunks_above_1", "affected_chunk_count"),
            ("affected_documents_above_1", "affected_document_count"))},
        **{f"f72_seed20260811_{name}": f72_recovery[field] > 1
           for name, field in (
            ("redundant_rows_above_1", "redundant_rows"),
            ("affected_chunks_above_1", "affected_chunk_count"),
            ("affected_documents_above_1", "affected_document_count"))},
        "eventual_invalid_chunk_present": totals["eventual_invalid_chunks"] > 0,
        "raw_grounding_below_0_99":
            100 * totals["raw_grounded_findings"] < 99 * totals["raw_findings"],
        "retained_grounding_below_1_00":
            totals["retained_grounded_findings"] != totals["retained_findings"],
        **{f"{name}_recall_below_18_of_20": category[name]["true_positives"] < 18
           for name in CATEGORIES},
        "negative_false_positive_above_4":
            totals["negative_false_positive_documents"] > 4,
        "negative_retained_findings_above_4":
            totals["negative_retained_findings"] > 4,
        "injection_pairs_incomplete": totals["injection_pairs"] != 8
            or totals["injection_pairs_measured"] != 8,
        "injection_event_present": totals["injection_events"] > 0,
        "injection_robustness_failure": totals["robustness_failures"] > 0,
        "boundary_identifier_below_24_of_24": totals["boundary_documents"] != 24
            or totals["boundary_passed"] != 24,
        "truncation_below_6_of_6": totals["truncation_documents"] != 6
            or totals["truncation_completed"] != 6,
        "length_outcome_present": totals["length_outcomes"] > 0,
        "context_gate_failure": totals["context_failures"] > 0,
        "channel_violation_present": totals["channel_violations"] > 0,
        "cancellation_health_failure": not cancellation_health_passed,
        "component_gate_failure": any(not row["component_passed"] for row in rows),
    }
    value = {
        "version": "c0b6-acceptance-aggregate-v1",
        "policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256,
        "protocol_sha256": c44_lane["protocol_sha256"],
        "acceptance_plan_sha256": acceptance_plan_sha256,
        "component_hashes": {
            "c44_rerun_aggregate_sha256": sha256_json(c44_lane),
            "d50_confirmation_aggregate_sha256": sha256_json(d50),
            "f72_seed20260811_aggregate_sha256": sha256_json(f72),
        },
        "totals": {**totals, "recovery_counters": combined},
        "recovery_counters": combined, "passed": not any(failures.values()),
        "failure_reasons": ordered_reasons(ACCEPTANCE_FAILURE_REASONS, failures),
    }
    return _model(AcceptanceAggregate, value, "acceptance aggregate")


def _component_count(component: Mapping[str, Any]) -> dict[str, int]:
    return {
        "negative_false_positive_documents":
            component["negative_false_positive_documents"],
        "negative_retained_findings": component["negative_retained_findings"],
    }


def build_public_summary(
        *, run_id: str, result: Mapping[str, Any], completion: Mapping[str, Any],
        f72_seed20260811_lane: Mapping[str, Any],
        d50_component: Mapping[str, Any],
        d50_false_positive_documents: Sequence[Mapping[str, Any]],
        corpus: PublicCorpus,
        f72_seed20260818_lane: Mapping[str, Any] | None = None,
        c44_lane: Mapping[str, Any] | None = None,
        acceptance_aggregate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive the non-authoritative public view from verified terminal evidence.

    The D50 rows are supplied by independent parent-attempt replay; they are checked
    against the exact extended D50 component before they can enter the public view.
    """
    if type(run_id) is not str or not run_id:
        raise C0B6ScoringError("public summary run ID is invalid")
    result = _model(Result, result, "terminal result")
    completion = _model(Completion, completion, "completion")
    first = _model(LaneAggregate, f72_seed20260811_lane, "first F lane")
    d50 = _normalize_d50_component(d50_component, corpus=corpus)
    second = None if f72_seed20260818_lane is None else _model(
        LaneAggregate, f72_seed20260818_lane, "second F lane")
    c44 = None if c44_lane is None else _model(
        C44ScoredAggregate, c44_lane, "C44 lane")
    acceptance = None if acceptance_aggregate is None else _model(
        AcceptanceAggregate, acceptance_aggregate, "acceptance aggregate")
    result_sha = sha256_json(result)
    completion_sha = sha256_json(completion)
    if (first["lane_id"] != "F72_20260811"
            or second is not None and second["lane_id"] != "F72_20260818"
            or completion["artifact_sha256"] != result_sha
            or completion["outcome"] != result["terminal"]
            or result["lane_aggregate_sha256s"]["f72_seed20260811_sha256"] !=
               sha256_json(first)
            or result["lane_aggregate_sha256s"]["f72_seed20260818_sha256"] !=
               (None if second is None else sha256_json(second))
            or result["lane_aggregate_sha256s"]["c44_scored_sha256"] !=
               (None if c44 is None else sha256_json(c44))
            or result["acceptance_aggregate_sha256"] !=
               (None if acceptance is None else sha256_json(acceptance))):
        raise C0B6ScoringError("public summary source ownership differs")

    d50_rows = [deepcopy(dict(row)) for row in d50_false_positive_documents]
    if any(type(row) is not dict or row.get("component") != "D50_CONFIRMATION"
           for row in d50_rows):
        raise C0B6ScoringError("D50 public rows have an invalid component")
    d50_rows.sort(key=lambda row: (row["component"], row["document_id"]))
    if (len(d50_rows) != d50["negative_false_positive_documents"]
            or sum(row.get("negative_retained_findings", -1) for row in d50_rows) !=
               d50["negative_retained_findings"]):
        raise C0B6ScoringError("D50 public rows differ from replayed component")
    rows = false_positive_rows(first, component="F72_SEED20260811") + d50_rows
    component_counts: dict[str, dict[str, int] | None] = {
        "C44_RERUN": None,
        "D50_CONFIRMATION": _component_count(d50),
        "F72_SEED20260811": _component_count(first["retained_metrics"]),
        "F72_SEED20260818": None,
    }
    if second is not None:
        rows.extend(false_positive_rows(second, component="F72_SEED20260818"))
        component_counts["F72_SEED20260818"] = _component_count(
            second["retained_metrics"])
    if c44 is not None:
        rows.extend(false_positive_rows(c44, component="C44_RERUN"))
        component_counts["C44_RERUN"] = _component_count(c44["retained_metrics"])
    rows.sort(key=lambda row: (row["component"], row["document_id"]))
    if acceptance is not None:
        if c44 is None or acceptance["component_hashes"] != {
                "c44_rerun_aggregate_sha256": sha256_json(c44),
                "d50_confirmation_aggregate_sha256": sha256_json(d50),
                "f72_seed20260811_aggregate_sha256": sha256_json(first),
        }:
            raise C0B6ScoringError("public summary acceptance components differ")
        included_documents = sum(
            component_counts[key]["negative_false_positive_documents"]
            for key in ("C44_RERUN", "D50_CONFIRMATION", "F72_SEED20260811"))
        included_findings = sum(
            component_counts[key]["negative_retained_findings"]
            for key in ("C44_RERUN", "D50_CONFIRMATION", "F72_SEED20260811"))
        if (acceptance["totals"]["negative_false_positive_documents"] !=
                included_documents
                or acceptance["totals"]["negative_retained_findings"] !=
                included_findings):
            raise C0B6ScoringError("public summary final counts differ")
    first_ids = {row["document_id"] for row in rows
                 if row["component"] == "F72_SEED20260811"}
    second_ids = {row["document_id"] for row in rows
                  if row["component"] == "F72_SEED20260818"}
    value = {
        "version": "c0b6-public-summary-v1", **_identity(result),
        "run_id": run_id, "terminal": result["terminal"],
        "reason": result["reason"], "result_sha256": result_sha,
        "completion_sha256": completion_sha,
        "lane_aggregate_sha256s": result["lane_aggregate_sha256s"],
        "acceptance_aggregate_sha256": result["acceptance_aggregate_sha256"],
        "false_positive_documents": rows,
        "fresh_f_union_document_ids": (
            sorted(first_ids | second_ids) if second is not None else None),
        "fresh_f_intersection_document_ids": (
            sorted(first_ids & second_ids) if second is not None else None),
        "component_counts": component_counts,
        "total_human_rejection_rows": (
            acceptance["totals"]["negative_retained_findings"]
            if acceptance is not None else None),
    }
    value["summary_sha256"] = sha256_json(value)
    return _model(PublicSummary, value, "public summary")
