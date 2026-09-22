"""Contract 12.1: a loopback Ollama run must stay byte-identical.

These tests pin exactly what the Ollama client puts on the wire. They exist so
the shared-transport extraction in N2b stage C cannot change the local path
without failing loudly.
"""

from __future__ import annotations

import json

import pytest

from experimental.analyst import ollama_contract as contract
from experimental.analyst.ollama_client import OllamaClient
from experimental.analyst.ollama_contract import (
    OllamaStatus,
    build_chat_request,
    build_read_chat_request,
)

_NONCE = "FENCE_0123456789ABCDEF"


class _Recorded(Exception):
    """Raised to stop after the request shape is captured."""


class _RecordingSession:
    """Captures one request exactly, then aborts before any I/O."""

    trust_env = True
    max_redirects = 30

    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        raise _Recorded()


def _capture(call):
    session = _RecordingSession()
    client = OllamaClient(session=session)
    try:
        call(client)
    except Exception:
        pass
    assert session.calls, "no request was attempted"
    return session.calls[0], session, client


def _shape(recorded):
    """Return the wire shape, with the body hashed rather than embedded."""
    body = recorded.get("data")
    return {
        "method": recorded["method"],
        "url": recorded["url"],
        "stream": recorded.get("stream"),
        "timeout": recorded.get("timeout"),
        "allow_redirects": recorded.get("allow_redirects"),
        "proxies": recorded.get("proxies"),
        "headers": recorded.get("headers"),
        "body_is_none": body is None,
    }


# --------------------------------------------------------------------------
# The session is disarmed the same way for every backend
# --------------------------------------------------------------------------

def test_the_client_disarms_ambient_environment_on_its_session():
    _, session, _ = _capture(lambda c: c.list_models())
    assert session.trust_env is False
    assert session.max_redirects == 0


# --------------------------------------------------------------------------
# Exact request shapes
# --------------------------------------------------------------------------

def test_discovery_request_shape_is_frozen():
    recorded, _, _ = _capture(lambda c: c.list_models())
    assert _shape(recorded) == {
        "method": "GET",
        "url": "http://127.0.0.1:11434/api/tags",
        "stream": True,
        "timeout": (10.0, 180.0),
        "allow_redirects": False,
        "proxies": {"http": None, "https": None},
        "headers": {"Accept": "application/json", "Accept-Encoding": "identity"},
        "body_is_none": True,
    }


def test_version_request_shape_is_frozen():
    recorded, _, _ = _capture(
        lambda c: c.check_version(cancel=lambda: False)
    )
    assert _shape(recorded) == {
        "method": "GET",
        "url": "http://127.0.0.1:11434/api/version",
        "stream": True,
        "timeout": (10.0, 180.0),
        "allow_redirects": False,
        "proxies": {"http": None, "https": None},
        "headers": {"Accept": "application/json", "Accept-Encoding": "identity"},
        "body_is_none": True,
    }


def test_chat_request_shape_and_body_are_frozen():
    request = build_chat_request("public sample", nonce=_NONCE)
    recorded, _, _ = _capture(
        lambda c: c.chat(
            request, expected_sha256=request.request_sha256, cancel=lambda: False,
        )
    )
    assert _shape(recorded) == {
        "method": "POST",
        "url": "http://127.0.0.1:11434/api/chat",
        "stream": True,
        "timeout": (10.0, 180.0),
        "allow_redirects": False,
        "proxies": {"http": None, "https": None},
        "headers": {
            "Accept": "application/x-ndjson",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
        },
        "body_is_none": False,
    }
    assert recorded["data"] == request.body


def test_the_chat_body_is_exactly_the_frozen_payload():
    """The bytes on the wire are the request's own canonical body."""
    request = build_chat_request("public sample", nonce=_NONCE)
    payload = json.loads(request.body)
    assert set(payload) == {
        "model", "messages", "stream", "format", "options", "think", "keep_alive",
    }
    assert payload["stream"] is True
    assert payload["think"] is False
    assert payload["keep_alive"] == contract.KEEP_ALIVE
    assert payload["options"] == contract.GENERATION_OPTIONS.as_payload()


def test_the_read_chat_body_uses_the_read_profile():
    request = build_read_chat_request("public sample", nonce=_NONCE)
    payload = json.loads(request.body)
    assert payload["options"] == contract.READ_GENERATION_OPTIONS.as_payload()
    assert payload["options"]["num_ctx"] == contract.READ_NUM_CTX


# --------------------------------------------------------------------------
# The bounds themselves
# --------------------------------------------------------------------------

def test_the_shared_timeouts_are_unchanged():
    """Review finding M1: giving llama.cpp its own timeouts must not move these."""
    assert contract.CONNECT_TIMEOUT_SECONDS == 10.0
    assert contract.IDLE_READ_TIMEOUT_SECONDS == 180.0
    assert contract.TOTAL_REQUEST_SECONDS == 600.0


def test_one_request_may_be_in_flight_per_process():
    """Contract 11, review finding M2. Whatever module owns the slot, there is
    exactly one and a second caller is refused rather than queued."""
    from experimental.analyst import ollama_client

    slot = ollama_client._GLOBAL_REQUEST_SLOT
    assert slot.acquire(blocking=False) is True
    try:
        session = _RecordingSession()
        client = OllamaClient(session=session)
        result = client.chat(
            build_chat_request("public", nonce=_NONCE),
            expected_sha256=build_chat_request(
                "public", nonce=_NONCE
            ).request_sha256,
            cancel=lambda: False,
        )
        assert result.status is OllamaStatus.TRANSPORT_UNAVAILABLE
        assert session.calls == []
    finally:
        slot.release()
