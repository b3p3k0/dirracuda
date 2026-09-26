"""Pure planning for the C0B-5 assistive review-budget confirmation.

The planner is intentionally isolated from checkpoint and transport state.  It freezes
all scored work and controls before execution and can independently rebuild every
identity from the public corpus and the protected run nonce key.

DISPOSITION: benchmark-only; remove after the accepted confirmation is handed to C1.
"""
from __future__ import annotations

import hashlib
import hmac
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from . import c0b2_plan as legacy_plan
from . import chunker
from .c0b2_public_schema import CandidateSelection, sha256_json, stage_f_candidate_id
from .c0b2_public_scoring import document_view_identity
from .c0b2_schema import canonical_json, stable_hash, worksheet_schema
from .c0b2_stage_f_plan import PublicCorpus
from .c0b2_transport import RequestSpec, request_spec_hash
from .c0b4_answer import build_prompt
from .c0b5_lineage import FROZEN_PARENT_BINDING, validate_parent_binding
from .c0b5_policy import POLICY_ID, POLICY_SHA256
from .c0b5_schema import (
    AcceptancePlan, C0B5PublicWork, CancellationControl, ContextControl,
    HealthControl, LanePlan, MasterPlan,
)

LANE_ORDER = ("F72_20260804", "F72_20260811", "C44_1")
LANE_CONFIG = {
    "F72_20260804": (
        20260804, "F_SEED_20260804", "c0b5-f72-20260804-nonce-v1", 92),
    "F72_20260811": (
        20260811, "F_SEED_20260811", "c0b5-f72-20260811-nonce-v1", 92),
    "C44_1": (1, "F_ACCEPTANCE", "c0b5-acceptance-c44-nonce-v1", 44),
}
SELECTION = {
    "model": "qwen3.6:27b",
    "model_digest": "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e",
    "worksheet": "v2", "chunk_chars": 8000, "overlap": 256,
    "num_ctx": 8192, "num_predict": 1024,
}

PARENT_BINDING = FROZEN_PARENT_BINDING
_WORK_KEYS = {
    "stage", "phase", "plan_key", "budget_stage", "activation_group_id",
    "candidate_id", "cell_id", "work_id", "model", "model_digest",
    "worksheet", "doc_id", "view_id", "document_sha256", "chunk_chars",
    "overlap", "num_ctx", "num_predict", "seed", "chunk_index",
    "chunk_sha256", "nonce", "prompt_sha256", "request_sha256",
}
_PLAN_KEYS = {
    "version", "policy_id", "policy_sha256", "protocol_sha256", "lane_id",
    "seed", "candidate", "parent_evidence", "work", "plan_sha256",
}


class C0B5PlanError(RuntimeError):
    """A parent, fixture, candidate, or derived request is not exact."""


@dataclass(frozen=True)
class ResolvedC0B5Control:
    """Exact offline inputs for one frozen seed-20260804 control."""

    control: Mapping[str, Any]
    request_spec: RequestSpec
    source_work_id: str
    source_chunk: str | None


def _sha256(value: Any, label: str) -> str:
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise C0B5PlanError(f"{label} must be lowercase SHA-256")
    return value


def _identity(protocol_sha256: str) -> dict[str, str]:
    return {
        "policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256,
        "protocol_sha256": _sha256(protocol_sha256, "protocol"),
    }


def _self_digest(value: Mapping[str, Any], field: str) -> str:
    body = dict(value)
    body.pop(field, None)
    return sha256_json(body)


def candidate_id() -> str:
    """Return the finalist identity inherited from the C0B-3 D4 decision."""
    normalized = CandidateSelection.model_validate(
        SELECTION, strict=True).model_dump(mode="json")
    return stage_f_candidate_id(
        normalized,
        PARENT_BINDING["execution_parent"]["final_d_decision_sha256"],
    )


def _generation_config(seed: int) -> dict[str, Any]:
    return {
        "keep_alive": legacy_plan.KEEP_ALIVE,
        "options": {
            "min_p": 0.0, "num_ctx": 8192, "num_predict": 1024,
            "repeat_last_n": 0, "repeat_penalty": 1.0, "seed": seed,
            "temperature": 0.0, "top_k": 1, "top_p": 1.0,
        },
        "think": False,
    }


