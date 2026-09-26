"""Pure, bounded Ollama wire-protocol parsing for Analyst.

This module deliberately owns no sockets, persistence, or worksheet semantics.  The
HTTP client supplies bounded byte chunks; this layer rejects ambiguous JSON, validates
the exact response channels Analyst permits, and returns immutable sanitized results.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

from .ollama_contract import (
    MAX_BODY_BYTES,
    MAX_CANONICAL_JSON_BYTES,
    MAX_COMBINED_CHANNEL_BYTES,
    MAX_CONTENT_BYTES,
    MAX_FRAME_BYTES,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    ChatMetrics,
    ContractError,
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_RESOURCE_ERROR_RE = re.compile(
    r"(?:out of memory|insufficient memory|not enough memory|memory allocation|"
    r"cuda[^\n]{0,80}memory|resource exhausted)",
    re.IGNORECASE,
)
_TOP_LEVEL_FIELDS = frozenset(
    {
        "model",
        "created_at",
        "message",
        "done",
        "done_reason",
        "total_duration",
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
        "logprobs",
    }
)
_MESSAGE_FIELDS = frozenset(
    {"role", "content", "thinking", "tool_calls", "images"}
)
_METRIC_FIELDS = (
    "total_duration",
    "load_duration",
    "prompt_eval_count",
    "prompt_eval_duration",
    "eval_count",
    "eval_duration",
)


class SafetyCode(str, Enum):
    """Closed failures caused by an unsafe or unbounded response envelope."""

    BODY_LIMIT = "http_body_limit"
    FRAME_LIMIT = "ndjson_frame_limit"
    INVALID_WIRE_JSON = "invalid_wire_json"
    FRAME_NOT_OBJECT = "ndjson_frame_not_object"
    JSON_DEPTH_LIMIT = "json_depth_limit"
    JSON_NODE_LIMIT = "json_node_limit"
    CANONICAL_JSON_LIMIT = "canonical_json_limit"
    UNCANONICALIZABLE_JSON = "uncanonicalizable_json"
    INVALID_STREAM_FRAME = "invalid_stream_frame"
    INVALID_STREAM_TIMESTAMP = "invalid_stream_timestamp"
    UNKNOWN_TOP_LEVEL_CHANNEL = "unknown_top_level_channel"
    UNKNOWN_MESSAGE_CHANNEL = "unknown_message_channel"
    INVALID_MESSAGE_ROLE = "invalid_message_role"
    INVALID_TEXT_CHANNEL = "invalid_text_channel"
    FORBIDDEN_MESSAGE_CHANNEL = "forbidden_message_channel"
    FORBIDDEN_LOGPROBS_CHANNEL = "forbidden_logprobs_channel"
    INVALID_METRIC_TYPE = "invalid_metric_type"
    CONTENT_LIMIT = "answer_content_limit"
    COMBINED_CHANNEL_LIMIT = "combined_channel_limit"
    FRAME_AFTER_DONE = "frame_after_done"
    INVALID_DONE_REASON = "invalid_done_reason"


class ProvenanceCode(str, Enum):
    """Closed failures where server/model identity does not match the run."""

    INVALID_EXPECTATION = "invalid_expected_identity"
    VERSION_MISMATCH = "ollama_version_mismatch"
    TAGS_SHAPE = "tags_shape"
    TAGS_ROW_SHAPE = "tags_row_shape"
    TAGS_NAME_MISMATCH = "tags_name_mismatch"
    DUPLICATE_MODEL_TAG = "duplicate_model_tag"
    INVALID_MODEL_DIGEST = "invalid_model_digest"
    CLOUD_MODEL_REFUSED = "cloud_model_refused"
    MODEL_MISSING = "model_missing"
    MODEL_DIGEST_MISMATCH = "model_digest_mismatch"
    RESPONSE_MODEL_MISMATCH = "response_model_mismatch"


class StreamCode(str, Enum):
    """Closed, retry-classifiable stream failures."""

    RESOURCE_ERROR = "resource_error"
    STREAM_ERROR = "stream_error"
    ENDED_WITHOUT_DONE = "stream_ended_without_done"
    ALREADY_FINISHED = "stream_parser_already_finished"


class AnswerCode(str, Enum):
    """Closed failures attributable to the model's answer JSON."""

    INVALID_JSON = "invalid_answer_json"


