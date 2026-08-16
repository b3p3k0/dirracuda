"""Pure, frozen contracts for the production Analyst Ollama client.

This module owns request identity and content-free result types.  It performs no
network or database I/O; the transport and durable orchestrator are later-card
concerns.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

from .models import ANALYST_DEFAULTS


OLLAMA_ENDPOINT: Final = "http://127.0.0.1:11434"
OLLAMA_VERSION_URL: Final = f"{OLLAMA_ENDPOINT}/api/version"
OLLAMA_TAGS_URL: Final = f"{OLLAMA_ENDPOINT}/api/tags"
OLLAMA_PS_URL: Final = f"{OLLAMA_ENDPOINT}/api/ps"
OLLAMA_CHAT_URL: Final = f"{OLLAMA_ENDPOINT}/api/chat"

MODEL_TAG: Final = "qwen3.6:27b"
MODEL_DIGEST: Final = (
    "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e"
)
WORKSHEET_VERSION: Final = "v2"
QUALIFIED_OLLAMA_VERSION: Final = "0.32.5"

TEMPERATURE: Final = 0.0
TOP_P: Final = 1.0
TOP_K: Final = 1
MIN_P: Final = 0.0
REPEAT_PENALTY: Final = 1.0
REPEAT_LAST_N: Final = 0
SEED: Final = 1
NUM_CTX: Final = 8192
NUM_PREDICT: Final = 1024
KEEP_ALIVE: Final = "15m"

MAX_SOURCE_CHARS: Final = 8000
MAX_PROMPT_BYTES: Final = 64 * 1024
MAX_FRAME_BYTES: Final = 512 * 1024
MAX_BODY_BYTES: Final = 2 * 1024 * 1024
MAX_CONTENT_BYTES: Final = 256 * 1024
MAX_COMBINED_CHANNEL_BYTES: Final = 1024 * 1024
MAX_CANONICAL_JSON_BYTES: Final = 256 * 1024
MAX_JSON_DEPTH: Final = 16
MAX_JSON_NODES: Final = 4096
MAX_SHOW_JSON_NODES: Final = 16_384

CONNECT_TIMEOUT_SECONDS: Final = 10.0
IDLE_READ_TIMEOUT_SECONDS: Final = 180.0
TOTAL_REQUEST_SECONDS: Final = 600.0
CANCEL_HEALTH_DELAY_SECONDS: Final = 2.0

_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_NONCE = re.compile(r"FENCE_[0-9A-F]{16}\Z", re.ASCII)
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}\Z", re.ASCII)
_REQUEST_KEYS = frozenset(
    {"model", "messages", "stream", "format", "options", "think", "keep_alive"}
)
_OPTION_KEYS = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "repeat_penalty",
        "repeat_last_n",
        "seed",
        "num_ctx",
        "num_predict",
    }
)


class ContractError(ValueError):
    """A caller supplied data outside the frozen C9 request contract."""


class OllamaStatus(str, Enum):
    """Closed, privacy-safe outcomes returned by the production client."""

    SUCCESS = "success"
    MODEL_INVALID = "model_invalid"
    CANCELLED_UNVERIFIED = "cancelled_unverified"
    REQUEST_TIMEOUT = "request_timeout"
    RESOURCE_BUSY = "resource_busy"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    PROTOCOL_VIOLATION = "protocol_violation"
    RESPONSE_LIMIT = "response_limit"
    IDENTITY_MISMATCH = "identity_mismatch"


class PromptKind(str, Enum):
    """The two frozen semantic request identities admitted by C11."""

    PRIMARY = "primary"
    MODEL_INVALID_REPAIR = "model_invalid_repair"


@dataclass(frozen=True, slots=True)
class GenerationOptions:
    """The exact generation controls selected by the public benchmark."""

    temperature: float = TEMPERATURE
    top_p: float = TOP_P
    top_k: int = TOP_K
    min_p: float = MIN_P
    repeat_penalty: float = REPEAT_PENALTY
    repeat_last_n: int = REPEAT_LAST_N
    seed: int = SEED
    num_ctx: int = NUM_CTX
    num_predict: int = NUM_PREDICT

    def __post_init__(self) -> None:
        expected = (
            TEMPERATURE,
            TOP_P,
            TOP_K,
            MIN_P,
            REPEAT_PENALTY,
            REPEAT_LAST_N,
            SEED,
            NUM_CTX,
            NUM_PREDICT,
        )
        observed = (
            self.temperature,
            self.top_p,
            self.top_k,
            self.min_p,
            self.repeat_penalty,
            self.repeat_last_n,
            self.seed,
            self.num_ctx,
            self.num_predict,
        )
        if tuple(type(value) for value in observed) != (
            float,
            float,
            int,
            float,
            float,
            int,
            int,
            int,
            int,
        ) or any(
            value.hex() != frozen.hex() if type(value) is float
            else value != frozen
            for value, frozen in zip(observed, expected, strict=True)
        ):
            raise ContractError("generation options differ from the frozen benchmark")

    def as_payload(self) -> dict[str, int | float]:
        """Return a fresh JSON-ready object in the frozen field set."""
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "repeat_penalty": self.repeat_penalty,
            "repeat_last_n": self.repeat_last_n,
            "seed": self.seed,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
        }


GENERATION_OPTIONS: Final = GenerationOptions()


@dataclass(frozen=True, slots=True)
class OllamaIdentity:
    """Expected local endpoint and immutable model identity."""

    endpoint: str = OLLAMA_ENDPOINT
    model_tag: str = MODEL_TAG
    model_digest: str = MODEL_DIGEST

    def __post_init__(self) -> None:
        if (
            type(self.endpoint) is not str
            or self.endpoint != OLLAMA_ENDPOINT
            or type(self.model_tag) is not str
            or self.model_tag != MODEL_TAG
            or type(self.model_digest) is not str
            or self.model_digest != MODEL_DIGEST
            or _SHA256.fullmatch(self.model_digest) is None
        ):
            raise ContractError("Ollama identity differs from the frozen benchmark")


EXPECTED_IDENTITY: Final = OllamaIdentity()


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Canonical request bytes and their independently checkable identity."""

    source_text: str = field(repr=False)
    nonce: str = field(repr=False)
    body: bytes = field(repr=False)
    request_sha256: str
    prompt_kind: PromptKind = PromptKind.PRIMARY
    model_tag: str = MODEL_TAG
    model_digest: str = MODEL_DIGEST
    endpoint: str = OLLAMA_ENDPOINT

    def __post_init__(self) -> None:
        validate_chat_request(self)

    def payload(self) -> dict[str, Any]:
        """Return a fresh decoded payload; callers cannot mutate request identity."""
        value = _load_json_object(self.body)
        return value


