"""Bounded local Ollama transport for the C0B-2 benchmark.

The transport is deliberately small and dependency-injected: a trusted runtime
resolves each durable executor request to an immutable :class:`RequestSpec`, while
this module owns the HTTP and response-safety boundary.  It never discovers work,
changes a checkpoint, or performs an uncharged request.

DISPOSITION: benchmark-only diagnostic; remove after the C0B artifacts are accepted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, TypeAlias, Union

import requests
import urllib3
from pydantic import ValidationError

from . import c0b2_schema
from .c0b2_executor import (SERVER_CONTROL_MODEL, ControlRequest, FakeResponse,
                            ProvenanceFailure, RetryableTransport, SafetyLimit,
                            WorkRequest)

EXACT_ENDPOINT = "http://127.0.0.1:11434"
CONNECT_TIMEOUT_SECONDS = 10.0
IDLE_READ_TIMEOUT_SECONDS = 180.0
TOTAL_REQUEST_SECONDS = 600.0
MAX_PROMPT_BYTES = 64 * 1024
MAX_FRAME_BYTES = 512 * 1024
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_COMBINED_CHANNEL_BYTES = 1024 * 1024
MAX_CONTENT_BYTES = 256 * 1024
MAX_CANONICAL_JSON_BYTES = 256 * 1024
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 4096
_READ_CHUNK_BYTES = 64 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_OPTIONS = frozenset({
    "temperature", "top_p", "top_k", "min_p", "repeat_penalty",
    "repeat_last_n", "seed", "num_ctx", "num_predict",
})
_TOP_LEVEL_FIELDS = frozenset({
    "model", "created_at", "message", "done", "done_reason",
    "total_duration", "load_duration", "prompt_eval_count",
    "prompt_eval_duration", "eval_count", "eval_duration", "logprobs",
})
_MESSAGE_FIELDS = frozenset({"role", "content", "thinking", "tool_calls", "images"})
_TERMINAL_INTEGER_FIELDS = (
    "total_duration", "load_duration", "prompt_eval_count",
    "prompt_eval_duration", "eval_count", "eval_duration",
)
_RETRYABLE_EXCEPTIONS = (
    requests.Timeout, requests.ConnectionError, urllib3.exceptions.HTTPError,
    TimeoutError, ConnectionError, OSError,
)
_RESOURCE_ERROR_RE = re.compile(
    r"(?:out of memory|insufficient memory|not enough memory|memory allocation|"
    r"cuda[^\n]{0,80}memory|resource exhausted)", re.IGNORECASE,
)


class _DuplicateKey(ValueError):
    pass


@dataclass(frozen=True)
class SchemaAssessment:
    """Independent strict/semantic worksheet classification."""

    strict_invalid: bool
    semantic_invalid: bool

    @property
    def valid(self) -> bool:
        return not self.strict_invalid and not self.semantic_invalid


SchemaValidator: TypeAlias = Callable[[str, Any], SchemaAssessment]


@dataclass(frozen=True)
class RequestSpec:
    """Typed HTTP intent returned by the injected durable-request resolver.

    Chat hashes remain the hash of the exact Ollama payload, matching the frozen
    work plans.  Control hashes use :func:`request_spec_hash`; callers should not
    invent a second control-identity encoding.
    """

    kind: Literal["chat", "version", "tags", "show", "ps"]
    payload: Mapping[str, Any] | None = None
    worksheet: str | None = None
    expected_model: str | None = None
    expected_digest: str | None = None
    expected_version: str | None = None
    expected_models: Mapping[str, str] | None = None
    min_context: int | None = None
    purpose: str | None = None
    config_sha256: str | None = None
    cancel_on_first_content: bool = False


DurableRequest: TypeAlias = Union[WorkRequest, ControlRequest]
Resolver: TypeAlias = Callable[[DurableRequest], RequestSpec]


def request_spec_hash(spec: RequestSpec) -> str:
    """Return the request hash the durable request must carry."""
    if spec.kind == "chat":
        if not isinstance(spec.payload, Mapping):
            raise ValueError("chat spec requires a payload")
        return c0b2_schema.stable_hash(dict(spec.payload))
    if spec.kind == "version":
        identity = {"kind": "version", "expected_version": spec.expected_version}
    elif spec.kind == "tags":
        identity = {"kind": "tags", "expected_models": dict(spec.expected_models or {})}
    elif spec.kind == "show":
        identity = {"kind": "show", "model": spec.expected_model,
                    "digest": spec.expected_digest, "verbose": False}
    elif spec.kind == "ps":
        identity = {"kind": "ps", "purpose": spec.purpose,
                    "model": spec.expected_model, "digest": spec.expected_digest,
                    "min_context": spec.min_context,
                    "config_sha256": spec.config_sha256}
    else:  # pragma: no cover - Literal plus runtime validation protects callers
        raise ValueError("unsupported request spec")
    return c0b2_schema.stable_hash(identity)


def default_schema_validator(worksheet: str, value: Any) -> SchemaAssessment:
    """Classify the frozen worksheet in strict-first, mutually-exclusive order."""
    strict_invalid = False
    semantic_invalid = False
    try:
        c0b2_schema.validate(worksheet, value)
    except ValidationError as exc:
        semantic_errors = False
        for error in exc.errors(include_url=False):
            message = str(error.get("msg", ""))
            if not _semantic_message(message):
                strict_invalid = True
            else:
                semantic_errors = True
        semantic_invalid = semantic_errors and not strict_invalid
    except Exception:
        strict_invalid = True
    return SchemaAssessment(strict_invalid, semantic_invalid)


class BoundedOllamaTransport:
    """Strictly serial, bounded requests adapter for ``DurableExecutor``."""

    def __init__(self, resolver: Resolver, *, endpoint: str = EXACT_ENDPOINT,
                 session: Any | None = None,
                 schema_validator: SchemaValidator = default_schema_validator,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        if endpoint != EXACT_ENDPOINT:
            raise ProvenanceFailure("endpoint_mismatch")
        self.endpoint = endpoint
        self.resolver = resolver
        self.schema_validator = schema_validator
        self.monotonic = monotonic
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.max_redirects = 0
        self._call_lock = threading.Lock()
        self._active_lock = threading.Lock()
        self._active_response: Any | None = None

    def cancel_current(self) -> None:
        """Close only this adapter's current response, if one exists."""
        with self._active_lock:
            response = self._active_response
        if response is not None:
            try:
                response.close()
            except Exception:  # closing is best effort; executor records uncertainty
                pass

    def __call__(self, request: DurableRequest,
                 cancel: threading.Event) -> FakeResponse:
        if cancel.is_set():
            raise RetryableTransport("cancelled")
        with self._call_lock:
            try:
                resolved = self.resolver(request)
            except Exception:
                raise ProvenanceFailure("request_resolution_failed") from None
            spec = self._prepare_spec(request, resolved)
            started = self.monotonic()
            watcher_stop = threading.Event()
            watcher = threading.Thread(
                target=self._watch_cancel,
                args=(cancel, watcher_stop), daemon=True,
                name="c0b2-ollama-cancel",
            )
            watcher.start()
            response = None
            try:
                method, path, payload, accept = self._http_request(spec)
                response = self.session.request(
                    method, self.endpoint + path, json=payload, stream=True,
                    timeout=(CONNECT_TIMEOUT_SECONDS, IDLE_READ_TIMEOUT_SECONDS),
                    allow_redirects=False, proxies={"http": None, "https": None},
                    headers={"Accept": accept, "Accept-Encoding": "identity",
                             "Content-Type": "application/json"},
                )
                self._set_active(response, cancel)
                self._check_cancel_deadline(cancel, started)
                self._classify_status(response, cancel, started)
                self._validate_encoding(response)
                if spec.kind == "chat":
                    self._require_content_type(response, "application/x-ndjson")
                    return self._read_chat(response, spec, cancel, started)
                self._require_content_type(response, "application/json")
                body = self._read_all(response, cancel, started)
                value = _load_wire_json(body)
                _bounded_json(value)
                return self._control_result(spec, value)
            except (SafetyLimit, ProvenanceFailure, RetryableTransport):
                raise
            except _RETRYABLE_EXCEPTIONS:
                if cancel.is_set():
                    raise RetryableTransport("cancelled") from None
                raise RetryableTransport("transport_error") from None
            finally:
                watcher_stop.set()
                if response is not None:
                    self._clear_active(response)
                    try:
                        response.close()
                    except Exception:
                        pass
                watcher.join(timeout=0.2)

    def _prepare_spec(self, request: DurableRequest, spec: RequestSpec) -> RequestSpec:
        if not isinstance(spec, RequestSpec):
            raise ProvenanceFailure("resolver_type_mismatch")
        try:
            got_hash = request_spec_hash(spec)
        except (TypeError, ValueError, OverflowError):
            raise ProvenanceFailure("invalid_request_spec") from None
        if not _digest_equal(request.request_hash, got_hash):
            raise ProvenanceFailure("request_hash_mismatch")
        if spec.kind == "chat":
            payload = _detach_mapping(spec.payload)
            _validate_chat_spec(request, spec, payload)
            return RequestSpec(
                kind="chat", payload=payload, worksheet=spec.worksheet,
                expected_model=spec.expected_model,
                expected_digest=spec.expected_digest,
                cancel_on_first_content=spec.cancel_on_first_content,
            )
        _validate_control_spec(request, spec)
        models = (_detach_string_mapping(spec.expected_models)
                  if spec.expected_models is not None else None)
        return RequestSpec(
            kind=spec.kind, expected_model=spec.expected_model,
            expected_digest=spec.expected_digest, expected_version=spec.expected_version,
            expected_models=models, min_context=spec.min_context,
            purpose=spec.purpose, config_sha256=spec.config_sha256,
        )

    @staticmethod
    def _http_request(spec: RequestSpec) -> tuple[str, str, Any, str]:
        if spec.kind == "chat":
            return "POST", "/api/chat", dict(spec.payload or {}), "application/x-ndjson"
        if spec.kind == "show":
            return "POST", "/api/show", {
                "model": spec.expected_model, "verbose": False,
            }, "application/json"
        paths = {"version": "/api/version", "tags": "/api/tags", "ps": "/api/ps"}
        return "GET", paths[spec.kind], None, "application/json"

    def _watch_cancel(self, cancel: threading.Event, stop: threading.Event) -> None:
        while not stop.wait(0.01):
            if cancel.is_set():
                self.cancel_current()
                return

    def _set_active(self, response: Any, cancel: threading.Event) -> None:
        with self._active_lock:
            self._active_response = response
        if cancel.is_set():
            self.cancel_current()

    def _clear_active(self, response: Any) -> None:
        with self._active_lock:
            if self._active_response is response:
                self._active_response = None

    def _check_cancel_deadline(self, cancel: threading.Event, started: float) -> None:
        if cancel.is_set():
            self.cancel_current()
            raise RetryableTransport("cancelled")
        if self.monotonic() - started > TOTAL_REQUEST_SECONDS:
            self.cancel_current()
            raise RetryableTransport("request_timeout")

    @staticmethod
    def _validate_encoding(response: Any) -> None:
        encoding = _header(response, "content-encoding").strip().lower()
        if encoding not in ("", "identity"):
            raise SafetyLimit("unexpected_content_encoding")

    @staticmethod
    def _require_content_type(response: Any, wanted: str) -> None:
        actual = _header(response, "content-type").split(";", 1)[0].strip().lower()
        if actual != wanted:
            raise SafetyLimit("unexpected_content_type")

    def _classify_status(self, response: Any, cancel: threading.Event,
                         started: float) -> None:
        status = response.status_code
        if type(status) is not int:
            raise SafetyLimit("invalid_http_status")
        if status == 200:
            return
        if status in (429, 503) or 500 <= status <= 599:
            raise RetryableTransport("retryable_http_status")
        if 400 <= status <= 499:
            body = self._read_all(response, cancel, started)
            message = _bounded_error_message(body)
            if message and _RESOURCE_ERROR_RE.search(message):
                raise RetryableTransport("resource_error")
            raise ProvenanceFailure("http_client_error")
        if 300 <= status <= 399:
            raise ProvenanceFailure("redirect_refused")
        raise ProvenanceFailure("unexpected_http_status")

    def _wire_chunks(self, response: Any, cancel: threading.Event,
                     started: float):
        raw = getattr(response, "raw", None)
        if raw is None or not callable(getattr(raw, "stream", None)):
            raise SafetyLimit("response_has_no_raw_stream")
        total = 0
        for chunk in raw.stream(amt=_READ_CHUNK_BYTES, decode_content=False):
            self._check_cancel_deadline(cancel, started)
            if not isinstance(chunk, bytes):
                raise SafetyLimit("non_bytes_http_body")
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                raise SafetyLimit("http_body_limit")
            yield chunk, total
        self._check_cancel_deadline(cancel, started)

    def _read_all(self, response: Any, cancel: threading.Event,
                  started: float) -> bytes:
        parts: list[bytes] = []
        for chunk, _total in self._wire_chunks(response, cancel, started):
            parts.append(chunk)
        return b"".join(parts)

    def _read_chat(self, response: Any, spec: RequestSpec,
                   cancel: threading.Event, started: float) -> FakeResponse:
        def frames():
            pending = bytearray()
            body_total = 0
            for chunk, body_total in self._wire_chunks(response, cancel, started):
                cursor = 0
                while True:
                    newline = chunk.find(b"\n", cursor)
                    if newline < 0:
                        _append_frame_bytes(pending, chunk[cursor:])
                        break
                    _append_frame_bytes(pending, chunk[cursor:newline])
                    if pending.strip():
                        yield _load_frame(bytes(pending)), body_total
                    pending.clear()
                    cursor = newline + 1
            if pending.strip():
                yield _load_frame(bytes(pending)), body_total
            yield None, body_total

        return self._consume_frames(frames(), spec, cancel)

    def _consume_frames(self, frames: Any, spec: RequestSpec,
                        cancel: threading.Event) -> FakeResponse:
        chunks: list[str] = []
        content_bytes = 0
        thinking_bytes = 0
        body_bytes = 0
        done = False
        done_reason = None
        metrics: dict[str, int | str | bool | None] = {}
        for frame, body_bytes in frames:
            if frame is None:
                break
            if done:
                raise SafetyLimit("frame_after_done")
            if "error" in frame:
                if type(frame["error"]) is not str:
                    raise SafetyLimit("invalid_stream_error")
                raise RetryableTransport(
                    "resource_error" if _RESOURCE_ERROR_RE.search(frame["error"])
                    else "stream_error")
            _validate_frame(frame, spec.expected_model or "")
            message = frame["message"]
            content = message.get("content", "")
            thinking = message.get("thinking", "")
            content_piece = content.encode("utf-8")
            thinking_piece = thinking.encode("utf-8")
            content_bytes += len(content_piece)
            thinking_bytes += len(thinking_piece)
            if content_bytes > MAX_CONTENT_BYTES:
                raise SafetyLimit("answer_content_limit")
            if content_bytes + thinking_bytes > MAX_COMBINED_CHANNEL_BYTES:
                raise SafetyLimit("combined_channel_limit")
            if content:
                chunks.append(content)
                if spec.cancel_on_first_content:
                    cancel.set()
                    self.cancel_current()
                    raise RetryableTransport("cancelled")
            if frame["done"]:
                done = True
                done_reason = frame.get("done_reason")
                if type(done_reason) is not str or not done_reason:
                    raise SafetyLimit("invalid_done_reason")
                for field in _TERMINAL_INTEGER_FIELDS:
                    value = frame.get(field)
                    if value is not None:
                        metrics[field] = value
        if not done:
            raise RetryableTransport("stream_ended_without_done")
        content = "".join(chunks)
        metadata: dict[str, Any] = {
            "http_status": 200, "model": spec.expected_model,
            "model_digest": spec.expected_digest, "done_reason": done_reason,
            "raw_body_bytes": body_bytes, "content_bytes": content_bytes,
            "thinking_bytes": thinking_bytes, "tools_empty": True,
            "images_empty": True, "unknown_message_fields_empty": True,
            **metrics,
        }
        outcome = "SCHEMA_INVALID"
        strict_invalid = False
        semantic_invalid = False
        try:
            parsed = _load_answer_json(content)
            _bounded_json(parsed)
            try:
                assessment = self.schema_validator(spec.worksheet or "", parsed)
            except SafetyLimit:
                raise
            except Exception:
                raise SafetyLimit("schema_validator_failed") from None
            if not isinstance(assessment, SchemaAssessment):
                raise SafetyLimit("schema_validator_type")
            if (type(assessment.strict_invalid) is not bool
                    or type(assessment.semantic_invalid) is not bool
                    or assessment.strict_invalid and assessment.semantic_invalid):
                raise SafetyLimit("schema_validator_classification")
            strict_invalid = assessment.strict_invalid
            semantic_invalid = assessment.semantic_invalid
            if assessment.valid:
                outcome = "ACCEPTED"
                metadata["canonical_content_sha256"] = hashlib.sha256(
                    _canonical_json(parsed)).hexdigest()
        except _AnswerSchemaInvalid:
            strict_invalid = True
        metadata["strict_schema_invalid"] = strict_invalid
        metadata["semantic_invalid"] = semantic_invalid
        return FakeResponse(
            content=content, metadata=metadata,
            accepted=outcome == "ACCEPTED", outcome=outcome,
        )

    def _control_result(self, spec: RequestSpec, value: Any) -> FakeResponse:
        if spec.kind == "version":
            result = _sanitize_version(value, spec.expected_version or "")
        elif spec.kind == "tags":
            result = _sanitize_tags(value, spec.expected_models or {})
        elif spec.kind == "show":
            result = _sanitize_show(value, spec.expected_model or "",
                                    spec.expected_digest or "")
        elif spec.kind == "ps":
            result = _sanitize_ps(value, spec.expected_model or "",
                                  spec.expected_digest or "", spec.min_context or 0,
                                  spec.purpose or "", spec.config_sha256 or "")
        else:  # pragma: no cover - chat is handled before this method
            raise ProvenanceFailure("invalid_control_kind")
        encoded = _canonical_json(result)
        return FakeResponse(
            content=encoded.decode("utf-8"),
            metadata={"http_status": 200, "control": spec.kind,
                      "response_sha256": hashlib.sha256(encoded).hexdigest()},
        )


