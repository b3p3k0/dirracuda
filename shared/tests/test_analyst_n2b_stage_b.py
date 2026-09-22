"""N2b stage B: run identity plumbing, provenance, and the backend registry."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from experimental.analyst.backends import (
    BackendError,
    BackendKind,
    IdentityKind,
    OllamaBackend,
    build_backend,
    supported_kinds,
)
from experimental.analyst.store import RunSpec, create_run, open_connection
from experimental.analyst.worker_preflight import current_parser_bundle_mapping

_DIGEST = "a" * 64


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------

def test_only_ollama_is_serviceable_until_the_adapter_lands():
    assert supported_kinds() == (BackendKind.OLLAMA,)


def test_building_the_ollama_backend_binds_the_loopback_default():
    backend = build_backend(BackendKind.OLLAMA)
    assert backend.kind is BackendKind.OLLAMA
    assert backend.endpoint.base_url == "http://127.0.0.1:11434"


def test_a_kind_may_be_given_as_a_string():
    assert build_backend("ollama").kind is BackendKind.OLLAMA


def test_an_unknown_kind_is_refused():
    with pytest.raises(BackendError, match="not a known transport"):
        build_backend("vllm")


def test_a_known_but_unserviceable_kind_says_so():
    with pytest.raises(BackendError, match="not available in this build"):
        build_backend(BackendKind.OPENAI_COMPAT)


def test_the_ollama_backend_forwards_to_its_client():
    calls = []

    class _Fake:
        endpoint = "sentinel"

        def chat(self, *args, **kwargs):
            calls.append(("chat", args, kwargs))
            return "chatted"

        def list_models(self):
            calls.append(("list_models", (), {}))
            return ()

        def cancel_current(self):
            calls.append(("cancel_current", (), {}))

    backend = OllamaBackend(client=_Fake())
    assert backend.chat("request", cancel=None) == "chatted"
    assert backend.list_models() == ()
    backend.cancel_current()
    assert [name for name, _, _ in calls] == ["chat", "list_models", "cancel_current"]
    assert backend.endpoint == "sentinel"


def test_an_ollama_tag_and_digest_is_a_verified_identity():
    identity = OllamaBackend.identity_for("qwen3.6:27b", _DIGEST)
    assert identity.kind is IdentityKind.DIGEST
    assert identity.is_verified is True
    assert identity.digest == _DIGEST


# --------------------------------------------------------------------------
# Provenance bundle (review finding M2)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["backends.__init__.py", "backends.base.py", "endpoint.py", "ollama_client.py"],
)
def test_the_transport_is_covered_by_the_run_provenance_bundle(name):
    """A run must record the code that talked to the model, and the module
    that decided which server that was."""
    files = current_parser_bundle_mapping()["files"]
    assert name in files
    assert len(files[name]) == 64


# --------------------------------------------------------------------------
# A reported-identity run can now be created (review finding B2)
# --------------------------------------------------------------------------

def _spec(**over):
    base = dict(
        run_id="r" * 32, mode="fast", source_mode="unknown",
        source_root="/s", output_root="/o", source_identity={},
        report_label="L", model_tag="qwen3.6:27b", model_digest=_DIGEST,
        worksheet_version="v2", prompt_sha256="b" * 64,
        response_schema_sha256="c" * 64, detector_rules_version="v1",
        detector_rules_sha256="d" * 64, parser_bundle={}, chunk_chars=8000,
        overlap_chars=256, num_ctx=8192, num_predict=1024,
        isolation_mode="strict", reduced_isolation_ack=False,
    )
    base.update(over)
    return RunSpec(**base)


def _inventory():
    from experimental.analyst.inventory import InventoryResult

    return InventoryResult(
        files=(), exclusions=(), root_device=1, root_inode=1, root_mount_id=1,
    )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    from experimental.analyst.store import initialize_database

    path = tmp_path / "analyst.db"
    initialize_database(path)
    return path


def test_a_digest_run_is_created_with_its_kind(db: Path):
    create_run(_spec(identity_kind="digest"), _inventory(), path=db)
    conn = open_connection(db, read_only=True)
    try:
        row = conn.execute(
            "SELECT model_digest,identity_kind,profile_id,backend_kind "
            "FROM analyst_runs"
        ).fetchone()
    finally:
        conn.close()
    assert row["model_digest"] == _DIGEST
    assert row["identity_kind"] == "digest"


def test_a_reported_run_can_finally_be_created(db: Path):
    """Before this, RunSpec.model_digest was a required str, so the only path
    that makes a run could not make a llama.cpp one."""
    create_run(
        _spec(
            model_tag="qwen3.8-27b", model_digest=None, identity_kind="reported",
            profile_id=None, backend_kind="openai",
            model_path="/opt/llm/x.gguf", model_n_ctx=32768,
            server_fingerprint="b1-f280b26",
        ),
        _inventory(),
        path=db,
    )
    conn = open_connection(db, read_only=True)
    try:
        row = conn.execute(
            "SELECT model_tag,model_digest,identity_kind,backend_kind,"
            "model_path,model_n_ctx,server_fingerprint FROM analyst_runs"
        ).fetchone()
    finally:
        conn.close()
    assert row["model_digest"] is None
    assert row["identity_kind"] == "reported"
    assert row["backend_kind"] == "openai"
    assert row["model_path"] == "/opt/llm/x.gguf"
    assert row["model_n_ctx"] == 32768
    assert row["server_fingerprint"] == "b1-f280b26"


def test_a_run_records_the_profile_it_came_from(db: Path):
    """M1: profile_id and backend_kind were dead columns before this."""
    from experimental.analyst.profiles import ensure_default_profile

    profile = ensure_default_profile(path=db)
    create_run(
        _spec(profile_id=profile.profile_id, backend_kind="ollama",
              identity_kind="digest"),
        _inventory(),
        path=db,
    )
    conn = open_connection(db, read_only=True)
    try:
        row = conn.execute(
            "SELECT profile_id,backend_kind FROM analyst_runs"
        ).fetchone()
    finally:
        conn.close()
    assert row["profile_id"] == profile.profile_id
    assert row["backend_kind"] == "ollama"


def test_a_reported_run_with_a_digest_is_refused_at_the_schema(db: Path):
    with pytest.raises(sqlite3.IntegrityError):
        create_run(
            _spec(identity_kind="reported", model_digest=_DIGEST),
            _inventory(), path=db,
        )


def test_an_existing_run_still_creates_without_the_new_fields(db: Path):
    """Every pre-N2b caller passes none of them."""
    create_run(_spec(), _inventory(), path=db)
    conn = open_connection(db, read_only=True)
    try:
        row = conn.execute(
            "SELECT identity_kind,profile_id,backend_kind FROM analyst_runs"
        ).fetchone()
    finally:
        conn.close()
    assert tuple(row) == (None, None, None)
