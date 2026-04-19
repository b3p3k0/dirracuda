"""
Unit tests for gui.components.se_dork_browser_window.

Uses __new__ to bypass Tk construction — no display required.
"""

from __future__ import annotations

from concurrent.futures import Future
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

from gui.components.se_dork_browser_window import (
    COLUMNS,
    COL_HEADERS,
    COL_WIDTHS,
    PROBE_STATUS_EMOJI,
    SeDorkBrowserWindow,
)


# ---------------------------------------------------------------------------
# Helpers — build an instance without Tk
# ---------------------------------------------------------------------------


def _make_browser(**kwargs) -> SeDorkBrowserWindow:
    """Return a SeDorkBrowserWindow with all Tk construction bypassed."""
    obj = SeDorkBrowserWindow.__new__(SeDorkBrowserWindow)
    obj.parent = MagicMock()
    obj.db_path = kwargs.get("db_path", None)
    obj.theme = MagicMock()
    obj._add_record_callback = kwargs.get("add_record_callback", None)
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
        _FakeBatchStatusDialog.created.append(self)

    def update_progress(self, done, total, message=None):
        self.progress_calls.append((done, total, message))

    def mark_finished(self, status, notes):
        self.finished_calls.append((status, notes))

    def show(self):
        self.show_calls += 1


class _InlineExecutor:
    """Deterministic executor test double that runs tasks immediately."""

    created = []

    def __init__(self, max_workers=None, thread_name_prefix=None):
        self.max_workers = max_workers
        self.thread_name_prefix = thread_name_prefix
        self.shutdown_calls = []
        _InlineExecutor.created.append(self)

    def submit(self, fn, *args, **kwargs):
        fut = Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except Exception as exc:
            fut.set_exception(exc)
        return fut

    def shutdown(self, wait=True, cancel_futures=False):
        self.shutdown_calls.append((wait, cancel_futures))


# ---------------------------------------------------------------------------
# UI contract
# ---------------------------------------------------------------------------


def test_column_contract_only_url_and_checked_at():
    assert COLUMNS == ["url", "probe_status", "probe_preview", "probe_checked_at"]
    assert set(COL_HEADERS.keys()) == {"url", "probe_status", "probe_preview", "probe_checked_at"}
    assert set(COL_WIDTHS.keys()) == {"url", "probe_status", "probe_preview", "probe_checked_at"}
    assert COL_HEADERS["url"] == "URL"
    assert COL_HEADERS["probe_status"] == "Probed"
    assert COL_HEADERS["probe_preview"] == "Probe Preview"
    assert COL_HEADERS["probe_checked_at"] == "Checked"
    assert COL_WIDTHS["url"] == 300
    assert COL_WIDTHS["probe_status"] == 70
    assert COL_WIDTHS["probe_preview"] == 500


def test_probe_status_emoji_mapping_contract():
    assert PROBE_STATUS_EMOJI["clean"] == "✔"
    assert PROBE_STATUS_EMOJI["issue"] == "✖"
    assert PROBE_STATUS_EMOJI["unprobed"] == "○"


def test_build_window_sets_searxng_title():
    b = _make_browser()
    b._build_window()
    b.window.title.assert_called_once_with("SearXNG Dork Results")


def test_build_window_uses_extended_selection_and_wires_scrollbar():
    b = _make_browser()

    fake_tree = MagicMock()
    fake_scrollbar = MagicMock()

    with patch("gui.components.se_dork_browser_window.ttk.Treeview", return_value=fake_tree) as mock_tree_ctor:
        with patch("gui.components.se_dork_browser_window.ttk.Scrollbar", return_value=fake_scrollbar):
            b._build_window()

    kwargs = mock_tree_ctor.call_args.kwargs
    assert kwargs["selectmode"] == "extended"
    fake_scrollbar.config.assert_called_once_with(command=fake_tree.yview)
    assert b._v_scrollbar is fake_scrollbar


