"""Runtime manager for optional tmpfs-backed quarantine storage.

This module centralizes tmpfs lifecycle decisions for quarantine paths:
- platform gating (Linux only)
- tmpfs mount detection + optional mount attempt
- fallback-to-disk behavior + one-time warning payload
- cleanup/unmount support on application exit

The GUI should call ``bootstrap_tmpfs_quarantine()`` once at startup and
``cleanup_tmpfs_quarantine()`` during shutdown.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

_DISK_DEFAULT = Path.home() / ".dirracuda" / "quarantine"
_TMPFS_DEFAULT = Path.home() / ".dirracuda" / "quarantine_tmpfs"
_README_NAME = "README.txt"
_TMPFS_SIZE_DEFAULT_MB = 512
_TMPFS_SIZE_MIN_MB = 64
_TMPFS_SIZE_MAX_MB = 4096


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _load_config_data(config_path: Optional[Union[str, Path]]) -> Dict[str, Any]:
    if not config_path:
        return {}
    path_obj = Path(config_path).expanduser()
    if not path_obj.exists() or not path_obj.is_file():
        return {}
    try:
        loaded = json.loads(path_obj.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _extract_disk_root(config_data: Dict[str, Any]) -> Path:
    candidates = (
        ((config_data.get("file_browser") or {}).get("quarantine_root")),
        ((config_data.get("ftp_browser") or {}).get("quarantine_base")),
        ((config_data.get("http_browser") or {}).get("quarantine_base")),
        ((config_data.get("file_collection") or {}).get("quarantine_base")),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return Path(candidate).expanduser()
    return _DISK_DEFAULT


def _kernel_supports_noswap() -> bool:
    if platform.system().lower() != "linux":
        return False
    release = platform.release()
    pieces = release.split(".")
    if len(pieces) < 2:
        return False
    try:
        major = int("".join(ch for ch in pieces[0] if ch.isdigit()) or "0")
        minor = int("".join(ch for ch in pieces[1] if ch.isdigit()) or "0")
    except ValueError:
        return False
    return (major, minor) >= (6, 4)


def _mount_fstype(path: Path) -> Optional[str]:
    proc_mounts = Path("/proc/mounts")
    if not proc_mounts.exists():
        return None
    try:
        resolved = str(path.expanduser().resolve())
    except Exception:
        resolved = str(path.expanduser())

    try:
        lines = proc_mounts.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None

    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mount_col = parts[1].replace("\\040", " ")
        try:
            mount_resolved = str(Path(mount_col).resolve())
        except Exception:
            mount_resolved = mount_col
        if mount_resolved == resolved:
            return parts[2]
    return None


def _is_tmpfs_mounted(path: Path) -> bool:
    return _mount_fstype(path) == "tmpfs"


def _is_any_mount(path: Path) -> bool:
    return _mount_fstype(path) is not None


def _run_mount_tmpfs(mountpoint: Path, size_mb: int, *, with_noswap: bool) -> tuple[bool, str]:
    options = [f"size={size_mb}M"]
    if with_noswap:
        options.append("noswap")

    cmd = [
        "mount",
        "-t",
        "tmpfs",
        "tmpfs",
        "-o",
        ",".join(options),
        str(mountpoint),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
    except Exception as exc:
        return False, str(exc)
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or result.stdout or "").strip()
    if not detail:
        detail = f"mount failed with exit code {result.returncode}"
    return False, detail


def _run_umount(mountpoint: Path) -> tuple[bool, str]:
    cmd = ["umount", str(mountpoint)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
    except Exception as exc:
        return False, str(exc)
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or result.stdout or "").strip()
    if not detail:
        detail = f"umount failed with exit code {result.returncode}"
    return False, detail


def _purge_directory_contents(root: Path) -> tuple[bool, str]:
    try:
        if not root.exists():
            return True, ""
        for child in root.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=False)
            else:
                child.unlink(missing_ok=True)
        return True, ""
    except Exception as exc:
        return False, str(exc)


@dataclass
class _TmpfsState:
    bootstrapped: bool = False
    use_tmpfs: bool = False
    tmpfs_size_mb: int = _TMPFS_SIZE_DEFAULT_MB
    platform_supported: bool = False
    mountpoint: Path = _TMPFS_DEFAULT
    disk_root: Path = _DISK_DEFAULT
    effective_root: Path = _DISK_DEFAULT
    tmpfs_active: bool = False
    mounted_by_app: bool = False
    fallback_reason: Optional[str] = None
    warning_message: Optional[str] = None
    warning_consumed: bool = False


_STATE = _TmpfsState()
_LOCK = threading.RLock()


def _snapshot() -> Dict[str, Any]:
    return {
        "bootstrapped": _STATE.bootstrapped,
        "use_tmpfs": _STATE.use_tmpfs,
        "tmpfs_size_mb": _STATE.tmpfs_size_mb,
        "platform_supported": _STATE.platform_supported,
        "mountpoint": str(_STATE.mountpoint),
        "disk_root": str(_STATE.disk_root),
        "effective_root": str(_STATE.effective_root),
        "tmpfs_active": _STATE.tmpfs_active,
        "mounted_by_app": _STATE.mounted_by_app,
        "fallback_reason": _STATE.fallback_reason,
        "warning_message": _STATE.warning_message,
    }


def bootstrap_tmpfs_quarantine(
    *,
    config_path: Optional[Union[str, Path]] = None,
    config_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Initialize tmpfs runtime state from config and prepare effective root.

    If tmpfs is enabled but unavailable, this function falls back to disk and
    stores a one-time warning payload retrievable through
    ``consume_tmpfs_startup_warning()``.
    """
    with _LOCK:
        data = config_data if isinstance(config_data, dict) else _load_config_data(config_path)

        quarantine_cfg = data.get("quarantine") if isinstance(data.get("quarantine"), dict) else {}
        use_tmpfs = _coerce_bool(quarantine_cfg.get("use_tmpfs"), False)
        size_mb = _coerce_int(
            quarantine_cfg.get("tmpfs_size_mb"),
            _TMPFS_SIZE_DEFAULT_MB,
            minimum=_TMPFS_SIZE_MIN_MB,
            maximum=_TMPFS_SIZE_MAX_MB,
        )

        disk_root = _extract_disk_root(data)

        _STATE.bootstrapped = True
        _STATE.use_tmpfs = use_tmpfs
        _STATE.tmpfs_size_mb = size_mb
        _STATE.platform_supported = platform.system().lower() == "linux"
        _STATE.mountpoint = _TMPFS_DEFAULT
        _STATE.disk_root = disk_root
        _STATE.effective_root = disk_root
        _STATE.tmpfs_active = False
        _STATE.mounted_by_app = False
        _STATE.fallback_reason = None
        _STATE.warning_message = None
        _STATE.warning_consumed = False

        if not use_tmpfs:
            return _snapshot()

        if not _STATE.platform_supported:
            _STATE.fallback_reason = "tmpfs quarantine is supported on Linux only"
            _STATE.warning_message = (
                "Tmpfs quarantine is enabled in config, but this platform is not Linux. "
                "Dirracuda will use disk quarantine for this session."
            )
            _STATE.effective_root = disk_root
            return _snapshot()

        mountpoint = _STATE.mountpoint
        try:
            mountpoint.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            _STATE.fallback_reason = f"unable to prepare tmpfs mountpoint: {exc}"
            _STATE.warning_message = (
                "Tmpfs quarantine is enabled, but the mountpoint could not be prepared. "
                "Dirracuda will use disk quarantine for this session."
            )
            _STATE.effective_root = disk_root
            return _snapshot()

        if _is_tmpfs_mounted(mountpoint):
            _STATE.tmpfs_active = True
            _STATE.mounted_by_app = False
            _STATE.effective_root = mountpoint
            return _snapshot()

        if _is_any_mount(mountpoint):
            _STATE.fallback_reason = "mountpoint is occupied by a non-tmpfs filesystem"
            _STATE.warning_message = (
                "Tmpfs quarantine is enabled, but the configured mountpoint is already mounted "
                "as a non-tmpfs filesystem. Dirracuda will use disk quarantine for this session."
            )
            _STATE.effective_root = disk_root
            return _snapshot()

        supports_noswap = _kernel_supports_noswap()
        mounted, detail = _run_mount_tmpfs(mountpoint, size_mb, with_noswap=supports_noswap)
        if not mounted:
            _STATE.fallback_reason = detail
            _STATE.warning_message = (
                "Tmpfs quarantine is enabled, but mount failed. "
                "Dirracuda will use disk quarantine for this session.\n\n"
                f"Reason: {detail}"
            )
            _STATE.effective_root = disk_root
            return _snapshot()

        _STATE.tmpfs_active = True
        _STATE.mounted_by_app = True
        _STATE.effective_root = mountpoint
        return _snapshot()


