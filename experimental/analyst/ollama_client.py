"""Bounded loopback-only Ollama transport for Analyst.

The client owns HTTP and cancellation only. It never reads or writes Analyst's
database, logs prompt/model text, or decides durable retry state.
"""

from __future__ import annotations

import hmac
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests
import urllib3

from .ollama_contract import (
    CONNECT_TIMEOUT_SECONDS,
    EXPECTED_IDENTITY,
    IDLE_READ_TIMEOUT_SECONDS,
    MAX_BODY_BYTES,
    MODEL_TAG,
    OLLAMA_CHAT_URL,
    OLLAMA_TAGS_URL,
    OLLAMA_VERSION_URL,
    TOTAL_REQUEST_SECONDS,
    ChatRequest,
    ChatResult,
    OllamaIdentity,
    OllamaStatus,
    PreflightResult,
    QUALIFIED_OLLAMA_VERSION,
    validate_chat_request,
)
from .ollama_protocol import (
    ChatStreamParser,
    OllamaAnswerError,
    OllamaProvenanceError,
    OllamaSafetyError,
    OllamaStreamError,
    SafetyCode,
    StreamCode,
    parse_answer_json,
    parse_tags_response,
    parse_version_response,
)


_READ_CHUNK_BYTES = 64 * 1024
_CALLER_POLL_SECONDS = 0.01
_RESOURCE_ERROR_RE = re.compile(
    r"(?:out of memory|insufficient memory|not enough memory|memory allocation|"
    r"cuda[^\n]{0,80}memory|resource exhausted)",
    re.IGNORECASE,
)
_TIMEOUT_EXCEPTIONS = (
    requests.Timeout,
    urllib3.exceptions.TimeoutError,
    TimeoutError,
)
_TRANSPORT_EXCEPTIONS = (
    requests.ConnectionError,
    urllib3.exceptions.HTTPError,
    ConnectionError,
    OSError,
)
_GLOBAL_REQUEST_SLOT = threading.BoundedSemaphore(1)

CancelProbe = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class _HttpIntent:
    method: str
    url: str
    body: bytes | None
    accept: str
    kind: str


class _WorkerState:
    def __init__(self) -> None:
        self.done = threading.Event()
        self._lock = threading.Lock()
        self._abandoned = False
        self._result: object | None = None
        self._status: OllamaStatus | None = None

    def abandon(self) -> bool:
        with self._lock:
            first = not self._abandoned
            self._abandoned = True
            return first

    def is_abandoned(self) -> bool:
        with self._lock:
            return self._abandoned

    def publish_result(self, value: object) -> None:
        with self._lock:
            if not self._abandoned:
                self._result = value

    def publish_status(self, status: OllamaStatus) -> None:
        with self._lock:
            if not self._abandoned:
                self._status = status

    def outcome(self) -> tuple[object | None, OllamaStatus | None]:
        with self._lock:
            return self._result, self._status