def test_build_window_sets_column_anchors():
    b = _make_browser()
    fake_tree = MagicMock()

    with patch("gui.components.se_dork_browser_window.ttk.Treeview", return_value=fake_tree):
        with patch("gui.components.se_dork_browser_window.ttk.Scrollbar", return_value=MagicMock()):
            b._build_window()

    column_calls = fake_tree.column.call_args_list
    anchors_by_col = {
        c.args[0]: c.kwargs.get("anchor")
        for c in column_calls
    }
    assert anchors_by_col["probe_status"] == "center"
    assert anchors_by_col["url"] == "w"
    assert anchors_by_col["probe_preview"] == "w"
    assert anchors_by_col["probe_checked_at"] == "w"


# ---------------------------------------------------------------------------
# _build_prefill
# ---------------------------------------------------------------------------


def test_build_prefill_http_url():
    b = _make_browser()
    row = {"url": "http://192.168.1.5:8080/files/"}
    prefill = b._build_prefill(row)
    assert prefill is not None
    assert prefill["host_type"] == "H"
    assert prefill["host"] == "192.168.1.5"
    assert prefill["port"] == 8080
    assert prefill["scheme"] == "http"
    assert prefill["_probe_host_hint"] == "192.168.1.5"
    assert prefill["_probe_path_hint"] == "/files/"
    assert prefill["_promotion_source"] == "se_dork_browser"


def test_build_prefill_https_default_port():
    b = _make_browser()
    row = {"url": "https://example.local/data"}
    prefill = b._build_prefill(row)
    assert prefill is not None
    assert prefill["port"] == 443
    assert prefill["scheme"] == "https"


def test_build_prefill_http_default_port():
    b = _make_browser()
    row = {"url": "http://example.local/"}
    prefill = b._build_prefill(row)
    assert prefill is not None
    assert prefill["port"] == 80


def test_build_prefill_explicit_port():
    b = _make_browser()
    row = {"url": "http://192.168.1.1:9999/some/path"}
    prefill = b._build_prefill(row)
    assert prefill is not None
    assert prefill["port"] == 9999
    assert prefill["_probe_path_hint"] == "/some/path"


def test_build_prefill_unsupported_scheme():
    b = _make_browser()
    row = {"url": "ftp://192.168.1.1/pub"}
    assert b._build_prefill(row) is None


def test_build_prefill_missing_hostname():
    b = _make_browser()
    row = {"url": "http:///some/path"}
    assert b._build_prefill(row) is None


def test_build_prefill_empty_url():
    b = _make_browser()
    row = {"url": ""}
    # empty scheme → not http/https → None
    assert b._build_prefill(row) is None


# ---------------------------------------------------------------------------
# _on_add_to_db — no callback
# ---------------------------------------------------------------------------


def test_on_add_to_db_no_callback_shows_not_available():
    b = _make_browser(add_record_callback=None)
    b.tree.selection.return_value = ["1"]
    b._row_by_iid["1"] = {"url": "http://192.168.1.1/"}

    with patch("gui.components.se_dork_browser_window.messagebox") as mock_mb:
        b._on_add_to_db()

    mock_mb.showinfo.assert_called_once()
    args = mock_mb.showinfo.call_args
    assert "Not available" in args[0][0] or "Not available" in str(args)


# ---------------------------------------------------------------------------
# _on_add_to_db — unsupported scheme
# ---------------------------------------------------------------------------


def test_on_add_to_db_unsupported_scheme_shows_message():
    callback = MagicMock()
    b = _make_browser(add_record_callback=callback)
    b.tree.selection.return_value = ["2"]
    b._row_by_iid["2"] = {"url": "ftp://192.168.1.1/pub"}

    with patch("gui.components.se_dork_browser_window.messagebox") as mock_mb:
        b._on_add_to_db()

    mock_mb.showinfo.assert_called_once()
    callback.assert_not_called()


# ---------------------------------------------------------------------------
# _on_add_to_db — callback invoked with correct prefill
# ---------------------------------------------------------------------------


