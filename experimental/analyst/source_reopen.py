"""Descriptor-safe reopening of one immutable Analyst inventory entry."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final

from .inventory import InventoryFile, InventoryResult


HASH_READ_SIZE: Final = 1024 * 1024
FDINFO_MAX_BYTES: Final = 16 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)

CancelCheck = Callable[[], bool]


class SourceReopenError(RuntimeError):
    """A source cannot be reopened under its frozen inventory identity."""


class SourceReopenCancelled(SourceReopenError):
    """The operator cancelled while a source was being revalidated."""


@dataclass(frozen=True, slots=True)
class SourceRootIdentity:
    """The mount-aware root identity recorded by the original inventory."""

    device: int
    inode: int
    mount_id: int

    def __post_init__(self) -> None:
        if (
            type(self.device) is not int
            or self.device < 0
            or type(self.inode) is not int
            or self.inode <= 0
            or type(self.mount_id) is not int
            or self.mount_id <= 0
        ):
            raise ValueError("source root identity is invalid")

    @classmethod
    def from_inventory(cls, inventory: InventoryResult) -> SourceRootIdentity:
        if type(inventory) is not InventoryResult:
            raise TypeError("root identity requires an InventoryResult")
        return cls(
            device=inventory.root_device,
            inode=inventory.root_inode,
            mount_id=inventory.root_mount_id,
        )


class OpenedInventoryFile:
    """One verified source descriptor whose lifetime is explicitly owned."""

    __slots__ = ("_fd",)

    def __init__(self, fd: int) -> None:
        if type(fd) is not int or fd < 0:
            raise ValueError("source descriptor must be a nonnegative integer")
        self._fd: int | None = fd

    def fileno(self) -> int:
        if self._fd is None:
            raise SourceReopenError("source descriptor is closed")
        return self._fd

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    def __enter__(self) -> OpenedInventoryFile:
        self.fileno()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


def open_inventory_file(
    root: Path,
    root_identity: SourceRootIdentity,
    expected: InventoryFile,
    *,
    cancel_check: CancelCheck | None = None,
) -> OpenedInventoryFile:
    """Reopen and hash one inventory entry without following any path component.

    The returned object owns the leaf descriptor. Callers should use it as a
    context manager and pass ``source.fileno()`` to the sandbox supervisor.
    """
    root_parts = _root_components(root)
    relative_parts = _relative_components(expected)
    if type(root_identity) is not SourceRootIdentity:
        raise TypeError("root_identity must be a SourceRootIdentity")
    if cancel_check is not None and not callable(cancel_check):
        raise TypeError("cancel_check must be callable")

    opened: list[int] = []
    bindings: list[tuple[int, str, int, bool]] = []
    leaf_fd: int | None = None
    try:
        _check_cancel(cancel_check)
        current = _open_directory(os.sep)
        opened.append(current)
        for name in root_parts:
            _check_cancel(cancel_check)
            child = _open_directory(name, dir_fd=current)
            opened.append(child)
            bindings.append((current, name, child, True))
            current = child
        _require_root_identity(current, root_identity)

        for name in relative_parts[:-1]:
            _check_cancel(cancel_check)
            child = _open_directory(name, dir_fd=current)
            opened.append(child)
            bindings.append((current, name, child, True))
            if _mount_id(child) != root_identity.mount_id:
                raise SourceReopenError("source path crossed a nested mount")
            current = child

        leaf_name = relative_parts[-1]
        _check_cancel(cancel_check)
        try:
            leaf_fd = os.open(leaf_name, _file_flags(), dir_fd=current)
        except OSError:
            raise SourceReopenError("source leaf cannot be opened safely") from None
        bindings.append((current, leaf_name, leaf_fd, False))
        before = os.fstat(leaf_fd)
        _require_file_identity(before, expected)
        if _mount_id(leaf_fd) != root_identity.mount_id:
            raise SourceReopenError("source file crossed a nested mount")
        digest = _hash_fd(leaf_fd, cancel_check)
        _check_cancel(cancel_check)
        after = os.fstat(leaf_fd)
        _require_file_identity(after, expected)
        if digest != expected.sha256:
            raise SourceReopenError("source content changed since inventory")
        _verify_bindings(bindings, expected)
        _require_root_identity(opened[len(root_parts)], root_identity)

        result = OpenedInventoryFile(leaf_fd)
        leaf_fd = None
        return result
    except SourceReopenError:
        raise
    except OSError:
        raise SourceReopenError("source changed while it was reopened") from None
    finally:
        if leaf_fd is not None:
            try:
                os.close(leaf_fd)
            except OSError:
                pass
        for fd in reversed(opened):
            try:
                os.close(fd)
            except OSError:
                pass


def _root_components(root: Path) -> tuple[str, ...]:
    raw = os.fspath(root)
    if type(raw) is not str:
        raise TypeError("source root must be a text path")
    lexical_parts = raw.split("/")
    path = Path(raw)
    if (
        not path.is_absolute()
        or raw.startswith("//")
        or "\\" in raw
        or any(part in {".", ".."} for part in lexical_parts)
        or path.as_posix() != raw
        or "\x00" in raw
    ):
        raise ValueError("source root must be a canonical absolute lexical path")
    return tuple(part for part in path.parts if part != os.sep)


def _relative_components(expected: InventoryFile) -> tuple[str, ...]:
    if type(expected) is not InventoryFile:
        raise TypeError("expected must be an InventoryFile")
    relative = expected.relative_path
    if (
        type(relative) is not str
        or not relative
        or relative.startswith("/")
        or "\\" in relative
        or "\x00" in relative
    ):
        raise ValueError("inventory path must be canonical relative text")
    parts = tuple(relative.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("inventory path contains a noncanonical component")
    integer_fields = (
        expected.size, expected.mtime_ns, expected.ctime_ns, expected.device,
        expected.inode, expected.mode,
    )
    if (
        any(type(value) is not int or value < 0 for value in integer_fields)
        or expected.mode > 0o7777
        or type(expected.sha256) is not str
        or _SHA256.fullmatch(expected.sha256) is None
    ):
        raise ValueError("inventory file identity is invalid")
    return parts


def _open_directory(path: str, *, dir_fd: int | None = None) -> int:
    try:
        fd = os.open(path, _directory_flags(), dir_fd=dir_fd)
    except OSError:
        raise SourceReopenError("source directory cannot be opened safely") from None
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise SourceReopenError("source path component is not a directory")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _require_root_identity(fd: int, expected: SourceRootIdentity) -> None:
    observed = os.fstat(fd)
    if (
        not stat.S_ISDIR(observed.st_mode)
        or observed.st_dev != expected.device
        or observed.st_ino != expected.inode
        or _mount_id(fd) != expected.mount_id
    ):
        raise SourceReopenError("source root identity changed since inventory")


def _require_file_identity(observed: os.stat_result, expected: InventoryFile) -> None:
    if (
        not stat.S_ISREG(observed.st_mode)
        or observed.st_size != expected.size
        or observed.st_mtime_ns != expected.mtime_ns
        or observed.st_ctime_ns != expected.ctime_ns
        or observed.st_dev != expected.device
        or observed.st_ino != expected.inode
        or stat.S_IMODE(observed.st_mode) != expected.mode
    ):
        raise SourceReopenError("source file identity changed since inventory")


def _hash_fd(fd: int, cancel_check: CancelCheck | None) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        _check_cancel(cancel_check)
        chunk = os.pread(fd, HASH_READ_SIZE, offset)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)
        offset += len(chunk)


def _check_cancel(cancel_check: CancelCheck | None) -> None:
    if cancel_check is None:
        return
    cancelled = cancel_check()
    if type(cancelled) is not bool:
        raise TypeError("cancel_check must return bool")
    if cancelled:
        raise SourceReopenCancelled("source revalidation was cancelled")


def _verify_bindings(
    bindings: list[tuple[int, str, int, bool]], expected: InventoryFile,
) -> None:
    for parent_fd, name, child_fd, is_directory in bindings:
        try:
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            raise SourceReopenError("source path binding changed") from None
        opened = os.fstat(child_fd)
        expected_type = stat.S_ISDIR if is_directory else stat.S_ISREG
        if (
            not expected_type(named.st_mode)
            or not expected_type(opened.st_mode)
            or named.st_dev != opened.st_dev
            or named.st_ino != opened.st_ino
        ):
            raise SourceReopenError("source path binding changed")
        if not is_directory:
            _require_file_identity(named, expected)


def _mount_id(fd: int) -> int:
    path = f"/proc/{os.getpid()}/fdinfo/{fd}"
    try:
        info_fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        raise SourceReopenError("source mount identity is unavailable") from None
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(info_fd, min(4096, FDINFO_MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > FDINFO_MAX_BYTES:
                raise SourceReopenError("source mount identity exceeded its bound")
    finally:
        os.close(info_fd)
    for line in b"".join(chunks).splitlines():
        if line.startswith(b"mnt_id:\t"):
            value = line.removeprefix(b"mnt_id:\t")
            if value.isdigit():
                return int(value)
    raise SourceReopenError("source mount identity is unavailable")


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW


def _file_flags() -> int:
    return os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


__all__ = [
    "OpenedInventoryFile",
    "SourceReopenCancelled",
    "SourceReopenError",
    "SourceRootIdentity",
    "open_inventory_file",
]
