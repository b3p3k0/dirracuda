from __future__ import annotations

import json
import threading
import time
from dataclasses import replace

import pytest
import requests

from experimental.analyst.ollama_contract import (
    ChatMetrics,
    ChatResult,
    EXPECTED_IDENTITY,
    OllamaStatus,
    PreflightResult,
    build_chat_request,
    new_prompt_nonce,
)
from scripts import analyst_c9_live_acceptance as live


def _success_result(content: str = "{}") -> ChatResult:
    return ChatResult(
        OllamaStatus.SUCCESS,
        content=content,
        metrics=ChatMetrics(
            done_reason="stop",
            prompt_eval_count=10,
            eval_count=2,
            total_duration_ns=100,
            load_duration_ns=10,
            prompt_eval_duration_ns=20,
            eval_duration_ns=30,
            raw_body_bytes=len(content.encode("utf-8")) + 100,
            content_bytes=len(content.encode("utf-8")),
            thinking_bytes=0,
        ),
    )


def test_no_confirmation_refuses_without_running(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        live,
        "run_live_acceptance",
        lambda: pytest.fail("live protocol ran without explicit confirmation"),
    )
    monkeypatch.setattr(
        requests.Session,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "HTTP contact occurred without explicit confirmation"
        ),
    )

    assert live.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "refusing live contact without --confirm-live\n"


def test_direct_api_requires_explicit_confirmation_before_contact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        requests.Session,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "HTTP contact occurred without explicit confirmation"
        ),
    )

    with pytest.raises(live.LiveAcceptanceError, match="live_confirmation_required"):
        live.run_live_acceptance(confirm_live=False)


def test_header_observer_arms_exactly_one_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status_code = 200
        headers = {
            "content-type": "application/x-ndjson",
            "content-encoding": "identity",
        }

    response = Response()
    monkeypatch.setattr(
        requests.Session,
        "request",
        lambda self, method, url, **kwargs: response,
    )
    session = live._HeaderCancellationSession(monotonic=lambda: 12.5)
    event = threading.Event()
    session.arm(event)

    assert session.request("GET", live.OLLAMA_CHAT_URL) is response
    assert not event.is_set()
    assert session.header_seen_at() is None
    assert session.request("POST", live.OLLAMA_CHAT_URL) is response
    assert event.is_set()
    assert session.header_seen_at() == 12.5


def test_real_session_tracker_records_exact_five_contact_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status_code = 200
        headers = {
            "content-type": "application/x-ndjson",
            "content-encoding": "identity",
        }

    monkeypatch.setattr(
        requests.Session,
        "request",
        lambda self, method, url, **kwargs: Response(),
    )
    session = live._HeaderCancellationSession(monotonic=lambda: 12.5)

    for method, url in (
        ("GET", live.OLLAMA_VERSION_URL),
        ("GET", live.OLLAMA_TAGS_URL),
        ("POST", live.OLLAMA_CHAT_URL),
        ("POST", live.OLLAMA_CHAT_URL),
        ("POST", live.OLLAMA_CHAT_URL),
    ):
        session.request(method, url)

    assert session.contact_order() == live._EXPECTED_CONTACT_ORDER
    session.request("POST", live.OLLAMA_CHAT_URL)
    assert session.contact_order() == live._EXPECTED_CONTACT_ORDER + (
        "health_chat",
    )


@pytest.mark.parametrize(
    ("status", "content_type", "encoding"),
    [
        (503, "application/x-ndjson", "identity"),
        (302, "application/x-ndjson", "identity"),
        (200, "application/json", "identity"),
        (200, "application/x-ndjson", "gzip"),
    ],
)
def test_header_observer_never_cancels_an_unaccepted_response(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    content_type: str,
    encoding: str,
) -> None:
    class Response:
        status_code = status
        headers = {
            "content-type": content_type,
            "content-encoding": encoding,
        }

    monkeypatch.setattr(
        requests.Session,
        "request",
        lambda self, method, url, **kwargs: Response(),
    )
    session = live._HeaderCancellationSession(monotonic=lambda: 12.5)
    event = threading.Event()
    session.arm(event)

    session.request("POST", live.OLLAMA_CHAT_URL)

    assert not event.is_set()
    assert session.header_seen_at() is None


