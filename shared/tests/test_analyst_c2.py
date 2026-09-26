"""C2 safe inventory and worker reattachment contracts."""

from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path

import pytest

from experimental.analyst import inventory
from experimental.analyst.inventory import (
    InventoryCancelled,
    InventoryChangedError,
    InventoryLimitError,
    InventoryLimits,
    InventoryRootError,
    inventory_tree,
)
from experimental.analyst.process_identity import (
    LeaseEvidence,
    ProcessIdentity,
    ProcessIdentityUnavailable,
    ReattachDecision,
    current_process_identity,
    decide_reattachment,
    parse_start_ticks,
    read_process_identity,
)

BOOT_ID = "12345678-1234-4567-89ab-123456789abc"


def _write_proc(root: Path, pid: int, *, start_ticks: int,
                comm: str = "analyst worker", boot_id: str = BOOT_ID) -> None:
    process = root / str(pid)
    process.mkdir(parents=True)
    fields = ["S", *("0" for _ in range(18)), str(start_ticks)]
    (process / "stat").write_text(
        f"{pid} ({comm}) {' '.join(fields)}\n", encoding="ascii"
    )
    boot = root / "sys" / "kernel" / "random"
    boot.mkdir(parents=True, exist_ok=True)
    (boot / "boot_id").write_text(boot_id + "\n", encoding="ascii")


def _lease(*, pid: int = 42, start_ticks: int = 900,
           boot_id: str = BOOT_ID, heartbeat: int = 1_000) -> LeaseEvidence:
    return LeaseEvidence(
        run_id="run-1",
        owner_token="owner-1",
        process=ProcessIdentity(pid, start_ticks, boot_id),
        heartbeat_monotonic_ns=heartbeat,
    )


def test_inventory_is_deterministic_and_hashes_exact_file_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    (root / "nested").mkdir(parents=True)
    (root / "z.txt").write_bytes(b"zulu")
    (root / "nested" / "a.txt").write_bytes(b"alpha")

    first = inventory_tree(root)
    second = inventory_tree(root)
    assert [item.relative_path for item in first.files] == [
        "nested/a.txt", "z.txt"
    ]
    assert first == second
    assert first.total_bytes == 9
    assert first.files[0].sha256 == hashlib.sha256(b"alpha").hexdigest()
    assert first.files[1].sha256 == hashlib.sha256(b"zulu").hexdigest()
    assert not first.exclusions


def test_inventory_includes_empty_regular_files(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "empty.txt").touch()
    result = inventory_tree(root)
    assert len(result.files) == 1
    assert result.files[0].size == 0
    assert result.files[0].sha256 == hashlib.sha256(b"").hexdigest()


def test_symlinks_special_files_and_analyst_outputs_are_never_followed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (root / "file-link").symlink_to(outside)
    real_dir = root / "real-dir"
    real_dir.mkdir()
    (real_dir / "inside.txt").write_text("inside", encoding="utf-8")
    (root / "dir-link").symlink_to(real_dir, target_is_directory=True)
    os.mkfifo(root / "pipe")
    output = root / "_analyst"
    output.mkdir()
    (output / "report.jsonl").write_text("never inventory me", encoding="utf-8")

    result = inventory_tree(root)
    assert [item.relative_path for item in result.files] == ["real-dir/inside.txt"]
    assert {(item.relative_path, item.reason) for item in result.exclusions} == {
        ("_analyst", "analyst_output"),
        ("dir-link", "symlink"),
        ("file-link", "symlink"),
        ("pipe", "special_file"),
    }


def test_symlinked_root_or_intermediate_component_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "child").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(InventoryRootError):
        inventory_tree(link)
    with pytest.raises(InventoryRootError):
        inventory_tree(link / "child")


def test_relative_or_parent_traversal_root_is_rejected() -> None:
    with pytest.raises(InventoryRootError):
        inventory_tree(Path("relative"))
    with pytest.raises(InventoryRootError):
        inventory_tree(Path("/tmp/../tmp"))


def test_each_hardlink_name_is_hashed_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    first = root / "first.txt"
    first.write_text("same bytes", encoding="utf-8")
    os.link(first, root / "second.txt")
    calls = 0
    original = inventory._hash_fd

    def counted(fd: int, cancel_check=None) -> str:
        nonlocal calls
        calls += 1
        return original(fd, cancel_check)

    monkeypatch.setattr(inventory, "_hash_fd", counted)
    result = inventory_tree(root)
    assert len(result.files) == 2
    assert result.files[0].inode == result.files[1].inode
    assert calls == 2


