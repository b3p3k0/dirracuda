"""
Minimal SMB navigation + download helper for the Tkinter file browser.

Goals:
 - Read-only operations (list + download) with SMB1/2/3 via impacket.
 - Hard limits (entries, depth, path length, timeouts) enforced here so the UI
   never handles raw SMB quirks.
 - Cancellation support for UI stop actions.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import List, Optional, Tuple

from impacket.smbconnection import SMBConnection, SessionError  # type: ignore
from impacket import smb  # SMB1 dialect constants


@dataclass
class Entry:
    name: str
    is_dir: bool
    size: int
    modified_time: Optional[float]


@dataclass
class ListResult:
    entries: List[Entry]
    truncated: bool
    warning: Optional[str] = None


@dataclass
class DownloadResult:
    saved_path: Path
    size: int
    elapsed_seconds: float
    mtime: Optional[float] = None


@dataclass
class ReadResult:
    """Result of reading file contents into memory."""
    data: bytes
    size: int
    truncated: bool


class SMBNavigator:
    """Lightweight wrapper around impacket's SMBConnection."""

    def __init__(
        self,
        *,
        allow_smb1: bool = True,
        connect_timeout: float = 8.0,
        request_timeout: float = 10.0,
        max_entries: int = 5000,
        max_depth: int = 12,
        max_path_length: int = 240,
        download_chunk_mb: int = 4,
    ) -> None:
        self.allow_smb1 = allow_smb1
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.max_entries = max_entries
        self.max_depth = max_depth
        self.max_path_length = max_path_length
        self.download_chunk = max(1, download_chunk_mb) * 1024 * 1024

        self._conn: Optional[SMBConnection] = None
        self._share: Optional[str] = None
        self._cancel_event = threading.Event()

    # --- Lifecycle -----------------------------------------------------

    def connect(
        self,
        host: str,
        share: str,
        username: str = "",
        password: str = "",
        port: int = 445,
        domain: str = "",
    ) -> None:
        """Establish SMB session and tree connect to the share."""
        self._cancel_event.clear()
        dialect = None
        if not self.allow_smb1:
            # Force SMB2+ negotiation; impacket picks SMB2_02+ automatically when SMB1 disabled.
            dialect = smb.SMB2_DIALECT_002
        last_error: Optional[Exception] = None
        for candidate_port in (port, 139) if port == 445 and self.allow_smb1 else (port,):
            try:
                conn = SMBConnection(
                    remoteName=host,
                    remoteHost=host,
                    sess_port=candidate_port,
                    preferredDialect=dialect,
                    timeout=self.connect_timeout,
                )
                conn.login(user=username, password=password, domain=domain)
                conn.setTimeout(self.request_timeout)
                conn.connectTree(share)

                self._conn = conn
                self._share = share
                return
            except Exception as exc:  # capture and retry/fail
                last_error = exc
                try:
                    conn.close()
                except Exception:
                    pass
                self._conn = None
                self._share = None
                if candidate_port != port:
                    # retry will proceed
                    continue
                else:
                    break

        raise RuntimeError(f"Failed to connect to {host}:{port}/{share}: {last_error}")

    def disconnect(self) -> None:
        try:
            if self._conn:
                self._conn.close()
        finally:
            self._conn = None
            self._share = None
            self._cancel_event.set()

    def cancel(self) -> None:
        """Signal in-progress operations to stop."""
        self._cancel_event.set()

    # --- Operations ----------------------------------------------------

    def list_dir(self, path: str) -> ListResult:
        conn = self._require_conn()
        share = self._require_share()

        norm_path = self._normalize_path(path)
        self._enforce_limits(norm_path)

        # Impacket expects wildcard for listing contents
        list_target = norm_path.rstrip("\\/") or "\\"
        if not list_target.endswith("*"):
            list_target = list_target.rstrip("\\/") + "\\*"

        conn.setTimeout(self.request_timeout)

        try:
            raw = conn.listPath(share, list_target)
        except SessionError as e:
            raise RuntimeError(f"SMB error listing {norm_path}: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Failed to list {norm_path}: {e}") from e

        entries: List[Entry] = []
        truncated = False

        for info in raw:
            if self._cancel_event.is_set():
                break
            name = info.get_longname()
            if name in (".", ".."):
                continue
            is_dir = info.is_directory()
            mtime = info.get_mtime_epoch() if hasattr(info, "get_mtime_epoch") else None
            entry = Entry(
                name=name,
                is_dir=is_dir,
                size=info.get_filesize() if not is_dir else 0,
                modified_time=mtime,
            )
            entries.append(entry)
            if len(entries) >= self.max_entries:
                truncated = True
                break

        warning = None
        if truncated:
            warning = f"Directory truncated to {self.max_entries} entries."
        if self._cancel_event.is_set():
            warning = (warning + " " if warning else "") + "Operation cancelled."

        return ListResult(entries=entries, truncated=truncated, warning=warning)

    def download_file(
        self,
        remote_path: str,
        dest_dir: Path,
        preserve_structure: bool = False,
        mtime: Optional[float] = None,
        progress_callback: Optional[callable] = None,
    ) -> DownloadResult:
        conn = self._require_conn()
        share = self._require_share()

        norm_path = self._normalize_path(remote_path)
        self._enforce_limits(norm_path)

        if preserve_structure:
            rel_parts = _safe_parts(norm_path.lstrip("\\"))
            dest_path = dest_dir.joinpath(*rel_parts)
        else:
            filename = PureWindowsPath(norm_path).name
            if filename in ("", ".", ".."):
                raise ValueError("Refusing SMB download: empty or root-only basename")
            dest_path = dest_dir / filename

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if dest_path.exists():
            raise FileExistsError(f"Destination already exists: {dest_path}")

        conn.setTimeout(self.request_timeout)

        start = time.time()
        bytes_written = 0

        def _writer(data: bytes) -> None:
            nonlocal bytes_written
            if self._cancel_event.is_set():
                raise RuntimeError("Download cancelled")
            dest_file.write(data)
            bytes_written += len(data)
            if progress_callback:
                try:
                    progress_callback(bytes_written, None)  # total unknown here
                except Exception:
                    pass

        try:
            with open(dest_path, "wb") as dest_file:
                conn.getFile(share, norm_path, _writer)
        except Exception:
            # Clean up partial file on failure
            if dest_path.exists():
                try:
                    dest_path.unlink()
                except Exception:
                    pass
            raise

        # Remove executable bits for safety
        try:
            dest_path.chmod(dest_path.stat().st_mode & 0o666)
        except Exception:
            pass

        # Preserve original modification time if provided
        if mtime is not None:
            try:
                os.utime(dest_path, (mtime, mtime))
            except Exception:
                pass

        elapsed = time.time() - start
        return DownloadResult(saved_path=dest_path, size=bytes_written, elapsed_seconds=elapsed, mtime=mtime)

    def read_file(self, remote_path: str, max_bytes: int = 5_242_880) -> ReadResult:
        """
        Read file contents into memory for preview.

        Args:
            remote_path: SMB path to file
            max_bytes: Maximum bytes to read (default 5MB, hard cap 1GB)

        Returns:
            ReadResult with file data, actual size, and truncation flag
        """
        conn = self._require_conn()
        share = self._require_share()

        norm_path = self._normalize_path(remote_path)
        self._enforce_limits(norm_path)

        # Hard cap at 1GB for sanity
        max_bytes = min(max_bytes, 1024 * 1024 * 1024)

        conn.setTimeout(self.request_timeout)

        chunks: List[bytes] = []
        bytes_read = 0
        truncated = False

        def _collector(data: bytes) -> None:
            nonlocal bytes_read, truncated
            if self._cancel_event.is_set():
                raise RuntimeError("Read cancelled")
            remaining = max_bytes - bytes_read
            if remaining <= 0:
                truncated = True
                raise StopIteration("Max bytes reached")
            if len(data) > remaining:
                chunks.append(data[:remaining])
                bytes_read += remaining
                truncated = True
                raise StopIteration("Max bytes reached")
            chunks.append(data)
            bytes_read += len(data)

        try:
            conn.getFile(share, norm_path, _collector)
        except StopIteration:
            # Expected when max_bytes reached
            pass
        except SessionError as e:
            raise RuntimeError(f"SMB error reading {norm_path}: {e}") from e
        except Exception as e:
            if "Max bytes reached" not in str(e) and "Read cancelled" not in str(e):
                raise RuntimeError(f"Failed to read {norm_path}: {e}") from e

        return ReadResult(data=b"".join(chunks), size=bytes_read, truncated=truncated)

    # --- Helpers -------------------------------------------------------

    def _require_conn(self) -> SMBConnection:
        if not self._conn:
            raise RuntimeError("Not connected to SMB host.")
        return self._conn

    def _require_share(self) -> str:
        if not self._share:
            raise RuntimeError("No share selected.")
        return self._share

    @property
    def share_name(self) -> Optional[str]:
        return self._share

    def _normalize_path(self, path: str) -> str:
        norm = path.replace("/", "\\")
        norm = norm or "\\"
        if not norm.startswith("\\"):
            norm = "\\" + norm
        return norm

    def _enforce_limits(self, path: str) -> None:
        depth = len([p for p in path.split("\\") if p])
        if depth > self.max_depth:
            raise ValueError(f"Path depth {depth} exceeds max_depth {self.max_depth}")
        if len(path) > self.max_path_length:
            raise ValueError(f"Path length {len(path)} exceeds max_path_length {self.max_path_length}")


def _safe_parts(rel_path: str) -> List[str]:
    parts: List[str] = []
    for segment in rel_path.replace("\\", "/").split("/"):
        if not segment or segment in (".", ".."):
            continue
        parts.append(segment)
    return parts


__all__ = ["SMBNavigator", "Entry", "ListResult", "DownloadResult", "ReadResult"]
