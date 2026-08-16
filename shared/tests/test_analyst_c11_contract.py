"""Pure offline tests for the frozen C11 request-contract additions."""

from __future__ import annotations

import hashlib

import pytest

from experimental.analyst import worksheet
from experimental.analyst.ollama_contract import (
    MODEL_DIGEST,
    ChatRequest,
    ContractError,
    OllamaStatus,
    PromptKind,
    TagsCheckResult,
    VersionCheckResult,
    build_chat_request,
    build_repair_chat_request,
    validate_chat_request,
)
from experimental.analyst.phase2_contract import (
    HEALTH_NONCE,
    HEALTH_REQUEST_SHA256,
    HEALTH_SOURCE,
    Phase2ContractError,
    Phase2AttemptIdentity,
    Phase2ChunkSnapshot,
    Phase2FileCompletion,
    Phase2FileSnapshot,
    Phase2Handoff,
    Phase2Outcome,
    build_health_chat_request,
    derive_nonce,
    HealthObligation,
)
from experimental.analyst.inventory import InventoryFile
from experimental.analyst.lease import LeaseFence
from experimental.analyst.models import FileStage, FileTerminal
from experimental.analyst.process_identity import ProcessIdentity
from experimental.analyst.state import AttemptState, ChunkState
from experimental.analyst.worker_contract import Phase1ChunkIdentity


_NONCE = "FENCE_0123456789ABCDEF"
_CHUNK_SHA = hashlib.sha256(b"public chunk").hexdigest()


def _forge(request: ChatRequest, **changes: object) -> ChatRequest:
    forged = object.__new__(ChatRequest)
    for name in (
        "source_text", "nonce", "body", "request_sha256", "prompt_kind",
        "model_tag", "model_digest", "endpoint",
    ):
        object.__setattr__(forged, name, changes.get(name, getattr(request, name)))
    return forged


def test_prompt_kind_vocabulary_is_closed_and_exact() -> None:
    assert {kind.value for kind in PromptKind} == {
        "primary", "model_invalid_repair",
    }


def test_primary_and_repair_requests_are_deterministic_distinct_identities() -> None:
    primary = build_chat_request("Public synthetic text", nonce=_NONCE)
    repair = build_repair_chat_request("Public synthetic text", nonce=_NONCE)
    repeated = build_repair_chat_request("Public synthetic text", nonce=_NONCE)

    assert primary.prompt_kind is PromptKind.PRIMARY
    assert repair.prompt_kind is PromptKind.MODEL_INVALID_REPAIR
    assert repair == repeated
    assert primary.body != repair.body
    assert primary.request_sha256 != repair.request_sha256
    assert repair.request_sha256 == hashlib.sha256(repair.body).hexdigest()
    assert primary.payload().keys() == repair.payload().keys()
    assert primary.payload()["options"] == repair.payload()["options"]
    assert primary.payload()["messages"] != repair.payload()["messages"]
    validate_chat_request(primary)
    validate_chat_request(repair)


def test_repair_prompt_is_error_specific_but_contains_no_prior_answer() -> None:
    marker = "PUBLIC_SOURCE_MARKER"
    request = build_repair_chat_request(marker, nonce=_NONCE)
    prompt = request.payload()["messages"][0]["content"]

    assert marker in prompt
    assert prompt.count(marker) == 1
    assert "prior answer did not satisfy" in prompt
    assert "do not repeat, quote or discuss any prior answer" in " ".join(prompt.split())
    assert "prior_response" not in prompt
    assert marker not in repr(request)
    assert _NONCE not in repr(request)


@pytest.mark.parametrize(
    ("builder", "other_kind"),
    [
        (build_chat_request, PromptKind.MODEL_INVALID_REPAIR),
        (build_repair_chat_request, PromptKind.PRIMARY),
    ],
)
def test_request_revalidation_rejects_prompt_kind_body_mismatch(
    builder, other_kind: PromptKind,
) -> None:
    request = builder("Public synthetic text", nonce=_NONCE)
    with pytest.raises(ContractError):
        validate_chat_request(_forge(request, prompt_kind=other_kind))