class OllamaProtocolError(RuntimeError):
    """Base class for content-free protocol failures."""


class OllamaSafetyError(OllamaProtocolError):
    def __init__(self, code: SafetyCode) -> None:
        self.code = code
        super().__init__(code.value)


class OllamaProvenanceError(OllamaProtocolError):
    def __init__(self, code: ProvenanceCode) -> None:
        self.code = code
        super().__init__(code.value)


class OllamaStreamError(OllamaProtocolError):
    def __init__(self, code: StreamCode) -> None:
        self.code = code
        super().__init__(code.value)


class OllamaAnswerError(ValueError):
    def __init__(self, code: AnswerCode = AnswerCode.INVALID_JSON) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class VersionResult:
    version: str

    def __post_init__(self) -> None:
        if type(self.version) is not str or not 1 <= len(self.version) <= 64:
            raise ContractError("parsed Ollama version is invalid")


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    model: str
    digest: str

    def __post_init__(self) -> None:
        if type(self.model) is not str or not self.model or not valid_digest(self.digest):
            raise ContractError("parsed Ollama model identity is invalid")


@dataclass(frozen=True, slots=True)
class TagsResult:
    """Only approved identities are retained; unrelated installed tags are discarded."""

    models: tuple[ModelIdentity, ...]

    def __post_init__(self) -> None:
        if (
            type(self.models) is not tuple
            or not self.models
            or any(not isinstance(item, ModelIdentity) for item in self.models)
            or tuple(item.model for item in self.models)
            != tuple(sorted({item.model for item in self.models}))
        ):
            raise ContractError("sanitized Ollama tags result is invalid")


@dataclass(frozen=True, slots=True)
class ParsedChatResponse:
    """Validated in-memory answer plus the frozen content-free client metrics."""

    model: str
    content: str = field(repr=False)
    content_sha256: str
    metrics: ChatMetrics

    def __post_init__(self) -> None:
        try:
            content_bytes = self.content.encode("utf-8")
        except (AttributeError, UnicodeEncodeError):
            raise ContractError("parsed Ollama chat response is invalid") from None
        if (
            type(self.model) is not str
            or not self.model
            or type(self.content) is not str
            or type(self.content_sha256) is not str
            or not digest_equal(
                self.content_sha256,
                hashlib.sha256(content_bytes).hexdigest(),
            )
            or not isinstance(self.metrics, ChatMetrics)
            or len(content_bytes) != self.metrics.content_bytes
        ):
            raise ContractError("parsed Ollama chat response is invalid")


def parse_version_response(raw: bytes, expected_version: str) -> VersionResult:
    """Parse and verify the exact bounded ``/api/version`` response."""
    if type(expected_version) is not str or not expected_version:
        raise OllamaProvenanceError(ProvenanceCode.INVALID_EXPECTATION)
    value = parse_wire_json(raw)
    if (
        type(value) is not dict
        or set(value) != {"version"}
        or type(value.get("version")) is not str
        or value["version"] != expected_version
    ):
        raise OllamaProvenanceError(ProvenanceCode.VERSION_MISMATCH)
    return VersionResult(expected_version)


def parse_tags_response(
    raw: bytes, expected_models: Mapping[str, str]
) -> TagsResult:
    """Verify approved local tag/digest pairs and discard unrelated tag metadata."""
    expected = _copy_expected_models(expected_models)
    value = parse_wire_json(raw)
    if (
        type(value) is not dict
        or set(value) != {"models"}
        or type(value["models"]) is not list
    ):
        raise OllamaProvenanceError(ProvenanceCode.TAGS_SHAPE)

    found: dict[str, str] = {}
    for row in value["models"]:
        if type(row) is not dict:
            raise OllamaProvenanceError(ProvenanceCode.TAGS_ROW_SHAPE)
        name, alias, digest = row.get("name"), row.get("model"), row.get("digest")
        if type(name) is not str or type(alias) is not str or name != alias:
            raise OllamaProvenanceError(ProvenanceCode.TAGS_NAME_MISMATCH)
        if name in found:
            raise OllamaProvenanceError(ProvenanceCode.DUPLICATE_MODEL_TAG)
        if not valid_digest(digest):
            raise OllamaProvenanceError(ProvenanceCode.INVALID_MODEL_DIGEST)
        found[name] = digest

    approved: list[ModelIdentity] = []
    for model in sorted(expected):
        digest = expected[model]
        if _is_cloud_model(model):
            raise OllamaProvenanceError(ProvenanceCode.CLOUD_MODEL_REFUSED)
        observed = found.get(model)
        if observed is None:
            raise OllamaProvenanceError(ProvenanceCode.MODEL_MISSING)
        if not digest_equal(observed, digest):
            raise OllamaProvenanceError(ProvenanceCode.MODEL_DIGEST_MISMATCH)
        approved.append(ModelIdentity(model, digest))
    return TagsResult(tuple(approved))


