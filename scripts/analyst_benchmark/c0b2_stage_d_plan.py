"""Pure selective-corpus and immutable Stage-D planning for C0B-2.

The module reads only public gold fixtures selected for D, never opens a checkpoint,
contacts Ollama, or inspects private data.  Runtime code persists the strict mappings
returned here and re-derives them before dispatch.

DISPOSITION: benchmark-only planner; remove after the frozen C0B selection is accepted.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import chunker, goldset
from . import c0b2_plan as legacy_plan
from .c0b2_public_schema import (
    DPhaseCandidate,
    DPhasePlan,
    context_control_id,
    public_cell_id,
    public_work_id,
    sha256_json,
)
from .c0b2_public_scoring import derive_nonce, document_view_identity
from .c0b2_schema import (
    CATEGORIES,
    build_prompt,
    canonical_json,
    stable_hash,
    validate_stage_c_selection,
    worksheet_schema,
)
from .c0b2_transport import RequestSpec, request_spec_hash

OVERLAP = 256
SEED = 1
D1_PANEL = (
    "trunc_out_01",
    "pos_pii_007",
    "pos_financial_007",
    "pos_contact_007",
    "pos_demographic_007",
)
D2_CHUNKS = (2000, 4000, 8000)
_D1_BUDGETS = {
    "gpt-oss:20b": (2048, 3072, 4096),
    "qwen3.6:35b": (1024, 2048, 3072, 4096),
    "qwen3.6:27b": (1024, 2048, 3072, 4096),
}
_MODEL_ROWS = {
    model: (index, digest, think)
    for index, (model, digest, think) in enumerate(legacy_plan.MODELS)
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_KEY_RE = re.compile(r"[0-9a-f]{64}\Z")
_MASTER_KEYS = {
    "gold_version", "gold_manifest_sha256", "boundary_generator_version",
    "boundary_filler_byte", "split", "markers", "boundary_views",
}
_GOLD_KEYS = {
    "gold_set_version", "generator_seed", "chunk_chars_reference",
    "document_count", "identifier_provenance", "screening_subset", "documents",
}
_GOLD_DOCUMENT_KEYS = {
    "doc_id", "path", "stratum", "source_format", "sha256", "size",
    "categories_present", "expected_identifiers", "clean_twin_id",
    "adversarial_class", "context_rule_exception",
}
_BOUNDARY_KEYS = {
    "doc_id", "chunk_chars", "split_offset", "expected_identifier", "text", "sha256",
}
_D_STRATA = {
    "positive_control", "negative_clean", "negative_near_miss", "boundary",
    "output_truncation", "input_truncation",
}


class StageDPlanError(RuntimeError):
    """A public fixture or proposed Stage-D identity violates the frozen protocol."""


@dataclass(frozen=True)
class D50BoundaryView:
    chunk_chars: int
    view_id: str
    text: str


@dataclass(frozen=True)
class D50Document:
    doc_id: str
    stratum: str
    document_sha256: str
    categories_present: tuple[str, ...]
    expected_identifiers: tuple[str, ...]
    text: str
    boundary_views: tuple[D50BoundaryView, ...]

    def view_for(self, chunk_chars: int) -> D50BoundaryView | None:
        matches = [view for view in self.boundary_views
                   if view.chunk_chars == chunk_chars]
        if len(matches) > 1:
            raise StageDPlanError(f"duplicate boundary view for {self.doc_id}")
        return matches[0] if matches else None


@dataclass(frozen=True)
class D50Corpus:
    master_manifest_sha256: str
    document_order: tuple[str, ...]
    documents: tuple[D50Document, ...]

    def by_id(self) -> dict[str, D50Document]:
        return {document.doc_id: document for document in self.documents}


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class StageDContextControl(_StrictModel):
    """The protocol-v4 Stage-D control, separate from the Stage-F group model."""

    control_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: str
    purpose: str
    candidate_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    model: str = Field(min_length=1)
    model_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    minimum_context_length: int
    trigger_rule: str
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact_stage_d_control(self) -> "StageDContextControl":
        pairs = {
            "d3_context_16384": ("first_http_terminal_d3", 16384),
            "d4_context_selected": ("first_http_terminal_d4", self.minimum_context_length),
        }
        if self.kind != "context_probe" or self.purpose not in pairs:
            raise ValueError("invalid Stage-D context-control kind or purpose")
        trigger, minimum = pairs[self.purpose]
        if (self.trigger_rule != trigger or self.minimum_context_length != minimum
                or self.minimum_context_length not in {4096, 8192, 16384}):
            raise ValueError("Stage-D context-control trigger/allocation mismatch")
        expected = context_control_id(
            candidate_id=self.candidate_id, config_sha256=self.config_sha256,
            model=self.model, model_digest=self.model_digest,
            payload_sha256=self.payload_sha256, purpose=self.purpose,
        )
        if self.control_id != expected:
            raise ValueError("Stage-D context-control identity mismatch")
        return self


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _mapping(value: Mapping[str, Any] | str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value, object_pairs_hook=_json_object) \
            if isinstance(value, str) else dict(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StageDPlanError(f"{label} is not an exact JSON object") from exc
    if type(parsed) is not dict:
        raise StageDPlanError(f"{label} is not an exact JSON object")
    return parsed


def _sha256(value: Any, label: str) -> str:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        raise StageDPlanError(f"{label} is not a lowercase SHA-256")
    return value


def _safe_fixture_path(root: Path, relative: Any, doc_id: str) -> Path:
    pure = PurePosixPath(relative) if type(relative) is str else PurePosixPath("/")
    if pure.is_absolute() or ".." in pure.parts or not pure.parts or str(pure) != relative:
        raise StageDPlanError(f"unsafe fixture path for {doc_id}")
    path = (root / pure).resolve()
    if path.parent != root and root not in path.parents:
        raise StageDPlanError(f"fixture escapes gold root for {doc_id}")
    return path


def _numbered_ids(rows: Sequence[Mapping[str, Any]], prefix: str,
                  first: int, last: int) -> list[str]:
    values: list[str] = []
    for row in rows:
        doc_id = row["doc_id"]
        if doc_id.startswith(prefix):
            try:
                number = int(doc_id.rsplit("_", 1)[1])
            except ValueError as exc:
                raise StageDPlanError("gold numbered document ID is malformed") from exc
            if first <= number <= last:
                values.append(doc_id)
    return values


def _boundary_ids(rows: Sequence[Mapping[str, Any]], first: int,
                  last: int) -> list[str]:
    prefixes = tuple(f"bnd_{number:02d}_" for number in range(first, last + 1))
    return [row["doc_id"] for row in rows if row["doc_id"].startswith(prefixes)]


def _expected_split(gold: Mapping[str, Any],
                    rows: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    return {
        "c": list(gold["screening_subset"]),
        "d": (
            _numbered_ids(rows, "pos_", 7, 12)
            + _numbered_ids(rows, "neg_clean_", 7, 12)
            + _numbered_ids(rows, "neg_nearmiss_", 7, 12)
            + _boundary_ids(rows, 1, 3)
            + ["trunc_out_01", "trunc_in_01"]
        ),
        "f": (
            _numbered_ids(rows, "pos_", 13, 20)
            + _numbered_ids(rows, "neg_clean_", 13, 20)
            + _numbered_ids(rows, "neg_nearmiss_", 13, 20)
            + [f"inj_{number:02d}" for number in range(5, 9)]
            + [f"inj_twin_{number:02d}" for number in range(5, 9)]
            + _boundary_ids(rows, 4, 6)
            + ["trunc_out_02", "trunc_out_03", "trunc_in_02", "trunc_in_03"]
        ),
    }


def _validate_master(value: Mapping[str, Any] | str,
                     expected_sha256: str) -> dict[str, Any]:
    master = _mapping(value, "master manifest")
    _sha256(expected_sha256, "master-manifest hash")
    if set(master) != _MASTER_KEYS or stable_hash(master) != expected_sha256:
        raise StageDPlanError("master manifest shape/hash differs from its frozen identity")
    if (master["boundary_generator_version"] != legacy_plan.BOUNDARY_GENERATOR_VERSION
            or master["boundary_filler_byte"] != legacy_plan.BOUNDARY_FILLER_BYTE
            or type(master["gold_version"]) is not str
            or not master["gold_version"]):
        raise StageDPlanError("master manifest generator identity is invalid")
    _sha256(master["gold_manifest_sha256"], "gold-manifest hash")
    split = master["split"]
    if type(split) is not dict or set(split) != {"c", "d", "f"}:
        raise StageDPlanError("master split has an unexpected shape")
    if any(type(split[name]) is not list or
           any(type(doc_id) is not str or not doc_id for doc_id in split[name])
           for name in ("c", "d", "f")):
        raise StageDPlanError("master split IDs are invalid")
    joined = split["c"] + split["d"] + split["f"]
    if ([len(split[name]) for name in ("c", "d", "f")] != [44, 50, 72]
            or len(set(joined)) != 166):
        raise StageDPlanError("master split is not the frozen disjoint 44/50/72 split")
    markers = master["markers"]
    if (type(markers) is not dict
            or list(markers) != [f"inj_{index:02d}" for index in range(1, 9)]
            or any(type(item) is not str or not item for item in markers.values())):
        raise StageDPlanError("master marker registry is invalid")
    views = master["boundary_views"]
    if type(views) is not list or len(views) != 72:
        raise StageDPlanError("master boundary-view registry must contain 72 rows")
    identities: list[tuple[str, int]] = []
    for row in views:
        if type(row) is not dict or set(row) != _BOUNDARY_KEYS:
            raise StageDPlanError("master boundary-view row has an unexpected shape")
        if (type(row["doc_id"]) is not str or not row["doc_id"]
                or type(row["chunk_chars"]) is not int
                or row["chunk_chars"] not in D2_CHUNKS
                or type(row["split_offset"]) is not int
                or row["split_offset"] not in {2, 4, 7, 9}
                or type(row["expected_identifier"]) is not str
                or not row["expected_identifier"]
                or type(row["text"]) is not str):
            raise StageDPlanError("master boundary-view row has invalid values")
        try:
            encoded = row["text"].encode("ascii")
        except UnicodeEncodeError as exc:
            raise StageDPlanError("boundary-view bytes are not ASCII") from exc
        if (len(row["text"]) != row["chunk_chars"] + 512
                or hashlib.sha256(encoded).hexdigest() != row["sha256"]
                or row["text"].count(row["expected_identifier"]) != 1):
            raise StageDPlanError("boundary-view content/hash is invalid")
        identities.append((row["doc_id"], row["chunk_chars"]))
    if len(set(identities)) != 72:
        raise StageDPlanError("master boundary-view identities are not unique")
    return master


def load_d50(
        master_manifest: Mapping[str, Any] | str, *,
        master_manifest_sha256: str,
        manifest_path: Path = goldset.MANIFEST,
        read_bytes: Callable[[Path], bytes] | None = None,
) -> D50Corpus:
    """Load and verify only the logical D50 fixture bytes in frozen manifest order."""
    master = _validate_master(master_manifest, master_manifest_sha256)
    reader = read_bytes or (lambda path: path.read_bytes())
    manifest_path = Path(manifest_path)
    try:
        raw_manifest = reader(manifest_path)
        gold = json.loads(raw_manifest, object_pairs_hook=_json_object)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StageDPlanError("gold manifest is unreadable") from exc
    if (type(gold) is not dict or set(gold) != _GOLD_KEYS
            or hashlib.sha256(raw_manifest).hexdigest() != master["gold_manifest_sha256"]
            or gold["gold_set_version"] != master["gold_version"]
            or gold["document_count"] != 166
            or gold["screening_subset"] != master["split"]["c"]):
        raise StageDPlanError("gold manifest differs from the frozen master manifest")
    rows = gold["documents"]
    if type(rows) is not list or len(rows) != 166:
        raise StageDPlanError("gold document registry is invalid")
    if any(type(row) is not dict or set(row) != _GOLD_DOCUMENT_KEYS for row in rows):
        raise StageDPlanError("gold document row has an unexpected shape")
    row_by_id = {row["doc_id"]: row for row in rows}
    all_ids = master["split"]["c"] + master["split"]["d"] + master["split"]["f"]
    if len(row_by_id) != 166 or set(row_by_id) != set(all_ids):
        raise StageDPlanError("gold document identities differ from the frozen split")
    if master["split"] != _expected_split(gold, rows):
        raise StageDPlanError("master split order differs from the preregistered derivation")

    for view in master["boundary_views"]:
        owner = row_by_id.get(view["doc_id"])
        expected_class = f"boundary_split_{view['split_offset']}"
        start = view["chunk_chars"] - view["split_offset"]
        identifier = view["expected_identifier"]
        expected_text = (
            legacy_plan.BOUNDARY_FILLER_BYTE * start + identifier
            + legacy_plan.BOUNDARY_FILLER_BYTE
            * (view["chunk_chars"] + 512 - start - len(identifier))
        )
        if (owner is None or owner["stratum"] != "boundary"
                or owner["expected_identifiers"] != [identifier]
                or owner["adversarial_class"] != expected_class
                or view["text"] != expected_text):
            raise StageDPlanError("boundary-view derivation differs from gold metadata")

    views_by_doc: dict[str, list[dict[str, Any]]] = {}
    for row in master["boundary_views"]:
        views_by_doc.setdefault(row["doc_id"], []).append(row)
    root = manifest_path.parent.resolve()
    documents: list[D50Document] = []
    for doc_id in master["split"]["d"]:
        row = row_by_id[doc_id]
        if (type(row["doc_id"]) is not str or row["doc_id"] != doc_id
                or row["stratum"] not in _D_STRATA
                or type(row["size"]) is not int or row["size"] < 0
                or type(row["categories_present"]) is not list
                or type(row["expected_identifiers"]) is not list
                or any(type(value) is not str for value in
                       row["categories_present"] + row["expected_identifiers"])):
            raise StageDPlanError(f"gold metadata is invalid for {doc_id}")
        if (row["categories_present"] != [
                category for category in CATEGORIES
                if category in row["categories_present"]]
                or len(set(row["categories_present"])) != len(row["categories_present"])):
            raise StageDPlanError(f"gold categories are invalid for {doc_id}")
        _sha256(row["sha256"], f"fixture hash for {doc_id}")
        path = _safe_fixture_path(root, row["path"], doc_id)
        try:
            raw = reader(path)
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise StageDPlanError(f"fixture is unreadable for {doc_id}") from exc
        if len(raw) != row["size"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise StageDPlanError(f"fixture bytes drifted for {doc_id}")
        raw_views = views_by_doc.get(doc_id, [])
        if row["stratum"] == "boundary":
            if ([item["chunk_chars"] for item in raw_views] != list(D2_CHUNKS)
                    or len(row["expected_identifiers"]) != 1
                    or any(item["expected_identifier"] != row["expected_identifiers"][0]
                           for item in raw_views)):
                raise StageDPlanError(f"boundary views differ from {doc_id}")
        elif raw_views:
            raise StageDPlanError(f"non-boundary fixture has views: {doc_id}")
        views = tuple(D50BoundaryView(
            item["chunk_chars"], item["sha256"], item["text"])
            for item in raw_views)
        documents.append(D50Document(
            doc_id, row["stratum"], row["sha256"],
            tuple(row["categories_present"]), tuple(row["expected_identifiers"]),
            text, views,
        ))
    if tuple(document.doc_id for document in documents) != tuple(master["split"]["d"]):
        raise StageDPlanError("D50 fixture order drifted")
    return D50Corpus(master_manifest_sha256, tuple(master["split"]["d"]),
                     tuple(documents))


def verified_run_nonce_key(
        key_manifest: Mapping[str, Any] | str,
        frozen_c_plan: legacy_plan.StagePlan | Mapping[str, Any] | str, *,
        corpus: goldset.GoldSet | None = None,
) -> bytes:
    """Decode the owner-only key only after it re-derives the frozen C plan exactly."""
    value = _mapping(key_manifest, "run nonce-key manifest")
    if (set(value) != {"version", "key_hex"}
            or value.get("version") != "c0b2-run-nonce-key-v1"
            or type(value.get("key_hex")) is not str
            or not _KEY_RE.fullmatch(value["key_hex"])):
        raise StageDPlanError("run nonce-key manifest is malformed")
    key = bytes.fromhex(value["key_hex"])
    try:
        expected = legacy_plan.stage_plan_payload(
            legacy_plan.build_c_stage_plan(key, corpus))
        if isinstance(frozen_c_plan, legacy_plan.StagePlan):
            stored = legacy_plan.stage_plan_payload(frozen_c_plan)
        else:
            stored = _mapping(frozen_c_plan, "frozen Stage-C plan")
    except (TypeError, ValueError, legacy_plan.PlanError, goldset.GoldSetError) as exc:
        raise StageDPlanError("Stage-C plan cannot be re-derived") from exc
    if canonical_json(stored) != canonical_json(expected):
        raise StageDPlanError("run nonce key does not re-derive the frozen Stage-C plan")
    return key


def d1_candidates_from_stage_c_selection(
        selection: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Translate a strict activated Stage-C selection to D1's null-factor rows."""
    try:
        normalized = validate_stage_c_selection(selection)
    except (TypeError, ValueError) as exc:
        raise StageDPlanError("Stage-C selection is invalid") from exc
    rows = [{
        "candidate_id": _candidate_id(row),
        "model": row["model"], "model_digest": row["model_digest"],
        "worksheet": row["worksheet"], "chunk_chars": None, "overlap": None,
        "num_ctx": None, "num_predict": None,
    } for row in normalized["survivors"]]
    return _candidates(rows, "D1")