def test_header_observer_cancels_real_client_after_headers_and_closes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockingRaw:
        def stream(self, *, amt, decode_content):
            pytest.fail("cancelled response body was read")
            yield b""  # pragma: no cover - preserve generator shape

    class Response:
        status_code = 200
        headers = {
            "content-type": "application/x-ndjson",
            "content-encoding": "identity",
        }

        def __init__(self):
            self.raw = BlockingRaw()
            self.close_count = 0
            self.closed = threading.Event()

        def close(self):
            self.close_count += 1
            self.closed.set()

    response = Response()
    monkeypatch.setattr(
        requests.Session,
        "request",
        lambda self, method, url, **kwargs: response,
    )
    session = live._HeaderCancellationSession(monotonic=time.monotonic)
    client = live.OllamaClient(session=session, monotonic=time.monotonic)
    cancelled = threading.Event()
    session.arm(cancelled)
    source = "PUBLIC SYNTHETIC POST-HEADER CANCELLATION"
    request = build_chat_request(source, nonce=new_prompt_nonce(source))

    result = client.chat(
        request,
        expected_sha256=request.request_sha256,
        cancel=cancelled.is_set,
    )

    assert result.status is OllamaStatus.CANCELLED_UNVERIFIED
    assert session.header_seen_at() is not None
    assert response.closed.wait(timeout=2)
    assert response.close_count == 1


def test_public_protocol_returns_only_content_free_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession:
        def __init__(self, *, monotonic):
            self.monotonic = monotonic
            self.event = None
            self.seen = None

        def arm(self, event):
            self.event = event

        def header_seen_at(self):
            return self.seen

        def contact_order(self):
            return live._EXPECTED_CONTACT_ORDER

    class FakeClient:
        def __init__(self, *, session, monotonic):
            self.session = session
            self.calls = 0

        def preflight(self, expected, *, cancel):
            assert expected is EXPECTED_IDENTITY
            assert not cancel()
            return PreflightResult(
                OllamaStatus.SUCCESS,
                observed_version="0.32.5",
                model_digest=EXPECTED_IDENTITY.model_digest,
            )

        def chat(self, request, *, expected_sha256, cancel):
            assert request.request_sha256 == expected_sha256
            self.calls += 1
            if self.calls == 2:
                self.session.seen = 50.0
                self.session.event.set()
                assert cancel()
                return ChatResult(OllamaStatus.CANCELLED_UNVERIFIED)
            assert not cancel()
            return _success_result(
                "PRIVATE_RESPONSE_MARKER"
                if self.calls == 1 else "SECOND_PRIVATE_RESPONSE_MARKER"
            )

    now = [50.0]
    monkeypatch.setattr(live, "_HeaderCancellationSession", FakeSession)
    monkeypatch.setattr(live, "OllamaClient", FakeClient)
    monkeypatch.setattr(
        live,
        "_sleep_until",
        lambda deadline, monotonic: now.__setitem__(0, deadline),
    )

    evidence = live.run_live_acceptance(
        confirm_live=True,
        monotonic=lambda: now[0],
    )
    payload = json.loads(evidence.as_json())
    rendered = evidence.as_json()

    assert payload["protocol"] == live.PROTOCOL_VERSION
    assert payload["preflight_status"] == "success"
    assert payload["cancellation_status"] == "cancelled_unverified"
    assert payload["cancellation_return_ms"] == 0
    assert payload["health_delay_ms"] == 2000
    assert payload["contact_count"] == 5
    assert payload["contact_order"] == list(live._EXPECTED_CONTACT_ORDER)
    assert "PRIVATE_RESPONSE_MARKER" not in rendered
    assert set(payload) == {
        "protocol",
        "preflight_status",
        "observed_version",
        "model_digest",
        "structured_request_sha256",
        "structured_content_sha256",
        "structured_eval_count",
        "cancellation_status",
        "cancellation_request_sha256",
        "cancellation_return_ms",
        "health_delay_ms",
        "health_request_sha256",
        "health_content_sha256",
        "health_eval_count",
        "contact_count",
        "contact_order",
    }
    with pytest.raises(ValueError, match="evidence is contradictory"):
        replace(
            evidence,
            contact_count=6,
            contact_order=evidence.contact_order + ("health_chat",),
        )