def get_tmpfs_runtime_state() -> Dict[str, Any]:
    """Return a snapshot of current tmpfs runtime state."""
    with _LOCK:
        return _snapshot()


def consume_tmpfs_startup_warning() -> Optional[str]:
    """Return pending startup warning once, then clear visibility."""
    with _LOCK:
        if _STATE.warning_message and not _STATE.warning_consumed:
            _STATE.warning_consumed = True
            return _STATE.warning_message
        return None


def resolve_effective_quarantine_root(
    base_path: Optional[Union[str, Path]] = None,
) -> Path:
    """Resolve the effective quarantine root for current runtime state.

    Behavior:
    - If tmpfs is configured (enabled), return current effective root
      (tmpfs mountpoint or disk fallback root).
    - If tmpfs is not configured, preserve existing behavior and prefer
      explicit ``base_path`` when provided.
    """
    with _LOCK:
        if _STATE.use_tmpfs:
            return _STATE.effective_root
        if base_path:
            return Path(base_path).expanduser()
        if _STATE.bootstrapped:
            return _STATE.disk_root
        return _DISK_DEFAULT


def tmpfs_has_quarantined_files() -> bool:
    """Check whether active tmpfs quarantine contains user artifacts.

    README marker files are ignored.
    """
    with _LOCK:
        if not _STATE.tmpfs_active:
            return False
        root = _STATE.mountpoint

    if not root.exists() or not root.is_dir():
        return False

    try:
        for child in root.iterdir():
            if child.name == _README_NAME:
                continue
            return True
    except Exception:
        return False
    return False


