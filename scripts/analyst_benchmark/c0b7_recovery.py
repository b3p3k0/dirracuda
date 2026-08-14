"""Read-only C0B-7 recovery of the completed C0B-6 public evidence.

DISPOSITION: benchmark-only; retain with the accepted C0B outcome.
"""
from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

from .c0b2_schema import CATEGORIES, canonical_json
from .c0b2_stage_f import build_d50_component, validate_acceptance_component_artifact
from .c0b6_checkpoint import sha256_json
from .c0b6_lineage import (
    FROZEN_PARENT_BINDING,
    ParentPaths,
    _PinnedSQLite,
    verify_parents_readonly,
)
from .c0b6_plan import SELECTION, candidate_id
from .c0b6_replay import (
    _c6_artifacts,
    replay_c0b6_connection,
    verify_c0b6_terminal_readonly,
)
from .c0b6_schema import validate_artifact
from .c0b6_scoring import (
    _normalize_d50_component,
    build_acceptance_aggregate,
)


RUN_ID = "c0b6-20260814-154202-472a5f0a12e0bf0dded7a13a"
SOURCE_COMMIT = "c7d5eda633f11d9aeb98ccd17b326cbec08ad1c1"
CHECKPOINT_SHA256 = "f91637933737a054e580f1915d2c239a6d5c5d2756b7e68059d519ebe729e61c"
SNAPSHOT_SHA256 = "8ae6d0ef009aa4f17b9e75657faa20392267f66d311974df13b72e5d32dc6de4"
ANCHOR_SHA256 = "bc2b21296e4dffa1cdb6cb05fe822fb187220f7974c92b77df7ede46cfe77097"
RECEIPT_SHA256 = "ce0c4f8894260a69b39eec111785d6dbc067f44f8d7b350bb309e971bc8be783"
FAILURE_SHA256 = "2d17a6fda6a3e4105f9ebd36ca7b9374a37524b4cba22180af6bfc0d87b5b1c3"


class C0B7RecoveryError(RuntimeError):
    """Pinned evidence cannot produce the one frozen recovery result."""


def _ordered_validation_view(value: Mapping[str, Any]) -> dict[str, Any]:
    """Reorder exact category mappings without changing canonical JSON meaning."""
    if type(value) is not dict:
        raise C0B7RecoveryError("legacy validation source is not an object")
    result = deepcopy(value)
    rows = result.get("selections", result.get("candidates"))
    if type(rows) is not list or not rows:
        raise C0B7RecoveryError("legacy validation source lacks candidate rows")
    for row in rows:
        try:
            recall = row["quality"]["category_recall"]
        except (KeyError, TypeError) as exc:
            raise C0B7RecoveryError("legacy category mapping is absent") from exc
        if type(recall) is not dict or set(recall) != set(CATEGORIES):
            raise C0B7RecoveryError("legacy category mapping is not exact")
        row["quality"]["category_recall"] = {
            category: recall[category] for category in CATEGORIES}
    if canonical_json(result) != canonical_json(value):
        raise C0B7RecoveryError("validation view changed canonical evidence")
    return result


def _corrected_d50(parent_facts: Any, corpus: Any) -> dict[str, Any]:
    facts = parent_facts.c0b3_d50_facts
    parent = FROZEN_PARENT_BINDING["execution_parent"]
    decision = _ordered_validation_view(facts.final_d_decision)
    aggregate = _ordered_validation_view(facts.d4_aggregate)
    try:
        artifact = build_d50_component(
            decision, aggregate,
            stage_d_decision_sha256=parent["final_d_decision_sha256"],
            f_candidate_id=candidate_id(), corpus=corpus)
        normalized = validate_acceptance_component_artifact(artifact)
    except Exception as exc:
        raise C0B7RecoveryError("verified D50 evidence does not rederive") from exc
    value = {key: item for key, item in normalized.items()
             if key not in {"version", "policy_id", "policy_sha256"}}
    value["negative_retained_findings"] = facts.negative_retained_findings
    return _normalize_d50_component(value, corpus=corpus)


