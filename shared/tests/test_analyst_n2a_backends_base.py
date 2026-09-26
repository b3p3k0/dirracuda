"""N2a: pure backend types, and the guardrail that keeps them pure."""

from __future__ import annotations

import ast
import os

import pytest

from experimental.analyst.backends import (
    UNKNOWN_CONTEXT,
    BackendCapabilities,
    BackendError,
    BackendKind,
    IdentityKind,
    ModelIdentity,
)
from experimental.analyst.db_schema import BACKEND_KINDS, IDENTITY_KINDS

_DIGEST = "a" * 64


def _caps(**over):
    base = dict(
        max_context=32768, can_set_context_per_request=False,
        supports_json_schema=True, supports_seed=True,
    )
    base.update(over)
    return BackendCapabilities(**base)


# --------------------------------------------------------------------------
# The enums match the schema
# --------------------------------------------------------------------------

def test_backend_kinds_match_the_schema():
    assert {k.value for k in BackendKind} == set(BACKEND_KINDS)


def test_identity_kinds_match_the_schema():
    assert {k.value for k in IdentityKind} == set(IDENTITY_KINDS)


# --------------------------------------------------------------------------
# ModelIdentity
# --------------------------------------------------------------------------

def test_a_digest_identity_is_verified():
    identity = ModelIdentity(
        kind=IdentityKind.DIGEST, model_name="qwen3.6:27b", digest=_DIGEST,
    )
    assert identity.is_verified is True


def test_a_reported_identity_is_not_verified():
    identity = ModelIdentity(kind=IdentityKind.REPORTED, model_name="qwen3.8-27b")
    assert identity.is_verified is False
    assert identity.digest is None


def test_a_digest_identity_needs_a_real_digest():
    for bad in (None, "", "z" * 64, _DIGEST[:63], _DIGEST.upper()):
        with pytest.raises(BackendError):
            ModelIdentity(kind=IdentityKind.DIGEST, model_name="m", digest=bad)


def test_a_reported_identity_may_not_carry_a_digest():
    with pytest.raises(BackendError, match="no digest"):
        ModelIdentity(
            kind=IdentityKind.REPORTED, model_name="m", digest=_DIGEST,
        )


def test_the_kind_must_be_the_closed_enum():
    with pytest.raises(BackendError):
        ModelIdentity(kind="digest", model_name="m", digest=_DIGEST)


def test_a_model_name_is_required():
    with pytest.raises(BackendError):
        ModelIdentity(kind=IdentityKind.REPORTED, model_name="")


@pytest.mark.parametrize(
    "field,bad",
    [
        ("model_path", ""), ("ftype", 3), ("server_fingerprint", ""),
        ("n_params", 0), ("size_bytes", -1), ("n_vocab", "x"),
        ("n_ctx", 0), ("n_ctx_train", -2),
    ],
)
def test_reported_fields_are_validated(field, bad):
    with pytest.raises(BackendError):
        ModelIdentity(kind=IdentityKind.REPORTED, model_name="m", **{field: bad})


def test_as_run_columns_matches_the_v8_schema():
    """The mapping must name real analyst_runs columns."""
    from experimental.analyst import db_schema

    identity = ModelIdentity(
        kind=IdentityKind.REPORTED, model_name="qwen3.8-27b",
        model_path="/opt/llm/x.gguf", n_params=27_000_000_000,
        size_bytes=17_000_000_000, ftype="Q4_K - Medium", n_vocab=151_936,
        n_ctx=32768, n_ctx_train=32768, server_fingerprint="b1-f280b26",
    )
    columns = identity.as_run_columns()
    runs_sql = [
        obj[2] for obj in db_schema._expected_snapshot(db_schema.SCHEMA_VERSION).objects
        if obj[1] == "analyst_runs"
    ][0]
    for name in columns:
        assert name in runs_sql, f"{name} is not a column of analyst_runs"
    assert columns["model_digest"] is None
    assert columns["identity_kind"] == "reported"


def test_as_run_columns_carries_a_digest_run():
    identity = ModelIdentity(
        kind=IdentityKind.DIGEST, model_name="qwen3.6:27b", digest=_DIGEST,
    )
    columns = identity.as_run_columns()
    assert columns["model_digest"] == _DIGEST
    assert columns["identity_kind"] == "digest"
    assert columns["model_path"] is None


# --------------------------------------------------------------------------
# BackendCapabilities, including the router's n_ctx: 0
# --------------------------------------------------------------------------

def test_a_known_context_gates_on_size():
    caps = _caps(max_context=32768)
    assert caps.context_is_known is True
    assert caps.admits(16384) is True
    assert caps.admits(32768) is True
    assert caps.admits(42058) is False


def test_an_unknown_context_admits_the_run():
    """Contract 5.3/5.4: 0 is unknown, never unlimited, and the server is the
    real guarantee. A router reports n_ctx 0, and must neither refuse every run
    nor be treated as a promise."""
    caps = _caps(max_context=UNKNOWN_CONTEXT)
    assert caps.context_is_known is False
    assert caps.admits(42058) is True


def test_required_context_must_be_positive():
    caps = _caps()
    for bad in (0, -1, "8192", None):
        with pytest.raises(BackendError):
            caps.admits(bad)


def test_capability_flags_must_be_boolean():
    with pytest.raises(BackendError):
        _caps(supports_seed=1)


def test_max_context_must_be_a_nonnegative_integer():
    for bad in (-1, "32768", None):
        with pytest.raises(BackendError):
            _caps(max_context=bad)


# --------------------------------------------------------------------------
# Purity guardrail
# --------------------------------------------------------------------------

_MODULE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "experimental", "analyst", "backends", "base.py",
)

_FORBIDDEN_PREFIXES = (
    "sqlite3", "socket", "ssl", "http", "urllib", "requests", "httpx",
    "pathlib", "shutil", "subprocess", "tkinter", "os",
    "experimental.analyst.db_schema", "experimental.analyst.store",
    "experimental.analyst.service", "experimental.analyst.ollama_client",
)

_FORBIDDEN_CALLS = {"open"}


def _tree():
    with open(_MODULE, "r", encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=_MODULE)


def test_base_imports_no_io_layer():
    names = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    offenders = [
        name for name in names
        if any(name == p or name.startswith(p + ".") for p in _FORBIDDEN_PREFIXES)
    ]
    assert offenders == [], f"backends/base.py must stay pure; found {offenders}"


def test_base_opens_no_files():
    called = {
        node.func.id
        for node in ast.walk(_tree())
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not (called & _FORBIDDEN_CALLS)


def test_base_declares_no_relative_io_imports():
    """A sibling import would drag the transport layer in behind it."""
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0 or node.module is None or "client" not in (
                node.module or ""
            )
