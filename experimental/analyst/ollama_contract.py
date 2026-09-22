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
from typing import Any, Final, NamedTuple

from .endpoint import (
    DEFAULT_ENDPOINT,
    EndpointError,
    normalize_endpoint,
    ollama_urls,
)
from .models import ANALYST_DEFAULTS


# N1: the endpoint is a supplied value.  These module-level constants remain the
# loopback default every shipped call site still resolves to, so a default run is
# byte-identical to the pre-N1 build.
OLLAMA_ENDPOINT: Final = DEFAULT_ENDPOINT
_LOOPBACK_URLS: Final = ollama_urls(OLLAMA_ENDPOINT)
OLLAMA_VERSION_URL: Final = _LOOPBACK_URLS.version
OLLAMA_TAGS_URL: Final = _LOOPBACK_URLS.tags
OLLAMA_PS_URL: Final = _LOOPBACK_URLS.ps
OLLAMA_CHAT_URL: Final = _LOOPBACK_URLS.chat

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
# Host READ is a separate reduce workload: it needs the full bounded inventory and
# more output room.  These are intentionally tunable without changing the frozen
# worksheet-v2 per-chunk generation profile above.
READ_NUM_CTX: Final = 16384
READ_NUM_PREDICT: Final = 2048
KEEP_ALIVE: Final = "15m"

MAX_SOURCE_CHARS: Final = 8000
READ_MAX_SOURCE_CHARS: Final = 24000
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


def is_cloud_model_tag(value: object) -> bool:
    """Return whether an exact string names a known Ollama cloud tag."""
    if type(value) is not str:
        return False
    lowered = value.lower()
    return lowered.endswith(":cloud") or "-cloud" in lowered


def valid_model_tag(value: object) -> bool:
    """Accept one nonempty local model tag and reject known cloud forms."""
    return type(value) is str and bool(value) and not is_cloud_model_tag(value)


def valid_model_digest(value: object) -> bool:
    """Accept one canonical lowercase SHA-256 model digest."""
    return type(value) is str and _SHA256.fullmatch(value) is not None


def valid_endpoint(value: object) -> bool:
    """Accept one canonical ``scheme://host:port`` endpoint string."""
    if type(value) is not str:
        return False
    try:
        return normalize_endpoint(value) == value
    except EndpointError:
        return False


