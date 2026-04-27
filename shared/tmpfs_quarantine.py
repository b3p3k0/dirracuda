"""Runtime manager for optional tmpfs-backed quarantine storage.

This module centralizes tmpfs quarantine runtime decisions:
- platform gating (Linux only)
- presence-only tmpfs mount detection (no mount/umount operations)
- fallback-to-disk behavior + one-time warning payload
- cleanup support for tmpfs contents on application exit

The GUI should call ``bootstrap_tmpfs_quarantine()`` once at startup and
``cleanup_tmpfs_quarantine()`` during shutdown.
"""

from __future__ import annotations

import json
import platform
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

from shared.path_service import get_paths, get_legacy_paths, select_existing_path

_PATHS = get_paths()
_LEGACY = get_legacy_paths(paths=_PATHS)
_DISK_DEFAULT = select_existing_path(
    _PATHS.quarantine_dir,
    [
        _LEGACY.flat_quarantine_dir,
        _LEGACY.legacy_home_root / "quarantine",
    ],
)

# Canonical mountpoint first; legacy mountpoints remain detection-only fallbacks.
_TMPFS_CANONICAL = _PATHS.tmpfs_quarantine_dir
_TMPFS_LEGACY_DIRRACUDA = _LEGACY.flat_tmpfs_quarantine_dir
_TMPFS_LEGACY_SMBSEEK = _LEGACY.legacy_home_root / "quarantine_tmpfs"
_TMPFS_CANDIDATES = (
    ("canonical", _TMPFS_CANONICAL),
    ("legacy_dirracuda", _TMPFS_LEGACY_DIRRACUDA),
    ("legacy_smbseek", _TMPFS_LEGACY_SMBSEEK),
)

_README_NAME = "README.txt"
_TMPFS_SIZE_DEFAULT_MB = 512
_TMPFS_SIZE_MIN_MB = 64
_TMPFS_SIZE_MAX_MB = 4096

_WARNING_NON_LINUX = "non_linux"
_WARNING_LEGACY_MOUNTPOINT = "legacy_mountpoint"
_WARNING_MOUNT_NOT_FOUND = "mount_not_found"


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


def _detect_tmpfs_mountpoint() -> tuple[Optional[str], Optional[Path]]:
    """Return first active tmpfs mountpoint by canonical-first precedence."""
    for label, mountpoint in _TMPFS_CANDIDATES:
        if _is_tmpfs_mounted(mountpoint):
            return label, mountpoint
    return None, None


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
    mountpoint: Path = _TMPFS_CANONICAL
    disk_root: Path = _DISK_DEFAULT
    effective_root: Path = _DISK_DEFAULT
    tmpfs_active: bool = False
    mounted_by_app: bool = False
    fallback_reason: Optional[str] = None
    warning_message: Optional[str] = None
    warning_code: Optional[str] = None
    legacy_mount_in_use: bool = False
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
        "warning_code": _STATE.warning_code,
        "legacy_mount_in_use": _STATE.legacy_mount_in_use,
    }


def bootstrap_tmpfs_quarantine(
    *,
    config_path: Optional[Union[str, Path]] = None,
    config_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Initialize tmpfs runtime state from config and prepare effective root.

    This function is presence-only. It never attempts to mount or unmount tmpfs.
    If tmpfs is enabled but no supported tmpfs mountpoint is active, runtime
    falls back to disk quarantine and emits a one-time warning.
    """
    with _LOCK:
        data = config_data if isinstance(config_data, dict) else _load_config_data(config_path)

        quarantine_cfg = data.get("quarantine") if isinstance(data.get("quarantine"), dict) else {}
        use_tmpfs = _coerce_bool(quarantine_cfg.get("use_tmpfs"), False)
        # Keep size readable for config compatibility, but never use it for mounting.
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
        _STATE.mountpoint = _TMPFS_CANONICAL
        _STATE.disk_root = disk_root
        _STATE.effective_root = disk_root
        _STATE.tmpfs_active = False
        _STATE.mounted_by_app = False
        _STATE.fallback_reason = None
        _STATE.warning_message = None
        _STATE.warning_code = None
        _STATE.legacy_mount_in_use = False
        _STATE.warning_consumed = False

        if not use_tmpfs:
            return _snapshot()

        if not _STATE.platform_supported:
            _STATE.fallback_reason = "tmpfs quarantine is supported on Linux only"
            _STATE.warning_code = _WARNING_NON_LINUX
            _STATE.warning_message = (
                "Tmpfs quarantine is enabled in config, but this platform is not Linux. "
                "Dirracuda will use disk quarantine for this session."
            )
            _STATE.effective_root = disk_root
            return _snapshot()

        label, mountpoint = _detect_tmpfs_mountpoint()
        if mountpoint is not None and label is not None:
            _STATE.mountpoint = mountpoint
            _STATE.tmpfs_active = True
            _STATE.effective_root = mountpoint
            _STATE.mounted_by_app = False

            if label != "canonical":
                _STATE.legacy_mount_in_use = True
                _STATE.warning_code = _WARNING_LEGACY_MOUNTPOINT
                _STATE.warning_message = (
                    "Tmpfs quarantine is active on a legacy mountpoint:\n"
                    f"- Active: {mountpoint}\n"
                    f"- Canonical: {_TMPFS_CANONICAL}\n\n"
                    "Dirracuda now uses detect-only tmpfs behavior and will never mount/unmount automatically.\n"
                    "Please migrate your mount to the canonical path.\n\n"
                    "Suggested fix:\n"
                    f"1) Update /etc/fstab to target {_TMPFS_CANONICAL}\n"
                    f"2) sudo mkdir -p {_TMPFS_CANONICAL}\n"
                    "3) sudo mount -a"
                )
            return _snapshot()

        _STATE.warning_code = _WARNING_MOUNT_NOT_FOUND
        _STATE.fallback_reason = "no supported tmpfs mountpoint detected"
        _STATE.warning_message = (
            "Tmpfs quarantine is enabled, but no tmpfs mount was detected. "
            "Dirracuda will use disk quarantine for this session.\n\n"
            "Checked mountpoints:\n"
            f"- {_TMPFS_CANONICAL}\n"
            f"- {_TMPFS_LEGACY_DIRRACUDA}\n"
            f"- {_TMPFS_LEGACY_SMBSEEK}\n\n"
            "Dirracuda does not mount tmpfs automatically. "
            "Please pre-mount tmpfs externally (for example via /etc/fstab) and restart Dirracuda."
        )
        _STATE.effective_root = disk_root
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
      (active tmpfs mountpoint or disk fallback root).
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
    """Cleanup tmpfs quarantine contents.

    Runtime is detect-only; Dirracuda never unmounts tmpfs.
    """
    with _LOCK:
        active = _STATE.tmpfs_active
        mountpoint = _STATE.mountpoint
        disk_root = _STATE.disk_root

    if not active:
        return {"ok": True, "skipped": True, "message": "tmpfs inactive"}

    ok, detail = _purge_directory_contents(mountpoint)
    if not ok:
        return {"ok": False, "skipped": False, "message": f"cleanup failed: {detail}"}

    with _LOCK:
        _STATE.tmpfs_active = False
        _STATE.mounted_by_app = False
        _STATE.effective_root = disk_root

    return {"ok": True, "skipped": False, "message": "tmpfs cleanup complete (no unmount attempted)"}


__all__ = [
    "bootstrap_tmpfs_quarantine",
    "cleanup_tmpfs_quarantine",
    "consume_tmpfs_startup_warning",
    "get_tmpfs_runtime_state",
    "resolve_effective_quarantine_root",
    "tmpfs_has_quarantined_files",
]