def parse_wire_json(raw: bytes, *, max_nodes: int = MAX_JSON_NODES) -> Any:
    """Decode one bounded, strict-UTF-8, unique-key JSON control body."""
    if type(raw) is not bytes or len(raw) > MAX_BODY_BYTES:
        raise OllamaSafetyError(SafetyCode.BODY_LIMIT)
    value = _decode_json(raw)
    bound_json(value, max_nodes=max_nodes)
    return value


def parse_answer_json(content: str) -> Any:
    """Decode model answer JSON, distinguishing malformed output from unsafe shape."""
    if type(content) is not str:
        raise OllamaAnswerError()
    try:
        value = json.loads(
            content,
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except RecursionError:
        raise OllamaSafetyError(SafetyCode.JSON_DEPTH_LIMIT) from None
    except (json.JSONDecodeError, _DuplicateKey, ValueError):
        raise OllamaAnswerError() from None
    bound_json(value)
    return value


def bound_json(value: Any, *, max_nodes: int = MAX_JSON_NODES) -> None:
    """Enforce decoded-node, depth, and canonical-byte limits iteratively."""
    if type(max_nodes) is not int or max_nodes <= 0:
        raise ValueError("max_nodes must be a positive integer")
    nodes = 0
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise OllamaSafetyError(SafetyCode.JSON_DEPTH_LIMIT)
        nodes += 1
        if nodes > max_nodes:
            raise OllamaSafetyError(SafetyCode.JSON_NODE_LIMIT)
        if isinstance(item, dict):
            nodes += len(item)  # Keys count as decoded nodes too.
            if nodes > max_nodes:
                raise OllamaSafetyError(SafetyCode.JSON_NODE_LIMIT)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    if len(canonical_json(value)) > MAX_CANONICAL_JSON_BYTES:
        raise OllamaSafetyError(SafetyCode.CANONICAL_JSON_LIMIT)


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeEncodeError):
        raise OllamaSafetyError(SafetyCode.UNCANONICALIZABLE_JSON) from None


def valid_digest(value: Any) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def digest_equal(left: Any, right: Any) -> bool:
    return valid_digest(left) and valid_digest(right) and hmac.compare_digest(left, right)


