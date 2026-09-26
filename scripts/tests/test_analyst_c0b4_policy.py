"""Offline tests for the frozen C0B-4 policy/protocol identity."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.analyst_benchmark import c0b4_policy as policy
from scripts.analyst_benchmark.c0b2_leakscan import LeakGateError


def _docs(root: Path) -> Path:
    path = root / "docs/dev/ollama_integration"
    path.mkdir(parents=True)
    return path


def test_policy_preimage_and_digest_are_exact() -> None:
    expected = (
        b'{"per_seed":{"max_affected_chunks":1,"max_affected_documents":1,'
        b'"max_redundant_rows":1},"policy_id":'
        b'"c0b4-bounded-grounded-dedup-v1","recovery":{'
        b'"all_raw_findings_grounded":true,"dedupe_key":"category+nfc_quote",'
        b'"only_semantic_error":"duplicate_evidence",'
        b'"required_structural_validity":true,"retention_order":"stable_first"},'
        b'"unit":"grounded_redundant_row"}'
    )
    assert policy.canonical_json_bytes(policy.policy_payload()) == expected
    assert hashlib.sha256(expected).hexdigest() == policy.POLICY_SHA256


def test_policy_payload_and_bindings_are_defensive_and_strict() -> None:
    payload = policy.policy_payload()
    payload["per_seed"]["max_redundant_rows"] = 99
    digest = "a" * 64
    binding = policy.policy_binding(digest)
    binding["policy_id"] = "changed"
    assert policy.policy_payload()["per_seed"]["max_redundant_rows"] == 1
    assert policy.policy_binding(digest)["policy_id"] == policy.POLICY_ID
    with pytest.raises(policy.PolicyIdentityError):
        policy.policy_binding(7)  # type: ignore[arg-type]


def test_protocol_digest_uses_all_six_exact_verified_files(tmp_path: Path) -> None:
    docs = _docs(tmp_path)
    names = (
        "BENCHMARK_PROTOCOL_C0B2.md", "BENCHMARK_PUBLIC_CDF_SCHEMA.md",
        "BENCHMARK_PROTOCOL_C0B3.md", "BENCHMARK_PROTOCOL_C0B4.md",
        "PUBLIC_CDF_OUTCOME_C0B3.md", "CONTRACT_ERRATA.md",
    )
    bodies = {name: f"exact {name}\n".encode() for name in names}
    for name, body in bodies.items():
        (docs / name).write_bytes(body)
    value = policy.protocol_identity_payload(tmp_path)
    assert list(value) == ["benchmark_protocol_id", "components"]
    assert value["benchmark_protocol_id"] == policy.BENCHMARK_PROTOCOL_ID
    assert len(value["components"]) == 6
    expected = hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode()).hexdigest()
    assert policy.protocol_sha256(tmp_path) == expected


def test_protocol_digest_rejects_symlinked_component(tmp_path: Path) -> None:
    docs = _docs(tmp_path)
    for name in (
        "BENCHMARK_PROTOCOL_C0B2.md", "BENCHMARK_PUBLIC_CDF_SCHEMA.md",
        "BENCHMARK_PROTOCOL_C0B3.md", "PUBLIC_CDF_OUTCOME_C0B3.md",
        "CONTRACT_ERRATA.md",
    ):
        (docs / name).write_text(name, encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (docs / "BENCHMARK_PROTOCOL_C0B4.md").symlink_to(outside)
    with pytest.raises(LeakGateError):
        policy.protocol_sha256(tmp_path)


def test_three_families_resolve_without_cross_normalization() -> None:
    digest = "a" * 64
    assert policy.resolve_payload_policy({}).family == policy.PolicyFamily.LEGACY_C0B2
    assert policy.resolve_header_policy({
        "protocol_sha256": digest,
    }).family == policy.PolicyFamily.LEGACY_C0B2
    assert policy.resolve_payload_policy({
        "policy_id": "c0b3-assistive-bounded-fp-v1",
        "policy_sha256":
            "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235",
    }).family == policy.PolicyFamily.C0B3
    assert policy.resolve_header_policy({
        "benchmark_protocol_id": "c0b3-assistive-confirmation-v1",
        "policy_id": "c0b3-assistive-bounded-fp-v1",
        "policy_sha256":
            "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235",
        "protocol_sha256": digest,
    }).family == policy.PolicyFamily.C0B3
    assert policy.require_current_payload(
        policy.policy_binding(digest)).family == policy.PolicyFamily.C0B4
    assert policy.require_current_header(
        policy.header_identity(digest)).family == policy.PolicyFamily.C0B4


@pytest.mark.parametrize("value", [
    {"policy_id": policy.POLICY_ID},
    {"policy_id": policy.POLICY_ID, "policy_sha256": policy.POLICY_SHA256},
    {**policy.policy_binding("a" * 64), "policy_sha256": "f" * 64},
    {**policy.policy_binding("a" * 64), "protocol_sha256": 7},
    {
        "policy_id": "c0b3-assistive-bounded-fp-v1",
        "policy_sha256":
            "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235",
        "protocol_sha256": "a" * 64,
    },
])
def test_partial_mixed_unknown_and_wrong_type_fail_closed(value) -> None:
    with pytest.raises(policy.PolicyIdentityError):
        policy.resolve_payload_policy(value)


def test_expected_protocol_digest_is_enforced() -> None:
    with pytest.raises(policy.PolicyIdentityError, match="source identity"):
        policy.require_current_payload(
            policy.policy_binding("a" * 64),
            expected_protocol_sha256="b" * 64)