def test_in_place_mutation_is_excluded(tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "source"
    root.mkdir()
    target = root / "changing.txt"
    target.write_text("before", encoding="utf-8")
    original = inventory._hash_fd

    def mutate(fd: int, cancel_check=None) -> str:
        digest = original(fd, cancel_check)
        target.write_text("after with different size", encoding="utf-8")
        return digest

    monkeypatch.setattr(inventory, "_hash_fd", mutate)
    result = inventory_tree(root)
    assert not result.files
    assert [(item.relative_path, item.reason) for item in result.exclusions] == [
        ("changing.txt", "changed_during_inventory")
    ]


def test_directory_or_name_swap_returns_no_partial_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    target = root / "target.txt"
    target.write_text("first", encoding="utf-8")
    replacement = tmp_path / "replacement.txt"
    replacement.write_text("second", encoding="utf-8")
    original = inventory._hash_fd

    def replace_name(fd: int, cancel_check=None) -> str:
        digest = original(fd, cancel_check)
        os.replace(replacement, target)
        return digest

    monkeypatch.setattr(inventory, "_hash_fd", replace_name)
    with pytest.raises(InventoryChangedError):
        inventory_tree(root)


def test_nested_mount_identity_is_excluded_before_file_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    target = root / "mounted.txt"
    target.write_text("do not hash", encoding="utf-8")
    root_inode = root.stat().st_ino
    hash_calls = 0

    def fake_mount_id(fd: int) -> int:
        return 10 if os.fstat(fd).st_ino == root_inode else 11

    def forbidden_hash(fd: int, cancel_check=None) -> str:
        nonlocal hash_calls
        hash_calls += 1
        return ""

    monkeypatch.setattr(inventory, "_mount_id", fake_mount_id)
    monkeypatch.setattr(inventory, "_hash_fd", forbidden_hash)
    result = inventory_tree(root)
    assert not result.files
    assert result.exclusions[0].reason == "mount_boundary"
    assert hash_calls == 0


def test_inventory_limits_fail_without_a_partial_result(tmp_path: Path) -> None:
    root = tmp_path / "source"
    (root / "one" / "two").mkdir(parents=True)
    (root / "a").write_text("a", encoding="utf-8")
    (root / "b").write_text("b", encoding="utf-8")
    with pytest.raises(InventoryLimitError, match="entry"):
        inventory_tree(root, limits=InventoryLimits(max_entries=1))
    with pytest.raises(InventoryLimitError, match="depth"):
        inventory_tree(root, limits=InventoryLimits(max_depth=0))


def test_inventory_closes_descriptors_on_failure(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    for index in range(3):
        (root / f"{index}.txt").write_text(str(index), encoding="utf-8")
    before = len(list(Path("/proc/self/fd").iterdir()))
    with pytest.raises(InventoryLimitError):
        inventory_tree(root, limits=InventoryLimits(max_entries=1))
    after = len(list(Path("/proc/self/fd").iterdir()))
    assert after == before


def test_inventory_cancellation_interrupts_hashing_and_closes_descriptors(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "large.bin").write_bytes(b"x" * (inventory.HASH_READ_SIZE * 2))
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    before = len(list(Path("/proc/self/fd").iterdir()))
    with pytest.raises(InventoryCancelled):
        inventory_tree(root, cancel_check=cancelled)
    after = len(list(Path("/proc/self/fd").iterdir()))
    assert after == before


def test_proc_parser_handles_spaces_and_closing_parentheses() -> None:
    fields = ["S", *("0" for _ in range(18)), "987654"]
    body = f"77 (worker ) with spaces) {' '.join(fields)}\n".encode("ascii")
    assert parse_start_ticks(body, expected_pid=77) == 987654


@pytest.mark.parametrize("body", [
    b"77 no-parentheses S 0",
    b"78 (worker) S 0",
    b"77 (worker) S 0",
    b"77 (worker) S " + b"0 " * 18 + b"not-a-number",
])
def test_proc_parser_rejects_malformed_records(body: bytes) -> None:
    with pytest.raises((ValueError, UnicodeError)):
        parse_start_ticks(body, expected_pid=77)


def test_process_identity_reads_start_ticks_and_boot_id(tmp_path: Path) -> None:
    _write_proc(tmp_path, 77, start_ticks=987654, comm="worker ) name")
    assert read_process_identity(77, proc_root=tmp_path) == ProcessIdentity(
        pid=77, start_ticks=987654, boot_id=BOOT_ID
    )
    assert read_process_identity(88, proc_root=tmp_path) is None


def test_malformed_or_unreadable_process_evidence_is_not_treated_as_dead(
    tmp_path: Path,
) -> None:
    _write_proc(tmp_path, 77, start_ticks=1)
    (tmp_path / "77" / "stat").write_bytes(b"malformed")
    with pytest.raises(ProcessIdentityUnavailable):
        read_process_identity(77, proc_root=tmp_path)


def test_oversized_or_symlinked_proc_evidence_is_unverifiable(
    tmp_path: Path,
) -> None:
    _write_proc(tmp_path, 77, start_ticks=1)
    (tmp_path / "77" / "stat").write_bytes(b"x" * (16 * 1024 + 1))
    with pytest.raises(ProcessIdentityUnavailable):
        read_process_identity(77, proc_root=tmp_path)

    (tmp_path / "77" / "stat").unlink()
    fields = ["S", *("0" for _ in range(18)), "1"]
    (tmp_path / "77" / "stat").write_text(
        f"77 (worker) {' '.join(fields)}", encoding="ascii"
    )
    boot = tmp_path / "sys" / "kernel" / "random" / "boot_id"
    boot.unlink()
    boot.symlink_to("/etc/machine-id")
    with pytest.raises(ProcessIdentityUnavailable):
        read_process_identity(77, proc_root=tmp_path)


def test_current_process_identity_matches_live_proc() -> None:
    identity = current_process_identity()
    assert identity.pid == os.getpid()
    assert identity.start_ticks > 0
    assert read_process_identity(os.getpid()) == identity


def test_reattach_requires_exact_live_identity_and_fresh_heartbeat() -> None:
    lease = _lease(heartbeat=1_000)
    reader = lambda pid: lease.process
    assert decide_reattachment(
        lease,
        max_heartbeat_age_ns=100,
        now_monotonic_ns=1_100,
        identity_reader=reader,
    ) is ReattachDecision.REATTACH
    assert decide_reattachment(
        lease,
        max_heartbeat_age_ns=100,
        now_monotonic_ns=1_101,
        identity_reader=reader,
    ) is ReattachDecision.BLOCK_STALE_LIVE


@pytest.mark.parametrize("observed", [
    None,
    ProcessIdentity(42, 901, BOOT_ID),
    ProcessIdentity(42, 900, "87654321-4321-4567-89ab-cba987654321"),
])
def test_dead_pid_reuse_or_reboot_clears_stale_lease(observed) -> None:
    lease = _lease()
    assert decide_reattachment(
        lease,
        max_heartbeat_age_ns=100,
        now_monotonic_ns=1_000,
        identity_reader=lambda pid: observed,
    ) is ReattachDecision.CLEAR_STALE


def test_future_heartbeat_blocks_while_exact_worker_is_live() -> None:
    lease = _lease(heartbeat=2_001)
    assert decide_reattachment(
        lease,
        max_heartbeat_age_ns=100,
        now_monotonic_ns=1_000,
        future_tolerance_ns=1_000,
        identity_reader=lambda pid: lease.process,
    ) is ReattachDecision.BLOCK_INVALID_HEARTBEAT


def test_unverifiable_live_state_never_clears_the_lease() -> None:
    lease = _lease()

    def unavailable(pid: int):
        raise ProcessIdentityUnavailable("hidden proc")

    assert decide_reattachment(
        lease,
        max_heartbeat_age_ns=100,
        now_monotonic_ns=1_000,
        identity_reader=unavailable,
    ) is ReattachDecision.BLOCK_UNVERIFIABLE


def test_identity_and_lease_models_reject_coercion() -> None:
    with pytest.raises(ValueError):
        ProcessIdentity(True, 1, BOOT_ID)
    with pytest.raises(ValueError):
        ProcessIdentity(1, -1, BOOT_ID)
    with pytest.raises(ValueError):
        ProcessIdentity(1, 1, BOOT_ID.upper())
    with pytest.raises(ValueError):
        LeaseEvidence("", "owner", ProcessIdentity(1, 1, BOOT_ID), 0)
    with pytest.raises(ValueError):
        decide_reattachment(
            _lease(), max_heartbeat_age_ns=True, now_monotonic_ns=1_000
        )


def test_c2_modules_import_no_database_network_subprocess_or_parser() -> None:
    package = Path(__file__).resolve().parents[2] / "experimental" / "analyst"
    banned = {
        "sqlite3", "subprocess", "socket", "urllib", "requests", "httpx",
        "fitz", "pymupdf", "docx", "openpyxl", "xlrd",
    }
    offenders: list[str] = []
    for name in ("inventory.py", "process_identity.py"):
        path = package / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                if module.split(".")[0] in banned:
                    offenders.append(f"{name}:{node.lineno} imports {module}")
    assert not offenders, offenders