def _json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _corrected_schedule(master_sha: str, plans: Mapping[str, Mapping[str, Any]],
                        artifacts: Mapping[tuple[str, str], tuple[dict, str]],
                        active: list[str], aggregate_hashes: Mapping[str, str]) -> None:
    """Validate cursor self-hashes against their documented preimage."""
    order = ("F72_20260811", "F72_20260818", "C44_1")
    previous = master_sha
    expected_transitions = set()
    for index, lane_id in enumerate(active):
        activation = artifacts.get(("plan_activation", lane_id))
        later = order[order.index(lane_id) + 1:]
        inactive = sorted(row["work_id"] for later_id in later
                          for row in plans[later_id]["work"])
        if (activation is None
                or activation[0]["plan_sha256"] != plans[lane_id]["plan_sha256"]
                or activation[0]["prerequisite_sha256"] != previous
                or activation[0]["activated_work_ids"] != sorted(
                    row["work_id"] for row in plans[lane_id]["work"])
                or activation[0]["inactive_work_ids"] != inactive):
            raise C0B7RecoveryError("activation chain does not rederive")
        if index:
            prior = active[index - 1]
            key = ("cursor_transition", prior)
            expected_transitions.add(key)
            stored = artifacts.get(key)
            census = sha256_json({
                "lane_id": prior,
                "completed_work_ids": sorted(
                    row["work_id"] for row in plans[prior]["work"]),
            })
            if stored is None:
                raise C0B7RecoveryError("cursor transition is absent")
            transition = stored[0]
            if (transition["from_lane_id"] != prior
                    or transition["to_lane_id"] != lane_id
                    or transition["from_aggregate_sha256"] != previous
                    or transition["to_plan_sha256"] != plans[lane_id]["plan_sha256"]
                    or transition["completed_work_census_sha256"] != census
                    or transition["transition_sha256"] != sha256_json(
                        transition, omit="transition_sha256")):
                raise C0B7RecoveryError("cursor transition does not rederive")
        previous = aggregate_hashes[lane_id]
    actual = {key for key in artifacts if key[0] == "cursor_transition"}
    if actual != expected_transitions:
        raise C0B7RecoveryError("cursor-transition owner census changed")


def _recover_connection(source: sqlite3.Connection, *, parent_facts: Any,
                        corpus: Any) -> dict[str, Any]:
    """Replay one pinned source through an in-memory quality-terminal projection."""
    memory = sqlite3.connect(":memory:")
    try:
        source.backup(memory)
        artifacts = _c6_artifacts(memory)
        header = json.loads(memory.execute(
            "SELECT json FROM run_header WHERE id=1").fetchone()[0])
        if (header.get("run_id") != RUN_ID
                or header.get("git_head") != SOURCE_COMMIT):
            raise C0B7RecoveryError("C0B-6 source identity changed")
        failure = artifacts.get(("failure", "terminal"))
        evidence = artifacts.get(("failure_evidence", "terminal"))
        if (failure is None or failure[1] != FAILURE_SHA256 or evidence is None
                or failure[0].get("terminal") != "BLOCKED_PROVENANCE"
                or failure[0].get("failure_origin") != "acceptance_derivation"
                or failure[0].get("charged_call_total") != 240):
            raise C0B7RecoveryError("C0B-6 terminal is not the frozen recovery source")
        first, first_sha = artifacts[("lane_aggregate", "F72_20260811")]
        second, second_sha = artifacts[("lane_aggregate", "F72_20260818")]
        c44, c44_sha = artifacts[("c44_aggregate", "C44_1")]
        master, master_sha = artifacts[("master_plan", "master")]
        c44_plan = artifacts[("lane_plan", "C44_1")][0]
        d50 = _corrected_d50(parent_facts, corpus)
        acceptance = build_acceptance_aggregate(
            c44, d50, first, corpus=corpus,
            acceptance_plan_sha256=c44_plan["plan_sha256"],
            cancellation_health_passed=True, provenance_passed=True,
            safety_passed=True)
        acceptance_sha = sha256_json(acceptance)
        identity = {key: header[key] for key in (
            "policy_id", "policy_sha256", "protocol_sha256")}
        terminal = "CONFIRMED" if acceptance["passed"] else "INCONCLUSIVE"
        result = validate_artifact({
            "version": "c0b6-result-v1", **identity,
            "terminal": terminal,
            "reason": ("complete_public_acceptance_passed" if acceptance["passed"]
                       else "complete_corpus_acceptance_failed"),
            "master_plan_sha256": master_sha,
            "lane_aggregate_sha256s": {
                "f72_seed20260811_sha256": first_sha,
                "f72_seed20260818_sha256": second_sha,
                "c44_scored_sha256": c44_sha},
            "acceptance_aggregate_sha256": acceptance_sha,
            "selection": dict(SELECTION) if acceptance["passed"] else None,
        })
        result_sha = sha256_json(result)
        completion = validate_artifact({
            "version": "c0b6-completion-v1", **identity,
            "outcome": terminal, "artifact_sha256": result_sha,
            "facts": ({"confirmed": True} if acceptance["passed"] else
                      {"deterministic_stop": True,
                       "reason": "complete_corpus_acceptance_failed"}),
        })
        with memory:
            memory.execute("DELETE FROM backup_receipts")
            memory.execute("DELETE FROM artifacts WHERE kind IN "
                           "('failure','failure_evidence')")
            for kind, owner, value in (
                    ("acceptance_aggregate", "complete", acceptance),
                    ("result", "terminal", result),
                    ("completion", "terminal", completion)):
                raw, digest = _json(value), sha256_json(value)
                memory.execute("INSERT INTO artifacts VALUES(?,?,?,?,0)",
                               (kind, owner, digest, raw))
            memory.execute("UPDATE run_state SET state=? WHERE id=1", (terminal,))
        import scripts.analyst_benchmark.c0b6_replay as replay
        with patch.object(replay, "derive_parent_d50_component",
                          lambda *_args, **_kwargs: deepcopy(d50)), \
                patch.object(replay, "_c6_schedule", _corrected_schedule):
            replayed = replay_c0b6_connection(
                memory, parent_facts=parent_facts, require_receipt=False)
        if (replayed.acceptance_aggregate != acceptance
                or replayed.result != result
                or set(replayed.lane_aggregates) != {
                    "F72_20260811", "F72_20260818", "C44_1"}):
            raise C0B7RecoveryError("independent recovery replay disagrees")
        return {
            "acceptance": acceptance,
            "result_sha256": result_sha,
            "public_summary_sha256": sha256_json(replayed.public_summary),
            "stability_lane_sha256": second_sha,
            "recovered_terminal": (
                "RECOVERED_CONFIRMED" if acceptance["passed"]
                else "RECOVERED_INCONCLUSIVE"),
        }
    finally:
        memory.close()