class ChatStreamParser:
    """Incrementally validate one exact Ollama ``/api/chat`` NDJSON stream."""

    def __init__(self, expected_model: str) -> None:
        if type(expected_model) is not str or not expected_model:
            raise OllamaProvenanceError(ProvenanceCode.INVALID_EXPECTATION)
        self._model = expected_model
        self._pending = bytearray()
        self._parts: list[str] = []
        self._body_bytes = 0
        self._content_bytes = 0
        self._thinking_bytes = 0
        self._done = False
        self._finished = False
        self._done_reason: str | None = None
        self._metrics: dict[str, int] = {}

    @property
    def content_started(self) -> bool:
        return self._content_bytes > 0

    @property
    def body_bytes(self) -> int:
        return self._body_bytes

    def feed(
        self,
        chunk: bytes,
        *,
        before_frame: Callable[[], None] | None = None,
    ) -> None:
        """Consume one raw, content-decoding-disabled HTTP body chunk."""
        self._require_open()
        if type(chunk) is not bytes:
            raise OllamaSafetyError(SafetyCode.INVALID_WIRE_JSON)
        if before_frame is not None and not callable(before_frame):
            raise TypeError("before_frame must be callable")
        self._body_bytes += len(chunk)
        if self._body_bytes > MAX_BODY_BYTES:
            raise OllamaSafetyError(SafetyCode.BODY_LIMIT)
        cursor = 0
        while True:
            newline = chunk.find(b"\n", cursor)
            if newline < 0:
                self._append_pending(chunk[cursor:])
                return
            self._append_pending(chunk[cursor:newline])
            self._consume_pending(before_frame=before_frame)
            cursor = newline + 1

    def finish(self) -> ParsedChatResponse:
        """Consume a final unterminated frame and require one terminal frame."""
        self._require_open()
        self._consume_pending()
        self._finished = True
        if not self._done or self._done_reason is None:
            raise OllamaStreamError(StreamCode.ENDED_WITHOUT_DONE)
        content = "".join(self._parts)
        metrics = ChatMetrics(
            done_reason=self._done_reason,
            prompt_eval_count=self._metrics["prompt_eval_count"],
            eval_count=self._metrics["eval_count"],
            total_duration_ns=self._metrics["total_duration"],
            load_duration_ns=self._metrics["load_duration"],
            prompt_eval_duration_ns=self._metrics["prompt_eval_duration"],
            eval_duration_ns=self._metrics["eval_duration"],
            raw_body_bytes=self._body_bytes,
            content_bytes=self._content_bytes,
            thinking_bytes=self._thinking_bytes,
        )
        return ParsedChatResponse(
            model=self._model,
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            metrics=metrics,
        )

    def _require_open(self) -> None:
        if self._finished:
            raise OllamaStreamError(StreamCode.ALREADY_FINISHED)

    def _append_pending(self, piece: bytes) -> None:
        if len(self._pending) + len(piece) > MAX_FRAME_BYTES:
            raise OllamaSafetyError(SafetyCode.FRAME_LIMIT)
        self._pending.extend(piece)

    def _consume_pending(
        self, *, before_frame: Callable[[], None] | None = None
    ) -> None:
        if not self._pending.strip():
            self._pending.clear()
            return
        if before_frame is not None:
            before_frame()
        frame = _decode_json(bytes(self._pending))
        self._pending.clear()
        _bound_json_shape(frame)
        if type(frame) is not dict:
            raise OllamaSafetyError(SafetyCode.FRAME_NOT_OBJECT)
        self._consume_frame(frame)

    def _consume_frame(self, frame: dict[str, Any]) -> None:
        if self._done:
            raise OllamaSafetyError(SafetyCode.FRAME_AFTER_DONE)
        if "error" in frame:
            message = frame.get("error")
            code = (
                StreamCode.RESOURCE_ERROR
                if type(message) is str and _RESOURCE_ERROR_RE.search(message)
                else StreamCode.STREAM_ERROR
            )
            raise OllamaStreamError(code)
        _validate_chat_frame(frame, self._model)

        message = frame["message"]
        content = message.get("content", "")
        thinking = message.get("thinking", "")
        try:
            content_size = len(content.encode("utf-8"))
            thinking_size = len(thinking.encode("utf-8"))
        except UnicodeEncodeError:
            raise OllamaSafetyError(SafetyCode.INVALID_TEXT_CHANNEL) from None
        self._content_bytes += content_size
        self._thinking_bytes += thinking_size
        if self._content_bytes > MAX_CONTENT_BYTES:
            raise OllamaSafetyError(SafetyCode.CONTENT_LIMIT)
        if self._content_bytes + self._thinking_bytes > MAX_COMBINED_CHANNEL_BYTES:
            raise OllamaSafetyError(SafetyCode.COMBINED_CHANNEL_LIMIT)
        if content:
            self._parts.append(content)

        if frame["done"]:
            done_reason = frame.get("done_reason")
            if done_reason not in {"stop", "length"}:
                raise OllamaSafetyError(SafetyCode.INVALID_DONE_REASON)
            if any(field not in frame for field in _METRIC_FIELDS):
                raise OllamaSafetyError(SafetyCode.INVALID_METRIC_TYPE)
            self._done = True
            self._done_reason = done_reason
            for field in _METRIC_FIELDS:
                self._metrics[field] = frame[field]


