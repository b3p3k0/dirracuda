#!/usr/bin/env python3
"""Run the bounded, public-only C9 Ollama acceptance protocol.

This runner is intentionally separate from production orchestration.  It uses
only fixed synthetic text, retains model content in memory only long enough to
hash it, and prints content-free evidence.  No network contact occurs unless
``--confirm-live`` is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final

import requests

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from experimental.analyst.ollama_client import OllamaClient
from experimental.analyst.ollama_contract import (
    CANCEL_HEALTH_DELAY_SECONDS,
    EXPECTED_IDENTITY,
    OLLAMA_CHAT_URL,
    OLLAMA_TAGS_URL,
    OLLAMA_VERSION_URL,
    OllamaStatus,
    build_chat_request,
    new_prompt_nonce,
)


PROTOCOL_VERSION: Final = "analyst-c9-live-v1"
CANCEL_RETURN_LIMIT_SECONDS: Final = 5.0
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_EXPECTED_CONTACT_ORDER: Final = (
    "version",
    "tags",
    "structured_chat",
    "cancellation_chat",
    "health_chat",
)
_FAILURE_CODES: Final = frozenset(
    {
        "preflight_failed",
        "structured_chat_failed",
        "cancellation_headers_not_observed",
        "cancellation_failed",
        "cancellation_health_failed",
        "cancellation_observer_already_armed",
        "contact_sequence_failed",
        "health_delay_failed",
        "live_confirmation_required",
    }
)

_PUBLIC_STRUCTURED_SOURCE: Final = (
    "PUBLIC SYNTHETIC TEST RECORD. Reserved documentation identifiers only.\n"
    "Contact: analyst@example.invalid\n"
    "Address: 192.0.2.44\n"
    "Assessment: this record is intentionally non-sensitive test material."
)
_PUBLIC_CANCEL_SOURCE: Final = (
    "PUBLIC SYNTHETIC CANCELLATION TEST. Reserved documentation data only.\n"
    "Contact: cancel-test@example.invalid\n"
    "Address: 198.51.100.77\n"
) * 24
_PUBLIC_HEALTH_SOURCE: Final = (
    "PUBLIC SYNTHETIC HEALTH TEST. Reserved documentation data only.\n"
    "Contact: health@example.invalid\n"
    "Status: routine."
)


class LiveAcceptanceError(RuntimeError):
    """A closed acceptance stage did not meet its frozen outcome."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _FAILURE_CODES:
            raise ValueError("live acceptance failure code is not closed")
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class LiveEvidence:
    """Content-free evidence returned by one successful acceptance run."""

    preflight_status: str
    observed_version: str
    model_digest: str
    structured_request_sha256: str
    structured_content_sha256: str
    structured_eval_count: int
    cancellation_status: str
    cancellation_request_sha256: str
    cancellation_return_ms: int
    health_delay_ms: int
    health_request_sha256: str
    health_content_sha256: str
    health_eval_count: int
    contact_count: int
    contact_order: tuple[str, ...]

    def __post_init__(self) -> None:
        hashes = (
            self.model_digest,
            self.structured_request_sha256,
            self.structured_content_sha256,
            self.cancellation_request_sha256,
            self.health_request_sha256,
            self.health_content_sha256,
        )
        if (
            self.preflight_status != "success"
            or self.cancellation_status != "cancelled_unverified"
            or self.observed_version != "0.32.5"
            or any(type(value) is not str or _SHA256.fullmatch(value) is None for value in hashes)
            or type(self.structured_eval_count) is not int
            or self.structured_eval_count < 0
            or type(self.health_eval_count) is not int
            or self.health_eval_count < 0
            or type(self.cancellation_return_ms) is not int
            or not 0 <= self.cancellation_return_ms <= 5_000
            or type(self.health_delay_ms) is not int
            or self.health_delay_ms < round(CANCEL_HEALTH_DELAY_SECONDS * 1000)
            or type(self.contact_count) is not int
            or self.contact_count != len(_EXPECTED_CONTACT_ORDER)
            or type(self.contact_order) is not tuple
            or self.contact_order != _EXPECTED_CONTACT_ORDER
        ):
            raise ValueError("live acceptance evidence is contradictory")

    def as_json(self) -> str:
        return json.dumps(
            {
                "protocol": PROTOCOL_VERSION,
                "preflight_status": self.preflight_status,
                "observed_version": self.observed_version,
                "model_digest": self.model_digest,
                "structured_request_sha256": self.structured_request_sha256,
                "structured_content_sha256": self.structured_content_sha256,
                "structured_eval_count": self.structured_eval_count,
                "cancellation_status": self.cancellation_status,
                "cancellation_request_sha256": self.cancellation_request_sha256,
                "cancellation_return_ms": self.cancellation_return_ms,
                "health_delay_ms": self.health_delay_ms,
                "health_request_sha256": self.health_request_sha256,
                "health_content_sha256": self.health_content_sha256,
                "health_eval_count": self.health_eval_count,
                "contact_count": self.contact_count,
                "contact_order": list(self.contact_order),
            },
            sort_keys=True,
            separators=(",", ":"),
        )


