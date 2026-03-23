"""
SMBSeek Scan Lock Manager

File-based lock coordination for scan operations.
Extracted from ScanManager to isolate lock lifecycle responsibility.

Design: ScanLockManager owns all file I/O for lock coordination.
        ScanManager owns the in-memory is_scanning guard and composes
        this class for file-level operations.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import psutil
except ImportError:
    psutil = None

from gui.utils.logging_config import get_logger

_logger = get_logger("scan_lock_manager")


class ScanLockManager:
    """
    File-based lock coordination for scan operations.

    Handles creation, removal, and stale-lock cleanup of the JSON lock
    file that prevents concurrent scan execution.

    Note: create_lock_file() writes the file unconditionally — it does
    NOT check the in-memory is_scanning flag. That guard lives in
    ScanManager.create_lock_file(), which calls is_scan_active() before
    delegating here.
    """

    def __init__(self, gui_dir: Path):
        self.gui_dir = gui_dir
        self.lock_file = gui_dir / ".scan_lock"

        # Clean up any stale lock files on startup
        self._cleanup_stale_locks()

        # Also clean up additional backend lock patterns
        self._initialize_backend_lock_cleanup()

    def _process_exists(self, pid: int) -> bool:
        """
        Check if process with given PID exists.

        Args:
            pid: Process ID to check

        Returns:
            True if process exists, False otherwise
        """
        if psutil:
            return psutil.pid_exists(pid)
        else:
            # Fallback method using os
            try:
                os.kill(pid, 0)
                return True
            except (OSError, ProcessLookupError):
                return False

    def _cleanup_stale_locks(self) -> None:
        """Clean up stale lock files from previous sessions."""
        if self.lock_file.exists():
            try:
                # Read lock file metadata
                with open(self.lock_file, 'r') as f:
                    lock_data = json.load(f)

                # Check if process is still running
                pid = lock_data.get('process_id')
                if pid and self._process_exists(pid):
                    # Process still exists, lock is valid
                    return

                # Process doesn't exist, remove stale lock
                self.lock_file.unlink()

            except (json.JSONDecodeError, FileNotFoundError, KeyError):
                # Invalid or corrupted lock file, remove it
                if self.lock_file.exists():
                    self.lock_file.unlink()

    def _initialize_backend_lock_cleanup(self) -> None:
        """
        Initialize backend interface lock cleanup for coordination.

        Ensures that both GUI and backend lock files are cleaned up
        as recommended by backend team integration guidelines.
        """
        try:
            # Clean up any additional lock patterns that might exist
            lock_patterns = [
                ".scan_lock",
                ".access_lock",
                ".discovery_lock",
                ".collection_lock"
            ]

            for pattern in lock_patterns:
                lock_path = self.gui_dir / pattern
                if lock_path.exists():
                    try:
                        # Check if lock file contains process information
                        with open(lock_path, 'r') as f:
                            lock_data = json.load(f)

                        # Check if process is still running
                        pid = lock_data.get('process_id')
                        if pid and self._process_exists(pid):
                            # Process still exists, lock is valid
                            continue

                        # Process doesn't exist, remove stale lock
                        lock_path.unlink()

                    except (json.JSONDecodeError, FileNotFoundError, KeyError):
                        # Invalid or corrupted lock file, remove it
                        if lock_path.exists():
                            lock_path.unlink()

        except Exception:
            # Non-critical cleanup failure - continue without error
            pass

    def is_lock_file_active(self) -> bool:
        """
        Check if the lock file indicates an active scan.

        Stale/corrupt semantics (mirrors is_scan_active() file-check path):
        - File absent                              → False
        - File present, JSON parses, PID alive     → True
        - File present, JSON parses, PID dead      → unlink file, False
        - File present, JSON corrupt / key error   → False (no unlink)

        Note: does NOT check in-memory is_scanning — that check is the
        caller's (ScanManager.is_scan_active) responsibility.

        Returns:
            True if a live process holds the lock file, False otherwise
        """
        if not self.lock_file.exists():
            return False

        try:
            with open(self.lock_file, 'r') as f:
                lock_data = json.load(f)

            # Check if process is still running
            pid = lock_data.get('process_id')
            if pid and self._process_exists(pid):
                return True
            else:
                # Stale lock file
                self.lock_file.unlink()
                return False

        except (json.JSONDecodeError, FileNotFoundError, KeyError):
            return False

    def create_lock_file(self, country: Optional[str] = None, scan_type: str = "complete") -> bool:
        """
        Write scan lock file with metadata.

        Writes the lock file unconditionally. Callers must check
        ScanManager.is_scan_active() before calling this method.

        Args:
            country: Country code for scan (None for global)
            scan_type: Type of scan being performed

        Returns:
            True if lock written successfully, False on I/O error
        """
        lock_data = {
            "start_time": datetime.now().isoformat(),
            "scan_type": scan_type,
            "country": country,
            "process_id": os.getpid(),
            "created_by": "SMBSeek GUI"
        }

        try:
            with open(self.lock_file, 'w') as f:
                json.dump(lock_data, f, indent=2)
            return True
        except Exception:
            return False

    def remove_lock_file(self) -> None:
        """Remove scan lock file."""
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
        except Exception:
            pass
