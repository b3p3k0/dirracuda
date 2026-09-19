"""R4c per-run Analyst model identity acceptance tests."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from experimental.analyst.models import ANALYST_DEFAULTS
from experimental.analyst.ollama_client import OllamaClient
from experimental.analyst.ollama_contract import (
    MODEL_DIGEST,
    MODEL_TAG,
    ContractError,
    OllamaIdentity,
    OllamaStatus,
    PromptKind,
    TagsCheckResult,
    build_chat_request,
    build_read_chat_request,
    build_repair_chat_request,
)
from experimental.analyst.phase2 import (
    Phase2Error,
    Phase2Failure,
    _build_request,
    run_phase2,
)
from experimental.analyst.service import (
    AnalystServiceError,
    DirectoryRunRequest,
    ServiceFailure,
    create_directory_run,
    create_manifest_run,
)
from experimental.analyst.store import load_worker_run, open_connection
from shared.extract_manifest import ExtractSummaryReference, ExtractSummarySource
from shared.path_service import get_paths
from shared.tests.test_analyst_c14 import _db_row, _main_db, _summary
from shared.tests.test_analyst_c9_client import (
    FakeResponse,
    FakeSession,
    _chat_wire,
    _encoded,
)
from shared.tests.test_analyst_c11_engine import (
    FakeClient,
    FakeClock,
    _dependencies,
    _selected,
)


_NONCE = "FENCE_0123456789ABCDEF"
_MODEL_TAG = "qwen3.6:14b"
_MODEL_DIGEST = "1" * 64
_CHANGED_DIGEST = "2" * 64


def _request(tmp_path: Path) -> DirectoryRunRequest:
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    (source / "public.txt").write_text("public", encoding="utf-8")
    return DirectoryRunRequest(source, output, "Public R4c")


def _record_model(path: Path, run_id: str) -> None:
    conn = open_connection(path)
    try:
        conn.execute(
            "UPDATE analyst_runs SET model_tag=?,model_digest=? WHERE run_id=?",
            (_MODEL_TAG, _MODEL_DIGEST, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_nondefault_model_drives_request_and_stream_parser() -> None:
    builders = (
        build_chat_request,
        build_repair_chat_request,
        build_read_chat_request,
    )
    for builder in builders:
        request = builder(
            "public",
            nonce=_NONCE,
            model_tag=_MODEL_TAG,
            model_digest=_MODEL_DIGEST,
        )
        assert request.model_tag == _MODEL_TAG
        assert request.model_digest == _MODEL_DIGEST
        assert request.payload()["model"] == _MODEL_TAG

    request = build_chat_request(
        "public",
        nonce=_NONCE,
        model_tag=_MODEL_TAG,
        model_digest=_MODEL_DIGEST,
    )
    client = OllamaClient(
        session=FakeSession(FakeResponse([_chat_wire(model=_MODEL_TAG)])),
    )
    result = client.chat(
        request,
        expected_sha256=request.request_sha256,
        cancel=lambda: False,
    )
    assert result.status is OllamaStatus.SUCCESS


def test_worker_preflight_fails_closed_when_tag_digest_changed(
    tmp_path: Path,
) -> None:
    path, context, handoff = _selected(tmp_path)
    _record_model(path, context.run_id)
    context = load_worker_run(context.run_id, path=path)

    class ChangedDigestClient(FakeClient):
        def check_tags(self, expected, *, cancel, poll=None) -> TagsCheckResult:
            assert expected.model_tag == context.model_tag
            assert expected.model_digest == context.model_digest
            assert not cancel()
            if poll is not None:
                poll()
            self.calls.append(("tags", expected))
            return TagsCheckResult(
                OllamaStatus.SUCCESS, model_digest=_CHANGED_DIGEST,
            )

    client = ChangedDigestClient(chats=())
    with pytest.raises(Phase2Error) as captured:
        run_phase2(
            context,
            handoff,
            threading.Event(),
            path=path,
            dependencies=_dependencies(client, FakeClock()),
        )
    assert captured.value.code is Phase2Failure.MODEL_DIGEST_MISMATCH
    assert str(captured.value) == "model_digest_mismatch"
    assert [kind for kind, _value in client.calls] == ["version", "tags"]


def test_tags_check_returns_observed_digest_for_the_run_tag() -> None:
    response = FakeResponse(
        [_encoded({"models": [{
            "name": _MODEL_TAG,
            "model": _MODEL_TAG,
            "digest": _CHANGED_DIGEST,
        }]})],
        content_type="application/json",
    )
    result = OllamaClient(session=FakeSession(response)).check_tags(
        OllamaIdentity(model_tag=_MODEL_TAG, model_digest=_MODEL_DIGEST),
        cancel=lambda: False,
    )
    assert result == TagsCheckResult(
        OllamaStatus.SUCCESS, model_digest=_CHANGED_DIGEST,
    )


def test_cloud_tag_rejected_at_creation_and_request_build(tmp_path: Path) -> None:
    request = _request(tmp_path)
    with pytest.raises(AnalystServiceError) as captured:
        create_directory_run(
            request,
            model_tag="qwen3.6:27b-cloud",
            model_digest=_MODEL_DIGEST,
            path=tmp_path / "analyst.db",
        )
    assert captured.value.code is ServiceFailure.CONTRACT

    for builder in (
        build_chat_request,
        build_repair_chat_request,
        build_read_chat_request,
    ):
        with pytest.raises(ContractError, match="model identity"):
            builder(
                "public",
                nonce=_NONCE,
                model_tag="qwen3.6:27b-cloud",
                model_digest=_MODEL_DIGEST,
            )


def test_run_creation_defaults_to_the_benchmarked_model(tmp_path: Path) -> None:
    db = tmp_path / "analyst.db"
    run_id, _inventory = create_directory_run(
        _request(tmp_path),
        path=db,
        run_id_factory=lambda _size: "a" * 32,
    )
    context = load_worker_run(run_id, path=db)
    assert (context.model_tag, context.model_digest) == (MODEL_TAG, MODEL_DIGEST)
    assert (context.model_tag, context.model_digest) == (
        ANALYST_DEFAULTS.model_tag,
        ANALYST_DEFAULTS.model_digest,
    )


def test_run_creation_records_a_nondefault_model(tmp_path: Path) -> None:
    db = tmp_path / "analyst.db"
    run_id, _inventory = create_directory_run(
        _request(tmp_path),
        model_tag=_MODEL_TAG,
        model_digest=_MODEL_DIGEST,
        path=db,
        run_id_factory=lambda _size: "b" * 32,
    )
    context = load_worker_run(run_id, path=db)
    assert (context.model_tag, context.model_digest) == (
        _MODEL_TAG,
        _MODEL_DIGEST,
    )


def test_manifest_run_creation_records_a_nondefault_model(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "saved"
    root.mkdir()
    item = root / "public.txt"
    item.write_text("public", encoding="utf-8")
    main_db = tmp_path / "main.db"
    _main_db(main_db, [_db_row(7, _summary([item]))])
    analyst_db = tmp_path / "analyst.db"
    paths = get_paths(home_root=tmp_path / "home")
    monkeypatch.setattr(
        "experimental.analyst.service.get_paths", lambda: paths,
    )
    run_id, _manifest = create_manifest_run(
        ExtractSummaryReference(7, None, ExtractSummarySource.PRIMARY_DB),
        main_db_path=main_db.absolute(),
        output_base=None,
        report_label="Public R4c manifest",
        model_tag=_MODEL_TAG,
        model_digest=_MODEL_DIGEST,
        path=analyst_db.absolute(),
        run_id_factory=lambda _size: "c" * 32,
    )
    context = load_worker_run(run_id, path=analyst_db)
    assert (context.model_tag, context.model_digest) == (
        _MODEL_TAG,
        _MODEL_DIGEST,
    )


def test_request_sha256_reconstruction_uses_durable_run_model_tag(
    tmp_path: Path,
) -> None:
    path, context, handoff = _selected(tmp_path)
    _record_model(path, context.run_id)
    context = load_worker_run(context.run_id, path=path)
    snapshot = next(item for item in handoff.files if item.chunks)
    chunk = snapshot.chunks[0]
    custom = _build_request(
        context, chunk, PromptKind.PRIMARY, "public selected text",
    )
    default = _build_request(
        replace(
            context,
            model_tag=ANALYST_DEFAULTS.model_tag,
            model_digest=ANALYST_DEFAULTS.model_digest,
        ),
        chunk,
        PromptKind.PRIMARY,
        "public selected text",
    )

    assert custom.payload()["model"] == context.model_tag
    assert custom.request_sha256 == hashlib.sha256(custom.body).hexdigest()
    assert custom.request_sha256 != default.request_sha256