def test_on_add_to_db_calls_callback_with_prefill():
    callback = MagicMock()
    b = _make_browser(add_record_callback=callback)
    b.tree.selection.return_value = ["3"]
    b._row_by_iid["3"] = {"url": "http://192.168.1.10:8080/files/"}

    # patch _resolve_prefill_host_ipv4 to return the host unchanged
    with patch.object(b, "_resolve_prefill_host_ipv4", return_value=("192.168.1.10", False)):
        b._on_add_to_db()

    callback.assert_called_once()
    prefill = callback.call_args[0][0]
    assert prefill["host"] == "192.168.1.10"
    assert prefill["port"] == 8080
    assert prefill["scheme"] == "http"
    assert prefill["host_type"] == "H"
    assert prefill["_promotion_source"] == "se_dork_browser"


def test_on_add_to_db_no_selection_does_not_call_callback():
    callback = MagicMock()
    b = _make_browser(add_record_callback=callback)
    b.tree.selection.return_value = []

    with patch("gui.components.se_dork_browser_window.messagebox"):
        b._on_add_to_db()

    callback.assert_not_called()


# ---------------------------------------------------------------------------
# QA hardening: _build_prefill robustness
# ---------------------------------------------------------------------------


def test_build_prefill_out_of_range_port_returns_none():
    """parsed.port raises ValueError for out-of-range ports; _build_prefill must return None."""
    b = _make_browser()
    # Port 99999 is out of range — urlparse accepts it syntactically but
    # parsed.port raises ValueError when accessed.
    row = {"url": "http://192.168.1.1:99999/files/"}
    result = b._build_prefill(row)
    assert result is None


def test_load_rows_purges_non_open_before_fetch():
    """Browser load enforces OPEN_INDEX-only historical purge before reading rows."""
    b = _make_browser()
    b.tree.get_children.return_value = []

    fake_conn = MagicMock()

    with patch("experimental.se_dork.store.init_db") as mock_init:
        with patch("experimental.se_dork.store.open_connection", return_value=fake_conn):
            with patch(
                "experimental.se_dork.store.delete_non_open_results",
                return_value=2,
            ) as mock_purge:
                with patch("experimental.se_dork.store.get_all_results", return_value=[]):
                    b._load_rows()

    mock_init.assert_called_once()
    mock_purge.assert_called_once_with(fake_conn, run_id=None)
    fake_conn.commit.assert_called_once()


def test_load_rows_inserts_only_visible_columns():
    """Tree insert values should match visible column contract."""
    b = _make_browser()
    b.tree.get_children.return_value = []
    fake_conn = MagicMock()
    rows = [
        {
            "result_id": 101,
            "url": "http://example.local/files/",
            "verdict": "OPEN_INDEX",
            "reason_code": None,
            "http_status": 200,
            "checked_at": "2026-04-18T15:01:02Z",
            "probe_status": "issue",
            "probe_preview": "pub,movies,[[loose files]]",
            "probe_checked_at": "2026-04-18T15:10:00Z",
        }
    ]

    with patch("experimental.se_dork.store.init_db"):
        with patch("experimental.se_dork.store.open_connection", return_value=fake_conn):
            with patch("experimental.se_dork.store.delete_non_open_results"):
                with patch("experimental.se_dork.store.get_all_results", return_value=rows):
                    b._load_rows()

    b.tree.insert.assert_called_once()
    values = b.tree.insert.call_args.kwargs["values"]
    assert values == (
        "http://example.local/files/",
        "✖",
        "pub,movies,[[loose files]]",
        "2026-04-18T15:10:00Z",
    )


# ---------------------------------------------------------------------------
# C9: Probe actions (button + context menu)
# ---------------------------------------------------------------------------


def test_on_probe_selected_no_selection_shows_info():
    b = _make_browser()
    b.tree.selection.return_value = []

    with patch("gui.components.se_dork_browser_window.messagebox") as mock_mb:
        b._on_probe_selected()

    mock_mb.showinfo.assert_called_once()