@dataclass(frozen=True, slots=True)
class ChatMetrics:
    """Bounded, content-free metadata from one completed chat stream."""

    done_reason: str
    prompt_eval_count: int
    eval_count: int
    total_duration_ns: int
    load_duration_ns: int
    prompt_eval_duration_ns: int
    eval_duration_ns: int
    raw_body_bytes: int
    content_bytes: int
    thinking_bytes: int

    def __post_init__(self) -> None:
        if type(self.done_reason) is not str or self.done_reason not in {"stop", "length"}:
            raise ContractError("done reason is invalid")
        counts = (
            self.prompt_eval_count,
            self.eval_count,
            self.total_duration_ns,
            self.load_duration_ns,
            self.prompt_eval_duration_ns,
            self.eval_duration_ns,
            self.raw_body_bytes,
            self.content_bytes,
            self.thinking_bytes,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise ContractError("chat metrics must be nonnegative integers")
        if self.raw_body_bytes > MAX_BODY_BYTES:
            raise ContractError("raw response body exceeds its bound")
        if self.content_bytes > MAX_CONTENT_BYTES:
            raise ContractError("content channel exceeds its bound")
        if self.content_bytes + self.thinking_bytes > MAX_COMBINED_CHANNEL_BYTES:
            raise ContractError("combined response channels exceed their bound")
        if self.raw_body_bytes < self.content_bytes + self.thinking_bytes:
            raise ContractError("raw body cannot be smaller than its text channels")


@dataclass(frozen=True, slots=True)
class ChatResult:
    """One closed client outcome; model text is never retained on failure."""

    status: OllamaStatus
    content: str | None = field(default=None, repr=False)
    metrics: ChatMetrics | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, OllamaStatus):
            raise ContractError("chat status is not a closed Ollama status")
        if self.status is OllamaStatus.SUCCESS:
            valid = (
                type(self.content) is str
                and isinstance(self.metrics, ChatMetrics)
                and self.metrics.done_reason == "stop"
                and _utf8_size(self.content, "chat content")
                == self.metrics.content_bytes
            )
        elif self.status is OllamaStatus.MODEL_INVALID:
            valid = (
                self.content is None
                and isinstance(self.metrics, ChatMetrics)
                and self.metrics.done_reason in {"stop", "length"}
            )
        else:
            valid = self.content is None and self.metrics is None
        if not valid:
            raise ContractError("chat result fields contradict its status")


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """Bounded identity evidence or one closed preflight failure."""

    status: OllamaStatus
    observed_version: str | None = None
    model_digest: str | None = None

    def __post_init__(self) -> None:
        allowed = {
            OllamaStatus.SUCCESS,
            OllamaStatus.CANCELLED_UNVERIFIED,
            OllamaStatus.REQUEST_TIMEOUT,
            OllamaStatus.RESOURCE_BUSY,
            OllamaStatus.TRANSPORT_UNAVAILABLE,
            OllamaStatus.PROTOCOL_VIOLATION,
            OllamaStatus.RESPONSE_LIMIT,
            OllamaStatus.IDENTITY_MISMATCH,
        }
        if self.status not in allowed:
            raise ContractError("status is not valid for preflight")
        if self.status is OllamaStatus.SUCCESS:
            valid = (
                type(self.observed_version) is str
                and _VERSION.fullmatch(self.observed_version) is not None
                and type(self.model_digest) is str
                and self.model_digest == MODEL_DIGEST
            )
        else:
            valid = self.observed_version is None and self.model_digest is None
        if not valid:
            raise ContractError("preflight fields contradict its status")


