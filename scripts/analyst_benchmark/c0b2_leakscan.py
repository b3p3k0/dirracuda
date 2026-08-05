"""Exact C0B-2A worktree allowlist and immutable public-delta seals.

The helpers are read-only.  A pre-task seal may contain unrelated dirty work;
only changes relative to that seal are treated as this task's delta.  Symlinks
are recorded without following them and are never accepted as task files.

DISPOSITION: retained C0B-2 benchmark guardrail; remove after C0B acceptance.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

FROZEN_C0B2A_PATHS = frozenset({
    "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B2.md",
    "docs/dev/ollama_integration/README.md",
    "docs/dev/ollama_integration/RISK_REGISTER.md",
    "docs/dev/ollama_integration/LESSONS_LEARNED.md",
    "scripts/analyst_benchmark/__main__.py",
    "scripts/analyst_benchmark/c0b2_schema.py",
    "scripts/analyst_benchmark/c0b2_plan.py",
    "scripts/analyst_benchmark/c0b2_checkpoint.py",
    "scripts/analyst_benchmark/c0b2_fsprobe.py",
    "scripts/analyst_benchmark/c0b2_executor.py",
    "scripts/analyst_benchmark/c0b2_cli.py",
    "scripts/analyst_benchmark/c0b2_leakscan.py",
    "scripts/tests/test_analyst_c0b2_contract.py",
    "scripts/tests/test_analyst_c0b2_checkpoint.py",
    "scripts/tests/test_analyst_c0b2_cli.py",
})

FROZEN_C0B2B1_PATHS = frozenset({
    "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B2.md",
    "docs/dev/ollama_integration/README.md",
    "docs/dev/ollama_integration/RISK_REGISTER.md",
    "docs/dev/ollama_integration/LESSONS_LEARNED.md",
    "scripts/analyst_benchmark/c0b2_cli.py",
    "scripts/analyst_benchmark/c0b2_schema.py",
    "scripts/analyst_benchmark/c0b2_plan.py",
    "scripts/analyst_benchmark/c0b2_checkpoint.py",
    "scripts/analyst_benchmark/c0b2_executor.py",
    "scripts/analyst_benchmark/c0b2_leakscan.py",
    "scripts/analyst_benchmark/c0b2_transport.py",
    "scripts/analyst_benchmark/c0b2_runtime.py",
    "scripts/analyst_benchmark/c0b2_stage_c.py",
    "scripts/tests/test_analyst_c0b2_cli.py",
    "scripts/tests/test_analyst_c0b2_checkpoint.py",
    "scripts/tests/test_analyst_c0b2_transport.py",
    "scripts/tests/test_analyst_c0b2_runtime.py",
    "scripts/tests/test_analyst_c0b2_stage_c.py",
})


class LeakGateError(RuntimeError):
    """The worktree changed outside the frozen task boundary."""


@dataclass(frozen=True)
class SealEntry:
    path: str
    status: str
    kind: str
    mode: int
    size: int
    sha256: str


@dataclass(frozen=True)
class WorktreeSeal:
    head: str
    entries: tuple[SealEntry, ...]

    @property
    def digest(self) -> str:
        payload = {"head": self.head, "entries": [asdict(row) for row in self.entries]}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def by_path(self) -> Mapping[str, SealEntry]:
        return {entry.path: entry for entry in self.entries}


def _git(repo_root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo_root, check=True,
                            capture_output=True, shell=False)
    return result.stdout


def _safe_relative(raw: bytes) -> str:
    value = raw.decode("utf-8", errors="surrogateescape")
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or ".." in pure.parts:
        raise LeakGateError("git returned an unsafe worktree path")
    return value


def _status_paths(repo_root: Path) -> dict[str, str]:
    fields = _git(repo_root, "status", "--porcelain=v1", "-z",
                  "--untracked-files=all").split(b"\0")
    paths: dict[str, str] = {}
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record:
            continue
        if len(record) < 4:
            raise LeakGateError("malformed git status record")
        code = record[:2].decode("ascii", errors="replace")
        paths[_safe_relative(record[3:])] = code
        if "R" in code or "C" in code:
            if index >= len(fields) or not fields[index]:
                raise LeakGateError("malformed git rename/copy record")
            paths[_safe_relative(fields[index])] = code + ":source"
            index += 1
    return paths


def _regular_digest(path: Path) -> tuple[os.stat_result, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise LeakGateError("delta path changed type while sealing")
        digest = hashlib.sha256()
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(fd)
        identity_before = (before.st_dev, before.st_ino, before.st_size,
                           before.st_mtime_ns, before.st_ctime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size,
                          after.st_mtime_ns, after.st_ctime_ns)
        if identity_before != identity_after:
            raise LeakGateError("delta file changed while sealing")
        return after, digest.hexdigest()
    finally:
        os.close(fd)


def _seal_entry(repo_root: Path, rel: str, code: str) -> SealEntry:
    path = repo_root / rel
    try:
        current = path.lstat()
    except FileNotFoundError:
        return SealEntry(rel, code, "missing", 0, 0,
                         hashlib.sha256(b"missing").hexdigest())
    mode = stat.S_IMODE(current.st_mode)
    if stat.S_ISREG(current.st_mode):
        verified, digest = _regular_digest(path)
        return SealEntry(rel, code, "file", stat.S_IMODE(verified.st_mode),
                         verified.st_size, digest)
    if stat.S_ISLNK(current.st_mode):
        target = os.fsencode(os.readlink(path))
        digest = hashlib.sha256(b"symlink\0" + target).hexdigest()
        return SealEntry(rel, code, "symlink", mode, len(target), digest)
    marker = f"other:{stat.S_IFMT(current.st_mode):o}".encode("ascii")
    return SealEntry(rel, code, "other", mode, current.st_size,
                     hashlib.sha256(marker).hexdigest())


def capture_worktree_seal(repo_root: Path) -> WorktreeSeal:
    """Hash every current tracked delta/untracked file without changing it."""
    root = repo_root.resolve(strict=True)
    head = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
    statuses = _status_paths(root)
    entries = tuple(_seal_entry(root, rel, statuses[rel]) for rel in sorted(statuses))
    return WorktreeSeal(head=head, entries=entries)


def task_delta_paths(before: WorktreeSeal, after: WorktreeSeal) -> tuple[str, ...]:
    """Return paths whose sealed state changed, including additions/deletions."""
    old = before.by_path()
    new = after.by_path()
    return tuple(sorted(path for path in set(old) | set(new)
                        if old.get(path) != new.get(path)))


def assert_frozen_task_delta(
        before: WorktreeSeal,
        after: WorktreeSeal,
        *,
        allowed_paths: Iterable[str] = FROZEN_C0B2B1_PATHS,
) -> tuple[str, ...]:
    """Fail closed unless all post-baseline changes are exact allowed files."""
    if before.head != after.head:
        raise LeakGateError("Git HEAD changed after the protected baseline")
    changed = task_delta_paths(before, after)
    allowed = frozenset(allowed_paths)
    unexpected = tuple(path for path in changed if path not in allowed)
    if unexpected:
        raise LeakGateError("worktree changed outside the frozen C0B-2B1 allowlist: "
                            + ", ".join(unexpected))
    current = after.by_path()
    unsafe = tuple(path for path in changed
                   if path in current and current[path].kind != "file")
    if unsafe:
        raise LeakGateError("allowed task paths must be regular files: "
                            + ", ".join(unsafe))
    return changed
