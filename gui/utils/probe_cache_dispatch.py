"""
Protocol-aware dispatch for probe cache load operations.

Thin adapter over the three per-protocol cache modules. Callers pass
(ip_address, host_type) and receive the cached snapshot (or None) without
branching on host_type themselves.

Scope: load dispatch, snapshot-path dispatch, and run dispatch. Save, clear,
and get_cache_path remain on the per-protocol modules; their contracts are
unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gui.utils import probe_cache, ftp_probe_cache, http_probe_cache
from gui.utils import probe_runner, ftp_probe_runner, http_probe_runner

_UNSET = object()


def load_probe_result_for_host(
    ip_address: str,
    host_type: str,
) -> Optional[Dict[str, Any]]:
    """Return cached probe snapshot for ip_address/host_type, or None.

    host_type follows project convention: 'S' = SMB, 'F' = FTP, 'H' = HTTP.
    Value is coerced via str(), stripped, and uppercased so lowercase callers
    ('f', 'h') and non-string values (int, object) are handled safely without
    raising AttributeError. Any unrecognised value falls through to the SMB path
    (matching the 'S' default used throughout the codebase).
    Returns None immediately if ip_address is falsy.
    """
    if not ip_address:
        return None
    _ht = str(host_type or "S").strip().upper()
    if _ht == "F":
        return ftp_probe_cache.load_ftp_probe_result(ip_address)
    if _ht == "H":
        return http_probe_cache.load_http_probe_result(ip_address)
    return probe_cache.load_probe_result(ip_address)


def get_probe_snapshot_path_for_host(
    ip_address: str,
    host_type: str,
) -> Optional[str]:
    """Return cache file path string for ip_address/host_type, or None.

    F → FTP cache path string, H → HTTP cache path string.
    S, unknown types, and falsy ip all return None (SMB snapshot_path
    is never stored as a file path in this codebase).
    """
    if not ip_address:
        return None
    _ht = str(host_type or "S").strip().upper()
    if _ht == "F":
        return str(ftp_probe_cache.get_ftp_cache_path(ip_address))
    if _ht == "H":
        return str(http_probe_cache.get_http_cache_path(ip_address))
    return None


def dispatch_probe_run(
    ip_address: str,
    host_type: str,
    *,
    max_directories: int,
    max_files: int,
    timeout_seconds: int,
    cancel_event,
    port: int = 21,
    scheme: Optional[str] = None,
    db_reader=None,
    shares: Optional[List[str]] = None,
    username=_UNSET,
    password=_UNSET,
    enable_rce: bool = False,
    allow_empty: bool = False,
) -> Dict[str, Any]:
    """Dispatch a probe run to the correct protocol runner.

    Returns the raw snapshot dict from the protocol runner.
    Does not catch exceptions — callers own error handling.

    host_type: 'S' = SMB, 'F' = FTP, 'H' = HTTP (coerced/uppercased).
    username/password: omit (leave as _UNSET) to let probe_runner use its own
        defaults (DEFAULT_USERNAME = "guest"). Pass explicit values to override.
    port: FTP only — caller pre-extracts from server data. HTTP port is resolved
        from db_reader.get_http_server_detail() when scheme is None.
    """
    _ht = str(host_type or "S").strip().upper()

    if _ht == "F":
        max_entries = max(1, max_directories * max_files)
        return ftp_probe_runner.run_ftp_probe(
            ip_address,
            port=port,
            max_entries=max_entries,
            max_directories=max_directories,
            max_files=max_files,
            connect_timeout=timeout_seconds,
            request_timeout=timeout_seconds,
            cancel_event=cancel_event,
        )

    if _ht == "H":
        if scheme is None:
            _detail = db_reader.get_http_server_detail(ip_address) if db_reader else None
            port = int((_detail or {}).get("port") or 80)
            scheme = (_detail or {}).get("scheme") or "http"
        max_entries = max(1, max_directories * max_files)
        return http_probe_runner.run_http_probe(
            ip_address,
            port=port,
            scheme=scheme,
            allow_insecure_tls=True,
            max_entries=max_entries,
            max_directories=max_directories,
            max_files=max_files,
            connect_timeout=timeout_seconds,
            request_timeout=timeout_seconds,
            cancel_event=cancel_event,
        )

    # SMB path
    _kwargs: Dict[str, Any] = {
        "max_directories": max_directories,
        "max_files": max_files,
        "timeout_seconds": timeout_seconds,
        "enable_rce_analysis": enable_rce,
        "cancel_event": cancel_event,
        "allow_empty": allow_empty,
        "db_accessor": db_reader,
    }
    if username is not _UNSET:
        _kwargs["username"] = username
    if password is not _UNSET:
        _kwargs["password"] = password
    return probe_runner.run_probe(ip_address, shares or [], **_kwargs)
