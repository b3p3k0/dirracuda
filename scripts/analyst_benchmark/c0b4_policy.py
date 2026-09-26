"""Frozen C0B-4 policy and protocol identity helpers.

This module is intentionally pure apart from descriptor-safe reads used to derive
the protocol digest.  It does not reinterpret either legacy C0B-2 artifacts or
C0B-3 policy-bound artifacts.

DISPOSITION: benchmark-only; retain through the accepted C0B-4 result.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

BENCHMARK_PROTOCOL_ID = "c0b4-grounded-duplicate-confirmation-v1"
POLICY_ID = "c0b4-bounded-grounded-dedup-v1"
POLICY_SHA256 = "7475e748165366ed0fb83daf1b6dae21a98d92d1c8faf3e3b7a3513aa3745c43"

_C0B3_PROTOCOL_ID = "c0b3-assistive-confirmation-v1"
_C0B3_POLICY_ID = "c0b3-assistive-bounded-fp-v1"
_C0B3_POLICY_SHA256 = (
    "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235"
)
_HEADER_IDENTITY_FIELDS = frozenset({
    "benchmark_protocol_id", "policy_id", "policy_sha256",
})
_PAYLOAD_FIELDS = frozenset({"policy_id", "policy_sha256", "protocol_sha256"})
_COMPONENT_PATHS = (
    ("c0b2_protocol_sha256",
     "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B2.md"),
    ("c0b2_public_schema_doc_sha256",
     "docs/dev/ollama_integration/BENCHMARK_PUBLIC_CDF_SCHEMA.md"),
    ("c0b3_protocol_sha256",
     "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B3.md"),
    ("c0b4_protocol_sha256",
     "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B4.md"),
    ("c0b3_outcome_sha256",
     "docs/dev/ollama_integration/PUBLIC_CDF_OUTCOME_C0B3.md"),
    ("contract_errata_sha256",
     "docs/dev/ollama_integration/CONTRACT_ERRATA.md"),
)


class PolicyIdentityError(ValueError):
    """An artifact mixes, omits, or falsifies a policy identity."""


class PolicyFamily(str, Enum):
    """The three mutually exclusive public benchmark families."""

    LEGACY_C0B2 = "legacy_c0b2"
    C0B3 = "c0b3"
    C0B4 = "c0b4"


@dataclass(frozen=True)
class ResolvedPolicy:
    family: PolicyFamily
    benchmark_protocol_id: str | None
    policy_id: str | None
    policy_sha256: str | None
    protocol_sha256: str | None


LEGACY_POLICY = ResolvedPolicy(PolicyFamily.LEGACY_C0B2, None, None, None, None)
C0B3_POLICY = ResolvedPolicy(
    PolicyFamily.C0B3, _C0B3_PROTOCOL_ID, _C0B3_POLICY_ID,
    _C0B3_POLICY_SHA256, None,
)


def canonical_json_bytes(value: Any) -> bytes:
    """Return the exact canonical JSON encoding used by C0B-4 identities."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def policy_payload() -> dict[str, Any]:
    """Return a fresh copy of the frozen policy-hash preimage."""
    return {
        "per_seed": {
            "max_affected_chunks": 1,
            "max_affected_documents": 1,
            "max_redundant_rows": 1,
        },
        "policy_id": POLICY_ID,
        "recovery": {
            "all_raw_findings_grounded": True,
            "dedupe_key": "category+nfc_quote",
            "only_semantic_error": "duplicate_evidence",
            "required_structural_validity": True,
            "retention_order": "stable_first",
        },
        "unit": "grounded_redundant_row",
    }


def _sha256(value: Any, label: str) -> str:
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise PolicyIdentityError(f"{label} is not lowercase SHA-256")
    return value


def policy_binding(protocol_digest: str) -> dict[str, str]:
    """Return fresh exact common identity fields for every C0B-4 artifact."""
    return {
        "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256,
        "protocol_sha256": _sha256(protocol_digest, "protocol digest"),
    }


def header_identity(protocol_digest: str) -> dict[str, str]:
    """Return the complete C0B-4 header identity."""
    return {"benchmark_protocol_id": BENCHMARK_PROTOCOL_ID,
            **policy_binding(protocol_digest)}


def current_policy(protocol_digest: str) -> ResolvedPolicy:
    """Build the exact C0B-4 resolved identity for one frozen source tree."""
    return ResolvedPolicy(
        PolicyFamily.C0B4, BENCHMARK_PROTOCOL_ID, POLICY_ID, POLICY_SHA256,
        _sha256(protocol_digest, "protocol digest"),
    )


def _present(value: Mapping[str, Any], fields: frozenset[str]) -> frozenset[str]:
    return frozenset(field for field in fields if field in value)


