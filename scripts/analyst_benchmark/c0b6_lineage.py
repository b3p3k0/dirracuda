"""Descriptor-pinned C0B-3/C0B-4 parent verification for C0B-6.

This module owns only immutable parent identity.  It deliberately does not import
the C0B-4 checkpoint, policy, plan, schema, scoring, runtime, or backup modules.
C0B-4 terminal semantics are replayed independently by :mod:`c0b6_replay`.

DISPOSITION: benchmark-only; retain through the accepted C0B-6 result.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


FROZEN_EXECUTION_PARENT: dict[str, Any] = {
    "run_id": "c0b3-20260809-154924-19afcaab26984160f20ec075",
    "source_commit": "dcd7e0b9504ded47dad82f25814aea54d666b268",
    "checkpoint_sha256": "f8cbd0419f62656476b38c60b628b1ce20f67b097d2ce7e8bc38381d80d852e3",
    "run_header_sha256": "80424fbfb492cae4264798d6294337c3beaca21f2172da302114adf05d8210b2",
    "benchmark_protocol_id": "c0b3-assistive-confirmation-v1",
    "protocol_sha256": "031b41f6cf0f153b94c47dc55907eae77fd6600379c009434dbc752deb33022d",
    "policy_id": "c0b3-assistive-bounded-fp-v1",
    "policy_sha256": "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235",
    "task_tree_sha256": "a936817083810cebc4f017d34f4d0be5e0821c1c1e0c9ffff218df69b9782bb0",
    "final_d_decision_sha256": "5c00ef2b06c014f7617bdb367034dc7be99fd462467961c7a15d3eac5b53d894",
    "d4_aggregate_sha256": "7cf23921758c6be35038456e7f4e568cef4f20618bf8ce9a9dddac5af7bab945",
    "f_master_plan_sha256": "093af02da48d938278e791955dc196ec1c8e0dacb434ddbe204186f2fbb963de",
    "seed1_aggregate_sha256": "cd87e163b2ac08b9f4de9f90291247411e80830a23a9bf635f8e6e2ba9eb11e1",
    "terminal_result_sha256": "ee2c8ed8c923deba3fb30eec3dcf5af87da69de9678bf6f45303e5ffeb1d9bcc",
    "completion_sha256": "6958b94d19d2a404003fba3e2d628a6828810cd503e8ced5bfc76f4f4ead5c00",
    "master_manifest_sha256": "df609a7c5c0baaf3215bb74ef8a3598c5f8ad5b75a16caad41cf3cd1523d5e12",
    "seed17_old_plan_sha256": "2175e51108362a273f13292b95fafd724cfc90b6817b15197c93fe2055d41f31",
    "seed17_old_plan_census": {
        "planned_work_rows": 92, "registered_work_rows": 0,
        "attempt_rows": 0, "activation_rows": 0,
    },
    "seed20260804_old_plan_sha256": "0a8e56835af83659ae6274772401da742feefb6e4d4121ed7c995cafbe9dcb21",
    "seed20260804_old_plan_census": {
        "planned_work_rows": 92, "registered_work_rows": 0,
        "attempt_rows": 0, "activation_rows": 0,
    },
    "backup_anchor_sha256": "b37396143265013ed01361d7ec31edff3d84c358d2f6c8ce932df39b21e61c56",
    "backup_snapshot_sha256": "262498adb36c12ef44fdeb779283e17305378cfcac33b4e87c740017453a799c",
    "backup_receipt_sha256": "398755d38227c30c527c787c3205407ed0ba47f18ccfab4b865584fb74ec14f9",
}

FROZEN_OBSERVED_C0B4: dict[str, Any] = {
    "run_id": "c0b4-20260811-210848-d2b52272f3aabb156f55d166",
    "source_commit": "377e4eb9e277d24d9ef1699d3a427253c052df75",
    "checkpoint_sha256": "c6d3e8e8dfeba129911ab034bb8301f028722227bf6c3e1d3817b1fa461d4285",
    "run_header_sha256": "301719b3a4d570bb87017f01bfb27d16db2d66c652ed251c56e71c423b2e7f0b",
    "benchmark_protocol_id": "c0b4-grounded-duplicate-confirmation-v1",
    "protocol_sha256": "71bde3bdd02f338216aa9a964a21207db3d1d4c80f0e676dab04776f7f833ae0",
    "policy_id": "c0b4-bounded-grounded-dedup-v1",
    "policy_sha256": "7475e748165366ed0fb83daf1b6dae21a98d92d1c8faf3e3b7a3513aa3745c43",
    "task_tree_sha256": "2e6c04acee48ce4b01f591239568b260b7dc6d5f4273c579c083513852f459fe",
    "master_plan_sha256": "7faea74d2d2d856658a3854af04576c83ba3f1cacb1fbbe939ad87db58e11832",
    "lane_plan_sha256s": {
        "F72_17": "945298c296a86dde850e2e8253aaebe1c99ee86886a8a93bf794261212929cd5",
        "F72_20260804": "ed9e9ac2ac9937a5460b9a6be63ea017a2a53d7a6630772d141910f2c3250169",
        "C44_1": "3333d49c849fb36eea7695be5338664cda60b9843d47648e279fd3bd191f6f6f",
    },
    "inactive_lane_census": {
        "F72_20260804": {
            "planned_work_rows": 92, "activation_rows": 0,
            "attempt_rows": 0, "aggregate_rows": 0,
        },
        "C44_1": {
            "planned_work_rows": 44, "activation_rows": 0,
            "attempt_rows": 0, "aggregate_rows": 0,
        },
    },
    "f72_seed17_aggregate_sha256": "4b86e1fc4a3e9ccf198247da8782a9be688c606f4a8a2dce7fd7b0a5c717215e",
    "terminal_result_sha256": "7c9a387e2b3b17bb028eb3c98156a54059ce23d316174b6ec81030ed0ac73497",
    "completion_sha256": "5b2144227b15a89e17a1ec235976cee4a26e193b5dee31c5b91d09ab7f0e051c",
    "backup_anchor_sha256": "60ac16a8962a5b87b16cc5bf7beeaae3d8009cf4d26a5441656ef125d2602358",
    "backup_snapshot_sha256": "f31a38d269a13c6df8b9e264f8d149d161504e3e3cdcae1ea0f1fd2a253fe94b",
    "backup_receipt_sha256": "e758b8f1bbe8f1a2d2c4edf048b64ff7f8be82392c26df18427e6a3e87546c75",
}

FROZEN_PARENT_BINDING: dict[str, Any] = {
    "execution_parent": FROZEN_EXECUTION_PARENT,
    "observed_c0b4": FROZEN_OBSERVED_C0B4,
}


class C0B6LineageError(RuntimeError):
    """A parent path, byte identity, or replayed lineage fact is invalid."""


@dataclass(frozen=True)
class ParentPaths:
    """The four immutable parent files required before child creation."""

    c0b3_checkpoint: Path
    c0b3_snapshot: Path
    c0b4_checkpoint: Path
    c0b4_snapshot: Path

    def __post_init__(self) -> None:
        for field in self.__dataclass_fields__:
            object.__setattr__(self, field, Path(getattr(self, field)))


@dataclass(frozen=True)
class FileIdentity:
    path: Path
    sha256: str
    size: int
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class ParentVerification:
    """Immutable handoff from pre-child verification to later rechecks."""

    paths: ParentPaths
    files: tuple[FileIdentity, ...]
    c0b3_d50_facts: Any
    c0b4_facts: Any

    @property
    def parent_binding(self) -> dict[str, Any]:
        return validate_parent_binding(FROZEN_PARENT_BINDING)


C0B3Verifier = Callable[[Path, Mapping[str, Any]], Mapping[str, Any] | None]


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_fd(fd: int) -> str:
    digest, offset = hashlib.sha256(), 0
    while True:
        block = os.pread(fd, 1 << 20, offset)
        if not block:
            return digest.hexdigest()
        digest.update(block)
        offset += len(block)


def validate_parent_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a fresh exact §4 binding; reject partial, mixed, or coerced forms."""
    if type(value) is not dict or _canonical(value) != _canonical(FROZEN_PARENT_BINDING):
        raise C0B6LineageError("parent binding differs from frozen C0B-3/C0B-4 evidence")
    return copy.deepcopy(FROZEN_PARENT_BINDING)


