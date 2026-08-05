"""Baseline-aware public-artifact leakage scanner.

DISPOSITION: retained diagnostic.

C0B-1 has no private scan mode. A public scan requires two explicit inputs:

* an owner-only baseline inventory created before the task; and
* the owner-only raw Stage-B JSONL artifact whose responses must stay out of git.

The baseline stores git status plus path metadata, never unrelated file content or
content hashes. During a scan, only changed allowlisted task files are opened.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_VERSION = 1
BASELINE_MAX_AGE_S = 30 * 24 * 60 * 60

# Exact expected task paths. Not broad globs.
ALLOWLIST_EXACT: Set[str] = {
    "docs/dev/ollama_integration/BENCHMARK.md",
    "docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B1.md",
    "docs/dev/ollama_integration/CONTRACT_ERRATA.md",
    "docs/dev/ollama_integration/LESSONS_LEARNED.md",
    "docs/dev/ollama_integration/README.md",
    "docs/dev/ollama_integration/STAGE_B_OUTCOME_C0B1.md",
    "scripts/analyst_benchmark/__init__.py",
    "scripts/analyst_benchmark/__main__.py",
    "scripts/analyst_benchmark/chunker.py",
    "scripts/analyst_benchmark/client.py",
    "scripts/analyst_benchmark/detectors.py",
    "scripts/analyst_benchmark/goldset.py",
    "scripts/analyst_benchmark/leakscan.py",
    "scripts/analyst_benchmark/ledger.py",
    "scripts/analyst_benchmark/metrics.py",
    "scripts/analyst_benchmark/preflight.py",
    "scripts/analyst_benchmark/protocol.py",
    "scripts/analyst_benchmark/report.py",
    "scripts/analyst_benchmark/rescore_c0b1.py",
    "scripts/analyst_benchmark/resources.py",
    "scripts/analyst_benchmark/runner.py",
    "scripts/analyst_benchmark/sandbox_smoke.py",
    "scripts/analyst_benchmark/stages.py",
    "scripts/analyst_benchmark/worksheet.py",
    "scripts/tests/test_analyst_benchmark.py",
    "scripts/tests/test_analyst_benchmark_integrity.py",
    "scripts/tests/test_analyst_security_provenance.py",
    "scripts/tests/test_analyst_stage_a_cleanup.py",
    "shared/tests/analyst_container_cases.py",
    "shared/tests/test_analyst_confirmation_gates.py",
    "shared/tests/test_analyst_container_cases.py",
    "shared/tests/test_analyst_gold_set.py",
    "shared/tests/test_analyst_purity.py",
    "shared/tests/fixtures/analyst_gold/__init__.py",
    "shared/tests/fixtures/analyst_gold/generate.py",
    "shared/tests/fixtures/analyst_gold/manifest.json",
}
ALLOWLIST_PREFIX: Tuple[str, ...] = ("shared/tests/fixtures/analyst_gold/docs/",)

GENERIC_PATTERNS: Dict[str, re.Pattern] = {
    "home_path": re.compile(r"/home/[a-z][a-z0-9_-]*/(?!DEV/dirracuda)"),
    "private_mount": re.compile(r"Documents/Extracted"),
    "pseudonym_key": re.compile(r"pseudonym\.key"),
    "bearer_token": re.compile(r"(?i)\b(authorization|bearer|api[_-]?key)\b\s*[:=]"),
    "private_ipv4": re.compile(
        r"\b(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|"
        r"172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b"),
}

SELF_REFERENTIAL: Dict[str, Dict[str, str]] = {
    "scripts/analyst_benchmark/leakscan.py": {
        "private_mount": "this scanner's own detection pattern",
    },
    "shared/tests/test_analyst_confirmation_gates.py": {
        "private_mount": "asserts no module names the private corpus root",
        "home_path": "asserts no module hand-builds a home-relative path",
    },
    "scripts/tests/test_analyst_benchmark.py": {
        "private_ipv4": "RFC1918 address used as a preflight rejection case",
    },
}


class BaselineError(RuntimeError):
    """The explicit task baseline is missing, stale, altered, or unsafe."""


def _git(*args: str) -> bytes:
    cp = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                        check=True, shell=False)
    return cp.stdout


def _head() -> str:
    return _git("rev-parse", "HEAD").decode("ascii").strip()


def _status_paths() -> List[Tuple[str, str]]:
    """Porcelain-v1 records with untracked directories expanded to file paths."""
    fields = _git("status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0")
    out: List[Tuple[str, str]] = []
    i = 0
    while i < len(fields):
        raw = fields[i]
        i += 1
        if not raw:
            continue
        code = raw[:2].decode("ascii", errors="replace")
        path = raw[3:].decode("utf-8", errors="surrogateescape")
        out.append((code, path))
        if "R" in code or "C" in code:
            if i >= len(fields) or not fields[i]:
                raise BaselineError("malformed git rename/copy status record")
            old = fields[i].decode("utf-8", errors="surrogateescape")
            i += 1
            out.append((f"{code}:source", old))
    return out


def _metadata(rel: str, code: str) -> Dict[str, Any]:
    path = REPO_ROOT / rel
    try:
        st = path.lstat()
    except FileNotFoundError:
        return {"path": rel, "status": code, "kind": "missing"}
    kind = "symlink" if stat.S_ISLNK(st.st_mode) else \
        "file" if stat.S_ISREG(st.st_mode) else \
        "directory" if stat.S_ISDIR(st.st_mode) else "other"
    return {
        "path": rel,
        "status": code,
        "kind": kind,
        "mode": stat.S_IMODE(st.st_mode),
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
    }


def status_inventory() -> Dict[str, Dict[str, Any]]:
    return {path: _metadata(path, code) for code, path in _status_paths()}


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _outside_repo(path: Path) -> Path:
    absolute = path.absolute()
    try:
        absolute.resolve(strict=False).relative_to(REPO_ROOT.resolve())
    except ValueError:
        return absolute
    raise BaselineError("baseline/raw artifact must live outside the repository")


def _protected_file(path: Path) -> os.stat_result:
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise BaselineError(f"not a regular non-symlink file: {path}")
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
        raise BaselineError(f"file must be owner-only (0600): {path}")
    return st


def _protected_parent(path: Path) -> None:
    st = path.parent.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise BaselineError(f"baseline parent is not a real directory: {path.parent}")
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
        raise BaselineError(f"baseline parent must be owner-only (0700): {path.parent}")


def create_baseline(path: Path) -> Path:
    """Exclusively record a pre-task inventory without reading file contents."""
    path = _outside_repo(path)
    _protected_parent(path)
    body: Dict[str, Any] = {
        "version": BASELINE_VERSION,
        "created_epoch": int(time.time()),
        "repo_root": str(REPO_ROOT.resolve()),
        "head": _head(),
        "inventory": status_inventory(),
    }
    artifact = dict(body)
    artifact["integrity_sha256"] = hashlib.sha256(_canonical(body)).hexdigest()
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            json.dump(artifact, fh, sort_keys=True, indent=2)
            fh.write("\n")
    finally:
        if fd >= 0:
            os.close(fd)
    return path


def load_baseline(path: Path, *, now: int | None = None) -> Dict[str, Any]:
    path = _outside_repo(path)
    _protected_file(path)
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BaselineError(f"baseline unreadable: {type(exc).__name__}") from exc
    digest = artifact.pop("integrity_sha256", None)
    actual = hashlib.sha256(_canonical(artifact)).hexdigest()
    if not isinstance(digest, str) or not secrets_compare(digest, actual):
        raise BaselineError("baseline integrity check failed")
    if artifact.get("version") != BASELINE_VERSION:
        raise BaselineError("unsupported baseline version")
    if artifact.get("repo_root") != str(REPO_ROOT.resolve()):
        raise BaselineError("baseline belongs to a different repository")
    if artifact.get("head") != _head():
        raise BaselineError("baseline HEAD is stale")
    current = int(time.time()) if now is None else now
    created = artifact.get("created_epoch")
    if not isinstance(created, int) or created > current + 300 or \
            current - created > BASELINE_MAX_AGE_S:
        raise BaselineError("baseline timestamp is stale or invalid")
    if not isinstance(artifact.get("inventory"), dict):
        raise BaselineError("baseline inventory is invalid")
    return artifact


def secrets_compare(a: str, b: str) -> bool:
    """Constant-time compare without importing private-data functionality."""
    import hmac
    return hmac.compare_digest(a, b)


def allowed(path: str) -> bool:
    return path in ALLOWLIST_EXACT or path.startswith(ALLOWLIST_PREFIX)


def load_raw_responses(paths: Sequence[Path]) -> List[str]:
    if not paths:
        raise BaselineError("at least one explicit raw Stage-B artifact is required")
    samples: List[str] = []
    for supplied in paths:
        path = _outside_repo(supplied)
        _protected_file(path)
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line_no, line in enumerate(fh, start=1):
                    row = json.loads(line)
                    value = row.get("raw_response")
                    if not isinstance(value, str):
                        raise BaselineError(f"raw_response missing at {path}:{line_no}")
                    if len(" ".join(value.split())) >= 24:
                        samples.append(value)
        except (OSError, ValueError) as exc:
            if isinstance(exc, BaselineError):
                raise
            raise BaselineError(f"raw artifact unreadable: {type(exc).__name__}") from exc
    if not samples:
        raise BaselineError("raw artifacts contained no fingerprintable responses")
    return samples


def scan_content(paths: Sequence[str], raw_responses: Sequence[str]) -> List[str]:
    hits: List[str] = []
    normalized_samples = [" ".join(s.split()).lower() for s in raw_responses]
    for rel in paths:
        path = REPO_ROOT / rel
        if not path.is_file() or path.is_symlink():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        exempt = SELF_REFERENTIAL.get(rel, {})
        for name, pattern in GENERIC_PATTERNS.items():
            match = pattern.search(text)
            if match and name not in exempt:
                hits.append(f"{rel}: generic pattern {name} at offset {match.start()}")
        normalized = " ".join(text.split()).lower()
        for sample in normalized_samples:
            if sample in normalized:
                hits.append(f"{rel}: exact raw model response matched")
                break
    return hits


def run(*, baseline_path: Path, raw_artifacts: Sequence[Path],
        mode: str = "public") -> int:
    if mode != "public":
        print("private leakage scanning is unavailable until C0B-2 gates exist")
        return 2
    try:
        baseline = load_baseline(baseline_path)
        raw_responses = load_raw_responses(raw_artifacts)
        before: Dict[str, Dict[str, Any]] = baseline["inventory"]
        current = status_inventory()
    except (BaselineError, OSError, subprocess.SubprocessError) as exc:
        print(f"leak scan (public)\n  RESULT: FAIL CLOSED — {exc}")
        return 1

    all_paths = sorted(set(before) | set(current))
    task_delta = [p for p in all_paths if before.get(p) != current.get(p)]
    preexisting = [p for p in all_paths if before.get(p) == current.get(p)]
    unlisted = sorted(p for p in task_delta if not allowed(p))

    # Never open an unrelated delta. Its unexpected path is already a failure.
    scan_paths = sorted(p for p in task_delta if allowed(p) and p in current)
    content_hits = scan_content(scan_paths, raw_responses)

    print("leak scan (public)")
    print(f"  pre-existing, unchanged: {preexisting or '-'}")
    print(f"  task delta paths       : {len(task_delta)}")
    print(f"  outside allowlist      : {unlisted or '-'}")
    print(f"  raw responses checked  : {len(raw_responses)}")
    print(f"  content hits           : {content_hits or '-'}")
    ok = not unlisted and not content_hits
    print(f"  RESULT                 : {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1
