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

FROZEN_C0B2_PUBLIC_PATHS = FROZEN_C0B2A_PATHS | FROZEN_C0B2B1_PATHS | frozenset({
    "docs/dev/ollama_integration/CONTRACT_ERRATA.md",
    "docs/dev/ollama_integration/BENCHMARK_PUBLIC_CDF_SCHEMA.md",
    "scripts/analyst_benchmark/leakscan.py",
    "scripts/analyst_benchmark/c0b2_public_schema.py",
    "scripts/analyst_benchmark/c0b2_public_scoring.py",
    "scripts/analyst_benchmark/c0b2_runtime_common.py",
    "scripts/analyst_benchmark/c0b2_runtime_d.py",
    "scripts/analyst_benchmark/c0b2_runtime_f.py",
    "scripts/analyst_benchmark/c0b2_runtime_f_evidence.py",
    "scripts/analyst_benchmark/c0b2_runtime_f_namespace.py",
    "scripts/analyst_benchmark/c0b2_stage_d_plan.py",
    "scripts/analyst_benchmark/c0b2_stage_d.py",
    "scripts/analyst_benchmark/c0b2_stage_f_plan.py",
    "scripts/analyst_benchmark/c0b2_stage_f.py",
    "scripts/tests/test_analyst_c0b2_public_schema.py",
    "scripts/tests/test_analyst_c0b2_public_scoring.py",
    "scripts/tests/test_analyst_c0b2_runtime_common.py",
    "scripts/tests/test_analyst_c0b2_stage_d_plan.py",
    "scripts/tests/test_analyst_c0b2_stage_d.py",
    "scripts/tests/test_analyst_c0b2_runtime_d.py",
    "scripts/tests/test_analyst_c0b2_stage_f_plan.py",
    "scripts/tests/test_analyst_c0b2_stage_f.py",
    "scripts/tests/test_analyst_c0b2_runtime_f.py",
    "scripts/tests/test_analyst_c0b2_runtime_f_evidence.py",
    "scripts/tests/test_analyst_c0b2_runtime_f_namespace.py",
    "scripts/tests/test_analyst_c0b2_public_flow.py",
    "scripts/tests/test_analyst_security_provenance.py",
})

# C0B-3 gets a distinct source identity.  Never widen or reinterpret the
# historical C0B-2 set: old checkpoints must continue to hash its exact tree.
FROZEN_C0B3_PUBLIC_PATHS = FROZEN_C0B2_PUBLIC_PATHS | frozenset({
    "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B3.md",
    "docs/dev/ollama_integration/UI_MOCKUPS.md",
    "scripts/analyst_benchmark/c0b3_cli.py",
    "scripts/analyst_benchmark/c0b3_policy.py",
    "scripts/analyst_benchmark/c0b3_schema.py",
    "scripts/tests/test_analyst_c0b3_cli.py",
    "scripts/tests/test_analyst_c0b3_policy.py",
    "scripts/tests/test_analyst_c0b3_public_flow.py",
    "scripts/tests/test_analyst_c0b3_runtime.py",
    "scripts/tests/test_analyst_c0b3_schema.py",
})

# C0B-4 has its own immutable 83-path source identity.  Do not widen either
# historical set: their stored task-tree hashes retain their exact meanings.
FROZEN_C0B4_PUBLIC_PATHS = FROZEN_C0B3_PUBLIC_PATHS | frozenset({
    "docs/dev/ollama_integration/BENCHMARK.md",
    "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B4.md",
    "docs/dev/ollama_integration/PUBLIC_CDF_OUTCOME_C0B3.md",
    "scripts/analyst_benchmark/c0b4_answer.py",
    "scripts/analyst_benchmark/c0b4_backup.py",
    "scripts/analyst_benchmark/c0b4_checkpoint.py",
    "scripts/analyst_benchmark/c0b4_cli.py",
    "scripts/analyst_benchmark/c0b4_executor.py",
    "scripts/analyst_benchmark/c0b4_filesystem.py",
    "scripts/analyst_benchmark/c0b4_plan.py",
    "scripts/analyst_benchmark/c0b4_policy.py",
    "scripts/analyst_benchmark/c0b4_runtime.py",
    "scripts/analyst_benchmark/c0b4_schema.py",
    "scripts/analyst_benchmark/c0b4_scoring.py",
    "scripts/tests/test_analyst_c0b4_answer.py",
    "scripts/tests/test_analyst_c0b4_backup.py",
    "scripts/tests/test_analyst_c0b4_checkpoint.py",
    "scripts/tests/test_analyst_c0b4_cli.py",
    "scripts/tests/test_analyst_c0b4_executor.py",
    "scripts/tests/test_analyst_c0b4_plan.py",
    "scripts/tests/test_analyst_c0b4_policy.py",
    "scripts/tests/test_analyst_c0b4_public_flow.py",
    "scripts/tests/test_analyst_c0b4_runtime.py",
    "scripts/tests/test_analyst_c0b4_schema.py",
    "scripts/tests/test_analyst_c0b4_scoring.py",
})


