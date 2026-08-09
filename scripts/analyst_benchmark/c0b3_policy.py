"""Frozen C0B-3 scoring-policy and benchmark-protocol identity helpers.

The legacy C0B-2 header and artifacts carry no policy fields.  This module keeps
that absence meaningful without adding defaults to their strict schemas.  C0B-3
identity is accepted only when every required field is present and exact.

DISPOSITION: retain through C0B; production consumes the selected result, not
the benchmark policy dispatcher.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

BENCHMARK_PROTOCOL_ID = "c0b3-assistive-confirmation-v1"
POLICY_ID = "c0b3-assistive-bounded-fp-v1"
POLICY_SHA256 = "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235"
LEGACY_POLICY_ID = "c0b2-strict-zero-intermediate-v1"

_HEADER_IDENTITY_FIELDS = frozenset({
    "benchmark_protocol_id", "policy_id", "policy_sha256",
})
_PAYLOAD_POLICY_FIELDS = frozenset({"policy_id", "policy_sha256"})
_GATE_ORDER = (
    "final_acceptance", "stage_c", "stage_d3_d4", "stage_f_per_seed",
)
_LEGACY_LIMITS = {
    "final_acceptance": 1,
    "stage_c": 1,
    "stage_d3_d4": 0,
    "stage_f_per_seed": 0,
}
_COMPONENT_PATHS = (
    ("c0b2_protocol_sha256",
     "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B2.md"),
    ("c0b2_public_schema_doc_sha256",
     "docs/dev/ollama_integration/BENCHMARK_PUBLIC_CDF_SCHEMA.md"),
    ("c0b3_protocol_sha256",
     "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B3.md"),
    ("contract_errata_sha256",
     "docs/dev/ollama_integration/CONTRACT_ERRATA.md"),
)


class PolicyIdentityError(ValueError):
    """A header, artifact, or requested policy identity is not exact."""


class PolicyFamily(str, Enum):
    """Closed policy families recognized by the C0B-3 dispatcher."""

    LEGACY_C0B2 = "legacy_c0b2"
    CURRENT_C0B3 = "current_c0b3"


@dataclass(frozen=True)
class ResolvedPolicy:
    """Internal policy resolution; fields are not serialized into legacy data."""

    family: PolicyFamily
    benchmark_protocol_id: str | None
    policy_id: str
    policy_sha256: str | None


LEGACY_POLICY = ResolvedPolicy(
    family=PolicyFamily.LEGACY_C0B2,
    benchmark_protocol_id=None,
    policy_id=LEGACY_POLICY_ID,
    policy_sha256=None,
)
CURRENT_POLICY = ResolvedPolicy(
    family=PolicyFamily.CURRENT_C0B3,
    benchmark_protocol_id=BENCHMARK_PROTOCOL_ID,
    policy_id=POLICY_ID,
    policy_sha256=POLICY_SHA256,
)


def canonical_json_bytes(value: Any) -> bytes:
    """Return the exact UTF-8 canonical encoding frozen by C0B-3."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def policy_payload() -> dict[str, Any]:
    """Return a fresh copy of the frozen scoring-policy hash preimage."""
    return {
        "gates": {
            "final_acceptance": {
                "max_false_positive_documents": 1,
                "negative_document_count": 40,
            },
            "stage_c": {
                "max_false_positive_documents": 1,
                "negative_document_count": 12,
            },
            "stage_d3_d4": {
                "max_false_positive_documents": 1,
                "negative_document_count": 12,
            },
            "stage_f_per_seed": {
                "max_false_positive_documents": 1,
                "negative_document_count": 16,
            },
        },
        "policy_id": POLICY_ID,
        "unit": "negative_document",
    }


def policy_binding() -> dict[str, str]:
    """Return the two required policy fields for a current D/F payload."""
    return {"policy_id": POLICY_ID, "policy_sha256": POLICY_SHA256}