def recover(checkpoint: Path, snapshot: Path, *, trusted_root: Path) -> dict[str, Any]:
    """Recover once from exact checkpoint/snapshot evidence without modifying either."""
    checkpoint, snapshot, trusted_root = map(Path, (checkpoint, snapshot, trusted_root))
    parents = None
    with _PinnedSQLite(checkpoint, trusted_root) as pinned:
        if pinned.sha256 != CHECKPOINT_SHA256:
            raise C0B7RecoveryError("checkpoint bytes differ from frozen evidence")
        assert pinned.conn is not None
        rows = pinned.conn.execute(
            "SELECT c0b3_db_path,c0b3_snapshot_path,c0b4_db_path,c0b4_snapshot_path "
            "FROM parent_files WHERE id=1").fetchall()
        if len(rows) != 1:
            raise C0B7RecoveryError("parent path census changed")
        parents = verify_parents_readonly(ParentPaths(*rows[0]))
    verified = verify_c0b6_terminal_readonly(
        checkpoint, snapshot, trusted_root=trusted_root, parent_facts=parents)
    if (verified.backup_anchor_sha256 != ANCHOR_SHA256
            or verified.backup_receipt_sha256 != RECEIPT_SHA256
            or verified.backup_snapshot_sha256 != SNAPSHOT_SHA256):
        raise C0B7RecoveryError("backup receipt differs from frozen evidence")
    from . import goldset
    from .c0b2_plan import build_master_manifest, master_manifest_payload
    from .c0b2_stage_f_plan import load_public_corpus
    source = goldset.load(verify=True)
    manifest = build_master_manifest(source)
    corpus = load_public_corpus(
        master_manifest_payload(manifest), master_manifest_sha256=manifest.sha256,
        source=source)
    recovered = []
    for path, expected in ((checkpoint, CHECKPOINT_SHA256),
                           (snapshot, SNAPSHOT_SHA256)):
        with _PinnedSQLite(path, trusted_root) as pinned:
            if pinned.sha256 != expected or pinned.conn is None:
                raise C0B7RecoveryError("recovery source bytes changed")
            recovered.append(_recover_connection(
                pinned.conn, parent_facts=parents, corpus=corpus))
    if recovered[0] != recovered[1]:
        raise C0B7RecoveryError("checkpoint and snapshot recovery disagree")
    value = {
        "version": "c0b7-recovery-v1", "source_run_id": RUN_ID,
        "source_commit": SOURCE_COMMIT,
        "source_terminal": "BLOCKED_PROVENANCE",
        "source_failure_origin": "acceptance_derivation",
        "charged_calls": 240,
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "snapshot_sha256": SNAPSHOT_SHA256,
        **recovered[0],
    }
    value["recovery_sha256"] = sha256_json(value)
    return value


def render_public(value: Mapping[str, Any]) -> bytes:
    return _json(value)
