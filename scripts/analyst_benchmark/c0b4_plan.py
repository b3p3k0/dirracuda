"""Pure planning for the C0B-4 grounded-duplicate confirmation.

The module accepts an already verified parent binding and public corpus.  It performs no
filesystem writes, checkpoint access, or network calls.  Every request, nonce, control,
and lane is frozen before execution starts.

DISPOSITION: benchmark-only; remove after the C0B selection is accepted.
"""
from __future__ import annotations

import hashlib
import hmac
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from . import c0b2_plan as legacy_plan
from . import chunker
from .c0b2_public_schema import (
    CandidateSelection, PublicWork, activation_group_id, public_cell_id,
    public_work_id, sha256_json, stage_f_candidate_id,
)
from .c0b2_public_scoring import derive_nonce, document_view_identity
from .c0b2_schema import canonical_json, stable_hash, worksheet_schema
from .c0b2_stage_f_plan import PublicCorpus
from .c0b2_transport import RequestSpec, request_spec_hash
from .c0b4_answer import build_prompt
from .c0b4_schema import (
    AcceptancePlan, CancellationControl, ContextControl, HealthControl, LanePlan,
    MasterPlan,
)

BENCHMARK_PROTOCOL_ID = "c0b4-grounded-duplicate-confirmation-v1"
POLICY_ID = "c0b4-bounded-grounded-dedup-v1"
POLICY_SHA256 = "7475e748165366ed0fb83daf1b6dae21a98d92d1c8faf3e3b7a3513aa3745c43"
LANE_ORDER = ("F72_17", "F72_20260804", "C44_1")
LANE_CONFIG = {
    "F72_17": (17, "F_SEED_17", "F", 92),
    "F72_20260804": (20260804, "F_SEED_20260804", "F", 92),
    "C44_1": (1, "F_ACCEPTANCE", "acceptance-c44", 44),
}
SELECTION = {
    "model": "qwen3.6:27b",
    "model_digest": "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e",
    "worksheet": "v2", "chunk_chars": 8000, "overlap": 256,
    "num_ctx": 8192, "num_predict": 1024,
}

PARENT_BINDING = {
    "run_id": "c0b3-20260809-154924-19afcaab26984160f20ec075",
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
    "seed17_old_plan_census": {
        "planned_work_rows": 92, "registered_work_rows": 0,
        "attempt_rows": 0, "activation_rows": 0,
    },
    "seed20260804_old_plan_sha256": "0a8e56835af83659ae6274772401da742feefb6e4d4121ed7c995cafbe9dcb21",
    "seed20260804_old_plan_census": {
        "planned_work_rows": 92, "registered_work_rows": 0,
        "attempt_rows": 0, "activation_rows": 0,
    },
    "backup_anchor_sha256": "b37396143265013ed01361d7ec31edff3d84c358d2f6c8ce932df39b21e61c56",
    "backup_snapshot_sha256": "262498adb36c12ef44fdeb779283e17305378cfcac33b4e87c740017453a799c",
    "backup_receipt_sha256": "398755d38227c30c527c787c3205407ed0ba47f18ccfab4b865584fb74ec14f9",
}


class C0B4PlanError(RuntimeError):
    """A parent, fixture, candidate, or derived request is not exact."""


@dataclass(frozen=True)
class ResolvedC0B4Control:
    """Exact offline inputs for one of the three frozen seed-17 controls."""

    control: Mapping[str, Any]
    request_spec: RequestSpec
    source_work_id: str
    source_chunk: str | None


def _identity(protocol_sha256: str) -> dict[str, str]:
    _sha256(protocol_sha256, "protocol")
    return {
        "policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256,
        "protocol_sha256": protocol_sha256,
    }


def _sha256(value: Any, label: str) -> str:
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise C0B4PlanError(f"{label} must be lowercase SHA-256")
    return value


def _self_digest(value: Mapping[str, Any], field: str) -> str:
    body = dict(value)
    body.pop(field, None)
    return sha256_json(body)