class _PinnedSQLite:
    """One no-follow, component-pinned, immutable read-only SQLite handle."""

    def __init__(self, path: Path, trusted_root: Path):
        self.path = Path(path)
        self.trusted_root = Path(trusted_root)
        self.fd = -1
        self.conn: sqlite3.Connection | None = None
        self._directories: list[int] = []
        self._bindings: list[tuple[int, str, int]] = []
        self._initial: os.stat_result | None = None
        self.sha256 = ""

    def __enter__(self) -> "_PinnedSQLite":
        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory = getattr(os, "O_DIRECTORY", None)
        if nofollow is None or directory is None:
            raise C0B6LineageError("platform lacks pinned no-follow reads")
        target = Path(os.path.abspath(os.path.normpath(os.fspath(self.path))))
        root = Path(os.path.abspath(os.path.normpath(os.fspath(self.trusted_root))))
        if any(part in {".", ".."} for part in (*target.parts[1:], *root.parts[1:])):
            raise C0B6LineageError("parent path contains an unsafe component")
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise C0B6LineageError("parent path escapes benchmark root") from exc
        if target == root:
            raise C0B6LineageError("parent file path names the benchmark root")
        flags = os.O_RDONLY | os.O_CLOEXEC | nofollow
        current = os.open("/", flags | directory)
        self._directories.append(current)
        root_depth = len(root.parts) - 1
        for index, component in enumerate(target.parent.parts[1:], start=1):
            child = os.open(component, flags | directory, dir_fd=current)
            child_stat = os.fstat(child)
            if not stat.S_ISDIR(child_stat.st_mode):
                os.close(child)
                raise C0B6LineageError("parent path contains a non-directory")
            if index >= root_depth and (
                    child_stat.st_uid != os.getuid()
                    or stat.S_IMODE(child_stat.st_mode) & 0o077):
                os.close(child)
                raise C0B6LineageError("benchmark parent directory is not owner-only")
            self._bindings.append((current, component, child))
            self._directories.append(child)
            current = child
        self.fd = os.open(target.name, flags, dir_fd=current)
        self._initial = os.fstat(self.fd)
        if (not stat.S_ISREG(self._initial.st_mode)
                or self._initial.st_uid != os.getuid()
                or stat.S_IMODE(self._initial.st_mode) != 0o600):
            raise C0B6LineageError("parent must be an owner-only regular file")
        self.sha256 = _sha256_fd(self.fd)
        self.conn = sqlite3.connect(
            f"file:/proc/self/fd/{self.fd}?mode=ro&immutable=1",
            uri=True, timeout=1.0)
        self.conn.execute("PRAGMA query_only=ON")
        if self.conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise C0B6LineageError("parent SQLite integrity check failed")
        if self.conn.execute("PRAGMA foreign_key_check").fetchall():
            raise C0B6LineageError("parent SQLite foreign-key check failed")
        return self

    def _assert_stable(self) -> None:
        if self.fd < 0 or self._initial is None:
            raise C0B6LineageError("parent descriptor is not open")
        for parent_fd, name, child_fd in self._bindings:
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            pinned = os.fstat(child_fd)
            if (named.st_dev, named.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise C0B6LineageError("parent directory changed during verification")
        named = os.stat(self.path, follow_symlinks=False)
        current = os.fstat(self.fd)
        stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(named, key) != getattr(current, key) for key in stable):
            raise C0B6LineageError("parent path changed during verification")
        if any(getattr(self._initial, key) != getattr(current, key) for key in stable):
            raise C0B6LineageError("parent file changed during verification")
        if _sha256_fd(self.fd) != self.sha256:
            raise C0B6LineageError("parent bytes changed during verification")

    def identity(self) -> FileIdentity:
        self._assert_stable()
        assert self._initial is not None
        return FileIdentity(
            self.path, self.sha256, self._initial.st_size, self._initial.st_dev,
            self._initial.st_ino, self._initial.st_mtime_ns,
            self._initial.st_ctime_ns)

    def __exit__(self, *_args: object) -> None:
        error: BaseException | None = None
        try:
            if self.conn is not None:
                self.conn.close()
            self._assert_stable()
        except BaseException as exc:  # preserve a stability failure after replay
            error = exc
        finally:
            if self.fd >= 0:
                os.close(self.fd)
                self.fd = -1
            for directory_fd in reversed(self._directories):
                os.close(directory_fd)
            self._directories.clear()
        if error is not None:
            raise error


