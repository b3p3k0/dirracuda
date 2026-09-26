"""
Two output sinks, and the guard that keeps them apart.

DISPOSITION: retained diagnostic.

  raw sink        0600 files under the user-data tree, OUTSIDE the repository.
                  Contains model output and thinking byte counts; trace text is
                  discarded by the client. Never committed.
  aggregate sink  counts, rates and timings only. Safe to commit.

`assert_committable` is the guard: it refuses to emit an aggregate report that
carries document text, model output, or thinking traces.

All user-data paths come from get_paths() - never a hand-built ~/.dirracuda
string. get_paths() is imported lazily so importing this module touches nothing.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

BENCH_DIRNAME = "analyst_bench"
_RUN_ID_RE = re.compile(r"^c0b1-[0-9]{8}-[0-9]{6}-[0-9a-f]{24}$")

# Vocabulary that must never appear in the private results section.
ACCURACY_WORDS = ("precision", "recall", "f1", "accuracy", "ground truth")


def _secure_directory(path: Path, *, create: bool = False) -> Path:
    """Return an owner-only real directory, never a symlink."""
    if create:
        try:
            path.mkdir(mode=0o700, parents=False, exist_ok=False)
        except FileExistsError:
            pass
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise OSError(f"artifact directory is not a real directory: {path}")
    if st.st_uid != os.getuid():
        raise PermissionError(f"artifact directory is not owned by this user: {path}")
    os.chmod(path, 0o700)
    return path


def bench_root() -> Path:
    """~/.dirracuda/data/experimental/analyst_bench via the canonical service."""
    from shared.path_service import get_paths          # lazy: gated by caller
    root = Path(get_paths().experimental_dir) / BENCH_DIRNAME
    root.parent.mkdir(parents=True, exist_ok=True)
    return _secure_directory(root, create=True)


def _runs_root() -> Path:
    return _secure_directory(bench_root() / "runs", create=True)


def create_run(run_id: str) -> Path:
    """Create one collision-resistant run directory exclusively."""
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"invalid run id: {run_id!r}")
    d = _runs_root() / run_id
    d.mkdir(mode=0o700, exist_ok=False)
    return _secure_directory(d)


def run_dir(run_id: str) -> Path:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"invalid run id: {run_id!r}")
    return _secure_directory(_runs_root() / run_id)


def new_run_id() -> str:
    stamp = time.strftime("c0b1-%Y%m%d-%H%M%S", time.gmtime())
    return f"{stamp}-{secrets.token_hex(12)}"


def _plain(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def _artifact_path(run_id: str, name: str) -> Path:
    if not name or Path(name).name != name or name in (".", ".."):
        raise ValueError(f"invalid artifact name: {name!r}")
    return run_dir(run_id) / name


def _open_secure(path: Path, flags: int):
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags | nofollow, 0o600)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        os.close(fd)
        raise PermissionError(f"artifact is not an owner-controlled regular file: {path}")
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "w", encoding="utf-8")


def write_raw(run_id: str, name: str, payload: Any) -> Path:
    """Create one 0600 raw artifact exclusively; never follow a symlink."""
    path = _artifact_path(run_id, name)
    with _open_secure(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY) as fh:
        json.dump(_plain(payload), fh, indent=2)
        fh.write("\n")
    return path


def append_raw_jsonl(run_id: str, name: str, row: Any) -> Path:
    path = _artifact_path(run_id, name)
    with _open_secure(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY) as fh:
        fh.write(json.dumps(_plain(row), separators=(",", ":")) + "\n")
    return path


class LeakGuard(AssertionError):
    pass


def assert_committable(text: str, *, forbidden_samples: Iterable[str],
                       min_len: int = 24) -> None:
    """Refuse to emit an aggregate artifact that carries raw material.

    `forbidden_samples` are document excerpts and raw model outputs collected
    during the run. Short samples are skipped: a 6-character fragment matching
    by coincidence would make the guard useless rather than strict.
    """
    hay = " ".join(text.split()).lower()
    for sample in forbidden_samples:
        s = " ".join((sample or "").split()).lower()
        if len(s) < min_len:
            continue
        if s in hay:
            raise LeakGuard(
                f"aggregate artifact contains a {len(s)}-character raw excerpt; "
                "refusing to write")


def assert_no_accuracy_words(section_text: str) -> None:
    """The private-results section may never claim accuracy: that corpus is
    unlabelled, and detector agreement is not ground truth."""
    low = section_text.lower()
    found = [w for w in ACCURACY_WORDS if w in low]
    if found:
        raise LeakGuard(
            f"private results section uses accuracy vocabulary {found}; the "
            "private corpus is label-free and cannot support such a claim")


def coverage_line(detector_scanned: int, model_reviewed: int, total: int) -> str:
    """CONTRACT.md §4: two separate percentages, never merged into one number."""
    if total <= 0:
        return "0 files discovered"
    return (f"{detector_scanned / total:.0%} detector-scanned; "
            f"{model_reviewed / total:.0%} model-reviewed "
            f"({total} files discovered)")


def render_screening_table(verdicts: List[Any]) -> str:
    rows = ["| cell | pass | schema validity | grounding | injection events | "
            "robustness failures | reasons |",
            "|---|---|---|---|---|---|---|"]
    for v in verdicts:
        rows.append(
            f"| `{v.cell}` | {'PASS' if v.passed else 'FAIL'} | "
            f"{v.schema_validity:.3f} | {v.grounding:.3f} | "
            f"{v.injection_events} | {v.robustness_failures} | "
            f"{'; '.join(v.reasons) or '-'} |")
    return "\n".join(rows)


def render_envelope_table(envelopes: List[Dict[str, Any]],
                          limit: int = 8) -> str:
    rows = ["| trial | gpu used/total MiB | compute-proc MiB | util % | "
            "approx GPU residency | RAM avail MiB | load1 |",
            "|---|---|---|---|---|---|---|"]
    for i, e in enumerate(envelopes[:limit], start=1):
        res = e.get("gpu_residency_approx")
        rows.append(
            f"| {i} | {e.get('gpu_used_mib')}/{e.get('gpu_total_mib')} | "
            f"{e.get('compute_procs_mib')} | {e.get('gpu_util_pct')} | "
            f"{'n/a' if res is None else f'{res:.2f}'} | "
            f"{e.get('ram_available_mib')} | {e.get('load1')} |")
    return "\n".join(rows)


def write_aggregate(path: Path, body: str, *,
                    forbidden_samples: Optional[Iterable[str]] = None) -> Path:
    assert_committable(body, forbidden_samples=forbidden_samples or [])
    path.parent.mkdir(parents=True, exist_ok=True)
    with _open_secure(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY) as fh:
        fh.write(body)
    return path