_CONTROL_STATUSES: Final = frozenset({
    OllamaStatus.SUCCESS,
    OllamaStatus.CANCELLED_UNVERIFIED,
    OllamaStatus.REQUEST_TIMEOUT,
    OllamaStatus.RESOURCE_BUSY,
    OllamaStatus.TRANSPORT_UNAVAILABLE,
    OllamaStatus.PROTOCOL_VIOLATION,
    OllamaStatus.RESPONSE_LIMIT,
    OllamaStatus.IDENTITY_MISMATCH,
})


@dataclass(frozen=True, slots=True)
class VersionCheckResult:
    """One separately chargeable ``/api/version`` outcome."""

    status: OllamaStatus
    observed_version: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _CONTROL_STATUSES:
            raise ContractError("status is not valid for a version contact")
        valid = (
            type(self.observed_version) is str
            and _VERSION.fullmatch(self.observed_version) is not None
            if self.status is OllamaStatus.SUCCESS
            else self.observed_version is None
        )
        if not valid:
            raise ContractError("version contact fields contradict its status")


@dataclass(frozen=True, slots=True)
class TagsCheckResult:
    """One separately chargeable ``/api/tags`` outcome."""

    status: OllamaStatus
    model_digest: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _CONTROL_STATUSES:
            raise ContractError("status is not valid for a tags contact")
        valid = (
            type(self.model_digest) is str
            and self.model_digest == MODEL_DIGEST
            if self.status is OllamaStatus.SUCCESS
            else self.model_digest is None
        )
        if not valid:
            raise ContractError("tags contact fields contradict its status")


def new_prompt_nonce(source_text: str) -> str:
    """Generate a cryptographic fence token guaranteed absent from this source."""
    if type(source_text) is not str:
        raise TypeError("source text must be a string")
    while True:
        nonce = f"FENCE_{secrets.token_hex(8).upper()}"
        if nonce not in source_text:
            return nonce


