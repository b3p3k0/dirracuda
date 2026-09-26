from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from gui.utils import analyst_post_extract
from gui.utils.default_gui_settings import DEFAULT_GUI_SETTINGS
from shared.extract_manifest import ExtractSummaryReference, ExtractSummarySource


class _Settings:
    def __init__(self, enabled: object) -> None:
        self.enabled = enabled

    def get_setting(self, key: str, default=None):
        assert key == "analyst.offer_after_extract"
        return self.enabled


class _Parent:
    def __init__(self) -> None:
        self.callbacks = []
        self.ui_thread = threading.get_ident()

    def after(self, _delay: int, callback) -> None:
        assert threading.get_ident() == self.ui_thread
        self.callbacks.append(callback)

    def drain_until(self, predicate, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.callbacks:
                self.callbacks.pop(0)()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("UI completion callback did not arrive")


def _reference() -> ExtractSummaryReference:
    return ExtractSummaryReference(17, None, ExtractSummarySource.PRIMARY_DB)


def test_offer_setting_is_off_by_default() -> None:
    assert DEFAULT_GUI_SETTINGS["analyst"]["offer_after_extract"] is False


def test_offer_default_off_and_legacy_path_are_zero_prompt(monkeypatch, tmp_path: Path) -> None:
    prompts = []
    monkeypatch.setattr(
        analyst_post_extract.safe_messagebox,
        "askyesno",
        lambda *args, **kwargs: prompts.append((args, kwargs)),
    )
    parent = _Parent()
    assert not analyst_post_extract.offer_after_extract(
        parent, _Settings(False), _reference(),
        main_db_path=(tmp_path / "main.db").absolute(), report_label="public host",
    )
    assert not analyst_post_extract.offer_after_extract(
        parent, _Settings(True), tmp_path / "legacy.json",
        main_db_path=(tmp_path / "main.db").absolute(), report_label="public host",
    )
    assert prompts == []


def test_decline_creates_no_run(monkeypatch, tmp_path: Path) -> None:
    called = []
    monkeypatch.setattr(
        analyst_post_extract.safe_messagebox, "askyesno", lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "experimental.analyst.service.create_manifest_and_launch",
        lambda *a, **k: called.append((a, k)),
    )
    assert analyst_post_extract.offer_after_extract(
        _Parent(), _Settings(True), _reference(),
        main_db_path=(tmp_path / "main.db").absolute(), report_label="public host",
    )
    assert called == []


def test_accept_launches_exactly_once_off_ui_thread_and_reports_on_ui(
    monkeypatch, tmp_path: Path,
) -> None:
    parent = _Parent()
    calls = []
    messages = []
    monkeypatch.setattr(
        analyst_post_extract.safe_messagebox, "askyesno", lambda *a, **k: True,
    )
    monkeypatch.setattr(
        analyst_post_extract.safe_messagebox,
        "showinfo",
        lambda *args, **kwargs: messages.append(
            ("info", threading.get_ident(), args, kwargs)
        ),
    )
    monkeypatch.setattr(
        analyst_post_extract.safe_messagebox,
        "showerror",
        lambda *args, **kwargs: messages.append(
            ("error", threading.get_ident(), args, kwargs)
        ),
    )

    def launch(*args, **kwargs):
        calls.append((threading.get_ident(), args, kwargs))

    monkeypatch.setattr(
        "experimental.analyst.service.create_manifest_and_launch", launch,
    )
    db = (tmp_path / "main.db").absolute()
    assert analyst_post_extract.offer_after_extract(
        parent, _Settings(True), _reference(),
        main_db_path=db, report_label=" 203.0.113.7 ",
    )
    parent.drain_until(lambda: bool(messages))

    assert len(calls) == 1
    worker_thread, args, kwargs = calls[0]
    assert worker_thread != parent.ui_thread
    assert args == (_reference(),)
    assert kwargs == {
        "main_db_path": db,
        "output_base": None,
        "report_label": "203.0.113.7",
        "mode": "fast",
    }
    assert messages[0][0:2] == ("info", parent.ui_thread)


def test_primary_write_returns_exact_database_row_reference(monkeypatch) -> None:
    from gui.utils import database_access, extract_runner

    calls = []

    class _Reader:
        def __init__(self, path: str) -> None:
            calls.append(("open", path))

        def upsert_extract_run_summary(self, summary, **kwargs):
            calls.append((summary, kwargs))
            return 23

    monkeypatch.setattr(database_access, "DatabaseReader", _Reader)
    summary = {"ip_address": "203.0.113.7", "files": [], "totals": {}}
    reference = extract_runner.write_extract_log(
        summary,
        db_path="/tmp/public-main.db",
        ip_address="203.0.113.7",
        host_type="S",
        protocol_server_id=9,
        port=445,
    )
    assert reference == ExtractSummaryReference(
        23, None, ExtractSummarySource.PRIMARY_DB,
    )
    assert calls[1][1]["protocol_server_id"] == 9


def test_dashboard_post_scan_extract_persists_structured_reference(
    monkeypatch, tmp_path: Path,
) -> None:
    from gui.components import dashboard_batch_ops

    events = []
    reference = _reference()
    summary = {
        "ip_address": "203.0.113.7",
        "files": [{"saved_to": str(tmp_path / "public.txt")}],
        "totals": {"files_downloaded": 1, "bytes_downloaded": 6},
    }

    class _Extract:
        @staticmethod
        def run_extract(*args, **kwargs):
            events.append("extract")
            return summary

        @staticmethod
        def write_extract_log(value, **kwargs):
            assert value is summary
            events.append("persist")
            return reference

    mapping = {
        "create_quarantine_dir": lambda *a, **k: tmp_path,
        "extract_runner": _Extract,
    }
    monkeypatch.setattr(dashboard_batch_ops, "_d", lambda name: mapping[name])

    class _Reader:
        db_path = str((tmp_path / "main.db").absolute())

        def upsert_extracted_flag_for_host(self, *args, **kwargs):
            events.append("flag")

    dash = SimpleNamespace(
        db_reader=_Reader(),
        _protocol_label_from_host_type=lambda _kind: "SMB",
    )
    result = dashboard_batch_ops.extract_single_server(
        dash,
        {
            "host_type": "S", "ip_address": "203.0.113.7",
            "accessible_shares_list": "public", "protocol_server_id": 9,
            "port": 445,
        },
        10, 20, 30, 2, "allow_only", [], [], tmp_path,
        threading.Event(),
    )
    assert events[:2] == ["extract", "persist"]
    assert result["_analyst_reference"] is reference
    assert result["_analyst_file_count"] == 1