class _AnswerSchemaInvalid(ValueError):
    pass


def _validate_chat_spec(request: DurableRequest, spec: RequestSpec,
                        payload: dict[str, Any]) -> None:
    if (type(spec.expected_model) is not str or not spec.expected_model
            or request.model != spec.expected_model
            or not _valid_digest(spec.expected_digest)):
        raise ProvenanceFailure("invalid_expected_digest")
    if spec.worksheet not in ("v1", "v2"):
        raise ProvenanceFailure("invalid_worksheet")
    expected_keys = {"model", "messages", "stream", "format", "options", "think",
                     "keep_alive"}
    if set(payload) != expected_keys or payload.get("model") != spec.expected_model:
        raise ProvenanceFailure("chat_payload_identity")
    if type(spec.cancel_on_first_content) is not bool:
        raise ProvenanceFailure("cancellation_mode_type")
    if isinstance(request, WorkRequest) and spec.cancel_on_first_content:
        raise ProvenanceFailure("scored_work_cannot_self_cancel")
    messages = payload.get("messages")
    if (not isinstance(messages, list) or len(messages) != 1
            or type(messages[0]) is not dict
            or set(messages[0]) != {"role", "content"}
            or messages[0].get("role") != "user"
            or type(messages[0].get("content")) is not str):
        raise ProvenanceFailure("chat_messages_invalid")
    if len(messages[0]["content"].encode("utf-8")) > MAX_PROMPT_BYTES:
        raise SafetyLimit("prompt_limit")
    if payload.get("stream") is not True or payload.get("keep_alive") != "15m":
        raise ProvenanceFailure("chat_runtime_options")
    if payload.get("format") != c0b2_schema.worksheet_schema(spec.worksheet):
        raise ProvenanceFailure("worksheet_schema_drift")
    options = payload.get("options")
    if type(options) is not dict or set(options) != _OPTIONS:
        raise ProvenanceFailure("generation_options_shape")
    for field in ("top_k", "repeat_last_n", "seed", "num_ctx", "num_predict"):
        if type(options[field]) is not int:
            raise ProvenanceFailure("generation_option_type")
    for field in ("temperature", "top_p", "min_p", "repeat_penalty"):
        if type(options[field]) is not float:
            raise ProvenanceFailure("generation_option_type")
    if options["num_ctx"] <= 0 or options["num_predict"] <= 0:
        raise ProvenanceFailure("generation_option_range")
    if type(payload.get("think")) not in (bool, str):
        raise ProvenanceFailure("thinking_option_type")


