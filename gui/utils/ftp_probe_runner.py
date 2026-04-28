"""
FTP probe snapshot runner.

Generates a probe snapshot in the exact format that probe_patterns.py expects
from SMB probes. The synthetic share name "ftp_root" lets indicator-matching
work on FTP listings without any changes to probe_patterns.py.

Snapshot schema (mirrors SMB probe_runner.py output):
  {
    "ip_address": str,
    "port": int,
    "protocol": "ftp",
    "run_at": ISO-8601 UTC string,
    "limits": {"max_entries": int, "max_directories": int, "max_files": int, "timeout_seconds": int},
    "shares": [
      {
        "share": "ftp_root",
        "root_files": [str, ...],          # filename strings only
        "root_files_truncated": bool,
        "directories": [
          {
            "name": str,
            "subdirectories": [str, ...],    # one level only, no recursion
            "subdirectories_truncated": bool,
            "files": [str, ...],           # filename strings only
            "files_truncated": bool,
          },
          ...
        ],
        "directories_truncated": bool,
      }
    ],
    "errors": [str, ...],
  }
"""

import threading
from datetime import datetime, timezone
from typing import Callable, List, Optional

from shared.ftp_browser import FtpNavigator


def run_ftp_probe(
    ip: str,
    port: int = 21,
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
    Walk the FTP root and return a probe snapshot dict.

    root_files and directories[].files contain filename strings (not dicts)
    so that probe_patterns._iter_snapshot_paths() can use them directly.
    directories[].subdirectories and directories[].files contain relative paths
    from each sampled top-level directory.

    Errors are non-fatal: they are collected in snapshot["errors"] and returned.
    """
    errors: List[str] = []
    root_files: List[str] = []
    root_files_truncated = False
    root_dirs: List = []
    directories: List[dict] = []

    directory_limit = max(1, int(max_directories)) if max_directories is not None else max(1, int(max_entries))
    file_limit = max(1, int(max_files)) if max_files is not None else max(1, int(max_entries))
    depth_limit = min(3, max(1, int(max_depth)))
    visit_limit_per_top_level = max(1, directory_limit * depth_limit)

    nav = FtpNavigator(
        connect_timeout=float(connect_timeout),
        request_timeout=float(request_timeout),
        max_entries=max_entries,
    )

    try:
        nav.connect(ip, port)
        # Assign cancel_event AFTER connect() so connect() does not clear it.
        if cancel_event is not None:
            nav._cancel_event = cancel_event
    except Exception as exc:
        errors.append(f"connect: {exc}")
    else:
        try:
            root_result = nav.list_dir("/")
            if root_result.warning:
                errors.append(f"root listing: {root_result.warning}")

            root_dirs = [e for e in root_result.entries if e.is_dir]
            all_root_files = [e.name for e in root_result.entries if not e.is_dir]
            root_files = all_root_files[:file_limit]
            root_files_truncated = root_result.truncated or (len(all_root_files) > file_limit)

            # Traverse each top-level directory up to configured depth.
            for dir_entry in root_dirs[:directory_limit]:
                if cancel_event is not None and cancel_event.is_set():
                    break
                dir_path = f"/{dir_entry.name}"
                if progress_callback is not None:
                    progress_callback(f"Listing {dir_path}...")
                try:
                    subdirs: List[str] = []
                    files: List[str] = []
                    subdirs_truncated = False
                    files_truncated = False
                    visited_directories = 0
                    # (path relative to top-level sampled directory, depth)
                    stack: List[tuple[str, int]] = [("", 1)]

                    while stack:
                        if cancel_event is not None and cancel_event.is_set():
                            break
                        if visited_directories >= visit_limit_per_top_level:
                            subdirs_truncated = True
                            files_truncated = True
                            break

                        current_rel, current_depth = stack.pop()
                        current_dir_path = _build_abs_path(dir_path, current_rel)
                        sub_result = nav.list_dir(current_dir_path)
                        visited_directories += 1

                        child_dirs = [e.name for e in sub_result.entries if e.is_dir]
                        child_files = [e.name for e in sub_result.entries if not e.is_dir]

                        if len(child_dirs) > file_limit:
                            subdirs_truncated = True
                        if sub_result.truncated or len(child_files) > file_limit:
                            files_truncated = True

                        selected_child_dirs: List[str] = []
                        for child_name in child_dirs[:file_limit]:
                            rel_dir = _join_rel(current_rel, child_name)
                            selected_child_dirs.append(rel_dir)
                            subdirs.append(rel_dir)

                        for file_name in child_files[:file_limit]:
                            files.append(_join_rel(current_rel, file_name))

                        if current_depth < depth_limit:
                            for rel_dir in reversed(selected_child_dirs):
                                if visited_directories + len(stack) >= visit_limit_per_top_level:
                                    subdirs_truncated = True
                                    files_truncated = True
                                    break
                                stack.append((rel_dir, current_depth + 1))
                        elif selected_child_dirs:
                            subdirs_truncated = True
                            files_truncated = True

                    directories.append({
                        "name": dir_entry.name,
                        "subdirectories": subdirs,
                        "subdirectories_truncated": subdirs_truncated,
                        "files": files,
                        "files_truncated": files_truncated,
                    })
                except Exception as sub_exc:
                    errors.append(f"{dir_path}: {sub_exc}")

        except Exception as exc:
            errors.append(f"list_dir(/): {exc}")
        finally:
            try:
                nav.disconnect()
            except Exception:
                pass

    snapshot = {
        "ip_address": ip,
        "port": port,
        "protocol": "ftp",
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
                "share": "ftp_root",
                "root_files": root_files,
                "root_files_truncated": root_files_truncated,
                "directories": directories,
                "directories_truncated": len(root_dirs) > directory_limit,
            }
        ],
        "errors": errors,
    }

    return snapshot


def _join_rel(parent: str, child: str) -> str:
    parent_norm = str(parent or "").strip().strip("/")
    child_norm = str(child or "").strip().strip("/")
    if not parent_norm:
        return child_norm
    if not child_norm:
        return parent_norm
    return f"{parent_norm}/{child_norm}"


def _build_abs_path(root: str, rel: str) -> str:
    rel_norm = str(rel or "").strip().strip("/")
    if not rel_norm:
        return root
    root_norm = str(root or "/").rstrip("/")
    return f"{root_norm}/{rel_norm}"
