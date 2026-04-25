"""Tests for experimental/keymaster/store.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experimental.keymaster import store as km_store
from experimental.keymaster.models import DuplicateKeyError, PROVIDER_SHODAN


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = tmp_path / "keymaster_test.db"
    km_store.init_db(db_path)
    yield db_path


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_init_db_creates_schema(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        # _check_schema is called inside open_connection; reaching here means pass
        assert conn is not None


# ---------------------------------------------------------------------------
# create_key
# ---------------------------------------------------------------------------

def test_create_key_returns_id(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        key_id = km_store.create_key(conn, PROVIDER_SHODAN, "My Key", "abc123", "")
        conn.commit()
    assert isinstance(key_id, int) and key_id > 0


def test_duplicate_key_blocked(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        km_store.create_key(conn, PROVIDER_SHODAN, "Key A", "abc123", "")
        conn.commit()
    with km_store.open_connection(tmp_db) as conn:
        with pytest.raises(DuplicateKeyError):
            km_store.create_key(conn, PROVIDER_SHODAN, "Key B", "abc123", "")


def test_unknown_provider_rejected(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        with pytest.raises(ValueError, match="unsupported provider"):
            km_store.create_key(conn, "UNKNOWN", "Label", "somekey", "")


def test_create_key_empty_label_rejected(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        with pytest.raises(ValueError, match="label is required"):
            km_store.create_key(conn, PROVIDER_SHODAN, "  ", "somekey", "")


def test_create_key_empty_api_key_rejected(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        with pytest.raises(ValueError, match="api_key is required"):
            km_store.create_key(conn, PROVIDER_SHODAN, "Label", "", "")


# ---------------------------------------------------------------------------
# update_key
# ---------------------------------------------------------------------------

def test_update_key_changes_fields(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        key_id = km_store.create_key(conn, PROVIDER_SHODAN, "Old Label", "key001", "old notes")
        conn.commit()
    with km_store.open_connection(tmp_db) as conn:
        km_store.update_key(conn, key_id, "New Label", "key001", "new notes")
        conn.commit()
    with km_store.open_connection(tmp_db) as conn:
        row = km_store.get_key(conn, key_id)
    assert row["label"] == "New Label"
    assert row["notes"] == "new notes"


def test_update_key_duplicate_blocked(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        km_store.create_key(conn, PROVIDER_SHODAN, "Key A", "key001", "")
        key_b_id = km_store.create_key(conn, PROVIDER_SHODAN, "Key B", "key002", "")
        conn.commit()
    with km_store.open_connection(tmp_db) as conn:
        with pytest.raises(DuplicateKeyError):
            km_store.update_key(conn, key_b_id, "Key B", "key001", "")


def test_update_key_empty_label_rejected(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        key_id = km_store.create_key(conn, PROVIDER_SHODAN, "Label", "key001", "")
        conn.commit()
    with km_store.open_connection(tmp_db) as conn:
        with pytest.raises(ValueError, match="label is required"):
            km_store.update_key(conn, key_id, "\t", "key001", "")


def test_update_key_empty_api_key_rejected(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        key_id = km_store.create_key(conn, PROVIDER_SHODAN, "Label", "key001", "")
        conn.commit()
    with km_store.open_connection(tmp_db) as conn:
        with pytest.raises(ValueError, match="api_key is required"):
            km_store.update_key(conn, key_id, "Label", "   ", "")


# ---------------------------------------------------------------------------
# delete_key
# ---------------------------------------------------------------------------

def test_delete_key_removes_row(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        key_id = km_store.create_key(conn, PROVIDER_SHODAN, "Label", "key001", "")
        conn.commit()
    with km_store.open_connection(tmp_db) as conn:
        result = km_store.delete_key(conn, key_id)
        conn.commit()
    assert result is True
    with km_store.open_connection(tmp_db) as conn:
        assert km_store.get_key(conn, key_id) is None


def test_delete_nonexistent_returns_false(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        result = km_store.delete_key(conn, 9999)
    assert result is False


# ---------------------------------------------------------------------------
# list_keys / search
# ---------------------------------------------------------------------------

def test_list_keys_search_filters(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        km_store.create_key(conn, PROVIDER_SHODAN, "Primary Paid", "key001", "baseline")
        km_store.create_key(conn, PROVIDER_SHODAN, "Backup Trial", "key002", "low allotment")
        conn.commit()
    with km_store.open_connection(tmp_db) as conn:
        all_rows = km_store.list_keys(conn, PROVIDER_SHODAN)
        filtered = km_store.list_keys(conn, PROVIDER_SHODAN, search_text="primary")
    assert len(all_rows) == 2
    assert len(filtered) == 1
    assert filtered[0]["label"] == "Primary Paid"


# ---------------------------------------------------------------------------
# touch_last_used
# ---------------------------------------------------------------------------

def test_touch_last_used_sets_timestamp(tmp_db):
    with km_store.open_connection(tmp_db) as conn:
        key_id = km_store.create_key(conn, PROVIDER_SHODAN, "Label", "key001", "")
        conn.commit()
    with km_store.open_connection(tmp_db) as conn:
        row_before = km_store.get_key(conn, key_id)
    assert row_before["last_used_at"] is None

    with km_store.open_connection(tmp_db) as conn:
        km_store.touch_last_used(conn, key_id)
        conn.commit()
    with km_store.open_connection(tmp_db) as conn:
        row_after = km_store.get_key(conn, key_id)
    assert row_after["last_used_at"] is not None