def _validate_control_spec(request: DurableRequest, spec: RequestSpec) -> None:
    if not isinstance(request, ControlRequest):
        raise ProvenanceFailure("work_request_requires_chat")
    if spec.payload is not None or spec.cancel_on_first_content:
        raise ProvenanceFailure("control_spec_has_payload")
    if spec.kind == "version":
        if request.model != SERVER_CONTROL_MODEL:
            raise ProvenanceFailure("server_control_model")
        if (not spec.expected_version or any(value is not None for value in (
                spec.expected_model, spec.expected_digest, spec.expected_models,
                spec.min_context, spec.purpose, spec.config_sha256))):
            raise ProvenanceFailure("version_spec_shape")
    elif spec.kind == "tags":
        if request.model != SERVER_CONTROL_MODEL:
            raise ProvenanceFailure("server_control_model")
        if (not spec.expected_models or any(value is not None for value in (
                spec.expected_model, spec.expected_digest, spec.expected_version,
                spec.min_context, spec.purpose, spec.config_sha256))):
            raise ProvenanceFailure("tags_spec_shape")
        if any(not model or not _valid_digest(digest)
               for model, digest in spec.expected_models.items()):
            raise ProvenanceFailure("tags_expected_identity")
    elif spec.kind == "show":
        if request.model != spec.expected_model:
            raise ProvenanceFailure("model_control_identity")
        if (not spec.expected_model or not _valid_digest(spec.expected_digest)
                or any(value is not None for value in (
                    spec.expected_version, spec.expected_models, spec.min_context,
                    spec.purpose, spec.config_sha256))):
            raise ProvenanceFailure("show_spec_shape")
    elif spec.kind == "ps":
        if request.model != spec.expected_model:
            raise ProvenanceFailure("model_control_identity")
        if (not spec.expected_model or not _valid_digest(spec.expected_digest)
                or type(spec.min_context) is not int or spec.min_context <= 0
                or not spec.purpose or not _valid_digest(spec.config_sha256)
                or spec.expected_version is not None or spec.expected_models is not None):
            raise ProvenanceFailure("ps_spec_shape")
    else:
        raise ProvenanceFailure("unknown_control_kind")


