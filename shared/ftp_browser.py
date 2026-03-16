"""
Read-only FTP navigation + download helper for the Tkinter FTP file browser.

Goals:
 - Anonymous FTP only (USER anonymous / PASS anonymous@).
 - MLSD-first listing with LIST fallback for broad server compatibility.
 - Hard limits (entries, depth, path length, file size, timeouts) enforced here.
 - Cancellation support via threading.Event; cancelled download removes partial file.
 - After any cancel, connection is set to None so _ensure_connected() reconnects cleanly.
"""

from __future__ import annotations

import calendar
import ftplib
import io
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Callable, List, Optional, Tuple

from shared.smb_browser import Entry, ListResult, DownloadResult, ReadResult


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class FtpCancelledError(Exception):
    """Raised by RETR callback when cancel_event is set during a transfer."""


class FtpFileTooLargeError(Exception):
    """Raised when the remote file exceeds the configured size limit."""


# ---------------------------------------------------------------------------
# LIST-line regexes (module-level; compiled once)
# ---------------------------------------------------------------------------

_UNIX_RE = re.compile(
    r'^([d\-lbcps])[rwxsStT\-]{9}\s+\d+\s+\S+\s+\S+\s+'
    r'(\d+)\s+'                                 # size
    r'(\w{3}\s+\d{1,2}\s+[\d:]{4,5})\s+'       # date
    r'(.+)$'                                    # name
)
_DOS_RE = re.compile(
    r'^(\d{2}-\d{2}-\d{4})\s+(\d{2}:\d{2}[AP]M)\s+'
    r'(<DIR>|\d+)\s+(.+)$'
)


# ---------------------------------------------------------------------------
# FtpNavigator
# ---------------------------------------------------------------------------

