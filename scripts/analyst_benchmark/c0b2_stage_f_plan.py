"""Pure immutable planning for the public C0B-2 Stage-F benchmark.

The planner consumes only already-verified public fixtures and frozen lineage values.
It never opens a checkpoint, contacts Ollama, or decides which frozen group may run.

DISPOSITION: benchmark-only planner; remove after the frozen C0B selection is accepted.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from . import c0b2_plan as legacy_plan
from . import chunker, goldset
from .c0b2_public_schema import (
    AcceptancePlan, CandidateSelection, CancellationControl, ContextControl,
    FMasterPlan, FPlanCandidate, FSeedPlan, HealthControl,
    activation_group_id,
    cancellation_control_id, context_control_id, health_control_id, health_work_id,
    public_cell_id, public_work_id, sha256_json, stage_f_candidate_id,
)
from .c0b2_public_scoring import (
    derive_health_nonce, derive_nonce, document_view_identity,
)
from .c0b2_schema import CATEGORIES, build_prompt, canonical_json, stable_hash, worksheet_schema
from .c0b2_transport import RequestSpec, request_spec_hash

SEEDS = (1, 17, 20260804)
PLAN_KEYS = {1: "F_SEED_1", 17: "F_SEED_17", 20260804: "F_SEED_20260804"}
EXPECTED_F_CHUNKS = {2000: 122, 4000: 100, 8000: 92}
EXPECTED_ACCEPTANCE_CHUNKS = {2000: 247, 4000: 214, 8000: 202}
_MODEL_THINK = {model: think for model, _digest, think in legacy_plan.MODELS}


class StageFPlanError(RuntimeError):
    """A Stage-F fixture, lineage value, or derived request is not exact."""


@dataclass(frozen=True)
class PublicDocument:
    doc_id: str
    stratum: str
    document_sha256: str
    categories_present: tuple[str, ...]
    expected_identifiers: tuple[str, ...]
    clean_twin_id: str | None
    adversarial_class: str | None
    text: str
    boundary_views: tuple[tuple[int, str, str], ...]

    def source_for(self, chunk_chars: int, *, derived: bool) -> tuple[str, str | None]:
        matches = [row for row in self.boundary_views if row[0] == chunk_chars]
        if derived:
            if self.stratum != "boundary" or len(matches) != 1:
                raise StageFPlanError(f"boundary view is missing for {self.doc_id}")
            return matches[0][2], matches[0][1]
        if self.stratum == "boundary" and matches and derived:
            raise StageFPlanError(f"unexpected boundary view for {self.doc_id}")
        return self.text, None


@dataclass(frozen=True)
class PublicCorpus:
    master_manifest_sha256: str
    document_order: tuple[str, ...]
    c_order: tuple[str, ...]
    d_order: tuple[str, ...]
    f_order: tuple[str, ...]
    markers: Mapping[str, str]
    documents: tuple[PublicDocument, ...]

    def by_id(self) -> dict[str, PublicDocument]:
        return {document.doc_id: document for document in self.documents}


@dataclass(frozen=True)
class ResolvedFSeed1Control:
    """Exact offline inputs for one frozen seed-1 control request."""

    control: Mapping[str, Any]
    request_spec: RequestSpec
    source_work_id: str
    source_chunk: str | None


def load_public_corpus(
        master_manifest: Mapping[str, Any], *, master_manifest_sha256: str,
        source: goldset.GoldSet | None = None,
) -> PublicCorpus:
    """Re-derive the frozen master and expose the public 166-document corpus."""
    if type(master_manifest) is not dict:
        raise StageFPlanError("master manifest must be an exact object")
    corpus = source or goldset.load(verify=True)
    try:
        rebuilt = legacy_plan.build_master_manifest(corpus)
        payload = legacy_plan.master_manifest_payload(rebuilt)
    except (ValueError, legacy_plan.PlanError, goldset.GoldSetError) as exc:
        raise StageFPlanError("public corpus cannot re-derive the frozen master") from exc
    if (rebuilt.sha256 != master_manifest_sha256
            or canonical_json(payload) != canonical_json(master_manifest)):
        raise StageFPlanError("public corpus differs from the frozen master manifest")
    order = rebuilt.split.c + rebuilt.split.d + rebuilt.split.f
    documents = []
    views: dict[str, list[tuple[int, str, str]]] = {}
    for row in payload["boundary_views"]:
        views.setdefault(row["doc_id"], []).append(
            (row["chunk_chars"], row["sha256"], row["text"]))
    for doc_id in order:
        doc = corpus.docs.get(doc_id)
        if doc is None:
            raise StageFPlanError(f"public corpus lacks {doc_id}")
        categories = tuple(category for category in CATEGORIES
                           if category in doc.categories_present)
        if set(categories) != doc.categories_present:
            raise StageFPlanError(f"public categories are invalid for {doc_id}")
        try:
            text = doc.text()
        except (OSError, UnicodeDecodeError) as exc:
            raise StageFPlanError(f"public fixture is unreadable for {doc_id}") from exc
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != doc.sha256:
            raise StageFPlanError(f"public fixture hash drifted for {doc_id}")
        documents.append(PublicDocument(
            doc_id, doc.stratum, doc.sha256, categories,
            tuple(doc.expected_identifiers), doc.clean_twin_id,
            doc.adversarial_class, text, tuple(views.get(doc_id, ())),
        ))
    return PublicCorpus(
        master_manifest_sha256, order, rebuilt.split.c, rebuilt.split.d,
        rebuilt.split.f, dict(rebuilt.markers), tuple(documents),
    )


def _selection_rows(
        selections: Sequence[Mapping[str, Any]], stage_d_decision_sha256: str,
) -> list[dict[str, Any]]:
    if isinstance(selections, (str, bytes)) or not 1 <= len(selections) <= 3:
        raise StageFPlanError("Stage-F requires one to three final selections")
    rows = []
    try:
        for raw in selections:
            selection = CandidateSelection.model_validate(
                raw, strict=True).model_dump(mode="json")
            rows.append(FPlanCandidate.model_validate({
                "candidate_id": stage_f_candidate_id(
                    selection, stage_d_decision_sha256), **selection,
            }, strict=True).model_dump(mode="json"))
    except (TypeError, ValueError) as exc:
        raise StageFPlanError("Stage-F final selections are invalid") from exc
    if len({row["candidate_id"] for row in rows}) != len(rows):
        raise StageFPlanError("Stage-F final selections are duplicated")
    return rows


def selections_from_final_decision(
        decision: Mapping[str, Any], *, stage_d_decision_sha256: str,
) -> list[dict[str, Any]]:
    """Extract exact selection objects from an activated FINALISTS decision."""
    if (type(decision) is not dict or set(decision) != {
            "version", "stage", "phase", "plan_sha256", "aggregate_sha256",
            "outcome", "reason", "selections"}
            or decision.get("version") != "stage-d-decision-v1"
            or decision.get("stage") != "D"
            or decision.get("phase") not in {"D3", "D4"}
            or (decision.get("outcome"), decision.get("reason")) !=
            ("FINALISTS", "finalists_selected")
            or type(decision.get("selections")) is not list):
        raise StageFPlanError("Stage-F parent is not an activated final D decision")
    return _selection_rows(
        [row.get("selection") if type(row) is dict else None
         for row in decision["selections"]], stage_d_decision_sha256)


def rotated_candidate_ids(master: Mapping[str, Any], seed: int) -> list[str]:
    """Return the frozen left-rotated execution order for one seed."""
    try:
        parsed = FMasterPlan.model_validate(master, strict=True)
    except (TypeError, ValueError) as exc:
        raise StageFPlanError("Stage-F master fails strict validation") from exc
    if type(seed) is not int or seed not in SEEDS:
        raise StageFPlanError("unknown Stage-F seed")
    base = list(parsed.base_candidate_order)
    offset = SEEDS.index(seed) % len(base)
    return base[offset:] + base[:offset]


def _pair_id(document: PublicDocument) -> str | None:
    if document.stratum == "injection":
        return document.doc_id
    if document.stratum == "injection_clean_twin":
        suffix = document.doc_id.removeprefix("inj_twin_")
        return f"inj_{suffix}"
    return None


def _generation_config(candidate: Mapping[str, Any], seed: int) -> dict[str, Any]:
    think = _MODEL_THINK.get(candidate["model"])
    if candidate["model"] not in _MODEL_THINK:
        raise StageFPlanError("unknown Stage-F model")
    return {
        "keep_alive": legacy_plan.KEEP_ALIVE,
        "options": {
            "min_p": 0.0, "num_ctx": candidate["num_ctx"],
            "num_predict": candidate["num_predict"], "repeat_last_n": 0,
            "repeat_penalty": 1.0, "seed": seed, "temperature": 0.0,
            "top_k": 1, "top_p": 1.0,
        },
        "think": think,
    }


def _request_payload(candidate: Mapping[str, Any], prompt: str,
                     seed: int) -> dict[str, Any]:
    config = _generation_config(candidate, seed)
    return {
        "model": candidate["model"],
        "messages": [{"role": "user", "content": prompt}],
        "stream": True, "format": worksheet_schema(candidate["worksheet"]),
        "options": config["options"], "think": config["think"],
        "keep_alive": config["keep_alive"],
    }


def _work_rows(
        candidate: Mapping[str, Any], *, plan_key: str, seed: int,
        document_ids: Sequence[str], corpus: PublicCorpus, run_nonce_key: bytes,
        nonce_domain: str, group_id: str | None,
) -> list[dict[str, Any]]:
    if type(run_nonce_key) is not bytes or len(run_nonce_key) != 32:
        raise StageFPlanError("Stage-F run nonce key must contain exactly 32 bytes")
    documents = corpus.by_id()
    rows = []
    cell = public_cell_id(
        budget_stage="F", candidate_id=candidate["candidate_id"],
        chunk_chars=candidate["chunk_chars"], num_ctx=candidate["num_ctx"],
        num_predict=candidate["num_predict"], phase=plan_key, seed=seed)
    for doc_id in document_ids:
        document = documents.get(doc_id)
        if document is None:
            raise StageFPlanError(f"Stage-F corpus lacks {doc_id}")
        derived = plan_key != "F_ACCEPTANCE" and document.stratum == "boundary"
        source, view_id = document.source_for(
            candidate["chunk_chars"], derived=derived)
        identity = document_view_identity(
            doc_id=doc_id, document_sha256=document.document_sha256,
            pair_id=_pair_id(document), view_sha256=view_id)
        nonce = derive_nonce(
            run_nonce_key, nonce_domain=nonce_domain,
            document_view_identity=identity, seed=seed,
            worksheet=candidate["worksheet"])
        if nonce in source:
            raise StageFPlanError(f"derived nonce occurs in source {doc_id}")
        chunks = chunker.chunk(
            source, chunk_chars=candidate["chunk_chars"],
            overlap_chars=candidate["overlap"])
        if plan_key == "F_ACCEPTANCE" and len(chunks) != 1:
            raise StageFPlanError(f"acceptance C44 item is not one chunk: {doc_id}")
        for item in chunks:
            prompt = build_prompt(candidate["worksheet"], item.text, nonce)
            payload = _request_payload(candidate, prompt, seed)
            request_hash = stable_hash(payload)
            chunk_hash = hashlib.sha256(item.text.encode("utf-8")).hexdigest()
            work_id = public_work_id(
                cell_id=cell, chunk_index=item.index, chunk_sha256=chunk_hash,
                doc_id=doc_id, document_sha256=document.document_sha256,
                nonce=nonce, plan_key=plan_key, request_sha256=request_hash,
                view_id=view_id)
            rows.append({
                "stage": "F", "phase": plan_key, "plan_key": plan_key,
                "budget_stage": "F", "activation_group_id": group_id,
                "candidate_id": candidate["candidate_id"], "cell_id": cell,
                "work_id": work_id, "model": candidate["model"],
                "model_digest": candidate["model_digest"],
                "worksheet": candidate["worksheet"], "doc_id": doc_id,
                "view_id": view_id, "document_sha256": document.document_sha256,
                "chunk_chars": candidate["chunk_chars"], "overlap": 256,
                "num_ctx": candidate["num_ctx"],
                "num_predict": candidate["num_predict"], "seed": seed,
                "chunk_index": item.index, "chunk_sha256": chunk_hash,
                "nonce": nonce,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "request_sha256": request_hash,
            })
    return rows


def _seed1_controls(
        candidate: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], *,
        corpus: PublicCorpus, run_nonce_key: bytes,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not rows:
        raise StageFPlanError("seed-1 control candidate has no work")
    first = rows[0]
    config = _generation_config(candidate, 1)
    config_hash = sha256_json(config)
    context_spec = RequestSpec(
        kind="ps", expected_model=candidate["model"],
        expected_digest=candidate["model_digest"],
        min_context=candidate["num_ctx"], purpose="stage_f_candidate_context",
        config_sha256=config_hash)
    payload_hash = request_spec_hash(context_spec)
    context = {
        "control_id": context_control_id(
            candidate_id=candidate["candidate_id"], config_sha256=config_hash,
            model=candidate["model"], model_digest=candidate["model_digest"],
            payload_sha256=payload_hash, purpose="stage_f_candidate_context"),
        "kind": "context_probe", "purpose": "stage_f_candidate_context",
        "candidate_id": candidate["candidate_id"], "model": candidate["model"],
        "model_digest": candidate["model_digest"], "config_sha256": config_hash,
        "minimum_context_length": candidate["num_ctx"],
        "trigger_rule": "first_http_terminal_seed1",
        "payload_sha256": payload_hash,
    }
    source_row = next((row for row in rows
                       if row["doc_id"] == "pos_pii_013"
                       and row["chunk_index"] == 0), None)
    if source_row is None:
        raise StageFPlanError("seed-1 cancellation source work is missing")
    cancellation = {
        "control_id": cancellation_control_id(
            candidate_id=candidate["candidate_id"],
            request_sha256=source_row["request_sha256"]),
        "kind": "cancellation_probe", "candidate_id": candidate["candidate_id"],
        "source_doc_id": "pos_pii_013", "chunk_index": 0,
        "request_sha256": source_row["request_sha256"],
        "max_close_after_first_byte_ms": 5000, "health_not_before_ms": 2000,
    }
    document = corpus.by_id()["pos_pii_013"]
    identity = document_view_identity(
        doc_id=document.doc_id, document_sha256=document.document_sha256)
    health_nonce = derive_health_nonce(
        run_nonce_key, candidate_id=candidate["candidate_id"],
        document_view_identity=identity, worksheet=candidate["worksheet"])
    if health_nonce in document.text or health_nonce == source_row["nonce"]:
        raise StageFPlanError("health nonce is not isolated from its source request")
    chunks = chunker.chunk(
        document.text, chunk_chars=candidate["chunk_chars"], overlap_chars=256)
    health_prompt = build_prompt(candidate["worksheet"], chunks[0].text, health_nonce)
    health_hash = stable_hash(_request_payload(candidate, health_prompt, 1))
    health = {
        "control_id": health_control_id(
            candidate_id=candidate["candidate_id"], nonce=health_nonce,
            request_sha256=health_hash),
        "kind": "cancellation_health", "candidate_id": candidate["candidate_id"],
        "source_doc_id": "pos_pii_013", "chunk_index": 0,
        "nonce": health_nonce,
        "health_work_id": health_work_id(
            candidate_id=candidate["candidate_id"], request_sha256=health_hash),
        "request_sha256": health_hash,
    }
    try:
        return (
            ContextControl.model_validate(context, strict=True).model_dump(mode="json"),
            CancellationControl.model_validate(
                cancellation, strict=True).model_dump(mode="json"),
            HealthControl.model_validate(health, strict=True).model_dump(mode="json"),
        )
    except (TypeError, ValueError) as exc:
        raise StageFPlanError("seed-1 controls violate the strict schema") from exc


def _seed_plan(
        parent: str, candidates: Sequence[Mapping[str, Any]], *, seed: int,
        corpus: PublicCorpus, run_nonce_key: bytes,
) -> dict[str, Any]:
    key = PLAN_KEYS[seed]
    work: list[dict[str, Any]] = []
    groups = []
    for candidate in candidates:
        group_id = activation_group_id(candidate["candidate_id"], key)
        rows = _work_rows(
            candidate, plan_key=key, seed=seed, document_ids=corpus.f_order,
            corpus=corpus, run_nonce_key=run_nonce_key, nonce_domain="F",
            group_id=group_id)
        expected = EXPECTED_F_CHUNKS[candidate["chunk_chars"]]
        if len(rows) != expected:
            raise StageFPlanError(
                f"F72 chunk census differs at {candidate['chunk_chars']}")
        controls = (_seed1_controls(
            candidate, rows, corpus=corpus, run_nonce_key=run_nonce_key)
                    if seed == 1 else (None, None, None))
        work.extend(rows)
        groups.append({
            "group_id": group_id, "candidate_id": candidate["candidate_id"],
            "activation_predicate": (
                "unconditional_stage_d_finalist" if seed == 1 else
                "seed1_qualifier"),
            "first_work_id": rows[0]["work_id"],
            "last_work_id": rows[-1]["work_id"],
            "planned_work_count": len(rows), "context_control": controls[0],
            "cancellation_control": controls[1], "health_control": controls[2],
        })
    value = {
        "version": "stage-f-seed-plan-v1", "stage": "F", "phase": key,
        "plan_key": key, "budget_stage": "F",
        "parent_decision_sha256": parent, "candidates": list(candidates),
        "work": work, "groups": groups,
    }
    try:
        return FSeedPlan.model_validate(value, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise StageFPlanError(f"generated {key} plan violates the strict schema") from exc


def build_f_master_plan(
        stage_d_decision_sha256: str, selections: Sequence[Mapping[str, Any]], *,
        corpus: PublicCorpus, run_nonce_key: bytes,
) -> dict[str, Any]:
    """Freeze all seed plans and every possible C44 template before seed 1."""
    candidates = _selection_rows(selections, stage_d_decision_sha256)
    plans = []
    for seed in SEEDS:
        payload = _seed_plan(
            stage_d_decision_sha256, candidates, seed=seed,
            corpus=corpus, run_nonce_key=run_nonce_key)
        plans.append({"plan_sha256": sha256_json(payload), "payload": payload})
    templates = []
    for candidate in candidates:
        payload = {
            "version": "stage-f-acceptance-plan-v1", "stage": "F",
            "phase": "F_ACCEPTANCE", "plan_key": "F_ACCEPTANCE",
            "budget_stage": "F", "parent_decision_sha256": None,
            "candidates": [candidate],
            "work": _work_rows(
                candidate, plan_key="F_ACCEPTANCE", seed=1,
                document_ids=corpus.c_order, corpus=corpus,
                run_nonce_key=run_nonce_key, nonce_domain="acceptance-c44",
                group_id=None),
        }
        template_hash = sha256_json(payload)
        templates.append({
            "template_sha256": template_hash,
            "candidate_id": candidate["candidate_id"], "payload": payload,
        })
    value = {
        "version": "stage-f-master-plan-v1", "stage": "F", "budget_stage": "F",
        "parent_decision_sha256": stage_d_decision_sha256,
        "master_manifest_sha256": corpus.master_manifest_sha256,
        "base_candidate_order": [row["candidate_id"] for row in candidates],
        "seed_order": list(SEEDS), "plans": plans,
        "acceptance_templates": templates,
    }
    try:
        return FMasterPlan.model_validate(value, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise StageFPlanError("generated F master violates the strict schema") from exc


def validate_f_master_plan(
        value: Mapping[str, Any], *, corpus: PublicCorpus, run_nonce_key: bytes,
) -> dict[str, Any]:
    """Strictly parse and independently rebuild the complete frozen F tree."""
    try:
        parsed = FMasterPlan.model_validate(
            value, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise StageFPlanError("Stage-F master fails strict validation") from exc
    selections = [row.selection() for row in FMasterPlan.model_validate(
        parsed, strict=True).plans[0].payload.candidates]
    expected = build_f_master_plan(
        parsed["parent_decision_sha256"], selections,
        corpus=corpus, run_nonce_key=run_nonce_key)
    if canonical_json(parsed) != canonical_json(expected):
        raise StageFPlanError("Stage-F master differs from independent re-derivation")
    return parsed


def build_acceptance_plan(
        master: Mapping[str, Any], *, candidate_id: str,
        provisional_decision_sha256: str,
) -> dict[str, Any]:
    """Activate exactly one byte-frozen C44 template under its decision parent."""
    try:
        parsed = FMasterPlan.model_validate(master, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise StageFPlanError("Stage-F master fails strict validation") from exc
    templates = [row for row in parsed["acceptance_templates"]
                 if row["candidate_id"] == candidate_id]
    if len(templates) != 1:
        raise StageFPlanError("acceptance winner lacks one frozen template")
    template = templates[0]
    value = {
        **template["payload"],
        "parent_decision_sha256": provisional_decision_sha256,
        "master_plan_sha256": sha256_json(parsed),
        "template_sha256": template["template_sha256"],
    }
    try:
        return AcceptancePlan.model_validate(value, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise StageFPlanError("activated acceptance plan violates frozen template") from exc


def _resolve_row(
        master: Mapping[str, Any], work_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed = FMasterPlan.model_validate(master, strict=True).model_dump(mode="json")
    match = _validated_work_index(parsed).get(work_id)
    if match is None:
        raise StageFPlanError("unknown or duplicate Stage-F work identity")
    return match


def _validated_work_index(
        master: Mapping[str, Any],
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """Index one already validated master while rejecting cross-plan collisions."""
    owners = [envelope["payload"] for envelope in master["plans"]]
    owners.extend(row["payload"] for row in master["acceptance_templates"])
    index: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for owner in owners:
        for row in owner["work"]:
            if row["work_id"] in index:
                raise StageFPlanError("unknown or duplicate Stage-F work identity")
            index[row["work_id"]] = (owner, row)
    return index


def _resolve_validated_f_work(
        master: Mapping[str, Any], work_id: str, *, corpus: PublicCorpus,
        indexed: tuple[dict[str, Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rebuild one work item from an already independently validated master."""
    _owner, row = indexed if indexed is not None else _resolve_row(master, work_id)
    if row["work_id"] != work_id:
        raise StageFPlanError("resolved F work differs from its indexed identity")
    document = corpus.by_id().get(row["doc_id"])
    if document is None or document.document_sha256 != row["document_sha256"]:
        raise StageFPlanError("resolved F work differs from its public document")
    source, view_id = document.source_for(
        row["chunk_chars"], derived=row["view_id"] is not None)
    if view_id != row["view_id"]:
        raise StageFPlanError("resolved F boundary view differs from frozen work")
    chunks = chunker.chunk(
        source, chunk_chars=row["chunk_chars"], overlap_chars=row["overlap"])
    if row["chunk_index"] >= len(chunks):
        raise StageFPlanError("resolved F chunk index is absent")
    chunk = chunks[row["chunk_index"]]
    prompt = build_prompt(row["worksheet"], chunk.text, row["nonce"])
    payload = _request_payload(row, prompt, row["seed"])
    if (hashlib.sha256(chunk.text.encode("utf-8")).hexdigest() != row["chunk_sha256"]
            or hashlib.sha256(prompt.encode("utf-8")).hexdigest() != row["prompt_sha256"]
            or stable_hash(payload) != row["request_sha256"]):
        raise StageFPlanError("resolved F request differs from frozen work")
    return {"work": row, "source": source, "chunk_text": chunk.text,
            "chunk_start": chunk.start, "prompt": prompt, "payload": payload}


