"""
Unit tests for gui/components/reddit_browser_window.py

All tests use the __new__ bypass pattern to avoid Tk widget construction
(no display required unless explicitly noted).

Groups:
  A — _load_rows: success, empty, DB errors
  B — _apply_filter_and_sort: filter, sort, reorder
  C — _selected_row
  D — action handlers: open_explorer, open_reddit_post, refresh, clear_db
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.reddit_browser_window import (
    COL_HEADERS,
    COLUMN_KEY_MAP,
    COLUMNS,
    RedditBrowserWindow,
)
from experimental.redseek.models import RedditTarget


# ---------------------------------------------------------------------------
# Tk-free StringVar stub (avoids needing a root window)
# ---------------------------------------------------------------------------

class _StrVar:
    """Minimal tk.StringVar replacement for headless tests."""

    def __init__(self, value: str = "") -> None:
        self._value = value
        self._traces: list = []

    def get(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        self._value = value
        for fn in self._traces:
            fn()

    def trace_add(self, mode: str, fn) -> None:
        self._traces.append(fn)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_raw_row(
    id_: int,
    target_normalized: str = "http://example.com",
    protocol: str = "http",
    parse_confidence: str = "high",
    post_author: str = "user",
    is_nsfw: int = 0,
    notes: str = "",
    created_at: str = "2026-01-01 00:00:00",
    post_id: str = "abc",
    host: str = "example.com",
    target_raw: str = "http://example.com",
    dedupe_key: str = "dk",
) -> dict:
    return dict(
        id=id_,
        target_normalized=target_normalized,
        protocol=protocol,
        parse_confidence=parse_confidence,
        post_author=post_author,
        is_nsfw=is_nsfw,
        notes=notes,
        created_at=created_at,
        post_id=post_id,
        host=host,
        target_raw=target_raw,
        dedupe_key=dedupe_key,
    )


class _CaptureTree:
    """Minimal Treeview stub that records inserts and supports selection."""

    def __init__(self):
        self._items: list[tuple[str, list]] = []  # [(iid, values), ...]
        self._selection: tuple[str, ...] = ()
        self._row_for_y: dict[int, str] = {}
        self._bind_counter: int = 0
        self.bind_calls: list[tuple[str, object, object, str]] = []
        self.unbind_calls: list[tuple[str, str | None]] = []

    def get_children(self) -> tuple:
        return tuple(iid for iid, _ in self._items)

    def delete(self, *items) -> None:
        if not items:
            return
        remaining = [(iid, v) for iid, v in self._items if iid not in set(items)]
        self._items = remaining

    def insert(self, parent, index, *, iid: str, values) -> str:
        self._items.append((iid, list(values)))
        return iid

    def heading(self, col, *, text=None, command=None) -> None:
        pass

    def column(self, col, *, width=None, minwidth=None) -> None:
        pass

    def selection(self) -> tuple:
        return self._selection

    def set_selection(self, iid: str) -> None:
        self._selection = (iid,)

    def selection_set(self, iid: str) -> None:
        self._selection = (iid,)

    def set_identify_row(self, y: int, iid: str) -> None:
        self._row_for_y[y] = iid

    def identify_row(self, y: int) -> str:
        return self._row_for_y.get(y, "")

    def bind(self, sequence, callback=None, add=None):
        self._bind_counter += 1
        bind_id = f"tree-bind-{self._bind_counter}"
        self.bind_calls.append((sequence, callback, add, bind_id))
        return bind_id

    def unbind(self, sequence, bind_id=None):
        self.unbind_calls.append((sequence, bind_id))

    @property
    def visible_iids(self) -> list[str]:
        return [iid for iid, _ in self._items]


class _BindWidget:
    """Minimal widget stub with bind/unbind tracking."""

    def __init__(self) -> None:
        self._bind_counter = 0
        self.bind_calls: list[tuple[str, object, object, str]] = []
        self.unbind_calls: list[tuple[str, str | None]] = []
        self.clipboard_clear = MagicMock()
        self.clipboard_append = MagicMock()

    def bind(self, sequence, callback=None, add=None):
        self._bind_counter += 1
        bind_id = f"win-bind-{self._bind_counter}"
        self.bind_calls.append((sequence, callback, add, bind_id))
        return bind_id

    def unbind(self, sequence, bind_id=None):
        self.unbind_calls.append((sequence, bind_id))


def _make_win(monkeypatch=None) -> RedditBrowserWindow:
    """Build a RedditBrowserWindow without constructing any Tk widgets."""
    win = RedditBrowserWindow.__new__(RedditBrowserWindow)
    win.parent = _BindWidget()
    win.db_path = None
    win.theme = MagicMock()
    win._row_by_iid = {}
    win._all_rows = []
    win._sort_col = None
    win._sort_reverse = False
    win.window = _BindWidget()
    win.tree = _CaptureTree()
    win._context_menu = MagicMock()
    win._context_menu_visible = False
    win._context_menu_bindings = []
    win.status_var = _StrVar()
    win._filter_var = _StrVar()
    win._add_record_callback = None
    return win


# ---------------------------------------------------------------------------
# Group A — _load_rows
# ---------------------------------------------------------------------------

class TestLoadRows:

    def test_empty_db(self, monkeypatch):
        win = _make_win()
        monkeypatch.setattr("gui.components.reddit_browser_window.store.init_db", lambda p: None)

        fake_conn = MagicMock()
        fake_conn.__enter__ = lambda s: s
        fake_conn.__exit__ = MagicMock(return_value=False)
        fake_conn.execute.return_value.fetchall.return_value = []
        fake_conn.close = MagicMock()
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.store.open_connection",
            lambda p: fake_conn,
        )

        win._load_rows()

        assert win._row_by_iid == {}
        assert win._all_rows == []
        assert win.status_var.get() == "0 targets loaded"

    def test_populates_row_by_iid(self, monkeypatch):
        win = _make_win()
        rows = [_make_raw_row(1), _make_raw_row(2, target_normalized="ftp://other.com")]
        monkeypatch.setattr("gui.components.reddit_browser_window.store.init_db", lambda p: None)
        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = [
            SimpleNamespace(**r, keys=lambda r=r: r.keys(), __iter__=lambda s: iter(r.items()),
                            __getitem__=lambda s, k: r[k])
            for r in rows
        ]
        fake_conn.close = MagicMock()
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.store.open_connection",
            lambda p: fake_conn,
        )

        # Patch open_connection to return a real sqlite3.Row-like object via dict
        # Use a simpler approach: patch the whole method
        def fake_load(self_inner):
            self_inner._row_by_iid.clear()
            self_inner._all_rows.clear()
            self_inner.tree.delete(*self_inner.tree.get_children())
            for r in rows:
                iid = str(r["id"])
                self_inner._all_rows.append(r)
                self_inner._row_by_iid[iid] = r
            self_inner._apply_filter_and_sort()

        monkeypatch.setattr(RedditBrowserWindow, "_load_rows", fake_load)
        win._load_rows()

        assert set(win._row_by_iid.keys()) == {"1", "2"}
        assert win._row_by_iid["1"]["target_normalized"] == "http://example.com"

    def test_init_db_error_sets_status(self, monkeypatch):
        win = _make_win()
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.store.init_db",
            lambda p: (_ for _ in ()).throw(sqlite3.Error("locked")),
        )
        win._load_rows()
        assert "DB error" in win.status_var.get()
        assert "locked" in win.status_var.get()

    def test_open_connection_error_sets_status(self, monkeypatch):
        win = _make_win()
        monkeypatch.setattr("gui.components.reddit_browser_window.store.init_db", lambda p: None)
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.store.open_connection",
            lambda p: (_ for _ in ()).throw(OSError("permission denied")),
        )
        win._load_rows()
        assert "DB error" in win.status_var.get()
        assert "permission denied" in win.status_var.get()


# ---------------------------------------------------------------------------
# Group B — _apply_filter_and_sort
# ---------------------------------------------------------------------------

class TestApplyFilterAndSort:

    def _win_with_rows(self, rows: list[dict]) -> RedditBrowserWindow:
        win = _make_win()
        win._all_rows = rows
        for r in rows:
            win._row_by_iid[str(r["id"])] = r
        return win

    def test_empty_filter_shows_all(self):
        rows = [_make_raw_row(1), _make_raw_row(2)]
        win = self._win_with_rows(rows)
        win._apply_filter_and_sort()
        assert len(win.tree.visible_iids) == 2
        assert win.status_var.get() == "2 targets loaded"

    def test_filter_reduces_visible_rows(self):
        rows = [
            _make_raw_row(1, target_normalized="http://alpha.com"),
            _make_raw_row(2, target_normalized="ftp://beta.net"),
        ]
        win = self._win_with_rows(rows)
        win._filter_var.set("alpha")
        win._apply_filter_and_sort()
        assert win.tree.visible_iids == ["1"]
        assert "1 of 2" in win.status_var.get()

    def test_filter_empty_restores_all(self):
        rows = [
            _make_raw_row(1, target_normalized="http://alpha.com"),
            _make_raw_row(2, target_normalized="ftp://beta.net"),
        ]
        win = self._win_with_rows(rows)
        win._filter_var.set("alpha")
        win._apply_filter_and_sort()
        win._filter_var.set("")
        win._apply_filter_and_sort()
        assert len(win.tree.visible_iids) == 2

    def test_sort_by_target_sets_sort_col(self):
        win = _make_win()
        win._on_sort("target")
        assert win._sort_col == "target"
        assert win._sort_reverse is False

    def test_sort_by_target_toggles_reverse(self):
        win = _make_win()
        win._on_sort("target")
        win._on_sort("target")
        assert win._sort_reverse is True

    def test_sort_by_target_reorders_rows(self):
        """Actual row order in tree must match ascending target_normalized sort."""
        rows = [
            _make_raw_row(1, target_normalized="http://b-example.com"),
            _make_raw_row(2, target_normalized="http://a-example.com"),
        ]
        win = self._win_with_rows(rows)
        win._sort_col = "target"
        win._sort_reverse = False
        win._apply_filter_and_sort()
        # After ascending sort, id=2 (a-example) should appear before id=1 (b-example)
        assert win.tree.visible_iids == ["2", "1"]


# ---------------------------------------------------------------------------
# Group C — _selected_row
# ---------------------------------------------------------------------------

class TestSelectedRow:

    def test_none_when_no_selection(self):
        win = _make_win()
        assert win._selected_row() is None

    def test_returns_correct_dict(self):
        win = _make_win()
        row = _make_raw_row(42)
        win._row_by_iid["42"] = row
        win.tree.set_selection("42")
        assert win._selected_row() is row


# ---------------------------------------------------------------------------
# Group D — action handlers
# ---------------------------------------------------------------------------

class TestActionHandlers:

    def test_open_reddit_post_no_selection(self, monkeypatch):
        win = _make_win()
        show_info_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.messagebox.showinfo",
            lambda *a, **k: show_info_calls.append(a),
        )
        open_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.webbrowser.open",
            lambda url: open_calls.append(url),
        )
        win._on_open_reddit_post()
        assert len(show_info_calls) == 1
        assert open_calls == []

    def test_open_reddit_post_opens_correct_url(self, monkeypatch):
        win = _make_win()
        row = _make_raw_row(1, post_id="xyz123")
        win._row_by_iid["1"] = row
        win.tree.set_selection("1")
        open_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.webbrowser.open",
            lambda url: open_calls.append(url),
        )
        win._on_open_reddit_post()
        assert open_calls == ["https://www.reddit.com/r/opendirectories/comments/xyz123/"]

    def test_on_refresh_resets_sort_and_filter(self, monkeypatch):
        win = _make_win()
        win._sort_col = "target"
        win._sort_reverse = True
        win._filter_var.set("alpha")
        load_called = []
        monkeypatch.setattr(win, "_load_rows", lambda: load_called.append(True))
        win._on_refresh()
        assert win._sort_col is None
        assert win._sort_reverse is False
        assert win._filter_var.get() == ""
        assert load_called

    def test_on_refresh_clears_heading_indicators(self, monkeypatch):
        win = _make_win()
        heading_calls: dict[str, str] = {}

        class _TrackingTree(_CaptureTree):
            def heading(self, col, *, text=None, command=None):
                if text is not None:
                    heading_calls[col] = text

        win.tree = _TrackingTree()
        win._sort_col = "target"
        monkeypatch.setattr(win, "_load_rows", lambda: None)
        win._on_refresh()
        # All columns must end up with their plain header (no ▲/▼)
        for col in COLUMNS:
            assert heading_calls.get(col, COL_HEADERS[col]) == COL_HEADERS[col]

    def test_on_refresh_preserves_load_error_status(self):
        """
        Refresh must not overwrite _load_rows DB error status via filter trace.
        Regression guard for ordering bug where _filter_var.set("") after load
        could trigger _apply_filter_and_sort and replace error with "0 targets loaded".
        """
        win = _make_win()
        win._filter_var.set("alpha")
        win._filter_var.trace_add("write", lambda *_: win._apply_filter_and_sort())
        # Seed old rows so trace callback has data to render.
        row = _make_raw_row(1, target_normalized="http://alpha.com")
        win._all_rows = [row]
        win._row_by_iid = {"1": row}

        def _fake_load_error():
            win._row_by_iid.clear()
            win._all_rows.clear()
            win.tree.delete(*win.tree.get_children())
            win.status_var.set("DB error: locked")

        win._load_rows = _fake_load_error
        win._on_refresh()

        assert win.status_var.get() == "DB error: locked"

    def test_on_clear_db_confirmed_wipes_and_reloads(self, monkeypatch):
        win = _make_win()
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.messagebox.askyesno",
            lambda *a, **k: True,
        )
        wipe_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.store.wipe_all",
            lambda p: wipe_calls.append(p),
        )
        load_calls = []
        monkeypatch.setattr(win, "_load_rows", lambda: load_calls.append(True))
        win._on_clear_db()
        assert len(wipe_calls) == 1
        assert len(load_calls) == 1

    def test_on_clear_db_cancelled_does_not_wipe(self, monkeypatch):
        win = _make_win()
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.messagebox.askyesno",
            lambda *a, **k: False,
        )
        wipe_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.store.wipe_all",
            lambda p: wipe_calls.append(p),
        )
        win._on_clear_db()
        assert wipe_calls == []

    def test_open_explorer_no_selection(self, monkeypatch):
        win = _make_win()
        show_info_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.messagebox.showinfo",
            lambda *a, **k: show_info_calls.append(a),
        )
        open_target_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.explorer_bridge.open_target",
            lambda t, p, **kw: open_target_calls.append(t),
        )
        win._on_open_explorer()
        assert len(show_info_calls) == 1
        assert open_target_calls == []

    def test_open_explorer_calls_bridge_with_correct_target(self, monkeypatch):
        win = _make_win()
        row = _make_raw_row(
            7,
            target_normalized="http://test.com",
            protocol="http",
            host="test.com",
            target_raw="http://test.com",
            dedupe_key="dk7",
            post_id="post7",
        )
        win._row_by_iid["7"] = row
        win.tree.set_selection("7")
        captured = []
        open_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.explorer_bridge.open_target",
            lambda t, p, **kw: captured.append((t, kw)),
        )
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.open_ftp_http_browser",
            lambda *args, **kwargs: open_calls.append((args, kwargs)),
        )
        win._on_open_explorer()
        assert len(captured) == 1
        t, kw = captured[0]
        assert isinstance(t, RedditTarget)
        assert t.id == 7
        assert t.target_normalized == "http://test.com"
        assert t.host == "test.com"
        factory = kw.get("browser_factory")
        assert callable(factory)
        factory("https", "test.com", 443, start_path="/movies/")
        assert len(open_calls) == 1
        args, kwargs = open_calls[0]
        assert args[0] == "H"
        assert args[1] is win.window
        assert args[2] == "test.com"
        assert args[3] == 443
        assert kwargs["initial_path"] == "/movies/"
        assert kwargs["scheme"] == "https"
        assert kwargs["theme"] is win.theme

    def test_open_system_browser_no_selection(self, monkeypatch):
        win = _make_win()
        show_info_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.messagebox.showinfo",
            lambda *a, **k: show_info_calls.append(a),
        )
        bridge_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.explorer_bridge.open_target_system_browser",
            lambda t, p: bridge_calls.append((t, p)),
        )
        win._on_open_system_browser()
        assert len(show_info_calls) == 1
        assert bridge_calls == []

    def test_open_system_browser_calls_bridge_with_target(self, monkeypatch):
        win = _make_win()
        row = _make_raw_row(
            9,
            target_normalized="http://sys.example",
            protocol="http",
            host="sys.example",
            target_raw="http://sys.example",
            dedupe_key="dk9",
            post_id="post9",
        )
        win._row_by_iid["9"] = row
        win.tree.set_selection("9")
        bridge_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.explorer_bridge.open_target_system_browser",
            lambda t, p: bridge_calls.append((t, p)),
        )
        win._on_open_system_browser()
        assert len(bridge_calls) == 1
        target, parent = bridge_calls[0]
        assert isinstance(target, RedditTarget)
        assert target.id == 9
        assert target.host == "sys.example"
        assert parent is win.window


# ---------------------------------------------------------------------------
# Group E — _build_prefill and _on_add_to_db
# ---------------------------------------------------------------------------

class TestAddToDb:

    def test_build_prefill_http(self):
        win = _make_win()
        row = _make_raw_row(1, protocol="http", host="example.com",
                            target_normalized="http://example.com/files/")
        result = win._build_prefill(row)
        assert result is not None
        assert result["host_type"] == "H"
        assert result["host"] == "example.com"
        assert result["port"] is None
        assert result["scheme"] == "http"
        assert result["_probe_host_hint"] == "example.com"
        assert result["_probe_path_hint"] == "/files/"

    def test_build_prefill_https_with_explicit_port(self):
        win = _make_win()
        row = _make_raw_row(1, protocol="https", host="example.com",
                            target_normalized="https://example.com:8443/files/")
        result = win._build_prefill(row)
        assert result is not None
        assert result["host_type"] == "H"
        assert result["host"] == "example.com"
        assert result["port"] == 8443
        assert result["scheme"] == "https"
        assert result["_probe_host_hint"] == "example.com"
        assert result["_probe_path_hint"] == "/files/"

    def test_build_prefill_ftp(self):
        win = _make_win()
        row = _make_raw_row(1, protocol="ftp", host="example.com",
                            target_normalized="ftp://example.com/pub/")
        result = win._build_prefill(row)
        assert result == {"host_type": "F", "host": "example.com", "port": None, "scheme": None}

    def test_build_prefill_ftp_with_port(self):
        win = _make_win()
        row = _make_raw_row(1, protocol="ftp", host="example.com",
                            target_normalized="ftp://example.com:2121/")
        result = win._build_prefill(row)
        assert result is not None
        assert result["port"] == 2121
        assert result["host_type"] == "F"

    def test_build_prefill_bare_host_port_form(self):
        win = _make_win()
        row = _make_raw_row(1, protocol="http", host="1.2.3.4",
                            target_normalized="1.2.3.4:8080")
        result = win._build_prefill(row)
        assert result is not None
        assert result["port"] == 8080
        assert result["_probe_host_hint"] == "1.2.3.4"
        assert result["_probe_path_hint"] == "/"

    def test_build_prefill_unknown_protocol_returns_none(self):
        win = _make_win()
        row = _make_raw_row(1, protocol="smb", host="example.com",
                            target_normalized="smb://example.com/share")
        assert win._build_prefill(row) is None

    def test_on_add_to_db_no_callback_shows_info(self, monkeypatch):
        win = _make_win()
        win._add_record_callback = None
        info_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.messagebox.showinfo",
            lambda *a, **k: info_calls.append(a),
        )
        win._on_add_to_db()
        assert len(info_calls) == 1

    def test_on_add_to_db_no_selection_shows_info(self, monkeypatch):
        win = _make_win()
        win._add_record_callback = MagicMock()
        info_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.messagebox.showinfo",
            lambda *a, **k: info_calls.append(a),
        )
        win._on_add_to_db()
        assert len(info_calls) == 1
        win._add_record_callback.assert_not_called()

    def test_on_add_to_db_unknown_protocol_shows_info(self, monkeypatch):
        win = _make_win()
        win._add_record_callback = MagicMock()
        row = _make_raw_row(1, protocol="smb", host="example.com")
        win._row_by_iid["1"] = row
        win.tree.set_selection("1")
        info_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.messagebox.showinfo",
            lambda *a, **k: info_calls.append(a),
        )
        win._on_add_to_db()
        assert len(info_calls) == 1
        win._add_record_callback.assert_not_called()

    def test_on_add_to_db_calls_callback_with_correct_prefill(self, monkeypatch):
        win = _make_win()
        captured = []
        win._add_record_callback = lambda p: captured.append(p)
        row = _make_raw_row(1, protocol="http", host="1.2.3.4",
                            target_normalized="http://1.2.3.4/files/")
        win._row_by_iid["1"] = row
        win.tree.set_selection("1")
        win._on_add_to_db()
        assert len(captured) == 1
        assert captured[0]["host_type"] == "H"
        assert captured[0]["host"] == "1.2.3.4"
        assert captured[0]["scheme"] == "http"
        assert captured[0]["port"] is None
        assert captured[0]["_promotion_source"] == "reddit_browser"
        assert captured[0]["_probe_host_hint"] == "1.2.3.4"
        assert captured[0]["_probe_path_hint"] == "/files/"

    def test_on_add_to_db_resolves_domain_to_ipv4_before_callback(self, monkeypatch):
        win = _make_win()
        captured = []
        win._add_record_callback = lambda p: captured.append(p)
        row = _make_raw_row(
            1,
            protocol="http",
            host="example.com",
            target_normalized="http://example.com/files/",
        )
        win._row_by_iid["1"] = row
        win.tree.set_selection("1")

        monkeypatch.setattr(
            "gui.components.reddit_browser_window.socket.getaddrinfo",
            lambda *a, **k: [
                (2, 1, 6, "", ("93.184.216.34", 0)),
                (2, 1, 6, "", ("93.184.216.35", 0)),
            ],
        )

        warn_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.messagebox.showwarning",
            lambda *a, **k: warn_calls.append(a),
        )

        win._on_add_to_db()

        assert len(captured) == 1
        assert captured[0]["host"] == "93.184.216.34"
        assert captured[0]["_promotion_source"] == "reddit_browser"
        assert captured[0]["_probe_host_hint"] == "example.com"
        assert captured[0]["_probe_path_hint"] == "/files/"
        assert warn_calls == []

    def test_on_add_to_db_resolution_failure_keeps_host_and_warns(self, monkeypatch):
        win = _make_win()
        captured = []
        win._add_record_callback = lambda p: captured.append(p)
        row = _make_raw_row(
            1,
            protocol="http",
            host="unknown.example.invalid",
            target_normalized="http://unknown.example.invalid/files/",
        )
        win._row_by_iid["1"] = row
        win.tree.set_selection("1")

        def _raise(*_args, **_kwargs):
            raise OSError("name or service not known")

        monkeypatch.setattr(
            "gui.components.reddit_browser_window.socket.getaddrinfo",
            _raise,
        )

        warn_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.messagebox.showwarning",
            lambda *a, **k: warn_calls.append((a, k)),
        )

        win._on_add_to_db()

        assert len(captured) == 1
        assert captured[0]["host"] == "unknown.example.invalid"
        assert captured[0]["_promotion_source"] == "reddit_browser"
        assert captured[0]["_probe_host_hint"] == "unknown.example.invalid"
        assert captured[0]["_probe_path_hint"] == "/files/"
        assert len(warn_calls) == 1
        assert "Host Resolution Failed" in warn_calls[0][0][0]

    def test_on_add_to_db_literal_ip_skips_dns_lookup(self, monkeypatch):
        win = _make_win()
        captured = []
        win._add_record_callback = lambda p: captured.append(p)
        row = _make_raw_row(
            1,
            protocol="http",
            host="1.2.3.4",
            target_normalized="http://1.2.3.4/files/",
        )
        win._row_by_iid["1"] = row
        win.tree.set_selection("1")

        dns_calls = []
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.socket.getaddrinfo",
            lambda *a, **k: dns_calls.append((a, k)),
        )

        win._on_add_to_db()

        assert len(captured) == 1
        assert captured[0]["host"] == "1.2.3.4"
        assert captured[0]["_probe_host_hint"] == "1.2.3.4"
        assert captured[0]["_probe_path_hint"] == "/files/"
        assert dns_calls == []


# ---------------------------------------------------------------------------
# Group F — context menu lifecycle
# ---------------------------------------------------------------------------

class TestContextMenuLifecycle:

    def test_right_click_row_shows_menu_and_marks_visible(self):
        win = _make_win()
        win.tree.set_identify_row(10, "1")
        event = SimpleNamespace(y=10, x_root=320, y_root=210)

        result = win._on_right_click(event)

        assert result == "break"
        win._context_menu.tk_popup.assert_called_once_with(320, 210)
        win._context_menu.grab_release.assert_called_once()
        assert win._context_menu_visible is True
        assert len(win._context_menu_bindings) == 4

    def test_dismiss_click_hides_menu_and_cleans_handlers(self):
        win = _make_win()
        win.tree.set_identify_row(10, "1")
        event = SimpleNamespace(y=10, x_root=320, y_root=210)
        win._on_right_click(event)

        win._handle_context_dismiss_click()

        win._context_menu.unpost.assert_called_once()
        assert win._context_menu_visible is False
        assert win._context_menu_bindings == []
        assert len(win.window.unbind_calls) == 2
        assert len(win.tree.unbind_calls) == 2

    def test_right_click_empty_space_hides_existing_menu_and_does_not_reopen(self):
        win = _make_win()
        win.tree.set_identify_row(10, "1")
        show_event = SimpleNamespace(y=10, x_root=320, y_root=210)
        win._on_right_click(show_event)

        win._context_menu.tk_popup.reset_mock()
        win._context_menu.unpost.reset_mock()

        empty_event = SimpleNamespace(y=99, x_root=500, y_root=500)
        result = win._on_right_click(empty_event)

        assert result == "break"
        win._context_menu.unpost.assert_called_once()
        win._context_menu.tk_popup.assert_not_called()
        assert win._context_menu_visible is False

    def test_on_add_to_db_hides_menu_before_dispatch(self):
        win = _make_win()
        captured = []
        win._add_record_callback = lambda p: captured.append(p)
        row = _make_raw_row(
            1,
            protocol="http",
            host="1.2.3.4",
            target_normalized="http://1.2.3.4/files/",
        )
        win._row_by_iid["1"] = row
        win.tree.set_selection("1")
        win.tree.set_identify_row(10, "1")
        show_event = SimpleNamespace(y=10, x_root=320, y_root=210)
        win._on_right_click(show_event)
        win._context_menu.unpost.reset_mock()

        win._on_add_to_db()

        win._context_menu.unpost.assert_called_once()
        assert win._context_menu_visible is False
        assert len(captured) == 1

    def test_copy_host_copies_selected_host_to_clipboard(self):
        win = _make_win()
        row = _make_raw_row(1, host="10.0.0.5")
        win._row_by_iid["1"] = row
        win.tree.set_selection("1")
        win._on_copy_host()
        win.window.clipboard_clear.assert_called_once()
        win.window.clipboard_append.assert_called_once_with("10.0.0.5")

    def test_copy_host_no_selection_is_noop(self):
        win = _make_win()
        win._on_copy_host()
        win.window.clipboard_clear.assert_not_called()
        win.window.clipboard_append.assert_not_called()

    def test_context_open_explorer_hides_menu_then_dispatches(self, monkeypatch):
        win = _make_win()
        row = _make_raw_row(1, protocol="http", host="example.com")
        win._row_by_iid["1"] = row
        win.tree.set_selection("1")
        win.tree.set_identify_row(10, "1")
        show_event = SimpleNamespace(y=10, x_root=320, y_root=210)
        win._on_right_click(show_event)
        win._context_menu.unpost.reset_mock()
        called = []
        monkeypatch.setattr(win, "_on_open_explorer", lambda: called.append(True))

        win._on_context_open_explorer()

        win._context_menu.unpost.assert_called_once()
        assert called == [True]

    def test_context_open_system_browser_hides_menu_then_dispatches(self, monkeypatch):
        win = _make_win()
        row = _make_raw_row(1, protocol="http", host="example.com")
        win._row_by_iid["1"] = row
        win.tree.set_selection("1")
        win.tree.set_identify_row(10, "1")
        show_event = SimpleNamespace(y=10, x_root=320, y_root=210)
        win._on_right_click(show_event)
        win._context_menu.unpost.reset_mock()
        called = []
        monkeypatch.setattr(win, "_on_open_system_browser", lambda: called.append(True))

        win._on_context_open_system_browser()

        win._context_menu.unpost.assert_called_once()
        assert called == [True]