def _request_payload(prompt: str, seed: int) -> dict[str, Any]:
    config = _generation_config(seed)
    return {
        "model": SELECTION["model"],
        "messages": [{"role": "user", "content": prompt}],
        "stream": True, "format": worksheet_schema("v2"),
        "options": config["options"], "think": False,
        "keep_alive": config["keep_alive"],
    }


def _pair_id(document: Any) -> str | None:
    if document.stratum == "injection":
        return document.doc_id
    if document.stratum == "injection_clean_twin":
        return "inj_" + document.doc_id.removeprefix("inj_twin_")
    return None


def _derive_nonce(key: bytes, *, domain: str, view_identity: str,
                  seed: int) -> str:
    message = canonical_json({
        "candidate_id": candidate_id(), "document_view_identity": view_identity,
        "domain": domain, "seed": seed, "worksheet": "v2",
    })
    return "FENCE_" + hmac.new(key, message, hashlib.sha256).hexdigest()[:32].upper()


def _group_id(lane_id: str) -> str | None:
    if lane_id == "C44_1":
        return None
    plan_key = LANE_CONFIG[lane_id][1]
    return stable_hash({
        "candidate_id": candidate_id(), "domain": "c0b5-stage-f-group-v1",
        "plan_key": plan_key,
    })


def _cell_id(lane_id: str, seed: int) -> str:
    phase = LANE_CONFIG[lane_id][1]
    return stable_hash({
        "budget_stage": "F", "candidate_id": candidate_id(),
        "chunk_chars": 8000, "domain": "c0b5-public-cell-v1",
        "num_ctx": 8192, "num_predict": 1024, "overlap": 256,
        "phase": phase, "seed": seed,
    })


def _work_id(*, cell_id: str, lane_id: str, row: Mapping[str, Any]) -> str:
    return stable_hash({
        "cell_id": cell_id, "chunk_index": row["chunk_index"],
        "chunk_sha256": row["chunk_sha256"], "doc_id": row["doc_id"],
        "document_sha256": row["document_sha256"],
        "domain": "c0b5-public-work-v1", "nonce": row["nonce"],
        "plan_key": row["plan_key"], "request_sha256": row["request_sha256"],
        "view_id": row["view_id"],
    })


