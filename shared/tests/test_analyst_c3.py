"""C3 strict parser-supervisor and bubblewrap boundary contracts."""

from __future__ import annotations

import ast
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from experimental.analyst import sandbox
from experimental.analyst.sandbox import (
    RuntimeBind,
    SandboxLimits,
    build_argv,
    run_sandboxed,
    strict_preflight,
    system_runtime_binds,
)


def _open_source(tmp_path: Path, content: bytes = b"public synthetic source"):
    path = tmp_path / "source.bin"
    path.write_bytes(content)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    return path, fd, sandbox._inventory_for_fd(fd)


def _python(command: str) -> tuple[str, ...]:
    return (str(sandbox.SYSTEM_PYTHON), "-c", command)


def _limits(**overrides) -> SandboxLimits:
    values = {
        "address_space_bytes": 256 * 1024 * 1024,
        "cpu_seconds": 5,
        "open_files": 32,
        "tasks": 8,
        "wall_seconds": 5.0,
        "stdout_bytes": 4096,
        "stderr_bytes": 4096,
    }
    values.update(overrides)
    return SandboxLimits(**values)


def test_argv_pins_namespaces_environment_limits_and_fd(tmp_path: Path) -> None:
    _path, fd, _expected = _open_source(tmp_path)
    try:
        argv, unit = build_argv(
            source_fd=fd,
            command=("/runtime/parser", "--safe"),
            runtime_binds=(RuntimeBind(Path("/usr"), Path("/usr")),),
            limits=_limits(),
            unit_token="0123456789abcdef",
        )
    finally:
        os.close(fd)

    assert unit == "dirracuda-analyst-parser-0123456789abcdef.scope"
    assert argv[:6] == [
        "/usr/bin/systemd-run", "--user", "--scope", "--collect", "--quiet",
        f"--unit={unit}",
    ]
    assert "--property=TasksMax=8" in argv
    assert "--property=KillMode=control-group" in argv
    assert "--property=SendSIGKILL=yes" in argv
    for option in ("--unshare-net", "--unshare-pid", "--unshare-ipc",
                   "--unshare-uts", "--clearenv", "--new-session",
                   "--die-with-parent"):
        assert option in argv
    fd_index = argv.index("--ro-bind-fd")
    assert argv[fd_index + 1:fd_index + 3] == [str(fd), "/input/document"]
    assert argv[-2:] == ["/runtime/parser", "--safe"]
    assert "--as=268435456" in argv
    assert "--cpu=5" in argv
    assert "--nofile=32" in argv
    assert "--core=0" in argv


@pytest.mark.parametrize(
    ("source", "destination"),
    [
        (Path("/"), Path("/runtime")),
        (Path("/home"), Path("/runtime")),
        (Path.home(), Path("/runtime")),
        (sandbox.REPO_ROOT, Path("/runtime")),
        (Path("/usr"), Path("/")),
        (Path("/usr"), Path("/input/runtime")),
        (Path("/usr"), Path("/run/user/runtime")),
    ],
)
def test_unsafe_runtime_binds_are_rejected(
    tmp_path: Path, source: Path, destination: Path,
) -> None:
    _path, fd, _expected = _open_source(tmp_path)
    try:
        with pytest.raises(ValueError):
            build_argv(
                source_fd=fd,
                command=("/runtime/parser",),
                runtime_binds=(RuntimeBind(source, destination),),
                limits=_limits(),
                unit_token="0123456789abcdef",
            )
    finally:
        os.close(fd)


def test_source_parent_cannot_be_exposed_as_runtime(tmp_path: Path) -> None:
    source_dir = tmp_path / "outside-home-source"
    source_dir.mkdir()
    path = source_dir / "document.bin"
    path.write_bytes(b"public")
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        with pytest.raises(ValueError, match="protected tree"):
            build_argv(
                source_fd=fd,
                command=("/runtime/parser",),
                runtime_binds=(RuntimeBind(source_dir, Path("/runtime")),),
                limits=_limits(),
                unit_token="0123456789abcdef",
            )
    finally:
        os.close(fd)


@pytest.mark.parametrize("executable", ["relative", "/input/document", "/tmp/x"])
def test_unsafe_executable_is_rejected(tmp_path: Path, executable: str) -> None:
    _path, fd, _expected = _open_source(tmp_path)
    try:
        with pytest.raises(ValueError):
            build_argv(
                source_fd=fd,
                command=(executable,),
                runtime_binds=(),
                limits=_limits(),
                unit_token="0123456789abcdef",
            )
    finally:
        os.close(fd)