class OllamaClient:
    """One serial, caller-bounded client for the exact V1 loopback endpoint."""

    def __init__(
        self,
        *,
        session: Any | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(monotonic):
            raise TypeError("monotonic clock must be callable")
        self._monotonic = monotonic
        self._session = session if session is not None else requests.Session()
        self._session.trust_env = False
        self._session.max_redirects = 0
        self._active_lock = threading.Lock()
        self._active_response: Any | None = None
        self._close_target: Any | None = None
        self._close_done: threading.Event | None = None

    def preflight(
        self,
        expected: OllamaIdentity = EXPECTED_IDENTITY,
        *,
        cancel: CancelProbe,
    ) -> PreflightResult:
        """Verify daemon version plus the exact local tag/digest without inference."""
        _require_cancel_probe(cancel)
        if cancel():
            return PreflightResult(OllamaStatus.CANCELLED_UNVERIFIED)
        if type(expected) is not OllamaIdentity:
            raise TypeError("preflight identity must use the exact OllamaIdentity type")
        if not _matches_expected_identity(expected):
            return PreflightResult(OllamaStatus.IDENTITY_MISMATCH)
        version_intent = _HttpIntent(
            "GET", OLLAMA_VERSION_URL, None, "application/json", "version",
        )
        version, status = self._execute(version_intent, cancel)
        if status is not None:
            return PreflightResult(status)
        try:
            observed = parse_version_response(
                _require_bytes(version), QUALIFIED_OLLAMA_VERSION,
            )
        except OllamaProvenanceError:
            return PreflightResult(OllamaStatus.IDENTITY_MISMATCH)
        except OllamaSafetyError as exc:
            return PreflightResult(_safety_status(exc))
        tags_intent = _HttpIntent(
            "GET", OLLAMA_TAGS_URL, None, "application/json", "tags",
        )
        tags, status = self._execute(tags_intent, cancel)
        if status is not None:
            return PreflightResult(status)
        try:
            parsed_tags = parse_tags_response(
                _require_bytes(tags), {expected.model_tag: expected.model_digest},
            )
        except OllamaProvenanceError:
            return PreflightResult(OllamaStatus.IDENTITY_MISMATCH)
        except OllamaSafetyError as exc:
            return PreflightResult(_safety_status(exc))
        if len(parsed_tags.models) != 1:
            return PreflightResult(OllamaStatus.IDENTITY_MISMATCH)
        return PreflightResult(
            OllamaStatus.SUCCESS,
            observed_version=observed.version,
            model_digest=parsed_tags.models[0].digest,
        )

    def chat(
        self,
        request: ChatRequest,
        *,
        expected_sha256: str,
        cancel: CancelProbe,
    ) -> ChatResult:
        """Run one exact chat request and retain text only on validated success."""
        _require_cancel_probe(cancel)
        if cancel():
            return ChatResult(OllamaStatus.CANCELLED_UNVERIFIED)
        try:
            validate_chat_request(request)
        except (TypeError, ValueError):
            return ChatResult(OllamaStatus.IDENTITY_MISMATCH)
        if (
            type(expected_sha256) is not str
            or not hmac.compare_digest(request.request_sha256, expected_sha256)
        ):
            return ChatResult(OllamaStatus.IDENTITY_MISMATCH)
        intent = _HttpIntent(
            "POST", OLLAMA_CHAT_URL, request.body,
            "application/x-ndjson", "chat",
        )
        value, status = self._execute(intent, cancel)
        if status is not None:
            return ChatResult(status)
        if not isinstance(value, ChatResult):
            return ChatResult(OllamaStatus.PROTOCOL_VIOLATION)
        return value

    def cancel_current(self) -> None:
        """Initiate one nonblocking close for only this client's active response."""
        with self._active_lock:
            response = self._active_response
        if response is not None:
            self._initiate_close(response)

    def _execute(
        self, intent: _HttpIntent, cancel: CancelProbe,
    ) -> tuple[object | None, OllamaStatus | None]:
        if cancel():
            return None, OllamaStatus.CANCELLED_UNVERIFIED
        if not _GLOBAL_REQUEST_SLOT.acquire(blocking=False):
            return None, (
                OllamaStatus.CANCELLED_UNVERIFIED
                if cancel() else OllamaStatus.TRANSPORT_UNAVAILABLE
            )
        if cancel():
            _GLOBAL_REQUEST_SLOT.release()
            return None, OllamaStatus.CANCELLED_UNVERIFIED
        state = _WorkerState()
        started = self._monotonic()
        worker = threading.Thread(
            target=self._request_worker,
            args=(intent, cancel, started, state),
            daemon=True,
            name="analyst-ollama-request",
        )
        try:
            worker.start()
        except BaseException:
            _GLOBAL_REQUEST_SLOT.release()
            raise
        return self._await_worker(state, cancel, started)

    def _request_worker(
        self,
        intent: _HttpIntent,
        cancel: CancelProbe,
        started: float,
        state: _WorkerState,
    ) -> None:
        try:
            result, status = self._perform(intent, cancel, started, state)
            if status is not None:
                state.publish_status(status)
            elif result is not None:
                state.publish_result(result)
            else:
                state.publish_status(OllamaStatus.PROTOCOL_VIOLATION)
        except _TIMEOUT_EXCEPTIONS:
            state.publish_status(
                OllamaStatus.CANCELLED_UNVERIFIED
                if cancel() else OllamaStatus.REQUEST_TIMEOUT
            )
        except _TRANSPORT_EXCEPTIONS:
            state.publish_status(
                OllamaStatus.CANCELLED_UNVERIFIED
                if cancel() else OllamaStatus.TRANSPORT_UNAVAILABLE
            )
        except OllamaSafetyError as exc:
            state.publish_status(_safety_status(exc))
        except Exception:
            state.publish_status(OllamaStatus.PROTOCOL_VIOLATION)
        finally:
            _GLOBAL_REQUEST_SLOT.release()
            state.done.set()

    def _await_worker(
        self, state: _WorkerState, cancel: CancelProbe, started: float,
    ) -> tuple[object | None, OllamaStatus | None]:
        deadline = started + TOTAL_REQUEST_SECONDS
        while True:
            if cancel():
                self._abandon_worker(state)
                return None, OllamaStatus.CANCELLED_UNVERIFIED
            now = self._monotonic()
            if now >= deadline:
                self._abandon_worker(state)
                return None, OllamaStatus.REQUEST_TIMEOUT
            if not state.done.wait(min(_CALLER_POLL_SECONDS, deadline - now)):
                continue
            if cancel():
                self._abandon_worker(state)
                return None, OllamaStatus.CANCELLED_UNVERIFIED
            if self._monotonic() >= deadline:
                self._abandon_worker(state)
                return None, OllamaStatus.REQUEST_TIMEOUT
            value, status = state.outcome()
            if status is None and value is None:
                return None, OllamaStatus.PROTOCOL_VIOLATION
            return value, status

    def _abandon_worker(self, state: _WorkerState) -> None:
        if not state.abandon():
            return
        self.cancel_current()

    def _perform(
        self,
        intent: _HttpIntent,
        cancel: CancelProbe,
        started: float,
        state: _WorkerState,
    ) -> tuple[object | None, OllamaStatus | None]:
        response = None
        try:
            response = self._session.request(
                intent.method,
                intent.url,
                data=intent.body,
                stream=True,
                timeout=(CONNECT_TIMEOUT_SECONDS, IDLE_READ_TIMEOUT_SECONDS),
                allow_redirects=False,
                proxies={"http": None, "https": None},
                headers={
                    "Accept": intent.accept,
                    "Accept-Encoding": "identity",
                    **({"Content-Type": "application/json"} if intent.body else {}),
                },
            )
            self._set_active(response, cancel)
            if state.is_abandoned():
                return None, OllamaStatus.TRANSPORT_UNAVAILABLE
            status = self._classify_http_status(response, intent, cancel, started)
            if status is not None:
                return None, status
            if not _identity_encoding(response):
                return None, OllamaStatus.PROTOCOL_VIOLATION
            if not _content_type_is(response, intent.accept):
                return None, OllamaStatus.PROTOCOL_VIOLATION
            if intent.kind == "chat":
                return self._read_chat(response, cancel, started), None
            return self._read_all(response, cancel, started), None
        finally:
            if response is not None:
                self._finish_response(response)

    def _classify_http_status(
        self,
        response: Any,
        intent: _HttpIntent,
        cancel: CancelProbe,
        started: float,
    ) -> OllamaStatus | None:
        status = getattr(response, "status_code", None)
        if type(status) is not int:
            return OllamaStatus.PROTOCOL_VIOLATION
        if status == 200:
            return None
        if status in {429, 503}:
            return OllamaStatus.RESOURCE_BUSY
        if 300 <= status <= 399:
            return OllamaStatus.PROTOCOL_VIOLATION
        if 400 <= status <= 499:
            body, read_status = self._read_all_status(response, cancel, started)
            if read_status is not None:
                return read_status
            if _resource_error(body):
                return OllamaStatus.RESOURCE_BUSY
            return (
                OllamaStatus.IDENTITY_MISMATCH
                if status == 404 or intent.kind != "chat"
                else OllamaStatus.PROTOCOL_VIOLATION
            )
        if 500 <= status <= 599:
            body, read_status = self._read_all_status(response, cancel, started)
            if read_status is not None:
                return read_status
            if _resource_error(body):
                return OllamaStatus.RESOURCE_BUSY
            return OllamaStatus.TRANSPORT_UNAVAILABLE
        return OllamaStatus.PROTOCOL_VIOLATION

    def _read_chat(
        self, response: Any, cancel: CancelProbe, started: float,
    ) -> ChatResult:
        parser = ChatStreamParser(MODEL_TAG)
        try:
            for chunk in self._wire_chunks(response, cancel, started):
                parser.feed(
                    chunk,
                    before_frame=lambda: self._check_stream_cancel(cancel),
                )
            parsed = parser.finish()
        except OllamaSafetyError as exc:
            return ChatResult(_safety_status(exc))
        except OllamaProvenanceError:
            return ChatResult(OllamaStatus.IDENTITY_MISMATCH)
        except OllamaStreamError as exc:
            status = (
                OllamaStatus.RESOURCE_BUSY
                if exc.code is StreamCode.RESOURCE_ERROR
                else OllamaStatus.TRANSPORT_UNAVAILABLE
            )
            return ChatResult(status)
        except _TIMEOUT_EXCEPTIONS:
            return ChatResult(
                OllamaStatus.CANCELLED_UNVERIFIED
                if cancel() else OllamaStatus.REQUEST_TIMEOUT
            )
        except _TRANSPORT_EXCEPTIONS:
            return ChatResult(
                OllamaStatus.CANCELLED_UNVERIFIED
                if cancel() else OllamaStatus.TRANSPORT_UNAVAILABLE
            )
        metrics = parsed.metrics
        if metrics.done_reason == "length":
            return ChatResult(OllamaStatus.MODEL_INVALID, metrics=metrics)
        if metrics.done_reason != "stop":
            return ChatResult(OllamaStatus.PROTOCOL_VIOLATION)
        try:
            parse_answer_json(parsed.content)
            from .worksheet import validate

            validate(parsed.content)
        except OllamaSafetyError as exc:
            return ChatResult(_safety_status(exc))
        except OllamaAnswerError:
            return ChatResult(OllamaStatus.MODEL_INVALID, metrics=metrics)
        except ValueError:
            return ChatResult(OllamaStatus.MODEL_INVALID, metrics=metrics)
        return ChatResult(
            OllamaStatus.SUCCESS,
            content=parsed.content,
            metrics=metrics,
        )

    def _read_all(
        self, response: Any, cancel: CancelProbe, started: float,
    ) -> bytes:
        parts: list[bytes] = []
        for chunk in self._wire_chunks(response, cancel, started):
            parts.append(chunk)
        return b"".join(parts)

    def _read_all_status(
        self, response: Any, cancel: CancelProbe, started: float,
    ) -> tuple[bytes, OllamaStatus | None]:
        try:
            return self._read_all(response, cancel, started), None
        except OllamaSafetyError as exc:
            return b"", _safety_status(exc)
        except _TIMEOUT_EXCEPTIONS:
            return b"", (
                OllamaStatus.CANCELLED_UNVERIFIED
                if cancel() else OllamaStatus.REQUEST_TIMEOUT
            )
        except _TRANSPORT_EXCEPTIONS:
            return b"", (
                OllamaStatus.CANCELLED_UNVERIFIED
                if cancel() else OllamaStatus.TRANSPORT_UNAVAILABLE
            )

    def _wire_chunks(
        self, response: Any, cancel: CancelProbe, started: float,
    ):
        raw = getattr(response, "raw", None)
        if raw is None or not callable(getattr(raw, "stream", None)):
            raise OllamaSafetyError(SafetyCode.INVALID_WIRE_JSON)
        body_bytes = 0
        for chunk in raw.stream(amt=_READ_CHUNK_BYTES, decode_content=False):
            if cancel():
                self.cancel_current()
                raise requests.ConnectionError("cancelled")
            if self._monotonic() - started >= TOTAL_REQUEST_SECONDS:
                self.cancel_current()
                raise requests.Timeout("request timeout")
            if type(chunk) is not bytes:
                raise OllamaSafetyError(SafetyCode.INVALID_WIRE_JSON)
            if chunk:
                body_bytes += len(chunk)
                if body_bytes > MAX_BODY_BYTES:
                    raise OllamaSafetyError(SafetyCode.BODY_LIMIT)
                yield chunk
        if cancel():
            self.cancel_current()
            raise requests.ConnectionError("cancelled")

    def _check_stream_cancel(self, cancel: CancelProbe) -> None:
        if cancel():
            self.cancel_current()
            raise requests.ConnectionError("cancelled")

    def _set_active(self, response: Any, cancel: CancelProbe) -> None:
        with self._active_lock:
            self._active_response = response
        if cancel():
            self.cancel_current()

    def _initiate_close(self, response: Any) -> None:
        with self._active_lock:
            if self._active_response is not response or self._close_target is not None:
                return
            done = threading.Event()
            self._close_target = response
            self._close_done = done
        closer = threading.Thread(
            target=self._close_response,
            args=(response, done),
            daemon=True,
            name="analyst-ollama-close",
        )
        try:
            closer.start()
        except Exception:
            with self._active_lock:
                if self._close_target is response:
                    self._close_target = None
                    self._close_done = None

    @staticmethod
    def _close_response(response: Any, done: threading.Event) -> None:
        try:
            try:
                response.close()
            except Exception:
                pass
        finally:
            done.set()

    def _finish_response(self, response: Any) -> None:
        with self._active_lock:
            if self._close_target is response:
                done = self._close_done
                owns_close = False
            else:
                done = threading.Event()
                self._close_target = response
                self._close_done = done
                owns_close = True
        if owns_close:
            self._close_response(response, done)
        elif done is not None:
            done.wait()
        with self._active_lock:
            if self._active_response is response:
                self._active_response = None
            if self._close_target is response:
                self._close_target = None
                self._close_done = None


def _require_cancel_probe(cancel: CancelProbe) -> None:
    if not callable(cancel):
        raise TypeError("cancel probe must be callable")


def _matches_expected_identity(identity: OllamaIdentity) -> bool:
    try:
        return (
            type(identity.endpoint) is str
            and identity.endpoint == EXPECTED_IDENTITY.endpoint
            and type(identity.model_tag) is str
            and identity.model_tag == EXPECTED_IDENTITY.model_tag
            and type(identity.model_digest) is str
            and hmac.compare_digest(
                identity.model_digest, EXPECTED_IDENTITY.model_digest,
            )
        )
    except AttributeError:
        return False


def _require_bytes(value: object | None) -> bytes:
    if type(value) is not bytes:
        raise OllamaSafetyError(SafetyCode.INVALID_WIRE_JSON)
    return value


def _header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", None)
    if headers is None or not callable(getattr(headers, "get", None)):
        return ""
    value = headers.get(name, "")
    return value if type(value) is str else ""


def _identity_encoding(response: Any) -> bool:
    return _header(response, "content-encoding").strip().lower() in {"", "identity"}


def _content_type_is(response: Any, expected: str) -> bool:
    return _header(response, "content-type").split(";", 1)[0].strip().lower() == expected


def _resource_error(body: bytes) -> bool:
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    return len(text) <= 4096 and _RESOURCE_ERROR_RE.search(text) is not None


def _safety_status(exc: OllamaSafetyError) -> OllamaStatus:
    return (
        OllamaStatus.RESPONSE_LIMIT
        if exc.code in {
            SafetyCode.BODY_LIMIT,
            SafetyCode.FRAME_LIMIT,
            SafetyCode.CONTENT_LIMIT,
            SafetyCode.COMBINED_CHANNEL_LIMIT,
            SafetyCode.JSON_DEPTH_LIMIT,
            SafetyCode.JSON_NODE_LIMIT,
            SafetyCode.CANONICAL_JSON_LIMIT,
        }
        else OllamaStatus.PROTOCOL_VIOLATION
    )


__all__ = ["CancelProbe", "OllamaClient"]