def _candidate_id(row: Mapping[str, Any]) -> str:
    from .c0b2_public_schema import stage_d_candidate_id
    return stage_d_candidate_id(row["model"], row["model_digest"], row["worksheet"])


def _candidates(rows: Sequence[Mapping[str, Any]], phase: str) -> list[dict[str, Any]]:
    if isinstance(rows, (str, bytes)) or not 1 <= len(rows) <= 3:
        raise StageDPlanError("Stage-D candidates must contain one to three rows")
    normalized: list[dict[str, Any]] = []
    try:
        for row in rows:
            normalized.append(DPhaseCandidate.model_validate(
                row, strict=True).model_dump(mode="json"))
    except (TypeError, ValueError) as exc:
        raise StageDPlanError(f"{phase} candidate rows are invalid") from exc
    indices: list[int] = []
    for row in normalized:
        model = _MODEL_ROWS.get(row["model"])
        if model is None or (row["model_digest"] != model[1]):
            raise StageDPlanError("Stage-D candidate is outside the frozen model registry")
        indices.append(model[0])
    if indices != sorted(set(indices)):
        raise StageDPlanError("Stage-D candidate order differs from Stage-C survivor order")
    expected_presence = DPhasePlan._PRESENCE[phase]
    if any(tuple(row[name] is not None for name in
                 ("chunk_chars", "overlap", "num_ctx", "num_predict"))
           != expected_presence for row in normalized):
        raise StageDPlanError(f"{phase} candidate factor presence is invalid")
    return normalized


