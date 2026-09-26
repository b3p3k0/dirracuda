"""R3b host READ reduce acceptance with an offline Ollama fake."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from experimental.analyst.lease import current_lease
from experimental.analyst.ollama_contract import (
    READ_MAX_SOURCE_CHARS,
    OllamaStatus,
    PromptKind,
)
from experimental.analyst.phase1 import Phase1Dependencies
from experimental.analyst.phase2 import run_phase2
from experimental.analyst.phase2_contract import Phase2Handoff
from experimental.analyst.read_reduce import (
    ReadReduceCancelled,
    ReadReduceDependencies,
    run_read_reduce,
)
from experimental.analyst.read_worksheet import read_schema
from experimental.analyst.store import open_connection
from experimental.analyst.worker import run_worker
from experimental.analyst.worker_contract import WorkerOutcome
from shared.tests.test_analyst_c10b import _success_extract
from shared.tests.test_analyst_c10c import _SUCCESS, _queued_run
from shared.tests.test_analyst_c11_engine import (
    FakeClient,
    FakeClock,
    _chat_result,
    _dependencies,
    _selected,
)


_MAP_QUOTE = "public-account-4821"
_MAP_RESPONSE = json.dumps({
    "document_type": "Public finance note",
    "subject": "Synthetic account register",
    "assessment": "findings_present",
    "findings": [{
        "category": "financial",
        "quote": _MAP_QUOTE,
        "offset": 0,
    }],
}, separators=(",", ":"))
_READ_RESPONSE = json.dumps({
    "host_summary": "The host appears to contain a small financial register.",
    "likely_owner": "Example Operations",
    "contacts": ["ops@example.test", "+1-555-0100"],
    "risk_level": "HIGH",
    "top_exposures": [
        {"severity": "HIGH", "text": "A financial account value is exposed."},
        {"severity": "MED", "text": "The register identifies its subject."},
    ],
}, separators=(",", ":"))


def _reviewed(tmp_path: Path):
    source = "PRIVATE_RAW_PREFIX " + _MAP_QUOTE + " PRIVATE_RAW_SUFFIX"
    path, context, phase1 = _selected(tmp_path, source)
    phase2 = run_phase2(
        context,
        phase1,
        threading.Event(),
        path=path,
        dependencies=_dependencies(
            FakeClient(chats=(
                _chat_result(OllamaStatus.SUCCESS, _MAP_RESPONSE),
            )),
            FakeClock(),
        ),
    )
    return path, context, phase2


def _read_dependencies(client: FakeClient, clock: FakeClock):
    return ReadReduceDependencies(
        client=client,
        monotonic=clock.monotonic,
        monotonic_ns=clock.monotonic_ns,
        sleep=clock.sleep,
        utc_now=clock.utc_now,
    )


def _rows(path: Path, sql: str):
    conn = open_connection(path, read_only=True)
    try:
        return tuple(tuple(row) for row in conn.execute(sql).fetchall())
    finally:
        conn.close()


def _refreshed(path: Path, handoff: Phase2Handoff) -> Phase2Handoff:
    fence = current_lease(path=path)
    assert fence is not None
    return Phase2Handoff(
        fence,
        handoff.reviewed_file_count,
        handoff.valid_chunk_count,
        handoff.retained_finding_count,
    )


def test_success_persists_read_exposures_and_bounded_safe_input(tmp_path: Path) -> None:
    path, context, handoff = _reviewed(tmp_path)
    client = FakeClient(chats=(
        _chat_result(OllamaStatus.SUCCESS, _READ_RESPONSE),
    ))
    run_read_reduce(
        context,
        handoff,
        threading.Event(),
        path=path,
        dependencies=_read_dependencies(client, FakeClock()),
    )

    assert _rows(
        path,
        "SELECT read_mode,risk_level,host_summary,likely_owner,contacts_json,"
        "files_read,files_total,flagged_files FROM analyst_read",
    ) == ((
        "full",
        "HIGH",
        "The host appears to contain a small financial register.",
        "Example Operations",
        '["ops@example.test","+1-555-0100"]',
        1,
        1,
        1,
    ),)
    assert _rows(
        path,
        "SELECT ordinal,severity,text FROM analyst_read_exposures ORDER BY ordinal",
    ) == (
        (1, "HIGH", "A financial account value is exposed."),
        (2, "MED", "The register identifies its subject."),
    )
    request = [value for kind, value in client.calls if kind == "chat"][0]
    assert len(request.source_text) <= READ_MAX_SOURCE_CHARS
    assert _MAP_QUOTE in request.source_text
    assert "PRIVATE_RAW_PREFIX" not in request.source_text
    assert "PRIVATE_RAW_SUFFIX" not in request.source_text
    assert request.payload()["format"] == read_schema()


def test_resume_existing_read_skips_without_contact(tmp_path: Path) -> None:
    path, context, handoff = _reviewed(tmp_path)
    first = FakeClient(chats=(
        _chat_result(OllamaStatus.SUCCESS, _READ_RESPONSE),
    ))
    run_read_reduce(
        context, handoff, threading.Event(), path=path,
        dependencies=_read_dependencies(first, FakeClock()),
    )
    second = FakeClient(chats=())
    run_read_reduce(
        context,
        _refreshed(path, handoff),
        threading.Event(),
        path=path,
        dependencies=_read_dependencies(second, FakeClock()),
    )
    assert second.calls == []
    assert _rows(path, "SELECT count(*) FROM analyst_read") == ((1,),)


def test_two_invalid_answers_leave_read_unavailable(tmp_path: Path) -> None:
    path, context, handoff = _reviewed(tmp_path)
    client = FakeClient(chats=(
        _chat_result(OllamaStatus.SUCCESS, "{}"),
        _chat_result(OllamaStatus.SUCCESS, "{}"),
    ))
    run_read_reduce(
        context, handoff, threading.Event(), path=path,
        dependencies=_read_dependencies(client, FakeClock()),
    )
    requests = [value for kind, value in client.calls if kind == "chat"]
    assert len(requests) == 2
    assert requests[0].nonce != requests[1].nonce
    assert [request.prompt_kind for request in requests] == [
        PromptKind.PRIMARY,
        PromptKind.MODEL_INVALID_REPAIR,
    ]
    assert "return ONLY the JSON object" in (
        requests[1].payload()["messages"][0]["content"]
    )
    assert _rows(path, "SELECT count(*) FROM analyst_read") == ((0,),)
    assert _rows(
        path,
        "SELECT attempt_no,state FROM analyst_read_contact ORDER BY attempt_no",
    ) == ((1, "model_invalid"), (2, "model_invalid"))


def test_resource_busy_backs_off_without_consuming_attempt(tmp_path: Path) -> None:
    path, context, handoff = _reviewed(tmp_path)
    clock = FakeClock()
    client = FakeClient(chats=(
        _chat_result(OllamaStatus.RESOURCE_BUSY),
        _chat_result(OllamaStatus.SUCCESS, _READ_RESPONSE),
    ))
    run_read_reduce(
        context, handoff, threading.Event(), path=path,
        dependencies=_read_dependencies(client, clock),
    )
    assert len([value for kind, value in client.calls if kind == "chat"]) == 2
    assert sum(clock.sleeps) == 15
    assert _rows(
        path,
        "SELECT attempt_no,state,resource_failures_before,"
        "resource_failures_after FROM analyst_read_contact",
    ) == ((1, "success", 1, 0),)
    assert _rows(path, "SELECT count(*) FROM analyst_read") == ((1,),)


def test_cancellation_finishes_contact_and_retains_live_fence(tmp_path: Path) -> None:
    path, context, handoff = _reviewed(tmp_path)
    stop_event = threading.Event()

    def cancel_during_chat(_poll, _cancel, _request):
        stop_event.set()

    client = FakeClient(
        chats=(_chat_result(OllamaStatus.CANCELLED_UNVERIFIED),),
        chat_hook=cancel_during_chat,
    )
    with pytest.raises(ReadReduceCancelled) as captured:
        run_read_reduce(
            context, handoff, stop_event, path=path,
            dependencies=_read_dependencies(client, FakeClock()),
        )
    assert repr(captured.value) == "ReadReduceCancelled('read_reduce')"
    assert _rows(
        path, "SELECT attempt_no,state FROM analyst_read_contact",
    ) == ((1, "cancelled_unverified"),)
    fence = current_lease(path=path)
    assert fence is not None and fence.run_id == context.run_id


def test_transport_exceptions_are_content_free_and_best_effort(tmp_path: Path) -> None:
    path, context, handoff = _reviewed(tmp_path)
    marker = "PRIVATE_EXCEPTION_BODY"

    def fail_chat(_poll, _cancel, _request):
        raise RuntimeError(marker)

    client = FakeClient(
        chats=(
            _chat_result(OllamaStatus.SUCCESS, _READ_RESPONSE),
            _chat_result(OllamaStatus.SUCCESS, _READ_RESPONSE),
        ),
        chat_hook=fail_chat,
    )
    dependencies = _read_dependencies(client, FakeClock())
    run_read_reduce(
        context, handoff, threading.Event(), path=path, dependencies=dependencies,
    )
    assert marker not in repr(dependencies)
    assert _rows(path, "SELECT count(*) FROM analyst_read") == ((0,),)
    assert _rows(
        path,
        "SELECT attempt_no,state FROM analyst_read_contact ORDER BY attempt_no",
    ) == ((1, "transport_unavailable"), (2, "transport_unavailable"))


def test_worker_finalizes_after_two_invalid_read_answers(tmp_path: Path) -> None:
    source = ("PRIVATE_RAW_PREFIX " + _MAP_QUOTE + " PRIVATE_RAW_SUFFIX").encode()
    path, spec, _inventory = _queued_run(
        tmp_path, bodies=(source,), mode="deep",
    )
    result = run_worker(
        spec.run_id,
        threading.Event(),
        path=path,
        phase1_dependencies=Phase1Dependencies(extract=_success_extract),
        phase2_dependencies=_dependencies(
            FakeClient(chats=(
                _chat_result(OllamaStatus.SUCCESS, _MAP_RESPONSE),
            )),
            FakeClock(),
        ),
        read_reduce_dependencies=_read_dependencies(
            FakeClient(chats=(
                _chat_result(OllamaStatus.SUCCESS, "{}"),
                _chat_result(OllamaStatus.SUCCESS, "{}"),
            )),
            FakeClock(),
        ),
        preflight=lambda _context, _cancel: _SUCCESS,
    )
    assert result.outcome is WorkerOutcome.COMPLETE
    assert _rows(path, "SELECT count(*) FROM analyst_read") == ((0,),)
    assert current_lease(path=path) is None


def test_worker_maps_read_cancellation_without_releasing_fence(tmp_path: Path) -> None:
    source = ("PRIVATE_RAW_PREFIX " + _MAP_QUOTE + " PRIVATE_RAW_SUFFIX").encode()
    path, spec, _inventory = _queued_run(
        tmp_path, bodies=(source,), mode="deep",
    )
    stop_event = threading.Event()

    def cancel_during_read(_poll, _cancel, _request):
        stop_event.set()

    result = run_worker(
        spec.run_id,
        stop_event,
        path=path,
        phase1_dependencies=Phase1Dependencies(extract=_success_extract),
        phase2_dependencies=_dependencies(
            FakeClient(chats=(
                _chat_result(OllamaStatus.SUCCESS, _MAP_RESPONSE),
            )),
            FakeClock(),
        ),
        read_reduce_dependencies=_read_dependencies(
            FakeClient(
                chats=(_chat_result(OllamaStatus.CANCELLED_UNVERIFIED),),
                chat_hook=cancel_during_read,
            ),
            FakeClock(),
        ),
        preflight=lambda _context, _cancel: _SUCCESS,
    )
    assert result.outcome is WorkerOutcome.CANCELLED
    assert _rows(
        path, "SELECT state FROM analyst_read_contact",
    ) == (("cancelled_unverified",),)
    fence = current_lease(path=path)
    assert fence is not None and fence.run_id == spec.run_id
