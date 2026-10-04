"""Persistence and provider-queue regression tests for the last scan window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gui.components import dashboard_provider_queue as provider_queue
from gui.components import dashboard_scan
from gui.utils import last_scan_window


@pytest.fixture
def paths(tmp_path):
    return SimpleNamespace(state_dir=tmp_path)


def _payload(db_path):
    return {
        "version": 1,
        "start": "2026-10-04 12:00:00",
        "end": "2026-10-04 12:05:00",
        "db_path": str(Path(db_path).resolve()),
        "providers": ["reddit", "shodan"],
        "cancelled": False,
    }


@pytest.mark.parametrize("cancelled", [False, True])
def test_round_trip(tmp_path, paths, cancelled):
    db_path = tmp_path / "data" / ".." / "test.db"
    start = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)

    assert last_scan_window.record_last_scan_window(
        start, end, db_path, providers=["reddit", "shodan"],
        cancelled=cancelled, paths=paths,
    )
    expected = _payload(db_path)
    expected["cancelled"] = cancelled
    assert json.loads((tmp_path / "last_scan_window.json").read_text()) == expected
    assert last_scan_window.load_last_scan_window(db_path, paths=paths) == {
        "start": start,
        "end": end,
        "providers": ["reddit", "shodan"],
        "cancelled": cancelled,
    }


@pytest.mark.parametrize("microsecond", [0, 1, 999999])
@pytest.mark.parametrize("tz", [None, timezone.utc, timezone(timedelta(hours=-4))])
def test_second_bounds_and_utc_conversion(tmp_path, paths, microsecond, tz):
    db_path = tmp_path / "test.db"
    start = datetime(2026, 10, 4, 23, 59, 58, microsecond, tzinfo=tz)
    end = datetime(2026, 10, 4, 23, 59, 59, microsecond, tzinfo=tz)

    assert last_scan_window.record_last_scan_window(
        start, end, db_path, providers=[], cancelled=False, paths=paths,
    )
    result = last_scan_window.load_last_scan_window(db_path, paths=paths)
    offset = timedelta(hours=4) if tz not in (None, timezone.utc) else timedelta()
    assert result["start"] == datetime(2026, 10, 4, 23, 59, 58, tzinfo=timezone.utc) + offset
    assert result["end"] == (
        datetime(2026, 10, 4, 23, 59, 59, tzinfo=timezone.utc)
        + offset + timedelta(seconds=bool(microsecond))
    )


def test_missing_file(tmp_path, paths):
    assert last_scan_window.load_last_scan_window(tmp_path / "test.db", paths=paths) is None


def test_unreadable_file(tmp_path, paths, monkeypatch):
    (tmp_path / "last_scan_window.json").write_text(json.dumps(_payload(tmp_path / "test.db")))
    monkeypatch.setattr(Path, "open", MagicMock(side_effect=PermissionError("unreadable")))

    assert last_scan_window.load_last_scan_window(tmp_path / "test.db", paths=paths) is None


@pytest.mark.parametrize("content", ["{", "[]", "null", "1", '"window"'])
def test_invalid_json_or_non_dict(tmp_path, paths, content):
    (tmp_path / "last_scan_window.json").write_text(content)

    assert last_scan_window.load_last_scan_window(tmp_path / "test.db", paths=paths) is None


@pytest.mark.parametrize("field", ["start", "end", "db_path", "version", "providers", "cancelled"])
def test_missing_field(tmp_path, paths, field):
    db_path = tmp_path / "test.db"
    payload = _payload(db_path)
    del payload[field]
    (tmp_path / "last_scan_window.json").write_text(json.dumps(payload))

    assert last_scan_window.load_last_scan_window(db_path, paths=paths) is None


@pytest.mark.parametrize(("field", "value"), [
    ("start", "invalid"), ("end", "invalid"),
    ("start", None), ("end", 123),
    ("end", "2026-10-04 11:59:59"),
    ("db_path", None), ("db_path", ""),
    ("version", 2), ("providers", "reddit"), ("providers", [1]),
    ("cancelled", "false"),
])
def test_invalid_field(tmp_path, paths, field, value):
    db_path = tmp_path / "test.db"
    payload = _payload(db_path)
    payload[field] = value
    (tmp_path / "last_scan_window.json").write_text(json.dumps(payload))

    assert last_scan_window.load_last_scan_window(db_path, paths=paths) is None


@pytest.mark.parametrize("db_path", [None, ""])
def test_missing_db_argument(tmp_path, paths, db_path):
    (tmp_path / "last_scan_window.json").write_text(json.dumps(_payload(tmp_path / "test.db")))

    assert last_scan_window.load_last_scan_window(db_path, paths=paths) is None


def test_db_path_mismatch(tmp_path, paths):
    (tmp_path / "last_scan_window.json").write_text(json.dumps(_payload(tmp_path / "test.db")))

    assert last_scan_window.load_last_scan_window(tmp_path / "other.db", paths=paths) is None


def test_write_failure_returns_false(tmp_path, caplog):
    state_dir = tmp_path / "state"
    state_dir.write_text("not a directory")
    now = datetime.now(timezone.utc)

    assert not last_scan_window.record_last_scan_window(
        now, now, tmp_path / "test.db", providers=[], cancelled=False,
        paths=SimpleNamespace(state_dir=state_dir),
    )
    assert any(
        record.name == last_scan_window.__name__ and record.levelname == "WARNING"
        for record in caplog.records
    )


def test_atomic_write_creates_parents(tmp_path, monkeypatch):
    state_dir = tmp_path / "nested" / "state"
    now = datetime.now(timezone.utc)
    replace = last_scan_window.os.replace
    calls = []

    def check_replace(source, destination):
        assert Path(source).parent == state_dir
        assert json.loads(Path(source).read_text())["version"] == 1
        calls.append(destination)
        replace(source, destination)

    monkeypatch.setattr(last_scan_window.os, "replace", check_replace)
    assert last_scan_window.record_last_scan_window(
        now, now, tmp_path / "test.db", providers=[], cancelled=False,
        paths=SimpleNamespace(state_dir=state_dir),
    )
    assert calls == [state_dir / "last_scan_window.json"]
    assert list(state_dir.iterdir()) == calls


def test_replace_failure_preserves_previous_window(tmp_path, paths, monkeypatch):
    window_path = tmp_path / "last_scan_window.json"
    previous = json.dumps(_payload(tmp_path / "test.db"))
    window_path.write_text(previous)
    monkeypatch.setattr(last_scan_window.os, "replace", MagicMock(side_effect=OSError("failed")))
    now = datetime.now(timezone.utc)

    assert not last_scan_window.record_last_scan_window(
        now, now, tmp_path / "test.db", providers=[], cancelled=True, paths=paths,
    )
    assert window_path.read_text() == previous
    assert list(tmp_path.iterdir()) == [window_path]


class _ImmediateParent:
    def after(self, delay, callback, *args):
        callback(*args)


@pytest.fixture
def dash(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_queue, "_mb", lambda: MagicMock())
    for launcher in ("start_reddit_scan", "start_searxng_scan", "start_shodan_provider"):
        monkeypatch.setattr(dashboard_scan, launcher, lambda _dash, _request: True)
    return SimpleNamespace(
        parent=_ImmediateParent(),
        db_reader=SimpleNamespace(db_path=tmp_path / "test.db"),
        _register_running_task=lambda **_kwargs: "provider-task",
        _remove_running_task=MagicMock(),
        _log_status_event=MagicMock(),
    )


@pytest.fixture
def recorder(monkeypatch):
    recorder = MagicMock(return_value=True)
    monkeypatch.setattr(last_scan_window, "record_last_scan_window", recorder)
    return recorder


@pytest.mark.parametrize("cancelled", [False, True])
def test_queue_records_window(dash, recorder, cancelled):
    before = datetime.now(timezone.utc)
    assert provider_queue.start_provider_queue(dash, {
        "providers": ["shodan", "reddit", "searxng"], "protocols": ["smb"],
    })
    start = dash._provider_queue_started_at
    assert before <= start <= datetime.now(timezone.utc)
    assert start.tzinfo is timezone.utc
    assert dash._provider_queue_providers == ["reddit", "searxng", "shodan"]
    recorder.assert_not_called()
    if cancelled:
        assert provider_queue.cancel_provider_queue(dash)
    else:
        for provider in ("reddit", "searxng", "shodan"):
            assert provider_queue.complete_provider(
                dash, provider, dash._provider_queue_generation, success=True,
            )

    recorder.assert_called_once()
    args, kwargs = recorder.call_args
    assert args[0] == start
    assert start <= args[1] <= datetime.now(timezone.utc)
    assert args[1].tzinfo is timezone.utc
    assert args[2] == dash.db_reader.db_path
    assert kwargs == {"providers": ["reddit", "searxng", "shodan"], "cancelled": cancelled}
    assert dash._provider_queue_started_at is None
    assert dash._provider_queue_active is False
    dash._remove_running_task.assert_called_once_with("provider-task")
    provider_queue._finish_provider_queue(dash)
    assert not provider_queue.cancel_provider_queue(dash)
    recorder.assert_called_once()


@pytest.mark.parametrize("reason", ["active", "conflict", "unsupported", "no_providers", "no_protocols"])
def test_rejected_start_records_nothing(dash, recorder, monkeypatch, reason):
    request = {"providers": ["shodan"], "protocols": ["smb"]}
    if reason == "active":
        dash._provider_queue_active = True
    elif reason == "conflict":
        dash._reddit_scan_running = True
    elif reason == "unsupported":
        request["providers"] = ["unsupported"]
    elif reason == "no_providers":
        monkeypatch.setattr(provider_queue, "rank_providers", lambda _providers: [])
    else:
        request["protocols"] = []

    assert not provider_queue.start_provider_queue(dash, request)
    assert not hasattr(dash, "_provider_queue_started_at")
    recorder.assert_not_called()


@pytest.mark.parametrize("cancelled", [False, True])
def test_record_exception_does_not_break_teardown(dash, recorder, cancelled):
    recorder.side_effect = RuntimeError("record failed")
    assert provider_queue.start_provider_queue(dash, {"providers": ["reddit"]})

    if cancelled:
        assert provider_queue.cancel_provider_queue(dash)
    else:
        provider_queue._finish_provider_queue(dash)

    recorder.assert_called_once()
    assert dash._provider_queue_active is False
    assert dash._provider_queue_started_at is None
    assert dash._provider_queue_pending == []
    assert dash._provider_queue_current is None
    dash._remove_running_task.assert_called_once_with("provider-task")


@pytest.mark.parametrize("missing", ["start", "db_reader", "db_path", "empty_db_path"])
def test_missing_start_or_db_skips_record(dash, recorder, missing):
    assert provider_queue.start_provider_queue(dash, {"providers": ["reddit"]})
    if missing == "start":
        del dash._provider_queue_started_at
    elif missing == "db_reader":
        del dash.db_reader
    elif missing == "db_path":
        del dash.db_reader.db_path
    else:
        dash.db_reader.db_path = ""

    provider_queue._finish_provider_queue(dash)

    recorder.assert_not_called()
    assert dash._provider_queue_active is False
    assert dash._provider_queue_started_at is None
