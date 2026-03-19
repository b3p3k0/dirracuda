"""
Stateless HTTP navigator for read-only browse and download of directory-index servers.

Mirrors shared/ftp_browser.py structure but requires no persistent connection — each
public method opens a fresh HTTP request via urllib.request.

Entry.name stores the absolute path (e.g. "/pub/file.txt") so callers can pass it
directly to list_dir / download_file / read_file without path reconstruction.
HttpBrowserWindow uses Entry.name to populate _path_map; PurePosixPath(entry.name).name
gives the display label.
"""
from __future__ import annotations

import os
import ssl
import stat
import threading
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Callable, List, Optional, Tuple

from shared.smb_browser import DownloadResult, Entry, ListResult, ReadResult


# ---------------------------------------------------------------------------
# Custom exceptions (mirror ftp_browser pattern)
# ---------------------------------------------------------------------------

class HttpCancelledError(Exception):
    """Raised when a cancel_event fires during a streaming operation."""


class HttpFileTooLargeError(Exception):
    """Raised when a remote file exceeds the configured max_file_bytes limit."""


# ---------------------------------------------------------------------------
# Module-level helper: _parse_dir_entries
# ---------------------------------------------------------------------------

def _parse_dir_entries(
    body: str,
    current_path: str = "/",
) -> Tuple[List[str], List[str]]:
    """
    Parse an Apache/nginx directory-listing HTML body and return
    (dir_abs_paths, file_abs_paths) — all paths are absolute from root.

    Normalization rules:
    - Relative hrefs (e.g. "pub/", "file.txt"):
        joined with current_path using PurePosixPath arithmetic.
        "pub/" at current_path "/data/" -> "/data/pub/"
    - Root-absolute hrefs (e.g. "/pub/"):
        used AS-IS — NOT stripped or joined with current_path.
        "/pub/" at current_path "/data/" stays "/pub/" (NOT "/data/pub/")
    - Skipped: "../", "..", "?..." sort links, "//" protocol-relative,
      "://" external links.

    Returning absolute paths means all callers can use them directly with
    list_dir / download_file without secondary path arithmetic.
    """
    import re

    hrefs = re.findall(r'<a\s+href=["\']([^"\']+)["\']', body, re.IGNORECASE)

    base = str(PurePosixPath(current_path))
    if not base.endswith("/"):
        base = base + "/"

    dir_abs_paths: List[str] = []
    file_abs_paths: List[str] = []

    for href in hrefs:
        # Skip parent directory
        if href in ("../", ".."):
            continue
        # Skip sort query links
        if href.startswith("?"):
            continue
        # Skip protocol-relative links
        if href.startswith("//"):
            continue
        # Skip external links with scheme
        if "://" in href:
            continue

        if href.startswith("/"):
            # Root-absolute: use as-is
            abs_path = href
        else:
            # Relative: join with current_path
            abs_path = str(PurePosixPath(base + href))
            # PurePosixPath normalises ".." etc; preserve trailing slash for dirs
            if href.endswith("/") and not abs_path.endswith("/"):
                abs_path = abs_path + "/"

        if abs_path.endswith("/"):
            dir_abs_paths.append(abs_path)
        else:
            file_abs_paths.append(abs_path)

    return dir_abs_paths, file_abs_paths


# ---------------------------------------------------------------------------
# HttpNavigator
# ---------------------------------------------------------------------------

