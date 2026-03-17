"""
Tests for DataImportEngine timestamp normalization (Card 2.5).

Covers:
- Incoming records with T-format timestamps are stored in canonical form.
- Incoming records with UTC-offset timestamps are converted to UTC.
- current_time written by the engine (created_at / updated_at) contains no T.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.utils.data_import_engine import DataImportEngine

_CANONICAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(tmp_path) -> DataImportEngine:
    db = tmp_path / "import_test.db"
    engine = DataImportEngine(str(db))
    engine._ensure_database_schema("servers")
    return engine


def _fetch_server(engine: DataImportEngine, ip: str) -> dict:
    conn = sqlite3.connect(engine.db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM servers WHERE ip_address = ?", (ip,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_import_normalizes_T_format_last_seen(tmp_path):
    """Incoming last_seen with T-separator is stored in canonical space format."""
    engine = _make_engine(tmp_path)
    records = [
        {
            "ip_address": "1.2.3.4",
            "country": "US",
            "auth_method": "anonymous",
            "last_seen": "2025-01-21T14:20:05",
        }
    ]
    engine._import_to_database(records, "servers", "merge", None)

    row = _fetch_server(engine, "1.2.3.4")
    assert row, "Row should have been inserted"
    assert "T" not in (row["last_seen"] or ""), (
        f"last_seen has T after import: {row['last_seen']!r}"
    )
    assert row["last_seen"] == "2025-01-21 14:20:05"


def test_import_normalizes_microsecond_timestamps(tmp_path):
    """Incoming timestamp with microseconds is truncated to seconds."""
    engine = _make_engine(tmp_path)
    records = [
        {
            "ip_address": "2.3.4.5",
            "country": "GB",
            "auth_method": "guest",
            "last_seen": "2025-06-01T08:00:05.123456",
        }
    ]
    engine._import_to_database(records, "servers", "merge", None)

    row = _fetch_server(engine, "2.3.4.5")
    assert row
    assert row["last_seen"] == "2025-06-01 08:00:05"


def test_import_normalizes_offset_timestamps(tmp_path):
    """Incoming timestamp with UTC offset is converted to UTC canonical form."""
    engine = _make_engine(tmp_path)
    records = [
        {
            "ip_address": "3.4.5.6",
            "country": "DE",
            "auth_method": "anonymous",
            # -05:00 → add 5h → 14:00 UTC
            "last_seen": "2025-01-21T09:00:00-05:00",
        }
    ]
    engine._import_to_database(records, "servers", "merge", None)

    row = _fetch_server(engine, "3.4.5.6")
    assert row
    assert row["last_seen"] == "2025-01-21 14:00:00", (
        f"Expected UTC conversion, got: {row['last_seen']!r}"
    )


def test_import_current_time_no_T(tmp_path):
    """created_at and updated_at written by the engine contain no T."""
    engine = _make_engine(tmp_path)
    records = [
        {
            "ip_address": "4.5.6.7",
            "country": "FR",
            "auth_method": "anonymous",
        }
    ]
    engine._import_to_database(records, "servers", "merge", None)

    row = _fetch_server(engine, "4.5.6.7")
    assert row

    created = row.get("created_at", "") or ""
    updated = row.get("updated_at", "") or ""

    assert "T" not in created, f"created_at has T: {created!r}"
    assert "T" not in updated, f"updated_at has T: {updated!r}"

    if created:
        assert _CANONICAL_RE.match(created), (
            f"created_at not canonical: {created!r}"
        )
    if updated:
        assert _CANONICAL_RE.match(updated), (
            f"updated_at not canonical: {updated!r}"
        )


def test_import_merge_update_no_T(tmp_path):
    """updated_at written during a merge-update also contains no T."""
    engine = _make_engine(tmp_path)
    records = [
        {"ip_address": "5.6.7.8", "country": "JP", "auth_method": "anonymous"},
    ]
    engine._import_to_database(records, "servers", "merge", None)

    # Second import triggers UPDATE path
    records2 = [
        {
            "ip_address": "5.6.7.8",
            "country": "JP",
            "auth_method": "anonymous",
            "last_seen": "2025-03-01T12:00:00",
        }
    ]
    engine._import_to_database(records2, "servers", "merge", None)

    row = _fetch_server(engine, "5.6.7.8")
    assert row
    updated = row.get("updated_at", "") or ""
    assert "T" not in updated, f"updated_at has T after update: {updated!r}"