def _validate_frame(frame: dict[str, Any], model: str) -> None:
    if frame.get("model") != model or type(frame.get("model")) is not str:
        raise ProvenanceFailure("response_model_mismatch")
    if type(frame.get("done")) is not bool or type(frame.get("message")) is not dict:
        raise SafetyLimit("invalid_stream_frame")
    if "created_at" in frame and type(frame["created_at"]) is not str:
        raise SafetyLimit("invalid_stream_timestamp")
    unknown_top = set(frame) - _TOP_LEVEL_FIELDS
    if any(not _empty_channel(frame[key]) for key in unknown_top):
        raise SafetyLimit("unknown_top_level_channel")
    message = frame["message"]
    if set(message) - _MESSAGE_FIELDS:
        unknown = set(message) - _MESSAGE_FIELDS
        if any(not _empty_channel(message[key]) for key in unknown):
            raise SafetyLimit("unknown_message_channel")
    if message.get("role", "assistant") != "assistant":
        raise SafetyLimit("invalid_message_role")
    for field in ("content", "thinking"):
        if type(message.get(field, "")) is not str:
            raise SafetyLimit("invalid_text_channel")
    for field in ("tool_calls", "images"):
        if field in message and (type(message[field]) is not list or message[field]):
            raise SafetyLimit("forbidden_message_channel")
    if "logprobs" in frame and (type(frame["logprobs"]) is not list
                                or frame["logprobs"]):
        raise SafetyLimit("forbidden_logprobs_channel")
    for field in _TERMINAL_INTEGER_FIELDS:
        if field in frame and (type(frame[field]) is not int or frame[field] < 0):
            raise SafetyLimit("invalid_metric_type")


