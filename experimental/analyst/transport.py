"""Bounded, cancellable HTTP shared by every Analyst backend.

Extracted verbatim from ``ollama_client`` so both transports run one
implementation rather than two drifting copies. Contract section 11 allows one
in-flight request per process, and ``GLOBAL_REQUEST_SLOT`` below is that bound:
it lives here, not in a backend, so a second backend cannot escape it (N2b
review finding M2).

Timeouts are class attributes so a backend can set its own without moving the
frozen Ollama values (review finding M1).

Subclasses supply two hooks:

``_classify_http_status``  map an HTTP response to a closed status, or None
``_read_response``         read one accepted response body

Everything else -- the worker thread, the cancel probes, the deadlines, the
nonblocking close -- is identical for every backend.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests
import urllib3

from .ollama_contract import (
    CONNECT_TIMEOUT_SECONDS,
    IDLE_READ_TIMEOUT_SECONDS,
    MAX_BODY_BYTES,
    TOTAL_REQUEST_SECONDS,
    OllamaStatus,
)
from .ollama_protocol import OllamaSafetyError, SafetyCode

_READ_CHUNK_BYTES = 64 * 1024
_CALLER_POLL_SECONDS = 0.01
_RESOURCE_ERROR_RE = re.compile(
    r"(?:out of memory|insufficient memory|not enough memory|memory allocation|"
    r"cuda[^\n]{0,80}memory|resource exhausted)",
    re.IGNORECASE,
)

TIMEOUT_EXCEPTIONS = (
    requests.exceptions.Timeout,
    urllib3.exceptions.TimeoutError,
)
TRANSPORT_EXCEPTIONS = (
    requests.exceptions.RequestException,
    urllib3.exceptions.HTTPError,
    OSError,
)

#: Contract section 11: one in-flight request per process, for every backend.
#: Never a cross-machine mechanism, and must not be described as one.
GLOBAL_REQUEST_SLOT = threading.BoundedSemaphore(1)

CancelProbe = Callable[[], bool]
CallerPoll = Callable[[], None] | None


@dataclass(frozen=True, slots=True)
class HttpIntent:
    """One bounded request the transport may perform."""

    method: str
    url: str
    body: bytes | None
    accept: str
    kind: str
    model_tag: str | None = None
    read_response: bool = False


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


class BoundedHttpClient:
    """One serial, caller-bounded HTTP client."""

    #: Per-backend deadlines. A subclass overrides these; the Ollama values are
    #: frozen by contract 12.1 and must not move.
    CONNECT_TIMEOUT: float = CONNECT_TIMEOUT_SECONDS
    IDLE_READ_TIMEOUT: float = IDLE_READ_TIMEOUT_SECONDS
    TOTAL_REQUEST_TIMEOUT: float = TOTAL_REQUEST_SECONDS

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

    # ---- hooks a backend supplies -------------------------------------

    def _classify_http_status(
        self, response: Any, intent: HttpIntent, cancel: CancelProbe, started: float,
    ) -> OllamaStatus | None:
        raise NotImplementedError

    def _read_response(
        self, response: Any, intent: HttpIntent, cancel: CancelProbe, started: float,
    ) -> object:
        """Read one accepted response. The default reads the whole body."""
        return self._read_all(response, cancel, started)

    def cancel_current(self) -> None:
        """Initiate one nonblocking close for only this client's active response."""
        with self._active_lock:
            response = self._active_response
        if response is not None:
            self._initiate_close(response)

    def _execute(
        self,
        intent: HttpIntent,
        cancel: CancelProbe,
        poll: CallerPoll | None,
    ) -> tuple[object | None, OllamaStatus | None]:
        if cancel():
            return None, OllamaStatus.CANCELLED_UNVERIFIED
        if not GLOBAL_REQUEST_SLOT.acquire(blocking=False):
            return None, (
                OllamaStatus.CANCELLED_UNVERIFIED
                if cancel() else OllamaStatus.TRANSPORT_UNAVAILABLE
            )
        if cancel():
            GLOBAL_REQUEST_SLOT.release()
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
            GLOBAL_REQUEST_SLOT.release()
            raise
        return self._await_worker(state, cancel, started, poll)

    def _request_worker(
        self,
        intent: HttpIntent,
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
        except TIMEOUT_EXCEPTIONS:
            state.publish_status(
                OllamaStatus.CANCELLED_UNVERIFIED
                if cancel() else OllamaStatus.REQUEST_TIMEOUT
            )
        except TRANSPORT_EXCEPTIONS:
            state.publish_status(
                OllamaStatus.CANCELLED_UNVERIFIED
                if cancel() else OllamaStatus.TRANSPORT_UNAVAILABLE
            )
        except OllamaSafetyError as exc:
            state.publish_status(safety_status(exc))
        except Exception:
            state.publish_status(OllamaStatus.PROTOCOL_VIOLATION)
        finally:
            GLOBAL_REQUEST_SLOT.release()
            state.done.set()

    def _await_worker(
        self,
        state: _WorkerState,
        cancel: CancelProbe,
        started: float,
        poll: CallerPoll | None,
    ) -> tuple[object | None, OllamaStatus | None]:
        deadline = started + self.TOTAL_REQUEST_TIMEOUT
        while True:
            try:
                run_caller_poll(poll)
            except BaseException:
                self._abandon_worker(state)
                raise
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
        intent: HttpIntent,
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
                timeout=(self.CONNECT_TIMEOUT, self.IDLE_READ_TIMEOUT),
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
            if not identity_encoding(response):
                return None, OllamaStatus.PROTOCOL_VIOLATION
            if not content_type_is(response, intent.accept):
                return None, OllamaStatus.PROTOCOL_VIOLATION
            return self._read_response(response, intent, cancel, started), None
        finally:
            if response is not None:
                self._finish_response(response)

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
            return b"", safety_status(exc)
        except TIMEOUT_EXCEPTIONS:
            return b"", (
                OllamaStatus.CANCELLED_UNVERIFIED
                if cancel() else OllamaStatus.REQUEST_TIMEOUT
            )
        except TRANSPORT_EXCEPTIONS:
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
            if self._monotonic() - started >= self.TOTAL_REQUEST_TIMEOUT:
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

def require_cancel_probe(cancel: CancelProbe) -> None:
    if not callable(cancel):
        raise TypeError("cancel probe must be callable")


def require_caller_poll(poll: CallerPoll | None) -> None:
    if poll is not None and not callable(poll):
        raise TypeError("caller poll hook must be callable")


def run_caller_poll(poll: CallerPoll | None) -> None:
    if poll is not None:
        poll()


def require_bytes(value: object | None) -> bytes:
    if type(value) is not bytes:
        raise OllamaSafetyError(SafetyCode.INVALID_WIRE_JSON)
    return value


def header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", None)
    if headers is None or not callable(getattr(headers, "get", None)):
        return ""
    value = headers.get(name, "")
    return value if type(value) is str else ""


def identity_encoding(response: Any) -> bool:
    return header(response, "content-encoding").strip().lower() in {"", "identity"}


def content_type_is(response: Any, expected: str) -> bool:
    return header(response, "content-type").split(";", 1)[0].strip().lower() == expected


def resource_error(body: bytes) -> bool:
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    return len(text) <= 4096 and _RESOURCE_ERROR_RE.search(text) is not None


def safety_status(exc: OllamaSafetyError) -> OllamaStatus:
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


__all__ = [
    "BoundedHttpClient",
    "TIMEOUT_EXCEPTIONS",
    "TRANSPORT_EXCEPTIONS",
    "CallerPoll",
    "CancelProbe",
    "GLOBAL_REQUEST_SLOT",
    "HttpIntent",
    "content_type_is",
    "header",
    "identity_encoding",
    "require_bytes",
    "require_cancel_probe",
    "require_caller_poll",
    "resource_error",
    "run_caller_poll",
    "safety_status",
]
