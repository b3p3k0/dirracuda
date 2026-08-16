"""Offline Phase 2 engine acceptance tests with real durable state."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from experimental.analyst.models import FileTerminal
from experimental.analyst.lease import (
    ReconcileResult,
    claim_worker,
    current_lease,
    reconcile_lease,
    release_worker,
)
from experimental.analyst.ollama_contract import (
    MODEL_DIGEST,
    QUALIFIED_OLLAMA_VERSION,
    ChatMetrics,
    ChatResult,
    OllamaStatus,
    PromptKind,
    TagsCheckResult,
    VersionCheckResult,
    build_chat_request,
)
from experimental.analyst.phase1 import Phase1Dependencies, run_phase1
import experimental.analyst.phase2 as phase2_module
from experimental.analyst.phase2 import (
    Phase2Cancelled,
    Phase2Dependencies,
    Phase2Error,
    Phase2Failure,
    Phase2Interrupted,
    Phase2PausedResource,
    run_phase2,
)
from experimental.analyst.phase2_contract import HEALTH_SOURCE, derive_nonce
from experimental.analyst.phase2_state import claim_next_phase2_file
from experimental.analyst.process_identity import current_process_identity
from experimental.analyst.ollama_state import finish_contact, precharge_chat_contact
from experimental.analyst.contact_contract import ContactStatus
from experimental.analyst.db_schema import AnalystSchemaError, validate_schema
from experimental.analyst.store import load_worker_run, open_connection
from experimental.analyst.worker_contract import Phase1Handoff
from shared.tests.test_analyst_c10b import _run, _success_extract


_NO_FINDINGS = json.dumps({
    "document_type": "Public note",
    "subject": "Synthetic",
    "assessment": "no_findings",
    "findings": [],
}, separators=(",", ":"))


def _metrics(content: str, *, done_reason: str = "stop") -> ChatMetrics:
    content_bytes = len(content.encode("utf-8"))
    return ChatMetrics(
        done_reason=done_reason,
        prompt_eval_count=10,
        eval_count=4,
        total_duration_ns=100,
        load_duration_ns=20,
        prompt_eval_duration_ns=30,
        eval_duration_ns=40,
        raw_body_bytes=content_bytes + 100,
        content_bytes=content_bytes,
        thinking_bytes=0,
    )


def _chat_result(
    status: OllamaStatus,
    content: str = _NO_FINDINGS,
) -> ChatResult:
    if status is OllamaStatus.SUCCESS:
        return ChatResult(status, content, _metrics(content))
    if status is OllamaStatus.MODEL_INVALID:
        return ChatResult(status, None, _metrics(""))
    return ChatResult(status)


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 0.0
        self.heartbeat_ns = 100
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.seconds

    def monotonic_ns(self) -> int:
        self.heartbeat_ns += 1
        return self.heartbeat_ns

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.seconds += seconds

    def utc_now(self) -> str:
        value = datetime(2026, 8, 16, tzinfo=timezone.utc) + timedelta(
            seconds=self.seconds,
        )
        return value.isoformat(timespec="microseconds")


class FakeClient:
    def __init__(
        self,
        *,
        versions: tuple[OllamaStatus, ...] = (OllamaStatus.SUCCESS,),
        tags: tuple[OllamaStatus, ...] = (OllamaStatus.SUCCESS,),
        chats: tuple[ChatResult, ...] = (_chat_result(OllamaStatus.SUCCESS),),
        chat_hook=None,
    ) -> None:
        self.versions = deque(versions)
        self.tags = deque(tags)
        self.chats = deque(chats)
        self.calls: list[tuple[str, object | None]] = []
        self.cancel_calls = 0
        self.chat_hook = chat_hook

    def check_version(self, *, cancel, poll=None) -> VersionCheckResult:
        assert not cancel()
        if poll is not None:
            poll()
        self.calls.append(("version", None))
        status = self.versions.popleft()
        return VersionCheckResult(
            status,
            observed_version=(
                QUALIFIED_OLLAMA_VERSION
                if status is OllamaStatus.SUCCESS else None
            ),
        )

    def check_tags(self, expected, *, cancel, poll=None) -> TagsCheckResult:
        assert not cancel()
        if poll is not None:
            poll()
        self.calls.append(("tags", expected))
        status = self.tags.popleft()
        return TagsCheckResult(
            status,
            model_digest=MODEL_DIGEST if status is OllamaStatus.SUCCESS else None,
        )

    def chat(self, request, *, expected_sha256, cancel, poll=None) -> ChatResult:
        assert not cancel()
        assert request.request_sha256 == expected_sha256
        if poll is not None:
            poll()
        self.calls.append(("chat", request))
        if self.chat_hook is not None:
            self.chat_hook(poll, cancel, request)
        return self.chats.popleft()

    def cancel_current(self) -> None:
        self.cancel_calls += 1


def _dependencies(client: FakeClient, clock: FakeClock) -> Phase2Dependencies:
    phase1 = Phase1Dependencies(
        extract=_success_extract,
        monotonic_ns=clock.monotonic_ns,
    )
    return Phase2Dependencies(
        client=client,
        phase1=phase1,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        utc_now=clock.utc_now,
    )


def _selected(tmp_path: Path, text: str = "public selected text"):
    path, spec, _inventory, fence = _run(
        tmp_path, bodies=(text,), mode="deep",
    )
    context = load_worker_run(spec.run_id, path=path)
    handoff = run_phase1(
        context,
        fence,
        threading.Event(),
        path=path,
        dependencies=Phase1Dependencies(extract=_success_extract),
    )
    return path, context, handoff


def _contacts(path: Path) -> tuple[tuple[object, ...], ...]:
    conn = open_connection(path, read_only=True)
    try:
        return tuple(tuple(row) for row in conn.execute(
            "SELECT kind,state,semantic_attempt_no FROM analyst_ollama_contacts "
            "ORDER BY contact_no",
        ).fetchall())
    finally:
        conn.close()


def test_empty_handoff_returns_live_fence_with_zero_contacts(tmp_path: Path) -> None:
    path, spec, _inventory, fence = _run(tmp_path, bodies=(), mode="deep")
    client = FakeClient(chats=())
    clock = FakeClock()
    result = run_phase2(
        load_worker_run(spec.run_id, path=path),
        Phase1Handoff(fence, ()),
        threading.Event(),
        path=path,
        dependencies=_dependencies(client, clock),
    )

    assert (result.reviewed_file_count, result.valid_chunk_count,
            result.retained_finding_count) == (0, 0, 0)
    assert result.fence.heartbeat_monotonic_ns > fence.heartbeat_monotonic_ns
    assert client.calls == []
    assert _contacts(path) == ()


def test_handoff_must_equal_current_durable_phase1_rows_before_any_contact(
    tmp_path: Path,
) -> None:
    path, context, handoff = _selected(tmp_path)
    client = FakeClient(chats=())
    with pytest.raises(Phase2Error) as captured:
        run_phase2(
            context,
            Phase1Handoff(handoff.fence, ()),
            threading.Event(),
            path=path,
            dependencies=_dependencies(client, FakeClock()),
        )
    assert captured.value.code is Phase2Failure.CONTRACT
    assert client.calls == []
    assert _contacts(path) == ()


def test_success_runs_exact_identity_contacts_then_chat_and_keeps_lease(
    tmp_path: Path,
) -> None:
    path, context, handoff = _selected(tmp_path)
    client = FakeClient()
    clock = FakeClock()
    result = run_phase2(
        context, handoff, threading.Event(), path=path,
        dependencies=_dependencies(client, clock),
    )

    assert [kind for kind, _value in client.calls] == ["version", "tags", "chat"]
    request = client.calls[-1][1]
    assert request.prompt_kind is PromptKind.PRIMARY
    assert (result.reviewed_file_count, result.valid_chunk_count,
            result.retained_finding_count) == (1, 1, 0)
    assert _contacts(path) == (
        ("version", "success", None),
        ("tags", "success", None),
        ("chat", "success", 1),
    )
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT stage,work_state,terminal_code FROM analyst_files",
        ).fetchone()) == (
            "model_response_valid", "terminal", "complete_model_reviewed",
        )
        assert conn.execute(
            "SELECT run_id FROM analyst_gpu_lease WHERE slot=1",
        ).fetchone()[0] == context.run_id
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("versions", "tags", "expected_calls", "expected_contacts"),
    [
        (
            (OllamaStatus.IDENTITY_MISMATCH,),
            (),
            ["version"],
            (("version", "identity_mismatch", None),),
        ),
        (
            (OllamaStatus.SUCCESS,),
            (OllamaStatus.RESPONSE_LIMIT,),
            ["version", "tags"],
            (
                ("version", "success", None),
                ("tags", "response_limit", None),
            ),
        ),
    ],
)
def test_preflight_failure_stops_before_source_and_chat_then_releases(
    tmp_path: Path,
    versions: tuple[OllamaStatus, ...],
    tags: tuple[OllamaStatus, ...],
    expected_calls: list[str],
    expected_contacts: tuple[tuple[object, ...], ...],
) -> None:
    path, context, handoff = _selected(tmp_path)
    client = FakeClient(versions=versions, tags=tags, chats=())
    with pytest.raises(Phase2Error) as captured:
        run_phase2(
            context, handoff, threading.Event(), path=path,
            dependencies=_dependencies(client, FakeClock()),
        )
    assert captured.value.code is Phase2Failure.PREFLIGHT
    assert [kind for kind, _value in client.calls] == expected_calls
    assert _contacts(path) == expected_contacts
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT state,work_state FROM analyst_runs "
            "JOIN analyst_files USING(run_id)",
        ).fetchone()) == ("interrupted", "pending")
        assert conn.execute(
            "SELECT run_id FROM analyst_gpu_lease WHERE slot=1",
        ).fetchone()[0] is None
    finally:
        conn.close()


def test_model_invalid_uses_one_distinct_repair_then_succeeds(tmp_path: Path) -> None:
    path, context, handoff = _selected(tmp_path)
    client = FakeClient(chats=(
        _chat_result(OllamaStatus.MODEL_INVALID),
        _chat_result(OllamaStatus.SUCCESS),
    ))
    result = run_phase2(
        context, handoff, threading.Event(), path=path,
        dependencies=_dependencies(client, FakeClock()),
    )

    requests = [value for kind, value in client.calls if kind == "chat"]
    assert [request.prompt_kind for request in requests] == [
        PromptKind.PRIMARY, PromptKind.MODEL_INVALID_REPAIR,
    ]
    assert requests[0].request_sha256 != requests[1].request_sha256
    assert result.valid_chunk_count == 1
    assert _contacts(path)[2:] == (
        ("chat", "model_invalid", 1),
        ("chat", "success", 2),
    )


def test_fully_ungrounded_findings_present_uses_repair_not_false_success(
    tmp_path: Path,
) -> None:
    ungrounded = json.dumps({
        "document_type": "Public note",
        "subject": "Synthetic",
        "assessment": "findings_present",
        "findings": [{
            "category": "contact",
            "quote": "absent@example.test",
            "offset": 0,
        }],
    }, separators=(",", ":"))
    path, context, handoff = _selected(tmp_path)
    client = FakeClient(chats=(
        _chat_result(OllamaStatus.SUCCESS, ungrounded),
        _chat_result(OllamaStatus.SUCCESS),
    ))
    result = run_phase2(
        context, handoff, threading.Event(), path=path,
        dependencies=_dependencies(client, FakeClock()),
    )

    requests = [value for kind, value in client.calls if kind == "chat"]
    assert [request.prompt_kind for request in requests] == [
        PromptKind.PRIMARY, PromptKind.MODEL_INVALID_REPAIR,
    ]
    assert _contacts(path)[2:] == (
        ("chat", "model_invalid", 1),
        ("chat", "success", 2),
    )
    assert result.valid_chunk_count == 1


def test_two_model_invalid_answers_terminalize_without_attempt_three(
    tmp_path: Path,
) -> None:
    path, context, handoff = _selected(tmp_path)
    client = FakeClient(chats=(
        _chat_result(OllamaStatus.MODEL_INVALID),
        _chat_result(OllamaStatus.MODEL_INVALID),
    ))
    result = run_phase2(
        context, handoff, threading.Event(), path=path,
        dependencies=_dependencies(client, FakeClock()),
    )

    assert [kind for kind, _value in client.calls].count("chat") == 2
    assert result.valid_chunk_count == 0
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT terminal_code FROM analyst_files",
        ).fetchone()[0] == FileTerminal.MODEL_INVALID.value
        assert conn.execute(
            "SELECT count(*) FROM analyst_model_attempts",
        ).fetchone()[0] == 2
    finally:
        conn.close()


def test_success_normalizes_one_duplicate_and_grounds_canonical_span(
    tmp_path: Path,
) -> None:
    source = "public@example.test"
    row = {"category": "contact", "quote": source, "offset": 99}
    raw = json.dumps({
        "document_type": "Public note",
        "subject": "Synthetic",
        "assessment": "findings_present",
        "findings": [row, {**row, "offset": 0}],
    }, separators=(",", ":"))
    path, context, handoff = _selected(tmp_path, source)
    client = FakeClient(chats=(_chat_result(OllamaStatus.SUCCESS, raw),))
    result = run_phase2(
        context, handoff, threading.Event(), path=path,
        dependencies=_dependencies(client, FakeClock()),
    )

    assert [kind for kind, _value in client.calls].count("chat") == 1
    assert result.retained_finding_count == 1
    conn = open_connection(path, read_only=True)
    try:
        finding = conn.execute(
            "SELECT quote,model_offset,canonical_offset,canonical_end "
            "FROM analyst_model_findings",
        ).fetchone()
        assert tuple(finding) == (source, 99, 0, len(source))
        counters = conn.execute(
            "SELECT raw_finding_count,removed_duplicate_count,"
            "dropped_ungrounded_count FROM analyst_chunks",
        ).fetchone()
        assert tuple(counters) == (2, 1, 0)
        dump = "\n".join(conn.iterdump())
        assert raw not in dump
        assert "<<<FENCE_" not in dump
    finally:
        conn.close()


@pytest.mark.parametrize(
    "status",
    [
        OllamaStatus.IDENTITY_MISMATCH,
        OllamaStatus.PROTOCOL_VIOLATION,
        OllamaStatus.RESPONSE_LIMIT,
    ],
)
def test_nonretryable_model_failure_makes_no_second_chat(
    tmp_path: Path, status: OllamaStatus,
) -> None:
    path, context, handoff = _selected(tmp_path)
    client = FakeClient(chats=(_chat_result(status),))
    result = run_phase2(
        context, handoff, threading.Event(), path=path,
        dependencies=_dependencies(client, FakeClock()),
    )

    assert [kind for kind, _value in client.calls].count("chat") == 1
    assert result.valid_chunk_count == 0
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT terminal_code FROM analyst_files",
        ).fetchone()[0] == FileTerminal.MODEL_TRANSPORT_ERROR.value
    finally:
        conn.close()


@pytest.mark.parametrize(
    "first_status",
    [OllamaStatus.REQUEST_TIMEOUT, OllamaStatus.TRANSPORT_UNAVAILABLE],
)
def test_ambiguous_delivery_requires_delayed_health_before_base_retry(
    tmp_path: Path, first_status: OllamaStatus,
) -> None:
    path, context, handoff = _selected(tmp_path)
    client = FakeClient(chats=(
        _chat_result(first_status),
        _chat_result(OllamaStatus.SUCCESS),
        _chat_result(OllamaStatus.SUCCESS),
    ))
    clock = FakeClock()
    result = run_phase2(
        context, handoff, threading.Event(), path=path,
        dependencies=_dependencies(client, clock),
    )

    requests = [value for kind, value in client.calls if kind == "chat"]
    assert [request.source_text for request in requests] == [
        "public selected text", HEALTH_SOURCE, "public selected text",
    ]
    assert requests[0].prompt_kind is requests[2].prompt_kind is PromptKind.PRIMARY
    assert requests[0].request_sha256 == requests[2].request_sha256
    assert sum(clock.sleeps) >= 2.0
    assert result.valid_chunk_count == 1
    assert _contacts(path)[2:] == (
        ("chat", first_status.value, 1),
        ("cancellation_health", "success", None),
        ("chat", "success", 2),
    )


def test_interrupted_attempt_resumes_with_preflight_health_then_exact_base_retry(
    tmp_path: Path,
) -> None:
    source = "public selected text"
    path, context, handoff = _selected(tmp_path, source)
    snapshot = claim_next_phase2_file(handoff.fence, path=path)
    assert snapshot is not None
    chunk = snapshot.chunks[0].identity
    nonce = derive_nonce(
        context.run_id, chunk.chunk_id, chunk.sha256, PromptKind.PRIMARY, source,
    )
    base_request = build_chat_request(source, nonce=nonce)
    charge = precharge_chat_contact(
        handoff.fence, chunk.chunk_id, base_request.request_sha256, path=path,
    )
    finish_contact(
        handoff.fence,
        charge.contact_id,
        ContactStatus.REQUEST_TIMEOUT,
        path=path,
    )
    assert release_worker(handoff.fence, path=path).value == "interrupted"
    resumed_context = load_worker_run(context.run_id, path=path)
    successor = claim_worker(
        context.run_id,
        handoff.fence.process,
        owner_token="b" * 64,
        heartbeat_monotonic_ns=handoff.fence.heartbeat_monotonic_ns + 100,
        path=path,
    )
    assert successor is not None
    resumed_handoff = Phase1Handoff(successor, handoff.files)
    client = FakeClient(chats=(
        _chat_result(OllamaStatus.SUCCESS),
        _chat_result(OllamaStatus.SUCCESS),
    ))
    clock = FakeClock()

    result = run_phase2(
        resumed_context,
        resumed_handoff,
        threading.Event(),
        path=path,
        dependencies=_dependencies(client, clock),
    )

    requests = [value for kind, value in client.calls if kind == "chat"]
    assert [request.source_text for request in requests] == [HEALTH_SOURCE, source]
    assert requests[-1].request_sha256 == base_request.request_sha256
    assert [kind for kind, _value in client.calls[:2]] == ["version", "tags"]
    assert sum(clock.sleeps) >= 2.0
    assert result.valid_chunk_count == 1
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT attempt_no,state,request_sha256 FROM analyst_model_attempts "
            "ORDER BY attempt_no",
        ).fetchall()[0]) == (
            1, "model_timeout", base_request.request_sha256,
        )
        assert tuple(conn.execute(
            "SELECT attempt_no,state,request_sha256 FROM analyst_model_attempts "
            "ORDER BY attempt_no",
        ).fetchall()[1]) == (
            2, "valid", base_request.request_sha256,
        )
    finally:
        conn.close()


def test_six_chat_resource_refusals_pause_and_release_without_semantic_attempt(
    tmp_path: Path,
) -> None:
    path, context, handoff = _selected(tmp_path)
    client = FakeClient(
        chats=tuple(
            _chat_result(OllamaStatus.RESOURCE_BUSY) for _ in range(6)
        ),
    )
    clock = FakeClock()
    with pytest.raises(Phase2PausedResource):
        run_phase2(
            context, handoff, threading.Event(), path=path,
            dependencies=_dependencies(client, clock),
        )

    assert [kind for kind, _value in client.calls] == [
        "version", "tags", *("chat" for _ in range(6)),
    ]
    assert _contacts(path) == (
        ("version", "success", None),
        ("tags", "success", None),
        *(("chat", "resource_busy", 1) for _ in range(6)),
    )
    assert sum(clock.sleeps) >= 15 + 30 + 60 + 120 + 240
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM analyst_model_attempts",
        ).fetchone()[0] == 0
        assert tuple(conn.execute(
            "SELECT state,work_state FROM analyst_runs "
            "JOIN analyst_files USING(run_id)",
        ).fetchone()) == ("interrupted", "pending")
        assert conn.execute(
            "SELECT run_id FROM analyst_gpu_lease WHERE slot=1",
        ).fetchone()[0] is None
    finally:
        conn.close()


def test_source_drift_after_preflight_makes_zero_chat_and_safe_terminal(
    tmp_path: Path,
) -> None:
    path, context, handoff = _selected(tmp_path)
    (Path(context.source_root) / "public-0.txt").write_text(
        "changed public text", encoding="utf-8",
    )
    client = FakeClient(chats=())
    result = run_phase2(
        context, handoff, threading.Event(), path=path,
        dependencies=_dependencies(client, FakeClock()),
    )

    assert [kind for kind, _value in client.calls] == ["version", "tags"]
    assert result.reviewed_file_count == 0
    assert result.valid_chunk_count == 0
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT terminal_code FROM analyst_files",
        ).fetchone()[0] == FileTerminal.SOURCE_CHANGED_SINCE_INVENTORY.value
    finally:
        conn.close()


def test_preexisting_local_stop_interrupts_and_releases_without_contact(
    tmp_path: Path,
) -> None:
    path, context, handoff = _selected(tmp_path)
    stop = threading.Event()
    stop.set()
    client = FakeClient(chats=())
    with pytest.raises(Phase2Interrupted):
        run_phase2(
            context, handoff, stop, path=path,
            dependencies=_dependencies(client, FakeClock()),
        )
    assert client.calls == []
    assert _contacts(path) == ()


def test_durable_cancel_wins_before_contact_and_acknowledges_run(tmp_path: Path) -> None:
    from experimental.analyst.lease import request_cancel

    path, context, handoff = _selected(tmp_path)
    request_cancel(context.run_id, path=path)
    client = FakeClient(chats=())
    with pytest.raises(Phase2Cancelled):
        run_phase2(
            context, handoff, threading.Event(), path=path,
            dependencies=_dependencies(client, FakeClock()),
        )
    assert client.calls == []
    assert _contacts(path) == ()
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT state FROM analyst_runs",
        ).fetchone()[0] == "cancelled_pending_resume"
    finally:
        conn.close()


def test_long_fake_chat_advances_persisted_successor_heartbeats(tmp_path: Path) -> None:
    path, context, handoff = _selected(tmp_path)
    clock = FakeClock()
    observed: list[int] = []

    def long_chat(poll, _cancel, _request) -> None:
        assert poll is not None
        for _ in range(3):
            clock.sleep(2.1)
            poll()
            conn = open_connection(path, read_only=True)
            try:
                observed.append(int(conn.execute(
                    "SELECT heartbeat_monotonic_ns FROM analyst_gpu_lease WHERE slot=1",
                ).fetchone()[0]))
            finally:
                conn.close()

    client = FakeClient(chat_hook=long_chat)
    result = run_phase2(
        context, handoff, threading.Event(), path=path,
        dependencies=_dependencies(client, clock),
    )

    assert len(observed) == 3
    assert observed == sorted(set(observed))
    assert result.fence.heartbeat_monotonic_ns >= observed[-1]


def test_durable_cancel_during_charged_chat_wins_and_reconciles_contact(
    tmp_path: Path,
) -> None:
    from experimental.analyst.lease import request_cancel

    path, context, handoff = _selected(tmp_path)

    def cancel_after_charge(poll, _cancel, _request) -> None:
        request_cancel(context.run_id, path=path)
        assert poll is not None
        poll()

    client = FakeClient(chat_hook=cancel_after_charge)
    with pytest.raises(Phase2Cancelled):
        run_phase2(
            context, handoff, threading.Event(), path=path,
            dependencies=_dependencies(client, FakeClock()),
        )

    assert [kind for kind, _value in client.calls] == ["version", "tags", "chat"]
    assert _contacts(path)[-1] == (
        "chat", "cancelled_unverified", 1,
    )
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT state,work_state FROM analyst_runs "
            "JOIN analyst_files USING(run_id)",
        ).fetchone()) == (
            "cancelled_pending_resume", "cancelled_pending_resume",
        )
        assert conn.execute(
            "SELECT run_id FROM analyst_gpu_lease WHERE slot=1",
        ).fetchone()[0] is None
    finally:
        conn.close()


def test_transport_exception_detail_is_not_exposed_or_persisted(tmp_path: Path) -> None:
    marker = "PRIVATE_EXCEPTION_DETAIL_MARKER"
    path, context, handoff = _selected(tmp_path)

    def fail_after_charge(_poll, _cancel, _request) -> None:
        raise RuntimeError(marker)

    client = FakeClient(chat_hook=fail_after_charge)
    with pytest.raises(Phase2Error) as captured:
        run_phase2(
            context, handoff, threading.Event(), path=path,
            dependencies=_dependencies(client, FakeClock()),
        )
    assert captured.value.code is Phase2Failure.STATE
    assert marker not in repr(captured.value)
    conn = open_connection(path, read_only=True)
    try:
        dump = "\n".join(conn.iterdump())
        assert marker not in dump
        assert tuple(conn.execute(
            "SELECT state FROM analyst_ollama_contacts ORDER BY contact_no",
        ).fetchall()[-1]) == ("orphaned_unknown",)
        assert tuple(conn.execute(
            "SELECT state,failure_code FROM analyst_model_attempts",
        ).fetchone()) == ("orphaned_unknown", "orphaned_unknown")
    finally:
        conn.close()


def test_slow_regeneration_keeps_successor_heartbeat_advancing(
    tmp_path: Path,
) -> None:
    path, context, handoff = _selected(tmp_path)
    clock = FakeClock()
    observed: list[int] = []

    def slow_extract(fd, expected, cancel):
        observed.append(clock.heartbeat_ns)
        deadline = time.monotonic() + 2.2
        while time.monotonic() < deadline:
            assert not cancel()
            time.sleep(0.02)
        observed.append(clock.heartbeat_ns)
        return _success_extract(fd, expected, cancel)

    dependencies = Phase2Dependencies(
        client=FakeClient(),
        phase1=Phase1Dependencies(
            extract=slow_extract,
            monotonic_ns=clock.monotonic_ns,
        ),
        monotonic=time.monotonic,
        sleep=time.sleep,
        utc_now=clock.utc_now,
    )
    result = run_phase2(
        context, handoff, threading.Event(), path=path,
        dependencies=dependencies,
    )

    assert len(observed) == 2
    assert observed[1] > observed[0]
    assert result.fence.heartbeat_monotonic_ns >= observed[1]


def test_durable_cancel_during_regeneration_is_not_source_drift(
    tmp_path: Path,
) -> None:
    from experimental.analyst.lease import request_cancel

    path, context, handoff = _selected(tmp_path)
    clock = FakeClock()
    started = threading.Event()

    def slow_extract(fd, expected, cancel):
        started.set()
        deadline = time.monotonic() + 5.0
        while not cancel() and time.monotonic() < deadline:
            time.sleep(0.01)
        return _success_extract(fd, expected, cancel)

    def cancel_run() -> None:
        assert started.wait(2.0)
        request_cancel(context.run_id, path=path)

    trigger = threading.Thread(target=cancel_run, daemon=True)
    trigger.start()
    dependencies = Phase2Dependencies(
        client=FakeClient(chats=()),
        phase1=Phase1Dependencies(
            extract=slow_extract,
            monotonic_ns=clock.monotonic_ns,
        ),
        monotonic=time.monotonic,
        sleep=time.sleep,
        utc_now=clock.utc_now,
    )
    with pytest.raises(Phase2Cancelled):
        run_phase2(
            context, handoff, threading.Event(), path=path,
            dependencies=dependencies,
        )
    trigger.join(2.0)

    assert not trigger.is_alive()
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT state,work_state,terminal_code FROM analyst_runs "
            "JOIN analyst_files USING(run_id)",
        ).fetchone()) == (
            "cancelled_pending_resume", "cancelled_pending_resume", None,
        )
    finally:
        conn.close()


def test_lease_loss_during_regeneration_stops_helper_without_masking_outcome(
    tmp_path: Path,
) -> None:
    path, context, handoff = _selected(tmp_path)
    clock = FakeClock()
    started = threading.Event()

    def slow_extract(fd, expected, cancel):
        started.set()
        deadline = time.monotonic() + 5.0
        while not cancel() and time.monotonic() < deadline:
            time.sleep(0.01)
        return _success_extract(fd, expected, cancel)

    def take_lease_away() -> None:
        assert started.wait(2.0)
        live = current_lease(path=path)
        assert live is not None
        assert release_worker(live, path=path).value == "interrupted"

    breaker = threading.Thread(target=take_lease_away, daemon=True)
    breaker.start()
    dependencies = Phase2Dependencies(
        client=FakeClient(chats=()),
        phase1=Phase1Dependencies(
            extract=slow_extract,
            monotonic_ns=clock.monotonic_ns,
        ),
        monotonic=time.monotonic,
        sleep=time.sleep,
        utc_now=clock.utc_now,
    )
    began = time.monotonic()
    with pytest.raises(Phase2Error) as captured:
        run_phase2(
            context, handoff, threading.Event(), path=path,
            dependencies=dependencies,
        )
    breaker.join(2.0)

    assert captured.value.code is Phase2Failure.LEASE
    assert time.monotonic() - began < 3.0
    assert not breaker.is_alive()
    assert current_lease(path=path) is None


@pytest.mark.parametrize(
    "crash_site", ["during_chat", "before_contact_finish", "after_contact_finish"],
)
def test_real_process_crash_around_chat_finish_never_resends_attempt_one(
    tmp_path: Path, crash_site: str,
) -> None:
    source = "public selected text"
    path, context, handoff = _selected(tmp_path, source)
    assert release_worker(handoff.fence, path=path).value == "interrupted"

    child = os.fork()
    if child == 0:
        try:
            child_context = load_worker_run(context.run_id, path=path)
            child_fence = claim_worker(
                context.run_id,
                current_process_identity(),
                owner_token="c" * 64,
                heartbeat_monotonic_ns=100,
                path=path,
            )
            if child_fence is None:
                os._exit(74)
            original_finish = phase2_module.finish_contact
            finishes = 0

            def crash_before_chat_finish(*args, **kwargs):
                nonlocal finishes
                finishes += 1
                if finishes == 3:
                    if crash_site == "after_contact_finish":
                        original_finish(*args, **kwargs)
                    os._exit(73)
                return original_finish(*args, **kwargs)

            phase2_module.finish_contact = crash_before_chat_finish
            client = FakeClient(
                chat_hook=(
                    (lambda _poll, _cancel, _request: os._exit(73))
                    if crash_site == "during_chat"
                    else None
                ),
            )
            run_phase2(
                child_context,
                Phase1Handoff(child_fence, handoff.files),
                threading.Event(),
                path=path,
                dependencies=_dependencies(client, FakeClock()),
            )
        except BaseException:
            os._exit(75)
        os._exit(76)

    waited, status = os.waitpid(child, 0)
    assert waited == child
    assert os.waitstatus_to_exitcode(status) == 73
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT kind,state,semantic_attempt_no FROM analyst_ollama_contacts "
            "ORDER BY contact_no",
        ).fetchall()[-1]) == (
            "chat",
            "success" if crash_site == "after_contact_finish" else "dispatching",
            1,
        )
    finally:
        conn.close()

    assert reconcile_lease(path=path) is ReconcileResult.CLEARED_INTERRUPTED
    resumed_context = load_worker_run(context.run_id, path=path)
    successor = claim_worker(
        context.run_id,
        current_process_identity(),
        owner_token="d" * 64,
        heartbeat_monotonic_ns=1_000,
        path=path,
    )
    assert successor is not None
    client = FakeClient(chats=(
        _chat_result(OllamaStatus.SUCCESS),
        _chat_result(OllamaStatus.SUCCESS),
    ))
    result = run_phase2(
        resumed_context,
        Phase1Handoff(successor, handoff.files),
        threading.Event(),
        path=path,
        dependencies=_dependencies(client, FakeClock()),
    )

    requests = [value for kind, value in client.calls if kind == "chat"]
    assert [request.source_text for request in requests] == [HEALTH_SOURCE, source]
    assert result.valid_chunk_count == 1
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(tuple(row) for row in conn.execute(
            "SELECT attempt_no,state FROM analyst_model_attempts ORDER BY attempt_no",
        ).fetchall()) == ((1, "orphaned_unknown"), (2, "valid"))
    finally:
        conn.close()


def test_resume_rejects_durable_attempt_request_hash_drift_before_scored_retry(
    tmp_path: Path,
) -> None:
    source = "public selected text"
    path, context, handoff = _selected(tmp_path, source)
    snapshot = claim_next_phase2_file(handoff.fence, path=path)
    assert snapshot is not None
    chunk = snapshot.chunks[0].identity
    request = build_chat_request(
        source,
        nonce=derive_nonce(
            context.run_id,
            chunk.chunk_id,
            chunk.sha256,
            PromptKind.PRIMARY,
            source,
        ),
    )
    charge = precharge_chat_contact(
        handoff.fence, chunk.chunk_id, request.request_sha256, path=path,
    )
    finish_contact(
        handoff.fence,
        charge.contact_id,
        ContactStatus.REQUEST_TIMEOUT,
        path=path,
    )
    assert release_worker(handoff.fence, path=path).value == "interrupted"
    forged = "f" * 64
    conn = open_connection(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE analyst_ollama_contacts SET request_sha256=? WHERE contact_id=?",
            (forged, charge.contact_id),
        )
        conn.execute(
            "UPDATE analyst_model_attempts SET request_sha256=? WHERE chunk_id=?",
            (forged, chunk.chunk_id),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    conn = open_connection(path)
    try:
        with pytest.raises(AnalystSchemaError, match="not deterministic"):
            validate_schema(conn)
    finally:
        conn.close()
    successor = claim_worker(
        context.run_id,
        handoff.fence.process,
        owner_token="9" * 64,
        heartbeat_monotonic_ns=1_000,
        path=path,
    )
    assert successor is not None
    client = FakeClient(chats=(_chat_result(OllamaStatus.SUCCESS),))

    with pytest.raises(Phase2Error) as captured:
        run_phase2(
            context,
            Phase1Handoff(successor, handoff.files),
            threading.Event(),
            path=path,
            dependencies=_dependencies(client, FakeClock()),
        )
    assert captured.value.code is Phase2Failure.RESUME_MISMATCH
    assert [kind for kind, _value in client.calls] == [
        "version", "tags", "chat",
    ]
    assert client.calls[-1][1].source_text == HEALTH_SOURCE


def test_real_crash_after_valid_chunk_resumes_file_close_with_zero_http(
    tmp_path: Path,
) -> None:
    path, context, handoff = _selected(tmp_path)
    assert release_worker(handoff.fence, path=path).value == "interrupted"
    child = os.fork()
    if child == 0:
        try:
            child_context = load_worker_run(context.run_id, path=path)
            child_fence = claim_worker(
                context.run_id,
                current_process_identity(),
                owner_token="7" * 64,
                heartbeat_monotonic_ns=100,
                path=path,
            )
            if child_fence is None:
                os._exit(74)
            phase2_module.finish_phase2_file = lambda *_args, **_kwargs: os._exit(72)
            run_phase2(
                child_context,
                Phase1Handoff(child_fence, handoff.files),
                threading.Event(),
                path=path,
                dependencies=_dependencies(FakeClient(), FakeClock()),
            )
        except BaseException:
            os._exit(75)
        os._exit(76)

    waited, status = os.waitpid(child, 0)
    assert waited == child
    assert os.waitstatus_to_exitcode(status) == 72
    conn = open_connection(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT c.state,f.stage,f.work_state FROM analyst_chunks c "
            "JOIN analyst_files f ON f.file_id=c.file_id",
        ).fetchone()) == (
            "model_response_valid", "selected_for_model", "active",
        )
    finally:
        conn.close()

    assert reconcile_lease(path=path) is ReconcileResult.CLEARED_INTERRUPTED
    successor = claim_worker(
        context.run_id,
        current_process_identity(),
        owner_token="8" * 64,
        heartbeat_monotonic_ns=1_000,
        path=path,
    )
    assert successor is not None
    client = FakeClient(versions=(), tags=(), chats=())
    result = run_phase2(
        load_worker_run(context.run_id, path=path),
        Phase1Handoff(successor, handoff.files),
        threading.Event(),
        path=path,
        dependencies=_dependencies(client, FakeClock()),
    )

    assert client.calls == []
    assert (result.reviewed_file_count, result.valid_chunk_count) == (1, 1)