def _generation_config(model: str, num_ctx: int, num_predict: int) -> dict[str, Any]:
    model_row = _MODEL_ROWS.get(model)
    if model_row is None:
        raise StageDPlanError("unknown Stage-D model")
    return {
        "keep_alive": legacy_plan.KEEP_ALIVE,
        "options": {
            "min_p": 0.0, "num_ctx": num_ctx, "num_predict": num_predict,
            "repeat_last_n": 0, "repeat_penalty": 1.0, "seed": SEED,
            "temperature": 0.0, "top_k": 1, "top_p": 1.0,
        },
        "think": model_row[2],
    }


def _request_payload(model: str, worksheet: str, prompt: str,
                     num_ctx: int, num_predict: int) -> dict[str, Any]:
    config = _generation_config(model, num_ctx, num_predict)
    return {
        "model": model, "messages": [{"role": "user", "content": prompt}],
        "stream": True, "format": worksheet_schema(worksheet),
        "options": config["options"], "think": config["think"],
        "keep_alive": config["keep_alive"],
    }


def _source(document: D50Document, chunk_chars: int) -> tuple[str, str | None]:
    view = document.view_for(chunk_chars)
    if document.stratum == "boundary":
        if view is None:
            raise StageDPlanError(f"missing derived boundary view for {document.doc_id}")
        return view.text, view.view_id
    if view is not None or document.boundary_views:
        raise StageDPlanError(f"unexpected derived view for {document.doc_id}")
    return document.text, None