def _header(conn: sqlite3.Connection) -> tuple[dict[str, Any], str]:
    rows = conn.execute("SELECT json,sha256 FROM run_header ORDER BY id").fetchall()
    if len(rows) != 1 or type(rows[0][0]) is not str or type(rows[0][1]) is not str:
        raise C0B6LineageError("parent run-header census is invalid")
    try:
        value = json.loads(rows[0][0])
    except (TypeError, json.JSONDecodeError) as exc:
        raise C0B6LineageError("parent run header is invalid JSON") from exc
    digest = hashlib.sha256(_canonical(value)).hexdigest()
    if rows[0][0].encode("utf-8") != _canonical(value) or rows[0][1] != digest:
        raise C0B6LineageError("parent run header is noncanonical or changed")
    return value, digest


def _verify_c0b3_header(conn: sqlite3.Connection) -> None:
    value, digest = _header(conn)
    expected = FROZEN_EXECUTION_PARENT
    checks = {
        "run_id": value.get("run_id"),
        "source_commit": value.get("git_head"),
        "benchmark_protocol_id": value.get("benchmark_protocol_id"),
        "protocol_sha256": value.get("protocol_sha256"),
        "policy_id": value.get("policy_id"),
        "policy_sha256": value.get("policy_sha256"),
        "task_tree_sha256": value.get("task_tree_sha256"),
        "master_manifest_sha256": value.get("master_manifest_sha256"),
    }
    if digest != expected["run_header_sha256"] or any(
            checks[key] != expected[key] for key in checks):
        raise C0B6LineageError("C0B-3 run-header literal changed")