def test_on_probe_selected_updates_probe_fields_and_refreshes():
    _FakeBatchStatusDialog.created.clear()
    b = _make_browser()
    b.tree.selection.return_value = ["7", "8"]
    b._row_by_iid["7"] = {"result_id": 7, "url": "http://example.local/files/"}
    b._row_by_iid["8"] = {"result_id": 8, "url": "http://example.local/archive/"}
    b.tree.exists.return_value = True
    fake_conn = MagicMock()

    from experimental.se_dork.probe import ProbeOutcome

    outcome = ProbeOutcome(
        probe_status="clean",
        probe_indicator_matches=0,
        probe_preview="pub,movies",
        probe_checked_at="2026-04-19T10:00:00",
        probe_error=None,
    )

    with patch(
        "gui.components.se_dork_browser_window.BatchStatusDialog",
        _FakeBatchStatusDialog,
    ):
        with patch("experimental.se_dork.store.init_db"):
            with patch("experimental.se_dork.store.open_connection", return_value=fake_conn):
                with patch("experimental.se_dork.store.update_result_probe") as mock_update:
                    with patch("experimental.se_dork.probe.probe_url", return_value=outcome):
                        with patch.object(b, "_load_rows") as mock_reload:
                            b._on_probe_selected()

    fake_conn.commit.assert_called_once()
    assert mock_update.call_count == 2
    result_ids = [call.kwargs["result_id"] for call in mock_update.call_args_list]
    assert result_ids == [7, 8]
    mock_reload.assert_called_once()
    b.tree.selection_set.assert_called_once_with("7", "8")


def test_on_probe_selected_unprobed_shows_info():
    _FakeBatchStatusDialog.created.clear()
    b = _make_browser()
    b.tree.selection.return_value = ["9"]
    b._row_by_iid["9"] = {"result_id": 9, "url": "ftp://example.local/pub"}
    fake_conn = MagicMock()

    from experimental.se_dork.probe import ProbeOutcome

    outcome = ProbeOutcome(
        probe_status="unprobed",
        probe_indicator_matches=0,
        probe_preview=None,
        probe_checked_at="2026-04-19T10:00:00",
        probe_error="unsupported_scheme",
    )

    with patch(
        "gui.components.se_dork_browser_window.BatchStatusDialog",
        _FakeBatchStatusDialog,
    ):
        with patch("experimental.se_dork.store.init_db"):
            with patch("experimental.se_dork.store.open_connection", return_value=fake_conn):
                with patch("experimental.se_dork.store.update_result_probe"):
                    with patch("experimental.se_dork.probe.probe_url", return_value=outcome):
                        with patch("gui.components.se_dork_browser_window.messagebox") as mock_mb:
                            with patch.object(b, "_load_rows"):
                                b._on_probe_selected()

    mock_mb.showinfo.assert_called_once()


def test_on_probe_selected_opens_and_finishes_status_dialog():
    _FakeBatchStatusDialog.created.clear()
    b = _make_browser()
    b.tree.selection.return_value = ["7", "8"]
    b._row_by_iid["7"] = {"result_id": 7, "url": "http://example.local/files/"}
    b._row_by_iid["8"] = {"result_id": 8, "url": "http://example.local/archive/"}
    b.tree.exists.return_value = True
    fake_conn = MagicMock()

    from experimental.se_dork.probe import ProbeOutcome

    outcome = ProbeOutcome(
        probe_status="clean",
        probe_indicator_matches=0,
        probe_preview="pub,movies",
        probe_checked_at="2026-04-19T10:00:00",
        probe_error=None,
    )

    with patch(
        "gui.components.se_dork_browser_window.BatchStatusDialog",
        _FakeBatchStatusDialog,
    ):
        with patch("experimental.se_dork.store.init_db"):
            with patch("experimental.se_dork.store.open_connection", return_value=fake_conn):
                with patch("experimental.se_dork.store.update_result_probe"):
                    with patch("experimental.se_dork.probe.probe_url", return_value=outcome):
                        with patch.object(b, "_load_rows"):
                            b._on_probe_selected()

    assert len(_FakeBatchStatusDialog.created) == 1
    dlg = _FakeBatchStatusDialog.created[0]
    assert dlg.title == "Probe Status"
    assert dlg.fields["Target"] == "SearXNG Results"
    assert dlg.fields["Selected"] == "2"
    # start tick + one update per processed row
    assert dlg.progress_calls[0][0:2] == (0, 2)
    assert dlg.progress_calls[-1][0:2] == (2, 2)
    assert dlg.finished_calls
    assert dlg.finished_calls[-1][0] == "success"
    assert dlg.show_calls >= 1