def _request_spec_from_resolved(resolved: Mapping[str, Any]) -> RequestSpec:
    row = resolved["work"]
    return RequestSpec(
        kind="chat", payload=resolved["payload"], worksheet=row["worksheet"],
        expected_model=row["model"], expected_digest=row["model_digest"])


def _request_specs_for_owner(
        master: Mapping[str, Any], owner: Mapping[str, Any], *,
        corpus: PublicCorpus,
) -> dict[str, RequestSpec]:
    index = _validated_work_index(master)
    specs: dict[str, RequestSpec] = {}
    for row in owner["work"]:
        match = index.get(row["work_id"])
        if match is None or match[0] is not owner or match[1] != row:
            raise StageFPlanError("F work index differs from its exact plan owner")
        resolved = _resolve_validated_f_work(
            master, row["work_id"], corpus=corpus, indexed=match)
        specs[row["work_id"]] = _request_spec_from_resolved(resolved)
    return specs


def request_specs_for_f_plan(
        master: Mapping[str, Any], plan_key: str, *, corpus: PublicCorpus,
        run_nonce_key: bytes,
) -> dict[str, RequestSpec]:
    """Resolve one exact frozen seed plan after one complete master validation."""
    parsed = validate_f_master_plan(
        master, corpus=corpus, run_nonce_key=run_nonce_key)
    matches = [row["payload"] for row in parsed["plans"]
               if row["payload"]["plan_key"] == plan_key]
    if plan_key == "F_ACCEPTANCE":
        raise StageFPlanError("activated acceptance resolution requires its plan")
    if len(matches) != 1:
        raise StageFPlanError("unknown or duplicate Stage-F plan key")
    return _request_specs_for_owner(parsed, matches[0], corpus=corpus)