def test_request_revalidation_rejects_prompt_kind_subclass_or_missing_field() -> None:
    request = build_chat_request("Public synthetic text", nonce=_NONCE)
    forged = _forge(request)
    object.__setattr__(forged, "prompt_kind", "primary")
    with pytest.raises(ContractError, match="identity"):
        validate_chat_request(forged)

    missing = object.__new__(ChatRequest)
    for name in (
        "source_text", "nonce", "body", "request_sha256", "model_tag",
        "model_digest", "endpoint",
    ):
        object.__setattr__(missing, name, getattr(request, name))
    with pytest.raises(ContractError, match="identity"):
        validate_chat_request(missing)


def test_repair_prompt_identity_is_hash_pinned_and_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert worksheet.repair_prompt_template_hash() == (
        worksheet.EXPECTED_REPAIR_PROMPT_TEMPLATE_SHA256
    )
    monkeypatch.setattr(
        worksheet, "_MODEL_INVALID_REPAIR", worksheet._MODEL_INVALID_REPAIR + " ",
    )
    with pytest.raises(RuntimeError, match="repair prompt drifted"):
        worksheet.repair_prompt_template_hash()


def test_split_control_results_are_content_free_on_every_failure() -> None:
    allowed_failures = set(OllamaStatus) - {
        OllamaStatus.SUCCESS, OllamaStatus.MODEL_INVALID,
    }
    for status in allowed_failures:
        assert VersionCheckResult(status).observed_version is None
        assert TagsCheckResult(status).model_digest is None

    with pytest.raises(ContractError):
        VersionCheckResult(OllamaStatus.MODEL_INVALID)
    with pytest.raises(ContractError):
        TagsCheckResult(OllamaStatus.MODEL_INVALID)
    with pytest.raises(ContractError):
        VersionCheckResult(OllamaStatus.SUCCESS)
    with pytest.raises(ContractError):
        TagsCheckResult(OllamaStatus.SUCCESS)
    with pytest.raises(ContractError):
        VersionCheckResult(OllamaStatus.IDENTITY_MISMATCH, "0.32.5")
    with pytest.raises(ContractError):
        TagsCheckResult(OllamaStatus.IDENTITY_MISMATCH, MODEL_DIGEST)


def test_phase2_outcome_vocabulary_is_closed_and_exact() -> None:
    assert {outcome.value for outcome in Phase2Outcome} == {
        "phase2_handoff", "cancelled", "interrupted", "paused_resource",
    }


def test_nonce_derivation_is_deterministic_prompt_specific_and_collision_safe() -> None:
    primary = derive_nonce(
        "run-public", 7, _CHUNK_SHA, PromptKind.PRIMARY, "Public source",
    )
    repeated = derive_nonce(
        "run-public", 7, _CHUNK_SHA, PromptKind.PRIMARY, "Public source",
    )
    repair = derive_nonce(
        "run-public", 7, _CHUNK_SHA,
        PromptKind.MODEL_INVALID_REPAIR, "Public source",
    )
    after_collision = derive_nonce(
        "run-public", 7, _CHUNK_SHA, PromptKind.PRIMARY,
        f"Public source containing {primary}",
    )

    assert primary == repeated
    assert primary.startswith("FENCE_") and len(primary) == len(_NONCE)
    assert repair != primary
    assert after_collision != primary
    assert after_collision not in f"Public source containing {primary}"


@pytest.mark.parametrize(
    "args",
    [
        ("", 1, _CHUNK_SHA, PromptKind.PRIMARY, "public"),
        ("run", 0, _CHUNK_SHA, PromptKind.PRIMARY, "public"),
        ("run", True, _CHUNK_SHA, PromptKind.PRIMARY, "public"),
        ("run", 1, _CHUNK_SHA.upper(), PromptKind.PRIMARY, "public"),
        ("run", 1, _CHUNK_SHA, "primary", "public"),
        ("run", 1, _CHUNK_SHA, PromptKind.PRIMARY, b"public"),
    ],
)
def test_nonce_derivation_rejects_noncanonical_identity_inputs(args: tuple) -> None:
    with pytest.raises(Phase2ContractError):
        derive_nonce(*args)


