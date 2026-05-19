"""Unit tests for experimental.webui.rate_limiter."""

import os
import stat
import time

import pytest

from experimental.webui.config import AuthConfig
from experimental.webui.rate_limiter import (
    NullRateLimiter,
    RateLimiter,
    RateLimiterInitError,
    _make_key,
)

_ACCOUNT = "testuser"
_IP1 = "127.0.0.1"
_IP2 = "10.0.0.2"
_ACCOUNT2 = "other"


def _cfg(**kwargs) -> AuthConfig:
    defaults = dict(
        lockout_threshold=5,
        lockout_window_sec=900,
        lockout_base_duration_sec=300,
        lockout_max_duration_sec=3600,
    )
    defaults.update(kwargs)
    return AuthConfig(**defaults)


@pytest.fixture
def rl(tmp_path):
    return RateLimiter(tmp_path / "rl.db", _cfg())


# ------------------------------------------------------------------
# Basic lockout threshold
# ------------------------------------------------------------------

def test_no_lockout_below_threshold(rl):
    for _ in range(4):
        rl.record_failure(_ACCOUNT, _IP1)
    locked, _ = rl.check_locked(_ACCOUNT, _IP1)
    assert not locked


def test_lockout_at_threshold(rl):
    for _ in range(5):
        rl.record_failure(_ACCOUNT, _IP1)
    locked, retry = rl.check_locked(_ACCOUNT, _IP1)
    assert locked
    assert retry > 0


def test_locked_stays_locked_before_expiry(rl):
    for _ in range(5):
        rl.record_failure(_ACCOUNT, _IP1)
    locked, _ = rl.check_locked(_ACCOUNT, _IP1)
    assert locked
    locked2, _ = rl.check_locked(_ACCOUNT, _IP1)
    assert locked2


def test_lockout_auto_expires(tmp_path, monkeypatch):
    cfg = _cfg(lockout_base_duration_sec=30)
    rl = RateLimiter(tmp_path / "rl.db", cfg)
    for _ in range(5):
        rl.record_failure(_ACCOUNT, _IP1)
    locked, _ = rl.check_locked(_ACCOUNT, _IP1)
    assert locked

    future = time.time() + 60
    monkeypatch.setattr("experimental.webui.rate_limiter.time.time", lambda: future)
    locked2, _ = rl.check_locked(_ACCOUNT, _IP1)
    assert not locked2


# ------------------------------------------------------------------
# Success clears all IPs for the account
# ------------------------------------------------------------------

def test_success_clears_all_ips(tmp_path):
    rl = RateLimiter(tmp_path / "rl.db", _cfg())
    for _ in range(5):
        rl.record_failure(_ACCOUNT, _IP1)
    for _ in range(5):
        rl.record_failure(_ACCOUNT, _IP2)
    rl.record_success(_ACCOUNT)
    assert not rl.check_locked(_ACCOUNT, _IP1)[0]
    assert not rl.check_locked(_ACCOUNT, _IP2)[0]


# ------------------------------------------------------------------
# Isolation between composite keys
# ------------------------------------------------------------------

def test_different_ip_not_blocked(rl):
    for _ in range(5):
        rl.record_failure(_ACCOUNT, _IP1)
    locked, _ = rl.check_locked(_ACCOUNT, _IP1)
    assert locked
    locked2, _ = rl.check_locked(_ACCOUNT, _IP2)
    assert not locked2


def test_different_account_not_blocked(rl):
    for _ in range(5):
        rl.record_failure(_ACCOUNT, _IP1)
    locked, _ = rl.check_locked(_ACCOUNT2, _IP1)
    assert not locked


# ------------------------------------------------------------------
# Exponential backoff
# ------------------------------------------------------------------

def test_exponential_backoff_second_lockout(tmp_path, monkeypatch):
    cfg = _cfg(lockout_base_duration_sec=60, lockout_max_duration_sec=3600)
    rl = RateLimiter(tmp_path / "rl.db", cfg)

    # First lockout
    for _ in range(5):
        rl.record_failure(_ACCOUNT, _IP1)
    _, retry1 = rl.check_locked(_ACCOUNT, _IP1)
    assert 55 <= retry1 <= 65  # ~60s

    # Advance past first lockout
    future = time.time() + 120
    monkeypatch.setattr("experimental.webui.rate_limiter.time.time", lambda: future)

    # Second lockout
    for _ in range(5):
        rl.record_failure(_ACCOUNT, _IP1)
    _, retry2 = rl.check_locked(_ACCOUNT, _IP1)
    assert retry2 > retry1  # base * 2^1 = 120s