def _sanitize_version(value: Any, expected: str) -> dict[str, str]:
    if type(value) is not dict or set(value) != {"version"} \
            or type(value.get("version")) is not str or value["version"] != expected:
        raise ProvenanceFailure("ollama_version_mismatch")
    return {"version": expected}


def _sanitize_tags(value: Any, expected: Mapping[str, str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"models"} or type(value["models"]) is not list:
        raise ProvenanceFailure("tags_shape")
    found: dict[str, str] = {}
    for row in value["models"]:
        if type(row) is not dict:
            raise ProvenanceFailure("tags_row_shape")
        name, alias, digest = row.get("name"), row.get("model"), row.get("digest")
        if type(name) is not str or type(alias) is not str or name != alias:
            raise ProvenanceFailure("tags_name_mismatch")
        if name in found:
            raise ProvenanceFailure("duplicate_model_tag")
        if not _valid_digest(digest):
            raise ProvenanceFailure("invalid_model_digest")
        found[name] = digest
    for model, digest in expected.items():
        if model.lower().endswith(":cloud") or "-cloud" in model.lower():
            raise ProvenanceFailure("cloud_model_refused")
        if model not in found or not _digest_equal(found[model], digest):
            raise ProvenanceFailure("model_digest_mismatch")
    return {"models": [{"name": model, "digest": expected[model]}
                       for model in sorted(expected)]}


def _sanitize_show(value: Any, model: str, digest: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ProvenanceFailure("show_shape")
    for field in ("parameters", "template"):
        if field in value and type(value[field]) is not str:
            raise ProvenanceFailure("show_field_type")
    capabilities = value.get("capabilities", [])
    if type(capabilities) is not list or any(type(item) is not str for item in capabilities):
        raise ProvenanceFailure("show_capabilities_type")
    for field in ("details", "model_info"):
        if field in value and type(value[field]) is not dict:
            raise ProvenanceFailure("show_field_type")
    details = value.get("details", {})
    safe_detail_names = (
        "parent_model", "format", "family", "families", "parameter_size",
        "quantization_level",
    )
    safe_details = {name: details[name] for name in safe_detail_names if name in details}
    return {
        "model": model, "digest": digest, "capabilities": capabilities,
        "parameters_sha256": hashlib.sha256(
            value.get("parameters", "").encode("utf-8")).hexdigest(),
        "template_sha256": hashlib.sha256(
            value.get("template", "").encode("utf-8")).hexdigest(),
        "model_info_sha256": hashlib.sha256(
            _canonical_json(value.get("model_info", {}))).hexdigest(),
        "details": safe_details,
    }


def _sanitize_ps(value: Any, model: str, digest: str, min_context: int,
                 purpose: str, config_sha256: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"models"} or type(value["models"]) is not list:
        raise ProvenanceFailure("ps_shape")
    matches: list[dict[str, Any]] = []
    for row in value["models"]:
        if type(row) is not dict:
            raise ProvenanceFailure("ps_row_shape")
        if row.get("name") == model or row.get("model") == model:
            matches.append(row)
    if len(matches) != 1:
        raise ProvenanceFailure("ps_model_missing_or_duplicate")
    row = matches[0]
    if row.get("name") != model or row.get("model") != model \
            or not _digest_equal(row.get("digest"), digest):
        raise ProvenanceFailure("ps_model_identity")
    for field in ("size", "size_vram", "context_length"):
        if type(row.get(field)) is not int or row[field] < 0:
            raise ProvenanceFailure("ps_metric_type")
    if row["context_length"] < min_context:
        raise ProvenanceFailure("ps_context_too_small")
    return {
        "purpose": purpose, "config_sha256": config_sha256,
        "model": model, "digest": digest, "size": row["size"],
        "size_vram": row["size_vram"], "context_length": row["context_length"],
    }


def _semantic_message(message: str) -> bool:
    return any(fragment in message for fragment in (
        "categories must occur once", "duplicate category/quote evidence",
        "present must equal bool(evidence)", "requires evidence", "requires no evidence",
    ))


def _load_wire_json(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8", errors="strict")
        return json.loads(text, object_pairs_hook=_unique_pairs,
                          parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKey, ValueError,
            RecursionError):
        raise SafetyLimit("invalid_wire_json") from None


def _load_frame(raw: bytes) -> dict[str, Any]:
    value = _load_wire_json(raw)
    # The 256-KiB canonical cap applies to the assembled answer/control JSON.
    # NDJSON envelopes have their separate 512-KiB raw-frame cap; still reject
    # adversarial envelope depth/node shapes before reading channel fields.
    _bounded_json_shape(value)
    if type(value) is not dict:
        raise SafetyLimit("ndjson_frame_not_object")
    return value


def _load_answer_json(content: str) -> Any:
    try:
        return json.loads(content, object_pairs_hook=_unique_pairs,
                          parse_constant=_reject_constant)
    except RecursionError:
        raise SafetyLimit("json_depth_limit") from None
    except (json.JSONDecodeError, _DuplicateKey, ValueError):
        raise _AnswerSchemaInvalid from None


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _bounded_json(value: Any) -> None:
    _bounded_json_shape(value)
    if len(_canonical_json(value)) > MAX_CANONICAL_JSON_BYTES:
        raise SafetyLimit("canonical_json_limit")


def _bounded_json_shape(value: Any) -> None:
    nodes = 0
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise SafetyLimit("json_depth_limit")
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise SafetyLimit("json_node_limit")
        if isinstance(item, dict):
            nodes += len(item)  # object keys are decoded nodes too
            if nodes > MAX_JSON_NODES:
                raise SafetyLimit("json_node_limit")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise SafetyLimit("uncanonicalizable_json") from None


def _append_frame_bytes(pending: bytearray, piece: bytes) -> None:
    if len(pending) + len(piece) > MAX_FRAME_BYTES:
        raise SafetyLimit("ndjson_frame_limit")
    pending.extend(piece)


def _bounded_error_message(raw: bytes) -> str | None:
    try:
        value = _load_wire_json(raw)
        _bounded_json(value)
    except SafetyLimit:
        return None
    if type(value) is dict and type(value.get("error")) is str:
        return value["error"]
    return None


def _header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", {})
    for key, value in headers.items():
        if str(key).lower() == name:
            return str(value)
    return ""


def _detach_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProvenanceFailure("payload_not_mapping")
    try:
        return json.loads(c0b2_schema.canonical_json(dict(value)))
    except Exception:
        raise ProvenanceFailure("payload_not_canonical") from None


def _detach_string_mapping(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or any(type(k) is not str or type(v) is not str
                                             for k, v in value.items()):
        raise ProvenanceFailure("identity_map_type")
    return dict(value)


def _valid_digest(value: Any) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _digest_equal(left: Any, right: Any) -> bool:
    return (_valid_digest(left) and _valid_digest(right)
            and hmac.compare_digest(left, right))


def _empty_channel(value: Any) -> bool:
    return value is None or value is False or value == "" or value == [] or value == {}
