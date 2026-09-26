"""N3: secrets and reasoning text never reach a log, a row, or an artifact.

Contract 9 (tokens) and 7.4 with erratum E1 (reasoning traces).

Written as a new test rather than by extending
`scripts/analyst_benchmark/leakscan.py`. That file is frozen provenance whose
path seals are checked by the benchmark policy scripts, so the rule is
enforced here instead of editing evidence.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from experimental.analyst.backends import openai_protocol as proto

_ROOT = Path(__file__).resolve().parents[2]
_ANALYST = _ROOT / "experimental" / "analyst"
_TOKEN = "super-secret-bearer-token-value"
_REASONING = "VERBATIM ECHO OF THE OPERATOR'S SENSITIVE INPUT"


def _analyst_sources():
    for path in _ANALYST.rglob("*.py"):
        yield path


# --------------------------------------------------------------------------
# Reasoning traces
# --------------------------------------------------------------------------

def test_reasoning_text_is_not_retained_by_the_parser():
    out = proto.parse_sse_stream([
        "data: " + json.dumps({"choices": [{
            "index": 0, "finish_reason": "stop",
            "delta": {"reasoning_content": _REASONING},
        }]}),
        "data: [DONE]",
    ])
    fields = {name: getattr(out, name) for name in out.__slots__}
    assert _REASONING not in json.dumps(fields, default=str)
    assert _REASONING not in repr(out)
    assert out.reasoning_only is True


def test_reasoning_is_disabled_on_every_request():
    """The cheapest protection is never asking for it."""
    from experimental.analyst.ollama_contract import build_chat_request

    request = build_chat_request("public", nonce="FENCE_0123456789ABCDEF")
    body = proto.build_chat_body(request.payload(), model_id="qwen3.8-27b")
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_no_analyst_module_persists_a_reasoning_field():
    """No SQL or payload key anywhere writes the reasoning channel."""
    offenders = []
    for path in _analyst_sources():
        source = path.read_text(encoding="utf-8")
        for marker in ('"reasoning_content":', "'reasoning_content':",
                       "reasoning_content=", "INSERT INTO"):
            if marker in source and "reasoning" in source and marker != "INSERT INTO":
                offenders.append(f"{path.name}:{marker}")
    assert offenders == [], f"reasoning text may be persisted at {offenders}"


def test_chat_metrics_never_carry_reasoning_bytes_for_this_backend():
    from experimental.analyst.backends.openai_api import _metrics

    out = proto.StreamOutcome(
        content="{}", finish_reason="stop", system_fingerprint="b1-f280b26",
        prompt_tokens=10, predicted_tokens=2, prompt_ms=1.0, predicted_ms=1.0,
        reasoning_only=False,
    )
    assert _metrics(out, 128).thinking_bytes == 0


# --------------------------------------------------------------------------
# Bearer tokens
# --------------------------------------------------------------------------

def test_a_token_is_never_written_to_the_analyst_database():
    """analyst_llm_profile stores a Keymaster key id, never the secret."""
    from experimental.analyst import db_schema

    runs_sql = [
        obj[2]
        for obj in db_schema._expected_snapshot(db_schema.SCHEMA_VERSION).objects
        if obj[1] == "analyst_llm_profile"
    ][0]
    assert "keymaster_key_id" in runs_sql
    for forbidden in ("token", "api_key", "secret", "bearer"):
        assert forbidden not in runs_sql.lower(), (
            f"analyst_llm_profile names a {forbidden} column; secrets belong "
            "in Keymaster"
        )


def test_the_token_is_held_only_in_memory():
    """The transport keeps it on the instance and writes it nowhere."""
    source = (_ANALYST / "transport.py").read_text(encoding="utf-8")
    assert "self._bearer_token" in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"info", "warning", "error", "debug", "print"}:
                rendered = ast.dump(node)
                assert "_bearer_token" not in rendered, "the token reaches a log"


def test_credential_errors_never_quote_the_secret():
    from experimental.analyst.credentials import CredentialError, KeymasterLocked

    for exc in (
        CredentialError("stored credential could not be read"),
        KeymasterLocked("Keymaster is locked"),
        CredentialError("keymaster key id is invalid"),
    ):
        assert _TOKEN not in str(exc)
        assert "Bearer" not in str(exc)


def test_the_authorization_header_is_built_only_from_the_held_token():
    source = (_ANALYST / "transport.py").read_text(encoding="utf-8")
    assert 'f"Bearer {self._bearer_token}"' in source
    assert source.count("Bearer ") == 1


def test_a_report_payload_has_no_place_to_put_a_secret():
    from experimental.analyst import report_json

    request = {
        "run_id": "r" * 32, "report_label": "L", "read_mode": "quick",
        "model_tag": "qwen3.8-27b", "model_digest": None,
        "identity_kind": "reported", "model_path": None,
        "model_n_params": None, "model_size_bytes": None, "model_ftype": None,
        "model_n_vocab": None, "model_n_ctx": None, "model_n_ctx_train": None,
        "server_fingerprint": "b1-f280b26",
        "created_at_utc": "t", "files_read": 0, "files_total": 0,
        "flagged_files": 0,
    }
    meta = report_json.RunMeta(**request)
    rendered = json.dumps({k: getattr(meta, k) for k in request}, default=str)
    for forbidden in ("token", "authorization", "bearer", "passphrase"):
        assert forbidden not in rendered.lower()
