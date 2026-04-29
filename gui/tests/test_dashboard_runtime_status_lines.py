"""Tests for dashboard runtime status line composition (ClamAV + tmpfs)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components import dashboard_batch_ops
from gui.components.dashboard import DashboardWidget


def _make_dashboard() -> DashboardWidget:
    return DashboardWidget.__new__(DashboardWidget)


class _Var:
    def __init__(self, value: str = "") -> None:
        self._value = value

    def set(self, value: str) -> None:
        self._value = value

    def get(self) -> str:
        return self._value


class _AfterQueue:
    def __init__(self) -> None:
        self._callbacks = []

    def after(self, _delay: int, callback=None):
        if callback is not None:
            self._callbacks.append(callback)
        return len(self._callbacks)

    def run_all(self) -> None:
        while self._callbacks:
            callback = self._callbacks.pop(0)
            callback()


def _make_runtime_dashboard() -> DashboardWidget:
    dash = _make_dashboard()
    dash.parent = _AfterQueue()
    dash.clamav_status_text = _Var()
    dash.tmpfs_status_text = _Var()
    dash.shodan_status_text = _Var()
    dash._shodan_balance_refresh_generation = 0
    dash._compose_runtime_status_lines = lambda: ("clam status", "tmpfs status")
    return dash


def test_compose_runtime_status_lines_enabled_and_active():
    dash = _make_dashboard()
    clamav_line, tmpfs_line = dash._compose_runtime_status_lines(
        clamav_cfg={"enabled": True, "backend": "clamdscan"},
        tmpfs_state={
            "tmpfs_active": True,
            "mountpoint": "/home/test/.dirracuda/quarantine_tmpfs",
        },
    )

    assert clamav_line == "✔ ClamAV Integration"
    assert tmpfs_line == "✔ tmpfs </home/test/.dirracuda/quarantine_tmpfs>"


def test_compose_runtime_status_lines_disabled_and_inactive():
    dash = _make_dashboard()
    clamav_line, tmpfs_line = dash._compose_runtime_status_lines(
        clamav_cfg={"enabled": False, "backend": "clamscan"},
        tmpfs_state={
            "tmpfs_active": False,
            "mountpoint": "/home/test/.dirracuda/quarantine_tmpfs",
        },
    )

    assert clamav_line == "✖ ClamAV Integration"
    assert tmpfs_line == "✖ tmpfs </home/test/.dirracuda/quarantine_tmpfs>"


def test_compose_runtime_status_lines_backend_invalid_or_missing_defaults_to_auto():
    dash = _make_dashboard()

    invalid_line, _ = dash._compose_runtime_status_lines(
        clamav_cfg={"enabled": True, "backend": "not-a-mode"},
        tmpfs_state={"tmpfs_active": False, "mountpoint": "/tmp/mount"},
    )
    missing_line, _ = dash._compose_runtime_status_lines(
        clamav_cfg={"enabled": "yes"},
        tmpfs_state={"tmpfs_active": False, "mountpoint": "/tmp/mount"},
    )

    assert invalid_line == "✔ ClamAV Integration"
    assert missing_line == "✔ ClamAV Integration"


def test_load_clamav_config_prefers_dashboard_active_config_path(tmp_path):
    active_cfg = tmp_path / "active.json"
    stale_cfg = tmp_path / "stale.json"
    active_cfg.write_text(
        '{"clamav": {"enabled": true, "backend": "clamscan"}}',
        encoding="utf-8",
    )
    stale_cfg.write_text(
        '{"clamav": {"enabled": false, "backend": "auto"}}',
        encoding="utf-8",
    )

    class _Settings:
        def get_setting(self, _key, default=None):
            return str(stale_cfg)

    dash = _make_dashboard()
    dash.config_path = str(active_cfg)
    dash.settings_manager = _Settings()

    loaded = dashboard_batch_ops.load_clamav_config(dash)

    assert loaded["enabled"] is True
    assert loaded["backend"] == "clamscan"


def test_update_runtime_status_display_shodan_no_key(monkeypatch):
    dash = _make_runtime_dashboard()
    monkeypatch.setattr(dash, "_read_shodan_api_key_from_config", lambda: "")
    started = []
    monkeypatch.setattr(
        dash,
        "_start_shodan_balance_refresh",
        lambda refresh_id, api_key: started.append((refresh_id, api_key)),
    )

    dash._update_runtime_status_display()

    assert dash.clamav_status_text.get() == "clam status"
    assert dash.tmpfs_status_text.get() == "tmpfs status"
    assert dash.shodan_status_text.get() == "✖ Shodan API key configured <none>"
    assert started == []


def test_update_runtime_status_display_shodan_key_success_async(monkeypatch):
    dash = _make_runtime_dashboard()
    monkeypatch.setattr(dash, "_read_shodan_api_key_from_config", lambda: "KEY_123")

    def _start(refresh_id, _api_key):
        dash.parent.after(
            0,
            lambda: dash._finish_shodan_balance_refresh(refresh_id, "123"),
        )

    monkeypatch.setattr(dash, "_start_shodan_balance_refresh", _start)

    dash._update_runtime_status_display()
    assert dash.shodan_status_text.get() == "✔ Shodan API key configured <checking balance...>"

    dash.parent.run_all()
    assert dash.shodan_status_text.get() == "✔ Shodan API key configured <query credits: 123>"


def test_update_runtime_status_display_shodan_key_failure_async(monkeypatch):
    dash = _make_runtime_dashboard()
    monkeypatch.setattr(dash, "_read_shodan_api_key_from_config", lambda: "KEY_123")

    def _start(refresh_id, _api_key):
        dash.parent.after(
            0,
            lambda: dash._finish_shodan_balance_refresh(refresh_id, None),
        )

    monkeypatch.setattr(dash, "_start_shodan_balance_refresh", _start)

    dash._update_runtime_status_display()
    assert dash.shodan_status_text.get() == "✔ Shodan API key configured <checking balance...>"

    dash.parent.run_all()
    assert dash.shodan_status_text.get() == "✔ Shodan API key configured <balance unavailable>"


def test_finish_shodan_balance_refresh_ignores_stale_result(monkeypatch):
    dash = _make_runtime_dashboard()
    keys = iter(["KEY_OLD", "KEY_NEW"])
    monkeypatch.setattr(dash, "_read_shodan_api_key_from_config", lambda: next(keys))
    starts = []
    monkeypatch.setattr(
        dash,
        "_start_shodan_balance_refresh",
        lambda refresh_id, api_key: starts.append((refresh_id, api_key)),
    )

    dash._update_runtime_status_display()
    first_id = starts[0][0]
    dash._update_runtime_status_display()
    second_id = starts[1][0]
    assert second_id > first_id

    dash._finish_shodan_balance_refresh(first_id, "999")
    assert dash.shodan_status_text.get() == "✔ Shodan API key configured <checking balance...>"

    dash._finish_shodan_balance_refresh(second_id, "111")
    assert dash.shodan_status_text.get() == "✔ Shodan API key configured <query credits: 111>"