def _discovery_identity_bytes(tags_url: str) -> bytes:
    return json.dumps(
        {
            "accept": "application/json",
            "accept_encoding": "identity",
            "allow_redirects": False,
            "kind": "model_discovery",
            "method": "GET",
            "proxies_ignored": True,
            "trust_env": False,
            "url": tags_url,
            "version": 1,
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def discovery_request_sha256(endpoint: str = OLLAMA_ENDPOINT) -> str:
    """Return the discovery request identity hash for one endpoint.

    N1: the endpoint is part of the request shape, so this seal is a function of
    it rather than a module-level constant.  Two endpoints hash differently.
    """
    return hashlib.sha256(
        _discovery_identity_bytes(ollama_urls(endpoint).tags)
    ).hexdigest()


#: The loopback discovery seal, unchanged from the pre-N1 build.
DISCOVERY_REQUEST_SHA256: Final = discovery_request_sha256(OLLAMA_ENDPOINT)


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
    #: The server refused the prompt as larger than its context (contract 5.4).
    CONTEXT_EXCEEDED = "context_exceeded"
    #: Analyst asked for something the server could not usefully answer
    #: (contract 7.4): never recorded as model_invalid, which would blame the
    #: model for an Analyst misconfiguration.
    CONFIGURATION_FAILURE = "configuration_failure"


#: Outcomes only a chat contact can produce. A control contact (version, tags,
#: ps) sends no prompt, so it can neither exceed a context nor return an
#: unusable answer shape. D20.
CHAT_ONLY_STATUSES: Final = frozenset({
    OllamaStatus.CONTEXT_EXCEEDED,
    OllamaStatus.CONFIGURATION_FAILURE,
})


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
class ReadGenerationOptions:
    """Generation controls for the host-level READ reduce workload."""

    temperature: float = TEMPERATURE
    top_p: float = TOP_P
    top_k: int = TOP_K
    min_p: float = MIN_P
    repeat_penalty: float = REPEAT_PENALTY
    repeat_last_n: int = REPEAT_LAST_N
    seed: int = SEED
    num_ctx: int = READ_NUM_CTX
    num_predict: int = READ_NUM_PREDICT

    def __post_init__(self) -> None:
        expected = (
            TEMPERATURE, TOP_P, TOP_K, MIN_P, REPEAT_PENALTY, REPEAT_LAST_N,
            SEED, READ_NUM_CTX, READ_NUM_PREDICT,
        )
        observed = (
            self.temperature, self.top_p, self.top_k, self.min_p,
            self.repeat_penalty, self.repeat_last_n, self.seed, self.num_ctx,
            self.num_predict,
        )
        if tuple(type(value) for value in observed) != (
            float, float, int, float, float, int, int, int, int,
        ) or any(
            value.hex() != frozen.hex() if type(value) is float
            else value != frozen
            for value, frozen in zip(observed, expected, strict=True)
        ):
            raise ContractError("READ generation options differ from their profile")

    def as_payload(self) -> dict[str, int | float]:
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


READ_GENERATION_OPTIONS: Final = ReadGenerationOptions()


@dataclass(frozen=True, slots=True)
class OllamaIdentity:
    """Expected local endpoint and one immutable per-run model identity."""

    endpoint: str = OLLAMA_ENDPOINT
    model_tag: str = ANALYST_DEFAULTS.model_tag
    model_digest: str = ANALYST_DEFAULTS.model_digest

    def __post_init__(self) -> None:
        if (
            not valid_endpoint(self.endpoint)
            or not valid_model_tag(self.model_tag)
            or not valid_model_digest(self.model_digest)
        ):
            raise ContractError("Ollama identity is invalid")


EXPECTED_IDENTITY: Final = OllamaIdentity()


@dataclass(frozen=True, slots=True)
class DiscoveryRequest:
    """Content-free identity for one explicit local model-list request."""

    endpoint: str = OLLAMA_ENDPOINT
    url: str = OLLAMA_TAGS_URL
    method: str = "GET"
    body: None = None
    accept: str = "application/json"
    accept_encoding: str = "identity"
    allow_redirects: bool = False
    trust_env: bool = False
    request_sha256: str = DISCOVERY_REQUEST_SHA256

    def __post_init__(self) -> None:
        if not valid_endpoint(self.endpoint):
            raise ContractError("model discovery request identity is invalid")
        expected_url = ollama_urls(self.endpoint).tags
        expected_sha = discovery_request_sha256(self.endpoint)
        if (
            type(self.url) is not str
            or self.url != expected_url
            or type(self.method) is not str
            or self.method != "GET"
            or self.body is not None
            or type(self.accept) is not str
            or self.accept != "application/json"
            or type(self.accept_encoding) is not str
            or self.accept_encoding != "identity"
            or self.allow_redirects is not False
            or self.trust_env is not False
            or type(self.request_sha256) is not str
            or self.request_sha256 != expected_sha
        ):
            raise ContractError("model discovery request identity is invalid")


class DiscoveredModel(NamedTuple):
    """One bounded local model identity, safe to persist or display."""

    model_tag: str
    model_digest: str


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Canonical request bytes and their independently checkable identity."""

    source_text: str = field(repr=False)
    nonce: str = field(repr=False)
    body: bytes = field(repr=False)
    request_sha256: str
    prompt_kind: PromptKind = PromptKind.PRIMARY
    model_tag: str = ANALYST_DEFAULTS.model_tag
    model_digest: str = ANALYST_DEFAULTS.model_digest
    endpoint: str = OLLAMA_ENDPOINT

    def __post_init__(self) -> None:
        validate_chat_request(self)

    def payload(self) -> dict[str, Any]:
        """Return a fresh decoded payload; callers cannot mutate request identity."""
        value = _load_json_object(self.body)
        return value


class ReadChatRequest(ChatRequest):
    """Fieldless discriminator for the READ prompt/schema request identity."""

    __slots__ = ()


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
                and valid_model_digest(self.model_digest)
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
            valid_model_digest(self.model_digest)
            if self.status is OllamaStatus.SUCCESS
            else self.model_digest is None
        )
        if not valid:
            raise ContractError("tags contact fields contradict its status")


def build_discovery_request(
    endpoint: str = OLLAMA_ENDPOINT,
) -> DiscoveryRequest:
    """Build the content-free ``GET /api/tags`` discovery intent for one endpoint."""
    resolved = normalize_endpoint(endpoint)
    return DiscoveryRequest(
        endpoint=resolved,
        url=ollama_urls(resolved).tags,
        request_sha256=discovery_request_sha256(resolved),
    )


def list_local_models(body: bytes) -> tuple[DiscoveredModel, ...]:
    """Return every valid non-cloud identity from one bounded tags response."""
    # Imported lazily because the wire parser imports this contract module.
    from . import ollama_protocol

    value = ollama_protocol.parse_wire_json(body)
    if (
        type(value) is not dict
        or set(value) != {"models"}
        or type(value["models"]) is not list
    ):
        raise ollama_protocol.OllamaProvenanceError(
            ollama_protocol.ProvenanceCode.TAGS_SHAPE
        )

    found: dict[str, str] = {}
    for row in value["models"]:
        if type(row) is not dict:
            raise ollama_protocol.OllamaProvenanceError(
                ollama_protocol.ProvenanceCode.TAGS_ROW_SHAPE
            )
        name, alias, digest = row.get("name"), row.get("model"), row.get("digest")
        if type(name) is not str or type(alias) is not str or name != alias:
            raise ollama_protocol.OllamaProvenanceError(
                ollama_protocol.ProvenanceCode.TAGS_NAME_MISMATCH
            )
        if name in found:
            raise ollama_protocol.OllamaProvenanceError(
                ollama_protocol.ProvenanceCode.DUPLICATE_MODEL_TAG
            )
        if not ollama_protocol.valid_digest(digest):
            raise ollama_protocol.OllamaProvenanceError(
                ollama_protocol.ProvenanceCode.INVALID_MODEL_DIGEST
            )
        found[name] = digest

    return tuple(
        DiscoveredModel(model, found[model])
        for model in sorted(found)
        if not ollama_protocol._is_cloud_model(model)
    )


def new_prompt_nonce(source_text: str) -> str:
    """Generate a cryptographic fence token guaranteed absent from this source."""
    if type(source_text) is not str:
        raise TypeError("source text must be a string")
    while True:
        nonce = f"FENCE_{secrets.token_hex(8).upper()}"
        if nonce not in source_text:
            return nonce


def build_chat_request(
    source_text: str,
    *,
    nonce: str,
    model_tag: str = ANALYST_DEFAULTS.model_tag,
    model_digest: str = ANALYST_DEFAULTS.model_digest,
    endpoint: str = OLLAMA_ENDPOINT,
) -> ChatRequest:
    """Build the only scored-chat request admitted by the V1 Analyst client."""
    return _build_chat_request(
        source_text, nonce, PromptKind.PRIMARY, model_tag, model_digest, endpoint,
    )


def build_repair_chat_request(
    source_text: str,
    *,
    nonce: str,
    model_tag: str = ANALYST_DEFAULTS.model_tag,
    model_digest: str = ANALYST_DEFAULTS.model_digest,
    endpoint: str = OLLAMA_ENDPOINT,
) -> ChatRequest:
    """Build the one error-specific C11 model-invalid repair request."""
    return _build_chat_request(
        source_text, nonce, PromptKind.MODEL_INVALID_REPAIR,
        model_tag, model_digest, endpoint,
    )


def build_read_chat_request(
    source_text: str,
    *,
    nonce: str,
    model_tag: str = ANALYST_DEFAULTS.model_tag,
    model_digest: str = ANALYST_DEFAULTS.model_digest,
    endpoint: str = OLLAMA_ENDPOINT,
) -> ChatRequest:
    """Build the pinned host READ reduce request."""
    return _build_read_chat_request(
        source_text, nonce, PromptKind.PRIMARY, model_tag, model_digest, endpoint,
    )


def build_read_repair_chat_request(
    source_text: str,
    *,
    nonce: str,
    model_tag: str = ANALYST_DEFAULTS.model_tag,
    model_digest: str = ANALYST_DEFAULTS.model_digest,
    endpoint: str = OLLAMA_ENDPOINT,
) -> ChatRequest:
    """Build the error-specific host READ repair request."""
    return _build_read_chat_request(
        source_text, nonce, PromptKind.MODEL_INVALID_REPAIR,
        model_tag, model_digest, endpoint,
    )


def _build_read_chat_request(
    source_text: str,
    nonce: str,
    prompt_kind: PromptKind,
    model_tag: str,
    model_digest: str,
    endpoint: str = OLLAMA_ENDPOINT,
) -> ChatRequest:
    if type(source_text) is not str or type(nonce) is not str:
        raise TypeError("source text and nonce must be strings")
    if type(prompt_kind) is not PromptKind:
        raise TypeError("prompt kind must use the closed enum")
    if not 1 <= len(source_text) <= READ_MAX_SOURCE_CHARS:
        raise ContractError("source text is outside the READ source bound")
    if _NONCE.fullmatch(nonce) is None or nonce in source_text:
        raise ContractError("nonce must be a fresh FENCE token absent from source")
    _require_model_identity(model_tag, model_digest)
    resolved_endpoint = normalize_endpoint(endpoint)

    from .read_worksheet import (
        build_read_prompt,
        build_read_repair_prompt,
        read_schema,
    )

    prompt_builder = (
        build_read_prompt
        if prompt_kind is PromptKind.PRIMARY
        else build_read_repair_prompt
    )
    prompt = prompt_builder(source_text, nonce=nonce)
    if _utf8_size(prompt, "prompt") > MAX_PROMPT_BYTES:
        raise ContractError("prompt exceeds the request bound")
    payload = {
        "model": model_tag,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "format": read_schema(),
        "options": READ_GENERATION_OPTIONS.as_payload(),
        "think": False,
        "keep_alive": KEEP_ALIVE,
    }
    body = canonical_json(payload)
    return ReadChatRequest(
        source_text=source_text,
        nonce=nonce,
        body=body,
        request_sha256=hashlib.sha256(body).hexdigest(),
        prompt_kind=prompt_kind,
        model_tag=model_tag,
        model_digest=model_digest,
        endpoint=resolved_endpoint,
    )


def _build_chat_request(
    source_text: str,
    nonce: str,
    prompt_kind: PromptKind,
    model_tag: str,
    model_digest: str,
    endpoint: str = OLLAMA_ENDPOINT,
) -> ChatRequest:
    if type(source_text) is not str or type(nonce) is not str:
        raise TypeError("source text and nonce must be strings")
    if type(prompt_kind) is not PromptKind:
        raise TypeError("prompt kind must use the closed enum")
    if not 1 <= len(source_text) <= MAX_SOURCE_CHARS:
        raise ContractError("source text is outside the frozen chunk bound")
    if _NONCE.fullmatch(nonce) is None or nonce in source_text:
        raise ContractError("nonce must be a fresh FENCE token absent from source")
    _require_model_identity(model_tag, model_digest)
    resolved_endpoint = normalize_endpoint(endpoint)

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
        "model": model_tag,
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
        model_tag=model_tag,
        model_digest=model_digest,
        endpoint=resolved_endpoint,
    )


def validate_chat_request(request: ChatRequest) -> None:
    """Revalidate a request immediately before transport dispatch."""
    if not isinstance(request, ChatRequest):
        raise TypeError("request must be a ChatRequest")
    read_request = type(request) is ReadChatRequest
    source_limit = READ_MAX_SOURCE_CHARS if read_request else MAX_SOURCE_CHARS
    if (
        type(request.source_text) is not str
        or not 1 <= len(request.source_text) <= source_limit
        or type(request.nonce) is not str
        or _NONCE.fullmatch(request.nonce) is None
        or request.nonce in request.source_text
        or type(request.body) is not bytes
        or type(request.request_sha256) is not str
        or _SHA256.fullmatch(request.request_sha256) is None
        or type(getattr(request, "prompt_kind", None)) is not PromptKind
        or read_request
        and request.prompt_kind not in {
            PromptKind.PRIMARY, PromptKind.MODEL_INVALID_REPAIR,
        }
        or not valid_model_tag(request.model_tag)
        or not valid_model_digest(request.model_digest)
        or not valid_endpoint(request.endpoint)
    ):
        raise ContractError("chat request identity is invalid")
    if hashlib.sha256(request.body).hexdigest() != request.request_sha256:
        raise ContractError("chat request hash does not match its exact body")
    payload = _load_json_object(request.body)
    if canonical_json(payload) != request.body:
        raise ContractError("chat request body is not canonical JSON")
    _validate_payload(
        payload, request.source_text, request.nonce, request.prompt_kind,
        read_request, request.model_tag,
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
    read_request: bool,
    model_tag: str,
) -> None:
    if read_request:
        from .read_worksheet import (
            build_read_prompt,
            build_read_repair_prompt,
            read_schema,
        )

        prompt_builder = (
            build_read_prompt
            if prompt_kind is PromptKind.PRIMARY
            else build_read_repair_prompt
        )
        response_schema = read_schema()
    else:
        from .worksheet import build_prompt, build_repair_prompt, worksheet_schema

        prompt_builder = (
            build_prompt
            if prompt_kind is PromptKind.PRIMARY
            else build_repair_prompt
        )
        response_schema = worksheet_schema()

    if set(payload) != _REQUEST_KEYS or payload.get("model") != model_tag:
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
    if payload.get("format") != response_schema:
        raise ContractError("worksheet schema differs from the selected contract")
    if payload.get("think") is not False or payload.get("keep_alive") != KEEP_ALIVE:
        raise ContractError("chat runtime controls are invalid")
    options = payload.get("options")
    if type(options) is not dict or set(options) != _OPTION_KEYS:
        raise ContractError("generation option field set is invalid")
    expected_options = (
        READ_GENERATION_OPTIONS.as_payload()
        if read_request
        else GENERATION_OPTIONS.as_payload()
    )
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


def _require_model_identity(model_tag: object, model_digest: object) -> None:
    if not valid_model_tag(model_tag) or not valid_model_digest(model_digest):
        raise ContractError("model identity is invalid")


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
    "CHAT_ONLY_STATUSES",
    "CONNECT_TIMEOUT_SECONDS",
    "ChatMetrics",
    "ChatRequest",
    "ChatResult",
    "ContractError",
    "DISCOVERY_REQUEST_SHA256",
    "DiscoveredModel",
    "DiscoveryRequest",
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
    "READ_GENERATION_OPTIONS",
    "READ_MAX_SOURCE_CHARS",
    "READ_NUM_CTX",
    "READ_NUM_PREDICT",
    "ReadGenerationOptions",
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
    "ReadChatRequest",
    "SEED",
    "TOTAL_REQUEST_SECONDS",
    "TagsCheckResult",
    "VersionCheckResult",
    "WORKSHEET_VERSION",
    "build_chat_request",
    "build_discovery_request",
    "build_read_chat_request",
    "build_read_repair_chat_request",
    "build_repair_chat_request",
    "canonical_json",
    "discovery_request_sha256",
    "is_cloud_model_tag",
    "list_local_models",
    "new_prompt_nonce",
    "valid_model_digest",
    "valid_endpoint",
    "valid_model_tag",
    "validate_chat_request",
]
