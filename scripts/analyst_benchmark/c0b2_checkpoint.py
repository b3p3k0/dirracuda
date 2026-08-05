"""Durable C0B-2 benchmark checkpoint primitives.

This module contains no Ollama client and never discovers a private corpus.  Callers
provide an explicit benchmark root after their confirmation gates have passed.

DISPOSITION: benchmark-only diagnostic; remove after C0B artifacts are accepted."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from .c0b2_plan import (MODELS, attempt_id as stable_attempt_id,
                        planned_work_identities, planned_work_ids)
from .c0b2_schema import validate_run_header_pins, validate_stage_c_selection

SCHEMA_VERSION = 2
CALL_CLASSES = ("scored", "schema_retry", "preflight_probe", "transport_orphan")
INVOCATION_CAPS = {"C": 3, "D": 6, "F": 10, "E": 10}
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SELECTION_FIELDS = {
    "model", "model_digest", "worksheet", "chunk_chars", "overlap",
    "num_ctx", "num_predict",
}
RESUMABLE_STATES = {
    "PREPARED", "RUNNING", "PAUSED_SOFT_WALL", "PAUSED_RESOURCE",
    "PAUSED_PREFLIGHT", "PAUSED_STAGE_BOUNDARY", "CANCELLED_PENDING_RESUME",
}
TERMINAL_STATES = {
    "SELECTED", "INCONCLUSIVE", "FAILED_SAFETY", "BLOCKED_PROVENANCE",
    "BLOCKED_BUDGET", "BLOCKED_FILESYSTEM", "ABANDONED", "PASS_OPERATIONAL",
    "FAIL_OPERATIONAL", "INCOMPLETE", "BLOCKED_SECURITY",
}
ALL_STATES = RESUMABLE_STATES | TERMINAL_STATES
AGGREGATE_TERMINALS = {
    "SELECTED", "INCONCLUSIVE", "PASS_OPERATIONAL", "FAIL_OPERATIONAL", "INCOMPLETE",
}
FINISH_OUTCOMES = {
    "ACCEPTED", "SCHEMA_INVALID", "RETRYABLE_TRANSPORT", "FAILED_SAFETY",
    "BLOCKED_SECURITY", "BLOCKED_PROVENANCE",
}
class CheckpointError(RuntimeError):
    pass
class ImmutableViolation(CheckpointError):
    pass
class CapExceeded(CheckpointError):
    pass
class LockUnavailable(CheckpointError):
    pass
@dataclass(frozen=True)
class BackoffRecord:
    model: str
    failures: int
    retry_not_before: float
@dataclass(frozen=True)
class ContextObligation:
    stage: str
    model: str
    control_id: str
    request_hash: str
    source_attempt_id: str
def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)
def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
def _secure_dir(path: Path, *, create: bool = False) -> Path:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise PermissionError(f"not a real directory: {path}")
    if st.st_uid != os.getuid():
        raise PermissionError(f"directory not owned by current user: {path}")
    os.chmod(path, 0o700)
    return path
def _regular_owner_file(path: Path) -> os.stat_result:
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise PermissionError(f"not an owner-controlled regular file: {path}")
    if st.st_uid != os.getuid():
        raise PermissionError(f"file not owned by current user: {path}")
    return st
_SCHEMA = """
CREATE TABLE run_header(id INTEGER PRIMARY KEY CHECK(id=1), json TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE run_state(id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE stage_limits(stage TEXT PRIMARY KEY, hard_cap INTEGER NOT NULL CHECK(hard_cap>=0));
CREATE TABLE class_limits(stage TEXT NOT NULL REFERENCES stage_limits(stage), call_class TEXT NOT NULL,
 allowance INTEGER NOT NULL CHECK(allowance>=0), PRIMARY KEY(stage,call_class));
CREATE TABLE plans(stage TEXT PRIMARY KEY, parent_hash TEXT NOT NULL, plan_hash TEXT NOT NULL,
 plan_json TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE manifests(name TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL,
 manifest_json TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE stage_aggregates(stage TEXT PRIMARY KEY, plan_hash TEXT NOT NULL,
 aggregate_hash TEXT NOT NULL, aggregate_json TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE acceptance_plan(id INTEGER PRIMARY KEY CHECK(id=1),
 parent_decision_hash TEXT NOT NULL, plan_hash TEXT NOT NULL,
 plan_json TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, stage TEXT NOT NULL, parent_hash TEXT NOT NULL,
 aggregate_hash TEXT NOT NULL, activation TEXT NOT NULL CHECK(activation IN ('ACTIVATED','NOT_ACTIVATED')),
 value_json TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE work_items(work_id TEXT PRIMARY KEY, stage TEXT NOT NULL, cell_id TEXT NOT NULL,
 request_hash TEXT NOT NULL, state TEXT NOT NULL, accepted_attempt_id TEXT);
CREATE TABLE attempts(attempt_id TEXT PRIMARY KEY, work_id TEXT REFERENCES work_items(work_id),
 control_id TEXT, stage TEXT NOT NULL, invocation_ordinal INTEGER,
 call_class TEXT NOT NULL, attempt_no INTEGER NOT NULL,
 request_hash TEXT NOT NULL, state TEXT NOT NULL, response TEXT, metadata_json TEXT,
 created REAL NOT NULL, updated REAL NOT NULL,
 UNIQUE(work_id,attempt_no), UNIQUE(control_id,attempt_no));
CREATE TABLE model_backoff(model TEXT PRIMARY KEY, failures INTEGER NOT NULL,
 retry_not_before REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE context_obligations(stage TEXT NOT NULL, model TEXT NOT NULL,
 control_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
 source_attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
 state TEXT NOT NULL CHECK(state IN ('PENDING','COMPLETE')),
 completion_attempt_id TEXT REFERENCES attempts(attempt_id), created REAL NOT NULL,
 updated REAL NOT NULL, PRIMARY KEY(stage,model));
CREATE TABLE invocations(stage TEXT NOT NULL, ordinal INTEGER NOT NULL,
 created REAL NOT NULL, PRIMARY KEY(stage,ordinal));
CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
 detail_json TEXT NOT NULL, created REAL NOT NULL);
"""
class Checkpoint:
    def __init__(self, db_path: Path, conn: sqlite3.Connection, benchmark_root: Path):
        self.path = Path(db_path)
        self.root = Path(benchmark_root)
        self.conn = conn

    @classmethod
    def create(cls, benchmark_root: Path, run_id: str, *, header: Mapping[str, Any],
               limits: Mapping[str, Mapping[str, int]], cumulative_cap: int,
               journal_mode: str = "DELETE") -> "Checkpoint":
        if not RUN_ID_RE.fullmatch(run_id) or run_id in (".", ".."):
            raise ValueError("invalid run id")
        mode = journal_mode.upper()
        if mode not in ("WAL", "DELETE"):
            raise ValueError("journal_mode must be WAL or DELETE")
        if isinstance(cumulative_cap, bool) or not isinstance(cumulative_cap, int) or cumulative_cap < 0:
            raise ValueError("cumulative cap must be a non-negative integer")
        normalized: dict[str, dict[str, int]] = {}
        for stage, classes in limits.items():
            if not isinstance(stage, str) or not stage:
                raise ValueError("stage names must be non-empty strings")
            normalized[stage] = {}
            for call_class, allowance in classes.items():
                if (call_class not in CALL_CLASSES or isinstance(allowance, bool)
                        or not isinstance(allowance, int) or allowance < 0):
                    raise ValueError(f"invalid call allowance {stage}/{call_class}")
                normalized[stage][call_class] = allowance
        frozen = validate_run_header_pins(header)
        canonical_root = str(Path(benchmark_root).resolve(strict=False))
        if (frozen["mount"]["canonical_path"] != canonical_root
                or frozen["filesystem_selected_mode"] != mode):
            raise ImmutableViolation("filesystem pins do not match the requested checkpoint")
        frozen.update({"schema_version": SCHEMA_VERSION, "journal_mode": mode,
                       "cumulative_cap": cumulative_cap, "run_id": run_id,
                       "limits": normalized, "invocation_caps": INVOCATION_CAPS})
        canonical_json(frozen)  # Reject non-canonical headers before creating the run.
        root = _secure_dir(Path(benchmark_root), create=True)
        runs = _secure_dir(root / "runs", create=True)
        run_dir = runs / run_id
        run_dir.mkdir(mode=0o700, exist_ok=False)
        _secure_dir(run_dir)
        path = run_dir / "checkpoint.sqlite3"
        conn = sqlite3.connect(path, isolation_level=None, timeout=5.0)
        try:
            cls._configure(conn, journal_mode)
            conn.executescript(_SCHEMA)
            now = time.time()
            conn.execute("INSERT INTO run_header VALUES(1,?,?)",
                         (canonical_json(frozen), sha256_json(frozen)))
            conn.execute("INSERT INTO run_state VALUES(1,'PREPARED',?)", (now,))
            for stage, classes in normalized.items():
                hard = sum(classes.values())
                conn.execute("INSERT INTO stage_limits VALUES(?,?)", (stage, hard))
                for call_class, allowance in classes.items():
                    conn.execute("INSERT INTO class_limits VALUES(?,?,?)",
                                 (stage, call_class, allowance))
            os.chmod(path, 0o600)
            cls._fsync_file_and_parent(path)
            return cls(path, conn, root)
        except Exception:
            conn.close()
            raise
    @staticmethod
    def _configure(conn: sqlite3.Connection, journal_mode: str) -> None:
        mode = journal_mode.upper()
        if mode not in ("WAL", "DELETE"):
            raise ValueError("journal_mode must be WAL or DELETE")
        got = str(conn.execute(f"PRAGMA journal_mode={mode}").fetchone()[0]).upper()
        if got != mode:
            raise CheckpointError(f"journal mode {mode} unavailable (got {got})")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA mmap_size=0")
    @classmethod
    def open(cls, db_path: Path, benchmark_root: Path) -> "Checkpoint":
        path = Path(db_path)
        root = _secure_dir(Path(benchmark_root))
        runs = _secure_dir(root / "runs")
        run_dir = _secure_dir(path.parent)
        if run_dir.parent != runs or path.name != "checkpoint.sqlite3":
            raise PermissionError("checkpoint is outside benchmark_root/runs/<run>")
        _regular_owner_file(path)
        conn = sqlite3.connect(path, isolation_level=None, timeout=5.0)
        mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).upper()
        cls._configure(conn, mode)
        os.chmod(path, 0o600)
        point = cls(path, conn, root)
        header = point.header()
        logical_run_id = header.get("run_id")
        if logical_run_id != run_dir.name:
            record_path = run_dir / "restore.json"
            _regular_owner_file(record_path)
            record = json.loads(record_path.read_text("utf-8"))
            expected = {
                "kind": "snapshot_restore_v1",
                "logical_run_id": logical_run_id,
                "storage_id": run_dir.name,
                "header_sha256": sha256_json(header),
            }
            if (not isinstance(record, dict)
                    or set(record) != {*expected, "snapshot_sha256"}
                    or any(record.get(key) != value for key, value in expected.items())
                    or not isinstance(record.get("snapshot_sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", record["snapshot_sha256"])):
                conn.close(); raise ImmutableViolation("invalid snapshot restoration record")
            origin = conn.execute(
                "SELECT detail_json FROM events WHERE kind='RESTORE_ORIGIN' "
                "ORDER BY seq DESC LIMIT 1").fetchone()
            if not origin or origin[0] != canonical_json(record):
                conn.close(); raise ImmutableViolation("restore origin does not match checkpoint")
        if (header["mount"]["canonical_path"] != str(root.resolve())
                or header["filesystem_selected_mode"] != mode):
            conn.close()
            raise ImmutableViolation("checkpoint path does not match immutable run id")
        return point
    def close(self) -> None:
        self.conn.close()
    def __enter__(self) -> "Checkpoint":
        return self
    def __exit__(self, *_args: object) -> None:
        self.close()
    def header(self) -> dict[str, Any]:
        raw, digest = self.conn.execute("SELECT json,sha256 FROM run_header WHERE id=1").fetchone()
        value = json.loads(raw)
        if sha256_json(value) != digest:
            raise ImmutableViolation("run header hash mismatch")
        return value
    def state(self) -> str:
        return str(self.conn.execute("SELECT state FROM run_state WHERE id=1").fetchone()[0])
    def transition(self, new_state: str) -> None:
        if new_state not in ALL_STATES:
            raise ValueError(f"unknown state {new_state}")
        if new_state in AGGREGATE_TERMINALS:
            raise CheckpointError("aggregate terminal states require artifact finalization")
        old = self.state()
        if new_state == "PAUSED_STAGE_BOUNDARY":
            raise CheckpointError(
                "stage-boundary pause requires an atomic activated decision")
        blockers = {"BLOCKED_PROVENANCE", "BLOCKED_BUDGET", "BLOCKED_FILESYSTEM",
                    "BLOCKED_SECURITY", "ABANDONED"}
        if old == new_state:
            return
        if old == "PREPARED":
            allowed = blockers | {"RUNNING"}
        elif old == "RUNNING":
            allowed = ALL_STATES - {"PREPARED", "RUNNING"}
        elif old == "PAUSED_STAGE_BOUNDARY":
            allowed = blockers
        elif old in RESUMABLE_STATES:
            allowed = blockers | {"RUNNING"}
        else:
            allowed = set()
        if new_state not in allowed:
            raise CheckpointError(f"illegal state transition {old} -> {new_state}")
        self.conn.execute("UPDATE run_state SET state=?,updated=? WHERE id=1",
                          (new_state, time.time()))
    def claim_invocation(
            self, stage: str, *,
            claim_guard: Optional[Callable[[], None]] = None) -> int:
        if stage not in INVOCATION_CAPS:
            raise ValueError(f"unknown invocation stage {stage}")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            if self.state() != "RUNNING":
                raise CheckpointError("invocation claim requires RUNNING")
            if not self.conn.execute(
                    "SELECT 1 FROM stage_limits WHERE stage=?", (stage,)).fetchone():
                raise CheckpointError(f"stage {stage} has no immutable allowance")
            prior = self.conn.execute(
                "SELECT coalesce(max(ordinal),0) FROM invocations WHERE stage=?",
                (stage,)).fetchone()[0]
            ordinal = int(prior) + 1
            cap = self.header()["invocation_caps"].get(stage)
            if cap != INVOCATION_CAPS[stage]:
                raise ImmutableViolation("invocation cap differs from the implementation contract")
            if ordinal > cap:
                self.conn.execute(
                    "UPDATE run_state SET state='BLOCKED_BUDGET',updated=? WHERE id=1",
                    (time.time(),))
                self.conn.commit()
                raise CapExceeded(f"invocation allowance exhausted for {stage}")
            if claim_guard:
                claim_guard()
            self.conn.execute("INSERT INTO invocations VALUES(?,?,?)",
                              (stage, ordinal, time.time()))
            if claim_guard:
                claim_guard()
            self.conn.commit()
            return ordinal
        except CapExceeded:
            raise
        except Exception:
            self.conn.rollback()
            raise
    def freeze_plan(self, stage: str, parent_hash: str, plan: Any) -> str:
        if not self.conn.execute("SELECT 1 FROM stage_limits WHERE stage=?", (stage,)).fetchone():
            raise CheckpointError(f"stage {stage} has no immutable allowance")
        predecessor = {"D": "C", "F": "D"}.get(stage)
        if predecessor and parent_hash not in self._decision_hashes(
                predecessor, activated_only=True):
            raise ImmutableViolation(
                f"stage {stage} plan is not chained to a {predecessor} decision")
        if (stage == "E"
                and parent_hash != self.header().get("parent_selection_sha256")):
            raise ImmutableViolation(
                "Stage E plan is not chained to the frozen public selection")
        raw, digest = canonical_json(plan), sha256_json(plan)
        work_ids = planned_work_ids(raw)
        if stage == "C" and self.header()["run_type"] == "public" and not work_ids:
            raise CheckpointError("public Stage C plan cannot be empty")
        row = self.conn.execute("SELECT parent_hash,plan_hash,plan_json FROM plans WHERE stage=?",
                                (stage,)).fetchone()
        if row:
            if row != (parent_hash, digest, raw):
                raise ImmutableViolation(f"stage {stage} plan already frozen")
            return digest
        self.conn.execute("INSERT INTO plans VALUES(?,?,?,?,?)",
                          (stage, parent_hash, digest, raw, time.time()))
        return digest
    def load_plan(self, stage: str) -> tuple[str, str, str]:
        row = self.conn.execute(
            "SELECT parent_hash,plan_hash,plan_json FROM plans WHERE stage=?",
            (stage,)).fetchone()
        if not row:
            raise CheckpointError(f"unknown stage plan {stage}")
        value = json.loads(row[2])
        if canonical_json(value) != row[2] or sha256_json(value) != row[1]:
            raise ImmutableViolation(f"stage {stage} plan hash mismatch")
        predecessor = {"D": "C", "F": "D"}.get(stage)
        if predecessor and row[0] not in self._decision_hashes(
                predecessor, activated_only=True):
            raise ImmutableViolation(
                f"stage {stage} plan parent is not a {predecessor} decision")
        return str(row[0]), str(row[1]), str(row[2])

    def freeze_manifest(self, name: str, value: Any) -> str:
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
            raise ValueError("invalid manifest name")
        raw, digest = canonical_json(value), sha256_json(value)
        if name == "master" and digest != self.header()["master_manifest_sha256"]:
            raise ImmutableViolation("master manifest differs from the run header")
        row = self.conn.execute(
            "SELECT manifest_hash,manifest_json FROM manifests WHERE name=?", (name,)
        ).fetchone()
        if row and row != (digest, raw):
            raise ImmutableViolation(f"manifest {name} is already frozen")
        if not row:
            self.conn.execute("INSERT INTO manifests VALUES(?,?,?,?)",
                              (name, digest, raw, time.time()))
        return digest

    def load_manifest(self, name: str) -> tuple[str, str]:
        row = self.conn.execute(
            "SELECT manifest_hash,manifest_json FROM manifests WHERE name=?", (name,)
        ).fetchone()
        if not row:
            raise CheckpointError(f"unknown manifest {name}")
        value = json.loads(row[1])
        if canonical_json(value) != row[1] or sha256_json(value) != row[0]:
            raise ImmutableViolation(f"manifest {name} hash mismatch")
        if name == "master" and row[0] != self.header()["master_manifest_sha256"]:
            raise ImmutableViolation("master manifest no longer matches the run header")
        return str(row[0]), str(row[1])

    def freeze_aggregate(self, stage: str, plan_hash: str, value: Any) -> str:
        _parent, expected_plan, raw_plan = self.load_plan(stage)
        if plan_hash != expected_plan:
            raise ImmutableViolation(f"aggregate is not chained to the {stage} plan")
        if stage == "C":
            from .c0b2_stage_c import validate_stage_c_aggregate_semantics
            normalized = validate_stage_c_aggregate_semantics(value)
        else:
            normalized = value
        if stage == "C":
            if (normalized["plan_sha256"] != plan_hash
                    or normalized["master_manifest_sha256"] != self.load_manifest("master")[0]):
                raise ImmutableViolation("Stage-C aggregate provenance differs from frozen inputs")
            planned_cells: dict[str, list[dict[str, Any]]] = {}
            for item in json.loads(raw_plan)["work"]:
                planned_cells.setdefault(item.get("cell_id"), []).append(item)
            aggregate_cells = normalized["cells"]
            if (len(planned_cells) != 6 or sum(map(len, planned_cells.values())) != 264
                    or len(aggregate_cells) != len(planned_cells)):
                raise ImmutableViolation("Stage-C aggregate does not cover the frozen plan")
            for cell, (cell_id, items) in zip(aggregate_cells, planned_cells.items()):
                expected = (cell_id, items[0].get("model"),
                            items[0].get("model_digest"), items[0].get("worksheet"))
                actual = (cell["cell_id"], cell["model"],
                          cell["model_digest"], cell["worksheet"])
                doc_ids = [item.get("doc_id") for item in items]
                if (actual != expected or len(items) != 44
                        or [row["doc_id"] for row in cell["documents"]] != doc_ids
                        or any((item.get("model"), item.get("model_digest"),
                                item.get("worksheet")) != expected[1:] for item in items)):
                    raise ImmutableViolation("Stage-C aggregate rows differ from the frozen plan")
            self._validate_stage_c_attempt_facts(normalized, raw_plan)
        raw, digest = canonical_json(normalized), sha256_json(normalized)
        row = self.conn.execute(
            "SELECT plan_hash,aggregate_hash,aggregate_json FROM stage_aggregates WHERE stage=?",
            (stage,)).fetchone()
        if row and row != (plan_hash, digest, raw):
            raise ImmutableViolation(f"stage {stage} aggregate is already frozen")
        if not row:
            self.conn.execute("INSERT INTO stage_aggregates VALUES(?,?,?,?,?)",
                              (stage, plan_hash, digest, raw, time.time()))
        return digest

    def load_aggregate(self, stage: str) -> tuple[str, str, str]:
        row = self.conn.execute(
            "SELECT plan_hash,aggregate_hash,aggregate_json FROM stage_aggregates WHERE stage=?",
            (stage,)).fetchone()
        if not row:
            raise CheckpointError(f"unknown stage aggregate {stage}")
        value = json.loads(row[2])
        if canonical_json(value) != row[2] or sha256_json(value) != row[1]:
            raise ImmutableViolation(f"stage {stage} aggregate hash mismatch")
        if self.load_plan(stage)[1] != row[0]:
            raise ImmutableViolation(f"stage {stage} aggregate plan changed")
        if stage == "C":
            from .c0b2_stage_c import validate_stage_c_aggregate_semantics
            validate_stage_c_aggregate_semantics(value)
            self._validate_stage_c_attempt_facts(value, self.load_plan("C")[2])
        return str(row[0]), str(row[1]), str(row[2])

    def _validate_stage_c_attempt_facts(
            self, aggregate: Mapping[str, Any], plan_json: str) -> None:
        planned: dict[str, list[dict[str, Any]]] = {}
        for item in json.loads(plan_json)["work"]:
            planned.setdefault(item["cell_id"], []).append(item)
        for cell in aggregate["cells"]:
            items = planned.get(cell["cell_id"], [])
            length_outcomes = 0
            for document, item in zip(cell["documents"], items):
                attempts = self.conn.execute(
                    "SELECT state,metadata_json FROM attempts WHERE work_id=? "
                    "ORDER BY attempt_no", (item["work_id"],)).fetchall()
                answered: list[tuple[str, dict[str, Any]]] = []
                for state, metadata_raw in attempts:
                    if state not in {"ACCEPTED", "SCHEMA_INVALID"}:
                        continue
                    try:
                        metadata = json.loads(metadata_raw)
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise ImmutableViolation(
                            "Stage-C answered attempt metadata is invalid") from exc
                    if not isinstance(metadata, dict):
                        raise ImmutableViolation(
                            "Stage-C answered attempt metadata is not an object")
                    flags = tuple(metadata.get(name) for name in (
                        "tools_empty", "images_empty", "unknown_message_fields_empty"))
                    invalid = (metadata.get("strict_schema_invalid"),
                               metadata.get("semantic_invalid"))
                    done_reason = metadata.get("done_reason")
                    if (any(type(flag) is not bool for flag in flags)
                            or any(type(flag) is not bool for flag in invalid)
                            or type(done_reason) is not str or not done_reason
                            or (state == "ACCEPTED" and invalid != (False, False))
                            or (state == "SCHEMA_INVALID" and sum(invalid) != 1)):
                        raise ImmutableViolation(
                            "Stage-C answered attempt metadata contradicts its outcome")
                    answered.append((str(state), metadata))
                    length_outcomes += done_reason == "length"
                accepted = next((meta for state, meta in answered
                                 if state == "ACCEPTED"), None)
                facts = {
                    "charged_attempt_count": len(attempts),
                    "first_pass_valid": bool(answered and answered[0][0] == "ACCEPTED"),
                    "eventual_valid": accepted is not None,
                    "strict_schema_invalid_attempts": sum(
                        meta["strict_schema_invalid"] for _state, meta in answered),
                    "semantic_invalid_attempts": sum(
                        meta["semantic_invalid"] for _state, meta in answered),
                    "done_reason": accepted.get("done_reason") if accepted else None,
                    "tools_empty": all(meta["tools_empty"] for _state, meta in answered),
                    "images_empty": all(meta["images_empty"] for _state, meta in answered),
                    "unknown_message_fields_empty": all(
                        meta["unknown_message_fields_empty"] for _state, meta in answered),
                }
                if document["doc_id"] != item.get("doc_id") or any(
                        document[name] != expected for name, expected in facts.items()):
                    raise ImmutableViolation(
                        "Stage-C aggregate document differs from checkpoint attempts")
            if len(items) != len(cell["documents"]) or \
                    cell["length_outcomes"] != length_outcomes:
                raise ImmutableViolation(
                    "Stage-C aggregate length outcomes differ from checkpoint attempts")

    def freeze_acceptance_plan(self, parent_decision_hash: str, plan: Any) -> str:
        if self.header()["run_type"] != "public":
            raise CheckpointError("private runs cannot freeze a public acceptance plan")
        selection: Optional[dict[str, Any]] = None
        for row in self.conn.execute(
                "SELECT decision_id,stage,parent_hash,aggregate_hash,activation,value_json "
                "FROM decisions WHERE stage='F'"):
            value = json.loads(row[5])
            if (sha256_json(tuple(row)) == parent_decision_hash and row[4] == "ACTIVATED"
                    and isinstance(value, dict)
                    and set(value) == {"outcome", "selection"}
                    and value.get("outcome") == "PROVISIONAL_SELECTED"
                    and isinstance(value.get("selection"), dict)
                    and set(value["selection"]) == SELECTION_FIELDS):
                selection = value["selection"]
        if selection is None:
            raise ImmutableViolation(
                "acceptance plan is not chained to a provisional Stage-F selection")
        if self.header()["model_digests"].get(selection["model"]) != \
                selection["model_digest"]:
            raise ImmutableViolation("provisional selection model digest is not frozen")
        raw, digest = canonical_json(plan), sha256_json(plan)
        work = planned_work_identities(raw)
        items = json.loads(raw)["work"]
        doc_ids = {item.get("doc_id") for item in items
                   if isinstance(item.get("doc_id"), str) and item.get("doc_id")}
        c_items = json.loads(self.load_plan("C")[2])["work"]
        c_doc_ids = {item.get("doc_id") for item in c_items
                     if isinstance(item.get("doc_id"), str) and item.get("doc_id")}
        if (not work or len(c_doc_ids) != 44 or doc_ids != c_doc_ids
                or any(any(item.get(field) != selection[field]
                           for field in SELECTION_FIELDS) for item in items)):
            raise CheckpointError(
                "acceptance plan must be the selected configuration over frozen C44")
        row = self.conn.execute(
            "SELECT parent_decision_hash,plan_hash,plan_json FROM acceptance_plan WHERE id=1"
        ).fetchone()
        frozen = (parent_decision_hash, digest, raw)
        if row and row != frozen:
            raise ImmutableViolation("acceptance plan is already frozen")
        if not row:
            self.conn.execute("INSERT INTO acceptance_plan VALUES(1,?,?,?,?)",
                              (*frozen, time.time()))
        return digest

    def load_acceptance_plan(self) -> Optional[tuple[str, str, str]]:
        row = self.conn.execute(
            "SELECT parent_decision_hash,plan_hash,plan_json FROM acceptance_plan WHERE id=1"
        ).fetchone()
        if not row:
            return None
        value = json.loads(row[2])
        if canonical_json(value) != row[2] or sha256_json(value) != row[1]:
            raise ImmutableViolation("acceptance plan hash mismatch")
        if row[0] not in self._decision_hashes("F", activated_only=True):
            raise ImmutableViolation("acceptance plan parent decision changed")
        return str(row[0]), str(row[1]), str(row[2])
    def freeze_decision(self, decision_id: str, stage: str, parent_hash: str,
                        aggregate_hash: str, activation: str, value: Any) -> str:
        if activation not in ("ACTIVATED", "NOT_ACTIVATED"):
            raise ValueError("invalid activation")
        candidate_plans = [self.load_plan(stage)]
        acceptance = self.load_acceptance_plan() if stage == "F" else None
        if acceptance:
            candidate_plans.append(acceptance)
        matched = next((item for item in candidate_plans if item[1] == parent_hash), None)
        if matched is None:
            raise ImmutableViolation(
                f"decision {decision_id} is not chained to the {stage} plan")
        _plan_parent, plan_hash, plan_json = matched
        aggregate = self.conn.execute(
            "SELECT plan_hash,aggregate_hash FROM stage_aggregates WHERE stage=?",
            (stage,)).fetchone()
        if aggregate and aggregate != (plan_hash, aggregate_hash):
            raise ImmutableViolation(f"decision {decision_id} aggregate provenance changed")
        planned = planned_work_ids(plan_json)
        states = {row[0]: row[1] for row in self.conn.execute(
            "SELECT work_id,state FROM work_items WHERE stage=?", (stage,))}
        complete_states = {"SUCCEEDED", "COMPLETED_INVALID"}
        incomplete = any(states.get(work_id) not in complete_states for work_id in planned)
        dispatching = self.conn.execute(
            "SELECT count(*) FROM attempts WHERE work_id IN "
            "(SELECT work_id FROM work_items WHERE stage=?) AND state='DISPATCHING'",
            (stage,)).fetchone()[0]
        if not planned <= set(states) or incomplete or dispatching:
            raise CheckpointError(
                f"stage {stage} cannot decide before its frozen work completes")
        if activation == "ACTIVATED" and not planned:
            raise CheckpointError(f"empty stage {stage} cannot activate a successor")
        frozen = (stage, parent_hash, aggregate_hash, activation, canonical_json(value))
        row = self.conn.execute(
            "SELECT stage,parent_hash,aggregate_hash,activation,value_json FROM decisions WHERE decision_id=?",
            (decision_id,)).fetchone()
        if row and row != frozen:
            raise ImmutableViolation(f"decision {decision_id} already frozen")
        if not row:
            self.conn.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
                              (decision_id, *frozen, time.time()))
        return sha256_json((decision_id, *frozen))

    def _decision_hashes(self, stage: str, *, activated_only: bool = False) -> set[str]:
        values: set[str] = set()
        for row in self.conn.execute(
                "SELECT decision_id,stage,parent_hash,aggregate_hash,activation,value_json "
                "FROM decisions WHERE stage=?", (stage,)):
            value = json.loads(row[5])
            if canonical_json(value) != row[5]:
                raise ImmutableViolation(f"decision {row[0]} is not canonical")
            if not activated_only or row[4] == "ACTIVATED":
                values.add(sha256_json(tuple(row)))
        return values
    def load_decision(self, decision_id: str) -> tuple[str, Any]:
        row = self.conn.execute(
            "SELECT stage,parent_hash,aggregate_hash,activation,value_json FROM decisions WHERE decision_id=?",
            (decision_id,)).fetchone()
        if not row:
            raise CheckpointError(f"unknown decision {decision_id}")
        value = json.loads(row[4])
        if canonical_json(value) != row[4]:
            raise ImmutableViolation(f"decision {decision_id} is not canonical")
        return sha256_json((decision_id, *row)), value

    def freeze_stage_boundary_decision(
            self, decision_id: str, stage: str, parent_hash: str,
            aggregate_hash: str, value: Any) -> str:
        """Atomically freeze an activated C/D decision and pause at its boundary."""
        if stage not in {"C", "D"}:
            raise ValueError("only Stage C or D can enter a stage-boundary pause")
        _plan_parent, plan_hash, plan_json = self.load_plan(stage)
        aggregate = self.load_aggregate(stage)
        if parent_hash != plan_hash or aggregate[:2] != (plan_hash, aggregate_hash):
            raise ImmutableViolation("stage-boundary decision provenance changed")
        normalized = validate_stage_c_selection(value) if stage == "C" else value
        if stage == "C":
            from .c0b2_stage_c import build_stage_c_selection
            expected_selection = validate_stage_c_selection(
                build_stage_c_selection(json.loads(aggregate[2])))
            if (decision_id != "stage-c-selection" or not normalized["survivors"]
                    or normalized != expected_selection
                    or len(json.loads(plan_json)["work"]) != 264
                    or normalized["plan_sha256"] != plan_hash
                    or normalized["aggregate_sha256"] != aggregate_hash
                    or [(row["model"], row["model_digest"])
                        for row in normalized["models"]]
                    != [(model, digest) for model, digest, _think in MODELS]
                    or self.header()["model_digests"]
                    != {model: digest for model, digest, _think in MODELS}):
                raise ImmutableViolation("Stage-C boundary selection is not the frozen survivor decision")
        raw = canonical_json(normalized)
        frozen = (stage, parent_hash, aggregate_hash, "ACTIVATED", raw)
        digest = sha256_json((decision_id, *frozen))
        existing = self.conn.execute(
            "SELECT stage,parent_hash,aggregate_hash,activation,value_json "
            "FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
        if existing:
            if existing != frozen or self.state() != "PAUSED_STAGE_BOUNDARY":
                raise ImmutableViolation(f"decision {decision_id} already frozen")
            return digest
        planned = planned_work_ids(plan_json)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            if self.state() != "RUNNING":
                raise CheckpointError("stage-boundary decision requires RUNNING")
            states = {row[0]: row[1] for row in self.conn.execute(
                "SELECT work_id,state FROM work_items WHERE stage=?", (stage,))}
            contexts = ({row[0]: row[1] for row in self.conn.execute(
                "SELECT model,state FROM context_obligations WHERE stage='C'")}
                if stage == "C" else {})
            if (not planned or planned != set(states)
                    or any(states[work_id] not in {"SUCCEEDED", "COMPLETED_INVALID"}
                           for work_id in planned)
                    or (stage == "C" and contexts != {
                        model: "COMPLETE" for model, _digest, _think in MODELS})
                    or self.conn.execute(
                        "SELECT 1 FROM attempts WHERE stage=? AND state='DISPATCHING' LIMIT 1",
                        (stage,)).fetchone()):
                raise CheckpointError("stage-boundary decision requires complete frozen work")
            self.conn.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
                              (decision_id, *frozen, time.time()))
            self.conn.execute(
                "UPDATE run_state SET state='PAUSED_STAGE_BOUNDARY',updated=? WHERE id=1",
                (time.time(),))
            self.conn.commit()
            return digest
        except Exception:
            self.conn.rollback()
            raise
    def register_work(self, work_id: str, stage: str, cell_id: str, request_hash: str) -> None:
        identities = planned_work_identities(self.load_plan(stage)[2])
        acceptance = self.load_acceptance_plan() if stage == "F" else None
        if acceptance:
            extra = planned_work_identities(acceptance[2])
            if set(identities) & set(extra):
                raise ImmutableViolation("primary and acceptance plans share work identities")
            identities.update(extra)
        identity = identities.get(work_id)
        if identity != (cell_id, request_hash):
            raise ImmutableViolation(f"work {work_id} differs from the frozen {stage} plan")
        row = self.conn.execute(
            "SELECT stage,cell_id,request_hash FROM work_items WHERE work_id=?", (work_id,)).fetchone()
        if row and row != (stage, cell_id, request_hash):
            raise ImmutableViolation(f"work {work_id} identity changed")
        if not row:
            self.conn.execute("INSERT INTO work_items VALUES(?,?,?,?,'PENDING',NULL)",
                              (work_id, stage, cell_id, request_hash))
    def precharge(self, *, attempt_id: str, stage: str, call_class: str,
                  request_hash: str, attempt_no: int, work_id: Optional[str] = None,
                  control_id: Optional[str] = None,
                  invocation_ordinal: Optional[int] = None,
                  first_control_class: Optional[str] = None,
                  claim_guard: Optional[Callable[[], None]] = None) -> bool:
        if (work_id is None) == (control_id is None) or attempt_no < 1:
            raise ValueError("exactly one of work_id or control_id is required")
        identity = work_id if work_id is not None else f"control:{control_id}"
        if attempt_id != stable_attempt_id(identity, attempt_no):
            raise ImmutableViolation("attempt id does not match its immutable identity")
        if call_class not in CALL_CLASSES or self.state() != "RUNNING":
            raise CheckpointError("precharge requires RUNNING and a known class")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            if claim_guard:
                claim_guard()
            existing = self.conn.execute(
                "SELECT work_id,control_id,stage,invocation_ordinal,call_class,attempt_no,"
                "request_hash FROM attempts WHERE attempt_id=?",
                (attempt_id,)).fetchone()
            frozen = (work_id, control_id, stage, invocation_ordinal,
                      call_class, attempt_no, request_hash)
            if existing:
                if existing != frozen:
                    raise ImmutableViolation(f"attempt {attempt_id} identity changed")
                self.conn.commit()
                return False
            if invocation_ordinal is not None:
                if (isinstance(invocation_ordinal, bool)
                        or not isinstance(invocation_ordinal, int)
                        or not self.conn.execute(
                            "SELECT 1 FROM invocations WHERE stage=? AND ordinal=?",
                            (stage, invocation_ordinal)).fetchone()):
                    raise ImmutableViolation("attempt invocation identity is not registered")
            if work_id:
                row = self.conn.execute(
                    "SELECT stage,request_hash,state FROM work_items WHERE work_id=?",
                    (work_id,)).fetchone()
                if not row or row[:2] != (stage, request_hash):
                    raise ImmutableViolation(f"unregistered or changed work {work_id}")
                if row[2] != "PENDING":
                    raise CheckpointError(f"work {work_id} is {row[2]}, not PENDING")
                prior = self.conn.execute(
                    "SELECT coalesce(max(attempt_no),0) FROM attempts WHERE work_id=?",
                    (work_id,)).fetchone()[0]
            else:
                prior = self.conn.execute(
                    "SELECT coalesce(max(attempt_no),0) FROM attempts WHERE control_id=?",
                    (control_id,)).fetchone()[0]
            if attempt_no != int(prior) + 1:
                raise CheckpointError(
                    f"attempt number {attempt_no} is not next after {prior}")
            if attempt_no == 1:
                if work_id:
                    expected_class = "scored"
                else:
                    expected_class = first_control_class or "preflight_probe"
                    if expected_class not in {"preflight_probe", "transport_orphan"}:
                        raise ImmutableViolation("invalid first control call class")
            else:
                previous = self.conn.execute(
                    "SELECT state FROM attempts WHERE "
                    + ("work_id=?" if work_id else "control_id=?")
                    + " AND attempt_no=?",
                    (work_id or control_id, attempt_no - 1)).fetchone()
                retry_classes = {
                    "SCHEMA_INVALID": "schema_retry",
                    "RETRYABLE_TRANSPORT": "transport_orphan",
                    "ORPHANED_UNKNOWN": "transport_orphan",
                    "CANCELLED_UNVERIFIED": "transport_orphan",
                }
                expected_class = retry_classes.get(previous[0] if previous else "")
                if expected_class is None:
                    raise CheckpointError("previous outcome does not permit another attempt")
            if call_class != expected_class:
                raise ImmutableViolation(
                    f"attempt cause requires {expected_class}, not {call_class}")
            limit = self.conn.execute(
                "SELECT allowance FROM class_limits WHERE stage=? AND call_class=?",
                (stage, call_class)).fetchone()
            if not limit:
                raise CheckpointError(f"no allowance for {stage}/{call_class}")
            used_class = self.conn.execute(
                "SELECT count(*) FROM attempts WHERE stage=? AND call_class=?",
                (stage, call_class)).fetchone()[0]
            stage_cap = self.conn.execute(
                "SELECT hard_cap FROM stage_limits WHERE stage=?", (stage,)).fetchone()[0]
            used_stage = self.conn.execute(
                "SELECT count(*) FROM attempts WHERE stage=?", (stage,)).fetchone()[0]
            header = self.header()
            expected = header["limits"].get(stage, {})
            if (limit[0] != expected.get(call_class)
                    or stage_cap != sum(expected.values())):
                raise ImmutableViolation("persisted call allowances differ from the run header")
            cap = int(header["cumulative_cap"])
            total = self.conn.execute("SELECT count(*) FROM attempts").fetchone()[0]
            if used_class + 1 > limit[0] or used_stage + 1 > stage_cap or total + 1 > cap:
                self.conn.execute("UPDATE run_state SET state='BLOCKED_BUDGET',updated=? WHERE id=1",
                                  (time.time(),))
                self.conn.commit()
                raise CapExceeded(f"call allowance exhausted for {stage}/{call_class}")
            now = time.time()
            self.conn.execute(
                "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,'DISPATCHING',NULL,NULL,?,?)",
                (attempt_id, work_id, control_id, stage, invocation_ordinal,
                 call_class, attempt_no,
                 request_hash, now, now))
            if work_id:
                self.conn.execute("UPDATE work_items SET state='DISPATCHING' WHERE work_id=?",
                                  (work_id,))
            self.conn.commit()
            return True
        except CapExceeded:
            raise
        except Exception:
            self.conn.rollback()
            raise
    def finish_attempt(self, attempt_id: str, *, outcome: str, response: Optional[str],
                       metadata: Mapping[str, Any], accept_work: bool,
                       before_commit: Optional[Callable[[], None]] = None,
                       terminal_state: Optional[str] = None) -> None:
        if outcome not in FINISH_OUTCOMES:
            raise ValueError("invalid finished-attempt outcome")
        if terminal_state not in {
                None, "FAILED_SAFETY", "BLOCKED_SECURITY", "BLOCKED_PROVENANCE"}:
            raise ValueError("invalid terminal state")
        if terminal_state is not None and terminal_state != outcome:
            raise ValueError("terminal state must match the attempt outcome")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT work_id,state FROM attempts WHERE attempt_id=?",
                                    (attempt_id,)).fetchone()
            if not row:
                raise CheckpointError(f"unknown attempt {attempt_id}")
            work_id, old = row
            if old != "DISPATCHING":
                raise CheckpointError(f"attempt {attempt_id} is {old}, not DISPATCHING")
            if work_id is not None and accept_work != (outcome == "ACCEPTED"):
                raise ValueError("work success must derive from the ACCEPTED outcome")
            if work_id is None and accept_work:
                raise ValueError("control attempts cannot accept work")
            self.conn.execute(
                "UPDATE attempts SET state=?,response=?,metadata_json=?,updated=? WHERE attempt_id=?",
                (outcome, response, canonical_json(metadata), time.time(), attempt_id))
            if work_id:
                if accept_work:
                    self.conn.execute(
                        "UPDATE work_items SET state='SUCCEEDED',accepted_attempt_id=? WHERE work_id=?",
                        (attempt_id, work_id))
                elif outcome == "SCHEMA_INVALID" and self.conn.execute(
                        "SELECT count(*) FROM attempts WHERE work_id=? "
                        "AND state='SCHEMA_INVALID'", (work_id,)).fetchone()[0] >= 2:
                    self.conn.execute(
                        "UPDATE work_items SET state='COMPLETED_INVALID',"
                        "accepted_attempt_id=NULL WHERE work_id=?", (work_id,))
                else:
                    self.conn.execute("UPDATE work_items SET state='PENDING' WHERE work_id=?",
                                      (work_id,))
            if terminal_state is not None:
                self.conn.execute("UPDATE run_state SET state=?,updated=? WHERE id=1", (terminal_state, time.time()))
            if before_commit:
                before_commit()
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
    def recover(self) -> int:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self.conn.execute(
                "SELECT attempt_id,work_id FROM attempts WHERE state='DISPATCHING'").fetchall()
            self.conn.execute(
                "UPDATE attempts SET state='ORPHANED_UNKNOWN',updated=? WHERE state='DISPATCHING'",
                (time.time(),))
            for _attempt_id, work_id in rows:
                if work_id:
                    self.conn.execute("UPDATE work_items SET state='PENDING' WHERE work_id=?",
                                      (work_id,))
            if self.state() == "RUNNING":
                self.conn.execute(
                    "UPDATE run_state SET state='CANCELLED_PENDING_RESUME',updated=? WHERE id=1",
                    (time.time(),))
            self.conn.commit()
            return len(rows)
        except Exception:
            self.conn.rollback()
            raise
    def cancel(self, attempt_id: Optional[str] = None) -> None:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            if self.state() in TERMINAL_STATES:
                raise CheckpointError("terminal run cannot be cancelled")
            if attempt_id:
                row = self.conn.execute(
                    "SELECT work_id,state FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
                if not row or row[1] != "DISPATCHING":
                    raise CheckpointError("cancel target is not dispatching")
                self.conn.execute(
                    "UPDATE attempts SET state='CANCELLED_UNVERIFIED',updated=? WHERE attempt_id=?",
                    (time.time(), attempt_id))
                if row[0]:
                    self.conn.execute("UPDATE work_items SET state='PENDING' WHERE work_id=?",
                                      (row[0],))
            self.conn.execute(
                "UPDATE run_state SET state='CANCELLED_PENDING_RESUME',updated=? WHERE id=1",
                (time.time(),))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
    def backoff(self, model: str) -> BackoffRecord:
        row = self.conn.execute(
            "SELECT failures,retry_not_before FROM model_backoff WHERE model=?", (model,)).fetchone()
        return BackoffRecord(model, int(row[0]), float(row[1])) if row else BackoffRecord(model, 0, 0.0)

    def ensure_context_obligation(
            self, *, stage: str, model: str, control_id: str,
            request_hash: str, source_attempt_id: str) -> ContextObligation:
        if not all(isinstance(value, str) and value for value in
                   (stage, model, control_id, request_hash, source_attempt_id)):
            raise ValueError("context obligation identity fields must be nonempty")
        source = self.conn.execute(
            "SELECT stage,state FROM attempts WHERE attempt_id=?", (source_attempt_id,)
        ).fetchone()
        if not source or source[0] != stage or source[1] not in {
                "ACCEPTED", "SCHEMA_INVALID"}:
            raise CheckpointError("context obligation requires an HTTP-accepted attempt")
        existing = self.conn.execute(
            "SELECT control_id,request_hash,source_attempt_id FROM context_obligations "
            "WHERE stage=? AND model=?", (stage, model)).fetchone()
        frozen = (control_id, request_hash, source_attempt_id)
        if existing and existing != frozen:
            raise ImmutableViolation("context obligation identity is already frozen")
        now = time.time()
        if not existing:
            self.conn.execute(
                "INSERT INTO context_obligations VALUES(?,?,?,?,?,'PENDING',NULL,?,?)",
                (stage, model, control_id, request_hash, source_attempt_id, now, now))
        row = self.conn.execute(
            "SELECT stage,model,control_id,request_hash,source_attempt_id "
            "FROM context_obligations WHERE stage=? AND model=?", (stage, model)).fetchone()
        return ContextObligation(*map(str, row))

    def pending_context_obligation(self, stage: str) -> Optional[ContextObligation]:
        row = self.conn.execute(
            "SELECT stage,model,control_id,request_hash,source_attempt_id "
            "FROM context_obligations WHERE stage=? AND state='PENDING' ORDER BY model LIMIT 1",
            (stage,)).fetchone()
        return ContextObligation(*map(str, row)) if row else None

    def complete_context_obligation(self, *, control_id: str,
                                    attempt_id: str) -> None:
        attempt = self.conn.execute(
            "SELECT control_id,state FROM attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if attempt != (control_id, "ACCEPTED"):
            raise CheckpointError("context completion requires its accepted control attempt")
        changed = self.conn.execute(
            "UPDATE context_obligations SET state='COMPLETE',completion_attempt_id=?,"
            "updated=? WHERE control_id=? AND state='PENDING'",
            (attempt_id, time.time(), control_id)).rowcount
        if changed != 1:
            raise CheckpointError("context obligation is missing or already complete")
    def usage(self) -> dict[str, Any]:
        by_class = {(r[0], r[1]): r[2] for r in self.conn.execute(
            "SELECT stage,call_class,count(*) FROM attempts GROUP BY stage,call_class")}
        return {"total": sum(by_class.values()), "by_class": by_class}
    def work(self, work_id: str) -> tuple[str, Optional[str]]:
        row = self.conn.execute(
            "SELECT state,accepted_attempt_id FROM work_items WHERE work_id=?", (work_id,)).fetchone()
        if not row:
            raise CheckpointError(f"unknown work {work_id}")
        return str(row[0]), row[1]
    @staticmethod
    def _fsync_file_and_parent(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        dfd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