def test_health_request_is_public_hash_pinned_and_primary() -> None:
    request = build_health_chat_request()
    assert request.source_text == HEALTH_SOURCE
    assert request.nonce == HEALTH_NONCE
    assert request.prompt_kind is PromptKind.PRIMARY
    assert request.request_sha256 == HEALTH_REQUEST_SHA256
    assert hashlib.sha256(request.body).hexdigest() == HEALTH_REQUEST_SHA256
    assert HEALTH_SOURCE in request.payload()["messages"][0]["content"]
    assert HEALTH_SOURCE not in repr(request)


def test_health_request_fails_closed_if_the_builder_drifts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experimental.analyst import phase2_contract

    original = phase2_contract.build_chat_request

    def drifted(source_text: str, *, nonce: str) -> ChatRequest:
        return original(source_text + " changed", nonce=nonce)

    monkeypatch.setattr(phase2_contract, "build_chat_request", drifted)
    with pytest.raises(RuntimeError, match="health request drifted"):
        phase2_contract.build_health_chat_request()


def _fence() -> LeaseFence:
    return LeaseFence(
        generation=1,
        run_id="run-public",
        owner_token="a" * 64,
        process=ProcessIdentity(
            pid=123,
            start_ticks=456,
            boot_id="00000000-0000-0000-0000-000000000001",
        ),
        heartbeat_monotonic_ns=789,
    )


def _inventory_file() -> InventoryFile:
    return InventoryFile(
        relative_path="public.txt",
        size=12,
        mtime_ns=1,
        ctime_ns=2,
        device=3,
        inode=4,
        mode=0o600,
        sha256=hashlib.sha256(b"public input").hexdigest(),
    )


def _chunk_identity(index: int = 0) -> Phase1ChunkIdentity:
    return Phase1ChunkIdentity(
        chunk_id=index + 1,
        index=index,
        start=index * 10,
        end=index * 10 + 10,
        sha256=hashlib.sha256(f"chunk-{index}".encode()).hexdigest(),
    )


def _attempt(attempt_no: int = 1) -> Phase2AttemptIdentity:
    return Phase2AttemptIdentity(
        attempt_id=hashlib.sha256(f"attempt-{attempt_no}".encode()).hexdigest(),
        attempt_no=attempt_no,
        request_sha256=hashlib.sha256(f"request-{attempt_no}".encode()).hexdigest(),
        state=AttemptState.MODEL_TIMEOUT,
    )


def test_phase2_handoff_is_immutable_content_free_and_hides_fence() -> None:
    handoff = Phase2Handoff(
        fence=_fence(),
        reviewed_file_count=2,
        valid_chunk_count=3,
        retained_finding_count=1,
    )
    assert handoff.fence.run_id == "run-public"
    assert "owner_token" not in repr(handoff)
    assert "a" * 64 not in repr(handoff)
    with pytest.raises(AttributeError):
        handoff.reviewed_file_count = 3  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    [
        {"fence": object()},
        {"reviewed_file_count": True},
        {"valid_chunk_count": -1},
        {"retained_finding_count": 1.0},
    ],
)
def test_phase2_handoff_rejects_invalid_types_and_negative_counts(
    changes: dict[str, object],
) -> None:
    values = {
        "fence": _fence(),
        "reviewed_file_count": 1,
        "valid_chunk_count": 1,
        "retained_finding_count": 0,
    }
    values.update(changes)
    with pytest.raises(Phase2ContractError):
        Phase2Handoff(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("reviewed", "valid_chunks", "findings"),
    [(0, 1, 0), (0, 0, 1), (1, 0, 1)],
)
def test_phase2_handoff_rejects_impossible_count_relationships(
    reviewed: int, valid_chunks: int, findings: int,
) -> None:
    with pytest.raises(Phase2ContractError):
        Phase2Handoff(_fence(), reviewed, valid_chunks, findings)


def test_phase2_attempt_and_chunk_snapshots_are_typed_ordered_and_bounded() -> None:
    first = _attempt(1)
    second = _attempt(2)
    snapshot = Phase2ChunkSnapshot(
        identity=_chunk_identity(),
        state=ChunkState.PENDING,
        attempts=(first, second),
    )
    assert snapshot.attempts == (first, second)

    with pytest.raises(Phase2ContractError):
        Phase2AttemptIdentity(
            first.attempt_id, True, first.request_sha256, first.state,
        )
    with pytest.raises(Phase2ContractError):
        Phase2AttemptIdentity(
            first.attempt_id.upper(), 1, first.request_sha256, first.state,
        )
    with pytest.raises(Phase2ContractError):
        Phase2ChunkSnapshot(_chunk_identity(), ChunkState.PENDING, (second,))
    with pytest.raises(Phase2ContractError):
        Phase2ChunkSnapshot(
            _chunk_identity(), ChunkState.PENDING, (first, second, second),
        )
    with pytest.raises(Phase2ContractError):
        Phase2ChunkSnapshot(_chunk_identity(), ChunkState.PENDING, [first])  # type: ignore[arg-type]


