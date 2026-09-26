"""N3: a run is pinned to the server that made it (contract 6.4)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from experimental.analyst.backend_select import (
    ProfileUnreachable,
    RunPinMismatch,
    backend_for_run,
    run_backend_spec,
)
from experimental.analyst.inventory import InventoryResult
from experimental.analyst.profiles import create_profile, ensure_default_profile
from experimental.analyst.store import RunSpec, create_run, initialize_database

_DIGEST = "a" * 64
_MIMIR = "http://100.125.197.36:9292"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "analyst.db"
    initialize_database(path)
    return path


def _inventory():
    return InventoryResult(
        files=(), exclusions=(), root_device=1, root_inode=1, root_mount_id=1,
    )


def _run(db: Path, run_id: str, **over):
    base = dict(
        run_id=run_id, mode="fast", source_mode="unknown", source_root="/s",
        output_root="/o", source_identity={}, report_label="L",
        model_tag="qwen3.6:27b", model_digest=_DIGEST, worksheet_version="v2",
        prompt_sha256="b" * 64, response_schema_sha256="c" * 64,
        detector_rules_version="v1", detector_rules_sha256="d" * 64,
        parser_bundle={}, chunk_chars=8000, overlap_chars=256, num_ctx=8192,
        num_predict=1024, isolation_mode="strict", reduced_isolation_ack=False,
    )
    base.update(over)
    create_run(RunSpec(**base), _inventory(), path=db)


def test_an_exact_match_resumes(db: Path):
    profile = ensure_default_profile(path=db)
    _run(db, "a" * 32, profile_id=profile.profile_id, backend_kind="ollama")
    kind, endpoint, *_ = run_backend_spec("a" * 32, path=db)
    assert endpoint == profile.endpoint_url


def test_a_deleted_profile_holds_the_run_rather_than_retargeting(db: Path):
    """Contract 6.4: hold it resumable, do not silently run it elsewhere."""
    profile = create_profile("mimir", _MIMIR, backend_kind="openai",
                             plaintext_ack=True, path=db)
    _run(db, "b" * 32, profile_id=profile.profile_id, backend_kind="openai",
         model_tag="qwen3.8-27b", model_digest=None, identity_kind="reported")
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM analyst_llm_profile")
    finally:
        conn.close()
    with pytest.raises(ProfileUnreachable, match="no longer configured"):
        run_backend_spec("b" * 32, path=db)


def test_a_profile_that_switched_backend_refuses_the_resume(db: Path):
    """One report comes from one model. A swapped backend is not that."""
    profile = create_profile("box", _MIMIR, backend_kind="openai",
                             plaintext_ack=True, path=db)
    _run(db, "c" * 32, profile_id=profile.profile_id, backend_kind="openai",
         model_tag="qwen3.8-27b", model_digest=None, identity_kind="reported")
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        conn.execute("UPDATE analyst_llm_profile SET backend_kind='ollama'")
    finally:
        conn.close()
    with pytest.raises(RunPinMismatch, match="Start a new run"):
        backend_for_run("c" * 32, path=db)


def test_a_pre_n2b_run_without_a_profile_is_unaffected(db: Path):
    """It never named a server, so there is nothing to mismatch."""
    _run(db, "d" * 32)
    kind, endpoint, ack, model, pin = run_backend_spec("d" * 32, path=db)
    assert endpoint == "http://127.0.0.1:11434"
    assert (ack, model, pin) == (False, None, None)


def test_the_mismatch_message_offers_a_new_run(db: Path):
    profile = create_profile("box", _MIMIR, backend_kind="openai",
                             plaintext_ack=True, path=db)
    _run(db, "e" * 32, profile_id=profile.profile_id, backend_kind="openai",
         model_tag="m", model_digest=None, identity_kind="reported")
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        conn.execute("UPDATE analyst_llm_profile SET backend_kind='ollama'")
    finally:
        conn.close()
    with pytest.raises(RunPinMismatch) as excinfo:
        run_backend_spec("e" * 32, path=db)
    assert "new run" in str(excinfo.value)
