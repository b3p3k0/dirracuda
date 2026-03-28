"""Tests for dashboard runtime status line composition (ClamAV + tmpfs)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.dashboard import DashboardWidget


def _make_dashboard() -> DashboardWidget:
    return DashboardWidget.__new__(DashboardWidget)


def test_compose_runtime_status_lines_enabled_and_active():
    dash = _make_dashboard()
    clamav_line, tmpfs_line = dash._compose_runtime_status_lines(
        clamav_cfg={"enabled": True, "backend": "clamdscan"},
        tmpfs_state={
            "tmpfs_active": True,
            "mountpoint": "/home/test/.dirracuda/quarantine_tmpfs",
        },
    )

    assert clamav_line == "✔ ClamAV integration active <clamdscan>"
    assert tmpfs_line == "✔ tmpfs activated </home/test/.dirracuda/quarantine_tmpfs>"


def test_compose_runtime_status_lines_disabled_and_inactive():
    dash = _make_dashboard()
    clamav_line, tmpfs_line = dash._compose_runtime_status_lines(
        clamav_cfg={"enabled": False, "backend": "clamscan"},
        tmpfs_state={
            "tmpfs_active": False,
            "mountpoint": "/home/test/.dirracuda/quarantine_tmpfs",
        },
    )

    assert clamav_line == "✖ ClamAV integration active <clamscan>"
    assert tmpfs_line == "✖ tmpfs activated </home/test/.dirracuda/quarantine_tmpfs>"


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

    assert invalid_line == "✔ ClamAV integration active <auto>"
    assert missing_line == "✔ ClamAV integration active <auto>"