def _validate_work(row: Mapping[str, Any], *, lane_id: str, seed: int,
                   plan_key: str) -> dict[str, Any]:
    if type(row) is not dict or set(row) != _WORK_KEYS:
        raise C0B5PlanError("generated work has an inexact shape")
    if any((row["stage"] != "F", row["budget_stage"] != "F",
            row["phase"] != plan_key, row["plan_key"] != plan_key,
            row["seed"] != seed, row["candidate_id"] != candidate_id(),
            row["model"] != SELECTION["model"],
            row["model_digest"] != SELECTION["model_digest"],
            row["worksheet"] != "v2", row["chunk_chars"] != 8000,
            row["overlap"] != 256, row["num_ctx"] != 8192,
            row["num_predict"] != 1024)):
        raise C0B5PlanError("generated work differs from its frozen configuration")
    for field in ("candidate_id", "cell_id", "work_id", "document_sha256",
                  "chunk_sha256", "prompt_sha256", "request_sha256"):
        _sha256(row[field], field)
    if (type(row["nonce"]) is not str or not row["nonce"].startswith("FENCE_")
            or len(row["nonce"]) != 38):
        raise C0B5PlanError("generated work nonce is invalid")
    expected_group = _group_id(lane_id)
    if row["activation_group_id"] != expected_group:
        raise C0B5PlanError("generated work activation group differs")
    if row["cell_id"] != _cell_id(lane_id, seed):
        raise C0B5PlanError("generated work cell identity differs")
    if row["work_id"] != _work_id(
            cell_id=row["cell_id"], lane_id=lane_id, row=row):
        raise C0B5PlanError("generated work identity differs")
    try:
        return C0B5PublicWork.model_validate(
            row, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise C0B5PlanError("generated work violates C0B-5 schema") from exc


def _work_rows(lane_id: str, corpus: PublicCorpus,
               run_nonce_key: bytes) -> list[dict[str, Any]]:
    if type(run_nonce_key) is not bytes or len(run_nonce_key) != 32:
        raise C0B5PlanError("run nonce key must contain exactly 32 bytes")
    if lane_id not in LANE_CONFIG:
        raise C0B5PlanError("unknown C0B-5 lane")
    seed, plan_key, nonce_domain, expected = LANE_CONFIG[lane_id]
    ids: Sequence[str] = corpus.c_order if lane_id == "C44_1" else corpus.f_order
    documents, cell = corpus.by_id(), _cell_id(lane_id, seed)
    rows: list[dict[str, Any]] = []
    for doc_id in ids:
        document = documents.get(doc_id)
        if document is None:
            raise C0B5PlanError(f"public corpus lacks {doc_id}")
        derived = lane_id != "C44_1" and document.stratum == "boundary"
        source, view_id = document.source_for(8000, derived=derived)
        view_identity = document_view_identity(
            doc_id=doc_id, document_sha256=document.document_sha256,
            pair_id=_pair_id(document), view_sha256=view_id)
        nonce = _derive_nonce(
            run_nonce_key, domain=nonce_domain,
            view_identity=view_identity, seed=seed)
        if nonce in source:
            raise C0B5PlanError(f"derived nonce occurs in source {doc_id}")
        chunks = chunker.chunk(source, chunk_chars=8000, overlap_chars=256)
        if lane_id == "C44_1" and len(chunks) != 1:
            raise C0B5PlanError(f"C44 document is not exactly one chunk: {doc_id}")
        for item in chunks:
            prompt = build_prompt("v2", item.text, nonce)
            request_sha256 = stable_hash(_request_payload(prompt, seed))
            row = {
                "stage": "F", "phase": plan_key, "plan_key": plan_key,
                "budget_stage": "F", "activation_group_id": _group_id(lane_id),
                "candidate_id": candidate_id(), "cell_id": cell,
                "work_id": "0" * 64, "model": SELECTION["model"],
                "model_digest": SELECTION["model_digest"], "worksheet": "v2",
                "doc_id": doc_id, "view_id": view_id,
                "document_sha256": document.document_sha256,
                "chunk_chars": 8000, "overlap": 256, "num_ctx": 8192,
                "num_predict": 1024, "seed": seed, "chunk_index": item.index,
                "chunk_sha256": hashlib.sha256(item.text.encode()).hexdigest(),
                "nonce": nonce,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "request_sha256": request_sha256,
            }
            row["work_id"] = _work_id(cell_id=cell, lane_id=lane_id, row=row)
            rows.append(_validate_work(
                row, lane_id=lane_id, seed=seed, plan_key=plan_key))
    if len(rows) != expected or len({row["work_id"] for row in rows}) != expected:
        raise C0B5PlanError(f"{lane_id} work census differs from {expected}")
    return rows


def _control_nonce(key: bytes, source_identity: str, *, domain: str) -> str:
    return _derive_nonce(
        key, domain=domain, view_identity=source_identity, seed=20260804)


def _control_plan(first_lane_work: Sequence[Mapping[str, Any]],
                  corpus: PublicCorpus, run_nonce_key: bytes,
                  protocol_sha256: str) -> dict[str, Any]:
    identity, candidate = _identity(protocol_sha256), candidate_id()
    config_sha256 = sha256_json(_generation_config(20260804))
    context_spec = RequestSpec(
        kind="ps", expected_model=SELECTION["model"],
        expected_digest=SELECTION["model_digest"], min_context=8192,
        purpose="c0b5_stage_f_candidate_context",
        config_sha256=config_sha256)
    context = {
        "version": "c0b5-context-control-v1", **identity,
        "control_id": stable_hash({
            "domain": "c0b5-context-control-v1", "candidate_id": candidate,
            "config_sha256": config_sha256,
            "payload_sha256": request_spec_hash(context_spec),
        }),
        "kind": "context_probe", "lane_id": "F72_20260804",
        "purpose": "c0b5_stage_f_candidate_context", "candidate_id": candidate,
        "model": SELECTION["model"], "model_digest": SELECTION["model_digest"],
        "config_sha256": config_sha256,
        "prompt_sha256": first_lane_work[0]["prompt_sha256"],
        "minimum_context_length": 8192,
        "trigger_rule": "first_bounded_http_terminal_seed20260804",
        "payload_sha256": request_spec_hash(context_spec),
    }
    matches = [row for row in first_lane_work
               if row["doc_id"] == "pos_pii_013" and row["chunk_index"] == 0]
    if len(matches) != 1:
        raise C0B5PlanError("cancellation source work is not unique")
    source_work = matches[0]
    document = corpus.by_id()["pos_pii_013"]
    source_identity = document_view_identity(
        doc_id=document.doc_id, document_sha256=document.document_sha256)
    chunks = chunker.chunk(document.text, chunk_chars=8000, overlap_chars=256)
    if len(chunks) != 1:
        raise C0B5PlanError("cancellation source is not exactly one chunk")
    source = chunks[0].text
    nonces = {
        name: _control_nonce(
            run_nonce_key, source_identity, domain=f"c0b5-{name}-nonce-v1")
        for name in ("cancellation", "health")
    }
    if len({source_work["nonce"], *nonces.values()}) != 3 \
            or any(nonce in source for nonce in nonces.values()):
        raise C0B5PlanError("control nonce/source isolation failed")
    prompts = {name: build_prompt("v2", source, nonce)
               for name, nonce in nonces.items()}
    requests = {name: stable_hash(_request_payload(prompt, 20260804))
                for name, prompt in prompts.items()}
    cancellation = {
        "version": "c0b5-cancellation-control-v1", **identity,
        "control_id": stable_hash({
            "domain": "c0b5-cancellation-control-v1", "candidate_id": candidate,
            "request_sha256": requests["cancellation"],
        }),
        "kind": "cancellation_probe", "lane_id": "F72_20260804",
        "candidate_id": candidate, "seed": 20260804,
        "prompt_sha256": hashlib.sha256(prompts["cancellation"].encode()).hexdigest(),
        "source_doc_id": "pos_pii_013", "chunk_index": 0,
        "nonce": nonces["cancellation"],
        "request_sha256": requests["cancellation"], "deadline_seconds": 600,
        "max_close_after_first_byte_ms": 5000, "health_not_before_ms": 2000,
    }
    health = {
        "version": "c0b5-health-control-v1", **identity,
        "control_id": stable_hash({
            "domain": "c0b5-health-control-v1", "candidate_id": candidate,
            "nonce": nonces["health"], "request_sha256": requests["health"],
        }),
        "kind": "cancellation_health", "lane_id": "F72_20260804",
        "candidate_id": candidate, "seed": 20260804,
        "prompt_sha256": hashlib.sha256(prompts["health"].encode()).hexdigest(),
        "source_doc_id": "pos_pii_013", "chunk_index": 0,
        "nonce": nonces["health"],
        "health_work_id": stable_hash({
            "domain": "c0b5-health-work-v1", "candidate_id": candidate,
            "request_sha256": requests["health"],
        }),
        "request_sha256": requests["health"], "deadline_seconds": 600,
    }
    try:
        return {
            "context": ContextControl.model_validate(
                context, strict=True).model_dump(mode="json"),
            "cancellation": CancellationControl.model_validate(
                cancellation, strict=True).model_dump(mode="json"),
            "health": HealthControl.model_validate(
                health, strict=True).model_dump(mode="json"),
        }
    except (TypeError, ValueError) as exc:
        raise C0B5PlanError("generated controls violate C0B-5 schema") from exc


def build_lane_plan(lane_id: str, *, corpus: PublicCorpus, run_nonce_key: bytes,
                    protocol_sha256: str,
                    parent_binding: Mapping[str, Any] = PARENT_BINDING) -> dict[str, Any]:
    """Build one self-digested lane plan from exact public inputs."""
    parent = validate_parent_binding(parent_binding)
    manifest = parent["execution_parent"]["master_manifest_sha256"]
    if corpus.master_manifest_sha256 != manifest or lane_id not in LANE_CONFIG:
        raise C0B5PlanError("lane parent, corpus, or identity is invalid")
    seed = LANE_CONFIG[lane_id][0]
    value = {
        "version": ("c0b5-acceptance-plan-v1" if lane_id == "C44_1"
                    else "c0b5-lane-plan-v1"),
        **_identity(protocol_sha256), "lane_id": lane_id, "seed": seed,
        "candidate": deepcopy(SELECTION), "parent_evidence": parent,
        "work": _work_rows(lane_id, corpus, run_nonce_key),
    }
    value["plan_sha256"] = _self_digest(value, "plan_sha256")
    parsed = _validate_lane_plan(value, corpus)
    model = AcceptancePlan if lane_id == "C44_1" else LanePlan
    try:
        return model.model_validate(parsed, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise C0B5PlanError("generated lane plan violates C0B-5 schema") from exc


def _validate_lane_plan(value: Mapping[str, Any], corpus: PublicCorpus) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _PLAN_KEYS:
        raise C0B5PlanError("lane plan has an inexact shape")
    lane_id = value.get("lane_id")
    if lane_id not in LANE_CONFIG:
        raise C0B5PlanError("lane plan has an unknown identity")
    seed, plan_key, _domain, count = LANE_CONFIG[lane_id]
    version = "c0b5-acceptance-plan-v1" if lane_id == "C44_1" \
        else "c0b5-lane-plan-v1"
    if (value["version"] != version or value["seed"] != seed
            or value["candidate"] != SELECTION
            or validate_parent_binding(value["parent_evidence"]) != PARENT_BINDING
            or type(value["work"]) is not list or len(value["work"]) != count
            or len({row.get("work_id") for row in value["work"]}) != count
            or value["plan_sha256"] != _self_digest(value, "plan_sha256")):
        raise C0B5PlanError("lane plan differs from its frozen identity")
    _identity(value["protocol_sha256"])
    rows = [_validate_work(row, lane_id=lane_id, seed=seed, plan_key=plan_key)
            for row in value["work"]]
    order = corpus.c_order if lane_id == "C44_1" else corpus.f_order
    if list(dict.fromkeys(row["doc_id"] for row in rows)) != list(order):
        raise C0B5PlanError("lane work order differs from public corpus")
    return deepcopy(value)


def build_master_plan(*, corpus: PublicCorpus, run_nonce_key: bytes,
                      protocol_sha256: str,
                      parent_binding: Mapping[str, Any] = PARENT_BINDING) -> dict[str, Any]:
    """Freeze both F72 lanes, C44, and all controls before contact."""
    parent = validate_parent_binding(parent_binding)
    lanes = [build_lane_plan(
        lane, corpus=corpus, run_nonce_key=run_nonce_key,
        protocol_sha256=protocol_sha256, parent_binding=parent)
        for lane in LANE_ORDER]
    value = {
        "version": "c0b5-master-plan-v1", **_identity(protocol_sha256),
        "parent_binding": parent, "lane_order": list(LANE_ORDER),
        "lane_plans": [{"plan_sha256": row["plan_sha256"], "payload": row}
                       for row in lanes[:2]],
        "control_plan": _control_plan(
            lanes[0]["work"], corpus, run_nonce_key, protocol_sha256),
        "acceptance_template": {
            "plan_sha256": lanes[2]["plan_sha256"], "payload": lanes[2]},
    }
    try:
        return MasterPlan.model_validate(
            value, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise C0B5PlanError("generated master violates C0B-5 schema") from exc


def validate_master_plan(value: Mapping[str, Any], *, corpus: PublicCorpus,
                         run_nonce_key: bytes) -> dict[str, Any]:
    """Independently rebuild and byte-compare a stored C0B-5 master plan."""
    if type(value) is not dict:
        raise C0B5PlanError("master plan must be an exact object")
    expected = build_master_plan(
        corpus=corpus, run_nonce_key=run_nonce_key,
        protocol_sha256=_sha256(value.get("protocol_sha256"), "protocol"),
        parent_binding=value.get("parent_binding"))
    if canonical_json(value) != canonical_json(expected):
        raise C0B5PlanError("master plan differs from independent re-derivation")
    return expected


def lane_from_master(master: Mapping[str, Any], lane_id: str, *,
                     corpus: PublicCorpus, run_nonce_key: bytes) -> dict[str, Any]:
    parsed = validate_master_plan(master, corpus=corpus, run_nonce_key=run_nonce_key)
    envelopes = list(parsed["lane_plans"]) + [parsed["acceptance_template"]]
    matches = [row["payload"] for row in envelopes
               if row["payload"]["lane_id"] == lane_id]
    if len(matches) != 1:
        raise C0B5PlanError("unknown or duplicate lane")
    return matches[0]


def _resolve_owned_work(owner: Mapping[str, Any], row: Mapping[str, Any], *,
                        corpus: PublicCorpus) -> dict[str, Any]:
    lane_id = owner["lane_id"]
    _validate_lane_plan(owner, corpus)
    document = corpus.by_id().get(row["doc_id"])
    if document is None or document.document_sha256 != row["document_sha256"]:
        raise C0B5PlanError("work differs from its public document")
    source, view_id = document.source_for(8000, derived=row["view_id"] is not None)
    chunks = chunker.chunk(source, chunk_chars=8000, overlap_chars=256)
    if view_id != row["view_id"] or row["chunk_index"] >= len(chunks):
        raise C0B5PlanError("work source view or chunk is absent")
    item = chunks[row["chunk_index"]]
    prompt = build_prompt("v2", item.text, row["nonce"])
    payload = _request_payload(prompt, row["seed"])
    if (hashlib.sha256(item.text.encode()).hexdigest() != row["chunk_sha256"]
            or hashlib.sha256(prompt.encode()).hexdigest() != row["prompt_sha256"]
            or stable_hash(payload) != row["request_sha256"]):
        raise C0B5PlanError("resolved request differs from frozen work")
    return {"lane": deepcopy(owner), "work": deepcopy(row), "source": source,
            "chunk_text": item.text, "chunk_start": item.start,
            "prompt": prompt, "payload": payload, "lane_id": lane_id}


def resolve_work(master: Mapping[str, Any], work_id: str, *, corpus: PublicCorpus,
                 run_nonce_key: bytes) -> dict[str, Any]:
    parsed = validate_master_plan(master, corpus=corpus, run_nonce_key=run_nonce_key)
    envelopes = list(parsed["lane_plans"]) + [parsed["acceptance_template"]]
    matches = [(owner["payload"], row) for owner in envelopes
               for row in owner["payload"]["work"] if row["work_id"] == work_id]
    if len(matches) != 1:
        raise C0B5PlanError("unknown or duplicate work identity")
    return _resolve_owned_work(*matches[0], corpus=corpus)


def request_spec_for_work(master: Mapping[str, Any], work_id: str, *,
                          corpus: PublicCorpus, run_nonce_key: bytes) -> RequestSpec:
    resolved = resolve_work(
        master, work_id, corpus=corpus, run_nonce_key=run_nonce_key)
    row = resolved["work"]
    return RequestSpec(
        kind="chat", payload=resolved["payload"], worksheet="v2",
        expected_model=row["model"], expected_digest=row["model_digest"])


def _resolve_control(parsed: Mapping[str, Any], control: Mapping[str, Any], *,
                     corpus: PublicCorpus, run_nonce_key: bytes) -> ResolvedC0B5Control:
    first = parsed["lane_plans"][0]["payload"]
    sources = [row for row in first["work"]
               if row["doc_id"] == "pos_pii_013" and row["chunk_index"] == 0]
    if len(sources) != 1:
        raise C0B5PlanError("control source work is not unique")
    if control["kind"] == "context_probe":
        spec = RequestSpec(
            kind="ps", expected_model=SELECTION["model"],
            expected_digest=SELECTION["model_digest"], min_context=8192,
            purpose="c0b5_stage_f_candidate_context",
            config_sha256=sha256_json(_generation_config(20260804)))
        if request_spec_hash(spec) != control["payload_sha256"]:
            raise C0B5PlanError("context control differs from request spec")
        return ResolvedC0B5Control(
            deepcopy(control), spec, first["work"][0]["work_id"], None)
    document = corpus.by_id()["pos_pii_013"]
    source_identity = document_view_identity(
        doc_id=document.doc_id, document_sha256=document.document_sha256)
    name = "cancellation" if control["kind"] == "cancellation_probe" else "health"
    nonce = _control_nonce(
        run_nonce_key, source_identity, domain=f"c0b5-{name}-nonce-v1")
    chunk = chunker.chunk(document.text, chunk_chars=8000, overlap_chars=256)[0].text
    prompt = build_prompt("v2", chunk, nonce)
    payload = _request_payload(prompt, 20260804)
    spec = RequestSpec(
        kind="chat", payload=payload, worksheet="v2",
        expected_model=SELECTION["model"], expected_digest=SELECTION["model_digest"],
        cancel_on_first_content=control["kind"] == "cancellation_probe")
    if (control["nonce"] != nonce
            or control["prompt_sha256"] != hashlib.sha256(prompt.encode()).hexdigest()
            or control["request_sha256"] != request_spec_hash(spec)
            or nonce == sources[0]["nonce"]):
        raise C0B5PlanError("resolved control differs from frozen request")
    return ResolvedC0B5Control(
        deepcopy(control), spec, sources[0]["work_id"], chunk)


def resolve_controls(master: Mapping[str, Any], *, corpus: PublicCorpus,
                     run_nonce_key: bytes) -> dict[str, ResolvedC0B5Control]:
    parsed = validate_master_plan(master, corpus=corpus, run_nonce_key=run_nonce_key)
    return {name: _resolve_control(
        parsed, control, corpus=corpus, run_nonce_key=run_nonce_key)
            for name, control in parsed["control_plan"].items()}


class C0B5RequestResolver:
    """Invocation-local exact request indexes after one master validation."""

    def __init__(self, master: Mapping[str, Any], *, corpus: PublicCorpus,
                 run_nonce_key: bytes) -> None:
        self.master = validate_master_plan(
            master, corpus=corpus, run_nonce_key=run_nonce_key)
        self.corpus, self.key = corpus, run_nonce_key
        envelopes = list(self.master["lane_plans"]) + [self.master["acceptance_template"]]
        items = [(owner["payload"], row) for owner in envelopes
                 for row in owner["payload"]["work"]]
        self.work = {row["work_id"]: (owner, row) for owner, row in items}
        if len(self.work) != 228:
            raise C0B5PlanError("master work index is duplicate or incomplete")

    def resolve_work(self, work_id: str) -> dict[str, Any]:
        match = self.work.get(work_id)
        if match is None:
            raise C0B5PlanError("unknown C0B-5 work identity")
        return _resolve_owned_work(*match, corpus=self.corpus)

    def request_spec_for_work(self, work_id: str) -> RequestSpec:
        resolved = self.resolve_work(work_id)
        row = resolved["work"]
        return RequestSpec(
            kind="chat", payload=resolved["payload"], worksheet="v2",
            expected_model=row["model"], expected_digest=row["model_digest"])

    def resolve_controls(self) -> dict[str, ResolvedC0B5Control]:
        return {name: _resolve_control(
            self.master, control, corpus=self.corpus, run_nonce_key=self.key)
                for name, control in self.master["control_plan"].items()}


def build_request_resolver(master: Mapping[str, Any], *, corpus: PublicCorpus,
                           run_nonce_key: bytes) -> C0B5RequestResolver:
    return C0B5RequestResolver(master, corpus=corpus, run_nonce_key=run_nonce_key)
