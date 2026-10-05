"""Shared probe worker defaults, saved overrides, and invalid input handling."""

from types import SimpleNamespace

import pytest

from shared.probe_concurrency import (
    PROBE_WORKERS_DEFAULT,
    PROBE_WORKERS_MAX,
    PROBE_WORKERS_MIN,
    clamp_probe_workers,
    resolve_probe_workers,
)


@pytest.mark.parametrize("value, expected", [
    (None, 12), ("abc", 12), (True, 12), (False, 12),
    (float("inf"), 12), (float("-inf"), 12), (float("nan"), 12),
    ("inf", 12), ("nan", 12), (object(), 12), ([], 12),
    (0, 1), (-5, 1), (99, 16), (16, 16), (1, 1),
    (3, 3), ("7", 7), (7.9, 7), (10**1000, 16),
])
def test_clamp_probe_workers(value, expected):
    assert clamp_probe_workers(value) == expected
    assert type(clamp_probe_workers(value)) is int


def test_policy_constants():
    assert (PROBE_WORKERS_DEFAULT, PROBE_WORKERS_MAX, PROBE_WORKERS_MIN) == (12, 16, 1)


def test_resolve_without_settings():
    assert resolve_probe_workers(None) == 12


def test_resolve_unset_setting():
    def get_setting(key, default):
        assert key == "probe.batch_max_workers"
        assert default == 12
        return default

    assert resolve_probe_workers(SimpleNamespace(get_setting=get_setting)) == 12


@pytest.mark.parametrize("value, expected", [(3, 3), (0, 1), (99, 16), ("abc", 12),
                                            (None, 12), (16, 16), (True, 12)])
def test_resolve_saved_setting(value, expected):
    settings = SimpleNamespace(get_setting=lambda key, default: value)
    assert resolve_probe_workers(settings) == expected


def test_resolve_settings_exception():
    def get_setting(key, default):
        raise RuntimeError("Settings unavailable")

    assert resolve_probe_workers(SimpleNamespace(get_setting=get_setting)) == 12


@pytest.mark.parametrize("value, expected", [(None, 12), (20, 16), (3, 3), ("bad", 12)])
def test_redseek_worker_resolver(value, expected):
    from experimental.redseek.service import _resolve_probe_worker_count

    assert _resolve_probe_worker_count(SimpleNamespace(probe_worker_count=value)) == expected