def _default_c0b3_verifier(
        checkpoint: Path, binding: Mapping[str, Any]) -> Mapping[str, Any]:
    from .c0b2_runtime import public_verify
    result = public_verify(str(binding["run_id"]), benchmark_root=checkpoint.parents[2])
    if result.get("ok") is not True or result.get("errors") != []:
        raise C0B6LineageError("full C0B-3 parent verification failed")
    return result


def verify_parents_readonly(
        paths: ParentPaths, *, c0b3_verifier: C0B3Verifier | None = None,
) -> ParentVerification:
    """Verify both parent checkpoint/snapshot pairs without creating child state."""
    from .c0b6_replay import replay_c0b3_d50_connection, replay_c0b4_connection

    paths = ParentPaths(**{name: getattr(paths, name)
                           for name in paths.__dataclass_fields__})
    root = paths.c0b3_checkpoint.parents[2]
    if any(path == root or root not in path.parents for path in (
            paths.c0b3_snapshot, paths.c0b4_checkpoint, paths.c0b4_snapshot)):
        raise C0B6LineageError("parent paths do not share one benchmark root")
    handles: list[_PinnedSQLite] = []
    with _PinnedSQLite(paths.c0b3_checkpoint, root) as c3_db, \
            _PinnedSQLite(paths.c0b3_snapshot, root) as c3_snapshot, \
            _PinnedSQLite(paths.c0b4_checkpoint, root) as c4_db, \
            _PinnedSQLite(paths.c0b4_snapshot, root) as c4_snapshot:
        handles.extend((c3_db, c3_snapshot, c4_db, c4_snapshot))
        if (c3_db.sha256 != FROZEN_EXECUTION_PARENT["checkpoint_sha256"]
                or c3_snapshot.sha256 !=
                FROZEN_EXECUTION_PARENT["backup_snapshot_sha256"]
                or c4_db.sha256 != FROZEN_OBSERVED_C0B4["checkpoint_sha256"]
                or c4_snapshot.sha256 !=
                FROZEN_OBSERVED_C0B4["backup_snapshot_sha256"]):
            raise C0B6LineageError("parent checkpoint or snapshot digest changed")
        assert c3_db.conn is not None and c3_snapshot.conn is not None
        assert c4_db.conn is not None and c4_snapshot.conn is not None
        _verify_c0b3_header(c3_db.conn)
        _verify_c0b3_header(c3_snapshot.conn)
        verifier = c0b3_verifier or _default_c0b3_verifier
        result = verifier(paths.c0b3_checkpoint, FROZEN_EXECUTION_PARENT)
        if result is not None and isinstance(result, Mapping) \
                and result.get("ok") is not True:
            raise C0B6LineageError("C0B-3 verifier rejected the execution parent")
        c3_facts = replay_c0b3_d50_connection(c3_db.conn)
        c3_snapshot_facts = replay_c0b3_d50_connection(c3_snapshot.conn)
        if c3_facts != c3_snapshot_facts:
            raise C0B6LineageError("C0B-3 D50 checkpoint and snapshot replay differ")
        checkpoint_facts = replay_c0b4_connection(c4_db.conn, require_receipt=True)
        snapshot_facts = replay_c0b4_connection(c4_snapshot.conn, require_receipt=False)
        if checkpoint_facts.without_receipt() != snapshot_facts.without_receipt():
            raise C0B6LineageError("C0B-4 checkpoint and snapshot replay differ")
        if (checkpoint_facts.backup_snapshot_sha256 != c4_snapshot.sha256
                or checkpoint_facts.backup_snapshot_size !=
                c4_snapshot.identity().size):
            raise C0B6LineageError("C0B-4 receipt does not bind its snapshot")
        identities = tuple(handle.identity() for handle in handles)
    return ParentVerification(paths, identities, c3_facts, checkpoint_facts)


def assert_parents_unchanged(verification: ParentVerification) -> None:
    """Cheap descriptor-safe byte recheck for every later mutating command."""
    if not isinstance(verification, ParentVerification):
        raise TypeError("parent verification token is required")
    root = verification.paths.c0b3_checkpoint.parents[2]
    for expected in verification.files:
        with _PinnedSQLite(expected.path, root) as pinned:
            observed = pinned.identity()
            if observed != expected:
                raise C0B6LineageError("immutable parent evidence changed")
