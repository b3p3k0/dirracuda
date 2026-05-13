"""Persistent per-account+IP login rate limiter backed by SQLite.

State file: ~/.dirracuda/state/webui_ratelimit.db (mode 0600).
Journal mode: default (DELETE) — no WAL/SHM sidecar files.

Lockout key: composite "account:{username}:ip:{client_ip}".
Success clears all rows for the account (all IPs).
"""

import os
import sqlite3
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from experimental.webui.config import AuthConfig

_DEFAULT_RL_PATH = Path.home() / ".dirracuda" / "state" / "webui_ratelimit.db"

_DDL = """
CREATE TABLE IF NOT EXISTS auth_attempts (
    key           TEXT PRIMARY KEY,
    account       TEXT NOT NULL,
    failures      INTEGER NOT NULL DEFAULT 0,
    window_start  REAL    NOT NULL DEFAULT 0.0,
    locked_until  REAL    NOT NULL DEFAULT 0.0,
    lockout_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_auth_attempts_account ON auth_attempts(account);
"""


class RateLimiterInitError(Exception):
    """Raised when the rate-limit DB cannot be created or opened."""


class RateLimiterRuntimeError(Exception):
    """Raised when a DB operation fails after successful init."""


class RateLimiter:
    def __init__(self, db_path: Path, cfg: "AuthConfig") -> None:
        self._path = Path(db_path)
        self._cfg = cfg
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            existed = self._path.exists()
            conn = sqlite3.connect(str(self._path))
            try:
                conn.executescript(_DDL)
                conn.commit()
            finally:
                conn.close()
            if not existed:
                os.chmod(self._path, 0o600)
        except OSError as exc:
            raise RateLimiterInitError(str(exc)) from exc
        except sqlite3.Error as exc:
            raise RateLimiterInitError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_locked(self, account: str, ip: str) -> tuple[bool, int]:
        """Return (is_locked, retry_after_seconds) for the composite key."""
        key = _make_key(account, ip)
        now = time.time()
        try:
            conn = sqlite3.connect(str(self._path))
            try:
                row = conn.execute(
                    "SELECT locked_until FROM auth_attempts WHERE key = ?", (key,)
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise RateLimiterRuntimeError(str(exc)) from exc
        if row is None:
            return False, 0
        locked_until = row[0]
        if locked_until <= now:
            return False, 0
        return True, max(1, int(locked_until - now))

    def record_failure(self, account: str, ip: str) -> None:
        """Record a login failure for the composite key. Triggers lockout if threshold
        reached. Prunes stale rows in the same transaction."""
        key = _make_key(account, ip)
        now = time.time()
        cfg = self._cfg
        try:
            conn = sqlite3.connect(str(self._path))
            try:
                with conn:
                    # Prune rows where both lockout and observation window have expired.
                    conn.execute(
                        """DELETE FROM auth_attempts
                           WHERE locked_until < :now
                             AND window_start + :window < :now""",
                        {"now": now, "window": cfg.lockout_window_sec},
                    )
                    row = conn.execute(
                        "SELECT failures, window_start, lockout_count FROM auth_attempts WHERE key = ?",
                        (key,),
                    ).fetchone()
                    if row is None:
                        failures, window_start, lockout_count = 0, now, 0
                    else:
                        failures, window_start, lockout_count = row
                        # Reset window if observation period has expired.
                        if now - window_start > cfg.lockout_window_sec:
                            failures, window_start = 0, now

                    failures += 1
                    locked_until = 0.0
                    if failures >= cfg.lockout_threshold:
                        lockout_count += 1
                        duration = _backoff(
                            cfg.lockout_base_duration_sec,
                            cfg.lockout_max_duration_sec,
                            lockout_count,
                        )
                        locked_until = now + duration
                        failures = 0
                        window_start = now

                    conn.execute(
                        """INSERT INTO auth_attempts
                               (key, account, failures, window_start, locked_until, lockout_count)
                           VALUES (:key, :account, :failures, :ws, :lu, :lc)
                           ON CONFLICT(key) DO UPDATE SET
                               failures = :failures,
                               window_start = :ws,
                               locked_until = :lu,
                               lockout_count = :lc""",
                        {
                            "key": key,
                            "account": account,
                            "failures": failures,
                            "ws": window_start,
                            "lu": locked_until,
                            "lc": lockout_count,
                        },
                    )
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise RateLimiterRuntimeError(str(exc)) from exc

    def record_success(self, account: str) -> None:
        """Delete all rows for account (all IPs). Per NIST 800-63B §5.2.2."""
        try:
            conn = sqlite3.connect(str(self._path))
            try:
                with conn:
                    conn.execute(
                        "DELETE FROM auth_attempts WHERE account = ?", (account,)
                    )
            finally:
                conn.close()
        except sqlite3.Error:
            pass

    def health_check(self) -> str:
        """Return 'ok' or 'error'. Runs a cheap read probe."""
        try:
            conn = sqlite3.connect(str(self._path))
            try:
                conn.execute("SELECT 1 FROM auth_attempts LIMIT 1")
            finally:
                conn.close()
            return "ok"
        except sqlite3.Error:
            return "error"


class NullRateLimiter:
    """No-op limiter for localhost-degraded mode when DB is unavailable."""

    def check_locked(self, account: str, ip: str) -> tuple[bool, int]:
        return False, 0

    def record_failure(self, account: str, ip: str) -> None:
        pass

    def record_success(self, account: str) -> None:
        pass

    def health_check(self) -> str:
        return "error"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_key(account: str, ip: str) -> str:
    return f"account:{account}:ip:{ip}"


def _backoff(base: int, maximum: int, lockout_count: int) -> int:
    duration = base * (2 ** (lockout_count - 1))
    return min(duration, maximum)
