"""CPM-C: disabling pacing skips waits; positive pacing stays unchanged."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from commands.access.operation import AccessOperation
from commands.discover import auth
from shared.config import SMBSeekConfig


@pytest.mark.parametrize("smart", [False, True])
@pytest.mark.parametrize("delay", [0, -1])
def test_auth_throttle_disabled_returns_before_lock(monkeypatch, smart, delay):
    sleep = Mock()
    monkeypatch.setattr(auth.time, "sleep", sleep)
    op = SimpleNamespace(config=SimpleNamespace(
        get_rate_limit_delay=lambda: delay,
        get_discovery_smart_throttling=lambda: smart,
    ))

    # No lock/timestamp attributes: disabled waits must return immediately.
    auth.throttled_auth_wait(op)
    auth.basic_throttled_auth_wait(op)

    sleep.assert_not_called()


@pytest.mark.parametrize("smart, threads, jitter, expected", [
    (False, 1, 0.5, 0.75),
    (True, 1, 0.5, 0.75),
    (True, 11, 0.5, 0.25),  # Smart floor remains 0.5 before elapsed time.
    (True, 11, 1.0, 0.30),  # Existing jitter formula remains unchanged.
])
def test_auth_positive_delay_preserves_pacing(monkeypatch, smart, threads, jitter, expected):
    sleep = Mock()
    monkeypatch.setattr(auth.time, "sleep", sleep)
    monkeypatch.setattr(auth.time, "monotonic", lambda: 10.25)
    monkeypatch.setattr(auth.threading, "active_count", lambda: threads)
    monkeypatch.setattr(auth.random, "random", lambda: jitter)
    op = SimpleNamespace(
        config=SimpleNamespace(
            get_rate_limit_delay=lambda: 1,
            get_discovery_smart_throttling=lambda: smart,
        ),
        _auth_rate_lock=threading.Lock(),
        _last_auth_attempt=0,
    )

    auth.throttled_auth_wait(op)
    sleep.assert_not_called()
    assert op._last_auth_attempt == 10.25

    op._last_auth_attempt = 10
    auth.throttled_auth_wait(op)
    sleep.assert_called_once_with(pytest.approx(expected))
    assert op._last_auth_attempt == 10.25


@pytest.mark.parametrize("delay, expected", [(0, []), (-1, []), (1, [1, 1])])
def test_sequential_auth_pacing(monkeypatch, delay, expected):
    sleeps = []
    monkeypatch.setattr(auth.time, "sleep", sleeps.append)
    monkeypatch.setattr(auth, "test_single_host", lambda *args: {"auth_method": "Anonymous"})
    op = SimpleNamespace(
        config=SimpleNamespace(get_rate_limit_delay=lambda: delay),
        output=Mock(),
        stats={},
    )

    results = auth.test_smb_authentication_sequential(op, ["192.0.2.1", "192.0.2.2", "192.0.2.3"])

    assert len(results) == 3
    assert sleeps == expected


@pytest.mark.parametrize("delay, expected", [(0, []), (-1, []), (1, [1, 1])])
def test_share_loop_pacing(monkeypatch, delay, expected):
    sleeps = []
    monkeypatch.setattr("commands.access.operation.time.sleep", sleeps.append)
    config = SimpleNamespace(
        get_connection_timeout=lambda: 5,
        get_share_access_delay=lambda: delay,
    )
    op = AccessOperation(config, Mock(), Mock(), session_id=1)
    monkeypatch.setattr(op, "check_port", lambda *args: True)
    monkeypatch.setattr(op, "enumerate_shares_detailed", lambda *args: {
        "success": True, "shares": ["Public", "Docs", "Media"],
    })
    monkeypatch.setattr(op, "test_share_access", lambda *args: {"accessible": True})

    result = op.process_target({"ip_address": "192.0.2.1", "auth_method": "Anonymous"}, 1)

    assert result["accessible_shares"] == ["Public", "Docs", "Media"]
    assert "error" not in result
    assert sleeps == expected


def test_config_without_file_has_new_pacing_defaults(tmp_path):
    config = SMBSeekConfig(str(tmp_path / "missing.json"))

    assert config.get_rate_limit_delay() == 0
    assert config.get_share_access_delay() == 0
    assert config.get("file_collection", "download_delay_seconds") == 0.5

    config.config = {}
    assert config.get_rate_limit_delay() == 0
    assert config.get_share_access_delay() == 0


@pytest.mark.parametrize("delay", [0, 1])
def test_config_accepts_saved_pacing_values(tmp_path, delay):
    exclusions = tmp_path / "exclusions.json"
    exclusions.write_text("{}", encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "shodan": {"api_key": "test-key"},
        "security": {"exclusion_file": str(exclusions)},
        "connection": {"rate_limit_delay": delay, "share_access_delay": delay},
        "file_collection": {"download_delay_seconds": 2},
    }), encoding="utf-8")

    config = SMBSeekConfig(str(config_path))

    assert config.validate_configuration()
    assert config.get_rate_limit_delay() == delay
    assert config.get_share_access_delay() == delay
    assert config.get("file_collection", "download_delay_seconds") == 2


def test_example_config_has_new_pacing_defaults():
    config = SMBSeekConfig(str(Path(__file__).resolve().parents[2] / "conf/config.json.example"))

    assert config.get_rate_limit_delay() == 0
    assert config.get_share_access_delay() == 0
    assert config.get("file_collection", "download_delay_seconds") == 0.5
