"""Offline production-flow proof for the public C0B-2 benchmark.

The fake below implements only the HTTP session seam.  Planning, durable execution,
bounded response parsing, adaptive decisions, receipts, and terminal verification all
run through their production implementations.
"""
from __future__ import annotations

import builtins
import getpass
import json
import os
import re
import socket
import sqlite3
import stat
from pathlib import Path
from typing import Any, Callable, Mapping, get_args
from urllib.parse import quote

import pytest

from scripts.analyst_benchmark import c0b2_runtime as runtime
from scripts.analyst_benchmark import c0b2_runtime_d as runtime_d
from scripts.analyst_benchmark import c0b2_runtime_f as runtime_f
from scripts.analyst_benchmark import c0b2_transport as transport_module
from scripts.analyst_benchmark import goldset
from scripts.analyst_benchmark.c0b2_checkpoint import (
    CapExceeded,
    Checkpoint,
    sha256_json,
)
from scripts.analyst_benchmark.c0b2_executor import (
    DurableExecutor,
    FakeResponse,
    WorkRequest,
)
from scripts.analyst_benchmark.c0b2_fsprobe import (
    GlobalExecutionLock,
    backup_snapshot,
    verify_readonly,
)
from scripts.analyst_benchmark.c0b2_plan import MODELS
from scripts.analyst_benchmark.c0b2_schema import CATEGORIES, canonical_json
from scripts.analyst_benchmark.c0b2_public_schema import (
    DInconclusiveReason,
    FInconclusiveReason,
    FAILURE_REASON_BY_TERMINAL,
)
from scripts.analyst_benchmark.c0b2_transport import (
    BoundedOllamaTransport,
    RequestSpec,
)
from shared import path_service


class _InjectedCrash(BaseException):
    """Simulate abrupt process loss without entering normal retry handling."""


class _RawBody:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.streamed = False

    def stream(self, *, amt: int, decode_content: bool):
        assert amt == 64 * 1024
        assert decode_content is False
        self.streamed = True
        # Exercise the bounded adapter's incremental frame/body assembly rather than
        # handing it a preassembled response.
        first = min(7, len(self.body))
        second = min(first + 23, len(self.body))
        for part in (self.body[:first], self.body[first:second], self.body[second:]):
            if part:
                yield part


class _HttpResponse:
    def __init__(self, body: bytes, content_type: str) -> None:
        self.status_code = 200
        self.headers = {"Content-Type": content_type}
        self.raw = _RawBody(body)
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


def _identifier_labels() -> tuple[tuple[str, str], ...]:
    """Return exact positive fixture identifiers without treating near misses as labels."""
    value = json.loads(goldset.MANIFEST.read_text(encoding="utf-8"))
    labels: set[tuple[str, str]] = set()
    for row in value["documents"]:
        categories = row["categories_present"]
        if not categories:
            continue
        for identifier in row["expected_identifiers"]:
            if len(categories) == 1:
                category = categories[0]
            elif "@" in identifier:
                category = "contact"
            elif re.fullmatch(r"\d{3}-\d{2}-\d{4}", identifier):
                category = "pii"
            elif re.fullmatch(r"\d{15,16}", identifier):
                category = "financial"
            else:  # The only multi-category fixtures are the frozen injection rows.
                raise AssertionError(f"unclassified public identifier: {identifier}")
            labels.add((category, identifier))
    return tuple(sorted(labels, key=lambda row: (-len(row[1]), row)))


def _answer(prompt: str, worksheet: str, *, perfect: bool,
            labels: tuple[tuple[str, str], ...]) -> str:
    matches = []
    if perfect:
        # One exact quote per category is sufficient for every frozen gate and keeps
        # output-truncation fixtures within the worksheet's bounded finding count.
        present = [(category, identifier) for category, identifier in labels
                   if identifier in prompt]
        matches = [next(row for row in present if row[0] == category)
                   for category in CATEGORIES
                   if any(row[0] == category for row in present)]
    assessment = "findings_present" if matches else "no_findings"
    base: dict[str, Any] = {
        "document_type": "record", "subject": "", "assessment": assessment,
    }
    if worksheet == "v1":
        base["categories"] = [{
            "category": category,
            "present": any(found == category for found, _quote in matches),
            "evidence": [{"quote": quote, "offset": 0}
                         for found, quote in matches if found == category],
        } for category in CATEGORIES]
    else:
        base["findings"] = [
            {"category": category, "quote": quote, "offset": 0}
            for category, quote in matches
        ]
    return canonical_json(base).decode("utf-8")