def build_chat_request(source_text: str, *, nonce: str) -> ChatRequest:
    """Build the only scored-chat request admitted by the V1 Analyst client."""
    return _build_chat_request(source_text, nonce, PromptKind.PRIMARY)


def build_repair_chat_request(source_text: str, *, nonce: str) -> ChatRequest:
    """Build the one error-specific C11 model-invalid repair request."""
    return _build_chat_request(
        source_text, nonce, PromptKind.MODEL_INVALID_REPAIR,
    )


def _build_chat_request(
    source_text: str, nonce: str, prompt_kind: PromptKind,
) -> ChatRequest:
    if type(source_text) is not str or type(nonce) is not str:
        raise TypeError("source text and nonce must be strings")
    if type(prompt_kind) is not PromptKind:
        raise TypeError("prompt kind must use the closed enum")
    if not 1 <= len(source_text) <= MAX_SOURCE_CHARS:
        raise ContractError("source text is outside the frozen chunk bound")
    if _NONCE.fullmatch(nonce) is None or nonce in source_text:
        raise ContractError("nonce must be a fresh FENCE token absent from source")

    from .worksheet import build_prompt, build_repair_prompt, worksheet_schema

    prompt_builder = (
        build_prompt
        if prompt_kind is PromptKind.PRIMARY
        else build_repair_prompt
    )
    prompt = prompt_builder(source_text, nonce=nonce)
    if _utf8_size(prompt, "prompt") > MAX_PROMPT_BYTES:
        raise ContractError("prompt exceeds the request bound")
    payload = {
        "model": MODEL_TAG,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "format": worksheet_schema(),
        "options": GENERATION_OPTIONS.as_payload(),
        "think": False,
        "keep_alive": KEEP_ALIVE,
    }
    body = canonical_json(payload)
    return ChatRequest(
        source_text=source_text,
        nonce=nonce,
        body=body,
        request_sha256=hashlib.sha256(body).hexdigest(),
        prompt_kind=prompt_kind,
    )


def validate_chat_request(request: ChatRequest) -> None:
    """Revalidate a request immediately before transport dispatch."""
    if not isinstance(request, ChatRequest):
        raise TypeError("request must be a ChatRequest")
    if (
        type(request.source_text) is not str
        or not 1 <= len(request.source_text) <= MAX_SOURCE_CHARS
        or type(request.nonce) is not str
        or _NONCE.fullmatch(request.nonce) is None
        or request.nonce in request.source_text
        or type(request.body) is not bytes
        or type(request.request_sha256) is not str
        or _SHA256.fullmatch(request.request_sha256) is None
        or type(getattr(request, "prompt_kind", None)) is not PromptKind
        or type(request.model_tag) is not str
        or request.model_tag != MODEL_TAG
        or type(request.model_digest) is not str
        or request.model_digest != MODEL_DIGEST
        or type(request.endpoint) is not str
        or request.endpoint != OLLAMA_ENDPOINT
    ):
        raise ContractError("chat request identity is invalid")
    if hashlib.sha256(request.body).hexdigest() != request.request_sha256:
        raise ContractError("chat request hash does not match its exact body")
    payload = _load_json_object(request.body)
    if canonical_json(payload) != request.body:
        raise ContractError("chat request body is not canonical JSON")
    _validate_payload(
        payload, request.source_text, request.nonce, request.prompt_kind,
    )


