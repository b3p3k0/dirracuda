"""
Unit tests for gui.components.censys_browser_window.

Uses __new__ to bypass Tk construction — no display required.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Lightweight impacket stub so GUI modules import cleanly in headless test env.
if "impacket" not in sys.modules:
    _imod = types.ModuleType("impacket")
    _ismb = types.ModuleType("impacket.smb")
    _ismb.SMB2_DIALECT_002 = object()
    _iconn = types.ModuleType("impacket.smbconnection")
    _iconn.SMBConnection = object

    class _SessionError(Exception):
        pass

    _iconn.SessionError = _SessionError
    _imod.smb = _ismb
    sys.modules["impacket"] = _imod
    sys.modules["impacket.smb"] = _ismb
    sys.modules["impacket.smbconnection"] = _iconn

from gui.components.censys_browser_window import (
    COLUMNS,
    COL_HEADERS,
    COL_WIDTHS,
    CensysBrowserWindow,
    show_censys_browser_window,
)
from gui.utils.sidecar_promotion import SidecarPromotionError


# ---------------------------------------------------------------------------
# Helpers — build an instance without Tk
# ---------------------------------------------------------------------------


def _make_browser(**kwargs) -> CensysBrowserWindow:
    """Return a CensysBrowserWindow with all Tk construction bypassed."""
    obj = CensysBrowserWindow.__new__(CensysBrowserWindow)
    obj.parent = MagicMock()
    obj.db_path = kwargs.get("db_path", None)
    obj.theme = MagicMock()
    obj._add_record_callback = kwargs.get("add_record_callback", None)
    obj._promote_record_callback = kwargs.get("promote_record_callback", None)
    obj._promote_records_callback = kwargs.get("promote_records_callback", None)
    obj._settings_manager = kwargs.get("settings_manager", None)
    obj._row_by_iid = {}
    obj._context_menu_visible = False
    obj.window = MagicMock()
    obj.tree = MagicMock()
    obj._status_label = MagicMock()
    obj._context_menu = MagicMock()
    return obj


class _FakeBatchStatusDialog:
    """Headless-safe stand-in for BatchStatusDialog."""

    created = []

    def __init__(self, parent, theme, *, title, fields, on_cancel, total=None):
        self.parent = parent
        self.theme = theme
        self.title = title
        self.fields = fields
        self.on_cancel = on_cancel
        self.total = total
        self.window = None
        self.progress_calls = []
        self.finished_calls = []
        self.show_calls = 0
        self.destroy_calls = 0
        _FakeBatchStatusDialog.created.append(self)

    def update_progress(self, done, total, message=None):
        self.progress_calls.append((done, total, message))

    def mark_finished(self, status, notes):
        self.finished_calls.append((status, notes))

    def show(self):
        self.show_calls += 1

    def destroy(self):
        self.destroy_calls += 1


# ---------------------------------------------------------------------------
# UI contract
# ---------------------------------------------------------------------------


def test_column_contract():
    assert COLUMNS == [
        "protocol", "ip_address", "port", "transport_protocol", "banner", "scan_time"
    ]
    assert set(COL_HEADERS.keys()) == set(COLUMNS)
    assert set(COL_WIDTHS.keys()) == set(COLUMNS)


def test_column_headers():
    assert COL_HEADERS["protocol"] == "Protocol"
    assert COL_HEADERS["ip_address"] == "IP Address"
    assert COL_HEADERS["port"] == "Port"
    assert COL_HEADERS["transport_protocol"] == "Transport"
    assert COL_HEADERS["banner"] == "Banner"
    assert COL_HEADERS["scan_time"] == "Scanned"


def test_column_widths():
    assert COL_WIDTHS["protocol"] == 70
    assert COL_WIDTHS["ip_address"] == 130
    assert COL_WIDTHS["port"] == 60
    assert COL_WIDTHS["transport_protocol"] == 70
    assert COL_WIDTHS["banner"] == 400
    assert COL_WIDTHS["scan_time"] == 150


# ---------------------------------------------------------------------------
# _build_prefill — protocol routing
# ---------------------------------------------------------------------------


def test_build_prefill_ftp():
    b = _make_browser()
    row = {"protocol": "FTP", "ip_address": "10.0.0.1", "port": 21}
    prefill = b._build_prefill(row)
    assert prefill is not None
    assert prefill["host_type"] == "F"
    assert prefill["host"] == "10.0.0.1"
    assert prefill["ip_address"] == "10.0.0.1"
    assert prefill["port"] == 21
    assert prefill["_promotion_source"] == "censys_browser"


def test_build_prefill_http_port_80():
    b = _make_browser()
    row = {"protocol": "HTTP", "ip_address": "10.0.0.2", "port": 80}
    prefill = b._build_prefill(row)
    assert prefill is not None
    assert prefill["host_type"] == "H"
    assert prefill["host"] == "10.0.0.2"
    assert prefill["ip_address"] == "10.0.0.2"
    assert prefill["port"] == 80
    assert prefill["scheme"] == "http"
    assert prefill["_promotion_source"] == "censys_browser"


def test_build_prefill_http_port_443_gets_https_scheme():
    b = _make_browser()
    row = {"protocol": "HTTP", "ip_address": "10.0.0.3", "port": 443}
    prefill = b._build_prefill(row)
    assert prefill is not None
    assert prefill["scheme"] == "https"


def test_build_prefill_smb():
    b = _make_browser()
    row = {"protocol": "SMB", "ip_address": "10.0.0.4", "port": 445}
    prefill = b._build_prefill(row)
    assert prefill is not None
    assert prefill["host_type"] == "S"
    assert prefill["host"] == "10.0.0.4"
    assert prefill["ip_address"] == "10.0.0.4"
    assert prefill["_promotion_source"] == "censys_browser"


def test_build_prefill_protocol_case_insensitive():
    b = _make_browser()
    row = {"protocol": "ftp", "ip_address": "10.0.0.5", "port": 21}
    prefill = b._build_prefill(row)
    assert prefill is not None
    assert prefill["host_type"] == "F"


def test_build_prefill_unsupported_protocol_returns_none():
    b = _make_browser()
    row = {"protocol": "TELNET", "ip_address": "10.0.0.6", "port": 23}
    assert b._build_prefill(row) is None


def test_build_prefill_empty_ip_returns_none():
    b = _make_browser()
    row = {"protocol": "FTP", "ip_address": "", "port": 21}
    assert b._build_prefill(row) is None


def test_build_prefill_missing_ip_returns_none():
    b = _make_browser()
    row = {"protocol": "HTTP", "port": 80}
    assert b._build_prefill(row) is None


# ---------------------------------------------------------------------------
# _load_rows — uses list_results via monkeypatch
# ---------------------------------------------------------------------------


def test_load_rows_populates_tree_from_list_results():
    b = _make_browser()
    fake_rows = [
        {
            "result_id": 1, "run_id": 1, "protocol": "FTP",
            "ip_address": "10.0.0.1", "port": 21,
            "transport_protocol": "TCP", "banner": "220 ok", "scan_time": "2026-01-01",
            "source_json": "{}",
        }
    ]
    # Store functions are imported locally inside _load_rows; patch via the store module.
    with patch("experimental.censys_discovery.store.init_db"), \
         patch("experimental.censys_discovery.store.open_connection") as mock_conn_fn, \
         patch("experimental.censys_discovery.store.list_results", return_value=fake_rows):
        mock_conn = MagicMock()
        mock_conn_fn.return_value = mock_conn
        b._load_rows()

    b.tree.insert.assert_called_once()
    call_kwargs = b.tree.insert.call_args
    values = call_kwargs[1]["values"] if "values" in call_kwargs[1] else call_kwargs[0][3]
    assert "FTP" in values
    assert "10.0.0.1" in values
    assert 21 in values


def test_load_rows_passes_db_path_to_store():
    custom_path = Path("/tmp/test_censys.db")
    b = _make_browser(db_path=custom_path)
    with patch("experimental.censys_discovery.store.init_db") as mock_init, \
         patch("experimental.censys_discovery.store.open_connection") as mock_open, \
         patch("experimental.censys_discovery.store.list_results", return_value=[]):
        mock_open.return_value = MagicMock()
        b._load_rows()
    mock_init.assert_called_once_with(custom_path)
    mock_open.assert_called_once_with(custom_path)


def test_load_rows_none_db_path_passes_none_to_store():
    b = _make_browser(db_path=None)
    with patch("experimental.censys_discovery.store.init_db") as mock_init, \
         patch("experimental.censys_discovery.store.open_connection") as mock_open, \
         patch("experimental.censys_discovery.store.list_results", return_value=[]):
        mock_open.return_value = MagicMock()
        b._load_rows()
    mock_init.assert_called_once_with(None)
    mock_open.assert_called_once_with(None)


def test_load_rows_on_exception_shows_error_in_status():
    b = _make_browser()
    with patch("experimental.censys_discovery.store.init_db", side_effect=RuntimeError("boom")):
        b._load_rows()
    b._status_label.configure.assert_called()
    call_args = b._status_label.configure.call_args
    assert "Load error" in call_args[1]["text"]


# ---------------------------------------------------------------------------
# _on_add_to_db_single — single promotion paths
# ---------------------------------------------------------------------------


def test_single_add_direct_promote_callback_success():
    callback = MagicMock(return_value={
        "payload": {"host_type": "F", "ip_address": "10.0.0.1", "port": 21},
        "result": {"host_type": "F", "operation": "insert"},
        "probe_cache_copied": False,
        "probe_snapshot_copied": False,
    })
    b = _make_browser(promote_record_callback=callback)
    b.tree.selection.return_value = ["1"]
    b._row_by_iid["1"] = {"protocol": "FTP", "ip_address": "10.0.0.1", "port": 21}

    with patch("gui.components.censys_browser_window.messagebox") as mock_mb:
        b._on_add_to_db()

    callback.assert_called_once()
    prefill = callback.call_args[0][0]
    assert prefill["host_type"] == "F"
    mock_mb.showinfo.assert_called_once()
    assert mock_mb.showinfo.call_args[0][0] == "Record Added"


def test_single_add_sidecar_promotion_error():
    callback = MagicMock(side_effect=SidecarPromotionError("bad host"))
    b = _make_browser(promote_record_callback=callback)
    b.tree.selection.return_value = ["2"]
    b._row_by_iid["2"] = {"protocol": "FTP", "ip_address": "10.0.0.2", "port": 21}

    with patch("gui.components.censys_browser_window.messagebox") as mock_mb:
        b._on_add_to_db()

    mock_mb.showinfo.assert_called_once()
    assert mock_mb.showinfo.call_args[0][0] == "Cannot promote"


def test_single_add_generic_exception_shows_error():
    callback = MagicMock(side_effect=RuntimeError("unexpected"))
    b = _make_browser(promote_record_callback=callback)
    b.tree.selection.return_value = ["3"]
    b._row_by_iid["3"] = {"protocol": "HTTP", "ip_address": "10.0.0.3", "port": 80}

    with patch("gui.components.censys_browser_window.messagebox") as mock_mb:
        b._on_add_to_db()

    mock_mb.showerror.assert_called_once()
    assert mock_mb.showerror.call_args[0][0] == "Add Record Failed"


def test_single_add_fallback_add_record_callback():
    add_cb = MagicMock()
    b = _make_browser(add_record_callback=add_cb)
    b.tree.selection.return_value = ["4"]
    b._row_by_iid["4"] = {"protocol": "SMB", "ip_address": "10.0.0.4", "port": 445}

    b._on_add_to_db()

    add_cb.assert_called_once()
    prefill = add_cb.call_args[0][0]
    assert prefill["host_type"] == "S"


def test_single_add_no_callbacks_shows_unavailable():
    b = _make_browser()
    b.tree.selection.return_value = ["5"]
    b._row_by_iid["5"] = {"protocol": "HTTP", "ip_address": "10.0.0.5", "port": 80}

    with patch("gui.components.censys_browser_window.messagebox") as mock_mb:
        b._on_add_to_db()

    mock_mb.showinfo.assert_called_once()
    assert mock_mb.showinfo.call_args[0][0] == "Main database unavailable"


def test_single_add_unsupported_protocol_shows_cannot_promote():
    b = _make_browser(promote_record_callback=MagicMock())
    b.tree.selection.return_value = ["6"]
    b._row_by_iid["6"] = {"protocol": "TELNET", "ip_address": "10.0.0.6", "port": 23}

    with patch("gui.components.censys_browser_window.messagebox") as mock_mb:
        b._on_add_to_db()

    mock_mb.showinfo.assert_called_once()
    assert mock_mb.showinfo.call_args[0][0] == "Cannot promote"


def test_on_add_to_db_no_selection_shows_info():
    b = _make_browser(promote_record_callback=MagicMock())
    b.tree.selection.return_value = []

    with patch("gui.components.censys_browser_window.messagebox") as mock_mb:
        b._on_add_to_db()

    mock_mb.showinfo.assert_called_once()
    assert mock_mb.showinfo.call_args[0][0] == "No selection"


# ---------------------------------------------------------------------------
# _on_add_to_db_bulk — bulk promotion paths
# ---------------------------------------------------------------------------


def test_bulk_add_missing_callback_shows_guidance():
    b = _make_browser()  # no promote_records_callback
    b.tree.selection.return_value = ["10", "11"]
    b._row_by_iid["10"] = {"protocol": "FTP", "ip_address": "10.0.0.10", "port": 21}
    b._row_by_iid["11"] = {"protocol": "HTTP", "ip_address": "10.0.0.11", "port": 80}

    with patch("gui.components.censys_browser_window.messagebox") as mock_mb:
        b._on_add_to_db()

    mock_mb.showinfo.assert_called_once()
    assert mock_mb.showinfo.call_args[0][0] == "Bulk import unavailable"


def test_bulk_add_collects_prefills_and_skips_unsupported():
    bulk_cb = MagicMock()
    b = _make_browser(promote_records_callback=bulk_cb)
    b.tree.selection.return_value = ["20", "21", "22"]
    b._row_by_iid["20"] = {"protocol": "FTP", "ip_address": "10.0.0.20", "port": 21}
    b._row_by_iid["21"] = {"protocol": "TELNET", "ip_address": "10.0.0.21", "port": 23}
    b._row_by_iid["22"] = {"protocol": "SMB", "ip_address": "10.0.0.22", "port": 445}
    b._start_bulk_promotion = MagicMock()

    b._on_add_to_db()

    b._start_bulk_promotion.assert_called_once()
    kwargs = b._start_bulk_promotion.call_args.kwargs
    assert kwargs["selected_count"] == 3
    assert len(kwargs["prefills"]) == 2
    assert len(kwargs["skipped_reasons"]) == 1
    assert "TELNET" in kwargs["skipped_reasons"][0]


def test_bulk_add_summary_merge_includes_skipped_samples():
    b = _make_browser()
    skipped = ["TELNET 10.0.0.1: unsupported protocol."]
    summary = {
        "processed": 2,
        "inserted": 1,
        "updated": 1,
        "skipped": 0,
        "failed": 0,
        "skipped_reason_samples": [],
        "failed_reason_samples": [],
    }
    merged = b._merge_bulk_promotion_summary(3, skipped, summary)
    assert merged["selected"] == 3
    assert merged["skipped"] == 1
    assert "TELNET" in merged["skipped_reason_samples"][0]


def test_bulk_add_worker_success_and_progress(monkeypatch):
    """Worker completes synchronously: progress and done both reach the queue."""
    import queue as _queue

    result_summary = {
        "processed": 2, "inserted": 1, "updated": 1,
        "skipped": 0, "failed": 0,
        "skipped_reason_samples": [], "failed_reason_samples": [],
    }

    def _fake_promote_many(prefills, cancel_event=None, progress_callback=None):
        if progress_callback:
            progress_callback(1, 2, "processing...")
        return result_summary

    b = _make_browser(promote_records_callback=_fake_promote_many)

    captured_summaries = []
    with patch("gui.components.censys_browser_window.BatchStatusDialog", _FakeBatchStatusDialog), \
         patch("gui.components.censys_browser_window.messagebox") as mock_mb:
        # Run synchronously by polling the queue manually
        updates = _queue.Queue()

        original_thread = __import__("threading").Thread

        def _sync_thread(target=None, daemon=None):
            class _T:
                def start(self_inner):
                    target()

                def is_alive(self_inner):
                    return False

            return _T()

        monkeypatch.setattr("gui.components.censys_browser_window.threading.Thread", _sync_thread)

        # Patch after() to execute immediately
        after_calls = []
        b.window.after = lambda delay, fn: (after_calls.append(fn), fn())

        b._start_bulk_promotion(
            prefills=[{"host_type": "F", "host": "10.0.0.1", "ip_address": "10.0.0.1", "port": 21}],
            skipped_reasons=[],
            selected_count=1,
        )

    assert mock_mb.showinfo.called
    title = mock_mb.showinfo.call_args[0][0]
    assert title == "Bulk import summary"


def test_bulk_add_worker_failure_shows_error(monkeypatch):
    def _bad_promote(prefills, cancel_event=None, progress_callback=None):
        raise RuntimeError("DB exploded")

    b = _make_browser(promote_records_callback=_bad_promote)

    with patch("gui.components.censys_browser_window.BatchStatusDialog", _FakeBatchStatusDialog), \
         patch("gui.components.censys_browser_window.messagebox") as mock_mb:
        def _sync_thread(target=None, daemon=None):
            class _T:
                def start(self_inner):
                    target()

                def is_alive(self_inner):
                    return False

            return _T()

        monkeypatch.setattr("gui.components.censys_browser_window.threading.Thread", _sync_thread)
        b.window.after = lambda delay, fn: fn()

        b._start_bulk_promotion(
            prefills=[{"host_type": "H", "host": "10.0.0.1", "ip_address": "10.0.0.1", "port": 80}],
            skipped_reasons=[],
            selected_count=1,
        )

    assert mock_mb.showerror.called
    assert "Bulk import failed" in mock_mb.showerror.call_args[0][0]


# ---------------------------------------------------------------------------
# Details view — source_json content
# ---------------------------------------------------------------------------


def test_format_result_details_includes_source_json():
    b = _make_browser()
    row = {
        "protocol": "FTP",
        "ip_address": "10.0.0.1",
        "port": 21,
        "transport_protocol": "TCP",
        "banner": "220 FTP server",
        "scan_time": "2026-01-01T00:00:00",
        "run_id": 7,
        "source_json": '{"port": 21, "service": "ftp"}',
    }
    text = b._format_result_details(row)
    assert "source_json" in text.lower() or "Source JSON" in text
    assert '"port": 21' in text
    assert '"service": "ftp"' in text


def test_format_result_details_invalid_json_shown_raw():
    b = _make_browser()
    row = {
        "protocol": "HTTP", "ip_address": "10.0.0.2",
        "port": 80, "transport_protocol": None,
        "banner": None, "scan_time": None, "run_id": 1,
        "source_json": "not-valid-json{{",
    }
    text = b._format_result_details(row)
    assert "not-valid-json" in text


# ---------------------------------------------------------------------------
# show_censys_browser_window factory
# ---------------------------------------------------------------------------


def test_show_censys_browser_window_instantiates_class():
    with patch(
        "gui.components.censys_browser_window.CensysBrowserWindow"
    ) as mock_cls:
        show_censys_browser_window(MagicMock(), db_path=None)
    mock_cls.assert_called_once()


def test_show_censys_browser_window_passes_positional_parent():
    parent = MagicMock()
    with patch("gui.components.censys_browser_window.CensysBrowserWindow") as mock_cls:
        show_censys_browser_window(parent)
    assert mock_cls.call_args[0][0] is parent
