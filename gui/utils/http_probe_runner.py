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
    max_depth: int = 1,
    connect_timeout: int = 10,
    request_timeout: int = 15,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Walk an HTTP listing path and return a probe snapshot dict.

    root_files and directories[].files contain relative path strings.
    directories[].name contains the basename without trailing slash.
    errors contains dicts {"share": "http_root", "message": str}.

    Errors are non-fatal: collected in snapshot["errors"] and returned.
    """
    errors: List[dict] = []
    root_files: List[str] = []
    root_files_truncated = False
    directories: List[dict] = []
    all_dir_abs_paths: List[str] = []

    directory_limit = max(1, int(max_directories)) if max_directories is not None else max(1, int(max_entries))
    file_limit = max(1, int(max_files)) if max_files is not None else max(1, int(max_entries))
    depth_limit = min(3, max(1, int(max_depth)))
    visit_limit_per_top_level = max(1, directory_limit * depth_limit)

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

        # Traverse each top-level directory up to configured depth.
        for dir_abs_path in all_dir_abs_paths[:directory_limit]:
            if cancel_event is not None and cancel_event.is_set():
                break

            dir_display_name = PurePosixPath(dir_abs_path.rstrip("/")).name
            if progress_callback is not None:
                progress_callback(f"Listing {dir_abs_path}...")

            try:
                subdirectories: List[str] = []
                files: List[str] = []
                subdirs_truncated = False
                files_truncated = False
                visited_directories = 0
                # (relative path from top-level dir, depth)
                stack: List[tuple[str, int]] = [("", 1)]

                while stack:
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    if visited_directories >= visit_limit_per_top_level:
                        subdirs_truncated = True
                        files_truncated = True
                        break

                    current_rel_path, current_depth = stack.pop()
                    current_abs_path = _join_abs_path(dir_abs_path, current_rel_path)
                    sub_status, sub_body, _tls, sub_reason = try_http_request(
                        active_connect_host,
                        port,
                        scheme_norm,
                        allow_insecure_tls,
                        float(request_timeout),
                        path=current_abs_path,
                        request_host=active_request_host,
                    )

                    if sub_reason or not validate_index_page(sub_body, sub_status):
                        visited_directories += 1
                        continue

                    sub_dir_paths, sub_file_paths = _parse_dir_entries(
                        sub_body, current_path=current_abs_path
                    )
                    visited_directories += 1

                    if len(sub_dir_paths) > file_limit:
                        subdirs_truncated = True
                    if len(sub_file_paths) > file_limit:
                        files_truncated = True

                    selected_child_dirs: List[str] = []
                    for child_abs in sub_dir_paths[:file_limit]:
                        child_rel = _to_relative_path(child_abs, dir_abs_path)
                        if not child_rel:
                            continue
                        selected_child_dirs.append(child_rel)
                        subdirectories.append(child_rel)

                    for file_abs in sub_file_paths[:file_limit]:
                        file_rel = _to_relative_path(file_abs, dir_abs_path)
                        if file_rel:
                            files.append(file_rel)

                    if current_depth < depth_limit:
                        for child_rel in reversed(selected_child_dirs):
                            if visited_directories + len(stack) >= visit_limit_per_top_level:
                                subdirs_truncated = True
                                files_truncated = True
                                break
                            stack.append((child_rel, current_depth + 1))
                    elif selected_child_dirs:
                        subdirs_truncated = True
                        files_truncated = True

                directories.append({
                    "name": dir_display_name,
                    "subdirectories": subdirectories,
                    "subdirectories_truncated": subdirs_truncated,
                    "files": files,
                    "files_truncated": files_truncated,
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
            "max_depth": depth_limit,
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

    return snapshot


def _to_relative_path(path_abs: str, base_abs: str) -> str:
    path_norm = str(path_abs or "").split("?", 1)[0].split("#", 1)[0].strip() or "/"
    base_norm = str(base_abs or "/").split("?", 1)[0].split("#", 1)[0].strip() or "/"
    if not path_norm.startswith("/"):
        path_norm = "/" + path_norm.lstrip("/")
    if not base_norm.startswith("/"):
        base_norm = "/" + base_norm.lstrip("/")
    base_prefix = base_norm.rstrip("/")
    if base_prefix and path_norm.startswith(base_prefix + "/"):
        rel = path_norm[len(base_prefix) + 1 :]
    elif path_norm == base_prefix:
        rel = ""
    else:
        rel = path_norm.lstrip("/")
    return rel.rstrip("/")


def _join_abs_path(root_abs: str, rel_path: str) -> str:
    root_norm = str(root_abs or "/").strip() or "/"
    if not root_norm.startswith("/"):
        root_norm = "/" + root_norm.lstrip("/")
    rel_norm = str(rel_path or "").strip().strip("/")
    if not rel_norm:
        return root_norm
    return f"{root_norm.rstrip('/')}/{rel_norm}"