def test_on_probe_selected_cancel_stops_remaining_rows():
    class _CancelAfterFirstDialog(_FakeBatchStatusDialog):
        def update_progress(self, done, total, message=None):
            super().update_progress(done, total, message)
            if done >= 1:
                self.on_cancel()

    _CancelAfterFirstDialog.created.clear()
    b = _make_browser()
    b.tree.selection.return_value = ["7", "8", "9"]
    b._row_by_iid["7"] = {"result_id": 7, "url": "http://example.local/files/"}
    b._row_by_iid["8"] = {"result_id": 8, "url": "http://example.local/archive/"}
    b._row_by_iid["9"] = {"result_id": 9, "url": "http://example.local/docs/"}
    b.tree.exists.return_value = True
    fake_conn = MagicMock()

    from experimental.se_dork.probe import ProbeOutcome

    outcome = ProbeOutcome(
        probe_status="clean",
        probe_indicator_matches=0,
        probe_preview="pub,movies",
        probe_checked_at="2026-04-19T10:00:00",
        probe_error=None,
    )

    with patch(
        "gui.components.se_dork_browser_window.BatchStatusDialog",
        _CancelAfterFirstDialog,
    ):
        with patch("experimental.se_dork.store.init_db"):
            with patch("experimental.se_dork.store.open_connection", return_value=fake_conn):
                with patch("experimental.se_dork.store.update_result_probe") as mock_update:
                    with patch("experimental.se_dork.probe.probe_url", return_value=outcome):
                        with patch.object(b, "_load_rows"):
                            b._on_probe_selected()

    assert len(_CancelAfterFirstDialog.created) == 1
    dlg = _CancelAfterFirstDialog.created[0]
    assert mock_update.call_count == 1
    assert dlg.finished_calls
    assert dlg.finished_calls[-1][0] == "cancelled"


def test_on_probe_selected_hard_failure_marks_dialog_failed():
    _FakeBatchStatusDialog.created.clear()
    b = _make_browser()
    b.tree.selection.return_value = ["7"]
    b._row_by_iid["7"] = {"result_id": 7, "url": "http://example.local/files/"}
    fake_conn = MagicMock()

    from experimental.se_dork.probe import ProbeOutcome

    outcome = ProbeOutcome(
        probe_status="clean",
        probe_indicator_matches=0,
        probe_preview="pub,movies",
        probe_checked_at="2026-04-19T10:00:00",
        probe_error=None,
    )

    with patch(
        "gui.components.se_dork_browser_window.BatchStatusDialog",
        _FakeBatchStatusDialog,
    ):
        with patch("experimental.se_dork.store.init_db"):
            with patch("experimental.se_dork.store.open_connection", return_value=fake_conn):
                with patch(
                    "experimental.se_dork.store.update_result_probe",
                    side_effect=RuntimeError("boom"),
                ):
                    with patch("experimental.se_dork.probe.probe_url", return_value=outcome):
                        with patch("gui.components.se_dork_browser_window.messagebox") as mock_mb:
                            with patch.object(b, "_load_rows") as mock_reload:
                                b._on_probe_selected()

    assert len(_FakeBatchStatusDialog.created) == 1
    dlg = _FakeBatchStatusDialog.created[0]
    assert dlg.finished_calls
    assert dlg.finished_calls[-1][0] == "failed"
    assert dlg.show_calls >= 1
    mock_mb.showinfo.assert_called_once()
    mock_reload.assert_not_called()


