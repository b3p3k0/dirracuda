"""Provider-aware Dorkbook behavior using isolated, local sidecars."""

from __future__ import annotations

import sqlite3

import pytest

from experimental.dorkbook import store
from experimental.dorkbook.models import (
    BuiltinDork,
    DuplicateEntryError,
    ReadOnlyEntryError,
)


@pytest.fixture()
def connection(tmp_path):
    path = tmp_path / "dorkbook.db"
    store.init_db(path)
    conn = store.open_connection(path)
    try:
        yield conn
    finally:
        conn.close()


def add_web(conn, query="intitle:directory", *, topic="General"):
    return store.create_entry(
        conn, None, "Web dork", query, "Web notes",
        provider="self_hosted", topic=topic,
    )


def test_legacy_callers_keep_shodan_defaults(connection):
    entry_id = store.create_entry(connection, "HTTP", "Legacy", "legacy query", "")
    entry = store.get_entry(connection, entry_id)
    assert entry["provider"] == "shodan"
    assert entry["protocol"] == "HTTP"
    assert entry["topic"] == "General"
    assert entry in store.list_entries(connection, "HTTP")


def test_web_entry_has_no_protocol_and_survives_update(connection):
    entry_id = add_web(connection, topic="Books")
    store.update_entry(connection, entry_id, "New name", "new web query", "New notes")
    entry = store.get_entry(connection, entry_id)
    assert entry["provider"] == "self_hosted"
    assert entry["protocol"] is None
    assert entry["topic"] == "Books"
    assert entry["nickname"] == "New name"
    assert entry["query"] == "new web query"
    assert entry["notes"] == "New notes"
    store.update_entry(connection, entry_id, "New name", "new web query", "", topic="Music")
    assert store.get_entry(connection, entry_id)["topic"] == "Music"
    assert store.delete_entry(connection, entry_id)
    assert store.get_entry(connection, entry_id) is None


def test_duplicate_domains_are_provider_and_applicable_protocol(connection):
    web_id = add_web(connection, "shared text")
    http_id = store.create_entry(connection, "HTTP", "HTTP", "shared text", "")
    ftp_id = store.create_entry(connection, "FTP", "FTP", "shared text", "")
    assert len({web_id, http_id, ftp_id}) == 3
    with pytest.raises(DuplicateEntryError):
        add_web(connection, "  shared text  ")
    with pytest.raises(DuplicateEntryError):
        store.create_entry(connection, "HTTP", "Another", "shared text", "")
    # Query equality remains exact, not case-folded.
    assert add_web(connection, "Shared text") != web_id
    assert store.query_exists(connection, None, "shared text", provider="self_hosted")
    assert not store.query_exists(
        connection, None, "shared text", provider="self_hosted", exclude_entry_id=web_id,
    )


def test_web_update_cannot_duplicate_another_web_query(connection):
    first_id = add_web(connection, "first query")
    second_id = add_web(connection, "second query")
    with pytest.raises(DuplicateEntryError):
        store.update_entry(connection, second_id, "Collision", " first query ", "")
    assert store.get_entry(connection, first_id)["query"] == "first query"
    assert store.get_entry(connection, second_id)["query"] == "second query"


def test_database_itself_blocks_duplicates_with_null_protocol(connection):
    entry_id = add_web(connection)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO dorkbook_entries
                (provider, protocol, topic, nickname, query, query_normalized,
                 notes, row_kind, builtin_key, created_at, updated_at)
            SELECT provider, protocol, topic, nickname, query, query_normalized,
                   notes, row_kind, NULL, created_at, updated_at
              FROM dorkbook_entries WHERE entry_id = ?
            """,
            (entry_id,),
        )


def test_library_filters_combine_provider_topic_and_search(connection):
    web_id = add_web(connection, "web books marker", topic="Test books")
    http_id = store.create_entry(
        connection, "HTTP", "Books marker", "http books", "", topic="Test books",
    )
    music_id = add_web(connection, "music marker", topic="Music")
    assert {row["entry_id"] for row in store.list_entries(
        connection, provider=None, topic="Test books", search_text="marker",
    )} == {web_id, http_id}
    assert {row["entry_id"] for row in store.list_entries(
        connection, provider="self_hosted", topic="Test books",
    )} == {web_id}
    assert music_id not in {row["entry_id"] for row in store.list_entries(connection)}
    assert http_id in {row["entry_id"] for row in store.list_entries(connection)}


def test_web_builtin_is_read_only_and_refresh_skips_custom_collision(connection):
    builtin = BuiltinDork(
        "web_test", None, "Web default", "web builtin query",
        provider="self_hosted", topic="Books",
    )
    assert store.upsert_builtin_pack(connection, [builtin]) == 1
    row = next(row for row in store.list_entries(connection, provider="self_hosted")
               if row["builtin_key"] == "web_test")
    with pytest.raises(ReadOnlyEntryError):
        store.update_entry(connection, row["entry_id"], "Changed", "changed", "")
    with pytest.raises(ReadOnlyEntryError):
        store.delete_entry(connection, row["entry_id"])

    custom_id = add_web(connection, "custom wins")
    conflicting = BuiltinDork(
        "web_test", None, "Changed default", "custom wins",
        provider="self_hosted", topic="Music",
    )
    assert store.upsert_builtin_pack(connection, [conflicting]) == 0
    assert store.get_entry(connection, row["entry_id"]) == row
    assert store.get_entry(connection, custom_id)["row_kind"] == "custom"
    conflicting_insert = BuiltinDork(
        "web_second", None, "Other default", "custom wins", provider="self_hosted",
    )
    assert store.upsert_builtin_pack(connection, [conflicting_insert]) == 0


@pytest.mark.parametrize("provider,protocol", [
    ("shodan", None), ("self_hosted", "HTTP"), ("unknown", None),
])
def test_invalid_provider_protocol_combinations_are_rejected(connection, provider, protocol):
    with pytest.raises(ValueError):
        store.create_entry(connection, protocol, "Invalid", "query", "", provider=provider)
