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
    connect_timeout: int = 10,
    request_timeout: int = 15,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Walk the FTP root (one level deep) and return a probe snapshot dict.

    root_files and directories[].files contain filename strings (not dicts)
    so that probe_patterns._iter_snapshot_paths() can use them directly.
    directories[].subdirectories contain immediate subdirectory names only.

    Errors are non-fatal: they are collected in snapshot["errors"] and returned.
    """
    errors: List[str] = []
    root_files: List[str] = []
    root_files_truncated = False
    root_dirs: List = []
    directories: List[dict] = []

    directory_limit = max(1, int(max_directories)) if max_directories is not None else max(1, int(max_entries))
    file_limit = max(1, int(max_files)) if max_files is not None else max(1, int(max_entries))

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

            # One level deep: list each top-level directory
            for dir_entry in root_dirs[:directory_limit]:
                if cancel_event is not None and cancel_event.is_set():
                    break
                dir_path = f"/{dir_entry.name}"
                if progress_callback is not None:
                    progress_callback(f"Listing {dir_path}...")
                try:
                    sub_result = nav.list_dir(dir_path)
                    sub_dirs = [
                        e.name for e in sub_result.entries if e.is_dir
                    ]
                    sub_files = [
                        e.name for e in sub_result.entries if not e.is_dir
                    ]
                    directories.append({
                        "name": dir_entry.name,
                        "subdirectories": sub_dirs[:file_limit],
                        "subdirectories_truncated": len(sub_dirs) > file_limit,
                        "files": sub_files[:file_limit],
                        "files_truncated": sub_result.truncated or (len(sub_files) > file_limit),
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