def test_live_preflight_proves_strict_boundary() -> None:
    capability = strict_preflight()
    assert capability.reason == "success", capability
    assert capability.ok
    assert dict(capability.checks) == {
        "input_fd_bound": True,
        "network_unreachable": True,
        "host_home_absent": True,
        "repository_absent": True,
        "task_limit_enforced": True,
    }


def test_live_source_fd_is_only_visible_at_fixed_mount(tmp_path: Path) -> None:
    _path, fd, expected = _open_source(tmp_path, b"known public bytes")
    try:
        result = run_sandboxed(
            source_fd=fd,
            expected=expected,
            command=_python(
                'import os; print(open("/input/document", "rb").read().decode()); '
                'print(os.path.exists("/home")); print(os.path.exists("/workspace"))'
            ),
            runtime_binds=system_runtime_binds(),
            limits=_limits(),
        )
    finally:
        os.close(fd)
    assert result.reason == "success", result.stderr
    assert result.stdout == b"known public bytes\nFalse\nFalse\n"


@pytest.mark.parametrize(
    ("stream", "code"),
    [
        ("stdout", 'import sys; sys.stdout.write("x" * 4096); sys.stdout.flush()'),
        ("stderr", 'import sys; sys.stderr.write("x" * 4096); sys.stderr.flush()'),
    ],
)
def test_output_caps_are_exact_and_kill_the_unit(
    tmp_path: Path, stream: str, code: str,
) -> None:
    _path, fd, expected = _open_source(tmp_path)
    try:
        result = run_sandboxed(
            source_fd=fd,
            expected=expected,
            command=_python(code),
            runtime_binds=system_runtime_binds(),
            limits=_limits(stdout_bytes=128, stderr_bytes=128),
        )
    finally:
        os.close(fd)
    assert result.reason == "parser_output_limit"
    assert result.stdout == b""
    assert result.stderr == b""
    _assert_unit_inactive(result.unit_name)


def test_wall_timeout_kills_and_reaps_unit(tmp_path: Path) -> None:
    _path, fd, expected = _open_source(tmp_path)
    try:
        result = run_sandboxed(
            source_fd=fd,
            expected=expected,
            command=_python("import time; time.sleep(30)"),
            runtime_binds=system_runtime_binds(),
            limits=_limits(wall_seconds=0.2),
        )
    finally:
        os.close(fd)
    assert result.reason == "parse_timeout"
    _assert_unit_inactive(result.unit_name)


def test_cancellation_kills_and_reaps_unit(tmp_path: Path) -> None:
    _path, fd, expected = _open_source(tmp_path)
    started = time.monotonic()
    try:
        result = run_sandboxed(
            source_fd=fd,
            expected=expected,
            command=_python("import time; time.sleep(30)"),
            runtime_binds=system_runtime_binds(),
            limits=_limits(),
            cancel_check=lambda: time.monotonic() - started > 0.2,
        )
    finally:
        os.close(fd)
    assert result.reason == "cancelled"
    _assert_unit_inactive(result.unit_name)


def test_cancellation_during_preflight_hash_never_launches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _path, fd, expected = _open_source(
        tmp_path, b"x" * (sandbox.READ_SIZE * 2)
    )
    checks = 0

    def cancel() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    monkeypatch.setattr(
        sandbox.subprocess, "Popen",
        lambda *args, **kwargs: pytest.fail("parser launched after cancellation"),
    )
    try:
        result = run_sandboxed(
            source_fd=fd,
            expected=expected,
            command=_python("pass"),
            runtime_binds=system_runtime_binds(),
            cancel_check=cancel,
        )
    finally:
        os.close(fd)
    assert result.reason == "cancelled"


def test_broken_cancel_callback_fails_closed_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _path, fd, expected = _open_source(tmp_path)

    def broken() -> bool:
        raise RuntimeError("callback failure")

    monkeypatch.setattr(
        sandbox.subprocess, "Popen",
        lambda *args, **kwargs: pytest.fail("parser launched after callback failure"),
    )
    try:
        result = run_sandboxed(
            source_fd=fd,
            expected=expected,
            command=_python("pass"),
            runtime_binds=system_runtime_binds(),
            cancel_check=broken,
        )
    finally:
        os.close(fd)
    assert result.reason == "cancelled"