def public_generation_options_sha256() -> str:
    """Hash every allowed C/D/F generation-factor combination."""
    from . import c0b2_plan as c
    from . import c0b2_stage_d_plan as d
    from . import c0b2_stage_f_plan as f

    rows = []
    contexts = (4096, 8192, 16384)
    for model, digest, think in c.MODELS:
        for worksheet in c.WORKSHEETS:
            rows.append({"phase": "C", "model": model, "model_digest": digest,
                         "worksheet": worksheet, "chunk_chars": 4000,
                         "overlap": c.OVERLAP, "config": {
                             "keep_alive": c.KEEP_ALIVE,
                             "options": dict(c.OPTIONS_C), "think": think}})
            for budget in d._D1_BUDGETS[model]:
                factors = {
                    "D1": ((4000, 8192, 1),),
                    "D2": tuple((chunk, 16384, 1) for chunk in d.D2_CHUNKS),
                    "D3": tuple((chunk, 16384, 1) for chunk in d.D2_CHUNKS),
                    "D4": tuple((chunk, context, 1) for chunk in d.D2_CHUNKS
                                for context in contexts[:-1]),
                    "F": tuple((chunk, context, seed) for chunk in d.D2_CHUNKS
                               for context in contexts for seed in f.SEEDS),
                    "F_ACCEPTANCE": tuple((chunk, context, 1)
                                          for chunk in d.D2_CHUNKS
                                          for context in contexts),
                }
                for phase, combinations in factors.items():
                    for chunk_chars, num_ctx, seed in combinations:
                        candidate = {"model": model, "num_ctx": num_ctx,
                                     "num_predict": budget}
                        config = (d._generation_config(model, num_ctx, budget)
                                  if phase.startswith("D") else
                                  f._generation_config(candidate, seed))
                        rows.append({"phase": phase, "model": model,
                                     "model_digest": digest, "worksheet": worksheet,
                                     "chunk_chars": chunk_chars, "overlap": d.OVERLAP,
                                     "config": config})
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


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