def resolve_payload_policy(
        payload: Mapping[str, Any], *, expected_protocol_sha256: str | None = None,
) -> ResolvedPolicy:
    """Resolve only exact C0B-2, C0B-3, or C0B-4 payload identities."""
    if not isinstance(payload, Mapping):
        raise PolicyIdentityError("policy-bearing payload must be a mapping")
    present = _present(payload, _PAYLOAD_FIELDS)
    if not present:
        return LEGACY_POLICY
    c0b3_fields = frozenset({"policy_id", "policy_sha256"})
    if present == c0b3_fields:
        if (type(payload["policy_id"]) is str
                and type(payload["policy_sha256"]) is str
                and payload["policy_id"] == _C0B3_POLICY_ID
                and payload["policy_sha256"] == _C0B3_POLICY_SHA256):
            return C0B3_POLICY
        raise PolicyIdentityError("payload C0B-3 identity is unknown or mismatched")
    if present != _PAYLOAD_FIELDS:
        raise PolicyIdentityError("payload has a partial or mixed C0B-4 identity")
    protocol_digest = _sha256(payload["protocol_sha256"], "protocol digest")
    expected = policy_binding(protocol_digest)
    if any(type(payload[field]) is not str or payload[field] != expected[field]
           for field in _PAYLOAD_FIELDS):
        raise PolicyIdentityError("payload C0B-4 identity is unknown or mismatched")
    if (expected_protocol_sha256 is not None
            and protocol_digest != _sha256(
                expected_protocol_sha256, "expected protocol digest")):
        raise PolicyIdentityError("payload protocol digest differs from source identity")
    return current_policy(protocol_digest)


def resolve_header_policy(
        header: Mapping[str, Any], *, expected_protocol_sha256: str | None = None,
) -> ResolvedPolicy:
    """Resolve an exact header family without allowing cross-family fields."""
    if not isinstance(header, Mapping):
        raise PolicyIdentityError("run header must be a mapping")
    present = _present(header, _HEADER_IDENTITY_FIELDS)
    if not present:
        return LEGACY_POLICY
    if present != _HEADER_IDENTITY_FIELDS:
        raise PolicyIdentityError("run header has a partial policy identity")
    c0b3_expected = {
            "benchmark_protocol_id": _C0B3_PROTOCOL_ID,
            "policy_id": _C0B3_POLICY_ID,
            "policy_sha256": _C0B3_POLICY_SHA256,
    }
    if all(type(header[field]) is str and header[field] == c0b3_expected[field]
           for field in _HEADER_IDENTITY_FIELDS):
        return C0B3_POLICY
    if "protocol_sha256" not in header:
        raise PolicyIdentityError("run header C0B-4 identity lacks protocol digest")
    protocol_digest = _sha256(header["protocol_sha256"], "protocol digest")
    expected = header_identity(protocol_digest)
    if any(type(header[field]) is not str or header[field] != expected[field]
           for field in expected):
        raise PolicyIdentityError("run header C0B-4 identity is unknown or mismatched")
    if (expected_protocol_sha256 is not None
            and protocol_digest != _sha256(
                expected_protocol_sha256, "expected protocol digest")):
        raise PolicyIdentityError("run header protocol digest differs from source identity")
    return current_policy(protocol_digest)


def require_current_payload(
        payload: Mapping[str, Any], *, expected_protocol_sha256: str | None = None,
) -> ResolvedPolicy:
    resolved = resolve_payload_policy(
        payload, expected_protocol_sha256=expected_protocol_sha256)
    if resolved.family != PolicyFamily.C0B4:
        raise PolicyIdentityError("C0B-4 operation requires a C0B-4 payload")
    return resolved


def require_current_header(
        header: Mapping[str, Any], *, expected_protocol_sha256: str | None = None,
) -> ResolvedPolicy:
    resolved = resolve_header_policy(
        header, expected_protocol_sha256=expected_protocol_sha256)
    if resolved.family != PolicyFamily.C0B4:
        raise PolicyIdentityError("C0B-4 operation requires a C0B-4 header")
    return resolved


def protocol_identity_payload(repo_root: Path) -> dict[str, Any]:
    """Build the protocol preimage from descriptor-safe exact file reads."""
    from .c0b2_leakscan import read_regular_file

    root = Path(repo_root)
    components: dict[str, str] = {}
    for name, relative in _COMPONENT_PATHS:
        _verified, body = read_regular_file(root / relative, trusted_root=root)
        components[name] = hashlib.sha256(body).hexdigest()
    return {
        "benchmark_protocol_id": BENCHMARK_PROTOCOL_ID,
        "components": components,
    }


def protocol_sha256(repo_root: Path) -> str:
    """Return the exact composite protocol digest for a verified source tree."""
    return hashlib.sha256(
        canonical_json_bytes(protocol_identity_payload(repo_root))).hexdigest()


_computed_policy_sha256 = hashlib.sha256(
    canonical_json_bytes(policy_payload())).hexdigest()
if _computed_policy_sha256 != POLICY_SHA256:
    raise RuntimeError("frozen C0B-4 policy payload differs from its declared SHA-256")