def validate_parent_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    """Require the complete independently verified C0B-3 parent identity."""
    if type(value) is not dict or canonical_json(value) != canonical_json(PARENT_BINDING):
        raise C0B4PlanError("parent binding differs from the frozen C0B-3 evidence")
    return deepcopy(PARENT_BINDING)


def candidate_id() -> str:
    """Return the exact finalist identity inherited from the final D decision."""
    normalized = CandidateSelection.model_validate(
        SELECTION, strict=True).model_dump(mode="json")
    return stage_f_candidate_id(
        normalized, PARENT_BINDING["final_d_decision_sha256"])


def _generation_config(seed: int) -> dict[str, Any]:
    return {
        "keep_alive": legacy_plan.KEEP_ALIVE,
        "options": {
            "min_p": 0.0, "num_ctx": SELECTION["num_ctx"],
            "num_predict": SELECTION["num_predict"], "repeat_last_n": 0,
            "repeat_penalty": 1.0, "seed": seed, "temperature": 0.0,
            "top_k": 1, "top_p": 1.0,
        },
        "think": False,
    }


def _request_payload(prompt: str, seed: int) -> dict[str, Any]:
    config = _generation_config(seed)
    return {
        "model": SELECTION["model"],
        "messages": [{"role": "user", "content": prompt}],
        "stream": True, "format": worksheet_schema("v2"),
        "options": config["options"], "think": config["think"],
        "keep_alive": config["keep_alive"],
    }


def _pair_id(document: Any) -> str | None:
    if document.stratum == "injection":
        return document.doc_id
    if document.stratum == "injection_clean_twin":
        return "inj_" + document.doc_id.removeprefix("inj_twin_")
    return None


def _work_rows(lane_id: str, corpus: PublicCorpus,
               run_nonce_key: bytes) -> list[dict[str, Any]]:
    if type(run_nonce_key) is not bytes or len(run_nonce_key) != 32:
        raise C0B4PlanError("run nonce key must contain exactly 32 bytes")
    if lane_id not in LANE_CONFIG:
        raise C0B4PlanError("unknown C0B-4 lane")
    seed, plan_key, nonce_domain, expected = LANE_CONFIG[lane_id]
    ids: Sequence[str] = corpus.c_order if lane_id == "C44_1" else corpus.f_order
    documents = corpus.by_id()
    candidate = candidate_id()
    group_id = None if lane_id == "C44_1" else activation_group_id(
        candidate, plan_key)
    cell_id = public_cell_id(
        budget_stage="F", candidate_id=candidate,
        chunk_chars=SELECTION["chunk_chars"], num_ctx=SELECTION["num_ctx"],
        num_predict=SELECTION["num_predict"], phase=plan_key, seed=seed)
    rows: list[dict[str, Any]] = []
    for doc_id in ids:
        document = documents.get(doc_id)
        if document is None:
            raise C0B4PlanError(f"public corpus lacks {doc_id}")
        derived = lane_id != "C44_1" and document.stratum == "boundary"
        source, view_id = document.source_for(8000, derived=derived)
        view_identity = document_view_identity(
            doc_id=doc_id, document_sha256=document.document_sha256,
            pair_id=_pair_id(document), view_sha256=view_id)
        nonce = derive_nonce(
            run_nonce_key, nonce_domain=nonce_domain,
            document_view_identity=view_identity, seed=seed, worksheet="v2")
        if nonce in source:
            raise C0B4PlanError(f"derived nonce occurs in source {doc_id}")
        chunks = chunker.chunk(source, chunk_chars=8000, overlap_chars=256)
        if lane_id == "C44_1" and len(chunks) != 1:
            raise C0B4PlanError(f"C44 document is not exactly one chunk: {doc_id}")
        for item in chunks:
            prompt = build_prompt("v2", item.text, nonce)
            request_sha256 = stable_hash(_request_payload(prompt, seed))
            chunk_sha256 = hashlib.sha256(item.text.encode()).hexdigest()
            work_id = public_work_id(
                cell_id=cell_id, chunk_index=item.index,
                chunk_sha256=chunk_sha256, doc_id=doc_id,
                document_sha256=document.document_sha256, nonce=nonce,
                plan_key=plan_key, request_sha256=request_sha256,
                view_id=view_id)
            row = {
                "stage": "F", "phase": plan_key, "plan_key": plan_key,
                "budget_stage": "F", "activation_group_id": group_id,
                "candidate_id": candidate, "cell_id": cell_id,
                "work_id": work_id, "model": SELECTION["model"],
                "model_digest": SELECTION["model_digest"], "worksheet": "v2",
                "doc_id": doc_id, "view_id": view_id,
                "document_sha256": document.document_sha256,
                "chunk_chars": 8000, "overlap": 256, "num_ctx": 8192,
                "num_predict": 1024, "seed": seed, "chunk_index": item.index,
                "chunk_sha256": chunk_sha256, "nonce": nonce,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "request_sha256": request_sha256,
            }
            try:
                rows.append(PublicWork.model_validate(
                    row, strict=True).model_dump(mode="json"))
            except (TypeError, ValueError) as exc:
                raise C0B4PlanError("generated work violates PublicWork") from exc
    if len(rows) != expected or len({row["work_id"] for row in rows}) != expected:
        raise C0B4PlanError(f"{lane_id} work census differs from {expected}")
    return rows


