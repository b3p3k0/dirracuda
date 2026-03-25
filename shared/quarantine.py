"""Utility helpers for quarantining untrusted SMB artifacts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional, Union

_DEFAULT_ROOT = Path.home() / ".dirracuda" / "quarantine"


def _sanitize_label(value: str) -> str:
    cleaned = ''.join(c if c.isalnum() or c in {'-', '_', '.'} else '-' for c in value)
    cleaned = cleaned.strip('-_.')
    return cleaned or "host"


def _ensure_root(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    notice = root / "README.txt"
    if not notice.exists():
        notice.write_text(
            "This directory stores quarantined Dirracuda artifacts."
            " Inspect contents in an isolated environment before promoting them.",
            encoding='utf-8'
        )
    return root


def _host_root(base_path: Optional[Union[str, Path]], ip_address: Optional[str]) -> Path:
    root = Path(base_path).expanduser() if base_path else _DEFAULT_ROOT
    root = _ensure_root(root)
    safe_ip = _sanitize_label(ip_address or "host")
    host_dir = root / safe_ip
    host_dir.mkdir(parents=True, exist_ok=True)
    return host_dir


def _date_bucket(now: Optional[datetime] = None) -> str:
    now = now or datetime.utcnow()
    return now.strftime("%Y%m%d")


def create_quarantine_dir(
    ip_address: Optional[str],
    *,
    purpose: str = "extract",
    base_path: Optional[Union[str, Path]] = None
) -> Path:
    """
    Create and return a quarantine subdirectory for the given host.

    Layout: <root>/<host>/<YYYYMMDD>
    """
    host_dir = _host_root(base_path, ip_address)
    date_dir = host_dir / _date_bucket()
    date_dir.mkdir(parents=True, exist_ok=True)
    return date_dir


def build_quarantine_path(
    ip_address: Optional[str],
    share_name: Optional[str],
    *,
    base_path: Optional[Union[str, Path]] = None,
    purpose: str = "file_browser"
) -> Path:
    """
    Build a quarantine directory path for a specific host/share without writing files.

    Creates a structure:
      <root>/<host>/<YYYYMMDD>/<share>/

    The directory is created on disk to guarantee the path exists for downloads.
    """
    host_dir = _host_root(base_path, ip_address)
    date_dir = host_dir / _date_bucket()
    date_dir.mkdir(parents=True, exist_ok=True)

    safe_share = _sanitize_label(share_name or "share")
    session_dir = date_dir / safe_share
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def log_quarantine_event(host_dir: Path, message: str) -> None:
    """Append a simple activity line to the host's activity.log."""
    try:
        log_file = host_dir / "activity.log"
        timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(f"{timestamp} {message}\n")
    except Exception:
        pass


__all__ = ["create_quarantine_dir", "build_quarantine_path", "log_quarantine_event"]