class FtpNavigator:
    """
    Anonymous FTP directory lister and file downloader.

    All public methods are designed to be called from a background thread.
    """

    def __init__(
        self,
        *,
        connect_timeout: float = 10.0,
        request_timeout: float = 15.0,
        max_entries: int = 5000,
        max_depth: int = 12,
        max_path_length: int = 1024,
        max_file_bytes: int = 26_214_400,   # 25 MB
    ) -> None:
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.max_entries = max_entries
        self.max_depth = max_depth
        self.max_path_length = max_path_length
        self.max_file_bytes = max_file_bytes

        self._ftp: Optional[ftplib.FTP] = None
        self._host: str = ""
        self._port: int = 21
        self._cancel_event = threading.Event()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self, host: str, port: int = 21) -> None:
        """Open an anonymous FTP connection to host:port."""
        ftp = ftplib.FTP(timeout=self.connect_timeout)
        ftp.connect(host=host, port=port, timeout=self.connect_timeout)
        ftp.encoding = "utf-8"
        ftp.login()           # anonymous: USER anonymous / PASS anonymous@
        ftp.set_pasv(True)    # always passive; active blocked by NAT
        ftp.sock.settimeout(self.request_timeout)
        self._ftp = ftp
        self._host = host
        self._port = port

    def _ensure_connected(self) -> None:
        """Send NOOP keepalive; reconnect if the connection has dropped."""
        if self._ftp is not None:
            try:
                self._ftp.voidcmd("NOOP")
                return
            except Exception:
                self._ftp = None
        # Reconnect using stored host/port.
        self.connect(self._host, self._port)

    def disconnect(self) -> None:
        """Close the FTP control connection gracefully."""
        if self._ftp:
            try:
                self._ftp.quit()
            except Exception:
                try:
                    self._ftp.close()
                except Exception:
                    pass
            finally:
                self._ftp = None

    def cancel(self) -> None:
        """Signal all in-flight operations to stop."""
        self._cancel_event.set()

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _normalize_path(self, path: str) -> str:
        """Ensure leading slash, strip trailing slash unless root."""
        if not path.startswith("/"):
            path = "/" + path
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        return path

    def _enforce_limits(self, path: str) -> None:
        depth = len([p for p in path.split("/") if p])
        if depth > self.max_depth:
            raise ValueError(
                f"Path depth {depth} exceeds max_depth {self.max_depth}"
            )
        if len(path) > self.max_path_length:
            raise ValueError(
                f"Path length {len(path)} exceeds max_path_length {self.max_path_length}"
            )

    # ------------------------------------------------------------------
    # Directory listing
    # ------------------------------------------------------------------

    def list_dir(self, path: str) -> ListResult:
        """List a directory; tries MLSD first, falls back to LIST."""
        path = self._normalize_path(path)
        self._ensure_connected()
        self._enforce_limits(path)

        try:
            return self._list_via_mlsd(path)
        except ftplib.error_perm:
            # MLSD not supported by this server; use LIST.
            entries, truncated, warning = self._list_via_LIST(path)
            if self._cancel_event.is_set():
                warning = (warning or "") + " Operation cancelled."
            return ListResult(entries=entries, truncated=truncated, warning=warning or None)

    def _list_via_mlsd(self, path: str) -> ListResult:
        """List via MLSD (RFC 3659); raises ftplib.error_perm if unsupported."""
        raw = list(self._ftp.mlsd(path, facts=["type", "size", "modify"]))  # type: ignore[union-attr]
        entries: List[Entry] = []
        truncated = False
        warning: Optional[str] = None

        for name, facts in raw:
            ftype = facts.get("type", "file")
            if ftype in ("cdir", "pdir") or name in (".", ".."):
                continue
            is_dir = ftype == "dir"
            size = 0
            if not is_dir:
                try:
                    size = int(facts.get("size", 0))
                except (ValueError, TypeError):
                    size = 0

            modified_time: Optional[float] = None
            modify_str = facts.get("modify", "")
            if modify_str:
                try:
                    modified_time = datetime.strptime(
                        modify_str[:14], "%Y%m%d%H%M%S"
                    ).timestamp()
                except (ValueError, IndexError):
                    pass

            entries.append(Entry(
                name=name,
                is_dir=is_dir,
                size=size,
                modified_time=modified_time,
            ))
            if len(entries) >= self.max_entries:
                truncated = True
                break

        if self._cancel_event.is_set():
            warning = "Operation cancelled."
        return ListResult(entries=entries, truncated=truncated, warning=warning)

    def _list_via_LIST(
        self, path: str
    ) -> Tuple[List[Entry], bool, Optional[str]]:
        """Parse Unix ls-l or DOS DIR output from the LIST command."""
        lines: List[str] = []
        self._ftp.retrlines(f"LIST {path}", lines.append)  # type: ignore[union-attr]

        entries: List[Entry] = []
        truncated = False
        warning: Optional[str] = None
        skipped = 0

        for line in lines:
            try:
                entry = self._parse_list_line(line)
            except (UnicodeDecodeError, ValueError):
                skipped += 1
                continue
            if entry is None:
                skipped += 1
                continue
            entries.append(entry)
            if len(entries) >= self.max_entries:
                truncated = True
                break

        if skipped:
            warning = f"Skipped {skipped} malformed listing entries."
        return entries, truncated, warning

    @staticmethod
    def _parse_unix_list_mtime(date_token: str) -> Optional[float]:
        """
        Parse Unix LIST mtime token.

        Supported forms:
        - "Mon DD HH:MM" (year omitted)
        - "Mon DD YYYY"
        """
        parts = date_token.split()
        if len(parts) != 3:
            return None

        month_str, day_str, year_or_time = parts
        try:
            month = list(calendar.month_abbr).index(month_str)
            day = int(day_str)
        except (ValueError, TypeError):
            return None

        if month <= 0:
            return None

        now = datetime.now()

        # Typical LIST output omits year for recent files.
        # Infer year as current year, then roll back one year if it lands in
        # the future by more than one day.
        if ":" in year_or_time:
            try:
                hour_str, minute_str = year_or_time.split(":", 1)
                hour = int(hour_str)
                minute = int(minute_str)
                dt = datetime(now.year, month, day, hour, minute)
                if dt > (now + timedelta(days=1)):
                    dt = dt.replace(year=dt.year - 1)
                return dt.timestamp()
            except ValueError:
                return None

        try:
            year = int(year_or_time)
            return datetime(year, month, day).timestamp()
        except ValueError:
            return None

    @staticmethod
    def _parse_list_line(line: str) -> Optional[Entry]:
        """Parse a single LIST output line; returns None if unrecognised."""
        # Unix format: -rwxr-xr-x 1 user group 1234 Jan  1 12:00 filename
        m = _UNIX_RE.match(line)
        if m:
            is_dir = m.group(1) == "d"
            size = int(m.group(2)) if not is_dir else 0
            name = m.group(4).strip()
            if name in (".", ".."):
                return None
            # Best-effort date parse from Unix LIST (no seconds)
            modified_time = FtpNavigator._parse_unix_list_mtime(m.group(3))
            return Entry(name=name, is_dir=is_dir, size=size, modified_time=modified_time)

        # DOS/Windows format: 01-01-2024  12:00AM  <DIR>  foldername
        m = _DOS_RE.match(line)
        if m:
            size_str = m.group(3)
            is_dir = size_str == "<DIR>"
            size = 0 if is_dir else int(size_str)
            name = m.group(4).strip()
            if name in (".", ".."):
                return None
            modified_time = None
            try:
                dt_str = f"{m.group(1)} {m.group(2)}"
                modified_time = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p").timestamp()
            except ValueError:
                pass
            return Entry(name=name, is_dir=is_dir, size=size, modified_time=modified_time)

        return None

    # ------------------------------------------------------------------
    # File size
    # ------------------------------------------------------------------

    def get_file_size(self, remote_path: str) -> Optional[int]:
        """Return file size via SIZE command; None if server doesn't support it."""
        try:
            self._ftp.voidcmd("TYPE I")  # type: ignore[union-attr]
            resp = self._ftp.sendcmd(f"SIZE {remote_path}")  # type: ignore[union-attr]
            return int(resp.split()[-1])
        except (ftplib.error_perm, ValueError, AttributeError):
            return None

    # ------------------------------------------------------------------
    # File download
    # ------------------------------------------------------------------

    def download_file(
        self,
        remote_path: str,
        dest_dir: Path,
        progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
    ) -> DownloadResult:
        """
        Download remote_path into dest_dir.

        Raises:
            FtpFileTooLargeError: if remote file exceeds max_file_bytes.
            FtpCancelledError: if cancel() was called during transfer.
            FileExistsError: if dest file already exists.
            Exception: on any other FTP or I/O error.

        On FtpCancelledError: partial file is removed and self._ftp is set to
        None so _ensure_connected() reconnects on the next operation.
        """
        self._ensure_connected()
        self._enforce_limits(remote_path)

        # Size pre-flight
        file_size = self.get_file_size(remote_path)
        if file_size is not None and file_size > self.max_file_bytes:
            raise FtpFileTooLargeError(
                f"{remote_path}: {file_size} bytes exceeds limit "
                f"of {self.max_file_bytes} bytes ({self.max_file_bytes // (1024*1024)} MB)"
            )

        dest_path = Path(dest_dir) / PurePosixPath(remote_path).name
        if dest_path.exists():
            raise FileExistsError(f"Destination already exists: {dest_path}")

        bytes_written = 0
        start_time = time.monotonic()

        def _callback(chunk: bytes) -> None:
            nonlocal bytes_written
            if self._cancel_event.is_set():
                raise FtpCancelledError("Transfer cancelled by user.")
            f.write(chunk)
            bytes_written += len(chunk)
            if progress_callback is not None:
                progress_callback(bytes_written, file_size)

        try:
            with open(dest_path, "wb") as f:
                self._ftp.sock.settimeout(self.request_timeout)  # type: ignore[union-attr]
                self._ftp.retrbinary(  # type: ignore[union-attr]
                    f"RETR {remote_path}", _callback, blocksize=65536
                )
        except FtpCancelledError:
            dest_path.unlink(missing_ok=True)
            self._ftp = None   # connection is dirty after partial RETR
            raise
        except Exception:
            dest_path.unlink(missing_ok=True)
            raise

        # Strip executable bits
        try:
            current_mode = dest_path.stat().st_mode
            dest_path.chmod(current_mode & 0o666)
        except OSError:
            pass

        elapsed = time.monotonic() - start_time
        return DownloadResult(
            saved_path=dest_path,
            size=bytes_written,
            elapsed_seconds=elapsed,
            mtime=None,
        )

    # ------------------------------------------------------------------
    # File read (in-memory)
    # ------------------------------------------------------------------

    def read_file(
        self, remote_path: str, max_bytes: int = 5_242_880
    ) -> ReadResult:
        """Read up to max_bytes from remote_path into memory."""
        self._ensure_connected()
        self._enforce_limits(remote_path)

        buf = io.BytesIO()
        bytes_read = 0
        truncated = False

        def _callback(chunk: bytes) -> None:
            nonlocal bytes_read, truncated
            if self._cancel_event.is_set():
                raise FtpCancelledError("Read cancelled.")
            remaining = max_bytes - bytes_read
            if remaining <= 0:
                truncated = True
                raise StopIteration
            to_write = chunk[:remaining]
            buf.write(to_write)
            bytes_read += len(to_write)
            if bytes_read >= max_bytes:
                truncated = True
                raise StopIteration

        try:
            self._ftp.retrbinary(  # type: ignore[union-attr]
                f"RETR {remote_path}", _callback, blocksize=65536
            )
        except StopIteration:
            pass
        except FtpCancelledError:
            self._ftp = None
            raise

        return ReadResult(data=buf.getvalue(), size=bytes_read, truncated=truncated)


__all__ = ["FtpNavigator", "FtpCancelledError", "FtpFileTooLargeError"]
