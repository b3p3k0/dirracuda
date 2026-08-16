"""Offline transport tests for the loopback-only C9 Ollama client."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from typing import Any

import pytest
import requests

from experimental.analyst.ollama_client import OllamaClient
from experimental.analyst.ollama_contract import (
    CONNECT_TIMEOUT_SECONDS,
    EXPECTED_IDENTITY,
    IDLE_READ_TIMEOUT_SECONDS,
    MAX_BODY_BYTES,
    ChatRequest,
    OllamaIdentity,
    MODEL_DIGEST,
    MODEL_TAG,
    OLLAMA_CHAT_URL,
    OLLAMA_TAGS_URL,
    OLLAMA_VERSION_URL,
    OllamaStatus,
    QUALIFIED_OLLAMA_VERSION,
    TOTAL_REQUEST_SECONDS,
    build_chat_request,
)


_NONCE = "FENCE_0123456789ABCDEF"
_VALID_ANSWER = json.dumps(
    {
        "document_type": "Public note",
        "subject": "Synthetic",
        "assessment": "no_findings",
        "findings": [],
    },
    separators=(",", ":"),
)


def _encoded(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


def _chat_wire(
    content: str = _VALID_ANSWER,
    *,
    done_reason: str = "stop",
    model: str = MODEL_TAG,
) -> bytes:
    return _encoded({
        "model": model,
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": done_reason,
        "total_duration": 100,
        "load_duration": 10,
        "prompt_eval_count": 5,
        "prompt_eval_duration": 20,
        "eval_count": 3,
        "eval_duration": 30,
    })


class FakeRaw:
    def __init__(self, chunks: Iterable[object]) -> None:
        self.chunks = list(chunks)
        self.calls: list[tuple[int, bool]] = []

    def stream(self, *, amt: int, decode_content: bool):
        self.calls.append((amt, decode_content))
        yield from self.chunks


class BlockingRaw:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.released = threading.Event()

    def stream(self, *, amt: int, decode_content: bool):
        self.entered.set()
        self.released.wait(timeout=5)
        if False:  # retain generator shape
            yield b""


class FakeResponse:
    def __init__(
        self,
        chunks: Iterable[object] = (),
        *,
        status: object = 200,
        content_type: str = "application/x-ndjson",
        content_encoding: str = "identity",
        raw: object | None = None,
    ) -> None:
        self.status_code = status
        self.headers = {
            "content-type": content_type,
            "content-encoding": content_encoding,
        }
        self.raw = FakeRaw(chunks) if raw is None else raw
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1
        released = getattr(self.raw, "released", None)
        if isinstance(released, threading.Event):
            released.set()


class FakeSession:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self.trust_env: object = True
        self.max_redirects: object = 30

    def request(self, method: str, url: str, **kwargs: object) -> object:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _client_with_chat_response(response: FakeResponse) -> tuple[OllamaClient, FakeSession]:
    session = FakeSession(response)
    return OllamaClient(session=session), session


def _chat(client: OllamaClient, *, cancel=lambda: False):
    request = build_chat_request("public", nonce=_NONCE)
    return client.chat(
        request, expected_sha256=request.request_sha256, cancel=cancel,
    )


def test_client_forces_no_ambient_proxy_and_redirect_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:9999")
    session = FakeSession()
    OllamaClient(session=session)
    assert session.trust_env is False
    assert session.max_redirects == 0
    assert os.environ["HTTP_PROXY"].endswith(":9999")


def test_chat_sends_exact_frozen_body_and_transport_controls() -> None:
    response = FakeResponse([_chat_wire()])
    client, session = _client_with_chat_response(response)
    request = build_chat_request("public", nonce=_NONCE)
    result = client.chat(
        request, expected_sha256=request.request_sha256, cancel=lambda: False,
    )

    assert result.status is OllamaStatus.SUCCESS
    assert result.content == _VALID_ANSWER
    assert len(session.calls) == 1
    call = session.calls[0]
    assert (call["method"], call["url"], call["data"]) == (
        "POST", OLLAMA_CHAT_URL, request.body,
    )
    assert call["stream"] is True
    assert call["timeout"] == (CONNECT_TIMEOUT_SECONDS, IDLE_READ_TIMEOUT_SECONDS)
    assert call["allow_redirects"] is False
    assert call["proxies"] == {"http": None, "https": None}
    assert call["headers"] == {
        "Accept": "application/x-ndjson",
        "Accept-Encoding": "identity",
        "Content-Type": "application/json",
    }
    assert response.raw.calls == [(64 * 1024, False)]  # type: ignore[attr-defined]
    assert response.close_count == 1


def test_preflight_uses_only_exact_version_then_tags_gets() -> None:
    version = FakeResponse(
        [json.dumps({"version": QUALIFIED_OLLAMA_VERSION}).encode()],
        content_type="application/json",
    )
    tags = FakeResponse(
        [json.dumps({"models": [{
            "name": MODEL_TAG, "model": MODEL_TAG, "digest": MODEL_DIGEST,
        }]}).encode()],
        content_type="application/json",
    )
    session = FakeSession(version, tags)
    result = OllamaClient(session=session).preflight(
        EXPECTED_IDENTITY, cancel=lambda: False,
    )

    assert result.status is OllamaStatus.SUCCESS
    assert result.observed_version == QUALIFIED_OLLAMA_VERSION
    assert result.model_digest == MODEL_DIGEST
    assert [(call["method"], call["url"], call["data"]) for call in session.calls] == [
        ("GET", OLLAMA_VERSION_URL, None),
        ("GET", OLLAMA_TAGS_URL, None),
    ]
    assert all(call["allow_redirects"] is False for call in session.calls)
    assert all(call["proxies"] == {"http": None, "https": None} for call in session.calls)
    assert version.close_count == tags.close_count == 1


@pytest.mark.parametrize(
    ("version", "digest", "expected"),
    [
        ("0.32.6", MODEL_DIGEST, OllamaStatus.IDENTITY_MISMATCH),
        (QUALIFIED_OLLAMA_VERSION, "1" * 64, OllamaStatus.IDENTITY_MISMATCH),
    ],
)
def test_preflight_fails_closed_on_version_or_digest_drift(
    version: str, digest: str, expected: OllamaStatus,
) -> None:
    responses = (
        FakeResponse(
            [json.dumps({"version": version}).encode()],
            content_type="application/json",
        ),
        FakeResponse(
            [json.dumps({"models": [{
                "name": MODEL_TAG, "model": MODEL_TAG, "digest": digest,
            }]}).encode()],
            content_type="application/json",
        ),
    )
    result = OllamaClient(session=FakeSession(*responses)).preflight(
        cancel=lambda: False,
    )
    assert result.status is expected
    assert result.observed_version is None
    assert result.model_digest is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("endpoint", type("EndpointSubclass", (str,), {})("http://127.0.0.1:11434")),
        ("model_tag", type("TagSubclass", (str,), {})(MODEL_TAG)),
        ("model_digest", type("DigestSubclass", (str,), {})(MODEL_DIGEST)),
        ("endpoint", "http://localhost:11434"),
        ("model_tag", "qwen3.6:27b-cloud"),
        ("model_digest", "1" * 64),
    ],
)
def test_forged_preflight_identity_is_rejected_with_zero_http(
    field: str, value: object,
) -> None:
    forged = object.__new__(OllamaIdentity)
    for name in ("endpoint", "model_tag", "model_digest"):
        object.__setattr__(
            forged, name, value if name == field else getattr(EXPECTED_IDENTITY, name),
        )
    session = FakeSession()
    result = OllamaClient(session=session).preflight(forged, cancel=lambda: False)
    assert result.status is OllamaStatus.IDENTITY_MISMATCH
    assert session.calls == []


def test_incomplete_forged_preflight_identity_is_rejected_with_zero_http() -> None:
    forged = object.__new__(OllamaIdentity)
    session = FakeSession()
    result = OllamaClient(session=session).preflight(forged, cancel=lambda: False)
    assert result.status is OllamaStatus.IDENTITY_MISMATCH
    assert session.calls == []


def test_preflight_body_limit_is_enforced_while_streaming_before_tags() -> None:
    oversized = FakeResponse(
        [b"x" * MAX_BODY_BYTES, b"x"], content_type="application/json",
    )
    session = FakeSession(oversized)
    result = OllamaClient(session=session).preflight(cancel=lambda: False)
    assert result.status is OllamaStatus.RESPONSE_LIMIT
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        # Depth 17 exceeds the frozen depth-16 decoded-object limit.
        b'{"version":' + b"[" * 16 + b"0" + b"]" * 16 + b"}",
        json.dumps({"version": QUALIFIED_OLLAMA_VERSION, "x": [0] * 4096}).encode(),
        json.dumps({"version": "x" * (256 * 1024)}).encode(),
    ],
    ids=("depth", "nodes", "canonical-bytes"),
)
def test_preflight_parsed_object_limits_are_response_limit(payload: bytes) -> None:
    response = FakeResponse([payload], content_type="application/json")
    result = OllamaClient(session=FakeSession(response)).preflight(cancel=lambda: False)
    assert result.status is OllamaStatus.RESPONSE_LIMIT
    assert result.observed_version is None


@pytest.mark.parametrize(
    "payload",
    [b"\xff", b'{"version":"0.32.5","version":"0.32.5"}', b"not-json"],
)
def test_preflight_malformed_wire_data_is_protocol_violation(payload: bytes) -> None:
    response = FakeResponse([payload], content_type="application/json")
    result = OllamaClient(session=FakeSession(response)).preflight(cancel=lambda: False)
    assert result.status is OllamaStatus.PROTOCOL_VIOLATION
    assert result.observed_version is None


@pytest.mark.parametrize(
    ("status", "content_type", "expected"),
    [
        (302, "application/json", OllamaStatus.PROTOCOL_VIOLATION),
        (200, "text/html", OllamaStatus.PROTOCOL_VIOLATION),
    ],
)
def test_preflight_redirect_or_content_type_failure_never_reaches_tags(
    status: int, content_type: str, expected: OllamaStatus,
) -> None:
    response = FakeResponse(
        [b'{"version":"0.32.5"}'], status=status, content_type=content_type,
    )
    session = FakeSession(response)
    result = OllamaClient(session=session).preflight(cancel=lambda: False)
    assert result.status is expected
    assert len(session.calls) == 1


def test_cancel_before_preflight_or_chat_makes_zero_http_requests() -> None:
    session = FakeSession()
    client = OllamaClient(session=session)
    assert client.preflight(cancel=lambda: True).status is (
        OllamaStatus.CANCELLED_UNVERIFIED
    )
    assert _chat(client, cancel=lambda: True).status is OllamaStatus.CANCELLED_UNVERIFIED
    assert session.calls == []


def test_chat_hash_or_request_identity_mismatch_makes_zero_http_requests() -> None:
    session = FakeSession()
    client = OllamaClient(session=session)
    request = build_chat_request("public", nonce=_NONCE)
    assert client.chat(
        request, expected_sha256="0" * 64, cancel=lambda: False,
    ).status is OllamaStatus.IDENTITY_MISMATCH

    object.__setattr__(request, "body", request.body + b" ")
    assert client.chat(
        request, expected_sha256=request.request_sha256, cancel=lambda: False,
    ).status is OllamaStatus.IDENTITY_MISMATCH
    assert session.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_text", type("TextSubclass", (str,), {})("public")),
        ("nonce", type("NonceSubclass", (str,), {})(_NONCE)),
        ("body", bytearray(b"{}")),
        ("request_sha256", type("HashSubclass", (str,), {})("0" * 64)),
        ("model_tag", type("TagSubclass", (str,), {})(MODEL_TAG)),
        ("model_digest", type("DigestSubclass", (str,), {})(MODEL_DIGEST)),
        ("endpoint", type("EndpointSubclass", (str,), {})("http://127.0.0.1:11434")),
    ],
)
def test_forged_request_exact_types_are_identity_mismatch_with_zero_http(
    field: str, value: object,
) -> None:
    valid = build_chat_request("public", nonce=_NONCE)
    forged = object.__new__(ChatRequest)
    for name in (
        "source_text", "nonce", "body", "request_sha256", "model_tag",
        "model_digest", "endpoint",
    ):
        object.__setattr__(forged, name, value if name == field else getattr(valid, name))
    session = FakeSession()
    result = OllamaClient(session=session).chat(
        forged, expected_sha256=valid.request_sha256, cancel=lambda: False,
    )
    assert result.status is OllamaStatus.IDENTITY_MISMATCH
    assert session.calls == []


def test_already_cancelled_wins_over_forged_identity_and_hash_with_zero_http() -> None:
    session = FakeSession()
    client = OllamaClient(session=session)
    request = build_chat_request("public", nonce=_NONCE)
    object.__setattr__(request, "body", request.body + b" ")
    chat = client.chat(
        request, expected_sha256="0" * 64, cancel=lambda: True,
    )

    identity = object.__new__(OllamaIdentity)
    object.__setattr__(identity, "endpoint", "http://localhost:11434")
    object.__setattr__(identity, "model_tag", "qwen3.6:27b-cloud")
    object.__setattr__(identity, "model_digest", "1" * 64)
    preflight = client.preflight(identity, cancel=lambda: True)

    assert chat.status is OllamaStatus.CANCELLED_UNVERIFIED
    assert preflight.status is OllamaStatus.CANCELLED_UNVERIFIED
    assert session.calls == []


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (429, b"", OllamaStatus.RESOURCE_BUSY),
        (503, b"", OllamaStatus.RESOURCE_BUSY),
        (400, b"CUDA out of memory", OllamaStatus.RESOURCE_BUSY),
        (400, b"bad request", OllamaStatus.PROTOCOL_VIOLATION),
        (404, b"missing", OllamaStatus.IDENTITY_MISMATCH),
        (500, b"", OllamaStatus.TRANSPORT_UNAVAILABLE),
        (500, b"resource exhausted", OllamaStatus.RESOURCE_BUSY),
        (302, b"", OllamaStatus.PROTOCOL_VIOLATION),
        (204, b"", OllamaStatus.PROTOCOL_VIOLATION),
        (True, b"", OllamaStatus.PROTOCOL_VIOLATION),
    ],
)
def test_http_status_classification_is_closed(
    status: object, body: bytes, expected: OllamaStatus,
) -> None:
    response = FakeResponse([body], status=status)
    result = _chat(_client_with_chat_response(response)[0])
    assert result.status is expected
    assert result.content is None


@pytest.mark.parametrize(
    ("encoding", "content_type"),
    [
        ("gzip", "application/x-ndjson"),
        ("identity", "application/json"),
        ("identity", "text/plain"),
        ("identity", ""),
    ],
)
def test_chat_rejects_compression_or_wrong_content_type(
    encoding: str, content_type: str,
) -> None:
    response = FakeResponse(
        [_chat_wire()], content_encoding=encoding, content_type=content_type,
    )
    result = _chat(_client_with_chat_response(response)[0])
    assert result.status is OllamaStatus.PROTOCOL_VIOLATION
    assert result.content is None


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        ([b"x" * MAX_BODY_BYTES, b"x"], OllamaStatus.RESPONSE_LIMIT),
        ([b"not-json\n"], OllamaStatus.PROTOCOL_VIOLATION),
        ([_chat_wire(model="other")], OllamaStatus.IDENTITY_MISMATCH),
        ([_encoded({"error": "CUDA memory allocation failed"})], OllamaStatus.RESOURCE_BUSY),
        ([_encoded({"error": "runner vanished"})], OllamaStatus.TRANSPORT_UNAVAILABLE),
        (["not bytes"], OllamaStatus.PROTOCOL_VIOLATION),
    ],
)
def test_chat_stream_failure_classification_discards_all_content(
    chunks: list[object], expected: OllamaStatus,
) -> None:
    result = _chat(_client_with_chat_response(FakeResponse(chunks))[0])
    assert result.status is expected
    assert result.content is None


def test_length_or_schema_invalid_response_never_retains_raw_output() -> None:
    length = _chat(_client_with_chat_response(FakeResponse([
        _chat_wire("raw-private-shape", done_reason="length"),
    ]))[0])
    invalid = _chat(_client_with_chat_response(FakeResponse([
        _chat_wire("raw-private-shape", done_reason="stop"),
    ]))[0])
    for result in (length, invalid):
        assert result.status is OllamaStatus.MODEL_INVALID
        assert result.content is None
        assert "raw-private-shape" not in repr(result)


def test_connection_exception_is_transport_unavailable_and_content_free() -> None:
    session = FakeSession(requests.ConnectionError("SECRET_HOST_DETAIL"))
    result = _chat(OllamaClient(session=session))
    assert result.status is OllamaStatus.TRANSPORT_UNAVAILABLE
    assert "SECRET_HOST_DETAIL" not in repr(result)


@pytest.mark.parametrize("error", [requests.ConnectTimeout(), requests.ReadTimeout()])
def test_chat_connect_or_read_timeout_is_request_timeout(error: BaseException) -> None:
    if isinstance(error, requests.ConnectTimeout):
        session = FakeSession(error)
    else:
        class RaisingRaw:
            def stream(self, *, amt: int, decode_content: bool):
                raise error
                yield b""  # pragma: no cover

        session = FakeSession(FakeResponse(raw=RaisingRaw()))
    result = _chat(OllamaClient(session=session))
    assert result.status is OllamaStatus.REQUEST_TIMEOUT
    assert result.content is None


@pytest.mark.parametrize("error", [requests.ConnectTimeout(), requests.ReadTimeout()])
def test_preflight_connect_or_body_read_timeout_is_request_timeout(
    error: BaseException,
) -> None:
    if isinstance(error, requests.ConnectTimeout):
        session = FakeSession(error)
    else:
        class RaisingRaw:
            def stream(self, *, amt: int, decode_content: bool):
                raise error
                yield b""  # pragma: no cover

        session = FakeSession(FakeResponse(
            raw=RaisingRaw(), content_type="application/json",
        ))
    result = OllamaClient(session=session).preflight(cancel=lambda: False)
    assert result.status is OllamaStatus.REQUEST_TIMEOUT
    assert result.observed_version is None


@pytest.mark.parametrize("operation", ["chat", "preflight"])
def test_cancellation_wins_over_simultaneous_timeout(operation: str) -> None:
    cancelled = threading.Event()

    class CancellingSession(FakeSession):
        def request(self, method: str, url: str, **kwargs: object) -> object:
            self.calls.append({"method": method, "url": url, **kwargs})
            cancelled.set()
            raise requests.ReadTimeout("PUBLIC_SIMULTANEOUS_TIMEOUT")

    client = OllamaClient(session=CancellingSession())
    result = (
        _chat(client, cancel=cancelled.is_set)
        if operation == "chat"
        else client.preflight(cancel=cancelled.is_set)
    )
    assert result.status is OllamaStatus.CANCELLED_UNVERIFIED


def test_cancel_during_blocking_stream_returns_promptly_and_closes_exact_response_once() -> None:
    raw = BlockingRaw()
    response = FakeResponse(raw=raw)
    client, _session = _client_with_chat_response(response)
    cancelled = threading.Event()
    result: list[object] = []
    thread = threading.Thread(
        target=lambda: result.append(_chat(client, cancel=cancelled.is_set)),
    )
    thread.start()
    assert raw.entered.wait(timeout=2)
    cancelled.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result[0].status is OllamaStatus.CANCELLED_UNVERIFIED  # type: ignore[attr-defined]
    raw.released.set()
    deadline = time.monotonic() + 2
    while response.close_count != 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert response.close_count == 1


def test_client_checks_cancel_between_coalesced_frames_before_decoding_second() -> None:
    response = FakeResponse([
        _encoded({
            "model": MODEL_TAG,
            "message": {"role": "assistant", "content": "partial"},
            "done": False,
        }) + b"malformed-second-frame\n",
    ])
    client, _session = _client_with_chat_response(response)
    client._monotonic = lambda: 0.0

    class CancelOnSecondFrame:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> bool:
            self.calls += 1
            return self.calls >= 3

    # Directly exercise the client/protocol seam: one wire-chunk poll followed by
    # one callback per coalesced frame. The second frame must never be decoded.
    cancel = CancelOnSecondFrame()
    result = client._read_chat(response, cancel, started=0.0)
    assert result.status is OllamaStatus.CANCELLED_UNVERIFIED
    assert result.content is None
    assert cancel.calls >= 3


def test_response_close_exception_is_swallowed_without_changing_valid_result() -> None:
    class ThrowingCloseResponse(FakeResponse):
        def close(self) -> None:
            self.close_count += 1
            raise OSError("PUBLIC_CLOSE_FAILURE")

    response = ThrowingCloseResponse([_chat_wire()])
    result = _chat(_client_with_chat_response(response)[0])
    assert result.status is OllamaStatus.SUCCESS
    assert response.close_count == 1


def test_global_request_permit_refuses_second_client_without_second_http_call() -> None:
    raw = BlockingRaw()
    first_response = FakeResponse(raw=raw)
    first_client, first_session = _client_with_chat_response(first_response)
    first_cancel = threading.Event()
    first_result: list[object] = []
    first_thread = threading.Thread(
        target=lambda: first_result.append(_chat(first_client, cancel=first_cancel.is_set)),
    )
    first_thread.start()
    assert raw.entered.wait(timeout=2)

    second_response = FakeResponse([_chat_wire()])
    second_client, second_session = _client_with_chat_response(second_response)
    second = _chat(second_client)
    assert second.status is OllamaStatus.TRANSPORT_UNAVAILABLE
    assert second_session.calls == []

    first_cancel.set()
    first_thread.join(timeout=2)
    raw.released.set()
    assert not first_thread.is_alive()
    assert len(first_session.calls) == 1

    deadline = time.monotonic() + 2
    while first_response.close_count != 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    following = _chat(second_client)
    assert following.status is OllamaStatus.SUCCESS
    assert len(second_session.calls) == 1


def test_blocked_close_holds_global_permit_until_close_and_worker_exit() -> None:
    class BlockingCloseResponse(FakeResponse):
        def __init__(self) -> None:
            super().__init__(raw=BlockingRaw())
            self.close_entered = threading.Event()
            self.allow_close = threading.Event()

        def close(self) -> None:
            self.close_count += 1
            self.close_entered.set()
            self.allow_close.wait(timeout=5)
            self.raw.released.set()  # type: ignore[attr-defined]

    first_response = BlockingCloseResponse()
    first_client, first_session = _client_with_chat_response(first_response)
    cancelled = threading.Event()
    first_result: list[object] = []
    first_thread = threading.Thread(
        target=lambda: first_result.append(_chat(first_client, cancel=cancelled.is_set)),
    )
    first_thread.start()
    assert first_response.raw.entered.wait(timeout=2)  # type: ignore[attr-defined]
    cancelled.set()
    assert first_response.close_entered.wait(timeout=2)
    first_thread.join(timeout=2)
    assert not first_thread.is_alive(), "caller waited for blocked response.close()"
    assert first_result[0].status is OllamaStatus.CANCELLED_UNVERIFIED  # type: ignore[attr-defined]

    second_response = FakeResponse([_chat_wire()])
    second_client, second_session = _client_with_chat_response(second_response)
    blocked = _chat(second_client)
    assert blocked.status is OllamaStatus.TRANSPORT_UNAVAILABLE
    assert second_session.calls == []
    assert first_response.close_count == 1

    first_response.allow_close.set()
    deadline = time.monotonic() + 2
    following = None
    while time.monotonic() < deadline:
        following = _chat(second_client)
        if following.status is OllamaStatus.SUCCESS:
            break
        assert following.status is OllamaStatus.TRANSPORT_UNAVAILABLE
        assert second_session.calls == []
        time.sleep(0.01)
    assert following is not None and following.status is OllamaStatus.SUCCESS
    assert len(second_session.calls) == 1
    assert len(first_session.calls) == 1
    assert first_response.close_count == 1


def test_cancel_while_session_request_is_blocked_closes_late_response_before_permit_recovers(
) -> None:
    late_response = FakeResponse([_chat_wire()])

    class PreHeaderBlockingSession(FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.entered = threading.Event()
            self.release_headers = threading.Event()

        def request(self, method: str, url: str, **kwargs: object) -> object:
            self.calls.append({"method": method, "url": url, **kwargs})
            self.entered.set()
            self.release_headers.wait(timeout=5)
            return late_response

    first_session = PreHeaderBlockingSession()
    first_client = OllamaClient(session=first_session)
    cancelled = threading.Event()
    first_result: list[object] = []
    first_thread = threading.Thread(
        target=lambda: first_result.append(_chat(first_client, cancel=cancelled.is_set)),
    )
    first_thread.start()
    assert first_session.entered.wait(timeout=2)

    cancelled.set()
    first_thread.join(timeout=2)
    assert not first_thread.is_alive(), "caller waited for blocked session.request()"
    assert first_result[0].status is OllamaStatus.CANCELLED_UNVERIFIED  # type: ignore[attr-defined]
    assert late_response.close_count == 0

    second_response = FakeResponse([_chat_wire()])
    second_client, second_session = _client_with_chat_response(second_response)
    blocked = _chat(second_client)
    assert blocked.status is OllamaStatus.TRANSPORT_UNAVAILABLE
    assert second_session.calls == []

    first_session.release_headers.set()
    deadline = time.monotonic() + 2
    following = None
    while time.monotonic() < deadline:
        following = _chat(second_client)
        if following.status is OllamaStatus.SUCCESS:
            break
        assert following.status is OllamaStatus.TRANSPORT_UNAVAILABLE
        assert second_session.calls == []
        time.sleep(0.01)
    assert following is not None and following.status is OllamaStatus.SUCCESS
    assert len(first_session.calls) == 1
    assert len(second_session.calls) == 1
    assert late_response.close_count == 1


def test_total_deadline_returns_request_timeout_and_closes_active_response() -> None:
    raw = BlockingRaw()
    response = FakeResponse(raw=raw)
    active = threading.Event()

    class Clock:
        def __init__(self) -> None:
            self.expired = False

        def __call__(self) -> float:
            return TOTAL_REQUEST_SECONDS + 1 if self.expired else 0.0

    clock = Clock()
    client, _session = _client_with_chat_response(response)
    client._monotonic = clock  # Inject after helper construction without live time.
    result: list[object] = []
    thread = threading.Thread(target=lambda: result.append(_chat(client)))
    thread.start()
    assert raw.entered.wait(timeout=2)
    active.set()
    clock.expired = True
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result[0].status is OllamaStatus.REQUEST_TIMEOUT  # type: ignore[attr-defined]
    raw.released.set()


def test_exact_total_deadline_boundary_times_out() -> None:
    raw = BlockingRaw()
    response = FakeResponse(raw=raw)

    class Clock:
        expired = False

        def __call__(self) -> float:
            return TOTAL_REQUEST_SECONDS if self.expired else 0.0

    clock = Clock()
    client, _session = _client_with_chat_response(response)
    client._monotonic = clock
    result: list[object] = []
    thread = threading.Thread(target=lambda: result.append(_chat(client)))
    thread.start()
    assert raw.entered.wait(timeout=2)
    clock.expired = True
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result[0].status is OllamaStatus.REQUEST_TIMEOUT  # type: ignore[attr-defined]
    raw.released.set()


@pytest.mark.parametrize("cancel", [None, True, 1])
def test_cancel_probe_must_be_callable(cancel: object) -> None:
    client = OllamaClient(session=FakeSession())
    with pytest.raises(TypeError, match="cancel probe"):
        client.preflight(cancel=cancel)  # type: ignore[arg-type]
    request = build_chat_request("public", nonce=_NONCE)
    with pytest.raises(TypeError, match="cancel probe"):
        client.chat(
            request, expected_sha256=request.request_sha256,
            cancel=cancel,  # type: ignore[arg-type]
        )


def test_constructor_requires_callable_clock() -> None:
    with pytest.raises(TypeError, match="clock"):
        OllamaClient(session=FakeSession(), monotonic=1)  # type: ignore[arg-type]


def test_client_module_import_constructs_no_session_or_socket() -> None:
    repo = Path(__file__).resolve().parents[2]
    script = """
import requests
import socket
def forbidden(*args, **kwargs):
    raise AssertionError('import performed network setup')
requests.Session = forbidden
socket.socket = forbidden
socket.create_connection = forbidden
import experimental.analyst.ollama_client
print('clean')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "clean"