def _work_rows(*, phase: str, plan_key: str, candidates: list[dict[str, Any]],
               corpus: D50Corpus, run_nonce_key: bytes) -> list[dict[str, Any]]:
    if type(run_nonce_key) is not bytes or len(run_nonce_key) != 32:
        raise StageDPlanError("Stage-D run nonce key must contain exactly 32 bytes")
    documents = corpus.by_id()
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        if phase == "D1":
            factors = [(4000, 8192, budget) for budget in _D1_BUDGETS[candidate["model"]]]
            doc_ids = D1_PANEL
            nonce_domain = "D1"
        elif phase == "D2":
            factors = [(size, 16384, candidate["num_predict"]) for size in D2_CHUNKS]
            doc_ids = tuple(doc_id for doc_id in corpus.document_order
                            if documents[doc_id].stratum == "boundary")
            nonce_domain = "D2"
        elif phase == "D3":
            factors = [(candidate["chunk_chars"], 16384, candidate["num_predict"])]
            doc_ids = corpus.document_order
            nonce_domain = "D34"
        elif phase == "D4":
            factors = [(candidate["chunk_chars"], candidate["num_ctx"],
                        candidate["num_predict"])]
            doc_ids = corpus.document_order
            nonce_domain = "D34"
        else:  # pragma: no cover - private helper has closed callers
            raise StageDPlanError("unknown Stage-D phase")
        for chunk_chars, num_ctx, num_predict in factors:
            cell_id = public_cell_id(
                budget_stage="D", candidate_id=candidate["candidate_id"],
                chunk_chars=chunk_chars, num_ctx=num_ctx, num_predict=num_predict,
                phase=phase, seed=SEED,
            )
            for doc_id in doc_ids:
                document = documents.get(doc_id)
                if document is None:
                    raise StageDPlanError(f"D50 corpus lacks planned document {doc_id}")
                source, view_id = _source(document, chunk_chars)
                identity = document_view_identity(
                    doc_id=doc_id, document_sha256=document.document_sha256,
                    view_sha256=view_id,
                )
                nonce = derive_nonce(
                    run_nonce_key, nonce_domain=nonce_domain,
                    document_view_identity=identity, seed=SEED,
                    worksheet=candidate["worksheet"],
                )
                chunks = chunker.chunk(
                    source, chunk_chars=chunk_chars, overlap_chars=OVERLAP)
                if phase == "D1" and len(chunks) != 1:
                    raise StageDPlanError(f"D1 panel item is not one chunk: {doc_id}")
                if phase == "D2" and len(chunks) != 2:
                    raise StageDPlanError(f"D2 boundary view is not two chunks: {doc_id}")
                for item in chunks:
                    prompt = build_prompt(candidate["worksheet"], item.text, nonce)
                    payload = _request_payload(
                        candidate["model"], candidate["worksheet"], prompt,
                        num_ctx, num_predict,
                    )
                    request_hash = stable_hash(payload)
                    chunk_hash = hashlib.sha256(item.text.encode("utf-8")).hexdigest()
                    work_id = public_work_id(
                        cell_id=cell_id, chunk_index=item.index,
                        chunk_sha256=chunk_hash, doc_id=doc_id,
                        document_sha256=document.document_sha256, nonce=nonce,
                        plan_key=plan_key, request_sha256=request_hash,
                        view_id=view_id,
                    )
                    rows.append({
                        "stage": "D", "phase": phase, "plan_key": plan_key,
                        "budget_stage": "D", "activation_group_id": None,
                        "candidate_id": candidate["candidate_id"], "cell_id": cell_id,
                        "work_id": work_id, "model": candidate["model"],
                        "model_digest": candidate["model_digest"],
                        "worksheet": candidate["worksheet"], "doc_id": doc_id,
                        "view_id": view_id,
                        "document_sha256": document.document_sha256,
                        "chunk_chars": chunk_chars, "overlap": OVERLAP,
                        "num_ctx": num_ctx, "num_predict": num_predict, "seed": SEED,
                        "chunk_index": item.index, "chunk_sha256": chunk_hash,
                        "nonce": nonce,
                        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                        "request_sha256": request_hash,
                    })
    return rows