def test_on_probe_selected_uses_configured_worker_count():
    _FakeBatchStatusDialog.created.clear()
    _InlineExecutor.created.clear()
    sm = MagicMock()
    sm.get_setting.return_value = 2
    b = _make_browser(settings_manager=sm)
    b.tree.selection.return_value = ["7", "8", "9"]
    b._row_by_iid["7"] = {"result_id": 7, "url": "http://example.local/files/"}
    b._row_by_iid["8"] = {"result_id": 8, "url": "http://example.local/archive/"}
    b._row_by_iid["9"] = {"result_id": 9, "url": "http://example.local/docs/"}
    b.tree.exists.return_value = True
    fake_conn = MagicMock()

    from experimental.se_dork.probe import ProbeOutcome

    outcome = ProbeOutcome(
        probe_status="clean",
        probe_indicator_matches=0,
        probe_preview="pub",
        probe_checked_at="2026-04-19T10:00:00",
        probe_error=None,
    )

    with patch(
        "gui.components.se_dork_browser_window.BatchStatusDialog",
        _FakeBatchStatusDialog,
    ):
        with patch(
            "gui.components.se_dork_browser_window.ThreadPoolExecutor",
            _InlineExecutor,
        ):
            with patch("experimental.se_dork.store.init_db"):
                with patch("experimental.se_dork.store.open_connection", return_value=fake_conn):
                    with patch("experimental.se_dork.store.update_result_probe"):
                        with patch("experimental.se_dork.probe.probe_url", return_value=outcome):
                            with patch.object(b, "_load_rows"):
                                b._on_probe_selected()

    assert len(_InlineExecutor.created) == 1
    assert _InlineExecutor.created[0].max_workers == 2


def test_on_probe_selected_invalid_worker_setting_falls_back_to_default():
    _FakeBatchStatusDialog.created.clear()
    _InlineExecutor.created.clear()
    sm = MagicMock()
    sm.get_setting.return_value = "bad"
    b = _make_browser(settings_manager=sm)
    b.tree.selection.return_value = ["1", "2", "3", "4", "5"]
    for i in range(1, 6):
        b._row_by_iid[str(i)] = {"result_id": i, "url": f"http://example.local/{i}"}
    b.tree.exists.return_value = True
    fake_conn = MagicMock()

    from experimental.se_dork.probe import ProbeOutcome

    outcome = ProbeOutcome(
        probe_status="clean",
        probe_indicator_matches=0,
        probe_preview="pub",
        probe_checked_at="2026-04-19T10:00:00",
        probe_error=None,
    )

    with patch(
        "gui.components.se_dork_browser_window.BatchStatusDialog",
        _FakeBatchStatusDialog,
    ):
        with patch(
            "gui.components.se_dork_browser_window.ThreadPoolExecutor",
            _InlineExecutor,
        ):
            with patch("experimental.se_dork.store.init_db"):
                with patch("experimental.se_dork.store.open_connection", return_value=fake_conn):
                    with patch("experimental.se_dork.store.update_result_probe"):
                        with patch("experimental.se_dork.probe.probe_url", return_value=outcome):
                            with patch.object(b, "_load_rows"):
                                b._on_probe_selected()

    assert len(_InlineExecutor.created) == 1
    # default fallback is 3; selected rows are 5 so pool remains 3
    assert _InlineExecutor.created[0].max_workers == 3


def test_on_context_probe_url_hides_menu_and_delegates():
    b = _make_browser()
    with patch.object(b, "_hide_context_menu") as mock_hide:
        with patch.object(b, "_on_probe_selected") as mock_probe:
            b._on_context_probe_url()
    mock_hide.assert_called_once()
    mock_probe.assert_called_once()


def test_selected_rows_returns_all_selected_rows_in_order():
    b = _make_browser()
    b.tree.selection.return_value = ["2", "1", "9"]
    b._row_by_iid["1"] = {"result_id": 1}
    b._row_by_iid["2"] = {"result_id": 2}
    # iid 9 intentionally missing

    rows = b._selected_rows()

    assert rows == [{"result_id": 2}, {"result_id": 1}]


