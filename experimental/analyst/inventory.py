"""Descriptor-safe, mergerfs-compatible source inventory."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final

HASH_READ_SIZE: Final = 1024 * 1024
FDINFO_MAX_BYTES: Final = 16 * 1024


class InventoryError(RuntimeError):
    """Base class for content-free inventory failures."""


class InventoryRootError(InventoryError):
    pass


class InventoryLimitError(InventoryError):
    pass


class InventoryChangedError(InventoryError):
    pass


class InventoryCancelled(InventoryError):
    pass


CancelCheck = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class InventoryLimits:
    max_entries: int = 100_000
    max_depth: int = 64

    def __post_init__(self) -> None:
        if type(self.max_entries) is not int or self.max_entries <= 0:
            raise ValueError("max_entries must be a positive integer")
        if type(self.max_depth) is not int or self.max_depth < 0:
            raise ValueError("max_depth must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class InventoryFile:
    relative_path: str
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int
    mode: int
    sha256: str


@dataclass(frozen=True, slots=True)
class InventoryExclusion:
    relative_path: str
    reason: str


@dataclass(frozen=True, slots=True)
class InventoryResult:
    root_device: int
    root_inode: int
    root_mount_id: int
    files: tuple[InventoryFile, ...]
    exclusions: tuple[InventoryExclusion, ...]

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)


@dataclass
class _RootHandle:
    root_fd: int
    opened_fds: list[int]
    bindings: list[tuple[int, str, int]]

    def close(self) -> None:
        for fd in reversed(self.opened_fds):
            try:
                os.close(fd)
            except OSError:
                pass


class _Walker:
    def __init__(self, root_stat: os.stat_result, mount_id: int,
                 limits: InventoryLimits,
                 cancel_check: CancelCheck | None) -> None:
        self.root_stat = root_stat
        self.mount_id = mount_id
        self.limits = limits
        self.cancel_check = cancel_check
        self.entries_seen = 0
        self.files: list[InventoryFile] = []
        self.exclusions: list[InventoryExclusion] = []

    def walk(self, directory_fd: int, parts: tuple[str, ...], depth: int) -> None:
        if depth > self.limits.max_depth:
            raise InventoryLimitError("inventory directory depth limit exceeded")
        before = os.fstat(directory_fd)
        try:
            with os.scandir(directory_fd) as iterator:
                names = sorted(entry.name for entry in iterator)
        except OSError as exc:
            raise InventoryChangedError("inventory directory became unreadable") from exc

        for name in names:
            self._check_cancel()
            self.entries_seen += 1
            if self.entries_seen > self.limits.max_entries:
                raise InventoryLimitError("inventory entry limit exceeded")
            relative = _relative(parts + (name,))
            try:
                observed = os.stat(name, dir_fd=directory_fd,
                                   follow_symlinks=False)
            except OSError:
                self._exclude(relative, "entry_unreadable")
                continue

            if name == "_analyst":
                self._exclude(relative, "analyst_output")
            elif stat.S_ISLNK(observed.st_mode):
                self._exclude(relative, "symlink")
            elif stat.S_ISDIR(observed.st_mode):
                self._walk_directory(
                    directory_fd, name, observed, parts + (name,), depth + 1
                )
            elif stat.S_ISREG(observed.st_mode):
                self._inventory_file(directory_fd, name, observed, relative)
            else:
                self._exclude(relative, "special_file")

        after = os.fstat(directory_fd)
        if not _stable_directory(before, after):
            raise InventoryChangedError("inventory directory changed during traversal")

    def _walk_directory(self, parent_fd: int, name: str,
                        observed: os.stat_result, parts: tuple[str, ...],
                        depth: int) -> None:
        relative = _relative(parts)
        try:
            child_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
        except OSError:
            self._exclude(relative, "entry_unreadable")
            return
        try:
            opened = os.fstat(child_fd)
            if not _same_object(observed, opened, stat.S_ISDIR):
                self._exclude(relative, "changed_during_inventory")
                return
            if _mount_id(child_fd) != self.mount_id:
                self._exclude(relative, "mount_boundary")
                return
            self.walk(child_fd, parts, depth)
            if not _name_still_binds(parent_fd, name, opened, stat.S_ISDIR):
                raise InventoryChangedError(
                    "inventory directory binding changed during traversal"
                )
        finally:
            os.close(child_fd)

    def _inventory_file(self, parent_fd: int, name: str,
                        observed: os.stat_result, relative: str) -> None:
        try:
            file_fd = os.open(name, _file_flags(), dir_fd=parent_fd)
        except OSError:
            self._exclude(relative, "entry_unreadable")
            return
        try:
            opened = os.fstat(file_fd)
            if not _same_object(observed, opened, stat.S_ISREG):
                self._exclude(relative, "changed_during_inventory")
                return
            if _mount_id(file_fd) != self.mount_id:
                self._exclude(relative, "mount_boundary")
                return
            try:
                digest = _hash_fd(file_fd, self.cancel_check)
                after = os.fstat(file_fd)
            except OSError:
                self._exclude(relative, "entry_unreadable")
                return
            if (not _stable_file(opened, after)
                    or not _name_still_binds(
                        parent_fd, name, after, stat.S_ISREG)):
                self._exclude(relative, "changed_during_inventory")
                return
            self.files.append(InventoryFile(
                relative_path=relative,
                size=after.st_size,
                mtime_ns=after.st_mtime_ns,
                ctime_ns=after.st_ctime_ns,
                device=after.st_dev,
                inode=after.st_ino,
                mode=stat.S_IMODE(after.st_mode),
                sha256=digest,
            ))
        finally:
            os.close(file_fd)

    def _exclude(self, relative: str, reason: str) -> None:
        self.exclusions.append(InventoryExclusion(relative, reason))

    def _check_cancel(self) -> None:
        if self.cancel_check is not None and self.cancel_check():
            raise InventoryCancelled("inventory cancelled")


def inventory_tree(root: Path, *,
                   limits: InventoryLimits | None = None,
                   cancel_check: CancelCheck | None = None) -> InventoryResult:
    """Inventory one absolute tree without following names after validation.

    The result is all-or-nothing for traversal/limit races. Individual unsafe
    entries are retained only as content-free exclusions.
    """
    selected_limits = limits or InventoryLimits()
    handle = _open_root(root)
    try:
        root_stat = os.fstat(handle.root_fd)
        root_mount_id = _mount_id(handle.root_fd)
        walker = _Walker(root_stat, root_mount_id, selected_limits, cancel_check)
        walker.walk(handle.root_fd, (), 0)
        _verify_root_bindings(handle)
        return InventoryResult(
            root_device=root_stat.st_dev,
            root_inode=root_stat.st_ino,
            root_mount_id=root_mount_id,
            files=tuple(walker.files),
            exclusions=tuple(walker.exclusions),
        )
    finally:
        handle.close()


def _open_root(root: Path) -> _RootHandle:
    raw = os.fspath(root)
    if not isinstance(raw, str):
        raise TypeError("inventory root must be a text path")
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise InventoryRootError("inventory root must be an absolute lexical path")
    parts = tuple(part for part in path.parts if part != os.sep)
    opened: list[int] = []
    bindings: list[tuple[int, str, int]] = []
    try:
        current = os.open(os.sep, _directory_flags())
        opened.append(current)
        for name in parts:
            child = os.open(name, _directory_flags(), dir_fd=current)
            opened.append(child)
            bindings.append((current, name, child))
            current = child
        return _RootHandle(current, opened, bindings)
    except OSError as exc:
        for fd in reversed(opened):
            try:
                os.close(fd)
            except OSError:
                pass
        raise InventoryRootError("inventory root cannot be opened safely") from exc


def _verify_root_bindings(handle: _RootHandle) -> None:
    for parent_fd, name, child_fd in handle.bindings:
        opened = os.fstat(child_fd)
        if not _name_still_binds(parent_fd, name, opened, stat.S_ISDIR):
            raise InventoryChangedError("inventory root binding changed")


def _hash_fd(fd: int, cancel_check: CancelCheck | None = None) -> str:
    digest = hashlib.sha256()
    while True:
        if cancel_check is not None and cancel_check():
            raise InventoryCancelled("inventory cancelled")
        chunk = os.read(fd, HASH_READ_SIZE)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)


def _mount_id(fd: int) -> int:
    path = f"/proc/{os.getpid()}/fdinfo/{fd}"
    info_fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                info_fd, min(4096, FDINFO_MAX_BYTES + 1 - total)
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > FDINFO_MAX_BYTES:
                raise InventoryError("mount identity record exceeded its bound")
    finally:
        os.close(info_fd)
    body = b"".join(chunks)
    for line in body.splitlines():
        if line.startswith(b"mnt_id:\t"):
            value = line.removeprefix(b"mnt_id:\t")
            if value.isdigit():
                return int(value)
    raise InventoryError("mount identity is unavailable")


def _name_still_binds(parent_fd: int, name: str, opened: os.stat_result,
                      expected_type) -> bool:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False
    return _same_object(current, opened, expected_type) and _stable_metadata(
        current, opened
    )


def _same_object(left: os.stat_result, right: os.stat_result,
                 expected_type) -> bool:
    return (
        expected_type(left.st_mode)
        and expected_type(right.st_mode)
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
    )


def _stable_metadata(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _stable_file(left: os.stat_result, right: os.stat_result) -> bool:
    return _same_object(left, right, stat.S_ISREG) and _stable_metadata(left, right)


def _stable_directory(left: os.stat_result, right: os.stat_result) -> bool:
    return _same_object(left, right, stat.S_ISDIR) and _stable_metadata(left, right)


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW


def _file_flags() -> int:
    return os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


def _relative(parts: tuple[str, ...]) -> str:
    return "/".join(parts)
