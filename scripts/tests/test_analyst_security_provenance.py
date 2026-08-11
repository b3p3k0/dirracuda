"""Focused C0B-1 safety and provenance regression tests."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.analyst_benchmark import (c0b2_leakscan, leakscan, preflight,
                                       protocol, report, runner)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   shell=False)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text("base\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "base")
    return repo


def _protected_dir(path: Path) -> Path:
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)
    return path


def test_private_leak_mode_is_not_reachable() -> None:
    with pytest.raises(SystemExit) as exc:
        runner.build_parser().parse_args(["--leak-scan", "--mode", "private"])
    assert exc.value.code == 2


def test_public_scan_uses_explicit_baseline_and_raw_without_get_paths(
        tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    unrelated = repo / "unrelated.txt"
    unrelated.write_text("pre-existing user work\n")
    secure = _protected_dir(tmp_path / "secure")
    baseline = secure / "baseline.json"

    monkeypatch.setattr(leakscan, "REPO_ROOT", repo)
    monkeypatch.setattr(leakscan, "ALLOWLIST_EXACT", {"task.txt"})
    monkeypatch.setattr(leakscan, "ALLOWLIST_PREFIX", ())
    leakscan.create_baseline(baseline)
    assert baseline.stat().st_mode & 0o777 == 0o600

    raw = secure / "raw.jsonl"
    response = '{"assessment":"findings_present","subject":"synthetic only"}'
    raw.write_text(json.dumps({"raw_response": response}) + "\n")
    os.chmod(raw, 0o600)
    (repo / "task.txt").write_text("aggregate counts only\n")

    import shared.path_service as path_service
    monkeypatch.setattr(path_service, "get_paths", lambda: pytest.fail(
        "public leak scan must not access the user-data/private path service"))

    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):
        if path == unrelated:
            pytest.fail("unchanged unrelated content was read")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    assert leakscan.run(baseline_path=baseline, raw_artifacts=[raw]) == 0
    assert "raw responses checked  : 1" in capsys.readouterr().out

    (repo / "task.txt").write_text(response + "\n")
    assert leakscan.run(baseline_path=baseline, raw_artifacts=[raw]) == 1


def test_baseline_integrity_and_freshness_fail_closed(tmp_path: Path,
                                                      monkeypatch) -> None:
    repo = _repo(tmp_path)
    secure = _protected_dir(tmp_path / "secure")
    baseline = secure / "baseline.json"
    monkeypatch.setattr(leakscan, "REPO_ROOT", repo)
    leakscan.create_baseline(baseline)

    artifact = json.loads(baseline.read_text())
    artifact["head"] = "0" * 40
    baseline.write_text(json.dumps(artifact))
    os.chmod(baseline, 0o600)
    with pytest.raises(leakscan.BaselineError, match="integrity"):
        leakscan.load_baseline(baseline)

    stale = secure / "stale.json"
    leakscan.create_baseline(stale)
    stale_artifact = json.loads(stale.read_text())
    stale_artifact["created_epoch"] = 1
    body = {k: v for k, v in stale_artifact.items() if k != "integrity_sha256"}
    stale_artifact["integrity_sha256"] = hashlib.sha256(
        leakscan._canonical(body)).hexdigest()
    stale.write_text(json.dumps(stale_artifact))
    os.chmod(stale, 0o600)
    with pytest.raises(leakscan.BaselineError, match="timestamp"):
        leakscan.load_baseline(stale)


def test_c0b4_scan_accepts_one_direct_nonmerge_task_commit(
        tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    secure = _protected_dir(tmp_path / "secure")
    baseline = secure / "baseline.json"
    raw = secure / "raw.jsonl"
    raw.write_text(json.dumps({
        "raw_response": '{"subject":"long synthetic model response"}',
    }) + "\n")
    os.chmod(raw, 0o600)
    monkeypatch.setattr(leakscan, "REPO_ROOT", repo)
    monkeypatch.setattr(leakscan, "C0B4_ALLOWLIST_EXACT", {"task.txt"})
    leakscan.create_baseline(baseline)
    (repo / "task.txt").write_text("public aggregate only\n")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-qm", "task")

    with pytest.raises(leakscan.BaselineError, match="HEAD is stale"):
        leakscan.load_baseline(baseline)
    assert leakscan.run(
        baseline_path=baseline, raw_artifacts=[raw],
        protocol_id=leakscan.C0B4_PROTOCOL_ID) == 0
    assert "task delta paths       : 1" in capsys.readouterr().out

    (repo / "task.txt").write_text("second commit\n")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-qm", "second")
    assert leakscan.run(
        baseline_path=baseline, raw_artifacts=[raw],
        protocol_id=leakscan.C0B4_PROTOCOL_ID) == 1
    assert "baseline HEAD is stale" in capsys.readouterr().out


def test_c0b4_direct_commit_still_rejects_an_unlisted_path(
        tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    secure = _protected_dir(tmp_path / "secure")
    baseline = secure / "baseline.json"
    raw = secure / "raw.jsonl"
    raw.write_text(json.dumps({
        "raw_response": '{"subject":"long synthetic model response"}',
    }) + "\n")
    os.chmod(raw, 0o600)
    monkeypatch.setattr(leakscan, "REPO_ROOT", repo)
    monkeypatch.setattr(leakscan, "C0B4_ALLOWLIST_EXACT", {"task.txt"})
    leakscan.create_baseline(baseline)
    (repo / "outside.txt").write_text("not approved\n")
    _git(repo, "add", "outside.txt")
    _git(repo, "commit", "-qm", "outside")

    assert leakscan.run(
        baseline_path=baseline, raw_artifacts=[raw],
        protocol_id=leakscan.C0B4_PROTOCOL_ID) == 1
    assert "outside.txt" in capsys.readouterr().out


@pytest.mark.parametrize("overlay", ["safe", "deleted"])
def test_c0b4_direct_commit_scans_head_blob_despite_worktree_overlay(
        overlay: str, tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    secure = _protected_dir(tmp_path / "secure")
    baseline = secure / "baseline.json"
    raw = secure / "raw.jsonl"
    response = '{"subject":"committed synthetic model response"}'
    raw.write_text(json.dumps({"raw_response": response}) + "\n")
    os.chmod(raw, 0o600)
    monkeypatch.setattr(leakscan, "REPO_ROOT", repo)
    monkeypatch.setattr(leakscan, "C0B4_ALLOWLIST_EXACT", {"task.txt"})
    leakscan.create_baseline(baseline)
    task = repo / "task.txt"
    task.write_text(response + "\n")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-qm", "leaking task")
    if overlay == "safe":
        task.write_text("safe dirty overlay\n")
    else:
        task.unlink()

    assert leakscan.run(
        baseline_path=baseline, raw_artifacts=[raw],
        protocol_id=leakscan.C0B4_PROTOCOL_ID) == 1
    assert "exact raw model response matched" in capsys.readouterr().out


def test_c0b4_direct_commit_ignores_git_replacement_objects(
        tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    secure = _protected_dir(tmp_path / "secure")
    baseline = secure / "baseline.json"
    raw = secure / "raw.jsonl"
    response = '{"subject":"committed synthetic model response"}'
    raw.write_text(json.dumps({"raw_response": response}) + "\n")
    os.chmod(raw, 0o600)
    monkeypatch.setattr(leakscan, "REPO_ROOT", repo)
    monkeypatch.setattr(leakscan, "C0B4_ALLOWLIST_EXACT", {"task.txt"})
    leakscan.create_baseline(baseline)
    task = repo / "task.txt"
    task.write_text(response + "\n")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-qm", "leaking task")
    leaking_oid = subprocess.run(
        ["git", "rev-parse", "HEAD:task.txt"], cwd=repo, check=True,
        capture_output=True, text=True, shell=False).stdout.strip()
    safe_oid = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo, check=True,
        capture_output=True, input="safe substitute\n", text=True,
        shell=False).stdout.strip()
    _git(repo, "replace", leaking_oid, safe_oid)
    task.write_text("safe dirty overlay\n")

    assert leakscan.run(
        baseline_path=baseline, raw_artifacts=[raw],
        protocol_id=leakscan.C0B4_PROTOCOL_ID) == 1
    assert "exact raw model response matched" in capsys.readouterr().out


def test_c0b4_direct_commit_rejects_committed_symlink(
        tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    secure = _protected_dir(tmp_path / "secure")
    baseline = secure / "baseline.json"
    raw = secure / "raw.jsonl"
    raw.write_text(json.dumps({
        "raw_response": '{"subject":"long synthetic model response"}',
    }) + "\n")
    os.chmod(raw, 0o600)
    monkeypatch.setattr(leakscan, "REPO_ROOT", repo)
    monkeypatch.setattr(leakscan, "C0B4_ALLOWLIST_EXACT", {"task.txt"})
    leakscan.create_baseline(baseline)
    (repo / "task.txt").symlink_to("regular-target")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-qm", "symlink task")

    assert leakscan.run(
        baseline_path=baseline, raw_artifacts=[raw],
        protocol_id=leakscan.C0B4_PROTOCOL_ID) == 1
    assert "not a regular file" in capsys.readouterr().out


def test_c0b4_direct_parent_exception_rejects_merge_commit(
        tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    secure = _protected_dir(tmp_path / "secure")
    baseline = secure / "baseline.json"
    raw = secure / "raw.jsonl"
    raw.write_text(json.dumps({
        "raw_response": '{"subject":"long synthetic model response"}',
    }) + "\n")
    os.chmod(raw, 0o600)
    monkeypatch.setattr(leakscan, "REPO_ROOT", repo)
    monkeypatch.setattr(leakscan, "C0B4_ALLOWLIST_EXACT", {"task.txt"})
    leakscan.create_baseline(baseline)
    _git(repo, "checkout", "-qb", "task-branch")
    (repo / "task.txt").write_text("task\n")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-qm", "task")
    _git(repo, "checkout", "-q", "-")
    (repo / "tracked.txt").write_text("main change\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "main")
    _git(repo, "merge", "--no-ff", "-qm", "merge", "task-branch")

    assert leakscan.run(
        baseline_path=baseline, raw_artifacts=[raw],
        protocol_id=leakscan.C0B4_PROTOCOL_ID) == 1
    assert "baseline HEAD is stale" in capsys.readouterr().out


def test_direct_parent_exception_is_not_available_to_c0b3(
        tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    secure = _protected_dir(tmp_path / "secure")
    baseline = secure / "baseline.json"
    raw = secure / "raw.jsonl"
    raw.write_text(json.dumps({
        "raw_response": '{"subject":"long synthetic model response"}',
    }) + "\n")
    os.chmod(raw, 0o600)
    monkeypatch.setattr(leakscan, "REPO_ROOT", repo)
    monkeypatch.setattr(leakscan, "C0B3_ALLOWLIST_EXACT", {"task.txt"})
    leakscan.create_baseline(baseline)
    (repo / "task.txt").write_text("task\n")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-qm", "task")

    assert leakscan.run(
        baseline_path=baseline, raw_artifacts=[raw],
        protocol_id=leakscan.BENCHMARK_PROTOCOL_ID) == 1
    assert "baseline HEAD is stale" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ("baseline", "raw"))
def test_leak_input_loaders_reject_symlinks(
        kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    secure = _protected_dir(tmp_path / "secure")
    target = secure / f"{kind}-target"
    target.write_text("{}\n")
    os.chmod(target, 0o600)
    link = secure / f"{kind}-link"
    link.symlink_to(target)
    monkeypatch.setattr(leakscan, "REPO_ROOT", repo)

    with pytest.raises(leakscan.BaselineError, match="read safely"):
        if kind == "baseline":
            leakscan.load_baseline(link)
        else:
            leakscan.load_raw_responses([link])


@pytest.mark.parametrize("kind", ("baseline", "raw"))
def test_leak_input_loaders_reject_mid_read_name_swap(
        kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    secure = _protected_dir(tmp_path / "secure")
    target = secure / f"{kind}-input"
    replacement = secure / f"{kind}-replacement"
    displaced = secure / f"{kind}-displaced"
    target.write_text("{}\n")
    replacement.write_text("{}\n")
    os.chmod(target, 0o600)
    os.chmod(replacement, 0o600)
    monkeypatch.setattr(leakscan, "REPO_ROOT", repo)
    original_read = c0b2_leakscan.os.read
    swapped = False

    def swapping_read(fd: int, size: int) -> bytes:
        nonlocal swapped
        block = original_read(fd, size)
        if block and not swapped:
            swapped = True
            target.replace(displaced)
            replacement.replace(target)
        return block

    monkeypatch.setattr(c0b2_leakscan.os, "read", swapping_read)
    with pytest.raises(leakscan.BaselineError, match="read safely"):
        if kind == "baseline":
            leakscan.load_baseline(target)
        else:
            leakscan.load_raw_responses([target])


@pytest.mark.parametrize("kind", ("baseline", "raw"))
def test_leak_input_loaders_require_0600_and_enforce_size_cap(
        kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    secure = _protected_dir(tmp_path / "secure")
    target = secure / f"{kind}-input"
    target.write_text("{}\n")
    monkeypatch.setattr(leakscan, "REPO_ROOT", repo)

    os.chmod(target, 0o640)
    with pytest.raises(leakscan.BaselineError, match="read safely"):
        if kind == "baseline":
            leakscan.load_baseline(target)
        else:
            leakscan.load_raw_responses([target])

    os.chmod(target, 0o600)
    limit_name = "BASELINE_MAX_BYTES" if kind == "baseline" \
        else "RAW_ARTIFACT_MAX_BYTES"
    monkeypatch.setattr(leakscan, limit_name, 1)
    with pytest.raises(leakscan.BaselineError, match="read safely"):
        if kind == "baseline":
            leakscan.load_baseline(target)
        else:
            leakscan.load_raw_responses([target])


@pytest.mark.parametrize("kind", ("baseline", "raw"))
def test_leak_input_loaders_reject_intermediate_symlink(
        kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    real = _protected_dir(tmp_path / "real-secure")
    target = real / f"{kind}-input"
    target.write_text("{}\n")
    os.chmod(target, 0o600)
    linked = tmp_path / "linked-secure"
    linked.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(leakscan, "REPO_ROOT", repo)

    with pytest.raises(leakscan.BaselineError, match="read safely"):
        if kind == "baseline":
            leakscan.load_baseline(linked / target.name)
        else:
            leakscan.load_raw_responses([linked / target.name])


@pytest.mark.parametrize("kind", ("baseline", "raw"))
def test_leak_input_loaders_reject_intermediate_swap(
        kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    secure = _protected_dir(tmp_path / "secure")
    replacement = _protected_dir(tmp_path / "replacement")
    displaced = tmp_path / "displaced"
    target = secure / f"{kind}-input"
    target.write_text("{}\n")
    (replacement / target.name).write_text("{}\n")
    os.chmod(target, 0o600)
    os.chmod(replacement / target.name, 0o600)
    monkeypatch.setattr(leakscan, "REPO_ROOT", repo)
    original_read = c0b2_leakscan.os.read
    swapped = False

    def swapping_read(fd: int, size: int) -> bytes:
        nonlocal swapped
        block = original_read(fd, size)
        if block and not swapped:
            swapped = True
            secure.replace(displaced)
            replacement.replace(secure)
        return block

    monkeypatch.setattr(c0b2_leakscan.os, "read", swapping_read)
    with pytest.raises(leakscan.BaselineError, match="read safely"):
        if kind == "baseline":
            leakscan.load_baseline(target)
        else:
            leakscan.load_raw_responses([target])


def test_raw_artifact_and_run_writers_are_owner_only_and_symlink_safe(
        tmp_path: Path, monkeypatch) -> None:
    experimental = tmp_path / "experimental"
    experimental.mkdir()
    import shared.path_service as path_service
    monkeypatch.setattr(path_service, "get_paths", lambda: SimpleNamespace(
        experimental_dir=experimental))

    run_id = report.new_run_id()
    run_dir = report.create_run(run_id)
    assert run_dir.stat().st_mode & 0o777 == 0o700
    artifact = report.write_raw(run_id, "header.json", {"ok": True})
    assert artifact.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        report.write_raw(run_id, "header.json", {"ok": False})

    target = tmp_path / "target"
    target.write_text("do not overwrite")
    symlink = run_dir / "linked.json"
    symlink.symlink_to(target)
    with pytest.raises(OSError):
        report.append_raw_jsonl(run_id, "linked.json", {"bad": True})
    assert target.read_text() == "do not overwrite"


def test_run_ids_are_randomized_and_collision_resistant() -> None:
    ids = {report.new_run_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(report._RUN_ID_RE.fullmatch(value) for value in ids)


def test_full_model_digest_is_required_and_recorded() -> None:
    tag = "qwen3.6:35b"
    digest = preflight.APPROVED_DIGESTS[tag]
    assert len(digest) == 64
    assert preflight.digest_matches(digest, digest)
    assert not preflight.digest_matches(digest[:16], digest)
    assert not preflight.digest_matches(digest[:-1] + "0", digest)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Session:
    trust_env = True
    max_redirects = 30

    def get(self, url, **_kwargs):
        if url.endswith("/api/version"):
            return _Response({"version": "test"})
        tag = "gpt-oss:20b"
        return _Response({"models": [{"name": tag,
                                      "digest": preflight.APPROVED_DIGESTS[tag]}]})


def test_preflight_charges_both_requests_and_keeps_full_digest() -> None:
    charged = []
    result = preflight.run_preflight(
        preflight.DEFAULT_ENDPOINT, ["gpt-oss:20b"], session=_Session(),
        charge=charged.append)
    assert result.ok
    assert charged == ["preflight_version", "preflight_tags"]
    assert result.resolved["gpt-oss:20b"] == \
        preflight.APPROVED_DIGESTS["gpt-oss:20b"]


def test_protocol_default_uses_independently_frozen_hash(tmp_path: Path) -> None:
    assert protocol.pin().sha256 == protocol.FROZEN_PROTOCOL_SHA256
    custom = tmp_path / "protocol.md"
    custom.write_text("frozen\n")
    expected = hashlib.sha256(custom.read_bytes()).hexdigest()
    assert protocol.pin(custom, expected_sha256=expected).sha256 == expected
    custom.write_text("changed\n")
    with pytest.raises(protocol.ProtocolMismatch):
        protocol.pin(custom, expected_sha256=expected)


def test_runner_uses_stable_template_sha256() -> None:
    source = Path(runner.__file__).read_text()
    assert "template_sha256" in source
    assert "str(hash(" not in source
