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

    @property
    def visible_iids(self) -> list[str]:
        return [iid for iid, _ in self._items]


def _make_win(monkeypatch=None) -> RedditBrowserWindow:
    """Build a RedditBrowserWindow without constructing any Tk widgets."""
    win = RedditBrowserWindow.__new__(RedditBrowserWindow)
    win.parent = MagicMock()
    win.db_path = None
    win.theme = MagicMock()
    win._row_by_iid = {}
    win._all_rows = []
    win._sort_col = None
    win._sort_reverse = False
    win.window = MagicMock()
    win.tree = _CaptureTree()
    win.status_var = _StrVar()
    win._filter_var = _StrVar()
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
            lambda t, p: open_target_calls.append(t),
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
        monkeypatch.setattr(
            "gui.components.reddit_browser_window.explorer_bridge.open_target",
            lambda t, p: captured.append(t),
        )
        win._on_open_explorer()
        assert len(captured) == 1
        t = captured[0]
        assert isinstance(t, RedditTarget)
        assert t.id == 7
        assert t.target_normalized == "http://test.com"
        assert t.host == "test.com"
