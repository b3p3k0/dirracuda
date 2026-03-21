"""
Probe runner built on impacket.smbconnection.

Given an IP and list of accessible shares, it enumerates a limited number
of directories/files per share so the GUI can render a quick preview.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional
from threading import Event

try:
    from impacket.smbconnection import SMBConnection
except ImportError:  # pragma: no cover - handled at runtime
    SMBConnection = None


DEFAULT_USERNAME = "guest"
DEFAULT_PASSWORD = ""
DEFAULT_CLIENT_NAME = "xsmbseek-probe"


class ProbeError(RuntimeError):
    """Raised when a probe operation fails."""


def _check_cancel(cancel_event: Optional[Event]) -> None:
    if cancel_event and cancel_event.is_set():
        raise ProbeError("Probe cancelled")


def run_probe(
    ip_address: str,
    shares: List[str],
    *,
    max_directories: int,
    max_files: int,
    timeout_seconds: int,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    enable_rce_analysis: bool = False,
    cancel_event: Optional[Event] = None,
    allow_empty: bool = False,
    config: Optional["SMBSeekConfig"] = None,
    db_accessor: Optional[Any] = None,
    legacy_mode: bool = False
) -> Dict[str, Any]:
    """
    Enumerate limited directory/file information for each accessible share.

    Args:
        ip_address: Target server IP/hostname.
        shares: List of share names marked accessible.
        max_directories: Max directories per share to inspect.
        max_files: Max files per directory to list.
        timeout_seconds: SMB socket timeout per request.
        username/password: Credentials to reuse (guest/anonymous by default).
        enable_rce_analysis: Enable RCE vulnerability analysis if True.
        config: Optional SMBSeekConfig to reuse budget/timeouts for RCE probes.
        db_accessor: Optional database accessor for persisting RCE status.
        legacy_mode: Enable SMB1 / MS17-010 safe probes when True.

    Returns:
        Dictionary describing probe snapshot suitable for caching/printing.
        Includes rce_analysis key if RCE analysis is enabled.
    """
    if SMBConnection is None:
        raise ProbeError(
            "impacket is not available. Install it in the GUI environment "
            "(e.g., pip install impacket) to enable probe support."
        )

    _check_cancel(cancel_event)

    normalized_shares = [share.strip("\\/ ") for share in shares if share.strip("\\/ ")]
    if not normalized_shares and not allow_empty:
        raise ProbeError("No accessible shares available to probe.")

    snapshot = {
        "ip_address": ip_address,
        "run_at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "limits": {
            "max_directories": max_directories,
            "max_files": max_files,
            "timeout_seconds": timeout_seconds
        },
        "shares": [],
        "errors": []
    }

    for raw_share in normalized_shares:
        _check_cancel(cancel_event)
        share_name = raw_share.strip("\\/ ")
        if not share_name:
            continue

        try:
            share_result = _probe_share(
                ip_address,
                share_name,
                max_directories=max_directories,
                max_files=max_files,
                timeout_seconds=timeout_seconds,
                username=username,
                password=password,
                cancel_event=cancel_event
            )
            snapshot["shares"].append(share_result)
        except Exception as exc:  # pragma: no cover
            snapshot["errors"].append({
                "share": share_name,
                "message": str(exc)
            })

    # RCE vulnerability analysis (if enabled)
    if enable_rce_analysis:
        _check_cancel(cancel_event)
        try:
            from shared.rce_scanner import scan_rce_indicators

            # Build host context from probe data
            host_context = {
                'ip_address': ip_address,
                'auth_method': f"{username}:{password}",
                'accessible_shares': [share['share'] for share in snapshot['shares']],
                'shares_found': [share['share'] for share in snapshot['shares']],
                'timestamp': snapshot['run_at']
            }

            # Perform RCE analysis
            rce_result = scan_rce_indicators(host_context)
            snapshot['rce_analysis'] = rce_result

            if db_accessor and rce_result:
                import json
                rce_status = rce_result.get('rce_status', 'not_run')
                verdict_summary = json.dumps({
                    'verdict': rce_result.get('verdict'),
                    'findings': rce_result.get('findings', [])[:5],
                    'not_assessable_reasons': rce_result.get('not_assessable_reasons', [])
                })
                try:
                    db_accessor.upsert_rce_status(ip_address, rce_status, verdict_summary)
                except Exception as e:
                    # Fail-soft: do not interrupt probe
                    pass

        except ImportError:
            # RCE scanner not available
            snapshot['rce_analysis'] = {
                'score': 0,
                'level': 'error',
                'status': 'scanner-unavailable',
                'error': 'RCE scanner dependencies not found'
            }
        except Exception as e:
            # RCE analysis failed
            snapshot['rce_analysis'] = {
                'score': 0,
                'level': 'error',
                'status': 'analysis-failed',
                'error': str(e)
            }
    else:
        # RCE analysis not enabled
        snapshot['rce_analysis'] = None

    return snapshot


def _connect(ip_address: str, timeout_seconds: int) -> SMBConnection:
    """Create and authenticate an SMBConnection."""
    conn = SMBConnection(
        ip_address,
        ip_address,
        DEFAULT_CLIENT_NAME,
        sess_port=445,
        timeout=timeout_seconds
    )
    conn.setTimeout(timeout_seconds)
    return conn


def _probe_share(
    ip_address: str,
    share_name: str,
    *,
    max_directories: int,
    max_files: int,
    timeout_seconds: int,
    username: str,
    password: str,
    cancel_event: Optional[Event] = None
) -> Dict[str, Any]:
    """Probe a single share and return structured directory/file info."""
    conn = _connect(ip_address, timeout_seconds)
    try:
        conn.login(username, password)
        root_entries = _list_entries(conn, share_name, pattern="*")
        dir_entries = [
            entry for entry in root_entries
            if entry["is_directory"] and entry["name"] not in (".", "..")
        ]
        root_files = [
            entry for entry in root_entries
            if (not entry["is_directory"]) and entry["name"] not in (".", "..")
        ]
        selected_dirs = dir_entries[:max_directories]
        root_files_selected = root_files[:max_files]

        directory_payload = []
        for dir_entry in selected_dirs:
            _check_cancel(cancel_event)
            safe_dir = dir_entry["name"].strip("\\/")
            nested_pattern = f"{safe_dir}\\*"
            nested_entries = _list_entries(conn, share_name, pattern=nested_pattern)
            nested_dirs = [
                entry for entry in nested_entries
                if entry["is_directory"] and entry["name"] not in (".", "..")
            ]
            file_entries = [
                entry for entry in nested_entries
                if not entry["is_directory"] and entry["name"] not in (".", "..")
            ]
            directory_payload.append({
                "name": dir_entry["name"],
                "subdirectories": [d["name"] for d in nested_dirs[:max_files]],
                "subdirectories_truncated": len(nested_dirs) > max_files,
                "files": [f["name"] for f in file_entries[:max_files]],
                "files_truncated": len(file_entries) > max_files
            })

        return {
            "share": share_name,
            "root_files": [f["name"] for f in root_files_selected],
            "root_files_truncated": len(root_files) > max_files,
            "directories": directory_payload,
            "directories_truncated": len(dir_entries) > max_directories
        }
    finally:
        try:
            conn.logoff()
        except Exception:
            pass


def _list_entries(conn: SMBConnection, share: str, pattern: str) -> List[Dict[str, Any]]:
    """Return parsed directory entries for a share pattern."""
    normalized_pattern = pattern if pattern else "*"
    if not normalized_pattern.endswith("*"):
        normalized_pattern = f"{normalized_pattern}*"

    entries = conn.listPath(share, normalized_pattern)
    payload = []
    for entry in entries:
        name = entry.get_longname()
        is_dir = entry.is_directory()
        payload.append({
            "name": name,
            "is_directory": is_dir
        })
    return payload