def test_phase2_file_snapshot_is_content_free_typed_and_ordered() -> None:
    chunks = (
        Phase2ChunkSnapshot(_chunk_identity(0), ChunkState.PENDING, ()),
        Phase2ChunkSnapshot(_chunk_identity(1), ChunkState.PENDING, ()),
    )
    snapshot = Phase2FileSnapshot(
        file_id=1,
        ordinal=0,
        inventory_file=_inventory_file(),
        stage=FileStage.SELECTED_FOR_MODEL,
        format_name="text",
        parser_identity_json='{"kind":"public"}',
        extraction_meta_json='{"text_bytes":12,"text_chars":12}',
        chunks=chunks,
    )
    rendered = repr(snapshot)
    assert "public.txt" not in rendered
    assert "parser_identity_json" not in rendered
    assert "extraction_meta_json" not in rendered

    with pytest.raises(Phase2ContractError):
        Phase2FileSnapshot(
            snapshot.file_id,
            snapshot.ordinal,
            snapshot.inventory_file,
            "selected_for_model",  # type: ignore[arg-type]
            snapshot.format_name,
            snapshot.parser_identity_json,
            snapshot.extraction_meta_json,
            snapshot.chunks,
        )
    with pytest.raises(Phase2ContractError):
        Phase2FileSnapshot(
            snapshot.file_id,
            snapshot.ordinal,
            snapshot.inventory_file,
            snapshot.stage,
            type("FormatSubclass", (str,), {})("text"),
            snapshot.parser_identity_json,
            snapshot.extraction_meta_json,
            snapshot.chunks,
        )
    with pytest.raises(Phase2ContractError):
        Phase2FileSnapshot(
            snapshot.file_id,
            snapshot.ordinal,
            snapshot.inventory_file,
            snapshot.stage,
            snapshot.format_name,
            snapshot.parser_identity_json,
            snapshot.extraction_meta_json,
            tuple(reversed(snapshot.chunks)),
        )


@pytest.mark.parametrize(
    "status",
    [
        "request_timeout", "transport_unavailable",
        "cancelled_unverified", "orphaned_unknown",
    ],
)
def test_health_obligation_accepts_only_closed_ambiguous_statuses(status: str) -> None:
    obligation = HealthObligation("a" * 64, 1, status)
    assert obligation.source_status == status


@pytest.mark.parametrize(
    "values",
    [
        ("A" * 64, 1, "request_timeout"),
        ("a" * 64, True, "request_timeout"),
        ("a" * 64, 1, "success"),
        ("a" * 64, 1, type("StatusSubclass", (str,), {})("request_timeout")),
    ],
)
def test_health_obligation_rejects_noncanonical_identity(values: tuple) -> None:
    with pytest.raises(Phase2ContractError):
        HealthObligation(*values)


def test_phase2_file_completion_enforces_terminal_counter_matrix() -> None:
    reviewed = Phase2FileCompletion(
        1, FileTerminal.COMPLETE_MODEL_REVIEWED, 2, 1,
    )
    failed = Phase2FileCompletion(
        2, FileTerminal.MODEL_TIMEOUT, 0, 0,
    )
    assert reviewed.valid_chunk_count == 2
    assert failed.retained_finding_count == 0

    invalid = (
        (1, FileTerminal.COMPLETE_MODEL_REVIEWED, 0, 0),
        (1, FileTerminal.MODEL_TIMEOUT, 1, 0),
        (1, FileTerminal.MODEL_INVALID, 0, 1),
        (1, "complete_model_reviewed", 1, 0),
        (True, FileTerminal.COMPLETE_MODEL_REVIEWED, 1, 0),
    )
    for values in invalid:
        with pytest.raises(Phase2ContractError):
            Phase2FileCompletion(*values)  # type: ignore[arg-type]
