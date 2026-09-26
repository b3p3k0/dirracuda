"""Offline tests for the C0B-7 immutable evidence recovery."""
from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.analyst_benchmark import c0b7_recovery as recovery
from scripts.analyst_benchmark.c0b2_schema import CATEGORIES, canonical_json


def _legacy(kind: str = "selections") -> dict:
    return {kind: [{"quality": {"category_recall": {
        "contact": {"support": 6, "true_positives": 6},
        "demographic": {"support": 6, "true_positives": 6},
        "financial": {"support": 6, "true_positives": 6},
        "pii": {"support": 6, "true_positives": 6},
    }}}]}


@pytest.mark.parametrize("kind", ["selections", "candidates"])
def test_validation_view_changes_order_only(kind: str) -> None:
    source = _legacy(kind)
    before = deepcopy(source)
    value = recovery._ordered_validation_view(source)
    assert source == before
    assert list(value[kind][0]["quality"]["category_recall"]) == list(CATEGORIES)
    assert canonical_json(value) == canonical_json(source)


def test_validation_view_rejects_category_drift() -> None:
    source = _legacy()
    source["selections"][0]["quality"]["category_recall"]["extra"] = {}
    with pytest.raises(recovery.C0B7RecoveryError, match="not exact"):
        recovery._ordered_validation_view(source)


def test_module_has_no_network_client_or_transport_import() -> None:
    tree = ast.parse(Path(recovery.__file__).read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any("client" in name or "transport" in name for name in imported)


def test_frozen_evidence_identities_are_lowercase_sha256() -> None:
    for value in (
            recovery.CHECKPOINT_SHA256, recovery.SNAPSHOT_SHA256,
            recovery.ANCHOR_SHA256, recovery.RECEIPT_SHA256,
            recovery.FAILURE_SHA256):
        assert len(value) == 64 and set(value) <= set("0123456789abcdef")


def test_cursor_self_hash_uses_omitted_field_preimage() -> None:
    order = ("F72_20260811", "F72_20260818", "C44_1")
    plans = {lane: {
        "plan_sha256": str(index + 1) * 64,
        "work": [{"work_id": f"{lane}-work"}],
    } for index, lane in enumerate(order)}
    aggregate_hashes = {lane: chr(97 + index) * 64
                        for index, lane in enumerate(order)}
    artifacts = {}
    previous = "m" * 64
    for index, lane in enumerate(order):
        later = order[index + 1:]
        artifacts[("plan_activation", lane)] = ({
            "plan_sha256": plans[lane]["plan_sha256"],
            "prerequisite_sha256": previous,
            "activated_work_ids": [f"{lane}-work"],
            "inactive_work_ids": sorted(f"{item}-work" for item in later),
        }, "e" * 64)
        if index:
            prior = order[index - 1]
            value = {
                "from_lane_id": prior, "to_lane_id": lane,
                "from_aggregate_sha256": previous,
                "to_plan_sha256": plans[lane]["plan_sha256"],
                "completed_work_census_sha256": recovery.sha256_json({
                    "lane_id": prior,
                    "completed_work_ids": [f"{prior}-work"],
                }),
            }
            value["transition_sha256"] = recovery.sha256_json(value)
            artifacts[("cursor_transition", prior)] = (value, "f" * 64)
        previous = aggregate_hashes[lane]
    recovery._corrected_schedule(
        "m" * 64, plans, artifacts, list(order), aggregate_hashes)