class HttpNavigator:
    """
    Stateless per-request HTTP navigator. Each public method opens a fresh
    HTTP request — no persistent connection, no connect()/disconnect() lifecycle.

    Entry.name stores the full absolute path (e.g. "/pub/file.txt").
    Callers pass entry.name directly to list_dir / download_file / read_file.
    """

    def __init__(
        self,
        *,
        ip: str,
        port: int,
        scheme: str,
        allow_insecure_tls: bool = True,
        connect_timeout: float = 10.0,
        request_timeout: float = 15.0,
        max_entries: int = 5000,
        max_file_bytes: int = 26_214_400,  # 25 MB
    ) -> None:
        self.ip = ip
        self.port = port
        self.scheme = scheme
        self.allow_insecure_tls = allow_insecure_tls
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.max_entries = max_entries
        self.max_file_bytes = max_file_bytes
        self._cancel_event = threading.Event()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ssl_context(self) -> Optional[ssl.SSLContext]:
        if self.scheme != "https":
            return None
        ctx = ssl.create_default_context()
        if self.allow_insecure_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _make_url(self, path: str) -> str:
        clean_path = "/" + path.lstrip("/")
        return f"{self.scheme}://{self.ip}:{self.port}{clean_path}"

    # ------------------------------------------------------------------
    # list_dir
    # ------------------------------------------------------------------

    def list_dir(self, path: str = "/") -> ListResult:
        """
        Fetch path, validate as directory index, parse hrefs into Entry objects.

        Entry.name = absolute path (e.g. "/pub/") — used directly as routing key.
        Display label = PurePosixPath(entry.name).name.

        Returns ListResult(entries=[], warning="...") when target is not a
        directory index — this is not an error, just an empty listing.
        """
        from commands.http.verifier import try_http_request, validate_index_page

        status_code, body, _tls_verified, reason = try_http_request(
            self.ip,
            self.port,
            self.scheme,
            self.allow_insecure_tls,
            self.request_timeout,
            path=path,
        )

        if reason:
            return ListResult(entries=[], truncated=False, warning=f"Request failed: {reason}")

        if not validate_index_page(body, status_code):
            return ListResult(
                entries=[],
                truncated=False,
                warning="Not a directory index listing.",
            )

        dir_abs_paths, file_abs_paths = _parse_dir_entries(body, current_path=path)

        all_entries: List[Entry] = []
        truncated = False

        for abs_path in dir_abs_paths:
            if len(all_entries) >= self.max_entries:
                truncated = True
                break
            name = abs_path  # full abs path stored in Entry.name
            all_entries.append(Entry(name=name, is_dir=True, size=0, modified_time=None))

        for abs_path in file_abs_paths:
            if len(all_entries) >= self.max_entries:
                truncated = True
                break
            all_entries.append(Entry(name=abs_path, is_dir=False, size=0, modified_time=None))

        return ListResult(entries=all_entries, truncated=truncated)

    # ------------------------------------------------------------------
    # download_file
    # ------------------------------------------------------------------

    def download_file(
        self,
        remote_path: str,
        dest_dir: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> DownloadResult:
        """
        Stream-download remote_path to dest_dir / basename.

        - Enforces max_file_bytes; raises HttpFileTooLargeError if exceeded.
        - Strips executable bits on the saved file.
        - Sets mtime from Last-Modified header if present.
        - Respects cancel_event; removes partial file on cancel.
        - Raises HttpCancelledError on cancel.
        """
        if self._cancel_event.is_set():
            raise HttpCancelledError("Cancelled before download started")

        url = self._make_url(remote_path)
        ctx = self._ssl_context()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

        basename = PurePosixPath(remote_path).name or "download"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / basename

        if dest_path.exists():
            raise FileExistsError(f"{dest_path} already exists")

        start_time = time.monotonic()
        downloaded = 0
        mtime: Optional[float] = None

        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout, context=ctx) as resp:
                # Extract mtime from Last-Modified header
                last_modified = resp.headers.get("Last-Modified")
                if last_modified:
                    try:
                        mtime = parsedate_to_datetime(last_modified).timestamp()
                    except Exception:
                        mtime = None

                with open(dest_path, "wb") as fh:
                    chunk_size = 8 * 1024
                    while True:
                        if self._cancel_event.is_set():
                            fh.close()
                            try:
                                dest_path.unlink()
                            except OSError:
                                pass
                            raise HttpCancelledError("Download cancelled")

                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break

                        downloaded += len(chunk)
                        if downloaded > self.max_file_bytes:
                            fh.close()
                            try:
                                dest_path.unlink()
                            except OSError:
                                pass
                            raise HttpFileTooLargeError(
                                f"File exceeds {self.max_file_bytes} byte limit"
                            )

                        fh.write(chunk)
                        if progress_callback is not None:
                            try:
                                progress_callback(downloaded, 0)
                            except Exception:
                                pass

        except (HttpCancelledError, HttpFileTooLargeError, FileExistsError):
            raise
        except Exception as exc:
            try:
                dest_path.unlink()
            except OSError:
                pass
            raise RuntimeError(f"Download failed: {exc}") from exc

        # Strip executable bits
        try:
            current_mode = stat.S_IMODE(os.stat(dest_path).st_mode)
            os.chmod(dest_path, current_mode & 0o666)
        except OSError:
            pass

        # Set mtime from Last-Modified header
        if mtime is not None:
            try:
                os.utime(dest_path, (mtime, mtime))
            except OSError:
                pass

        elapsed = time.monotonic() - start_time
        return DownloadResult(saved_path=dest_path, size=downloaded, elapsed_seconds=elapsed, mtime=mtime)

    # ------------------------------------------------------------------
    # read_file
    # ------------------------------------------------------------------

    def read_file(self, remote_path: str, max_bytes: int = 5 * 1024 * 1024) -> ReadResult:
        """Fetch and return up to max_bytes as bytes."""
        url = self._make_url(remote_path)
        ctx = self._ssl_context()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout, context=ctx) as resp:
                data = resp.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            try:
                data = exc.read(max_bytes + 1)
            except Exception:
                data = b""
        except Exception as exc:
            raise RuntimeError(f"Read failed: {exc}") from exc

        truncated = len(data) > max_bytes
        if truncated:
            data = data[:max_bytes]

        return ReadResult(data=data, size=len(data), truncated=truncated)

    # ------------------------------------------------------------------
    # cancel
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Set internal cancel event to abort any in-flight streaming operation."""
        self._cancel_event.set()
