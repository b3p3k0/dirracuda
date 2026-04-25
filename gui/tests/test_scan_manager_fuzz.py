"""Deterministic seeded fuzz tests for ScanManager lifecycle ordering."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from gui.utils import scan_manager as sm_mod


class _DummyBackendInterface:
    def __init__(self, backend_path: str):
        self.backend_path = Path(backend_path).resolve()
        self.config_path = self.backend_path / "conf" / "config.json"
        self.terminate_calls = 0

    def terminate_current_operation(self) -> None:
        self.terminate_calls += 1


class _DummyThread:
    def __init__(self, target=None, args=(), daemon=True, **_kwargs):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False

    def start(self) -> None:
        self.started = True


def _run_sequence(seed: int, *, steps: int, tmp_path: Path, monkeypatch) -> None:
    rng = random.Random(seed)
    monkeypatch.setattr(sm_mod, "BackendInterface", _DummyBackendInterface)
    monkeypatch.setattr(sm_mod.threading, "Thread", _DummyThread)

    sm = sm_mod.ScanManager(gui_directory=str(tmp_path))
    actions = [
        "start_smb",
        "start_ftp",
        "start_http",
        "interrupt",
        "cleanup",
        "create_lock",
        "remove_lock",
        "is_active",
    ]

    for _ in range(steps):
        action = rng.choice(actions)

        if action == "start_smb":
            sm.start_scan(
                scan_options={"country": rng.choice([None, "US", "DE"])},
                backend_path=str(tmp_path),
                progress_callback=lambda *_a: None,
                log_callback=lambda _line: None,
                config_path=None,
            )
        elif action == "start_ftp":
            sm.start_ftp_scan(
                scan_options={"country": rng.choice([None, "US", "DE"])},
                backend_path=str(tmp_path),
                progress_callback=lambda *_a: None,
                log_callback=lambda _line: None,
                config_path=None,
            )
        elif action == "start_http":
            sm.start_http_scan(
                scan_options={"country": rng.choice([None, "US", "DE"])},
                backend_path=str(tmp_path),
                progress_callback=lambda *_a: None,
                log_callback=lambda _line: None,
                config_path=None,
            )
        elif action == "interrupt":
            sm.interrupt_scan()
        elif action == "cleanup":
            sm._cleanup_scan()
        elif action == "create_lock":
            sm.create_lock_file(scan_type=rng.choice(["complete", "ftp", "http"]))
        elif action == "remove_lock":
            sm.remove_lock_file()
        elif action == "is_active":
            _ = sm.is_scan_active()

        if sm.is_scanning:
            assert sm.scan_results.get("status") in {"running", "cancelling"}
        if sm.scan_results.get("status") == "cancelling":
            # Cancellation may persist in historical results after terminal cleanup.
            assert sm.is_scanning is True or "cleanup_time" in sm.scan_results
        if not sm.is_scanning and sm.lock_file.exists():
            # Lock can exist when manually created, but it must resolve as active (not stale).
            assert sm.is_scan_active() is True

    sm._cleanup_scan()
    assert sm.is_scanning is False
    assert not sm.lock_file.exists()


@pytest.mark.fuzz
@pytest.mark.parametrize("seed", [19, 73, 211])
def test_fuzz_fast_scan_manager_lifecycle(seed: int, tmp_path, monkeypatch) -> None:
    _run_sequence(seed, steps=110, tmp_path=tmp_path, monkeypatch=monkeypatch)


@pytest.mark.fuzz_heavy
@pytest.mark.parametrize("seed", [7, 41, 149, 313, 2026])
def test_fuzz_heavy_scan_manager_lifecycle(seed: int, tmp_path, monkeypatch) -> None:
    _run_sequence(seed, steps=420, tmp_path=tmp_path, monkeypatch=monkeypatch)
