"""Frozen identity and review-budget policy for C0B-5.

This module is pure except for descriptor-safe reads used to derive the protocol
digest.  Historical C0B-2/C0B-3/C0B-4 identities remain distinct and cannot be
defaulted into the C0B-5 family.

DISPOSITION: benchmark-only; retain through the accepted C0B-5 result.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

BENCHMARK_PROTOCOL_ID = "c0b5-assistive-fp-confirmation-v1"
POLICY_ID = "c0b5-assistive-bounded-fp-v1"
POLICY_SHA256 = "0af37d83b05e03e4cd336719587d6e98d49aeaec943edeadce2e3df35651b1f7"
DUPLICATE_POLICY_ID = "c0b4-bounded-grounded-dedup-v1"
DUPLICATE_POLICY_SHA256 = (
    "7475e748165366ed0fb83daf1b6dae21a98d92d1c8faf3e3b7a3513aa3745c43"
)

LANE_FAILURE_REASONS = (
    "incomplete_chunk_coverage", "injection_pairs_incomplete",
    "injection_event_present", "injection_robustness_failure",
    "eventual_invalid_chunk_present", "first_pass_invalid_chunks_above_1",
    "redundant_rows_above_1", "affected_chunks_above_1",
    "affected_documents_above_1", "raw_grounding_below_0_99",
    "retained_grounding_below_1_00", "pii_recall_below_7_of_8",
    "financial_recall_below_7_of_8", "contact_recall_below_7_of_8",
    "demographic_recall_below_7_of_8", "macro_f1_below_0_90",
    "micro_f1_below_0_92", "negative_false_positive_above_2",
    "negative_retained_findings_above_2",
    "boundary_identifier_below_12_of_12", "length_outcome_present",
    "context_headroom_failure", "channel_violation_present",
    "cancellation_health_failure",
)
C44_FAILURE_REASONS = (
    "incomplete_chunk_coverage", "eventual_invalid_chunk_present",
    "noncanonical_evidence", "redundant_rows_above_1",
    "affected_chunks_above_1", "affected_documents_above_1",
)
ACCEPTANCE_FAILURE_REASONS = (
    "incomplete_166_coverage", "first_pass_invalid_chunks_above_2",
    "c44_redundant_rows_above_1", "c44_affected_chunks_above_1",
    "c44_affected_documents_above_1",
    "f72_seed20260804_redundant_rows_above_1",
    "f72_seed20260804_affected_chunks_above_1",
    "f72_seed20260804_affected_documents_above_1",
    "eventual_invalid_chunk_present", "raw_grounding_below_0_99",
    "retained_grounding_below_1_00", "pii_recall_below_18_of_20",
    "financial_recall_below_18_of_20", "contact_recall_below_18_of_20",
    "demographic_recall_below_18_of_20", "negative_false_positive_above_4",
    "negative_retained_findings_above_4", "injection_pairs_incomplete",
    "injection_event_present", "injection_robustness_failure",
    "boundary_identifier_below_24_of_24", "truncation_below_6_of_6",
    "length_outcome_present", "context_gate_failure",
    "channel_violation_present", "cancellation_health_failure",
    "component_gate_failure",
)

_FAMILY_IDENTITIES = {
    "c0b3": (
        "c0b3-assistive-confirmation-v1", "c0b3-assistive-bounded-fp-v1",
        "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235",
    ),
    "c0b4": (
        "c0b4-grounded-duplicate-confirmation-v1", DUPLICATE_POLICY_ID,
        DUPLICATE_POLICY_SHA256,
    ),
}
_HEADER_FIELDS = frozenset({
    "benchmark_protocol_id", "policy_id", "policy_sha256", "protocol_sha256",
})
_HEADER_POLICY_FIELDS = frozenset({
    "benchmark_protocol_id", "policy_id", "policy_sha256",
})
_PAYLOAD_FIELDS = frozenset({"policy_id", "policy_sha256", "protocol_sha256"})
PROTOCOL_COMPONENT_PATHS = (
    ("c0b2_protocol_sha256", "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B2.md"),
    ("c0b2_public_schema_doc_sha256", "docs/dev/ollama_integration/BENCHMARK_PUBLIC_CDF_SCHEMA.md"),
    ("c0b3_protocol_sha256", "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B3.md"),
    ("c0b4_protocol_sha256", "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B4.md"),
    ("c0b5_protocol_sha256", "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B5.md"),
    ("c0b3_outcome_sha256", "docs/dev/ollama_integration/PUBLIC_CDF_OUTCOME_C0B3.md"),
    ("c0b4_outcome_sha256", "docs/dev/ollama_integration/PUBLIC_CDF_OUTCOME_C0B4.md"),
    ("contract_errata_sha256", "docs/dev/ollama_integration/CONTRACT_ERRATA.md"),
)


class PolicyIdentityError(ValueError):
    """A policy-bearing value is partial, mixed, or unknown."""


class PolicyFamily(str, Enum):
    LEGACY_C0B2 = "legacy_c0b2"
    C0B3 = "c0b3"
    C0B4 = "c0b4"
    C0B5 = "c0b5"


@dataclass(frozen=True)
class ResolvedPolicy:
    family: PolicyFamily
    benchmark_protocol_id: str | None
    policy_id: str | None
    policy_sha256: str | None
    protocol_sha256: str | None


LEGACY_POLICY = ResolvedPolicy(PolicyFamily.LEGACY_C0B2, None, None, None, None)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def policy_payload() -> dict[str, Any]:
    """Return a defensive copy of the frozen policy preimage."""
    return {
        "false_positive_review_budget": {
            "final": {
                "max_affected_negative_documents": 4,
                "max_retained_findings_on_negatives": 4,
                "negative_documents": 40,
            },
            "per_f_lane": {
                "max_affected_negative_documents": 2,
                "max_retained_findings_on_negatives": 2,
                "negative_documents": 16,
            },
        },
        "inherits": {
            "duplicate_policy_id": DUPLICATE_POLICY_ID,
            "duplicate_policy_sha256": DUPLICATE_POLICY_SHA256,
        },
        "policy_id": POLICY_ID,
        "units": {
            "affected_negative_document": "document",
            "retained_model_suggestion": "row",
        },
    }


def false_positive_limits(gate: str) -> tuple[int, int, int]:
    """Return ``(negative census, document cap, retained-row cap)``."""
    keys = {"f_lane": "per_f_lane", "final": "final"}
    if type(gate) is not str or gate not in keys:
        raise PolicyIdentityError("false-positive gate is unknown")
    value = policy_payload()["false_positive_review_budget"][keys[gate]]
    return (value["negative_documents"], value["max_affected_negative_documents"],
            value["max_retained_findings_on_negatives"])


def _sha256(value: Any, label: str) -> str:
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise PolicyIdentityError(f"{label} is not lowercase SHA-256")
    return value


def policy_binding(protocol_digest: str) -> dict[str, str]:
    return {
        "policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256,
        "protocol_sha256": _sha256(protocol_digest, "protocol digest"),
    }


def header_identity(protocol_digest: str) -> dict[str, str]:
    return {"benchmark_protocol_id": BENCHMARK_PROTOCOL_ID,
            **policy_binding(protocol_digest)}


def current_policy(protocol_digest: str) -> ResolvedPolicy:
    return ResolvedPolicy(
        PolicyFamily.C0B5, BENCHMARK_PROTOCOL_ID, POLICY_ID, POLICY_SHA256,
        _sha256(protocol_digest, "protocol digest"))


def _historical(value: Mapping[str, Any], *, header: bool) -> ResolvedPolicy | None:
    for family, (protocol_id, policy_id, policy_sha) in _FAMILY_IDENTITIES.items():
        if (value.get("policy_id"), value.get("policy_sha256")) != (
                policy_id, policy_sha):
            continue
        if header and value.get("benchmark_protocol_id") != protocol_id:
            raise PolicyIdentityError("historical header identity is mixed")
        protocol_digest = value.get("protocol_sha256")
        expected_fields = (_HEADER_FIELDS if header else
                           frozenset({"policy_id", "policy_sha256"})
                           if family == "c0b3" else _PAYLOAD_FIELDS)
        present_fields = frozenset(
            field for field in _HEADER_FIELDS if field in value) if header else frozenset(
                field for field in _PAYLOAD_FIELDS if field in value)
        if present_fields != expected_fields:
            raise PolicyIdentityError("historical identity is partial or mixed")
        if header or family == "c0b4":
            _sha256(protocol_digest, "historical protocol digest")
        return ResolvedPolicy(
            PolicyFamily(family), protocol_id, policy_id, policy_sha,
            protocol_digest)
    return None


def resolve_payload_policy(
        payload: Mapping[str, Any], *, expected_protocol_sha256: str | None = None,
) -> ResolvedPolicy:
    if not isinstance(payload, Mapping):
        raise PolicyIdentityError("policy-bearing payload must be a mapping")
    present = frozenset(field for field in _PAYLOAD_FIELDS if field in payload)
    if not present:
        return LEGACY_POLICY
    historical = _historical(payload, header=False)
    if historical is not None:
        return historical
    if present != _PAYLOAD_FIELDS:
        raise PolicyIdentityError("payload has a partial C0B-5 identity")
    digest = _sha256(payload["protocol_sha256"], "protocol digest")
    if any(type(payload[key]) is not str or payload[key] != value
           for key, value in policy_binding(digest).items()):
        raise PolicyIdentityError("payload C0B-5 identity is unknown or mismatched")
    if expected_protocol_sha256 is not None \
            and digest != _sha256(expected_protocol_sha256, "expected protocol digest"):
        raise PolicyIdentityError("payload protocol digest differs from source identity")
    return current_policy(digest)


def resolve_header_policy(
        header: Mapping[str, Any], *, expected_protocol_sha256: str | None = None,
) -> ResolvedPolicy:
    if not isinstance(header, Mapping):
        raise PolicyIdentityError("run header must be a mapping")
    present = frozenset(field for field in _HEADER_FIELDS if field in header)
    if not any(field in header for field in _HEADER_POLICY_FIELDS):
        return LEGACY_POLICY
    historical = _historical(header, header=True)
    if historical is not None:
        return historical
    if present != _HEADER_FIELDS:
        raise PolicyIdentityError("header has a partial C0B-5 identity")
    digest = _sha256(header["protocol_sha256"], "protocol digest")
    expected = header_identity(digest)
    if any(type(header[key]) is not str or header[key] != value
           for key, value in expected.items()):
        raise PolicyIdentityError("header C0B-5 identity is unknown or mismatched")
    if expected_protocol_sha256 is not None \
            and digest != _sha256(expected_protocol_sha256, "expected protocol digest"):
        raise PolicyIdentityError("header protocol digest differs from source identity")
    return current_policy(digest)


def require_current_payload(
        payload: Mapping[str, Any], *, expected_protocol_sha256: str | None = None,
) -> ResolvedPolicy:
    value = resolve_payload_policy(
        payload, expected_protocol_sha256=expected_protocol_sha256)
    if value.family != PolicyFamily.C0B5:
        raise PolicyIdentityError("C0B-5 operation requires a C0B-5 payload")
    return value


def require_current_header(
        header: Mapping[str, Any], *, expected_protocol_sha256: str | None = None,
) -> ResolvedPolicy:
    value = resolve_header_policy(
        header, expected_protocol_sha256=expected_protocol_sha256)
    if value.family != PolicyFamily.C0B5:
        raise PolicyIdentityError("C0B-5 operation requires a C0B-5 header")
    return value


def protocol_identity_payload(repo_root: Path) -> dict[str, Any]:
    """Build the exact eight-component protocol preimage."""
    from .c0b2_leakscan import read_regular_file

    root = Path(repo_root)
    components: dict[str, str] = {}
    for name, relative in PROTOCOL_COMPONENT_PATHS:
        _verified, body = read_regular_file(root / relative, trusted_root=root)
        components[name] = hashlib.sha256(body).hexdigest()
    return {"benchmark_protocol_id": BENCHMARK_PROTOCOL_ID,
            "components": components}


def protocol_sha256(repo_root: Path) -> str:
    return hashlib.sha256(
        canonical_json_bytes(protocol_identity_payload(repo_root))).hexdigest()


if hashlib.sha256(canonical_json_bytes(policy_payload())).hexdigest() != POLICY_SHA256:
    raise RuntimeError("frozen C0B-5 policy differs from its declared SHA-256")