class _FakeOllamaSession:
    """Deterministic HTTP-only Ollama double used by the real bounded adapter."""

    def __init__(self, labels: tuple[tuple[str, str], ...], *,
                 failure_mode: str | None = None,
                 no_findings_stages: frozenset[str] = frozenset(),
                 inject_crash: bool = True,
                 inject_stream_error: bool = True) -> None:
        if failure_mode not in {None, "safety", "provenance"}:
            raise ValueError("unknown fake-session failure mode")
        self.trust_env = True
        self.max_redirects = 30
        self.labels = labels
        self.failure_mode = failure_mode
        self.no_findings_stages = no_findings_stages
        self.current_request: Any = None
        self.current_spec: RequestSpec | None = None
        self.paths: list[str] = []
        self.responses: list[_HttpResponse] = []
        self.crash_next_chat = inject_crash
        self.crash_count = 0
        self.stream_error_next_chat = inject_stream_error
        self.stream_error_count = 0

    def resolver(self, delegate: Callable[[Any], RequestSpec]) -> Callable[[Any], RequestSpec]:
        def tracked(request: Any) -> RequestSpec:
            spec = delegate(request)
            self.current_request = request
            self.current_spec = spec
            return spec

        return tracked

    def request(self, method: str, url: str, **kwargs: Any) -> _HttpResponse:
        assert url.startswith(runtime.OLLAMA_ENDPOINT + "/api/")
        assert kwargs["allow_redirects"] is False
        assert kwargs["proxies"] == {"http": None, "https": None}
        assert kwargs["stream"] is True
        assert kwargs["timeout"] == (
            transport_module.CONNECT_TIMEOUT_SECONDS,
            transport_module.IDLE_READ_TIMEOUT_SECONDS)
        assert kwargs["headers"]["Accept-Encoding"] == "identity"
        assert kwargs["headers"]["Content-Type"] == "application/json"
        path = url.removeprefix(runtime.OLLAMA_ENDPOINT)
        self.paths.append(path)
        spec = self.current_spec
        assert spec is not None

        if path == "/api/chat":
            assert method == "POST"
            assert kwargs["headers"]["Accept"] == "application/x-ndjson"
            assert kwargs["json"] == spec.payload
            if self.crash_next_chat:
                self.crash_next_chat = False
                self.crash_count += 1
                raise _InjectedCrash("one-shot fake process loss")
            payload = kwargs["json"]
            if self.stream_error_next_chat:
                self.stream_error_next_chat = False
                self.stream_error_count += 1
                partial = {
                    "model": payload["model"],
                    "message": {"role": "assistant", "content": "{"},
                    "done": False,
                }
                error = {"error": "temporary fake stream failure"}
                response = _HttpResponse(
                    b"\n".join(json.dumps(row, separators=(",", ":")).encode()
                                for row in (partial, error)) + b"\n",
                    "application/x-ndjson")
                self.responses.append(response)
                return response
            request = self.current_request
            perfect = not (
                isinstance(request, WorkRequest)
                and (request.stage in self.no_findings_stages
                     or request.stage == "C"
                     and payload["model"] != MODELS[0][0])
            )
            answer = _answer(
                payload["messages"][0]["content"], str(spec.worksheet),
                perfect=perfect, labels=self.labels)
            midpoint = max(1, len(answer) // 2)
            frames = [{
                "model": payload["model"],
                "message": {"role": "assistant", "content": answer[:midpoint]},
                "done": False,
            }, {
                "model": payload["model"],
                "message": {"role": "assistant", "content": answer[midpoint:]},
                "done": True, "done_reason": "stop",
                # A small measured prompt makes D3 choose 4096, exercising D4.
                "prompt_eval_count": 100, "eval_count": 1,
            }]
            response = _HttpResponse(
                b"\n".join(json.dumps(frame, separators=(",", ":")).encode("utf-8")
                            for frame in frames) + b"\n",
                "application/x-ndjson")
            self.responses.append(response)
            return response

        assert kwargs["headers"]["Accept"] == "application/json"
        if path == "/api/version":
            assert method == "GET" and kwargs["json"] is None
            value: Mapping[str, Any] = {"version": runtime.OLLAMA_VERSION}
        elif path == "/api/tags":
            assert method == "GET" and kwargs["json"] is None
            value = ({"models": []} if self.failure_mode == "provenance" else
                     {"models": [
                         {"name": model, "model": model, "digest": digest}
                         for model, digest, _think in MODELS
                     ]})
        elif path == "/api/show":
            assert method == "POST"
            assert kwargs["json"] == {
                "model": spec.expected_model, "verbose": False}
            value = {}
        elif path == "/api/ps":
            assert method == "GET" and kwargs["json"] is None
            assert spec.expected_model is not None
            assert spec.expected_digest is not None
            value = {"models": [{
                "name": spec.expected_model, "model": spec.expected_model,
                "digest": spec.expected_digest, "size": 1, "size_vram": 0,
                "context_length": 16384,
            }]}
        else:  # pragma: no cover - exact production paths are asserted by the test
            raise AssertionError(f"unexpected Ollama path: {path}")
        content_type = ("text/plain" if self.failure_mode == "safety"
                        and path == "/api/version" else "application/json")
        response = _HttpResponse(
            json.dumps(value, separators=(",", ":")).encode("utf-8"),
            content_type)
        self.responses.append(response)
        return response


_B5_TERMINAL_PROOFS = {
    "SELECTED":
        "scripts/tests/test_analyst_c0b2_public_flow.py::"
        "test_selected_public_flow_uses_bounded_transport_and_recovers_one_crash",
    "no_stage_c_survivor":
        "scripts/tests/test_analyst_c0b2_runtime.py::"
        "test_public_stage_c_runs_exact_plan_and_finalizes_no_survivor",
    "no_d1_output_budget_survivor":
        "scripts/tests/test_analyst_c0b2_runtime_d.py::"
        "test_d_inconclusive_crash_window_rebuilds_terminal_before_receipt",
    "no_d2_chunk_survivor":
        "scripts/tests/test_analyst_c0b2_runtime_d.py::"
        "test_each_later_d_inconclusive_terminal_is_persisted_and_receiptable",
    "no_d3_context_survivor":
        "scripts/tests/test_analyst_c0b2_runtime_d.py::"
        "test_each_later_d_inconclusive_terminal_is_persisted_and_receiptable",
    "no_d4_confirmation_finalist":
        "scripts/tests/test_analyst_c0b2_runtime_d.py::"
        "test_each_later_d_inconclusive_terminal_is_persisted_and_receiptable",
    "no_seed1_qualifier":
        "scripts/tests/test_analyst_c0b2_runtime_f.py::"
        "test_seed1_no_qualifier_persists_exact_terminal_and_calls_validator",
    "no_all_seed_qualifier":
        "scripts/tests/test_analyst_c0b2_runtime_f.py::"
        "test_all_seed_no_qualifier_persists_exact_terminal_and_calls_validator",
    "ranking_not_decisive":
        "scripts/tests/test_analyst_c0b2_runtime_f.py::"
        "test_all_seed_finalizer_rebuilds_authority_inside_write_transaction",
    "complete_corpus_acceptance_failed":
        "scripts/tests/test_analyst_c0b2_runtime_f.py::"
        "test_failed_acceptance_persists_exact_terminal_and_calls_validator",
    "FAILED_SAFETY":
        "scripts/tests/test_analyst_c0b2_public_flow.py::"
        "test_bounded_adapter_freezes_transport_originated_terminal",
    "BLOCKED_PROVENANCE":
        "scripts/tests/test_analyst_c0b2_public_flow.py::"
        "test_bounded_adapter_freezes_transport_originated_terminal",
    "BLOCKED_BUDGET":
        "scripts/tests/test_analyst_c0b2_public_flow.py::"
        "test_public_invocation_budget_blocks_before_transport_dispatch",
    "BLOCKED_FILESYSTEM":
        "scripts/tests/test_analyst_c0b2_checkpoint.py::"
        "test_invocation_guard_rechecks_filesystem_before_recovery",
    "ABANDONED":
        "scripts/tests/test_analyst_c0b2_runtime_common.py::"
        "test_public_abandon_is_locked_receipted_and_idempotent",
}

_B5_QUALITY_RECEIPT_PROOFS = {
    "SELECTED": _B5_TERMINAL_PROOFS["SELECTED"],
    "no_stage_c_survivor": _B5_TERMINAL_PROOFS["no_stage_c_survivor"],
    "no_d1_output_budget_survivor":
        _B5_TERMINAL_PROOFS["no_d1_output_budget_survivor"],
    "no_d2_chunk_survivor": _B5_TERMINAL_PROOFS["no_d2_chunk_survivor"],
    "no_d3_context_survivor": _B5_TERMINAL_PROOFS["no_d3_context_survivor"],
    "no_d4_confirmation_finalist":
        _B5_TERMINAL_PROOFS["no_d4_confirmation_finalist"],
    "no_seed1_qualifier": _B5_TERMINAL_PROOFS["SELECTED"],
    "no_all_seed_qualifier": _B5_TERMINAL_PROOFS["SELECTED"],
    "ranking_not_decisive": _B5_TERMINAL_PROOFS["SELECTED"],
    "complete_corpus_acceptance_failed": _B5_TERMINAL_PROOFS["SELECTED"],
}


def _install_no_external_access_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("public fake-session proof attempted external access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(
        transport_module.requests, "Session",
        lambda: (_ for _ in ()).throw(
            AssertionError("bounded transport constructed a real HTTP session")))
    monkeypatch.setattr(
        path_service, "get_paths",
        lambda: (_ for _ in ()).throw(
            AssertionError("public flow requested user-data paths")))
    monkeypatch.setattr(builtins, "input", forbidden)
    monkeypatch.setattr(getpass, "getpass", forbidden)


def test_b5_terminal_proof_matrix_is_closed_and_names_existing_nodes() -> None:
    quality = {
        "SELECTED", "no_stage_c_survivor", *get_args(DInconclusiveReason),
        *get_args(FInconclusiveReason),
    }
    assert set(_B5_TERMINAL_PROOFS) == quality | set(FAILURE_REASON_BY_TERMINAL)
    assert set(_B5_QUALITY_RECEIPT_PROOFS) == quality
    for node in {*_B5_TERMINAL_PROOFS.values(),
                 *_B5_QUALITY_RECEIPT_PROOFS.values()}:
        relative, function = node.split("::", 1)
        source = runtime.REPO_ROOT / relative
        assert source.is_file(), node
        assert f"def {function}(" in source.read_text(encoding="utf-8"), node


def test_public_invocation_budget_blocks_before_transport_dispatch(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-public-flow-budget"
    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    point = Checkpoint.open(runtime._checkpoint_path(run_id, root), root)
    calls: list[str] = []
    try:
        with GlobalExecutionLock(root) as lock:
            point.conn.execute("UPDATE run_state SET state='RUNNING' WHERE id=1")
            for _ in range(3):
                point.claim_invocation("C")
            executor = DurableExecutor(
                point, lock,
                lambda *_args: calls.append("transport") or FakeResponse("unused"),
                enforce_public_budget_contract=True)
            with pytest.raises(CapExceeded):
                executor.recover_and_start("C")
        assert point.state() == "BLOCKED_BUDGET"
        assert calls == [] and point.usage()["total"] == 0
        assert point.conn.execute(
            "SELECT count(*) FROM public_artifacts "
            "WHERE terminal='BLOCKED_BUDGET'").fetchone()[0] == 2
    finally:
        point.close()


@pytest.mark.parametrize(("failure_mode", "terminal", "last_path"), [
    ("safety", "FAILED_SAFETY", "/api/version"),
    ("provenance", "BLOCKED_PROVENANCE", "/api/tags"),
])
def test_bounded_adapter_freezes_transport_originated_terminal(
        failure_mode: str, terminal: str, last_path: str,
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Reach dedicated wire failures through the real bounded adapter."""
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    _install_no_external_access_guards(monkeypatch)
    root = tmp_path / "bench"
    run_id = f"c0b2-public-flow-{failure_mode}"
    session = _FakeOllamaSession(_identifier_labels(), failure_mode=failure_mode)

    def factory(resolver: Callable[[Any], RequestSpec],
                header: Mapping[str, Any]) -> BoundedOllamaTransport:
        transport = BoundedOllamaTransport(
            session.resolver(resolver), endpoint=header["ollama_endpoint"],
            session=session)
        assert session.trust_env is False and session.max_redirects == 0
        return transport

    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    result = runtime.run_public_stage_c(
        run_id, benchmark_root=root, transport_factory=factory)
    assert result["state"] == terminal
    assert session.paths[-1] == last_path
    assert "/api/chat" not in session.paths
    assert session.responses and all(
        response.close_count >= 1 for response in session.responses)
    if failure_mode == "safety":
        assert session.responses[-1].raw.streamed is False, \
            "content type must fail before body parsing"
    else:
        assert all(response.raw.streamed for response in session.responses)

    paths_before_reentry = list(session.paths)
    reentered = runtime.run_public_stage_c(
        run_id, benchmark_root=root,
        transport_factory=lambda *_args: pytest.fail(
            "terminal receipt re-entry must not construct transport"))
    assert reentered["state"] == terminal
    assert session.paths == paths_before_reentry
    status = runtime.public_status(run_id, benchmark_root=root)
    assert status["state"] == terminal
    assert status["backup"]["receipt_present"] is True
    assert runtime.public_verify(run_id, benchmark_root=root)["ok"] is True


def _run_f_inconclusive_receipt_branch(
        root: Path, run_id: str, labels: tuple[tuple[str, str], ...], *,
        first_resume: bool, expected_reason: str) -> None:
    """Drive one exact F owner shape to a receipted terminal without a socket."""
    session = _FakeOllamaSession(
        labels, no_findings_stages=frozenset({"F"}),
        inject_crash=False, inject_stream_error=False)

    def factory(resolver: Callable[[Any], RequestSpec],
                header: Mapping[str, Any]) -> BoundedOllamaTransport:
        transport = BoundedOllamaTransport(
            session.resolver(resolver), endpoint=header["ollama_endpoint"],
            session=session)
        assert session.trust_env is False and session.max_redirects == 0
        return transport

    result = runtime_f.run_public_stage_f(
        run_id, resume=first_resume, benchmark_root=root,
        transport_factory=factory)
    for _ in range(4):
        if result["state"] == "INCONCLUSIVE":
            break
        result = runtime_f.run_public_stage_f(
            run_id, resume=True, benchmark_root=root,
            transport_factory=factory)
    assert result["state"] == "INCONCLUSIVE"
    checkpoint = runtime._checkpoint_path(run_id, root)
    with sqlite3.connect(checkpoint) as conn:
        row = conn.execute(
            "SELECT artifact_json FROM public_artifacts "
            "WHERE artifact_id='stage-f-result'").fetchone()
        receipts = conn.execute(
            "SELECT count(*) FROM backup_receipts").fetchone()[0]
    assert row is not None and json.loads(row[0])["reason"] == expected_reason
    assert receipts >= 1
    assert session.responses and all(
        response.close_count >= 1 for response in session.responses)
    paths_before_reentry = list(session.paths)
    reentered = runtime_f.run_public_stage_f(
        run_id, resume=True, benchmark_root=root,
        transport_factory=lambda *_args: pytest.fail(
            "F terminal re-entry must not construct transport"))
    assert reentered["state"] == "INCONCLUSIVE"
    assert session.paths == paths_before_reentry
    status = runtime.public_status(run_id, benchmark_root=root)
    assert status["backup"]["receipt_present"] is True
    assert runtime.public_verify(run_id, benchmark_root=root)["ok"] is True


def _create_rewind_snapshot(root: Path, run_id: str) -> Path:
    """Capture the canonical run through the production online-backup path."""
    path = runtime._checkpoint_path(run_id, root)
    with GlobalExecutionLock(root) as lock:
        point = Checkpoint.open(path, root)
        try:
            snapshot = backup_snapshot(
                point, point.path.parent / "proof-rewinds", lock=lock)
        finally:
            point.close()
    source_check = verify_readonly(snapshot)
    assert source_check.ok, source_check.errors
    assert snapshot.is_relative_to(path.parent)
    source_stat = snapshot.lstat()
    assert (stat.S_ISREG(source_stat.st_mode)
            and source_stat.st_uid == os.getuid()
            and stat.S_IMODE(source_stat.st_mode) == 0o600)
    return snapshot


def _rewind_checkpoint(root: Path, run_id: str, snapshot: Path) -> None:
    """Rewind the same canonical checkpoint from a verified read-only snapshot."""
    path = runtime._checkpoint_path(run_id, root)
    with GlobalExecutionLock(root):
        source_identity = snapshot.stat(follow_symlinks=False)
        destination_identity = path.stat(follow_symlinks=False)
        assert (stat.S_ISREG(source_identity.st_mode)
                and stat.S_IMODE(source_identity.st_mode) == 0o600)
        assert (stat.S_ISREG(destination_identity.st_mode)
                and stat.S_IMODE(destination_identity.st_mode) == 0o600)
        source_check = verify_readonly(snapshot)
        assert source_check.ok, source_check.errors
        for suffix in ("-wal", "-shm", "-journal"):
            assert not Path(f"{path}{suffix}").exists()
        point = Checkpoint.open(path, root)
        source = sqlite3.connect(
            f"file:{quote(str(snapshot.absolute()), safe='/')}?mode=ro",
            uri=True, timeout=5.0)
        try:
            source.execute("PRAGMA query_only=ON")
            header_raw, header_hash = source.execute(
                "SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
            header = json.loads(header_raw)
            assert canonical_json(header).decode("utf-8") == header_raw
            assert sha256_json(header) == header_hash
            assert header["run_id"] == run_id
            assert header["mount"]["canonical_path"] == str(root.resolve())
            receipt_rows = source.execute(
                "SELECT anchor_hash,receipt_hash,receipt_json "
                "FROM backup_receipts ORDER BY anchor_hash").fetchall()
            source.backup(point.conn)
            assert point.header() == header
            assert point.conn.execute(
                "SELECT anchor_hash,receipt_hash,receipt_json "
                "FROM backup_receipts ORDER BY anchor_hash").fetchall() \
                == receipt_rows
            for anchor_hash, receipt_hash, receipt_json in receipt_rows:
                receipt = runtime._load_receipt(
                    str(receipt_json), str(receipt_hash),
                    anchor_hash=str(anchor_hash))
                runtime._verify_receipt_file(point.path.parent, receipt)
        finally:
            source.close()
            point.close()
        source_after = snapshot.stat(follow_symlinks=False)
        destination_after = path.stat(follow_symlinks=False)
        assert (source_after.st_dev, source_after.st_ino, source_after.st_size,
                source_after.st_mtime_ns) == (
            source_identity.st_dev, source_identity.st_ino,
            source_identity.st_size, source_identity.st_mtime_ns)
        assert (destination_after.st_dev, destination_after.st_ino) == (
            destination_identity.st_dev, destination_identity.st_ino)
        assert (destination_after.st_uid == os.getuid()
                and stat.S_IMODE(destination_after.st_mode) == 0o600)
        destination_check = verify_readonly(path)
        assert destination_check.ok, destination_check.errors
        with Checkpoint.open(path, root) as recovered:
            assert recovered.header() == header
        for suffix in ("-wal", "-shm", "-journal"):
            assert not Path(f"{path}{suffix}").exists()


def test_production_checkpoint_rewind_roundtrip_keeps_canonical_path(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    root, run_id = tmp_path / "bench", "c0b2-public-flow-rewind"
    runtime.create_public_run(benchmark_root=root, run_id=run_id)
    with GlobalExecutionLock(root):
        with Checkpoint.open(runtime._checkpoint_path(run_id, root), root) as point:
            point.transition("RUNNING")
    snapshot = _create_rewind_snapshot(root, run_id)
    with GlobalExecutionLock(root):
        with Checkpoint.open(runtime._checkpoint_path(run_id, root), root) as point:
            point.conn.execute(
                "INSERT INTO events(kind,detail_json,created) "
                "VALUES('post-rewind-marker','{}',0)")
    _rewind_checkpoint(root, run_id, snapshot)
    path = runtime._checkpoint_path(run_id, root)
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT count(*) FROM events WHERE kind='post-rewind-marker'").fetchone() \
            == (0,)
    assert runtime.public_status(run_id, benchmark_root=root)["state"] == "RUNNING"


def test_selected_public_flow_uses_bounded_transport_and_recovers_one_crash(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Drive C→D1→D2→D3→D4→all F seeds→acceptance without a socket."""
    monkeypatch.setattr(runtime, "_require_clean_task_delta", lambda _seal: None)
    _install_no_external_access_guards(monkeypatch)
    benchmark_root = tmp_path / "bench"
    labels = _identifier_labels()
    session = _FakeOllamaSession(labels)
    reads: list[Path] = []
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text

    def tracked_read_bytes(path: Path) -> bytes:
        reads.append(path.resolve())
        return original_read_bytes(path)

    def tracked_read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(path.resolve())
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)
    monkeypatch.setattr(Path, "read_text", tracked_read_text)

    def transport_factory(resolver: Callable[[Any], RequestSpec],
                          header: Mapping[str, Any]) -> BoundedOllamaTransport:
        transport = BoundedOllamaTransport(
            session.resolver(resolver), endpoint=header["ollama_endpoint"],
            session=session)
        assert session.trust_env is False and session.max_redirects == 0
        return transport

    run_id = runtime.create_public_run(
        benchmark_root=benchmark_root, run_id="c0b2-public-flow-selected")
    with pytest.raises(_InjectedCrash, match="one-shot"):
        runtime.run_public_stage_c(
            run_id, benchmark_root=benchmark_root,
            transport_factory=transport_factory)
    c_result = runtime.run_public_stage_c(
        run_id, resume=True, benchmark_root=benchmark_root,
        transport_factory=transport_factory)
    assert c_result["state"] == "PAUSED_STAGE_BOUNDARY"
    assert c_result["survivor_count"] == 1
    assert session.crash_count == 1
    assert session.stream_error_count == 1

    d_result = runtime_d.run_public_stage_d(
        run_id, benchmark_root=benchmark_root,
        transport_factory=transport_factory)
    assert d_result["state"] == "PAUSED_STAGE_BOUNDARY"
    assert d_result["active_plan_key"] == "D4_CONFIRMATION"
    assert d_result["outcome"] == "FINALISTS"
    seed1_rewind = _create_rewind_snapshot(benchmark_root, run_id)
    _run_f_inconclusive_receipt_branch(
        benchmark_root, run_id, labels, first_resume=False,
        expected_reason="no_seed1_qualifier")
    _rewind_checkpoint(benchmark_root, run_id, seed1_rewind)

    cancelled = runtime_f.run_public_stage_f(
        run_id, benchmark_root=benchmark_root,
        transport_factory=transport_factory)
    assert (cancelled["state"], cancelled["active_plan_key"],
            cancelled["outcome"]) == (
        "RUNNING", "F_SEED_1", "CANCELLED_UNVERIFIED")
    f1 = runtime_f.run_public_stage_f(
        run_id, resume=True, benchmark_root=benchmark_root,
        transport_factory=transport_factory)
    assert (f1["state"], f1["active_plan_key"], f1["outcome"]) == (
        "RUNNING", "F_SEED_17", "LATER_SEEDS_ACTIVATED")
    final_seed_rewind = _create_rewind_snapshot(benchmark_root, run_id)
    _run_f_inconclusive_receipt_branch(
        benchmark_root, run_id, labels, first_resume=True,
        expected_reason="no_all_seed_qualifier")
    _rewind_checkpoint(benchmark_root, run_id, final_seed_rewind)
    f17 = runtime_f.run_public_stage_f(
        run_id, resume=True, benchmark_root=benchmark_root,
        transport_factory=transport_factory)
    assert (f17["state"], f17["active_plan_key"], f17["outcome"]) == (
        "RUNNING", "F_SEED_20260804", "F20260804_ACTIVATED")
    f_last = runtime_f.run_public_stage_f(
        run_id, resume=True, benchmark_root=benchmark_root,
        transport_factory=transport_factory)
    assert (f_last["state"], f_last["active_plan_key"], f_last["outcome"]) == (
        "RUNNING", "F_ACCEPTANCE", "ACCEPTANCE_ACTIVATED")
    acceptance_rewind = _create_rewind_snapshot(benchmark_root, run_id)
    _run_f_inconclusive_receipt_branch(
        benchmark_root, run_id, labels, first_resume=True,
        expected_reason="complete_corpus_acceptance_failed")
    _rewind_checkpoint(benchmark_root, run_id, acceptance_rewind)
    selected = runtime_f.run_public_stage_f(
        run_id, resume=True, benchmark_root=benchmark_root,
        transport_factory=transport_factory)
    assert (selected["state"], selected["active_plan_key"], selected["outcome"]) == (
        "SELECTED", "F_ACCEPTANCE", "SELECTED")
    selected_paths = list(session.paths)
    selected_reentry = runtime_f.run_public_stage_f(
        run_id, resume=True, benchmark_root=benchmark_root,
        transport_factory=lambda *_args: pytest.fail(
            "selected terminal re-entry must not construct transport"))
    assert selected_reentry["state"] == "SELECTED"
    assert session.paths == selected_paths

    status = runtime.public_status(run_id, benchmark_root=benchmark_root)
    assert status["state"] == "SELECTED"
    assert status["backup"]["receipt_present"] is True
    assert runtime.public_verify(run_id, benchmark_root=benchmark_root) == {
        "ok": True, "errors": [],
        **{key: status[key] for key in (
            "benchmark_protocol_id", "policy_id", "policy_sha256")},
        "backup": status["backup"],
    }
    assert {"/api/chat", "/api/version", "/api/tags", "/api/show", "/api/ps"} \
        <= set(session.paths)
    assert session.responses
    assert all(response.raw.streamed and response.close_count == 1
               for response in session.responses), \
        "bounded transport did not preserve single-owner response cleanup"
    checkpoint = runtime._checkpoint_path(run_id, benchmark_root)
    with sqlite3.connect(checkpoint) as conn:
        retry_rows = conn.execute(
            "SELECT response FROM attempts WHERE state='RETRYABLE_TRANSPORT'"
        ).fetchall()
    assert retry_rows == [(None,)], "partial stream content became durable evidence"
    allowed_roots = (runtime.REPO_ROOT.resolve(), benchmark_root.resolve())
    assert reads
    unexpected_home_reads = [
        path for path in reads if path.is_relative_to(Path.home().resolve())
        and not any(path.is_relative_to(root) for root in allowed_roots)]
    assert unexpected_home_reads == [], \
        f"public flow read outside public home roots: {unexpected_home_reads}"
