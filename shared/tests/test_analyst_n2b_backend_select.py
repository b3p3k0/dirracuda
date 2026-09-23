"""N2b: a run talks to the server it was created against."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from experimental.analyst.backend_select import (
    BackendSelectionError,
    backend_for_run,
    run_backend_spec,
)
from experimental.analyst.backends import BackendKind
from experimental.analyst.backends.openai_api import OpenAICompatBackend
from experimental.analyst.endpoint import AddressPolicyError
from experimental.analyst.inventory import InventoryResult
from experimental.analyst.ollama_client import OllamaClient
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


def test_a_run_with_no_profile_gets_the_pre_n2b_default(db: Path):
    """Object-for-object what it was before: a raw loopback Ollama client."""
    _run(db, "a" * 32)
    kind, endpoint, ack, model, pin = run_backend_spec("a" * 32, path=db)
    assert kind is BackendKind.OLLAMA
    assert endpoint == "http://127.0.0.1:11434"
    assert (ack, model, pin) == (False, None, None)
    client = backend_for_run("a" * 32, path=db)
    assert type(client) is OllamaClient
    assert client.endpoint.base_url == "http://127.0.0.1:11434"


def test_a_run_pinned_to_an_ollama_profile_uses_its_endpoint(db: Path):
    profile = ensure_default_profile(path=db)
    _run(db, "b" * 32, profile_id=profile.profile_id, backend_kind="ollama")
    client = backend_for_run("b" * 32, path=db)
    assert type(client) is OllamaClient
    assert client.endpoint.base_url == profile.endpoint_url


def test_a_run_pinned_to_a_llama_cpp_profile_rebuilds_that_backend(db: Path):
    profile = create_profile(
        "mimir", _MIMIR, backend_kind="openai", plaintext_ack=True, path=db,
    )
    _run(
        db, "c" * 32, profile_id=profile.profile_id, backend_kind="openai",
        model_tag="qwen3.8-27b", model_digest=None, identity_kind="reported",
    )
    kind, endpoint, ack, model, pin = run_backend_spec("c" * 32, path=db)
    assert kind is BackendKind.OPENAI_COMPAT
    assert endpoint == _MIMIR
    assert ack is True
    assert model == "qwen3.8-27b"
    client = backend_for_run("c" * 32, path=db)
    assert type(client) is OpenAICompatBackend
    assert client.endpoint.base_url == _MIMIR


def test_a_run_whose_profile_lost_its_acknowledgement_fails_closed(db: Path):
    """Contract 4.3: the address check runs again at run start."""
    profile = create_profile(
        "mimir", _MIMIR, backend_kind="openai", plaintext_ack=True, path=db,
    )
    _run(db, "d" * 32, profile_id=profile.profile_id, backend_kind="openai",
         model_tag="qwen3.8-27b", model_digest=None, identity_kind="reported")
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        conn.execute("UPDATE analyst_llm_profile SET plaintext_ack=0")
    finally:
        conn.close()
    with pytest.raises(AddressPolicyError):
        backend_for_run("d" * 32, path=db)


def test_a_run_pinned_to_a_public_endpoint_is_refused(db: Path):
    profile = create_profile(
        "public", "http://8.8.8.8:9292", backend_kind="openai",
        plaintext_ack=True, path=db,
    )
    _run(db, "e" * 32, profile_id=profile.profile_id, backend_kind="openai",
         model_tag="m", model_digest=None, identity_kind="reported")
    with pytest.raises(AddressPolicyError, match="no override"):
        backend_for_run("e" * 32, path=db)


def test_an_unknown_run_is_refused(db: Path):
    with pytest.raises(BackendSelectionError, match="does not exist"):
        run_backend_spec("f" * 32, path=db)


def test_an_unknown_backend_kind_is_refused(db: Path):
    _run(db, "g" * 32)
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("UPDATE analyst_runs SET backend_kind='vllm'")
    except sqlite3.IntegrityError:
        pytest.skip("the schema CHECK already refuses an unknown kind")
    finally:
        conn.close()
    with pytest.raises(BackendSelectionError, match="unknown backend"):
        run_backend_spec("g" * 32, path=db)


# --------------------------------------------------------------------------
# Run creation records the identity kind and the profile (erratum E19)
# --------------------------------------------------------------------------

def test_the_identity_helper_keeps_the_pre_n2b_digest_rules():
    from experimental.analyst.models import ANALYST_DEFAULTS
    from experimental.analyst.service import _run_model_identity

    assert _run_model_identity(None, None) == (
        ANALYST_DEFAULTS.model_tag, ANALYST_DEFAULTS.model_digest, "digest",
    )
    assert _run_model_identity("qwen3.6:27b", _DIGEST) == (
        "qwen3.6:27b", _DIGEST, "digest",
    )


def test_the_identity_helper_accepts_a_reported_model():
    from experimental.analyst.service import _run_model_identity

    assert _run_model_identity("qwen3.8-27b", None, "reported") == (
        "qwen3.8-27b", None, "reported",
    )


@pytest.mark.parametrize(
    "args",
    [
        ("qwen3.8-27b", _DIGEST, "reported"),   # reported may not carry a digest
        ("qwen3.6:27b", None, "digest"),        # digest must carry one
        ("", None, "reported"),                 # a name is still required
        ("qwen3.8-27b:cloud", None, "reported"),  # cloud tags stay rejected
        ("qwen3.6:27b", _DIGEST, "trusted"),    # unknown kind
    ],
)
def test_the_identity_helper_refuses_incoherent_identities(args):
    from experimental.analyst.service import _run_model_identity

    with pytest.raises(ValueError):
        _run_model_identity(*args)
