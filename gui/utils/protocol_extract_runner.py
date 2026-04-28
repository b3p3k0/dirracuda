"""Protocol-native bulk extract runners for FTP and HTTP/HTTPS.

These runners mirror the SMB extract summary contract used by
``gui.utils.extract_runner.run_extract`` so existing callers can reuse
status rendering and extract log persistence.
"""

from __future__ import annotations

import datetime as _dt
import os
import stat
import time
import urllib.request
import ssl
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from threading import Event
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from commands.http.verifier import try_http_request, validate_index_page
from gui.utils.extract_runner import (
    ExtractError,
    _normalize_extensions,
    _should_download_file,
    build_browser_download_clamav_setup,
    update_browser_clamav_accum,
)
from shared.ftp_browser import FtpCancelledError, FtpFileTooLargeError, FtpNavigator
from shared.http_browser import _parse_dir_entries
from shared.quarantine import log_quarantine_event
from shared.quarantine_postprocess import PostProcessInput


class _TotalSizeLimitExceeded(Exception):
    """Raised when a download would exceed the remaining global byte budget."""


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _check_cancel(cancel_event: Optional[Event]) -> None:
    if cancel_event and cancel_event.is_set():
        raise ExtractError("Extraction cancelled")


def _safe_rel_parts(path_value: str) -> List[str]:
    parts: List[str] = []
    for segment in str(path_value or "").replace("\\", "/").split("/"):
        cleaned = segment.strip()
        if not cleaned or cleaned in {".", ".."}:
            continue
        parts.append(cleaned)
    return parts


