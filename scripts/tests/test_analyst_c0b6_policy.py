"""Offline tests for the frozen C0B-6 policy and protocol identity."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.analyst_benchmark import c0b6_policy as policy
from scripts.analyst_benchmark.c0b2_leakscan import LeakGateError


def _docs(root: Path) -> Path:
    path = root / "docs/dev/ollama_integration"
    path.mkdir(parents=True)
    return path


def test_policy_preimage_digest_limits_and_reason_order_are_exact() -> None:
    expected = (
        b'{"false_positive_review_budget":{"final":{'
        b'"max_affected_negative_documents":4,'
        b'"max_retained_findings_on_negatives":4,"negative_documents":40},'
        b'"per_f_lane":{"max_affected_negative_documents":2,'
        b'"max_retained_findings_on_negatives":2,"negative_documents":16}},'
        b'"inherits":{"duplicate_policy_id":"c0b4-bounded-grounded-dedup-v1",'
        b'"duplicate_policy_sha256":'
        b'"7475e748165366ed0fb83daf1b6dae21a98d92d1c8faf3e3b7a3513aa3745c43"},'
        b'"policy_id":"c0b6-assistive-bounded-fp-v1","units":{'
        b'"affected_negative_document":"document",'
        b'"retained_model_suggestion":"row"}}'
    )
    assert policy.canonical_json_bytes(policy.policy_payload()) == expected
    assert hashlib.sha256(expected).hexdigest() == policy.POLICY_SHA256
    assert policy.false_positive_limits("f_lane") == (16, 2, 2)
    assert policy.false_positive_limits("final") == (40, 4, 4)
    assert policy.LANE_FAILURE_REASONS.index(
        "negative_false_positive_above_2") < policy.LANE_FAILURE_REASONS.index(
            "negative_retained_findings_above_2")
    assert policy.ACCEPTANCE_FAILURE_REASONS.index(
        "negative_false_positive_above_4") < policy.ACCEPTANCE_FAILURE_REASONS.index(
            "negative_retained_findings_above_4")


def test_policy_values_are_defensive_and_strict() -> None:
    payload = policy.policy_payload()
    payload["false_positive_review_budget"]["final"][
        "max_retained_findings_on_negatives"] = 99
    assert policy.false_positive_limits("final") == (40, 4, 4)
    with pytest.raises(policy.PolicyIdentityError):
        policy.false_positive_limits("unknown")
    with pytest.raises(policy.PolicyIdentityError):
        policy.policy_binding(7)  # type: ignore[arg-type]


def test_protocol_digest_uses_all_ten_descriptor_safe_components(
        tmp_path: Path) -> None:
    docs = _docs(tmp_path)
    names = [Path(relative).name for _key, relative in policy.PROTOCOL_COMPONENT_PATHS]
    for name in names:
        (docs / name).write_text(f"exact {name}\n", encoding="utf-8")
    value = policy.protocol_identity_payload(tmp_path)
    assert list(value) == ["benchmark_protocol_id", "components"]
    assert value["benchmark_protocol_id"] == policy.BENCHMARK_PROTOCOL_ID
    assert list(value["components"]) == [
        key for key, _relative in policy.PROTOCOL_COMPONENT_PATHS]
    expected = hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode()).hexdigest()
    assert policy.protocol_sha256(tmp_path) == expected


def test_protocol_digest_rejects_symlinked_component(tmp_path: Path) -> None:
    docs = _docs(tmp_path)
    names = [Path(relative).name for _key, relative in policy.PROTOCOL_COMPONENT_PATHS]
    for name in names[:-1]:
        (docs / name).write_text(name, encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (docs / names[-1]).symlink_to(outside)
    with pytest.raises(LeakGateError):
        policy.protocol_sha256(tmp_path)


def test_five_families_resolve_without_cross_normalization() -> None:
    digest = "a" * 64
    assert policy.resolve_payload_policy({}).family == policy.PolicyFamily.LEGACY_C0B2
    assert policy.resolve_header_policy({
        "protocol_sha256": digest,
    }).family == policy.PolicyFamily.LEGACY_C0B2
    c0b3 = {
        "policy_id": "c0b3-assistive-bounded-fp-v1",
        "policy_sha256":
            "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235",
    }
    assert policy.resolve_payload_policy(c0b3).family == policy.PolicyFamily.C0B3
    assert policy.resolve_header_policy({
        "benchmark_protocol_id": "c0b3-assistive-confirmation-v1",
        "protocol_sha256": digest, **c0b3,
    }).family == policy.PolicyFamily.C0B3
    c0b4 = {
        "policy_id": policy.DUPLICATE_POLICY_ID,
        "policy_sha256": policy.DUPLICATE_POLICY_SHA256,
        "protocol_sha256": digest,
    }
    assert policy.resolve_payload_policy(c0b4).family == policy.PolicyFamily.C0B4
    assert policy.resolve_header_policy({
        "benchmark_protocol_id": "c0b4-grounded-duplicate-confirmation-v1", **c0b4,
    }).family == policy.PolicyFamily.C0B4
    c0b5 = {
        "policy_id": "c0b5-assistive-bounded-fp-v1",
        "policy_sha256":
            "0af37d83b05e03e4cd336719587d6e98d49aeaec943edeadce2e3df35651b1f7",
        "protocol_sha256": digest,
    }
    assert policy.resolve_payload_policy(c0b5).family == policy.PolicyFamily.C0B5
    assert policy.resolve_header_policy({
        "benchmark_protocol_id": "c0b5-assistive-fp-confirmation-v1", **c0b5,
    }).family == policy.PolicyFamily.C0B5
    assert policy.require_current_payload(
        policy.policy_binding(digest)).family == policy.PolicyFamily.C0B6
    assert policy.require_current_header(
        policy.header_identity(digest)).family == policy.PolicyFamily.C0B6


def test_failure_origin_vocabulary_is_closed_and_exact() -> None:
    assert policy.FAILURE_ORIGINS == (
        "safety_transport", "budget_claim", "filesystem_revalidation",
        "parent_replay", "source_revalidation", "master_replay",
        "resume_history", "resume_control_replay", "preflight",
        "lane_activation", "lane_execution", "lane_derivation",
        "cursor_transition", "acceptance_derivation", "terminal_recheck",
        "backup_live_replay", "backup_snapshot_replay", "backup_publication",
        "operator_abandon",
    )


@pytest.mark.parametrize("value", [
    {"policy_id": policy.POLICY_ID},
    {"policy_id": policy.POLICY_ID, "policy_sha256": policy.POLICY_SHA256},
    {**policy.policy_binding("a" * 64), "policy_sha256": "f" * 64},
    {**policy.policy_binding("a" * 64), "protocol_sha256": 7},
    {"policy_id": policy.DUPLICATE_POLICY_ID,
     "policy_sha256": policy.DUPLICATE_POLICY_SHA256},
    {"policy_id": "c0b3-assistive-bounded-fp-v1",
     "policy_sha256":
        "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235",
     "protocol_sha256": "a" * 64},
])
def test_partial_mixed_unknown_and_wrong_type_payloads_fail_closed(value) -> None:
    with pytest.raises(policy.PolicyIdentityError):
        policy.resolve_payload_policy(value)


def test_expected_protocol_digest_and_historical_current_guard_are_enforced() -> None:
    with pytest.raises(policy.PolicyIdentityError, match="source identity"):
        policy.require_current_payload(
            policy.policy_binding("a" * 64), expected_protocol_sha256="b" * 64)
    with pytest.raises(policy.PolicyIdentityError, match="requires a C0B-6"):
        policy.require_current_payload({
            "policy_id": policy.DUPLICATE_POLICY_ID,
            "policy_sha256": policy.DUPLICATE_POLICY_SHA256,
            "protocol_sha256": "a" * 64,
        })


@pytest.mark.parametrize("header", [
    {"benchmark_protocol_id": policy.BENCHMARK_PROTOCOL_ID},
    {"policy_id": policy.POLICY_ID, "protocol_sha256": "a" * 64},
    {"benchmark_protocol_id": "c0b3-assistive-confirmation-v1",
     "policy_id": "c0b3-assistive-bounded-fp-v1",
     "policy_sha256":
        "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235"},
])
def test_partial_current_and_historical_headers_fail_closed(header) -> None:
    with pytest.raises(policy.PolicyIdentityError):
        policy.resolve_header_policy(header)
