"""
HTTP probe snapshot runner.

Generates a probe snapshot in the exact format that probe_patterns.py expects
from SMB/FTP probes. The synthetic share name "http_root" lets indicator-matching
work on HTTP listings without changes to probe_patterns.py.

Key differences from ftp_probe_runner:
  - Stateless: no connect()/disconnect() — each request is independent.
  - errors are dicts {"share": "http_root", "message": "..."} (not plain strings)
    because the renderer at details.py calls err.get("share") / err.get("message").
  - directories[].name stored WITHOUT trailing slash; renderer appends "/" itself.

Snapshot schema:
  {
    "ip_address": str,
    "port": int,
    "scheme": str,
    "protocol": "http",
    "run_at": ISO-8601 UTC string,
    "limits": {"max_entries": int, "max_directories": int, "max_files": int,
               "timeout_seconds": int},
    "shares": [
      {
        "share": "http_root",
        "root_files": [str, ...],          # basename strings only
        "root_files_truncated": bool,
        "directories": [
          {
            "name": str,                   # basename, no trailing slash
            "subdirectories": [str, ...],  # basenames, no trailing slash
            "subdirectories_truncated": bool,
            "files": [str, ...],           # basename strings only
            "files_truncated": bool,
          },
          ...
        ],
        "directories_truncated": bool,
      }
    ],
    "errors": [{"share": "http_root", "message": str}, ...],
  }
"""

import threading
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Callable, List, Optional

from commands.http.verifier import try_http_request, validate_index_page
from gui.utils.http_probe_cache import save_http_probe_result
from shared.http_browser import _parse_dir_entries