def test_exponential_backoff_capped_at_max(tmp_path, monkeypatch):
    cfg = _cfg(lockout_base_duration_sec=60, lockout_max_duration_sec=90)
    rl = RateLimiter(tmp_path / "rl.db", cfg)
    now = time.time()

    # Trigger multiple lockouts, each time advancing time past the lockout
    for i in range(5):
        offset = now + (200 * i)
        monkeypatch.setattr(
            "experimental.webui.rate_limiter.time.time", lambda _o=offset: _o
        )
        for _ in range(5):
            rl.record_failure(_ACCOUNT, _IP1)

    _, retry = rl.check_locked(_ACCOUNT, _IP1)
    assert retry <= 91  # capped at max=90s + 1s margin


# ------------------------------------------------------------------
# Observation window
# ------------------------------------------------------------------

def test_observation_window_resets_old_failures(tmp_path, monkeypatch):
    cfg = _cfg(lockout_threshold=5, lockout_window_sec=60)
    rl = RateLimiter(tmp_path / "rl.db", cfg)

    # Capture real time.time before any patching so we can restore it correctly.
    _real_time = time.time

    # 4 failures in the past
    past = _real_time() - 120
    monkeypatch.setattr("experimental.webui.rate_limiter.time.time", lambda: past)
    for _ in range(4):
        rl.record_failure(_ACCOUNT, _IP1)

    # Back to now — one more failure should not trigger lockout (window expired)
    monkeypatch.setattr("experimental.webui.rate_limiter.time.time", _real_time)
    rl.record_failure(_ACCOUNT, _IP1)
    locked, _ = rl.check_locked(_ACCOUNT, _IP1)
    assert not locked


# ------------------------------------------------------------------
# Persistence and file properties
# ------------------------------------------------------------------

def test_db_created_on_first_use(tmp_path):
    path = tmp_path / "new.db"
    assert not path.exists()
    rl = RateLimiter(path, _cfg())
    rl.record_failure(_ACCOUNT, _IP1)
    assert path.exists()


def test_file_mode_restricted(tmp_path):
    path = tmp_path / "rl.db"
    RateLimiter(path, _cfg())
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_no_wal_sidecar_files(tmp_path):
    path = tmp_path / "rl.db"
    rl = RateLimiter(path, _cfg())
    rl.record_failure(_ACCOUNT, _IP1)
    assert not (tmp_path / "rl.db-wal").exists()
    assert not (tmp_path / "rl.db-shm").exists()


def test_state_survives_reinit(tmp_path):
    path = tmp_path / "rl.db"
    rl = RateLimiter(path, _cfg())
    for _ in range(5):
        rl.record_failure(_ACCOUNT, _IP1)

    rl2 = RateLimiter(path, _cfg())
    locked, _ = rl2.check_locked(_ACCOUNT, _IP1)
    assert locked


# ------------------------------------------------------------------
# Stale-row pruning
# ------------------------------------------------------------------

def test_stale_rows_pruned(tmp_path, monkeypatch):
    cfg = _cfg(lockout_threshold=5, lockout_window_sec=60, lockout_base_duration_sec=30)
    path = tmp_path / "rl.db"
    rl = RateLimiter(path, cfg)

    # Trigger lockout
    for _ in range(5):
        rl.record_failure(_ACCOUNT, _IP1)

    # Advance past lockout duration and window
    future = time.time() + 200
    monkeypatch.setattr("experimental.webui.rate_limiter.time.time", lambda: future)

    # One more failure from a different pair triggers prune
    rl.record_failure(_ACCOUNT2, _IP2)

    import sqlite3
    conn = sqlite3.connect(str(path))
    key = _make_key(_ACCOUNT, _IP1)
    row = conn.execute(
        "SELECT key FROM auth_attempts WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    assert row is None


# ------------------------------------------------------------------
# NullRateLimiter
# ------------------------------------------------------------------

def test_null_rate_limiter_never_blocks():
    nrl = NullRateLimiter()
    for _ in range(100):
        nrl.record_failure("user", "1.2.3.4")
    locked, retry = nrl.check_locked("user", "1.2.3.4")
    assert not locked
    assert retry == 0


def test_null_rate_limiter_health_is_error():
    assert NullRateLimiter().health_check() == "error"


def test_null_rate_limiter_success_noop():
    nrl = NullRateLimiter()
    nrl.record_success("user")  # must not raise


# ------------------------------------------------------------------
# Init failure
# ------------------------------------------------------------------

def test_rate_limiter_init_error_unwritable_dir(tmp_path):
    locked_dir = tmp_path / "locked"
    locked_dir.mkdir()
    os.chmod(locked_dir, 0o500)
    try:
        with pytest.raises(RateLimiterInitError):
            RateLimiter(locked_dir / "rl.db", _cfg())
    finally:
        os.chmod(locked_dir, 0o700)
