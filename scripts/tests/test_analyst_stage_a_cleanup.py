"""Stage A scratch lifecycle and artifact-permission regressions.

DISPOSITION: retained guardrail.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from scripts.analyst_benchmark import runner, stages


def _scratch_probe(*, ok: bool = True) -> tuple[stages.DependencyProbe, Path]:
    root = Path(tempfile.mkdtemp(prefix=stages.SCRATCH_PREFIX, dir="/tmp"))
    python = root / "probe" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    return stages.DependencyProbe(
        ok=ok,
        scratch_root=str(root),
        scratch_python=str(python),
        pymupdf_version="1.28.0",
        mupdf_version="1.29.0",
    ), root


def test_cleanup_uses_recorded_root_even_without_python() -> None:
    probe, root = _scratch_probe()
    probe.scratch_python = None

    result = stages.cleanup_scratch(probe)

    assert result == f"removed {root}"
    assert not root.exists()


def test_cleanup_rejects_path_outside_owned_prefix(tmp_path: Path) -> None:
    probe = stages.DependencyProbe(scratch_root=str(tmp_path))

    assert stages.cleanup_scratch(probe).startswith("refusing")
    assert tmp_path.exists()


def test_dependency_probe_cleans_scratch_after_subprocess_failure(
        monkeypatch) -> None:
    before = set(Path("/tmp").glob(f"{stages.SCRATCH_PREFIX}*"))
    failed = subprocess.CompletedProcess(
        args=["bash"], returncode=7, stdout="", stderr="probe failed")
    monkeypatch.setattr(stages.subprocess, "run", lambda *_a, **_kw: failed)

    probe = stages.run_dependency_probe()

    assert not probe.ok
    assert probe.scratch_root is not None
    assert not Path(probe.scratch_root).exists()
    assert set(Path("/tmp").glob(f"{stages.SCRATCH_PREFIX}*")) == before


def test_stage_a_cleans_scratch_when_sandbox_raises(monkeypatch) -> None:
    probe, root = _scratch_probe()
    monkeypatch.setattr(stages, "run_dependency_probe", lambda: probe)

    def fail_sandbox(**_kwargs):
        raise RuntimeError("sandbox failed")

    from scripts.analyst_benchmark import sandbox_smoke
    monkeypatch.setattr(sandbox_smoke, "run_all", fail_sandbox)

    with pytest.raises(RuntimeError, match="sandbox failed"):
        runner._stage_a()
    assert not root.exists()


def test_stage_a_artifact_is_randomized_owner_only(tmp_path: Path) -> None:
    first = runner._write_stage_a_artifact({"ok": True}, tmp_path)
    second = runner._write_stage_a_artifact({"ok": True}, tmp_path)

    assert first != second
    assert first.stat().st_mode & 0o777 == 0o600
    assert json.loads(first.read_text(encoding="utf-8")) == {"ok": True}