def _validate_chat_frame(frame: dict[str, Any], expected_model: str) -> None:
    if type(frame.get("model")) is not str or frame["model"] != expected_model:
        raise OllamaProvenanceError(ProvenanceCode.RESPONSE_MODEL_MISMATCH)
    if type(frame.get("done")) is not bool or type(frame.get("message")) is not dict:
        raise OllamaSafetyError(SafetyCode.INVALID_STREAM_FRAME)
    if "created_at" in frame and type(frame["created_at"]) is not str:
        raise OllamaSafetyError(SafetyCode.INVALID_STREAM_TIMESTAMP)

    unknown_top = set(frame) - _TOP_LEVEL_FIELDS
    if any(not _empty_channel(frame[field]) for field in unknown_top):
        raise OllamaSafetyError(SafetyCode.UNKNOWN_TOP_LEVEL_CHANNEL)
    message = frame["message"]
    unknown_message = set(message) - _MESSAGE_FIELDS
    if any(not _empty_channel(message[field]) for field in unknown_message):
        raise OllamaSafetyError(SafetyCode.UNKNOWN_MESSAGE_CHANNEL)
    if message.get("role", "assistant") != "assistant":
        raise OllamaSafetyError(SafetyCode.INVALID_MESSAGE_ROLE)
    for field in ("content", "thinking"):
        if type(message.get(field, "")) is not str:
            raise OllamaSafetyError(SafetyCode.INVALID_TEXT_CHANNEL)
    for field in ("tool_calls", "images"):
        if field in message and (type(message[field]) is not list or message[field]):
            raise OllamaSafetyError(SafetyCode.FORBIDDEN_MESSAGE_CHANNEL)
    if "logprobs" in frame and (
        type(frame["logprobs"]) is not list or frame["logprobs"]
    ):
        raise OllamaSafetyError(SafetyCode.FORBIDDEN_LOGPROBS_CHANNEL)
    for field in _METRIC_FIELDS:
        if field in frame and (type(frame[field]) is not int or frame[field] < 0):
            raise OllamaSafetyError(SafetyCode.INVALID_METRIC_TYPE)


class _DuplicateKey(ValueError):
    pass


def _decode_json(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKey, ValueError):
        raise OllamaSafetyError(SafetyCode.INVALID_WIRE_JSON) from None
    except RecursionError:
        raise OllamaSafetyError(SafetyCode.JSON_DEPTH_LIMIT) from None


def _bound_json_shape(value: Any, *, max_nodes: int = MAX_JSON_NODES) -> None:
    nodes = 0
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise OllamaSafetyError(SafetyCode.JSON_DEPTH_LIMIT)
        nodes += 1
        if nodes > max_nodes:
            raise OllamaSafetyError(SafetyCode.JSON_NODE_LIMIT)
        if isinstance(item, dict):
            nodes += len(item)
            if nodes > max_nodes:
                raise OllamaSafetyError(SafetyCode.JSON_NODE_LIMIT)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _copy_expected_models(expected_models: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(expected_models, Mapping) or not expected_models:
        raise OllamaProvenanceError(ProvenanceCode.INVALID_EXPECTATION)
    expected: dict[str, str] = {}
    for model, digest in expected_models.items():
        if type(model) is not str or not model or not valid_digest(digest):
            raise OllamaProvenanceError(ProvenanceCode.INVALID_EXPECTATION)
        if model in expected:
            raise OllamaProvenanceError(ProvenanceCode.INVALID_EXPECTATION)
        expected[model] = digest
    return expected


def _is_cloud_model(model: str) -> bool:
    lowered = model.lower()
    return lowered.endswith(":cloud") or "-cloud" in lowered


def _empty_channel(value: Any) -> bool:
    return value is None or value is False or value == "" or value == [] or value == {}


__all__ = [
    "AnswerCode",
    "ChatStreamParser",
    "ModelIdentity",
    "OllamaAnswerError",
    "OllamaProtocolError",
    "OllamaProvenanceError",
    "OllamaSafetyError",
    "OllamaStreamError",
    "ParsedChatResponse",
    "ProvenanceCode",
    "SafetyCode",
    "StreamCode",
    "TagsResult",
    "VersionResult",
    "bound_json",
    "canonical_json",
    "digest_equal",
    "parse_answer_json",
    "parse_tags_response",
    "parse_version_response",
    "parse_wire_json",
    "valid_digest",
]
