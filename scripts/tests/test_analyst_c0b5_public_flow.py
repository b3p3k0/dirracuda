"""C0B-5 parent-lineage and independent C0B-4 replay tests."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from shared.path_service import get_paths
from scripts.analyst_benchmark import c0b5_lineage as lineage, goldset
from scripts.analyst_benchmark import c0b5_replay as replay
from scripts.analyst_benchmark.c0b2_plan import (
    build_master_manifest, master_manifest_payload,
)
from scripts.analyst_benchmark.c0b2_stage_f_plan import load_public_corpus
from scripts.analyst_benchmark.c0b5_checkpoint import C0B5Checkpoint, sha256_json
from scripts.analyst_benchmark.c0b5_plan import build_master_plan
from scripts.analyst_benchmark.c0b5_policy import POLICY_ID, POLICY_SHA256
from scripts.tests.test_analyst_c0b5_checkpoint import header as checkpoint_header


C3_RUN = "c0b3-20260809-154924-19afcaab26984160f20ec075"
C4_RUN = "c0b4-20260811-210848-d2b52272f3aabb156f55d166"
C3_SNAPSHOT = (
    "snapshot-b37396143265013ed01361d7ec31edff3d84c358d2f6c8ce932df39b21e61c56-"
    "3cb8b072b119667cea73f5a2.sqlite3"
)
C4_SNAPSHOT = (
    "snapshot-60ac16a8962a5b87b16cc5bf7beeaae3d8009cf4d26a5441656ef125d2602358-"
    "d250024af75103a5232dd66c.sqlite3"
)


def _actual_paths() -> lineage.ParentPaths:
    root = get_paths().experimental_dir / "analyst_bench"
    c3 = root / "runs" / C3_RUN
    c4 = root / "runs" / C4_RUN
    paths = lineage.ParentPaths(
        c3 / "checkpoint.sqlite3", c3 / "backups" / C3_SNAPSHOT,
        c4 / "checkpoint.sqlite3", c4 / "backups" / C4_SNAPSHOT)
    if not all(path.is_file() for path in (
            paths.c0b3_checkpoint, paths.c0b3_snapshot,
            paths.c0b4_checkpoint, paths.c0b4_snapshot)):
        pytest.skip("immutable C0B-3/C0B-4 parent artifacts are not installed")
    return paths


def _copy(path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for parent in (destination.parent, *destination.parent.parents):
        if parent == destination.parents[-1]:
            break
    shutil.copyfile(path, destination)
    os.chmod(destination, 0o600)


def _copied_parents(tmp_path: Path) -> tuple[Path, lineage.ParentPaths]:
    actual = _actual_paths()
    root = tmp_path / "analyst_bench"
    root.mkdir(mode=0o700)
    c3 = root / "runs" / C3_RUN
    c4 = root / "runs" / C4_RUN
    paths = lineage.ParentPaths(
        c3 / "checkpoint.sqlite3", c3 / "backups" / C3_SNAPSHOT,
        c4 / "checkpoint.sqlite3", c4 / "backups" / C4_SNAPSHOT)
    for source, target in zip((
            actual.c0b3_checkpoint, actual.c0b3_snapshot,
            actual.c0b4_checkpoint, actual.c0b4_snapshot), (
            paths.c0b3_checkpoint, paths.c0b3_snapshot,
            paths.c0b4_checkpoint, paths.c0b4_snapshot), strict=True):
        _copy(source, target)
    for directory in (root / "runs", c3, c3 / "backups", c4, c4 / "backups"):
        os.chmod(directory, 0o700)
    return root, paths


def _rewrite_row(db: Path, sql: str, parameters: tuple[object, ...]) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(sql, parameters)
        conn.commit()
    finally:
        conn.close()


def _rehash(value: dict[str, object]) -> tuple[str, str]:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def test_protocol_parent_literals_are_copied_exactly() -> None:
    protocol = Path("docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B5.md").read_text()
    blocks = [json.loads(value) for value in __import__("re").findall(
        r"```json\n(.*?)\n```", protocol, __import__("re").S)]
    c3 = next(value for value in blocks if value.get("run_id") == C3_RUN)
    c4 = next(value for value in blocks if value.get("run_id") == C4_RUN)
    assert lineage.FROZEN_EXECUTION_PARENT == c3
    assert lineage.FROZEN_OBSERVED_C0B4 == c4
    assert lineage.validate_parent_binding({
        "execution_parent": c3, "observed_c0b4": c4,
    }) == lineage.FROZEN_PARENT_BINDING


def test_partial_or_mixed_parent_binding_fails_closed() -> None:
    partial = {"execution_parent": lineage.FROZEN_EXECUTION_PARENT}
    with pytest.raises(lineage.C0B5LineageError, match="parent binding"):
        lineage.validate_parent_binding(partial)
    mixed = json.loads(json.dumps(lineage.FROZEN_PARENT_BINDING))
    mixed["observed_c0b4"]["policy_id"] = "c0b3-assistive-bounded-fp-v1"
    with pytest.raises(lineage.C0B5LineageError, match="parent binding"):
        lineage.validate_parent_binding(mixed)


def test_replay_imports_only_the_frozen_c0b4_answer_surface() -> None:
    tree = ast.parse(Path(replay.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    c0b4 = {name for name in imported if ".c0b4_" in name or name.startswith("c0b4_")}
    assert c0b4 == {"c0b4_answer"}


def test_exact_json_rejects_a_coherently_rehashed_noncanonical_row() -> None:
    value = {"version": "example", "count": 1}
    canonical, digest = _rehash(value)
    assert replay._exact_json(canonical, digest, "example") == value
    noncanonical = json.dumps(value, indent=2)
    with pytest.raises(replay.C0B5ReplayError, match="noncanonical"):
        replay._exact_json(
            noncanonical, hashlib.sha256(noncanonical.encode()).hexdigest(), "example")


def test_descriptor_pinned_sqlite_rejects_file_and_directory_symlinks(
        tmp_path: Path) -> None:
    root = tmp_path / "root"
    real = root / "real"
    root.mkdir(mode=0o700)
    real.mkdir(mode=0o700)
    db = real / "db.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("create table sample(value integer)")
    conn.commit()
    conn.close()
    os.chmod(db, 0o600)
    file_link = real / "file-link.sqlite3"
    file_link.symlink_to(db)
    with pytest.raises((OSError, lineage.C0B5LineageError)):
        with lineage._PinnedSQLite(file_link, root):
            pass
    directory_link = root / "directory-link"
    directory_link.symlink_to(real, target_is_directory=True)
    with pytest.raises((OSError, lineage.C0B5LineageError)):
        with lineage._PinnedSQLite(directory_link / db.name, root):
            pass


def test_immutable_c0b4_checkpoint_and_snapshot_replay_exactly() -> None:
    paths = _actual_paths()
    root = paths.c0b4_checkpoint.parents[2]
    facts = replay.verify_observed_c0b4_readonly(
        paths.c0b4_checkpoint, paths.c0b4_snapshot, trusted_root=root)
    assert (facts.terminal, facts.reason, facts.failure_reasons) == (
        "INCONCLUSIVE", "seed17_no_qualifier",
        ("negative_false_positive_above_1",))
    assert (facts.calls_total, facts.invocations) == (96, 1)
    assert facts.inactive_lane_census == {
        "F72_20260804": {"planned_work_rows": 92, "activation_rows": 0,
                          "attempt_rows": 0, "aggregate_rows": 0},
        "C44_1": {"planned_work_rows": 44, "activation_rows": 0,
                   "attempt_rows": 0, "aggregate_rows": 0},
    }


def test_immutable_c0b3_d50_attempts_rederive_one_public_review_row() -> None:
    paths = _actual_paths()
    conn = sqlite3.connect(f"file:{paths.c0b3_checkpoint}?mode=ro", uri=True)
    try:
        facts = replay.replay_c0b3_d50_connection(conn)
    finally:
        conn.close()
    assert facts.negative_retained_findings == 1
    assert facts.false_positive_documents == ({
        "component": "D50_CONFIRMATION",
        "document_id": "neg_nearmiss_009", "categories": ["financial"],
        "public_template_family": "near_miss_invalid_iban_template_placeholder",
        "negative_retained_findings": 1,
    },)


def test_c0b3_d50_coherent_answer_tamper_fails_raw_replay(tmp_path: Path) -> None:
    _root, paths = _copied_parents(tmp_path)
    conn = sqlite3.connect(paths.c0b3_checkpoint)
    plan = json.loads(conn.execute(
        "SELECT plan_json FROM phase_plans WHERE plan_key='D4_CONFIRMATION'"
    ).fetchone()[0])
    target = next(row["work_id"] for row in plan["work"]
                  if row["doc_id"] == "neg_nearmiss_009")
    source = next(row["work_id"] for row in plan["work"]
                  if row["doc_id"].startswith("neg_clean_"))
    response = conn.execute(
        "SELECT response FROM attempts WHERE work_id=? AND state='ACCEPTED'",
        (source,)).fetchone()[0]
    metadata_raw = conn.execute(
        "SELECT metadata_json FROM attempts WHERE work_id=? AND state='ACCEPTED'",
        (target,)).fetchone()[0]
    metadata = json.loads(metadata_raw)
    canonical = json.dumps(json.loads(response), sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False)
    metadata["content_bytes"] = len(response.encode())
    metadata["canonical_content_sha256"] = hashlib.sha256(
        canonical.encode()).hexdigest()
    conn.execute(
        "UPDATE attempts SET response=?,metadata_json=? WHERE work_id=? "
        "AND state='ACCEPTED'",
        (response, json.dumps(metadata, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False), target))
    conn.commit()
    conn.execute("PRAGMA query_only=ON")
    with pytest.raises(replay.C0B5ReplayError, match="aggregate does not independently"):
        replay.replay_c0b3_d50_connection(conn)
    conn.close()


def test_child_schedule_rejects_inactive_and_cursor_tamper() -> None:
    lane_ids = ("F72_20260804", "F72_20260811", "C44_1")
    plans = {lane: {"plan_sha256": str(index) * 64,
                    "work": [{"work_id": chr(96 + index) * 64}]}
             for index, lane in enumerate(lane_ids, 1)}
    aggregate = {lane: chr(100 + index) * 64
                 for index, lane in enumerate(lane_ids)}
    master_sha = "f" * 64
    artifacts = {}
    previous = master_sha
    for index, lane in enumerate(lane_ids):
        later = lane_ids[index + 1:]
        artifacts[("plan_activation", lane)] = ({
            "plan_sha256": plans[lane]["plan_sha256"],
            "prerequisite_sha256": previous,
            "activated_work_ids": sorted(row["work_id"] for row in plans[lane]["work"]),
            "inactive_work_ids": sorted(row["work_id"] for item in later
                                        for row in plans[item]["work"]),
        }, "0" * 64)
        if index:
            prior = lane_ids[index - 1]
            digest = str(index + 6) * 64
            artifacts[("cursor_transition", prior)] = ({
                "from_lane_id": prior, "to_lane_id": lane,
                "from_aggregate_sha256": previous,
                "to_plan_sha256": plans[lane]["plan_sha256"],
                "completed_work_census_sha256": sha256_json({
                    "lane_id": prior, "completed_work_ids": sorted(
                        row["work_id"] for row in plans[prior]["work"])}),
                "transition_sha256": digest,
            }, digest)
        previous = aggregate[lane]
    replay._c5_schedule(master_sha, plans, artifacts, lane_ids, aggregate)
    changed = copy.deepcopy(artifacts)
    changed[("plan_activation", lane_ids[0])][0]["inactive_work_ids"] = []
    with pytest.raises(replay.C0B5ReplayError, match="activation"):
        replay._c5_schedule(master_sha, plans, changed, lane_ids, aggregate)
    changed = copy.deepcopy(artifacts)
    changed[("cursor_transition", lane_ids[0])][0]["to_lane_id"] = "C44_1"
    with pytest.raises(replay.C0B5ReplayError, match="cursor"):
        replay._c5_schedule(master_sha, plans, changed, lane_ids, aggregate)


def test_child_failure_terminal_replays_without_quality_summary(tmp_path: Path) -> None:
    run_id = "c0b5-20260811-220000-" + "c" * 24
    source = goldset.load(verify=True)
    manifest = build_master_manifest(source)
    corpus = load_public_corpus(
        master_manifest_payload(manifest), master_manifest_sha256=manifest.sha256,
        source=source)
    value = checkpoint_header(run_id)
    value["master_manifest_sha256"] = manifest.sha256
    parents = tuple(tmp_path / f"parent-{index}" for index in range(4))
    point = C0B5Checkpoint.create(
        tmp_path, run_id, header=value, parent_paths=parents)
    point._assert_parents_unchanged = lambda: None  # type: ignore[method-assign]
    master = build_master_plan(
        corpus=corpus, run_nonce_key=b"k" * 32,
        protocol_sha256=value["protocol_sha256"])
    point.store_artifact("master_plan", "master", master)
    for envelope in (*master["lane_plans"], master["acceptance_template"]):
        lane = envelope["payload"]
        point.store_artifact("lane_plan", lane["lane_id"], lane)
    point.set_nonce_key(b"k" * 32)
    evidence = {
        "version": "c0b5-failure-evidence-v1", "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256,
        "protocol_sha256": value["protocol_sha256"],
        "terminal": "BLOCKED_FILESYSTEM",
        "reason": "filesystem_capability_or_integrity_failure",
        "lane_id": None, "plan_sha256": None, "attempt_id": None,
        "control_id": None, "charged_call_total": 0,
    }
    evidence["evidence_sha256"] = sha256_json(evidence)
    evidence_sha = point.store_artifact("failure_evidence", "terminal", evidence)
    failure = {
        "version": "c0b5-failure-v1", "policy_id": POLICY_ID,
        "policy_sha256": POLICY_SHA256,
        "protocol_sha256": value["protocol_sha256"],
        "terminal": "BLOCKED_FILESYSTEM",
        "reason": "filesystem_capability_or_integrity_failure",
        "evidence_sha256": evidence_sha, "charged_call_total": 0,
    }
    point.set_state("RUNNING")
    point.finalize("BLOCKED_FILESYSTEM", failure)
    parents_facts = SimpleNamespace(
        parent_binding=lineage.FROZEN_PARENT_BINDING, c0b3_d50_facts=None)
    facts = replay.replay_c0b5_connection(
        point.conn, parent_facts=parents_facts, require_receipt=False)
    assert facts.result["terminal"] == "BLOCKED_FILESYSTEM"
    assert facts.completion is None and facts.public_summary is None
    point.close()


def test_coherent_terminal_artifact_tamper_fails_semantic_replay(
        tmp_path: Path) -> None:
    _root, paths = _copied_parents(tmp_path)
    conn = sqlite3.connect(paths.c0b4_checkpoint)
    raw = conn.execute(
        "select json from artifacts where kind='result' and owner_id='terminal'"
    ).fetchone()[0]
    value = json.loads(raw)
    value["reason"] = "complete_public_acceptance_passed"
    changed, digest = _rehash(value)
    conn.execute(
        "update artifacts set json=?,sha256=? where kind='result' and owner_id='terminal'",
        (changed, digest))
    conn.commit()
    conn.execute("pragma query_only=on")
    with pytest.raises(replay.C0B5ReplayError, match="frozen artifact digest"):
        replay.replay_c0b4_connection(conn, require_receipt=True)
    conn.close()


def test_mixed_protocol_header_fails_before_attempt_replay(tmp_path: Path) -> None:
    _root, paths = _copied_parents(tmp_path)
    conn = sqlite3.connect(paths.c0b4_checkpoint)
    raw = conn.execute("select json from run_header where id=1").fetchone()[0]
    value = json.loads(raw)
    value["benchmark_protocol_id"] = "c0b3-assistive-confirmation-v1"
    changed, digest = _rehash(value)
    conn.execute("update run_header set json=?,sha256=? where id=1", (changed, digest))
    conn.commit()
    conn.execute("pragma query_only=on")
    with pytest.raises(replay.C0B5ReplayError, match="mixed|run-header digest"):
        replay.replay_c0b4_connection(conn, require_receipt=True)
    conn.close()


def test_inactive_lane_activation_tamper_fails_closed(tmp_path: Path) -> None:
    _root, paths = _copied_parents(tmp_path)
    conn = sqlite3.connect(paths.c0b4_checkpoint)
    raw = conn.execute(
        "select json from artifacts where kind='plan_activation' and owner_id='F72_17'"
    ).fetchone()[0]
    value = json.loads(raw)
    changed, digest = _rehash(value)
    conn.execute(
        "insert into artifacts(kind,owner_id,sha256,json,created) values(?,?,?,?,0)",
        ("plan_activation", "F72_20260804", digest, changed))
    conn.commit()
    conn.execute("pragma query_only=on")
    with pytest.raises(replay.C0B5ReplayError, match="artifact owner census"):
        replay.replay_c0b4_connection(conn, require_receipt=True)
    conn.close()


def test_all_four_parents_verify_before_recheck_token_is_issued(
        tmp_path: Path) -> None:
    root, paths = _copied_parents(tmp_path)
    before = {path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (paths.c0b3_checkpoint, paths.c0b3_snapshot,
                           paths.c0b4_checkpoint, paths.c0b4_snapshot)}
    calls = []

    def c3_verifier(path, binding):
        calls.append((path, binding["run_id"]))
        return {"ok": True, "errors": []}

    verified = lineage.verify_parents_readonly(paths, c0b3_verifier=c3_verifier)
    lineage.assert_parents_unchanged(verified)
    after = {path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in (paths.c0b3_checkpoint, paths.c0b3_snapshot,
                          paths.c0b4_checkpoint, paths.c0b4_snapshot)}
    assert before == after
    assert calls == [(paths.c0b3_checkpoint, C3_RUN)]
    assert verified.parent_binding == lineage.FROZEN_PARENT_BINDING
    assert verified.c0b3_d50_facts.negative_retained_findings == 1

    with paths.c0b4_snapshot.open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(lineage.C0B5LineageError, match="changed|integrity"):
        lineage.assert_parents_unchanged(verified)