def _control_nonce(run_nonce_key: bytes, source_identity: str, *, domain: str) -> str:
    message = canonical_json({
        "candidate_id": candidate_id(), "document_view_identity": source_identity,
        "domain": domain, "seed": 17, "worksheet": "v2",
    })
    return "FENCE_" + hmac.new(
        run_nonce_key, message, hashlib.sha256).hexdigest()[:32].upper()


def _control_plan(f17_work: Sequence[Mapping[str, Any]], corpus: PublicCorpus,
                  run_nonce_key: bytes, protocol_sha256: str) -> dict[str, Any]:
    identity = _identity(protocol_sha256)
    first, candidate = f17_work[0], candidate_id()
    config_sha256 = sha256_json(_generation_config(17))
    context_spec = RequestSpec(
        kind="ps", expected_model=SELECTION["model"],
        expected_digest=SELECTION["model_digest"], min_context=8192,
        purpose="c0b4_stage_f_candidate_context",
        config_sha256=config_sha256)
    payload_sha256 = request_spec_hash(context_spec)
    context = {
        "version": "c0b4-context-control-v1", **identity,
        "control_id": stable_hash({
            "domain": "c0b4-context-control-v1", "candidate_id": candidate,
            "config_sha256": config_sha256, "payload_sha256": payload_sha256,
        }),
        "kind": "context_probe", "lane_id": "F72_17",
        "purpose": "c0b4_stage_f_candidate_context",
        "candidate_id": candidate, "model": SELECTION["model"],
        "model_digest": SELECTION["model_digest"],
        "config_sha256": config_sha256,
        "prompt_sha256": first["prompt_sha256"], "minimum_context_length": 8192,
        "trigger_rule": "first_bounded_http_terminal_seed17",
        "payload_sha256": payload_sha256,
    }
    sources = [row for row in f17_work
               if row["doc_id"] == "pos_pii_013" and row["chunk_index"] == 0]
    if len(sources) != 1:
        raise C0B4PlanError("cancellation source work is not unique")
    source = sources[0]
    document = corpus.by_id()["pos_pii_013"]
    source_identity = document_view_identity(
        doc_id=document.doc_id, document_sha256=document.document_sha256)
    chunks = chunker.chunk(document.text, chunk_chars=8000, overlap_chars=256)
    if len(chunks) != 1:
        raise C0B4PlanError("cancellation source is not exactly one chunk")
    source_chunk = chunks[0].text
    cancellation_nonce = _control_nonce(
        run_nonce_key, source_identity, domain="c0b4-cancellation-nonce-v1")
    cancellation_prompt = build_prompt("v2", source_chunk, cancellation_nonce)
    cancellation_request_sha256 = stable_hash(
        _request_payload(cancellation_prompt, 17))
    if cancellation_nonce in source_chunk or cancellation_nonce == source["nonce"]:
        raise C0B4PlanError("cancellation nonce/source isolation failed")
    cancellation = {
        "version": "c0b4-cancellation-control-v1", **identity,
        "control_id": stable_hash({
            "domain": "c0b4-cancellation-control-v1",
            "candidate_id": candidate,
            "request_sha256": cancellation_request_sha256,
        }),
        "kind": "cancellation_probe", "lane_id": "F72_17",
        "candidate_id": candidate, "seed": 17,
        "prompt_sha256": hashlib.sha256(
            cancellation_prompt.encode()).hexdigest(),
        "source_doc_id": "pos_pii_013", "chunk_index": 0,
        "nonce": cancellation_nonce,
        "request_sha256": cancellation_request_sha256,
        "deadline_seconds": 600, "max_close_after_first_byte_ms": 5000,
        "health_not_before_ms": 2000,
    }
    health_nonce = _control_nonce(
        run_nonce_key, source_identity, domain="c0b4-health-nonce-v1")
    if (health_nonce in source_chunk
            or health_nonce in {source["nonce"], cancellation_nonce}):
        raise C0B4PlanError("health nonce/source isolation failed")
    health_prompt = build_prompt("v2", source_chunk, health_nonce)
    health_request_sha256 = stable_hash(_request_payload(health_prompt, 17))
    health = {
        "version": "c0b4-health-control-v1", **identity,
        "control_id": stable_hash({
            "domain": "c0b4-health-control-v1", "candidate_id": candidate,
            "nonce": health_nonce, "request_sha256": health_request_sha256,
        }),
        "kind": "cancellation_health", "lane_id": "F72_17",
        "candidate_id": candidate, "seed": 17,
        "prompt_sha256": hashlib.sha256(health_prompt.encode()).hexdigest(),
        "source_doc_id": "pos_pii_013", "chunk_index": 0,
        "nonce": health_nonce,
        "health_work_id": stable_hash({
            "domain": "c0b4-health-work-v1", "candidate_id": candidate,
            "request_sha256": health_request_sha256,
        }),
        "request_sha256": health_request_sha256, "deadline_seconds": 600,
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
        raise C0B4PlanError("generated controls violate C0B-4 schema") from exc


def build_lane_plan(lane_id: str, *, corpus: PublicCorpus, run_nonce_key: bytes,
                    protocol_sha256: str,
                    parent_binding: Mapping[str, Any] = PARENT_BINDING) -> dict[str, Any]:
    """Build one self-digested lane plan from exact parent and public inputs."""
    parent = validate_parent_binding(parent_binding)
    if corpus.master_manifest_sha256 != parent["master_manifest_sha256"]:
        raise C0B4PlanError("public corpus differs from the parent master manifest")
    if lane_id not in LANE_CONFIG:
        raise C0B4PlanError("unknown C0B-4 lane")
    seed = LANE_CONFIG[lane_id][0]
    value = {
        "version": ("c0b4-acceptance-plan-v1" if lane_id == "C44_1"
                    else "c0b4-lane-plan-v1"),
        **_identity(protocol_sha256), "lane_id": lane_id, "seed": seed,
        "candidate": deepcopy(SELECTION), "parent_evidence": parent,
        "work": _work_rows(lane_id, corpus, run_nonce_key),
    }
    value["plan_sha256"] = _self_digest(value, "plan_sha256")
    model = AcceptancePlan if lane_id == "C44_1" else LanePlan
    try:
        return model.model_validate(value, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise C0B4PlanError("generated lane plan violates C0B-4 schema") from exc


def build_master_plan(*, corpus: PublicCorpus, run_nonce_key: bytes,
                      protocol_sha256: str,
                      parent_binding: Mapping[str, Any] = PARENT_BINDING) -> dict[str, Any]:
    """Freeze both F72 lanes, C44, and all controls before any HTTP call."""
    parent = validate_parent_binding(parent_binding)
    lanes = [build_lane_plan(
        lane, corpus=corpus, run_nonce_key=run_nonce_key,
        protocol_sha256=protocol_sha256, parent_binding=parent)
        for lane in LANE_ORDER]
    controls = _control_plan(lanes[0]["work"], corpus, run_nonce_key, protocol_sha256)
    value = {
        "version": "c0b4-master-plan-v1", **_identity(protocol_sha256),
        "parent_binding": parent, "lane_order": list(LANE_ORDER),
        "lane_plans": [
            {"plan_sha256": row["plan_sha256"], "payload": row}
            for row in lanes[:2]],
        "control_plan": controls,
        "acceptance_template": {
            "plan_sha256": lanes[2]["plan_sha256"], "payload": lanes[2]},
    }
    try:
        return MasterPlan.model_validate(
            value, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise C0B4PlanError("generated master violates C0B-4 schema") from exc


def validate_master_plan(value: Mapping[str, Any], *, corpus: PublicCorpus,
                         run_nonce_key: bytes) -> dict[str, Any]:
    """Independently rebuild and byte-compare a stored C0B-4 master plan."""
    if type(value) is not dict:
        raise C0B4PlanError("master plan must be an exact object")
    protocol_sha256 = value.get("protocol_sha256")
    _sha256(protocol_sha256, "protocol")
    expected = build_master_plan(
        corpus=corpus, run_nonce_key=run_nonce_key,
        protocol_sha256=protocol_sha256,
        parent_binding=value.get("parent_binding"))
    if canonical_json(value) != canonical_json(expected):
        raise C0B4PlanError("master plan differs from independent re-derivation")
    return expected


def lane_from_master(master: Mapping[str, Any], lane_id: str, *,
                     corpus: PublicCorpus, run_nonce_key: bytes) -> dict[str, Any]:
    """Return one exact lane only after validating the complete master."""
    parsed = validate_master_plan(master, corpus=corpus, run_nonce_key=run_nonce_key)
    envelopes = list(parsed["lane_plans"]) + [parsed["acceptance_template"]]
    matches = [row["payload"] for row in envelopes
               if row["payload"]["lane_id"] == lane_id]
    if len(matches) != 1:
        raise C0B4PlanError("unknown or duplicate lane")
    return matches[0]


def _resolve_owned_work(owner: Mapping[str, Any], row: Mapping[str, Any], *,
                        corpus: PublicCorpus, verify_owner: bool = True,
                        documents: Mapping[str, Any] | None = None,
                        ) -> dict[str, Any]:
    document = (documents if documents is not None else corpus.by_id()).get(
        row["doc_id"])
    if document is None or document.document_sha256 != row["document_sha256"]:
        raise C0B4PlanError("work differs from its public document")
    source, view_id = document.source_for(
        8000, derived=row["view_id"] is not None)
    chunks = chunker.chunk(source, chunk_chars=8000, overlap_chars=256)
    if view_id != row["view_id"] or row["chunk_index"] >= len(chunks):
        raise C0B4PlanError("work source view or chunk is absent")
    item = chunks[row["chunk_index"]]
    prompt = build_prompt("v2", item.text, row["nonce"])
    payload = _request_payload(prompt, row["seed"])
    if (hashlib.sha256(item.text.encode()).hexdigest() != row["chunk_sha256"]
            or hashlib.sha256(prompt.encode()).hexdigest() != row["prompt_sha256"]
            or stable_hash(payload) != row["request_sha256"]
            or verify_owner and owner["plan_sha256"] !=
               _self_digest(owner, "plan_sha256")):
        raise C0B4PlanError("resolved request differs from frozen work")
    return {"lane": deepcopy(owner), "work": deepcopy(row), "source": source,
            "chunk_text": item.text, "chunk_start": item.start,
            "prompt": prompt, "payload": payload}


def resolve_work(master: Mapping[str, Any], work_id: str, *, corpus: PublicCorpus,
                 run_nonce_key: bytes) -> dict[str, Any]:
    """Rebuild one frozen source chunk and exact C0B-4 request payload."""
    parsed = validate_master_plan(master, corpus=corpus, run_nonce_key=run_nonce_key)
    envelopes = list(parsed["lane_plans"]) + [parsed["acceptance_template"]]
    matches = [(owner["payload"], row) for owner in envelopes
               for row in owner["payload"]["work"] if row["work_id"] == work_id]
    if len(matches) != 1:
        raise C0B4PlanError("unknown or duplicate work identity")
    owner, row = matches[0]
    return _resolve_owned_work(owner, row, corpus=corpus)


def request_spec_for_work(master: Mapping[str, Any], work_id: str, *,
                          corpus: PublicCorpus, run_nonce_key: bytes) -> RequestSpec:
    """Return one bounded chat spec after complete request re-derivation."""
    resolved = resolve_work(
        master, work_id, corpus=corpus, run_nonce_key=run_nonce_key)
    row = resolved["work"]
    return RequestSpec(
        kind="chat", payload=resolved["payload"], worksheet="v2",
        expected_model=row["model"], expected_digest=row["model_digest"])


def _resolve_validated_control(
        parsed: Mapping[str, Any], control: Mapping[str, Any], *,
        corpus: PublicCorpus, run_nonce_key: bytes,
        f17: Mapping[str, Any] | None = None,
        source_work: Mapping[str, Any] | None = None,
) -> ResolvedC0B4Control:
    if f17 is None:
        f17_matches = [row["payload"] for row in parsed["lane_plans"]
                       if row["payload"]["lane_id"] == "F72_17"]
        if len(f17_matches) != 1:
            raise C0B4PlanError("control owner lane is not unique")
        f17 = f17_matches[0]
    if source_work is None:
        source_rows = [row for row in f17["work"]
                       if row["doc_id"] == "pos_pii_013"
                       and row["chunk_index"] == 0]
        if len(source_rows) != 1:
            raise C0B4PlanError("control source work is not unique")
        source_work = source_rows[0]
    if control["kind"] == "context_probe":
        spec = RequestSpec(
            kind="ps", expected_model=SELECTION["model"],
            expected_digest=SELECTION["model_digest"], min_context=8192,
            purpose="c0b4_stage_f_candidate_context",
            config_sha256=sha256_json(_generation_config(17)))
        if request_spec_hash(spec) != control["payload_sha256"]:
            raise C0B4PlanError("context control differs from its request spec")
        return ResolvedC0B4Control(
            deepcopy(control), spec, f17["work"][0]["work_id"], None)

    document = corpus.by_id().get("pos_pii_013")
    if document is None:
        raise C0B4PlanError("control source document is absent")
    source_identity = document_view_identity(
        doc_id=document.doc_id, document_sha256=document.document_sha256)
    domain = ("c0b4-cancellation-nonce-v1" if control["kind"] ==
              "cancellation_probe" else "c0b4-health-nonce-v1")
    nonce = _control_nonce(run_nonce_key, source_identity, domain=domain)
    chunks = chunker.chunk(document.text, chunk_chars=8000, overlap_chars=256)
    if len(chunks) != 1 or nonce in chunks[0].text:
        raise C0B4PlanError("control source chunk or nonce is invalid")
    prompt = build_prompt("v2", chunks[0].text, nonce)
    payload = _request_payload(prompt, 17)
    spec = RequestSpec(
        kind="chat", payload=payload, worksheet="v2",
        expected_model=SELECTION["model"],
        expected_digest=SELECTION["model_digest"],
        cancel_on_first_content=control["kind"] == "cancellation_probe")
    if (control["nonce"] != nonce
            or control["prompt_sha256"] != hashlib.sha256(
                prompt.encode()).hexdigest()
            or control["request_sha256"] != request_spec_hash(spec)
            or nonce == source_work["nonce"]):
        raise C0B4PlanError("resolved control differs from its frozen request")
    return ResolvedC0B4Control(
        deepcopy(control), spec, source_work["work_id"], chunks[0].text)


def resolve_control(
        master: Mapping[str, Any], control_id: str, *, corpus: PublicCorpus,
        run_nonce_key: bytes,
) -> ResolvedC0B4Control:
    """Resolve one frozen control and independently verify its request identity."""
    parsed = validate_master_plan(
        master, corpus=corpus, run_nonce_key=run_nonce_key)
    matches = [control for control in parsed["control_plan"].values()
               if control["control_id"] == control_id]
    if len(matches) != 1:
        raise C0B4PlanError("unknown or duplicate C0B-4 control identity")
    return _resolve_validated_control(
        parsed, matches[0], corpus=corpus, run_nonce_key=run_nonce_key)


def resolve_controls(
        master: Mapping[str, Any], *, corpus: PublicCorpus,
        run_nonce_key: bytes,
) -> dict[str, ResolvedC0B4Control]:
    """Resolve context, dedicated cancellation, and following-health controls."""
    parsed = validate_master_plan(
        master, corpus=corpus, run_nonce_key=run_nonce_key)
    return {name: _resolve_validated_control(
        parsed, control, corpus=corpus, run_nonce_key=run_nonce_key)
            for name, control in parsed["control_plan"].items()}


class C0B4RequestResolver:
    """Invocation-local exact indexes after one complete master validation."""

    __slots__ = (
        "_master", "_corpus", "_documents", "_run_nonce_key", "_work",
        "_controls", "_f17", "_control_source",
    )

    def __init__(self, master: Mapping[str, Any], *, corpus: PublicCorpus,
                 run_nonce_key: bytes) -> None:
        self._master = validate_master_plan(
            master, corpus=corpus, run_nonce_key=run_nonce_key)
        self._corpus = corpus
        self._documents = corpus.by_id()
        self._run_nonce_key = run_nonce_key
        envelopes = list(self._master["lane_plans"]) + [
            self._master["acceptance_template"]]
        work_items = [(owner["payload"], row) for owner in envelopes
                      for row in owner["payload"]["work"]]
        self._work = {row["work_id"]: (owner, row)
                      for owner, row in work_items}
        if len(self._work) != len(work_items) or len(self._work) != 228:
            raise C0B4PlanError("master work index is duplicate or incomplete")
        controls = list(self._master["control_plan"].values())
        self._controls = {row["control_id"]: row for row in controls}
        if len(self._controls) != len(controls) or len(self._controls) != 3:
            raise C0B4PlanError("master control index is duplicate or incomplete")
        f17_matches = [row["payload"] for row in self._master["lane_plans"]
                       if row["payload"]["lane_id"] == "F72_17"]
        if len(f17_matches) != 1:
            raise C0B4PlanError("master seed-17 lane index is invalid")
        self._f17 = f17_matches[0]
        source_rows = [row for row in self._f17["work"]
                       if row["doc_id"] == "pos_pii_013"
                       and row["chunk_index"] == 0]
        if len(source_rows) != 1:
            raise C0B4PlanError("master control source index is invalid")
        self._control_source = source_rows[0]

    def resolve_work(self, work_id: str) -> dict[str, Any]:
        """Resolve one exact source/request through the prevalidated O(1) index."""
        match = self._work.get(work_id)
        if match is None:
            raise C0B4PlanError("unknown C0B-4 work identity")
        return _resolve_owned_work(
            *match, corpus=self._corpus, verify_owner=False,
            documents=self._documents)

    def request_spec_for_work(self, work_id: str) -> RequestSpec:
        """Return one exact chat spec without rebuilding the complete master."""
        resolved = self.resolve_work(work_id)
        row = resolved["work"]
        return RequestSpec(
            kind="chat", payload=resolved["payload"], worksheet="v2",
            expected_model=row["model"], expected_digest=row["model_digest"])

    def resolve_control(self, control_id: str) -> ResolvedC0B4Control:
        """Resolve one exact control through the prevalidated O(1) index."""
        control = self._controls.get(control_id)
        if control is None:
            raise C0B4PlanError("unknown C0B-4 control identity")
        return _resolve_validated_control(
            self._master, control, corpus=self._corpus,
            run_nonce_key=self._run_nonce_key, f17=self._f17,
            source_work=self._control_source)

    def resolve_controls(self) -> dict[str, ResolvedC0B4Control]:
        """Resolve all three frozen controls without revalidating the master."""
        return {name: self.resolve_control(control["control_id"])
                for name, control in self._master["control_plan"].items()}


def build_request_resolver(
        master: Mapping[str, Any], *, corpus: PublicCorpus,
        run_nonce_key: bytes,
) -> C0B4RequestResolver:
    """Validate once and build invocation-local bounded request indexes."""
    return C0B4RequestResolver(
        master, corpus=corpus, run_nonce_key=run_nonce_key)
