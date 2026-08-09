"""Focused tests for the frozen C0B-3 policy/protocol identity."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.analyst_benchmark import c0b3_policy as policy
from scripts.analyst_benchmark.c0b2_leakscan import LeakGateError


def _docs(root: Path) -> Path:
    directory = root / "docs/dev/ollama_integration"
    directory.mkdir(parents=True)
    return directory


def test_policy_preimage_and_digest_are_exact() -> None:
    expected = (
        b'{"gates":{"final_acceptance":{"max_false_positive_documents":1,'
        b'"negative_document_count":40},"stage_c":{"max_false_positive_documents":1,'
        b'"negative_document_count":12},"stage_d3_d4":{'
        b'"max_false_positive_documents":1,"negative_document_count":12},'
        b'"stage_f_per_seed":{"max_false_positive_documents":1,'
        b'"negative_document_count":16}},"policy_id":'
        b'"c0b3-assistive-bounded-fp-v1","unit":"negative_document"}'
    )
    assert policy.canonical_json_bytes(policy.policy_payload()) == expected
    assert hashlib.sha256(expected).hexdigest() == policy.POLICY_SHA256


def test_policy_payload_and_bindings_are_defensive_copies() -> None:
    payload = policy.policy_payload()
    payload["gates"]["stage_d3_d4"]["max_false_positive_documents"] = 99
    binding = policy.policy_binding()
    binding["policy_id"] = "changed"
    assert policy.false_positive_limit(policy.CURRENT_POLICY, "stage_d3_d4") == 1
    assert policy.policy_binding()["policy_id"] == policy.POLICY_ID


def test_protocol_digest_uses_exact_verified_file_bytes(tmp_path: Path) -> None:
    docs = _docs(tmp_path)
    contents = {
        "BENCHMARK_PROTOCOL_C0B2.md": b"parent protocol\n",
        "BENCHMARK_PUBLIC_CDF_SCHEMA.md": b"schema catalog\n",
        "BENCHMARK_PROTOCOL_C0B3.md": b"current protocol\n",
        "CONTRACT_ERRATA.md": b"accepted errata\n",
    }
    for name, body in contents.items():
        (docs / name).write_bytes(body)
    components = {
        "c0b2_protocol_sha256": hashlib.sha256(contents[
            "BENCHMARK_PROTOCOL_C0B2.md"]).hexdigest(),
        "c0b2_public_schema_doc_sha256": hashlib.sha256(contents[
            "BENCHMARK_PUBLIC_CDF_SCHEMA.md"]).hexdigest(),
        "c0b3_protocol_sha256": hashlib.sha256(contents[
            "BENCHMARK_PROTOCOL_C0B3.md"]).hexdigest(),
        "contract_errata_sha256": hashlib.sha256(contents[
            "CONTRACT_ERRATA.md"]).hexdigest(),
    }
    expected = {
        "benchmark_protocol_id": policy.BENCHMARK_PROTOCOL_ID,
        "components": components,
    }
    assert policy.protocol_identity_payload(tmp_path) == expected
    expected_digest = hashlib.sha256(json.dumps(
        expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()
    assert policy.protocol_sha256(tmp_path) == expected_digest


def test_protocol_digest_rejects_a_symlinked_component(tmp_path: Path) -> None:
    docs = _docs(tmp_path)
    for name in (
        "BENCHMARK_PROTOCOL_C0B2.md", "BENCHMARK_PUBLIC_CDF_SCHEMA.md",
        "CONTRACT_ERRATA.md",
    ):
        (docs / name).write_text(name, encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("not an exact protocol path", encoding="utf-8")
    (docs / "BENCHMARK_PROTOCOL_C0B3.md").symlink_to(outside)
    with pytest.raises(LeakGateError):
        policy.protocol_sha256(tmp_path)


def test_header_resolver_distinguishes_only_exact_frozen_shapes() -> None:
    legacy = {"run_id": "c0b3-looking-name-does-not-select-policy"}
    current = {"run_id": "anything", **policy.header_identity()}
    assert policy.resolve_header_policy(legacy) == policy.LEGACY_POLICY
    assert policy.resolve_header_policy(current) == policy.CURRENT_POLICY
    assert policy.require_legacy_header(legacy) == policy.LEGACY_POLICY
    assert policy.require_current_header(current) == policy.CURRENT_POLICY


def test_reported_identity_is_explicit_for_both_header_families() -> None:
    assert policy.reported_identity({}) == {
        "benchmark_protocol_id": None,
        "policy_id": policy.LEGACY_POLICY_ID,
        "policy_sha256": None,
    }
    assert policy.reported_identity(policy.header_identity()) == {
        "benchmark_protocol_id": policy.BENCHMARK_PROTOCOL_ID,
        "policy_id": policy.POLICY_ID,
        "policy_sha256": policy.POLICY_SHA256,
    }


@pytest.mark.parametrize("header", [
    {"benchmark_protocol_id": policy.BENCHMARK_PROTOCOL_ID},
    {"policy_id": policy.POLICY_ID, "policy_sha256": policy.POLICY_SHA256},
    {**policy.header_identity(), "policy_sha256": "0" * 64},
    {**policy.header_identity(), "policy_id": policy.LEGACY_POLICY_ID},
    {**policy.header_identity(), "benchmark_protocol_id": "unknown"},
    {**policy.header_identity(), "policy_sha256": 7},
])
def test_header_resolver_rejects_partial_mixed_unknown_and_wrong_type(
        header: dict[str, object]) -> None:
    with pytest.raises(policy.PolicyIdentityError):
        policy.resolve_header_policy(header)


def test_mutating_namespace_guards_reject_the_other_policy() -> None:
    with pytest.raises(policy.PolicyIdentityError):
        policy.require_current_header({"run_id": "legacy"})
    with pytest.raises(policy.PolicyIdentityError):
        policy.require_legacy_header(policy.header_identity())


def test_payload_resolver_and_current_guard_fail_closed() -> None:
    assert policy.resolve_payload_policy({"version": "legacy-v1"}) == policy.LEGACY_POLICY
    current = {"version": "current-v2", **policy.policy_binding()}
    assert policy.require_current_payload(current) == policy.CURRENT_POLICY
    for invalid in (
        {"policy_id": policy.POLICY_ID},
        {"policy_sha256": policy.POLICY_SHA256},
        {"policy_id": policy.POLICY_ID, "policy_sha256": "f" * 64},
        {"policy_id": policy.POLICY_ID, "policy_sha256": None},
    ):
        with pytest.raises(policy.PolicyIdentityError):
            policy.resolve_payload_policy(invalid)
    with pytest.raises(policy.PolicyIdentityError):
        policy.require_current_payload({"version": "legacy-v1"})


@pytest.mark.parametrize("family,expected", [
    (policy.LEGACY_POLICY, {
        "stage_c": (1, "negative_false_positive_above_1"),
        "stage_d3_d4": (0, "negative_false_positive_present"),
        "stage_f_per_seed": (0, "negative_false_positive_present"),
        "final_acceptance": (1, "negative_false_positive_above_1"),
    }),
    (policy.CURRENT_POLICY, {
        "stage_c": (1, "negative_false_positive_above_1"),
        "stage_d3_d4": (1, "negative_false_positive_above_1"),
        "stage_f_per_seed": (1, "negative_false_positive_above_1"),
        "final_acceptance": (1, "negative_false_positive_above_1"),
    }),
])
def test_limits_and_failure_reasons_are_policy_specific(
        family: policy.ResolvedPolicy,
        expected: dict[str, tuple[int, str]]) -> None:
    observed = {
        gate: (policy.false_positive_limit(family, gate),
               policy.false_positive_failure_reason(family, gate))
        for gate in expected
    }
    assert observed == expected


def test_limit_helper_rejects_unknown_policy_and_gate() -> None:
    unknown = policy.ResolvedPolicy(
        family=policy.PolicyFamily.CURRENT_C0B3,
        benchmark_protocol_id="unknown", policy_id="unknown", policy_sha256=None)
    with pytest.raises(policy.PolicyIdentityError):
        policy.false_positive_limit(unknown, "stage_d3_d4")
    with pytest.raises(policy.PolicyIdentityError):
        policy.false_positive_limit(policy.CURRENT_POLICY, "D3")
    with pytest.raises(policy.PolicyIdentityError):
        policy.false_positive_limit(policy.CURRENT_POLICY, True)
