from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from experimental.analyst import report, report_browser, store
from experimental.analyst.service import (
    AnalystServiceError,
    ServiceFailure,
    read_report_json,
)
from experimental.analyst.store import AnalystStoreBusy


def _busy() -> sqlite3.OperationalError:
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    return error


def test_run_read_retries_whole_operation_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = store.initialize_database(tmp_path / "retry" / "analyst.db")
    sleeps: list[float] = []
    calls = 0
    monkeypatch.setattr(store.time, "sleep", sleeps.append)

    def operation(conn: sqlite3.Connection) -> int:
        nonlocal calls
        calls += 1
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 2000
        if calls < 3:
            raise _busy()
        return int(conn.execute("SELECT 7").fetchone()[0])

    assert store.run_read(operation, path=path) == 7
    assert calls == 3
    assert sleeps == [0.05, 0.10]
    assert store.READ_BUSY_TIMEOUT_MS == 2000
    assert store.READ_ATTEMPTS == 6
    assert store.READ_BACKOFF_SECONDS == (0.05, 0.10, 0.15, 0.20, 0.25)


def test_run_read_raises_store_busy_after_bounded_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = store.initialize_database(tmp_path / "busy" / "analyst.db")
    sleeps: list[float] = []
    calls = 0
    monkeypatch.setattr(store.time, "sleep", sleeps.append)

    def operation(_conn: sqlite3.Connection) -> None:
        nonlocal calls
        calls += 1
        raise _busy()

    with pytest.raises(AnalystStoreBusy, match="bounded read retry"):
        store.run_read(operation, path=path)

    assert calls == store.READ_ATTEMPTS
    assert sleeps == list(store.READ_BACKOFF_SECONDS)


def test_report_browser_surfaces_store_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def busy(*_args, **_kwargs):
        raise AnalystStoreBusy("busy")

    monkeypatch.setattr(report_browser, "run_read", busy)

    with pytest.raises(AnalystStoreBusy, match="busy"):
        report_browser.list_completed_reports()


def test_relaxed_report_and_service_preserve_busy_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def busy(*_args, **_kwargs):
        raise AnalystStoreBusy("busy")

    monkeypatch.setattr(report, "run_read", busy)
    run_id = "a" * 32

    with pytest.raises(AnalystStoreBusy, match="busy"):
        report.open_completed_report_relaxed(run_id)
    with pytest.raises(AnalystServiceError) as caught:
        read_report_json(run_id)

    assert caught.value.code is ServiceFailure.BUSY
    assert str(caught.value) == "busy"
