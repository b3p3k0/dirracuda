"""
File extraction helper for the Dirracuda GUI.

Reuses impacket.smbconnection to download a limited number of files from
anonymous/guest-accessible shares while respecting configurable safety limits.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from threading import Event

from shared.quarantine import log_quarantine_event

try:  # pragma: no cover - runtime dependency
    from impacket.smbconnection import SMBConnection, SessionError
except ImportError:  # pragma: no cover - handled upstream
    SMBConnection = None
    SessionError = Exception

DEFAULT_CLIENT_NAME = "dirracuda-extract"


class ExtractError(RuntimeError):
    """Raised when extraction cannot proceed."""


def run_extract(
    ip_address: str,
    shares: Sequence[str],
    *,
    download_dir: Path,
    username: str,
    password: str,
    max_total_bytes: int,
    max_file_bytes: int,
    max_file_count: int,
    max_seconds: int,
    max_depth: int,
    allowed_extensions: Sequence[str],
    denied_extensions: Sequence[str],
    delay_seconds: float,
    connection_timeout: int,
    extension_mode: Optional[str] = None,
    progress_callback: Optional[Callable[[str, int, Optional[int]], None]] = None,
    cancel_event: Optional[Event] = None,
) -> Dict[str, Any]:
    """
    Download files from accessible shares while enforcing guardrails.

    Args:
        ip_address: Target host.
        shares: Accessible share names.
        download_dir: Destination directory (will be created).
        username/password: Credentials to reuse.
        max_total_bytes: Global cap for downloaded bytes (0 = unlimited).
        max_file_bytes: Per-file size limit (0 = unlimited).
        max_file_count: Maximum files to download (0 = unlimited).
        max_seconds: Maximum wall-clock time (0 = unlimited).
        max_depth: Maximum directory recursion depth.
        allowed_extensions: Whitelist of extensions (empty = allow all).
        denied_extensions: Blacklist of extensions.
        delay_seconds: Delay between downloads to avoid aggressive pulls.
        connection_timeout: Socket timeout per SMB request.
        progress_callback: Callable receiving (display_path, current_count, max_count).

    Returns:
        Summary dictionary describing the run (suitable for JSON logging).
    """
    if SMBConnection is None:  # pragma: no cover - runtime detection
        raise ExtractError(
            "impacket is not available. Install it in the GUI environment "
            "(e.g., pip install impacket) to enable extraction."
        )

    _check_cancel(cancel_event)

    normalized_shares = [share.strip("\\/ ") for share in shares if share.strip("\\/ ")]
    if not normalized_shares:
        raise ExtractError("No accessible shares provided.")

    download_dir.mkdir(parents=True, exist_ok=True)

    allowed_set = _normalize_extensions(allowed_extensions)
    denied_set = _normalize_extensions(denied_extensions)
    mode = (extension_mode or "legacy").lower()
    if mode not in ("download_all", "allow_only", "deny_only", "legacy"):
        mode = "legacy"

    summary: Dict[str, Any] = {
        "ip_address": ip_address,
        "shares_requested": normalized_shares,
        "download_root": str(download_dir),
        "started_at": _utcnow(),
        "finished_at": None,
        "limits": {
            "max_total_bytes": max_total_bytes,
            "max_file_bytes": max_file_bytes,
            "max_file_count": max_file_count,
            "max_seconds": max_seconds,
            "max_depth": max_depth,
        },
        "totals": {
            "files_downloaded": 0,
            "bytes_downloaded": 0,
            "files_skipped": 0,
        },
        "extension_mode": mode,
        "files": [],
        "skipped": [],
        "errors": [],
        "timed_out": False,
        "stop_reason": None,
    }

    start_time = time.time()
    total_bytes = 0
    total_files = 0

    for share in normalized_shares:
        _check_cancel(cancel_event)
        if _time_exceeded(start_time, max_seconds):
            summary["timed_out"] = True
            summary["stop_reason"] = "time_limit"
            break
        try:
            conn = _connect(ip_address, connection_timeout)
            conn.login(username, password)
        except Exception as exc:  # pragma: no cover - network errors
            summary["errors"].append({
                "share": share,
                "message": f"Login failed: {exc}"
            })
            continue

        try:
            for file_info in _walk_files(conn, share, max_depth, summary):
                _check_cancel(cancel_event)
                if _time_exceeded(start_time, max_seconds):
                    summary["timed_out"] = True
                    summary["stop_reason"] = "time_limit"
                    break

                if max_file_count > 0 and total_files >= max_file_count:
                    summary["stop_reason"] = "file_limit"
                    break

                rel_display = file_info["display_path"]
                smb_path = file_info["smb_path"]
                file_size = file_info["size"]
                file_mtime = file_info.get("mtime")

                should_download, reason = _should_download_file(
                    rel_display,
                    file_size,
                    allowed_set,
                    denied_set,
                    mode,
                    max_file_bytes,
                    max_total_bytes,
                    total_bytes,
                )

                if not should_download:
                    summary["totals"]["files_skipped"] += 1
                    summary["skipped"].append({
                        "share": share,
                        "path": rel_display,
                        "reason": reason,
                        "size": file_size
                    })
                    if reason == "total_size_limit":
                        summary["stop_reason"] = "total_size_limit"
                        break
                    continue

                dest_path = download_dir / share / file_info["local_rel_path"]
                dest_path.parent.mkdir(parents=True, exist_ok=True)

                current_index = total_files + 1
                if progress_callback:
                    progress_callback(rel_display, current_index, max_file_count or None)

                with open(dest_path, "wb") as outfile:
                    def _writer(data: bytes) -> None:
                        outfile.write(data)

                    try:
                        conn.getFile(share, smb_path, _writer)
                    except Exception as exc:
                        if _is_access_denied(exc):
                            summary["totals"]["files_skipped"] += 1
                            summary["skipped"].append({
                                "share": share,
                                "path": rel_display,
                                "reason": "access_denied",
                                "size": file_size
                            })
                            summary["errors"].append({
                                "share": share,
                                "path": rel_display,
                                "message": f"Access denied downloading file: {exc}"
                            })
                            # Remove partially written file if any
                            try:
                                dest_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            continue
                        summary["errors"].append({
                            "share": share,
                            "path": rel_display,
                            "message": f"Download error: {exc}"
                        })
                        try:
                            dest_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        continue

                # Preserve original modification time if available
                if file_mtime is not None:
                    try:
                        os.utime(dest_path, (file_mtime, file_mtime))
                    except Exception:
                        pass

                total_files += 1
                total_bytes += file_size
                summary["files"].append({
                    "share": share,
                    "path": rel_display,
                    "size": file_size,
                    "saved_to": str(dest_path)
                })
                try:
                    host_dir = download_dir.parent
                    log_quarantine_event(host_dir, f"extracted {share}/{rel_display} -> {dest_path}")
                except Exception:
                    pass

                if delay_seconds > 0:
                    _check_cancel(cancel_event)
                    time.sleep(delay_seconds)

                if max_total_bytes > 0 and total_bytes >= max_total_bytes:
                    summary["stop_reason"] = "total_size_limit"
                    break
            else:
                # Completed loop without break; continue to next share
                pass

            if summary["stop_reason"] in {"time_limit", "file_limit", "total_size_limit"}:
                break

        finally:
            try:
                conn.logoff()
            except Exception:
                pass

    summary["totals"]["files_downloaded"] = total_files
    summary["totals"]["bytes_downloaded"] = total_bytes
    summary["finished_at"] = _utcnow()

    return summary


def _check_cancel(cancel_event: Optional[Event]) -> None:
    if cancel_event and cancel_event.is_set():
        raise ExtractError("Extraction cancelled")


def write_extract_log(summary: Dict[str, Any]) -> Path:
    """
    Persist extraction summary under ~/.dirracuda/extract_logs.

    Returns:
        Path to the log file on disk.
    """
    logs_dir = Path.home() / ".dirracuda" / "extract_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = summary.get("finished_at") or summary.get("started_at") or _utcnow()
    ip_fragment = (summary.get("ip_address") or "host").replace(":", "-")
    safe_timestamp = timestamp.replace(":", "").replace("-", "")
    log_file = logs_dir / f"extract_{ip_fragment}_{safe_timestamp}.json"
    log_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return log_file


def _connect(ip_address: str, timeout_seconds: int) -> SMBConnection:
    conn = SMBConnection(
        ip_address,
        ip_address,
        DEFAULT_CLIENT_NAME,
        sess_port=445,
        timeout=timeout_seconds,
    )
    conn.setTimeout(timeout_seconds)
    return conn


def _walk_files(
    conn: SMBConnection,
    share: str,
    max_depth: int,
    summary: Dict[str, Any],
) -> Iterable[Dict[str, Any]]:
    """Yield file metadata dictionaries for the share up to max_depth."""
    stack: List[Tuple[str, int]] = [("", 0)]

    while stack:
        current_path, depth = stack.pop()
        try:
            entries = _list_directory(conn, share, current_path or "")
        except Exception as exc:
            reason = "access_denied" if _is_access_denied(exc) else "list_error"
            summary["errors"].append({
                "share": share,
                "path": current_path or "\\",
                "message": f"List failed: {exc}",
                "reason": reason
            })
            # Skip this branch but keep processing others
            continue

        for entry in entries:
            name = entry["name"]
            rel_path = f"{current_path}\\{name}" if current_path else name

            if entry["is_directory"]:
                if depth < max_depth:
                    stack.append((rel_path, depth + 1))
                continue

            display_path = rel_path.replace("\\", "/")
            smb_path = _smb_path(rel_path)
            local_rel = Path(*_safe_parts(rel_path))
            yield {
                "display_path": display_path,
                "smb_path": smb_path,
                "local_rel_path": local_rel,
                "size": entry["size"],
                "mtime": entry.get("mtime"),
            }


def _list_directory(
    conn: SMBConnection,
    share: str,
    current_path: str,
) -> List[Dict[str, Any]]:
    pattern = f"{current_path}\\*" if current_path else "*"
    entries = conn.listPath(share, pattern)
    payload: List[Dict[str, any]] = []
    for entry in entries:
        name = entry.get_longname()
        if name in (".", ".."):
            continue
        mtime = entry.get_mtime_epoch() if hasattr(entry, "get_mtime_epoch") else None
        payload.append({
            "name": name,
            "is_directory": entry.is_directory(),
            "size": entry.get_filesize(),
            "mtime": mtime,
        })
    return payload


def _normalize_extensions(values: Sequence[str]) -> set:
    normalized = set()
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = value.strip().lower()
        if not cleaned:
            continue
        if cleaned in ("<no extension>", "no extension", "no-extension"):
            normalized.add("")  # Represent extensionless files
            continue
        normalized.add(cleaned)
    return normalized


def _should_download_file(
    rel_path: str,
    file_size: int,
    allowed_set: set,
    denied_set: set,
    mode: str,
    max_file_bytes: int,
    max_total_bytes: int,
    total_bytes: int,
) -> Tuple[bool, Optional[str]]:
    ext = Path(rel_path).suffix.lower()

    if mode == "download_all":
        pass  # skip extension filtering entirely
    elif mode == "deny_only":
        if denied_set and ext in denied_set:
            return False, "denied_extension"
    elif mode == "allow_only":
        if allowed_set:
            if ext in allowed_set:
                pass
            elif ext == "" and "" in allowed_set:
                pass
            else:
                return False, "not_included_extension"
    else:  # legacy combined behavior
        if denied_set and ext in denied_set:
            return False, "denied_extension"
        if allowed_set and ext and ext not in allowed_set:
            return False, "not_included_extension"

    if max_file_bytes > 0 and file_size > max_file_bytes:
        return False, "file_too_large"
    if max_total_bytes > 0 and (total_bytes + file_size) > max_total_bytes:
        return False, "total_size_limit"
    return True, None


def _smb_path(rel_path: str) -> str:
    cleaned = rel_path.replace("/", "\\").lstrip("\\")
    return f"\\{cleaned}"


def _safe_parts(rel_path: str) -> List[str]:
    parts: List[str] = []
    for segment in rel_path.replace("\\", "/").split("/"):
        if not segment or segment in (".", ".."):
            continue
        parts.append(segment)
    return parts


def _time_exceeded(start: float, max_seconds: int) -> bool:
    if max_seconds <= 0:
        return False
    return (time.time() - start) >= max_seconds


def _utcnow() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _is_access_denied(exc: Exception) -> bool:
    """
    Return True if the exception represents an SMB access-denied condition.
    """
    try:
        code = getattr(exc, "getErrorCode", lambda: None)()
        if isinstance(code, int) and code in (0xC0000022, 0xC00000A2):
            return True
    except Exception:
        pass
    text = str(exc).upper()
    return "STATUS_ACCESS_DENIED" in text or "ACCESS_DENIED" in text
