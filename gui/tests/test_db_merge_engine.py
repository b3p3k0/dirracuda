"""
Unit tests for gui.utils.db_merge_engine pure helper functions.

These are headless tests (no Tkinter, no db_tools_engine import) that validate
the extracted helpers in isolation. Merge/import behavior is covered by the
integration tests in test_db_tools_engine.py.
"""

import sqlite3
from datetime import datetime

import pytest

from gui.utils.db_merge_engine import (
    parse_timestamp,
    table_columns,
    table_exists,
    table_has_required_columns,
)

_MIN_DATE = datetime(1970, 1, 1)


# ---------------------------------------------------------------------------
# parse_timestamp
# ---------------------------------------------------------------------------

def test_parse_timestamp_space_format():
    result = parse_timestamp("2025-01-21 14:20:05", _MIN_DATE)
    assert result == datetime(2025, 1, 21, 14, 20, 5)


def test_parse_timestamp_T_format():
    """T-separator is normalized to space-separated naive datetime."""
    result = parse_timestamp("2025-01-21T14:20:05", _MIN_DATE)
    assert result == datetime(2025, 1, 21, 14, 20, 5)


def test_parse_timestamp_offset_strips_and_parses_naive():
    """Offset is stripped; the naive local time is preserved (current behavior)."""
    result = parse_timestamp("2025-01-21T14:20:05+05:30", _MIN_DATE)
    assert result == datetime(2025, 1, 21, 14, 20, 5)


def test_parse_timestamp_null_returns_min_date():
    assert parse_timestamp(None, _MIN_DATE) is _MIN_DATE


def test_parse_timestamp_empty_string_returns_min_date():
    assert parse_timestamp("", _MIN_DATE) is _MIN_DATE


def test_parse_timestamp_invalid_returns_min_date():
    assert parse_timestamp("not-a-date", _MIN_DATE) is _MIN_DATE


# ---------------------------------------------------------------------------
# table_exists
# ---------------------------------------------------------------------------

def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_table_exists_true():
    conn = _make_conn()
    conn.execute("CREATE TABLE foo (id INTEGER PRIMARY KEY)")
    assert table_exists(conn, "foo") is True
    conn.close()


def test_table_exists_false():
    conn = _make_conn()
    assert table_exists(conn, "nonexistent") is False
    conn.close()


# ---------------------------------------------------------------------------
# table_columns
# ---------------------------------------------------------------------------

def test_table_columns_returns_set():
    conn = _make_conn()
    conn.execute("CREATE TABLE bar (id INTEGER PRIMARY KEY, name TEXT, value REAL)")
    cols = table_columns(conn, "bar")
    assert cols == {"id", "name", "value"}
    conn.close()


def test_table_columns_missing_table_returns_empty():
    conn = _make_conn()
    assert table_columns(conn, "missing") == set()
    conn.close()


# ---------------------------------------------------------------------------
# table_has_required_columns
# ---------------------------------------------------------------------------

def test_table_has_required_columns_pass():
    conn = _make_conn()
    conn.execute("CREATE TABLE baz (id INTEGER, ip TEXT, last_seen TEXT)")
    assert table_has_required_columns(conn, "baz", {"id", "ip"}) is True
    conn.close()


def test_table_has_required_columns_fail_missing_col():
    conn = _make_conn()
    conn.execute("CREATE TABLE baz (id INTEGER, ip TEXT)")
    assert table_has_required_columns(conn, "baz", {"id", "ip", "last_seen"}) is False
    conn.close()


def test_table_has_required_columns_fail_missing_table():
    conn = _make_conn()
    assert table_has_required_columns(conn, "absent", {"id"}) is False
    conn.close()