def _build_summary(
    ip_address: str,
    share_name: str,
    download_root: Path,
    *,
    max_total_bytes: int,
    max_file_bytes: int,
    max_file_count: int,
    max_seconds: int,
    max_depth: int,
    mode: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "ip_address": ip_address,
        "shares_requested": [share_name],
        "download_root": str(download_root),
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
    if extra:
        payload.update(extra)
    return payload


def _finalize_summary(summary: Dict[str, Any], clamav_accum: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    summary["finished_at"] = _utcnow()
    summary["clamav"] = clamav_accum if clamav_accum is not None else {"enabled": False}
    return summary


def _append_skip(
    summary: Dict[str, Any],
    share_name: str,
    rel_display: str,
    reason: str,
    size: int,
) -> None:
    summary["totals"]["files_skipped"] += 1
    summary["skipped"].append(
        {
            "share": share_name,
            "path": rel_display,
            "reason": reason,
            "size": int(size or 0),
        }
    )


def _append_error(summary: Dict[str, Any], share_name: str, rel_display: str, message: str) -> None:
    summary["errors"].append(
        {
            "share": share_name,
            "path": rel_display,
            "message": message,
        }
    )


def run_ftp_extract(
    ip_address: str,
    *,
    port: int,
    download_dir: Path,
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
    clamav_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Extract files recursively from anonymous FTP starting at '/'."""
    _check_cancel(cancel_event)

    mode = (extension_mode or "legacy").lower()
    if mode not in {"download_all", "allow_only", "deny_only", "legacy"}:
        mode = "legacy"

    share_name = "ftp_root"
    ftp_root_dir = Path(download_dir) / share_name
    ftp_root_dir.mkdir(parents=True, exist_ok=True)

    summary = _build_summary(
        ip_address,
        share_name,
        download_root=Path(download_dir),
        max_total_bytes=max_total_bytes,
        max_file_bytes=max_file_bytes,
        max_file_count=max_file_count,
        max_seconds=max_seconds,
        max_depth=max_depth,
        mode=mode,
        extra={
            "protocol": "ftp",
            "port": int(port),
        },
    )

    allowed_set = _normalize_extensions(allowed_extensions)
    denied_set = _normalize_extensions(denied_extensions)

    pp, clamav_accum, clamav_init_err = build_browser_download_clamav_setup(
        clamav_config,
        ip_address,
        ftp_root_dir,
        share_name,
    )
    if clamav_init_err:
        _append_error(summary, share_name, "(clamav-init)", clamav_init_err)

    start_time = time.time()
    total_files = 0
    total_bytes = 0

    nav = FtpNavigator(
        connect_timeout=float(max(1, int(connection_timeout))),
        request_timeout=float(max(1, int(connection_timeout))),
        max_entries=5000,
        max_depth=max(2, int(max_depth) + 1),
        max_path_length=1024,
        max_file_bytes=max(1, int(max_file_bytes)) if max_file_bytes > 0 else 2**63 - 1,
    )
    if cancel_event is not None:
        nav._cancel_event = cancel_event

    stack: List[Tuple[str, int]] = [("/", 0)]
    visited: Set[str] = set()

    try:
        nav.connect(ip_address, int(port))

        while stack:
            _check_cancel(cancel_event)
            if max_seconds > 0 and (time.time() - start_time) >= max_seconds:
                summary["timed_out"] = True
                summary["stop_reason"] = "time_limit"
                break

            current_dir, depth = stack.pop()
            current_dir_norm = "/" + str(PurePosixPath(current_dir)).lstrip("/")
            if current_dir_norm != "/" and current_dir_norm.endswith("/"):
                current_dir_norm = current_dir_norm.rstrip("/")
            if current_dir_norm in visited:
                continue
            visited.add(current_dir_norm)

            try:
                listing = nav.list_dir(current_dir_norm)
            except Exception as exc:
                _append_error(summary, share_name, current_dir_norm, f"List failed: {exc}")
                continue

            if listing.warning:
                _append_error(summary, share_name, current_dir_norm, str(listing.warning))

            for entry in listing.entries:
                _check_cancel(cancel_event)
                if max_seconds > 0 and (time.time() - start_time) >= max_seconds:
                    summary["timed_out"] = True
                    summary["stop_reason"] = "time_limit"
                    break

                entry_abs = str(PurePosixPath(current_dir_norm) / entry.name)
                if entry.is_dir:
                    if depth < max_depth:
                        stack.append((entry_abs, depth + 1))
                    continue

                rel_parts = _safe_rel_parts(entry_abs)
                if not rel_parts:
                    rel_parts = [PurePosixPath(entry_abs).name or "download"]
                rel_display = "/".join(rel_parts)

                file_size = int(getattr(entry, "size", 0) or 0)
                should_download, skip_reason = _should_download_file(
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
                    _append_skip(summary, share_name, rel_display, str(skip_reason), file_size)
                    if skip_reason == "total_size_limit":
                        summary["stop_reason"] = "total_size_limit"
                        break
                    continue

                if max_file_count > 0 and total_files >= max_file_count:
                    summary["stop_reason"] = "file_limit"
                    break

                destination = ftp_root_dir.joinpath(*rel_parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.unlink(missing_ok=True)

                current_index = total_files + 1
                if progress_callback:
                    progress_callback(rel_display, current_index, max_file_count or None)

                try:
                    result = nav.download_file(entry_abs, destination.parent)
                except FileExistsError:
                    destination.unlink(missing_ok=True)
                    result = nav.download_file(entry_abs, destination.parent)
                except FtpCancelledError as exc:
                    raise ExtractError(str(exc)) from exc
                except FtpFileTooLargeError:
                    _append_skip(summary, share_name, rel_display, "file_too_large", file_size)
                    continue
                except Exception as exc:
                    _append_error(summary, share_name, rel_display, f"Download failed: {exc}")
                    continue

                saved_path = Path(result.saved_path)
                downloaded_size = int(result.size or 0)

                if max_total_bytes > 0 and (total_bytes + downloaded_size) > max_total_bytes:
                    saved_path.unlink(missing_ok=True)
                    _append_skip(summary, share_name, rel_display, "total_size_limit", downloaded_size)
                    summary["stop_reason"] = "total_size_limit"
                    break

                if getattr(entry, "modified_time", None) is not None:
                    try:
                        mtime = float(entry.modified_time)
                        os.utime(saved_path, (mtime, mtime))
                    except Exception:
                        pass

                final_path = saved_path
                if pp is not None and clamav_accum is not None:
                    try:
                        pp_result = pp(
                            PostProcessInput(
                                file_path=saved_path,
                                ip_address=ip_address,
                                share=share_name,
                                rel_display=rel_display,
                                file_size=downloaded_size,
                            )
                        )
                        final_path = pp_result.final_path
                        update_browser_clamav_accum(clamav_accum, pp_result, rel_display)
                    except Exception as exc:
                        _append_error(
                            summary,
                            share_name,
                            rel_display,
                            f"post_processor error (file kept in quarantine): {exc}",
                        )
                        if clamav_accum is not None:
                            clamav_accum["errors"] += 1
                            clamav_accum["error_items"].append(
                                {"path": rel_display, "error": str(exc)}
                            )

                total_files += 1
                total_bytes += downloaded_size

                summary["files"].append(
                    {
                        "share": share_name,
                        "path": rel_display,
                        "size": downloaded_size,
                        "saved_to": str(final_path),
                    }
                )
                try:
                    log_quarantine_event(download_dir, f"extracted {share_name}/{rel_display} -> {final_path}")
                except Exception:
                    pass

                if delay_seconds > 0:
                    _check_cancel(cancel_event)
                    time.sleep(delay_seconds)

            if summary.get("stop_reason") in {"time_limit", "file_limit", "total_size_limit"}:
                break

    finally:
        try:
            nav.disconnect()
        except Exception:
            pass

    summary["totals"]["files_downloaded"] = total_files
    summary["totals"]["bytes_downloaded"] = total_bytes
    return _finalize_summary(summary, clamav_accum)


def _build_http_context(scheme: str, allow_insecure_tls: bool) -> Optional[ssl.SSLContext]:
    if scheme != "https":
        return None
    ctx = ssl.create_default_context()
    if allow_insecure_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _normalize_host_header(request_host: Optional[str], scheme: str, port: int) -> Optional[str]:
    host_header = str(request_host or "").strip()
    if not host_header:
        return None
    if ":" not in host_header and not (
        host_header.startswith("[") and host_header.endswith("]")
    ):
        default_port = 443 if scheme == "https" else 80
        if port != default_port:
            host_header = f"{host_header}:{port}"
    return host_header


def _http_fetch_listing(
    *,
    connect_host: str,
    request_host: Optional[str],
    port: int,
    scheme: str,
    allow_insecure_tls: bool,
    timeout_seconds: int,
    path: str,
) -> Tuple[bool, List[str], List[str], Optional[str]]:
    status, body, _tls_verified, reason = try_http_request(
        connect_host,
        int(port),
        scheme,
        allow_insecure_tls,
        float(timeout_seconds),
        path=path,
        request_host=request_host,
    )
    if reason:
        return False, [], [], f"{path} fetch failed: {reason}"
    if not validate_index_page(body, status):
        return False, [], [], f"{path} is not a directory index"
    try:
        dirs, files = _parse_dir_entries(body, current_path=path)
    except Exception as exc:
        return False, [], [], f"{path} parse failed: {exc}"
    return True, dirs, files, None


def _http_download_file(
    *,
    connect_host: str,
    request_host: Optional[str],
    port: int,
    scheme: str,
    allow_insecure_tls: bool,
    timeout_seconds: int,
    remote_path: str,
    dest_path: Path,
    max_file_bytes: int,
    max_total_remaining: Optional[int],
    cancel_event: Optional[Event],
    progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
) -> Tuple[int, Optional[float]]:
    _check_cancel(cancel_event)

    normalized_path = "/" + str(remote_path or "/").lstrip("/")
    url = f"{scheme}://{connect_host}:{int(port)}{normalized_path}"
    headers = {"User-Agent": "Mozilla/5.0"}
    host_header = _normalize_host_header(request_host, scheme, int(port))
    if host_header:
        headers["Host"] = host_header

    req = urllib.request.Request(url, headers=headers)
    ctx = _build_http_context(scheme, allow_insecure_tls)

    downloaded = 0
    mtime: Optional[float] = None

    try:
        with urllib.request.urlopen(req, timeout=float(timeout_seconds), context=ctx) as resp:
            last_modified = resp.headers.get("Last-Modified")
            if last_modified:
                try:
                    mtime = parsedate_to_datetime(last_modified).timestamp()
                except Exception:
                    mtime = None

            with open(dest_path, "wb") as fh:
                while True:
                    _check_cancel(cancel_event)
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break

                    next_size = downloaded + len(chunk)
                    if max_file_bytes > 0 and next_size > max_file_bytes:
                        raise FtpFileTooLargeError(
                            f"{normalized_path}: exceeds per-file limit {max_file_bytes}"
                        )
                    if max_total_remaining is not None and next_size > max_total_remaining:
                        raise _TotalSizeLimitExceeded("total_size_limit")

                    fh.write(chunk)
                    downloaded = next_size
                    if progress_callback is not None:
                        try:
                            progress_callback(downloaded, None)
                        except Exception:
                            pass
    except _TotalSizeLimitExceeded:
        dest_path.unlink(missing_ok=True)
        raise
    except FtpFileTooLargeError:
        dest_path.unlink(missing_ok=True)
        raise
    except ExtractError:
        dest_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        dest_path.unlink(missing_ok=True)
        raise RuntimeError(f"Download failed: {exc}") from exc

    try:
        current_mode = stat.S_IMODE(os.stat(dest_path).st_mode)
        os.chmod(dest_path, current_mode & 0o666)
    except OSError:
        pass

    if mtime is not None:
        try:
            os.utime(dest_path, (mtime, mtime))
        except OSError:
            pass

    return downloaded, mtime


def run_http_extract(
    ip_address: str,
    *,
    port: int,
    scheme: str,
    request_host: Optional[str],
    start_path: str,
    allow_insecure_tls: bool,
    download_dir: Path,
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
    clamav_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Extract files recursively from an HTTP/HTTPS directory index endpoint."""
    _check_cancel(cancel_event)

    mode = (extension_mode or "legacy").lower()
    if mode not in {"download_all", "allow_only", "deny_only", "legacy"}:
        mode = "legacy"

    scheme_norm = str(scheme or "http").strip().lower() or "http"
    if scheme_norm not in {"http", "https"}:
        scheme_norm = "https" if int(port) == 443 else "http"

    start_path_norm = str(start_path or "/").split("?", 1)[0].split("#", 1)[0].strip() or "/"
    if not start_path_norm.startswith("/"):
        start_path_norm = "/" + start_path_norm.lstrip("/")

    share_name = "http_root"
    http_root_dir = Path(download_dir) / share_name
    http_root_dir.mkdir(parents=True, exist_ok=True)

    summary = _build_summary(
        ip_address,
        share_name,
        download_root=Path(download_dir),
        max_total_bytes=max_total_bytes,
        max_file_bytes=max_file_bytes,
        max_file_count=max_file_count,
        max_seconds=max_seconds,
        max_depth=max_depth,
        mode=mode,
        extra={
            "protocol": "http",
            "scheme": scheme_norm,
            "port": int(port),
            "request_host": request_host,
            "start_path": start_path_norm,
        },
    )

    allowed_set = _normalize_extensions(allowed_extensions)
    denied_set = _normalize_extensions(denied_extensions)

    pp, clamav_accum, clamav_init_err = build_browser_download_clamav_setup(
        clamav_config,
        ip_address,
        http_root_dir,
        share_name,
    )
    if clamav_init_err:
        _append_error(summary, share_name, "(clamav-init)", clamav_init_err)

    start_time = time.time()
    total_files = 0
    total_bytes = 0

    active_connect_host = ip_address
    active_request_host = str(request_host or "").strip() or None

    paths_to_try = [start_path_norm]
    if start_path_norm != "/":
        paths_to_try.append("/")

    root_listing_ok = False
    root_dirs: List[str] = []
    root_files: List[str] = []
    root_path_in_use = start_path_norm
    last_root_error: Optional[str] = None

    for candidate in paths_to_try:
        ok, dirs, files, err = _http_fetch_listing(
            connect_host=ip_address,
            request_host=active_request_host,
            port=int(port),
            scheme=scheme_norm,
            allow_insecure_tls=allow_insecure_tls,
            timeout_seconds=max(1, int(connection_timeout)),
            path=candidate,
        )
        if ok:
            root_listing_ok = True
            root_dirs, root_files = dirs, files
            root_path_in_use = candidate
            active_connect_host = ip_address
            break
        last_root_error = err

        if (
            scheme_norm == "https"
            and active_request_host
            and active_request_host != ip_address
        ):
            ok, dirs, files, err = _http_fetch_listing(
                connect_host=active_request_host,
                request_host=active_request_host,
                port=int(port),
                scheme=scheme_norm,
                allow_insecure_tls=allow_insecure_tls,
                timeout_seconds=max(1, int(connection_timeout)),
                path=candidate,
            )
            if ok:
                root_listing_ok = True
                root_dirs, root_files = dirs, files
                root_path_in_use = candidate
                active_connect_host = active_request_host
                break
            last_root_error = err

    if not root_listing_ok:
        _append_error(
            summary,
            share_name,
            root_path_in_use,
            last_root_error or f"{root_path_in_use} is not a directory index",
        )
        summary["totals"]["files_downloaded"] = 0
        summary["totals"]["bytes_downloaded"] = 0
        return _finalize_summary(summary, clamav_accum)

    stack: List[Tuple[str, int, Optional[List[str]], Optional[List[str]]]] = [
        (root_path_in_use, 0, root_dirs, root_files)
    ]
    visited: Set[str] = set()

    while stack:
        _check_cancel(cancel_event)
        if max_seconds > 0 and (time.time() - start_time) >= max_seconds:
            summary["timed_out"] = True
            summary["stop_reason"] = "time_limit"
            break

        current_dir, depth, seeded_dirs, seeded_files = stack.pop()
        current_dir_norm = "/" + str(PurePosixPath(current_dir)).lstrip("/")
        if current_dir_norm != "/" and current_dir_norm.endswith("/"):
            current_dir_norm = current_dir_norm.rstrip("/")
        if current_dir_norm in visited:
            continue
        visited.add(current_dir_norm)

        if seeded_dirs is not None and seeded_files is not None:
            dir_paths = list(seeded_dirs)
            file_paths = list(seeded_files)
        else:
            ok, dir_paths, file_paths, err = _http_fetch_listing(
                connect_host=active_connect_host,
                request_host=active_request_host,
                port=int(port),
                scheme=scheme_norm,
                allow_insecure_tls=allow_insecure_tls,
                timeout_seconds=max(1, int(connection_timeout)),
                path=current_dir_norm,
            )
            if not ok:
                _append_error(summary, share_name, current_dir_norm, err or "listing failed")
                continue

        for file_abs in file_paths:
            _check_cancel(cancel_event)
            if max_seconds > 0 and (time.time() - start_time) >= max_seconds:
                summary["timed_out"] = True
                summary["stop_reason"] = "time_limit"
                break

            rel_parts = _safe_rel_parts(file_abs)
            if not rel_parts:
                rel_parts = [PurePosixPath(file_abs).name or "download"]
            rel_display = "/".join(rel_parts)

            should_download, skip_reason = _should_download_file(
                rel_display,
                0,
                allowed_set,
                denied_set,
                mode,
                max_file_bytes,
                max_total_bytes,
                total_bytes,
            )
            if not should_download:
                _append_skip(summary, share_name, rel_display, str(skip_reason), 0)
                if skip_reason == "total_size_limit":
                    summary["stop_reason"] = "total_size_limit"
                    break
                continue

            if max_file_count > 0 and total_files >= max_file_count:
                summary["stop_reason"] = "file_limit"
                break

            destination = http_root_dir.joinpath(*rel_parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.unlink(missing_ok=True)

            current_index = total_files + 1
            if progress_callback:
                progress_callback(rel_display, current_index, max_file_count or None)

            remaining_budget = None
            if max_total_bytes > 0:
                remaining_budget = max(0, max_total_bytes - total_bytes)
                if remaining_budget <= 0:
                    _append_skip(summary, share_name, rel_display, "total_size_limit", 0)
                    summary["stop_reason"] = "total_size_limit"
                    break

            try:
                downloaded_size, _mtime = _http_download_file(
                    connect_host=active_connect_host,
                    request_host=active_request_host,
                    port=int(port),
                    scheme=scheme_norm,
                    allow_insecure_tls=allow_insecure_tls,
                    timeout_seconds=max(1, int(connection_timeout)),
                    remote_path=file_abs,
                    dest_path=destination,
                    max_file_bytes=max_file_bytes,
                    max_total_remaining=remaining_budget,
                    cancel_event=cancel_event,
                    progress_callback=None,
                )
            except _TotalSizeLimitExceeded:
                _append_skip(summary, share_name, rel_display, "total_size_limit", 0)
                summary["stop_reason"] = "total_size_limit"
                break
            except FtpFileTooLargeError:
                _append_skip(summary, share_name, rel_display, "file_too_large", 0)
                continue
            except ExtractError:
                raise
            except Exception as exc:
                _append_error(summary, share_name, rel_display, f"Download failed: {exc}")
                continue

            final_path = destination
            if pp is not None and clamav_accum is not None:
                try:
                    pp_result = pp(
                        PostProcessInput(
                            file_path=destination,
                            ip_address=ip_address,
                            share=share_name,
                            rel_display=rel_display,
                            file_size=downloaded_size,
                        )
                    )
                    final_path = pp_result.final_path
                    update_browser_clamav_accum(clamav_accum, pp_result, rel_display)
                except Exception as exc:
                    _append_error(
                        summary,
                        share_name,
                        rel_display,
                        f"post_processor error (file kept in quarantine): {exc}",
                    )
                    if clamav_accum is not None:
                        clamav_accum["errors"] += 1
                        clamav_accum["error_items"].append(
                            {"path": rel_display, "error": str(exc)}
                        )

            total_files += 1
            total_bytes += downloaded_size

            summary["files"].append(
                {
                    "share": share_name,
                    "path": rel_display,
                    "size": downloaded_size,
                    "saved_to": str(final_path),
                }
            )
            try:
                log_quarantine_event(download_dir, f"extracted {share_name}/{rel_display} -> {final_path}")
            except Exception:
                pass

            if delay_seconds > 0:
                _check_cancel(cancel_event)
                time.sleep(delay_seconds)

        if summary.get("stop_reason") in {"time_limit", "file_limit", "total_size_limit"}:
            break

        if depth < max_depth:
            for child_dir in dir_paths:
                child_norm = "/" + str(PurePosixPath(child_dir)).lstrip("/")
                if child_norm != "/" and child_norm.endswith("/"):
                    child_norm = child_norm.rstrip("/")
                if child_norm not in visited:
                    stack.append((child_norm, depth + 1, None, None))

    summary["totals"]["files_downloaded"] = total_files
    summary["totals"]["bytes_downloaded"] = total_bytes
    return _finalize_summary(summary, clamav_accum)
