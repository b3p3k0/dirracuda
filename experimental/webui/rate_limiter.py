"""Bounded persistent Web UI login rate limiting.

The SQLite store contains only SHA-256 subject identifiers. It applies both
per-account/IP lockout and an IP-wide spray limit.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import time
from pathlib import Path
from typing import TYPE_CHECKING

from shared.path_service import get_paths

if TYPE_CHECKING:
    from experimental.webui.config import AuthConfig

_DEFAULT_RL_PATH = get_paths().state_dir / "webui_ratelimit.db"
SCHEMA_VERSION = 2
ROW_LIMIT = 4096
_PAIR_SCOPE = "pair"
_IP_SCOPE = "ip"

_DDL = """
CREATE TABLE IF NOT EXISTS auth_attempts (
    key           TEXT PRIMARY KEY,
    scope         TEXT NOT NULL CHECK(scope IN ('pair', 'ip')),
    account_hash  TEXT NOT NULL,
    ip_hash       TEXT NOT NULL,
    failures      INTEGER NOT NULL DEFAULT 0,
    window_start  REAL    NOT NULL DEFAULT 0.0,
    locked_until  REAL    NOT NULL DEFAULT 0.0,
    lockout_count INTEGER NOT NULL DEFAULT 0,
    updated_at    REAL    NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_auth_attempts_account
    ON auth_attempts(scope, account_hash);
CREATE INDEX IF NOT EXISTS idx_auth_attempts_ip
    ON auth_attempts(scope, ip_hash);
CREATE INDEX IF NOT EXISTS idx_auth_attempts_updated
    ON auth_attempts(updated_at);
"""


class RateLimiterInitError(Exception):
    """Raised when the rate-limit DB cannot be created or opened."""


class RateLimiterRuntimeError(Exception):
    """Raised when a DB operation fails after successful init."""


def subject_hash(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8", errors="surrogatepass")
    ).hexdigest()


def _pair_key(account_hash: str, ip_hash: str) -> str:
    return hashlib.sha256(
        f"pair:{account_hash}:{ip_hash}".encode("ascii")
    ).hexdigest()


def _ip_key(ip_hash: str) -> str:
    return hashlib.sha256(f"ip:{ip_hash}".encode("ascii")).hexdigest()


def _make_key(account: str, ip: str) -> str:
    """Compatibility helper returning the v2 pair key."""
    return _pair_key(subject_hash(account), subject_hash(ip))


class RateLimiter:
    def __init__(self, db_path: Path, cfg: "AuthConfig") -> None:
        self._path = Path(db_path)
        self._cfg = cfg
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            existed = self._path.exists()
            conn = sqlite3.connect(str(self._path))
            try:
                table_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='auth_attempts'"
                ).fetchone() is not None
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                migrate = table_exists and version != SCHEMA_VERSION
                if migrate:
                    conn.executescript(
                        "DROP TABLE IF EXISTS auth_attempts;"
                        "DROP INDEX IF EXISTS idx_auth_attempts_account;"
                        "DROP INDEX IF EXISTS idx_auth_attempts_ip;"
                        "DROP INDEX IF EXISTS idx_auth_attempts_updated;"
                    )
                conn.executescript(_DDL)
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                conn.commit()
                if migrate:
                    conn.execute("VACUUM")
            finally:
                conn.close()
            if not existed or os.name != "nt":
                os.chmod(self._path, 0o600)
        except OSError as exc:
            raise RateLimiterInitError(str(exc)) from exc
        except sqlite3.Error as exc:
            raise RateLimiterInitError(str(exc)) from exc

    @property
    def _ip_threshold(self) -> int:
        return max(20, self._cfg.lockout_threshold * 5)

    def check_locked(self, account: str, ip: str) -> tuple[bool, int]:
        account_hash = subject_hash(account)
        ip_hash = subject_hash(ip)
        keys = (_pair_key(account_hash, ip_hash), _ip_key(ip_hash))
        now = time.time()
        try:
            conn = sqlite3.connect(str(self._path))
            try:
                rows = conn.execute(
                    "SELECT locked_until FROM auth_attempts WHERE key IN (?, ?)",
                    keys,
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise RateLimiterRuntimeError(str(exc)) from exc
        active = [row[0] for row in rows if row[0] > now]
        if not active:
            return False, 0
        return True, max(1, int(max(active) - now))

    def _record_scope(
        self,
        conn: sqlite3.Connection,
        *,
        key: str,
        scope: str,
        account_hash: str,
        ip_hash: str,
        threshold: int,
        now: float,
    ) -> None:
        cfg = self._cfg
        row = conn.execute(
            "SELECT failures, window_start, lockout_count "
            "FROM auth_attempts WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            failures, window_start, lockout_count = 0, now, 0
        else:
            failures, window_start, lockout_count = row
            if now - window_start > cfg.lockout_window_sec:
                failures, window_start = 0, now

        failures += 1
        locked_until = 0.0
        if failures >= threshold:
            lockout_count += 1
            locked_until = now + _backoff(
                cfg.lockout_base_duration_sec,
                cfg.lockout_max_duration_sec,
                lockout_count,
            )
            failures = 0
            window_start = now

        conn.execute(
            """INSERT INTO auth_attempts
                   (key, scope, account_hash, ip_hash, failures, window_start,
                    locked_until, lockout_count, updated_at)
               VALUES (:key, :scope, :account, :ip, :failures, :ws, :lu, :lc, :now)
               ON CONFLICT(key) DO UPDATE SET
                   failures = :failures,
                   window_start = :ws,
                   locked_until = :lu,
                   lockout_count = :lc,
                   updated_at = :now""",
            {
                "key": key,
                "scope": scope,
                "account": account_hash,
                "ip": ip_hash,
                "failures": failures,
                "ws": window_start,
                "lu": locked_until,
                "lc": lockout_count,
                "now": now,
            },
        )

    def record_failure(self, account: str, ip: str) -> None:
        account_hash = subject_hash(account)
        ip_hash = subject_hash(ip)
        now = time.time()
        try:
            conn = sqlite3.connect(str(self._path))
            try:
                with conn:
                    conn.execute(
                        """DELETE FROM auth_attempts
                           WHERE locked_until < :now
                             AND window_start + :window < :now""",
                        {"now": now, "window": self._cfg.lockout_window_sec},
                    )
                    self._record_scope(
                        conn,
                        key=_pair_key(account_hash, ip_hash),
                        scope=_PAIR_SCOPE,
                        account_hash=account_hash,
                        ip_hash=ip_hash,
                        threshold=self._cfg.lockout_threshold,
                        now=now,
                    )
                    self._record_scope(
                        conn,
                        key=_ip_key(ip_hash),
                        scope=_IP_SCOPE,
                        account_hash="",
                        ip_hash=ip_hash,
                        threshold=self._ip_threshold,
                        now=now,
                    )
                    count = conn.execute(
                        "SELECT COUNT(*) FROM auth_attempts"
                    ).fetchone()[0]
                    excess = max(0, count - ROW_LIMIT)
                    if excess:
                        conn.execute(
                            """DELETE FROM auth_attempts WHERE key IN (
                                   SELECT key FROM auth_attempts
                                   ORDER BY updated_at ASC, key ASC LIMIT ?
                               )""",
                            (excess,),
                        )
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise RateLimiterRuntimeError(str(exc)) from exc

    def record_success(self, account: str, ip: str) -> None:
        account_hash = subject_hash(account)
        ip_hash = subject_hash(ip)
        try:
            conn = sqlite3.connect(str(self._path))
            try:
                with conn:
                    conn.execute(
                        "DELETE FROM auth_attempts "
                        "WHERE (scope = ? AND account_hash = ?) OR key = ?",
                        (_PAIR_SCOPE, account_hash, _ip_key(ip_hash)),
                    )
            finally:
                conn.close()
        except sqlite3.Error:
            pass

    def health_check(self) -> str:
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

    def record_success(self, account: str, ip: str) -> None:
        pass

    def health_check(self) -> str:
        return "error"


def _backoff(base: int, maximum: int, lockout_count: int) -> int:
    duration = base * (2 ** (lockout_count - 1))
    return min(duration, maximum)