class _HeaderCancellationSession(requests.Session):
    """Set one cancellation flag immediately after chat response headers arrive."""

    def __init__(self, *, monotonic: Callable[[], float]) -> None:
        super().__init__()
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._armed_event: threading.Event | None = None
        self._header_seen_at: float | None = None
        self._contacts: list[str] = []

    def arm(self, event: threading.Event) -> None:
        if type(event) is not threading.Event:
            raise TypeError("cancellation event must be an exact threading.Event")
        with self._lock:
            if self._armed_event is not None:
                raise LiveAcceptanceError("cancellation_observer_already_armed")
            self._armed_event = event
            self._header_seen_at = None

    def header_seen_at(self) -> float | None:
        with self._lock:
            return self._header_seen_at

    def contact_order(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._contacts)

    def request(self, method: str, url: str, **kwargs):  # type: ignore[no-untyped-def]
        with self._lock:
            contact_number = len(self._contacts)
            expected = {
                ("GET", OLLAMA_VERSION_URL): "version",
                ("GET", OLLAMA_TAGS_URL): "tags",
                ("POST", OLLAMA_CHAT_URL): (
                    "structured_chat",
                    "cancellation_chat",
                    "health_chat",
                )[min(max(contact_number - 2, 0), 2)],
            }.get((method, url), "unexpected")
            self._contacts.append(expected)
        response = super().request(method, url, **kwargs)
        event: threading.Event | None = None
        if method == "POST" and url == OLLAMA_CHAT_URL:
            with self._lock:
                if self._armed_event is not None:
                    candidate = self._armed_event
                    self._armed_event = None
                    if _is_accepted_stream_response(response):
                        event = candidate
                        self._header_seen_at = self._monotonic()
        if event is not None:
            event.set()
        return response


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sleep_until(deadline: float, *, monotonic: Callable[[], float]) -> None:
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.1))


def _is_accepted_stream_response(response: object) -> bool:
    if type(getattr(response, "status_code", None)) is not int:
        return False
    if response.status_code != 200:  # type: ignore[attr-defined]
        return False
    headers = getattr(response, "headers", None)
    if headers is None or not callable(getattr(headers, "get", None)):
        return False
    content_type = headers.get("content-type", "")
    content_encoding = headers.get("content-encoding", "")
    return (
        type(content_type) is str
        and content_type.split(";", 1)[0].strip().lower()
        == "application/x-ndjson"
        and type(content_encoding) is str
        and content_encoding.strip().lower() in {"", "identity"}
    )