def test_right_click_preserves_multi_selection_when_clicked_row_already_selected():
    b = _make_browser()
    b.tree.identify_row.return_value = "2"
    b.tree.selection.return_value = ["1", "2", "3"]
    event = MagicMock(y=5, x_root=10, y_root=20)

    b._on_right_click(event)

    b.tree.selection_set.assert_not_called()
    b._context_menu.post.assert_called_once_with(10, 20)


def test_right_click_selects_only_clicked_row_when_not_in_selection():
    b = _make_browser()
    b.tree.identify_row.return_value = "2"
    b.tree.selection.return_value = ["1", "3"]
    event = MagicMock(y=5, x_root=10, y_root=20)

    b._on_right_click(event)

    b.tree.selection_set.assert_called_once_with("2")
    b._context_menu.post.assert_called_once_with(10, 20)


# ---------------------------------------------------------------------------
# C7: Open in Explorer action wiring
# ---------------------------------------------------------------------------


def test_on_open_explorer_no_selection_shows_info():
    b = _make_browser()
    b.tree.selection.return_value = []

    with patch("gui.components.se_dork_browser_window.messagebox") as mock_mb:
        with patch("gui.components.se_dork_browser_window.open_ftp_http_browser") as mock_open:
            b._on_open_explorer()

    mock_mb.showinfo.assert_called_once()
    mock_open.assert_not_called()


def test_on_open_explorer_http_defaults_port_and_path():
    b = _make_browser()
    b.tree.selection.return_value = ["1"]
    b._row_by_iid["1"] = {"url": "http://example.local"}

    with patch("gui.components.se_dork_browser_window.open_ftp_http_browser") as mock_open:
        b._on_open_explorer()

    mock_open.assert_called_once()
    args, kwargs = mock_open.call_args
    assert args[0] == "H"
    assert args[1] is b.window
    assert args[2] == "example.local"
    assert args[3] == 80
    assert kwargs["initial_path"] == "/"
    assert kwargs["scheme"] == "http"
    assert kwargs["theme"] is b.theme


def test_on_open_explorer_https_explicit_port_and_path():
    b = _make_browser()
    b.tree.selection.return_value = ["2"]
    b._row_by_iid["2"] = {"url": "https://example.local:8443/files/"}

    with patch("gui.components.se_dork_browser_window.open_ftp_http_browser") as mock_open:
        b._on_open_explorer()

    mock_open.assert_called_once()
    args, kwargs = mock_open.call_args
    assert args[0] == "H"
    assert args[1] is b.window
    assert args[2] == "example.local"
    assert args[3] == 8443
    assert kwargs["initial_path"] == "/files/"
    assert kwargs["scheme"] == "https"
    assert kwargs["theme"] is b.theme


def test_on_open_explorer_ftp_defaults_port_and_path():
    b = _make_browser()
    b.tree.selection.return_value = ["3"]
    b._row_by_iid["3"] = {"url": "ftp://example.local/pub"}

    with patch("gui.components.se_dork_browser_window.open_ftp_http_browser") as mock_open:
        b._on_open_explorer()

    mock_open.assert_called_once()
    args, kwargs = mock_open.call_args
    assert args[0] == "F"
    assert args[1] is b.window
    assert args[2] == "example.local"
    assert args[3] == 21
    assert kwargs["initial_path"] == "/pub"
    assert kwargs["scheme"] is None
    assert kwargs["theme"] is b.theme


def test_on_open_explorer_invalid_or_unsupported_url_shows_info_and_skips_open():
    b = _make_browser()
    b.tree.selection.return_value = ["4"]
    b._row_by_iid["4"] = {"url": "smb://example.local/share"}

    with patch("gui.components.se_dork_browser_window.messagebox") as mock_mb:
        with patch("gui.components.se_dork_browser_window.open_ftp_http_browser") as mock_open:
            b._on_open_explorer()

    mock_mb.showinfo.assert_called_once()
    mock_open.assert_not_called()


def test_on_context_open_explorer_hides_menu_and_delegates():
    b = _make_browser()
    b._hide_context_menu = MagicMock()
    b._on_open_explorer = MagicMock()

    b._on_context_open_explorer()

    b._hide_context_menu.assert_called_once()
    b._on_open_explorer.assert_called_once()
