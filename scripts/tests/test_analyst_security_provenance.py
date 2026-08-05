"""Focused C0B-1 safety and provenance regression tests."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.analyst_benchmark import leakscan, preflight, protocol, report, runner


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
