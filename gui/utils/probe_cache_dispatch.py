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
from gui.utils.database_access import DatabaseReader
from gui.utils.settings_manager import get_settings_manager

_UNSET = object()
_DB_READER_CACHE: Dict[str, Any] = {"path": None, "reader": None}


def _get_cached_db_reader() -> Optional[DatabaseReader]:
    """Return DatabaseReader bound to active settings DB path, cached by path."""
    try:
        settings = get_settings_manager()
        db_path = settings.get_database_path() if hasattr(settings, "get_database_path") else None
        if not db_path and hasattr(settings, "get_setting"):
            db_path = settings.get_setting("backend.database_path", None)
        db_path = str(db_path or "").strip()
        if not db_path:
            return None
        if _DB_READER_CACHE.get("path") != db_path or _DB_READER_CACHE.get("reader") is None:
            _DB_READER_CACHE["path"] = db_path
            _DB_READER_CACHE["reader"] = DatabaseReader(db_path)
        return _DB_READER_CACHE.get("reader")
    except Exception:
        return None


def load_probe_result_for_host(
    ip_address: str,
    host_type: str,
    *,
    port: Optional[int] = None,
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
    db_reader = _get_cached_db_reader()
    if db_reader is not None:
        try:
            snapshot = db_reader.get_probe_snapshot_for_host(
                ip_address,
                _ht,
                port=port,
            )
            if isinstance(snapshot, dict):
                return snapshot
        except Exception:
            pass
    if _ht == "F":
        return ftp_probe_cache.load_ftp_probe_result(ip_address)
    if _ht == "H":
        if port is None:
            return http_probe_cache.load_http_probe_result(ip_address)
        return http_probe_cache.load_http_probe_result(ip_address, port=port)
    return probe_cache.load_probe_result(ip_address)


def get_probe_snapshot_path_for_host(
    ip_address: str,
    host_type: str,
    *,
    port: Optional[int] = None,
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
        if port is None:
            return str(http_probe_cache.get_http_cache_path(ip_address))
        return str(http_probe_cache.get_http_cache_path(ip_address, port=port))
    return None


def dispatch_probe_run(
    ip_address: str,
    host_type: str,
    *,
    max_directories: int,
    max_files: int,
    timeout_seconds: int,
    cancel_event,
    max_depth: int = 1,
    port: Optional[int] = None,
    scheme: Optional[str] = None,
    request_host: Optional[str] = None,
    start_path: Optional[str] = None,
    protocol_server_id: Optional[int] = None,
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
    max_depth: Probe recursion depth, clamped to 1..3.
    port: caller-selected endpoint port (FTP or HTTP). When omitted for HTTP,
        db_reader.get_http_server_detail() is used as fallback.
    request_host/start_path: optional HTTP probe hints. When omitted for HTTP,
        db_reader.get_http_server_detail() may provide probe_host/probe_path.
    """
    _ht = str(host_type or "S").strip().upper()
    depth_limit = min(3, max(1, int(max_depth)))

    if _ht == "F":
        try:
            ftp_port = int(port) if port is not None else 21
        except (TypeError, ValueError):
            ftp_port = 21
        max_entries = max(1, max_directories * max_files)
        return ftp_probe_runner.run_ftp_probe(
            ip_address,
            port=ftp_port,
            max_entries=max_entries,
            max_directories=max_directories,
            max_files=max_files,
            connect_timeout=timeout_seconds,
            request_timeout=timeout_seconds,
            cancel_event=cancel_event,
            max_depth=depth_limit,
        )

    if _ht == "H":
        detail: Optional[Dict[str, Any]] = None
        try:
            http_port = int(port) if port is not None else None
        except (TypeError, ValueError):
            http_port = None

        if db_reader and (scheme is None or http_port is None):
            if protocol_server_id is None and http_port is None:
                detail = db_reader.get_http_server_detail(ip_address)
            else:
                detail = db_reader.get_http_server_detail(
                    ip_address,
                    protocol_server_id=protocol_server_id,
                    port=http_port,
                )

        if http_port is None:
            http_port = int((detail or {}).get("port") or 80)
        if scheme is None:
            scheme = (detail or {}).get("scheme") or ("https" if http_port == 443 else "http")
        if request_host is None:
            request_host = (detail or {}).get("probe_host") or None
        if start_path is None:
            start_path = (detail or {}).get("probe_path") or "/"
        start_path = str(start_path or "/").split("?", 1)[0].split("#", 1)[0].strip() or "/"
        if not start_path.startswith("/"):
            start_path = "/" + start_path.lstrip("/")

        max_entries = max(1, max_directories * max_files)
        return http_probe_runner.run_http_probe(
            ip_address,
            port=http_port,
            scheme=scheme,
            request_host=request_host,
            start_path=start_path,
            allow_insecure_tls=True,
            max_entries=max_entries,
            max_directories=max_directories,
            max_files=max_files,
            connect_timeout=timeout_seconds,
            request_timeout=timeout_seconds,
            cancel_event=cancel_event,
            max_depth=depth_limit,
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
        "max_depth": depth_limit,
    }
    if username is not _UNSET:
        _kwargs["username"] = username
    if password is not _UNSET:
        _kwargs["password"] = password
    return probe_runner.run_probe(ip_address, shares or [], **_kwargs)
