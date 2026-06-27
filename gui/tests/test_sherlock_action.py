"""
C4 standalone "Scan Sherlock Selected" action-handler tests.

Verifies empty-selection warning, target construction, summary reporting, the
in-flight guard (no overlapping scans), and deterministic flag clearing even when
the worker raises. The handler is exercised in isolation from the full window.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.server_list_window.actions import sherlock_action as sa
from gui.components.server_list_window.actions.sherlock_action import (
    ServerListWindowSherlockMixin,
)
from gui.utils.sherlock_scan import SherlockScanSummary


class _Win(ServerListWindowSherlockMixin):
    """Minimal host object carrying just the attributes the mixin touches."""

    def __init__(self):
        self.tree = MagicMock()
        self.filtered_servers = []
        self.window = MagicMock()
        # Run after() callbacks synchronously so tests are deterministic.
        self.window.after = lambda delay, fn: fn()
        self.db_reader = MagicMock()
        self.settings_manager = None
        self._sherlock_scan_active = False
        self._set_status = MagicMock()
        self._update_action_buttons_state = MagicMock()
        self._load_data = MagicMock()

    def _join(self):
        t = getattr(self, "_sherlock_scan_thread", None)
        if t is not None:
            t.join(timeout=5)


@pytest.fixture
def patched(monkeypatch):
    info = MagicMock()
    warning = MagicMock()
    error = MagicMock()
    monkeypatch.setattr(sa.messagebox, "showinfo", info)
    monkeypatch.setattr(sa.messagebox, "showwarning", warning)
    monkeypatch.setattr(sa.messagebox, "showerror", error)
    return {"info": info, "warning": warning, "error": error}


def test_empty_selection_warns_and_does_not_scan(monkeypatch, patched):
    monkeypatch.setattr(sa.table, "get_selected_server_data", lambda tree, servers: [])
    run = MagicMock()
    monkeypatch.setattr(sa, "run_sherlock_scan", run)

    win = _Win()
    win._on_sherlock_scan_selected()

    patched["warning"].assert_called_once()
    run.assert_not_called()


def test_scan_builds_targets_and_reports_summary(monkeypatch, patched):
    selected = [
        {"ip_address": "1.1.1.1", "host_type": "S", "protocol_server_id": 1, "port": None},
        {"ip_address": "2.2.2.2", "host_type": "H", "protocol_server_id": 9, "port": 8080},
    ]
    monkeypatch.setattr(sa.table, "get_selected_server_data", lambda tree, servers: selected)
    captured = {}

    def fake_run(db_reader, settings, targets):
        captured["targets"] = list(targets)
        return SherlockScanSummary(scanned=2, with_findings=1, clean=1, skipped_no_snapshot=0)

    monkeypatch.setattr(sa, "run_sherlock_scan", fake_run)

    win = _Win()
    win._on_sherlock_scan_selected()
    win._join()

    assert captured["targets"][0]["ip_address"] == "1.1.1.1"
    assert captured["targets"][1]["port"] == 8080
    patched["info"].assert_called_once()
    win._load_data.assert_called_once()
    assert win._sherlock_scan_active is False


def test_summary_surfaces_not_persisted_and_errors(monkeypatch, patched):
    monkeypatch.setattr(sa.table, "get_selected_server_data",
                        lambda tree, servers: [{"ip_address": "1.1.1.1", "host_type": "S", "protocol_server_id": 1, "port": None}])
    monkeypatch.setattr(sa, "run_sherlock_scan",
                        lambda *a, **k: SherlockScanSummary(scanned=0, not_persisted=1, errors=2))

    win = _Win()
    win._on_sherlock_scan_selected()
    win._join()

    msg = patched["info"].call_args[0][1]
    assert "not persisted" in msg
    assert "errors" in msg


def test_in_flight_guard_rejects_second_click(monkeypatch, patched):
    monkeypatch.setattr(sa.table, "get_selected_server_data",
                        lambda tree, servers: [{"ip_address": "1.1.1.1", "host_type": "S", "protocol_server_id": 1, "port": None}])
    run = MagicMock(return_value=SherlockScanSummary())
    monkeypatch.setattr(sa, "run_sherlock_scan", run)

    win = _Win()
    win._sherlock_scan_active = True  # a scan is already running
    win._on_sherlock_scan_selected()

    run.assert_not_called()
    patched["info"].assert_called_once()  # "already running" notice


def test_flag_cleared_when_worker_raises(monkeypatch, patched):
    monkeypatch.setattr(sa.table, "get_selected_server_data",
                        lambda tree, servers: [{"ip_address": "1.1.1.1", "host_type": "S", "protocol_server_id": 1, "port": None}])

    def boom(*a, **k):
        raise RuntimeError("worker failed")

    monkeypatch.setattr(sa, "run_sherlock_scan", boom)

    win = _Win()
    win._on_sherlock_scan_selected()
    win._join()

    assert win._sherlock_scan_active is False
    patched["error"].assert_called_once()