def _build_plan(*, phase: str, plan_key: str, parent_decision_sha256: str,
                candidates: Sequence[Mapping[str, Any]], corpus: D50Corpus,
                run_nonce_key: bytes) -> dict[str, Any]:
    _sha256(parent_decision_sha256, "parent decision hash")
    normalized = _candidates(candidates, phase)
    if phase == "D4" and any(row["num_ctx"] == 16384 for row in normalized):
        raise StageDPlanError("D4 may contain only lower-context rerun candidates")
    payload = {
        "version": "stage-d-phase-plan-v1", "stage": "D", "phase": phase,
        "plan_key": plan_key, "budget_stage": "D",
        "parent_decision_sha256": parent_decision_sha256,
        "candidates": normalized,
        "work": _work_rows(
            phase=phase, plan_key=plan_key, candidates=normalized,
            corpus=corpus, run_nonce_key=run_nonce_key),
    }
    try:
        return DPhasePlan.model_validate(payload, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise StageDPlanError(f"generated {phase} plan violates the strict schema") from exc


def build_d1_plan(parent_decision_sha256: str,
                  candidates: Sequence[Mapping[str, Any]], *,
                  corpus: D50Corpus, run_nonce_key: bytes) -> dict[str, Any]:
    """Build candidate→ascending-output-budget→D1-panel work."""
    return _build_plan(
        phase="D1", plan_key="D1_OUTPUT",
        parent_decision_sha256=parent_decision_sha256, candidates=candidates,
        corpus=corpus, run_nonce_key=run_nonce_key)


def build_d2_plan(parent_decision_sha256: str,
                  candidates: Sequence[Mapping[str, Any]], *,
                  corpus: D50Corpus, run_nonce_key: bytes) -> dict[str, Any]:
    """Build candidate→ascending-chunk→boundary-document→chunk D2 work."""
    return _build_plan(
        phase="D2", plan_key="D2_CHUNK",
        parent_decision_sha256=parent_decision_sha256, candidates=candidates,
        corpus=corpus, run_nonce_key=run_nonce_key)


def build_d3_plan(parent_decision_sha256: str,
                  candidates: Sequence[Mapping[str, Any]], *,
                  corpus: D50Corpus, run_nonce_key: bytes) -> dict[str, Any]:
    """Build complete selected-chunk D50 work at actual context 16384."""
    return _build_plan(
        phase="D3", plan_key="D3_CONTEXT",
        parent_decision_sha256=parent_decision_sha256, candidates=candidates,
        corpus=corpus, run_nonce_key=run_nonce_key)


def build_d4_plan(parent_decision_sha256: str,
                  candidates: Sequence[Mapping[str, Any]], *,
                  corpus: D50Corpus, run_nonce_key: bytes) -> dict[str, Any]:
    """Build only lower-context complete-D50 confirmation reruns."""
    return _build_plan(
        phase="D4", plan_key="D4_CONFIRMATION",
        parent_decision_sha256=parent_decision_sha256, candidates=candidates,
        corpus=corpus, run_nonce_key=run_nonce_key)


_BUILDERS = {
    "D1": build_d1_plan, "D2": build_d2_plan,
    "D3": build_d3_plan, "D4": build_d4_plan,
}


def validate_d_plan(plan: Mapping[str, Any] | str, *, corpus: D50Corpus,
                    run_nonce_key: bytes) -> dict[str, Any]:
    """Strictly parse and independently re-derive a complete Stage-D phase plan."""
    raw = _mapping(plan, "Stage-D plan")
    try:
        normalized = DPhasePlan.model_validate(raw, strict=True).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise StageDPlanError("Stage-D plan fails its strict schema") from exc
    expected = _BUILDERS[normalized["phase"]](
        normalized["parent_decision_sha256"], normalized["candidates"],
        corpus=corpus, run_nonce_key=run_nonce_key,
    )
    if canonical_json(normalized) != canonical_json(expected):
        raise StageDPlanError("Stage-D plan differs from independent re-derivation")
    return normalized


def resolve_d_work(plan: Mapping[str, Any] | str, work_id: str, *,
                   corpus: D50Corpus, run_nonce_key: bytes) -> dict[str, Any]:
    """Resolve one exact planned D request and its public source/chunk without I/O."""
    _sha256(work_id, "work ID")
    normalized = validate_d_plan(plan, corpus=corpus, run_nonce_key=run_nonce_key)
    matches = [row for row in normalized["work"] if row["work_id"] == work_id]
    if len(matches) != 1:
        raise StageDPlanError(f"unknown or duplicate Stage-D work {work_id}")
    work = matches[0]
    document = corpus.by_id().get(work["doc_id"])
    if document is None:
        raise StageDPlanError("resolved Stage-D document is absent")
    source, view_id = _source(document, work["chunk_chars"])
    chunks = chunker.chunk(
        source, chunk_chars=work["chunk_chars"], overlap_chars=work["overlap"])
    if work["chunk_index"] >= len(chunks):
        raise StageDPlanError("resolved Stage-D chunk index is absent")
    item = chunks[work["chunk_index"]]
    prompt = build_prompt(work["worksheet"], item.text, work["nonce"])
    payload = _request_payload(
        work["model"], work["worksheet"], prompt,
        work["num_ctx"], work["num_predict"],
    )
    if (view_id != work["view_id"]
            or hashlib.sha256(item.text.encode("utf-8")).hexdigest()
            != work["chunk_sha256"]
            or hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            != work["prompt_sha256"]
            or stable_hash(payload) != work["request_sha256"]):
        raise StageDPlanError("resolved Stage-D request differs from frozen work")
    return {
        "work": dict(work), "source": source, "chunk_text": item.text,
        "prompt": prompt, "payload": payload,
    }


def request_spec_for_d_work(plan: Mapping[str, Any] | str, work_id: str, *,
                            corpus: D50Corpus,
                            run_nonce_key: bytes) -> RequestSpec:
    """Return the transport's exact immutable chat specification for planned D work."""
    resolved = resolve_d_work(
        plan, work_id, corpus=corpus, run_nonce_key=run_nonce_key)
    return RequestSpec(
        kind="chat", payload=resolved["payload"],
        worksheet=resolved["work"]["worksheet"],
        expected_model=resolved["work"]["model"],
        expected_digest=resolved["work"]["model_digest"],
    )


def derive_d_context_controls(plan: Mapping[str, Any] | str, *,
                              corpus: D50Corpus,
                              run_nonce_key: bytes) -> list[dict[str, Any]]:
    """Derive one ordered, phase-bound context control per D3/D4 candidate."""
    normalized = validate_d_plan(plan, corpus=corpus, run_nonce_key=run_nonce_key)
    phase = normalized["phase"]
    if phase not in {"D3", "D4"}:
        raise StageDPlanError("context controls exist only for D3/D4")
    purpose = "d3_context_16384" if phase == "D3" else "d4_context_selected"
    trigger = "first_http_terminal_d3" if phase == "D3" else "first_http_terminal_d4"
    controls: list[dict[str, Any]] = []
    for candidate in normalized["candidates"]:
        rows = [row for row in normalized["work"]
                if row["candidate_id"] == candidate["candidate_id"]]
        if not rows:
            raise StageDPlanError("context-control candidate has no planned work")
        first = rows[0]
        minimum = 16384 if phase == "D3" else candidate["num_ctx"]
        config = _generation_config(
            candidate["model"], first["num_ctx"], candidate["num_predict"])
        config_hash = sha256_json(config)
        spec = RequestSpec(
            kind="ps", expected_model=candidate["model"],
            expected_digest=candidate["model_digest"], min_context=minimum,
            purpose=purpose, config_sha256=config_hash,
        )
        payload_hash = request_spec_hash(spec)
        control = {
            "control_id": context_control_id(
                candidate_id=candidate["candidate_id"],
                config_sha256=config_hash, model=candidate["model"],
                model_digest=candidate["model_digest"],
                payload_sha256=payload_hash, purpose=purpose,
            ),
            "kind": "context_probe", "purpose": purpose,
            "candidate_id": candidate["candidate_id"], "model": candidate["model"],
            "model_digest": candidate["model_digest"],
            "config_sha256": config_hash, "minimum_context_length": minimum,
            "trigger_rule": trigger, "payload_sha256": payload_hash,
        }
        try:
            controls.append(StageDContextControl.model_validate(
                control, strict=True).model_dump(mode="json"))
        except (TypeError, ValueError) as exc:
            raise StageDPlanError("derived Stage-D context control is invalid") from exc
    return controls