def run_live_acceptance(
    *, confirm_live: bool, monotonic: Callable[[], float] = time.monotonic,
) -> LiveEvidence:
    """Run the exact public preflight/chat/cancel/health sequence once."""
    if type(confirm_live) is not bool or not confirm_live:
        raise LiveAcceptanceError("live_confirmation_required")
    session = _HeaderCancellationSession(monotonic=monotonic)
    client = OllamaClient(session=session, monotonic=monotonic)

    preflight = client.preflight(EXPECTED_IDENTITY, cancel=lambda: False)
    if (
        preflight.status is not OllamaStatus.SUCCESS
        or preflight.observed_version is None
        or preflight.model_digest is None
    ):
        raise LiveAcceptanceError("preflight_failed")

    structured = build_chat_request(
        _PUBLIC_STRUCTURED_SOURCE,
        nonce=new_prompt_nonce(_PUBLIC_STRUCTURED_SOURCE),
    )
    structured_result = client.chat(
        structured,
        expected_sha256=structured.request_sha256,
        cancel=lambda: False,
    )
    if (
        structured_result.status is not OllamaStatus.SUCCESS
        or structured_result.content is None
        or structured_result.metrics is None
    ):
        raise LiveAcceptanceError("structured_chat_failed")

    cancel_event = threading.Event()
    cancelled_request = build_chat_request(
        _PUBLIC_CANCEL_SOURCE,
        nonce=new_prompt_nonce(_PUBLIC_CANCEL_SOURCE),
    )
    session.arm(cancel_event)
    cancelled_result = client.chat(
        cancelled_request,
        expected_sha256=cancelled_request.request_sha256,
        cancel=cancel_event.is_set,
    )
    returned_at = monotonic()
    header_seen_at = session.header_seen_at()
    if header_seen_at is None:
        raise LiveAcceptanceError("cancellation_headers_not_observed")
    cancellation_seconds = returned_at - header_seen_at
    if (
        cancelled_result.status is not OllamaStatus.CANCELLED_UNVERIFIED
        or cancellation_seconds < 0
        or cancellation_seconds > CANCEL_RETURN_LIMIT_SECONDS
    ):
        raise LiveAcceptanceError("cancellation_failed")

    _sleep_until(
        returned_at + CANCEL_HEALTH_DELAY_SECONDS,
        monotonic=monotonic,
    )
    health_started_at = monotonic()
    health_delay_seconds = health_started_at - returned_at
    if health_delay_seconds < CANCEL_HEALTH_DELAY_SECONDS:
        raise LiveAcceptanceError("health_delay_failed")
    health = build_chat_request(
        _PUBLIC_HEALTH_SOURCE,
        nonce=new_prompt_nonce(_PUBLIC_HEALTH_SOURCE),
    )
    health_result = client.chat(
        health,
        expected_sha256=health.request_sha256,
        cancel=lambda: False,
    )
    if (
        health_result.status is not OllamaStatus.SUCCESS
        or health_result.content is None
        or health_result.metrics is None
    ):
        raise LiveAcceptanceError("cancellation_health_failed")
    contact_order = session.contact_order()
    if contact_order != _EXPECTED_CONTACT_ORDER:
        raise LiveAcceptanceError("contact_sequence_failed")

    return LiveEvidence(
        preflight_status=preflight.status.value,
        observed_version=preflight.observed_version,
        model_digest=preflight.model_digest,
        structured_request_sha256=structured.request_sha256,
        structured_content_sha256=_sha256_text(structured_result.content),
        structured_eval_count=structured_result.metrics.eval_count,
        cancellation_status=cancelled_result.status.value,
        cancellation_request_sha256=cancelled_request.request_sha256,
        cancellation_return_ms=round(cancellation_seconds * 1000),
        health_delay_ms=round(health_delay_seconds * 1000),
        health_request_sha256=health.request_sha256,
        health_content_sha256=_sha256_text(health_result.content),
        health_eval_count=health_result.metrics.eval_count,
        contact_count=len(contact_order),
        contact_order=contact_order,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the public-only Analyst C9 Ollama live acceptance protocol.",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="authorize five bounded loopback HTTP contacts using public synthetic text",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.confirm_live:
        print("refusing live contact without --confirm-live", file=sys.stderr)
        return 2
    try:
        evidence = run_live_acceptance(confirm_live=True)
    except LiveAcceptanceError as exc:
        print(f"C9 live acceptance FAIL: {exc}", file=sys.stderr)
        return 3
    except Exception:
        print("C9 live acceptance FAIL: internal_error", file=sys.stderr)
        return 4
    print(evidence.as_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
