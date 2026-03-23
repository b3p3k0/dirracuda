"""
Parity tests for ScanLockManager.

Covers:
- JSON key contract for create_lock_file()
- is_lock_file_active() semantics: no file, live PID, stale PID (unlinks), corrupt JSON (no unlink)
"""

import json
import os
from pathlib import Path
from unittest.mock import patch
import pytest

from gui.utils.scan_lock_manager import ScanLockManager


@pytest.fixture
def lock_dir(tmp_path):
    """Provide a temp directory; ScanLockManager cleans up on init so no pre-existing locks."""
    return tmp_path


@pytest.fixture
def lm(lock_dir):
    """ScanLockManager instance pointed at a clean temp directory."""
    return ScanLockManager(lock_dir)


class TestCreateLockFile:
    def test_creates_file_with_correct_keys(self, lm, lock_dir):
        result = lm.create_lock_file("US", "complete")
        assert result is True

        lock_path = lock_dir / ".scan_lock"
        assert lock_path.exists()

        data = json.loads(lock_path.read_text())
        assert set(data.keys()) == {"start_time", "scan_type", "country", "process_id", "created_by"}

    def test_values_match_expected(self, lm, lock_dir):
        lm.create_lock_file("GB", "ftp")
        data = json.loads((lock_dir / ".scan_lock").read_text())

        assert data["scan_type"] == "ftp"
        assert data["country"] == "GB"
        assert data["process_id"] == os.getpid()
        assert data["created_by"] == "SMBSeek GUI"

    def test_country_none_allowed(self, lm, lock_dir):
        lm.create_lock_file(None, "complete")
        data = json.loads((lock_dir / ".scan_lock").read_text())
        assert data["country"] is None


class TestIsLockFileActive:
    def test_no_file_returns_false(self, lm, lock_dir):
        assert not (lock_dir / ".scan_lock").exists()
        assert lm.is_lock_file_active() is False

    def test_live_pid_returns_true_and_does_not_unlink(self, lm, lock_dir):
        # Write lock with current PID (guaranteed alive)
        lock_path = lock_dir / ".scan_lock"
        lock_path.write_text(json.dumps({
            "start_time": "2026-01-01T00:00:00",
            "scan_type": "complete",
            "country": "US",
            "process_id": os.getpid(),
            "created_by": "SMBSeek GUI"
        }))

        assert lm.is_lock_file_active() is True
        assert lock_path.exists()  # must not be unlinked

    def test_stale_pid_returns_false_and_unlinks(self, lm, lock_dir):
        lock_path = lock_dir / ".scan_lock"
        lock_path.write_text(json.dumps({
            "start_time": "2026-01-01T00:00:00",
            "scan_type": "complete",
            "country": "US",
            "process_id": 99999,
            "created_by": "SMBSeek GUI"
        }))

        # Force _process_exists to return False regardless of real PID state
        with patch.object(lm, '_process_exists', return_value=False):
            result = lm.is_lock_file_active()

        assert result is False
        assert not lock_path.exists()  # must be unlinked

    def test_corrupt_json_returns_false_and_does_not_unlink(self, lm, lock_dir):
        lock_path = lock_dir / ".scan_lock"
        lock_path.write_text("not valid json {{{")

        assert lm.is_lock_file_active() is False
        assert lock_path.exists()  # must NOT be unlinked on corrupt JSON
