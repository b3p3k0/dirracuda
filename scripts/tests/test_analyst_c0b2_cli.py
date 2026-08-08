"""C0B-2A confirmation ordering and exact worktree-boundary tests."""
from __future__ import annotations

import builtins
import getpass
import itertools
import os
import socket
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from scripts.analyst_benchmark import __main__ as entrypoint
from scripts.analyst_benchmark import c0b2_cli, c0b2_leakscan

PRIVATE_COMMANDS = ("create-private", "run-private", "resume-private")
ACK_FLAGS = (
    "--confirm-live",
    "--confirm-private-corpus",
    "--confirm-private-authority",
    "--confirm-trusted-local-boundary",
)
ROOT_MODES = (
    (),
    ("--private-root-prompt",),
    ("--private-root-fd", "9"),
    ("--private-root-prompt", "--private-root-fd", "9"),
)


def _private_args(command: str, acknowledgements: tuple[bool, ...],
                  root_mode: tuple[str, ...]) -> list[str]:
    identity_flag = "--parent-run" if command == "create-private" else "--run-id"
    args = [command, identity_flag, "c0b2-test-run"]
    args.extend(flag for flag, enabled in zip(ACK_FLAGS, acknowledgements) if enabled)
    args.extend(root_mode)
    return args


@contextmanager
def _deny_side_effects(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def denied(*_args, **_kwargs):
        raise AssertionError("confirmation refusal crossed a side-effect boundary")

    import shared.path_service as path_service

    real_import = builtins.__import__
    real_open = builtins.open
    real_os_open = os.open

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {
            "shared.path_service",
            "scripts.analyst_benchmark.client",
            "scripts.analyst_benchmark.report",
            "scripts.analyst_benchmark.c0b2_checkpoint",
            "scripts.analyst_benchmark.c0b2_runtime_f",
        } or set(fromlist or ()) & {
            "path_service", "client", "report", "c0b2_checkpoint",
            "c0b2_runtime_f",
        }:
            raise AssertionError(f"gated command imported side-effect module {name}")
        return real_import(name, globals, locals, fromlist, level)

    def guarded_open(*args, **kwargs):
        mode = kwargs.get("mode", args[1] if len(args) > 1 else "r")
        if any(flag in mode for flag in "wax+"):
            denied()
        return real_open(*args, **kwargs)

    def guarded_os_open(path, flags, *args, **kwargs):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        if flags & write_flags:
            denied()
        return real_os_open(path, flags, *args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(path_service, "get_paths", denied)
        guard.setattr(builtins, "__import__", guarded_import)
        guard.setattr(builtins, "input", denied)
        guard.setattr(builtins, "open", guarded_open)
        guard.setattr(getpass, "getpass", denied)
        guard.setattr(os, "open", guarded_os_open)
        guard.setattr(os, "fdopen", denied)
        guard.setattr(os, "fstat", denied)
        guard.setattr(os, "read", denied)
        guard.setattr(os, "close", denied)
        guard.setattr(os, "mkdir", denied)
        guard.setattr(socket, "socket", denied)
        guard.setattr(socket, "create_connection", denied)
        guard.setattr(Path, "write_text", denied)
        guard.setattr(Path, "mkdir", denied)
        yield


@pytest.mark.parametrize("command", PRIVATE_COMMANDS)
@pytest.mark.parametrize("acks", tuple(itertools.product((False, True), repeat=4)))
@pytest.mark.parametrize("root_mode", ROOT_MODES)
def test_every_incomplete_private_gate_combination_has_zero_side_effects(
        command: str, acks: tuple[bool, ...], root_mode: tuple[str, ...],
        monkeypatch: pytest.MonkeyPatch) -> None:
    complete = all(acks) and root_mode in ROOT_MODES[1:3]
    if complete:
        pytest.skip("complete combinations have a separate held-path test")
    with _deny_side_effects(monkeypatch):
        try:
            result = c0b2_cli.main(_private_args(command, acks, root_mode))
        except SystemExit as exc:
            result = exc.code
    assert result == c0b2_cli.EXIT_USAGE


@pytest.mark.parametrize("command", PRIVATE_COMMANDS)
@pytest.mark.parametrize("root_mode", ROOT_MODES[1:3])
def test_complete_private_gate_is_still_held_before_root_or_prompt_access(
        command: str, root_mode: tuple[str, ...],
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    with _deny_side_effects(monkeypatch):
        result = c0b2_cli.main(_private_args(command, (True,) * 4, root_mode))

    assert result == c0b2_cli.EXIT_HELD
    assert "C0B-2 LIVE HELD" in capsys.readouterr().err


@pytest.mark.parametrize("command", ("run", "resume"))
def test_public_live_commands_require_confirmation_then_delegate_stage_c(
        command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    args = [command, "--run-id", "c0b2-public", "--stage", "C"]
    with _deny_side_effects(monkeypatch):
        assert c0b2_cli.main(args) == c0b2_cli.EXIT_USAGE

    from scripts.analyst_benchmark import c0b2_runtime
    calls = []
    monkeypatch.setattr(
        c0b2_runtime, "run_public_stage_c",
        lambda run_id, *, resume: calls.append((run_id, resume)) or {"state": "RUNNING"},
        raising=False,
    )
    assert c0b2_cli.main([*args, "--confirm-live"]) == 0
    assert calls == [("c0b2-public", command == "resume")]


@pytest.mark.parametrize("command", ("run", "resume"))
def test_public_stage_d_requires_confirmation_then_delegates(
        command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    args = [command, "--run-id", "c0b2-public", "--stage", "D"]
    with _deny_side_effects(monkeypatch):
        assert c0b2_cli.main(args) == c0b2_cli.EXIT_USAGE

    from scripts.analyst_benchmark import c0b2_runtime_d
    calls = []
    monkeypatch.setattr(
        c0b2_runtime_d, "run_public_stage_d",
        lambda run_id, *, resume: calls.append((run_id, resume)) or {
            "state": "RUNNING"},
    )
    assert c0b2_cli.main([*args, "--confirm-live"]) == 0
    assert calls == [("c0b2-public", command == "resume")]


def test_public_offline_commands_delegate_without_live_transport(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.analyst_benchmark import c0b2_runtime

    monkeypatch.setattr(c0b2_runtime, "create_public_run", lambda: "c0b2-created")
    monkeypatch.setattr(c0b2_runtime, "public_status", lambda run_id: {
        "state": "PREPARED", "run_id": run_id})
    monkeypatch.setattr(c0b2_runtime, "public_verify", lambda run_id: {
        "ok": True, "errors": [], "run_id": run_id})

    assert c0b2_cli.main(["create"]) == 0
    assert c0b2_cli.main(["status", "--run-id", "c0b2-public"]) == 0
    assert c0b2_cli.main(["verify", "--run-id", "c0b2-public"]) == 0
    output = capsys.readouterr().out
    assert "c0b2-created" in output
    assert "PREPARED" in output


def test_cli_generic_failure_is_stable_and_content_free(
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.analyst_benchmark import c0b2_runtime

    secret = "/opt/dirracuda-private/customer-record.txt"

    class SentinelSecretFailure(RuntimeError):
        pass

    monkeypatch.setattr(
        c0b2_runtime, "public_status",
        lambda _run_id: (_ for _ in ()).throw(SentinelSecretFailure(secret)))
    assert c0b2_cli.main(["status", "--run-id", "c0b2-public"]) == \
        c0b2_cli.EXIT_BLOCKED
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "C0B-2 BLOCKED: operation_failed\n"
    assert secret not in captured.err
    assert "SentinelSecretFailure" not in captured.err


def test_cli_runtime_import_failure_is_stable_and_content_free(
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    real_import = builtins.__import__
    secret = "/opt/dirracuda-private/customer-record.txt"

    class SentinelSecretImportFailure(RuntimeError):
        pass

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 1 and "c0b2_runtime" in tuple(fromlist or ()):
            raise SentinelSecretImportFailure(secret)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert c0b2_cli.main(["status", "--run-id", "c0b2-public"]) == \
        c0b2_cli.EXIT_BLOCKED
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "C0B-2 BLOCKED: operation_failed\n"
    assert secret not in captured.err
    assert "SentinelSecretImportFailure" not in captured.err


@pytest.mark.parametrize("command", ("run", "resume"))
def test_public_stage_f_requires_confirmation_then_delegates(
        command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    args = [command, "--run-id", "c0b2-public", "--stage", "F"]
    with _deny_side_effects(monkeypatch):
        assert c0b2_cli.main(args) == c0b2_cli.EXIT_USAGE

    from scripts.analyst_benchmark import c0b2_runtime_f
    calls = []
    monkeypatch.setattr(
        c0b2_runtime_f, "run_public_stage_f",
        lambda run_id, *, resume: calls.append((run_id, resume)) or {
            "state": "RUNNING"},
        raising=False,
    )
    assert c0b2_cli.main([*args, "--confirm-live"]) == 0
    assert calls == [("c0b2-public", command == "resume")]


def test_abandon_requires_confirmation_then_delegates_without_transport(
        monkeypatch: pytest.MonkeyPatch) -> None:
    args = ["abandon", "--run-id", "c0b2-public"]
    with _deny_side_effects(monkeypatch):
        assert c0b2_cli.main(args) == c0b2_cli.EXIT_USAGE

    from scripts.analyst_benchmark import c0b2_runtime_common
    calls = []
    monkeypatch.setattr(
        c0b2_runtime_common, "abandon_public_run",
        lambda run_id: calls.append(run_id) or {"state": "ABANDONED"})
    assert c0b2_cli.main([*args, "--confirm-abandon"]) == 0
    assert calls == ["c0b2-public"]


@pytest.mark.parametrize("bad_id", ("../escape", "/absolute", "has space", ""))
def test_run_ids_are_opaque_not_paths(bad_id: str) -> None:
    with pytest.raises(SystemExit) as exc:
        c0b2_cli.main(["status", "--run-id", bad_id])
    assert exc.value.code == c0b2_cli.EXIT_USAGE


def test_entrypoint_preserves_c0b1_arguments_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.analyst_benchmark import runner

    seen: list[list[str]] = []
    monkeypatch.setattr(runner, "main", lambda argv: seen.append(argv) or 17)
    original = ["--stage", "A", "--confirm-dependency-probe"]

    assert entrypoint.main(original) == 17
    assert seen == [original]


def test_entrypoint_routes_only_the_c0b2_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(c0b2_cli, "main", lambda argv: seen.append(argv) or 19)

    assert entrypoint.main(["c0b2", "create"]) == 19
    assert seen == [["create"]]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   shell=False)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "base")
    return repo


def _write(repo: Path, relative: str, text: str) -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_seal_ignores_unchanged_preexisting_keyboard_tree(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    keyboard = _write(repo, "docs/dev/kbd_ctrl_improve/notes.md", "user work\n")
    before = c0b2_leakscan.capture_worktree_seal(repo)

    task = _write(repo, "scripts/analyst_benchmark/c0b2_cli.py", "task work\n")
    after = c0b2_leakscan.capture_worktree_seal(repo)

    assert c0b2_leakscan.assert_frozen_task_delta(before, after) == (
        "scripts/analyst_benchmark/c0b2_cli.py",
    )
    assert keyboard.read_text() == "user work\n"
    assert task.read_text() == "task work\n"

    keyboard.write_text("user work changed\n")
    changed = c0b2_leakscan.capture_worktree_seal(repo)
    with pytest.raises(c0b2_leakscan.LeakGateError, match="kbd_ctrl_improve"):
        c0b2_leakscan.assert_frozen_task_delta(before, changed)


def test_allowlist_is_exact_not_a_c0b2_prefix(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    before = c0b2_leakscan.capture_worktree_seal(repo)
    _write(repo, "scripts/analyst_benchmark/c0b2_surprise.py", "not frozen\n")
    after = c0b2_leakscan.capture_worktree_seal(repo)

    with pytest.raises(c0b2_leakscan.LeakGateError, match="c0b2_surprise"):
        c0b2_leakscan.assert_frozen_task_delta(before, after)


def test_allowed_task_symlink_is_never_followed_or_accepted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    before = c0b2_leakscan.capture_worktree_seal(repo)
    target = _write(repo, "outside.txt", "do not read through link\n")
    link = repo / "scripts/analyst_benchmark/c0b2_cli.py"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    after = c0b2_leakscan.capture_worktree_seal(repo)

    with pytest.raises(c0b2_leakscan.LeakGateError, match="regular files"):
        c0b2_leakscan.assert_frozen_task_delta(
            before, after,
            allowed_paths={"scripts/analyst_benchmark/c0b2_cli.py", "outside.txt"},
        )
