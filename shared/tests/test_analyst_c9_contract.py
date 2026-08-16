"""Hostile offline tests for the frozen C9 Ollama request contract."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from experimental.analyst import ollama_contract as contract
from experimental.analyst.ollama_contract import (
    ChatMetrics,
    ChatRequest,
    ChatResult,
    ContractError,
    GenerationOptions,
    OllamaIdentity,
    OllamaStatus,
    PreflightResult,
    build_chat_request,
    canonical_json,
    new_prompt_nonce,
    validate_chat_request,
)
from experimental.analyst.worksheet import worksheet_schema


_NONCE = "FENCE_0123456789ABCDEF"


def _metrics(**changes: object) -> ChatMetrics:
    values: dict[str, object] = {
        "done_reason": "stop",
        "prompt_eval_count": 10,
        "eval_count": 4,
        "total_duration_ns": 100,
        "load_duration_ns": 20,
        "prompt_eval_duration_ns": 30,
        "eval_duration_ns": 40,
        "raw_body_bytes": 200,
        "content_bytes": 2,
        "thinking_bytes": 0,
    }
    values.update(changes)
    return ChatMetrics(**values)  # type: ignore[arg-type]


def _forged_request(**changes: object) -> ChatRequest:
    source = build_chat_request("public", nonce=_NONCE)
    forged = object.__new__(ChatRequest)
    for name in (
        "source_text", "nonce", "body", "request_sha256", "prompt_kind", "model_tag",
        "model_digest", "endpoint",
    ):
        object.__setattr__(forged, name, changes.get(name, getattr(source, name)))
    return forged


def test_frozen_identity_urls_limits_and_timeouts_are_exact() -> None:
    assert contract.OLLAMA_ENDPOINT == "http://127.0.0.1:11434"
    assert (
        contract.OLLAMA_VERSION_URL,
        contract.OLLAMA_TAGS_URL,
        contract.OLLAMA_PS_URL,
        contract.OLLAMA_CHAT_URL,
    ) == (
        "http://127.0.0.1:11434/api/version",
        "http://127.0.0.1:11434/api/tags",
        "http://127.0.0.1:11434/api/ps",
        "http://127.0.0.1:11434/api/chat",
    )
    assert contract.MODEL_TAG == "qwen3.6:27b"
    assert contract.MODEL_DIGEST == (
        "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e"
    )
    assert contract.QUALIFIED_OLLAMA_VERSION == "0.32.5"
    assert (
        contract.MAX_SOURCE_CHARS,
        contract.MAX_PROMPT_BYTES,
        contract.MAX_FRAME_BYTES,
        contract.MAX_BODY_BYTES,
        contract.MAX_CONTENT_BYTES,
        contract.MAX_COMBINED_CHANNEL_BYTES,
        contract.MAX_CANONICAL_JSON_BYTES,
        contract.MAX_JSON_DEPTH,
        contract.MAX_JSON_NODES,
        contract.MAX_SHOW_JSON_NODES,
    ) == (8000, 65536, 524288, 2097152, 262144, 1048576, 262144, 16, 4096, 16384)
    assert (
        contract.CONNECT_TIMEOUT_SECONDS,
        contract.IDLE_READ_TIMEOUT_SECONDS,
        contract.TOTAL_REQUEST_SECONDS,
        contract.CANCEL_HEALTH_DELAY_SECONDS,
    ) == (10.0, 180.0, 600.0, 2.0)


def test_status_vocabulary_is_closed_and_exact() -> None:
    assert {item.value for item in OllamaStatus} == {
        "success",
        "model_invalid",
        "cancelled_unverified",
        "request_timeout",
        "resource_busy",
        "transport_unavailable",
        "protocol_violation",
        "response_limit",
        "identity_mismatch",
    }


def test_generation_options_have_exact_fields_values_and_json_types() -> None:
    options = contract.GENERATION_OPTIONS.as_payload()
    assert options == {
        "temperature": 0.0,
        "top_p": 1.0,
        "top_k": 1,
        "min_p": 0.0,
        "repeat_penalty": 1.0,
        "repeat_last_n": 0,
        "seed": 1,
        "num_ctx": 8192,
        "num_predict": 1024,
    }
    assert tuple(type(options[name]) for name in options) == (
        float, float, int, float, float, int, int, int, int,
    )
    assert contract.GENERATION_OPTIONS.as_payload() is not options


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", -0.0),
        ("temperature", 0),
        ("top_p", 1),
        ("top_k", True),
        ("min_p", -0.0),
        ("repeat_penalty", 1),
        ("repeat_last_n", False),
        ("seed", 2),
        ("num_ctx", 16384),
        ("num_predict", 1025),
    ],
)
def test_generation_options_reject_value_or_type_drift(
    field: str, value: object,
) -> None:
    values = contract.GENERATION_OPTIONS.as_payload()
    values[field] = value  # type: ignore[assignment]
    with pytest.raises(ContractError, match="frozen benchmark"):
        GenerationOptions(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"endpoint": "http://localhost:11434"},
        {"endpoint": "http://127.0.0.1:11435"},
        {"model_tag": "qwen3.6:27b-cloud"},
        {"model_digest": "a" * 64},
        {"model_digest": contract.MODEL_DIGEST.upper()},
    ],
)
def test_identity_rejects_every_nonfrozen_value(changes: dict[str, str]) -> None:
    with pytest.raises(ContractError, match="identity"):
        OllamaIdentity(**changes)


def test_chat_request_payload_and_hash_are_exact_and_deterministic() -> None:
    first = build_chat_request("Public sample", nonce=_NONCE)
    second = build_chat_request("Public sample", nonce=_NONCE)

    assert first == second
    assert first.body == canonical_json(first.payload())
    assert first.request_sha256 == hashlib.sha256(first.body).hexdigest()
    payload = first.payload()
    assert set(payload) == {
        "model", "messages", "stream", "format", "options", "think", "keep_alive",
    }
    assert payload["model"] == contract.MODEL_TAG
    assert payload["stream"] is True
    assert payload["format"] == worksheet_schema()
    assert payload["options"] == contract.GENERATION_OPTIONS.as_payload()
    assert payload["think"] is False
    assert payload["keep_alive"] == "15m"
    assert payload["messages"][0]["role"] == "user"
    assert _NONCE in payload["messages"][0]["content"]
    assert "Public sample" in payload["messages"][0]["content"]


def test_payload_returns_detached_data_and_repr_hides_prompt() -> None:
    request = build_chat_request("DO_NOT_ECHO_PUBLIC_MARKER", nonce=_NONCE)
    payload = request.payload()
    payload["model"] = "forged"
    payload["messages"][0]["content"] = "forged"

    assert request.payload()["model"] == contract.MODEL_TAG
    assert "DO_NOT_ECHO_PUBLIC_MARKER" not in repr(request)
    assert _NONCE not in repr(request)
    assert "body=" not in repr(request)


@pytest.mark.parametrize("size", [1, contract.MAX_SOURCE_CHARS])
def test_source_character_bounds_accept_n(size: int) -> None:
    request = build_chat_request("x" * size, nonce=_NONCE)
    validate_chat_request(request)


@pytest.mark.parametrize("source", ["", "x" * (contract.MAX_SOURCE_CHARS + 1)])
def test_source_character_bounds_reject_outside_n(source: str) -> None:
    with pytest.raises(ContractError, match="chunk bound"):
        build_chat_request(source, nonce=_NONCE)


@pytest.mark.parametrize(
    "nonce",
    ["", "FENCE_0123456789ABCDE", "FENCE_0123456789abcdef", "0123456789ABCDEF"],
)
def test_nonce_must_match_exact_uppercase_shape(nonce: str) -> None:
    with pytest.raises(ContractError, match="nonce"):
        build_chat_request("public", nonce=nonce)


def test_nonce_collision_is_rejected() -> None:
    with pytest.raises(ContractError, match="nonce"):
        build_chat_request(f"source includes {_NONCE}", nonce=_NONCE)


def test_nonce_generation_retries_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    values = iter(("0123456789abcdef", "fedcba9876543210"))
    monkeypatch.setattr(contract.secrets, "token_hex", lambda _size: next(values))
    assert new_prompt_nonce(f"contains {_NONCE}") == "FENCE_FEDCBA9876543210"


@pytest.mark.parametrize("source", [None, b"public", 1])
def test_nonce_generation_requires_exact_string(source: object) -> None:
    with pytest.raises(TypeError):
        new_prompt_nonce(source)  # type: ignore[arg-type]


def test_request_revalidation_rejects_body_hash_identity_and_canonical_drift() -> None:
    valid = build_chat_request("public", nonce=_NONCE)
    cases = (
        _forged_request(body=valid.body + b" "),
        _forged_request(request_sha256="0" * 64),
        _forged_request(model_tag="other"),
        _forged_request(model_digest="0" * 64),
        _forged_request(endpoint="http://localhost:11434"),
        _forged_request(source_text="different public source"),
        _forged_request(nonce="FENCE_FEDCBA9876543210"),
    )
    for forged in cases:
        with pytest.raises(ContractError):
            validate_chat_request(forged)


def test_request_revalidation_rejects_counterfeit_extra_or_duplicate_fields() -> None:
    valid = build_chat_request("public", nonce=_NONCE)
    payload = valid.payload()
    payload["tools"] = []
    extra = canonical_json(payload)
    with pytest.raises(ContractError, match="field set"):
        validate_chat_request(_forged_request(
            body=extra, request_sha256=hashlib.sha256(extra).hexdigest(),
        ))

    duplicate = valid.body[:-1] + b',"model":"qwen3.6:27b"}'
    with pytest.raises(ContractError, match="duplicate"):
        validate_chat_request(_forged_request(
            body=duplicate, request_sha256=hashlib.sha256(duplicate).hexdigest(),
        ))


def test_canonical_json_rejects_nan_and_nonserializable_values() -> None:
    with pytest.raises(ContractError):
        canonical_json({"x": float("nan")})
    with pytest.raises(ContractError):
        canonical_json({"x": object()})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt_eval_count", True),
        ("eval_count", -1),
        ("raw_body_bytes", contract.MAX_BODY_BYTES + 1),
        ("content_bytes", contract.MAX_CONTENT_BYTES + 1),
        ("done_reason", ""),
        ("done_reason", "x" * 81),
    ],
)
def test_chat_metrics_reject_invalid_types_and_n_plus_one(
    field: str, value: object,
) -> None:
    with pytest.raises(ContractError):
        _metrics(**{field: value})


def test_chat_metrics_accept_exact_channel_limits() -> None:
    metrics = _metrics(
        raw_body_bytes=contract.MAX_BODY_BYTES,
        content_bytes=contract.MAX_CONTENT_BYTES,
        thinking_bytes=(
            contract.MAX_COMBINED_CHANNEL_BYTES - contract.MAX_CONTENT_BYTES
        ),
    )
    assert metrics.content_bytes + metrics.thinking_bytes == (
        contract.MAX_COMBINED_CHANNEL_BYTES
    )


def test_chat_result_enforces_status_content_metrics_matrix() -> None:
    success = ChatResult(OllamaStatus.SUCCESS, "{}", _metrics())
    invalid = ChatResult(
        OllamaStatus.MODEL_INVALID,
        None,
        _metrics(done_reason="length", content_bytes=0),
    )
    assert success.content == "{}"
    assert invalid.content is None
    for status in set(OllamaStatus) - {
        OllamaStatus.SUCCESS, OllamaStatus.MODEL_INVALID,
    }:
        assert ChatResult(status).status is status

    with pytest.raises(ContractError):
        ChatResult(OllamaStatus.SUCCESS, None, _metrics())
    with pytest.raises(ContractError):
        ChatResult(OllamaStatus.SUCCESS, "x", _metrics())
    with pytest.raises(ContractError):
        ChatResult(OllamaStatus.MODEL_INVALID, "raw", _metrics(done_reason="length"))
    with pytest.raises(ContractError):
        ChatResult(OllamaStatus.TRANSPORT_UNAVAILABLE, "raw", None)


def test_preflight_result_retains_identity_only_on_success() -> None:
    result = PreflightResult(
        OllamaStatus.SUCCESS,
        contract.QUALIFIED_OLLAMA_VERSION,
        contract.MODEL_DIGEST,
    )
    assert result.observed_version == contract.QUALIFIED_OLLAMA_VERSION
    assert result.model_digest == contract.MODEL_DIGEST
    for status in set(OllamaStatus) - {
        OllamaStatus.SUCCESS, OllamaStatus.MODEL_INVALID,
    }:
        assert PreflightResult(status).observed_version is None
    with pytest.raises(ContractError):
        PreflightResult(OllamaStatus.SUCCESS, None, contract.MODEL_DIGEST)
    with pytest.raises(ContractError):
        PreflightResult(
            OllamaStatus.IDENTITY_MISMATCH,
            contract.QUALIFIED_OLLAMA_VERSION,
            contract.MODEL_DIGEST,
        )


def test_request_body_is_valid_utf8_json_without_ascii_escaping() -> None:
    request = build_chat_request("café", nonce=_NONCE)
    assert "café" in request.body.decode("utf-8")
    assert json.loads(request.body)["model"] == contract.MODEL_TAG


def test_c9_pure_modules_have_no_database_network_or_path_imports() -> None:
    package = Path(__file__).resolve().parents[2] / "experimental" / "analyst"
    banned = {
        "pathlib", "sqlite3", "subprocess", "socket", "urllib", "requests",
        "httpx", "fitz", "pymupdf", "docx", "openpyxl", "xlrd",
    }
    offenders: list[str] = []
    for name in ("ollama_contract.py", "ollama_protocol.py", "resource_policy.py"):
        path = package / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                if module.split(".")[0] in banned:
                    offenders.append(f"{name}:{node.lineno} imports {module}")
    assert not offenders, offenders