@pytest.mark.parametrize("wall_seconds", [float("nan"), float("inf")])
def test_nonfinite_wall_limit_is_rejected(wall_seconds: float) -> None:
    with pytest.raises(ValueError, match="wall_seconds"):
        SandboxLimits(wall_seconds=wall_seconds)


def test_source_mutation_discards_parser_output(tmp_path: Path) -> None:
    path, fd, expected = _open_source(tmp_path, b"before")

    def mutate() -> None:
        time.sleep(0.15)
        path.write_bytes(b"after, with a new length")

    thread = threading.Thread(target=mutate)
    thread.start()
    try:
        result = run_sandboxed(
            source_fd=fd,
            expected=expected,
            command=_python('import time; print("sensitive"); time.sleep(.4)'),
            runtime_binds=system_runtime_binds(),
            limits=_limits(),
        )
    finally:
        thread.join()
        os.close(fd)
    assert result.reason == "source_changed_since_inventory"
    assert result.stdout == b""
    assert result.stderr == b""


def test_address_space_limit_prevents_large_allocation(tmp_path: Path) -> None:
    _path, fd, expected = _open_source(tmp_path)
    code = ('try:\n x=bytearray(512 * 1024 * 1024)\n print("ALLOCATED")\n'
            'except MemoryError:\n print("MEMORY_LIMIT")')
    try:
        result = run_sandboxed(
            source_fd=fd,
            expected=expected,
            command=_python(code),
            runtime_binds=system_runtime_binds(),
            limits=_limits(address_space_bytes=128 * 1024 * 1024),
        )
    finally:
        os.close(fd)
    assert b"ALLOCATED" not in result.stdout
    assert result.reason in {"success", "parse_signal", "parse_error"}
    if result.reason == "success":
        assert result.stdout == b"MEMORY_LIMIT\n"


def test_source_identity_includes_device_and_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _path, fd, expected = _open_source(tmp_path)
    changed = expected.__class__(
        relative_path=expected.relative_path,
        size=expected.size,
        mtime_ns=expected.mtime_ns,
        ctime_ns=expected.ctime_ns,
        device=expected.device,
        inode=expected.inode + 1,
        mode=expected.mode,
        sha256=expected.sha256,
    )
    monkeypatch.setattr(
        sandbox.subprocess, "Popen",
        lambda *args, **kwargs: pytest.fail("mismatched source was launched"),
    )
    try:
        result = run_sandboxed(
            source_fd=fd,
            expected=changed,
            command=_python("pass"),
            runtime_binds=system_runtime_binds(),
        )
    finally:
        os.close(fd)
    assert result.reason == "source_changed_since_inventory"


def test_missing_cgroup_pid_controller_fails_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _path, fd, expected = _open_source(tmp_path)
    controllers = tmp_path / "controllers"
    controllers.write_text("cpu memory io\n", encoding="ascii")
    monkeypatch.setattr(sandbox, "CGROUP_CONTROLLERS", controllers)
    monkeypatch.setattr(
        sandbox.subprocess, "Popen",
        lambda *args, **kwargs: pytest.fail("parser launched without pids controller"),
    )
    try:
        result = run_sandboxed(
            source_fd=fd,
            expected=expected,
            command=_python("pass"),
            runtime_binds=system_runtime_binds(),
        )
    finally:
        os.close(fd)
    assert result.reason == "sandbox_unavailable"


def test_supervisor_does_not_import_document_parsers() -> None:
    tree = ast.parse(Path(sandbox.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert imported.isdisjoint({"fitz", "pymupdf", "striprtf", "xlrd", "docx"})


def test_launcher_forbids_shell_and_preexec_hooks() -> None:
    tree = ast.parse(Path(sandbox.__file__).read_text(encoding="utf-8"))
    popen_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Popen"
    ]
    assert len(popen_calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in popen_calls[0].keywords}
    assert isinstance(keywords["shell"], ast.Constant)
    assert keywords["shell"].value is False
    assert "preexec_fn" not in keywords
    assert "pass_fds" in keywords


def _assert_unit_inactive(unit: str | None) -> None:
    assert unit is not None
    check = subprocess.run(
        [str(sandbox.SYSTEMCTL), "--user", "is-active", "--quiet", unit],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=5,
    )
    assert check.returncode != 0