def cleanup_tmpfs_quarantine() -> Dict[str, Any]:
    """Cleanup tmpfs quarantine contents and unmount when app-mounted.

    Unmount is attempted only when this process mounted tmpfs.
    """
    with _LOCK:
        active = _STATE.tmpfs_active
        mountpoint = _STATE.mountpoint
        mounted_by_app = _STATE.mounted_by_app
        disk_root = _STATE.disk_root

    if not active:
        return {"ok": True, "skipped": True, "message": "tmpfs inactive"}

    ok, detail = _purge_directory_contents(mountpoint)
    if not ok:
        return {"ok": False, "skipped": False, "message": f"cleanup failed: {detail}"}

    if mounted_by_app:
        umount_ok, umount_detail = _run_umount(mountpoint)
        if not umount_ok:
            return {
                "ok": False,
                "skipped": False,
                "message": f"umount failed: {umount_detail}",
            }

    with _LOCK:
        _STATE.tmpfs_active = False
        _STATE.mounted_by_app = False
        _STATE.effective_root = disk_root

    return {"ok": True, "skipped": False, "message": "tmpfs cleanup complete"}


__all__ = [
    "bootstrap_tmpfs_quarantine",
    "cleanup_tmpfs_quarantine",
    "consume_tmpfs_startup_warning",
    "get_tmpfs_runtime_state",
    "resolve_effective_quarantine_root",
    "tmpfs_has_quarantined_files",
]