def run_http_probe(
    ip: str,
    port: int = 80,
    scheme: str = "http",
    request_host: Optional[str] = None,
    start_path: str = "/",
    allow_insecure_tls: bool = True,
    max_entries: int = 5000,
    max_directories: Optional[int] = None,
    max_files: Optional[int] = None,
    connect_timeout: int = 10,
    request_timeout: int = 15,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Walk an HTTP listing path (one level deep) and return a probe snapshot dict.

    root_files and directories[].files contain basename strings (not dicts).
    directories[].name contains the basename without trailing slash.
    errors contains dicts {"share": "http_root", "message": str}.

    The snapshot is also persisted to http_probe_cache for later retrieval.
    Errors are non-fatal: collected in snapshot["errors"] and returned.
    """
    errors: List[dict] = []
    root_files: List[str] = []
    root_files_truncated = False
    directories: List[dict] = []
    all_dir_abs_paths: List[str] = []

    directory_limit = max(1, int(max_directories)) if max_directories is not None else max(1, int(max_entries))
    file_limit = max(1, int(max_files)) if max_files is not None else max(1, int(max_entries))

    scheme_norm = str(scheme or "http").strip().lower() or "http"
    request_host_norm = str(request_host or "").strip() or None
    start_path_norm = str(start_path or "/").split("?", 1)[0].split("#", 1)[0].strip() or "/"
    if not start_path_norm.startswith("/"):
        start_path_norm = "/" + start_path_norm.lstrip("/")

    paths_to_try = [start_path_norm]
    if start_path_norm != "/":
        paths_to_try.append("/")

    active_connect_host = ip
    active_request_host = request_host_norm
    listing_path = start_path_norm
    root_is_valid = False
    file_abs_paths: List[str] = []
    last_error_message: Optional[str] = None

    def _attempt_listing(
        path: str,
        *,
        connect_host: str,
        host_override: Optional[str],
    ) -> tuple[bool, List[str], List[str], Optional[str]]:
        status_code, body, _tls_verified, reason = try_http_request(
            connect_host,
            port,
            scheme_norm,
            allow_insecure_tls,
            float(request_timeout),
            path=path,
            request_host=host_override,
        )
        if reason:
            return False, [], [], f"{path} fetch failed: {reason}"
        if not validate_index_page(body, status_code):
            return False, [], [], f"{path} is not a directory index"
        try:
            dirs, files = _parse_dir_entries(body, current_path=path)
        except Exception as exc:
            return False, [], [], f"parse error at {path}: {exc}"
        return True, dirs, files, None

    for candidate_path in paths_to_try:
        success, dirs, files, err_msg = _attempt_listing(
            candidate_path,
            connect_host=ip,
            host_override=request_host_norm,
        )
        if success:
            root_is_valid = True
            listing_path = candidate_path
            all_dir_abs_paths = dirs
            file_abs_paths = files
            active_connect_host = ip
            active_request_host = request_host_norm
            break
        last_error_message = err_msg

        # HTTPS virtual hosts can require SNI/authority matching the requested host.
        if (
            scheme_norm == "https"
            and request_host_norm
            and request_host_norm != ip
        ):
            success, dirs, files, err_msg = _attempt_listing(
                candidate_path,
                connect_host=request_host_norm,
                host_override=request_host_norm,
            )
            if success:
                root_is_valid = True
                listing_path = candidate_path
                all_dir_abs_paths = dirs
                file_abs_paths = files
                active_connect_host = request_host_norm
                active_request_host = request_host_norm
                break
            last_error_message = err_msg

    if not root_is_valid:
        errors.append(
            {
                "share": "http_root",
                "message": last_error_message or f"{start_path_norm} is not a directory index",
            }
        )
    else:
        all_root_file_names = [PurePosixPath(p).name for p in file_abs_paths]
        root_files = all_root_file_names[:file_limit]
        root_files_truncated = len(all_root_file_names) > file_limit

        # One level deep: list each top-level directory
        for dir_abs_path in all_dir_abs_paths[:directory_limit]:
            if cancel_event is not None and cancel_event.is_set():
                break

            dir_display_name = PurePosixPath(dir_abs_path.rstrip("/")).name
            if progress_callback is not None:
                progress_callback(f"Listing {dir_abs_path}...")

            try:
                sub_status, sub_body, _tls, sub_reason = try_http_request(
                    active_connect_host,
                    port,
                    scheme_norm,
                    allow_insecure_tls,
                    float(request_timeout),
                    path=dir_abs_path,
                    request_host=active_request_host,
                )

                if sub_reason or not validate_index_page(sub_body, sub_status):
                    directories.append({
                        "name": dir_display_name,
                        "subdirectories": [],
                        "subdirectories_truncated": False,
                        "files": [],
                        "files_truncated": False,
                    })
                    continue

                sub_dir_paths, sub_file_paths = _parse_dir_entries(
                    sub_body, current_path=dir_abs_path
                )
                sub_file_names = [PurePosixPath(p).name for p in sub_file_paths]
                sub_dir_names = [
                    PurePosixPath(p.rstrip("/")).name for p in sub_dir_paths
                ]

                directories.append({
                    "name": dir_display_name,
                    "subdirectories": sub_dir_names[:file_limit],
                    "subdirectories_truncated": len(sub_dir_names) > file_limit,
                    "files": sub_file_names[:file_limit],
                    "files_truncated": len(sub_file_names) > file_limit,
                })

            except Exception as sub_exc:
                errors.append({
                    "share": "http_root",
                    "message": f"{dir_abs_path}: {sub_exc}",
                })

    snapshot = {
        "ip_address": ip,
        "port": port,
        "scheme": scheme_norm,
        "protocol": "http",
        "request_host": request_host_norm,
        "start_path": listing_path,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "limits": {
            "max_entries": max_entries,
            "max_directories": directory_limit,
            "max_files": file_limit,
            "timeout_seconds": request_timeout,
        },
        "shares": [
            {
                "share": "http_root",
                "root_files": root_files,
                "root_files_truncated": root_files_truncated,
                "directories": directories,
                "directories_truncated": root_is_valid and len(all_dir_abs_paths) > directory_limit,
            }
        ],
        "errors": errors,
    }

    save_http_probe_result(ip, snapshot, port=port)
    return snapshot