def request_specs_for_activated_f_plan(
        master: Mapping[str, Any], plan: Mapping[str, Any], *,
        corpus: PublicCorpus, run_nonce_key: bytes,
) -> dict[str, RequestSpec]:
    """Resolve a strict activated acceptance plan from its frozen master template."""
    parsed = validate_f_master_plan(
        master, corpus=corpus, run_nonce_key=run_nonce_key)
    try:
        activated = AcceptancePlan.model_validate(
            plan, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise StageFPlanError("activated acceptance plan fails strict validation") from exc
    templates = [row for row in parsed["acceptance_templates"]
                 if row["template_sha256"] == activated["template_sha256"]]
    if len(templates) != 1:
        raise StageFPlanError("activated acceptance lacks one frozen template")
    template = templates[0]
    expected = {
        **template["payload"],
        "parent_decision_sha256": activated["parent_decision_sha256"],
        "master_plan_sha256": sha256_json(parsed),
        "template_sha256": template["template_sha256"],
    }
    if activated != expected:
        raise StageFPlanError("activated acceptance differs from its frozen template")
    return _request_specs_for_owner(
        parsed, template["payload"], corpus=corpus)


def resolve_f_work(
        master: Mapping[str, Any], work_id: str, *, corpus: PublicCorpus,
        run_nonce_key: bytes,
) -> dict[str, Any]:
    """Rebuild one frozen F request, source chunk, and request hash."""
    parsed = validate_f_master_plan(
        master, corpus=corpus, run_nonce_key=run_nonce_key)
    return _resolve_validated_f_work(parsed, work_id, corpus=corpus)


def resolve_f_seed1_control(
        master: Mapping[str, Any], control_id: str, *, corpus: PublicCorpus,
        run_nonce_key: bytes,
) -> ResolvedFSeed1Control:
    """Resolve exactly one frozen seed-1 control without contacting Ollama."""
    parsed = validate_f_master_plan(
        master, corpus=corpus, run_nonce_key=run_nonce_key)
    seed1 = next((envelope["payload"] for envelope in parsed["plans"]
                  if envelope["payload"]["plan_key"] == "F_SEED_1"), None)
    if seed1 is None:  # pragma: no cover - independent validation guarantees this
        raise StageFPlanError("Stage-F seed-1 plan is absent")
    matches = []
    for group in seed1["groups"]:
        for name in ("context_control", "cancellation_control", "health_control"):
            control = group[name]
            if control is not None and control["control_id"] == control_id:
                matches.append((group, control))
    if len(matches) != 1:
        raise StageFPlanError("unknown or duplicate Stage-F seed-1 control identity")
    group, control = matches[0]
    candidates = [row for row in seed1["candidates"]
                  if row["candidate_id"] == group["candidate_id"]]
    if len(candidates) != 1:
        raise StageFPlanError("seed-1 control lacks exactly one candidate")
    candidate = candidates[0]
    source_rows = [row for row in seed1["work"]
                   if row["candidate_id"] == candidate["candidate_id"]
                   and row["doc_id"] == "pos_pii_013"
                   and row["chunk_index"] == 0]
    if len(source_rows) != 1:
        raise StageFPlanError("seed-1 control lacks exactly one source work item")
    source_row = source_rows[0]

    if control["kind"] == "context_probe":
        config = _generation_config(candidate, 1)
        config_hash = sha256_json(config)
        spec = RequestSpec(
            kind="ps", expected_model=candidate["model"],
            expected_digest=candidate["model_digest"],
            min_context=candidate["num_ctx"], purpose=control["purpose"],
            config_sha256=config_hash)
        if (control["config_sha256"] != config_hash
                or control["minimum_context_length"] != candidate["num_ctx"]
                or control["model"] != candidate["model"]
                or control["model_digest"] != candidate["model_digest"]
                or request_spec_hash(spec) != control["payload_sha256"]):
            raise StageFPlanError("resolved context control differs from its candidate")
        return ResolvedFSeed1Control(
            control, spec, group["first_work_id"], None)

    source = _resolve_validated_f_work(
        parsed, source_row["work_id"], corpus=corpus)
    if control["kind"] == "cancellation_probe":
        spec = RequestSpec(
            kind="chat", payload=source["payload"],
            worksheet=candidate["worksheet"], expected_model=candidate["model"],
            expected_digest=candidate["model_digest"],
            cancel_on_first_content=True)
        if (control["request_sha256"] != source_row["request_sha256"]
                or request_spec_hash(spec) != control["request_sha256"]):
            raise StageFPlanError("resolved cancellation control differs from source work")
        return ResolvedFSeed1Control(
            control, spec, source_row["work_id"], source["chunk_text"])

    if control["kind"] != "cancellation_health":  # pragma: no cover
        raise StageFPlanError("unsupported Stage-F seed-1 control kind")
    document = corpus.by_id().get("pos_pii_013")
    if document is None:
        raise StageFPlanError("health control source document is absent")
    identity = document_view_identity(
        doc_id=document.doc_id, document_sha256=document.document_sha256)
    nonce = derive_health_nonce(
        run_nonce_key, candidate_id=candidate["candidate_id"],
        document_view_identity=identity, worksheet=candidate["worksheet"])
    chunks = chunker.chunk(
        document.text, chunk_chars=candidate["chunk_chars"],
        overlap_chars=candidate["overlap"])
    if not chunks:
        raise StageFPlanError("health control source chunk is absent")
    source_chunk = chunks[0].text
    prompt = build_prompt(candidate["worksheet"], source_chunk, nonce)
    payload = _request_payload(candidate, prompt, 1)
    spec = RequestSpec(
        kind="chat", payload=payload, worksheet=candidate["worksheet"],
        expected_model=candidate["model"],
        expected_digest=candidate["model_digest"])
    if (nonce in source_chunk or nonce == source_row["nonce"]
            or source_chunk != source["chunk_text"]
            or hashlib.sha256(source_chunk.encode("utf-8")).hexdigest()
            != source_row["chunk_sha256"]
            or control["nonce"] != nonce
            or request_spec_hash(spec) != control["request_sha256"]):
        raise StageFPlanError("resolved health control differs from its frozen source")
    return ResolvedFSeed1Control(
        control, spec, source_row["work_id"], source_chunk)


def request_spec_for_f_work(
        master: Mapping[str, Any], work_id: str, *, corpus: PublicCorpus,
        run_nonce_key: bytes,
) -> RequestSpec:
    resolved = resolve_f_work(
        master, work_id, corpus=corpus, run_nonce_key=run_nonce_key)
    row = resolved["work"]
    return RequestSpec(
        kind="chat", payload=resolved["payload"], worksheet=row["worksheet"],
        expected_model=row["model"], expected_digest=row["model_digest"])