def header_identity() -> dict[str, str]:
    """Return the three required policy/protocol fields for a C0B-3 header."""
    return {"benchmark_protocol_id": BENCHMARK_PROTOCOL_ID, **policy_binding()}


def _mapping(value: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PolicyIdentityError(f"{label} must be a mapping")
    return value


def _present_fields(value: Mapping[str, Any], fields: frozenset[str]) -> frozenset[str]:
    return frozenset(field for field in fields if field in value)


def resolve_header_policy(header: Mapping[str, Any]) -> ResolvedPolicy:
    """Resolve only the two frozen header shapes; reject every mixed shape."""
    value = _mapping(header, "run header")
    present = _present_fields(value, _HEADER_IDENTITY_FIELDS)
    if not present:
        return LEGACY_POLICY
    if present != _HEADER_IDENTITY_FIELDS:
        raise PolicyIdentityError("run header has a partial policy identity")
    expected = header_identity()
    if any(type(value[field]) is not str or value[field] != expected[field]
           for field in _HEADER_IDENTITY_FIELDS):
        raise PolicyIdentityError("run header policy identity is unknown or mismatched")
    return CURRENT_POLICY


def resolve_payload_policy(payload: Mapping[str, Any]) -> ResolvedPolicy:
    """Resolve an absent legacy or exact current D/F artifact policy binding."""
    value = _mapping(payload, "policy-bearing payload")
    present = _present_fields(value, _PAYLOAD_POLICY_FIELDS)
    if not present:
        return LEGACY_POLICY
    if present != _PAYLOAD_POLICY_FIELDS:
        raise PolicyIdentityError("payload has a partial policy identity")
    expected = policy_binding()
    if any(type(value[field]) is not str or value[field] != expected[field]
           for field in _PAYLOAD_POLICY_FIELDS):
        raise PolicyIdentityError("payload policy identity is unknown or mismatched")
    return CURRENT_POLICY


def require_current_header(header: Mapping[str, Any]) -> ResolvedPolicy:
    """Require a complete current header before a C0B-3 mutation or HTTP call."""
    resolved = resolve_header_policy(header)
    if resolved != CURRENT_POLICY:
        raise PolicyIdentityError("C0B-3 operation requires a current header")
    return resolved


def require_legacy_header(header: Mapping[str, Any]) -> ResolvedPolicy:
    """Require an exact legacy header before a C0B-2 mutating operation."""
    resolved = resolve_header_policy(header)
    if resolved != LEGACY_POLICY:
        raise PolicyIdentityError("C0B-2 operation requires a legacy header")
    return resolved


def require_current_payload(payload: Mapping[str, Any]) -> ResolvedPolicy:
    """Require the exact policy binding carried by every current D/F payload."""
    resolved = resolve_payload_policy(payload)
    if resolved != CURRENT_POLICY:
        raise PolicyIdentityError("C0B-3 D/F payload requires a current policy binding")
    return resolved


def require_expected_header(
        header: Mapping[str, Any], protocol_id: str | None) -> ResolvedPolicy:
    """Require the namespace-selected header family before any mutation."""
    if protocol_id is None:
        return require_legacy_header(header)
    if protocol_id == BENCHMARK_PROTOCOL_ID:
        return require_current_header(header)
    raise PolicyIdentityError("unknown expected benchmark protocol")


def read_checkpoint_header(path: Path) -> dict[str, Any]:
    """Read and strictly validate a checkpoint header without opening it writable."""
    from .c0b2_checkpoint import _stored_header
    from .c0b2_fsprobe import _readonly_uri, _regular_owner_file

    target = Path(path)
    _regular_owner_file(target)
    conn = sqlite3.connect(_readonly_uri(target), uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    try:
        return _stored_header(conn)
    finally:
        conn.close()


def require_checkpoint_header(path: Path, protocol_id: str | None) -> dict[str, Any]:
    """Read-only namespace guard used before Checkpoint.open can add tables."""
    header = read_checkpoint_header(path)
    require_expected_header(header, protocol_id)
    return header


def reported_identity(header: Mapping[str, Any]) -> dict[str, str | None]:
    """Return explicit content-free protocol/policy identity for status output."""
    policy = resolve_header_policy(header)
    return {
        "benchmark_protocol_id": policy.benchmark_protocol_id,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.policy_sha256,
    }


def frozen_task_paths(protocol_id: str | None) -> frozenset[str]:
    """Return a distinct exact source tree without mutating the legacy set."""
    from .c0b2_leakscan import FROZEN_C0B2_PUBLIC_PATHS, FROZEN_C0B3_PUBLIC_PATHS

    if protocol_id is None:
        return FROZEN_C0B2_PUBLIC_PATHS
    if protocol_id == BENCHMARK_PROTOCOL_ID:
        return FROZEN_C0B3_PUBLIC_PATHS
    raise PolicyIdentityError("unknown benchmark protocol task tree")


def task_tree_sha256(repo_root: Path, protocol_id: str | None) -> str:
    """Hash every verified file in the namespace's exact frozen task tree."""
    from .c0b2_leakscan import read_regular_file

    root = Path(repo_root)
    rows = {}
    for relative in sorted(frozen_task_paths(protocol_id)):
        _verified, body = read_regular_file(root / relative, trusted_root=root)
        rows[relative] = hashlib.sha256(body).hexdigest()
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def assert_clean_task_delta(seal: Any, protocol_id: str | None) -> None:
    """Reject dirty files in the exact selected benchmark source tree."""
    paths = frozen_task_paths(protocol_id)
    dirty = tuple(entry.path for entry in seal.entries if entry.path in paths)
    if dirty:
        raise PolicyIdentityError(
            "commit the frozen public implementation before create: "
            + ", ".join(dirty))


def false_positive_limit(policy: ResolvedPolicy, gate: str) -> int:
    """Return the preregistered document limit without accepting unknown inputs."""
    if type(gate) is not str or gate not in _GATE_ORDER:
        raise PolicyIdentityError("false-positive gate is unknown")
    if policy == LEGACY_POLICY:
        return _LEGACY_LIMITS[gate]
    if policy == CURRENT_POLICY:
        return int(policy_payload()["gates"][gate]["max_false_positive_documents"])
    raise PolicyIdentityError("resolved policy is unknown")


def false_positive_failure_reason(policy: ResolvedPolicy, gate: str) -> str:
    """Return the exact failure identity for a recognized policy and gate."""
    limit = false_positive_limit(policy, gate)
    if gate in {"stage_d3_d4", "stage_f_per_seed"} and limit == 0:
        return "negative_false_positive_present"
    return "negative_false_positive_above_1"


def protocol_identity_payload(repo_root: Path) -> dict[str, Any]:
    """Build the composite protocol preimage from verified exact file reads."""
    from .c0b2_leakscan import read_regular_file

    root = Path(repo_root)
    components: dict[str, str] = {}
    for name, relative in _COMPONENT_PATHS:
        _verified, body = read_regular_file(
            root / relative, trusted_root=root)
        components[name] = hashlib.sha256(body).hexdigest()
    return {
        "benchmark_protocol_id": BENCHMARK_PROTOCOL_ID,
        "components": components,
    }


def protocol_sha256(repo_root: Path) -> str:
    """Return the frozen composite benchmark-protocol digest for this tree."""
    return hashlib.sha256(
        canonical_json_bytes(protocol_identity_payload(repo_root))).hexdigest()


_computed_policy_sha256 = hashlib.sha256(
    canonical_json_bytes(policy_payload())).hexdigest()
if _computed_policy_sha256 != POLICY_SHA256:
    raise RuntimeError("frozen C0B-3 policy payload differs from its declared SHA-256")