def read_regular_file(
        path: Path, *, trusted_root: Path, max_bytes: int | None = None,
        required_mode: int | None = None,
        required_trusted_root_mode: int | None = None,
) -> tuple[os.stat_result, bytes]:
    """Read below an explicit boundary without following any path component.

    ``trusted_root`` is a lexical authority boundary, not a path to resolve. Every
    component from the filesystem root through the target parent stays open and is
    rebound to its name after the read, closing intermediate-directory swap races.
    """
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise LeakGateError("platform cannot provide pinned no-follow file reads")
    path_text = os.fspath(path)
    boundary_text = os.fspath(trusted_root)
    if any(component in {".", ".."}
           for value in (path_text, boundary_text)
           for component in value.split(os.sep)):
        raise LeakGateError("task path contains an unsafe lexical component")
    target = Path(os.path.abspath(os.path.normpath(path_text)))
    boundary = Path(os.path.abspath(os.path.normpath(boundary_text)))
    if any(component in {".", ".."}
           for component in (*target.parts[1:], *boundary.parts[1:])):
        raise LeakGateError("task path contains an unsafe lexical component")
    try:
        target.relative_to(boundary)
    except ValueError as exc:
        raise LeakGateError("task file escapes its trusted root") from exc
    if target == boundary or target.name in {"", ".", ".."}:
        raise LeakGateError("task file is not below its trusted root")

    common = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow
    directory_flags = common | directory
    directory_fds: list[int] = []
    bindings: list[tuple[int, str, int]] = []
    fd = -1
    try:
        current = os.open("/", directory_flags)
        directory_fds.append(current)
        boundary_parts = boundary.parts[1:]
        parent_parts = target.parent.parts[1:]
        for index, component in enumerate(parent_parts, start=1):
            child = os.open(component, directory_flags, dir_fd=current)
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                os.close(child)
                raise LeakGateError("task path contains a non-directory component")
            bindings.append((current, component, child))
            directory_fds.append(child)
            current = child
            if index == len(boundary_parts):
                trusted = os.fstat(child)
                if (required_trusted_root_mode is not None
                        and (trusted.st_uid != os.getuid()
                             or stat.S_IMODE(trusted.st_mode)
                             != required_trusted_root_mode)):
                    raise LeakGateError("trusted root is not exact owner-only mode")
        fd = os.open(target.name, common, dir_fd=current)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid():
            raise LeakGateError("task path is not an owner-controlled regular file")
        if (required_mode is not None
                and stat.S_IMODE(before.st_mode) != required_mode):
            raise LeakGateError("task file mode is not the required owner-only mode")
        if max_bytes is not None and before.st_size > max_bytes:
            raise LeakGateError("task file exceeds its safe read limit")
        body = bytearray()
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            body.extend(block)
            if max_bytes is not None and len(body) > max_bytes:
                raise LeakGateError("task file exceeds its safe read limit")
        after = os.fstat(fd)
        identity_before = (before.st_dev, before.st_ino, before.st_size,
                           before.st_mtime_ns, before.st_ctime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size,
                          after.st_mtime_ns, after.st_ctime_ns)
        if identity_before != identity_after:
            raise LeakGateError("task file changed while being read")
        named = os.stat(target.name, dir_fd=current, follow_symlinks=False)
        if ((named.st_dev, named.st_ino) != (after.st_dev, after.st_ino)
                or not stat.S_ISREG(named.st_mode)):
            raise LeakGateError("task file name changed while being read")
        for parent_fd, name, child_fd in reversed(bindings):
            named_dir = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            opened_dir = os.fstat(child_fd)
            if ((named_dir.st_dev, named_dir.st_ino)
                    != (opened_dir.st_dev, opened_dir.st_ino)
                    or not stat.S_ISDIR(named_dir.st_mode)):
                raise LeakGateError("task directory changed while being read")
        return after, bytes(body)
    except OSError as exc:
        raise LeakGateError(f"task file cannot be opened safely: {path}") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        for opened in reversed(directory_fds):
            os.close(opened)


def _regular_digest(path: Path, trusted_root: Path) -> tuple[os.stat_result, str]:
    verified, body = read_regular_file(path, trusted_root=trusted_root)
    return verified, hashlib.sha256(body).hexdigest()


def _seal_entry(repo_root: Path, rel: str, code: str) -> SealEntry:
    path = repo_root / rel
    try:
        current = path.lstat()
    except FileNotFoundError:
        return SealEntry(rel, code, "missing", 0, 0,
                         hashlib.sha256(b"missing").hexdigest())
    mode = stat.S_IMODE(current.st_mode)
    if stat.S_ISREG(current.st_mode):
        verified, digest = _regular_digest(path, repo_root)
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
        allowed_paths: Iterable[str] = FROZEN_C0B2_PUBLIC_PATHS,
) -> tuple[str, ...]:
    """Fail closed unless all post-baseline changes are exact allowed files."""
    if before.head != after.head:
        raise LeakGateError("Git HEAD changed after the protected baseline")
    changed = task_delta_paths(before, after)
    allowed = frozenset(allowed_paths)
    unexpected = tuple(path for path in changed if path not in allowed)
    if unexpected:
        raise LeakGateError("worktree changed outside the frozen C0B-2 public allowlist: "
                            + ", ".join(unexpected))
    current = after.by_path()
    unsafe = tuple(path for path in changed
                   if path in current and current[path].kind != "file")
    if unsafe:
        raise LeakGateError("allowed task paths must be regular files: "
                            + ", ".join(unsafe))
    return changed