def canonical_json(value: Any) -> bytes:
    """Encode a JSON value with the frozen request-identity representation."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ContractError("value is not canonical JSON") from exc
    return encoded


def _validate_payload(
    payload: dict[str, Any],
    source_text: str,
    nonce: str,
    prompt_kind: PromptKind,
) -> None:
    from .worksheet import build_prompt, build_repair_prompt, worksheet_schema

    prompt_builder = (
        build_prompt
        if prompt_kind is PromptKind.PRIMARY
        else build_repair_prompt
    )

    if set(payload) != _REQUEST_KEYS or payload.get("model") != MODEL_TAG:
        raise ContractError("chat request field set is invalid")
    messages = payload.get("messages")
    if (
        type(messages) is not list
        or len(messages) != 1
        or type(messages[0]) is not dict
        or set(messages[0]) != {"role", "content"}
        or messages[0].get("role") != "user"
        or type(messages[0].get("content")) is not str
        or messages[0]["content"] != prompt_builder(source_text, nonce=nonce)
        or _utf8_size(messages[0]["content"], "prompt") > MAX_PROMPT_BYTES
    ):
        raise ContractError("chat message contract is invalid")
    if payload.get("stream") is not True:
        raise ContractError("streaming must remain enabled for cancellation")
    if payload.get("format") != worksheet_schema():
        raise ContractError("worksheet schema differs from the selected contract")
    if payload.get("think") is not False or payload.get("keep_alive") != KEEP_ALIVE:
        raise ContractError("chat runtime controls are invalid")
    options = payload.get("options")
    if type(options) is not dict or set(options) != _OPTION_KEYS:
        raise ContractError("generation option field set is invalid")
    expected_options = GENERATION_OPTIONS.as_payload()
    if canonical_json(options) != canonical_json(expected_options):
        raise ContractError("generation options differ from the frozen benchmark")
    if tuple(type(options[name]) for name in expected_options) != (
        float,
        float,
        int,
        float,
        float,
        int,
        int,
        int,
        int,
    ):
        raise ContractError("generation option JSON types are invalid")


def _utf8_size(value: str, label: str) -> int:
    try:
        return len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as exc:
        raise ContractError(f"{label} is not valid Unicode scalar text") from exc


def _load_json_object(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes:
        raise ContractError("chat request body must be bytes")

    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContractError("chat request contains a duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicate,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ContractError("chat request contains a non-finite number")
            ),
        )
    except ContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("chat request body is not strict JSON") from exc
    if type(value) is not dict:
        raise ContractError("chat request body must be a JSON object")
    return value


if (
    ANALYST_DEFAULTS.model_tag != MODEL_TAG
    or ANALYST_DEFAULTS.model_digest != MODEL_DIGEST
    or ANALYST_DEFAULTS.worksheet_version != WORKSHEET_VERSION
    or ANALYST_DEFAULTS.chunk_chars != MAX_SOURCE_CHARS
    or ANALYST_DEFAULTS.num_ctx != NUM_CTX
    or ANALYST_DEFAULTS.num_predict != NUM_PREDICT
):
    raise RuntimeError("C1 Analyst defaults drifted from the frozen C9 request contract")


__all__ = [
    "CANCEL_HEALTH_DELAY_SECONDS",
    "CONNECT_TIMEOUT_SECONDS",
    "ChatMetrics",
    "ChatRequest",
    "ChatResult",
    "ContractError",
    "EXPECTED_IDENTITY",
    "GENERATION_OPTIONS",
    "GenerationOptions",
    "IDLE_READ_TIMEOUT_SECONDS",
    "KEEP_ALIVE",
    "MAX_BODY_BYTES",
    "MAX_CANONICAL_JSON_BYTES",
    "MAX_COMBINED_CHANNEL_BYTES",
    "MAX_CONTENT_BYTES",
    "MAX_FRAME_BYTES",
    "MAX_JSON_DEPTH",
    "MAX_JSON_NODES",
    "MAX_PROMPT_BYTES",
    "MAX_SHOW_JSON_NODES",
    "MODEL_DIGEST",
    "MODEL_TAG",
    "NUM_CTX",
    "NUM_PREDICT",
    "OLLAMA_CHAT_URL",
    "OLLAMA_ENDPOINT",
    "OLLAMA_PS_URL",
    "OLLAMA_TAGS_URL",
    "OLLAMA_VERSION_URL",
    "OllamaIdentity",
    "OllamaStatus",
    "PreflightResult",
    "PromptKind",
    "QUALIFIED_OLLAMA_VERSION",
    "SEED",
    "TOTAL_REQUEST_SECONDS",
    "TagsCheckResult",
    "VersionCheckResult",
    "WORKSHEET_VERSION",
    "build_chat_request",
    "build_repair_chat_request",
    "canonical_json",
    "new_prompt_nonce",
    "validate_chat_request",
]
