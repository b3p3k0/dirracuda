"""
Unit tests for experimental.dorkbook.store.

All tests use tmp_path injection and do not touch ~/.dirracuda.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from experimental.dorkbook.models import (
    PROTOCOL_FTP,
    PROTOCOL_HTTP,
    PROTOCOL_SMB,
    ROW_KIND_BUILTIN,
    BuiltinDork,
    DuplicateEntryError,
    ReadOnlyEntryError,
)
from experimental.dorkbook.store import (
    create_entry,
    delete_entry,
    get_db_path,
    init_db,
    list_entries,
    open_connection,
    upsert_builtin_pack,
    update_entry,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "dorkbook.db"
    init_db(path)
    return path


def test_get_db_path_returns_override(tmp_path: Path) -> None:
    override = tmp_path / "custom.db"
    assert get_db_path(override) == override


def test_init_db_creates_schema_and_seeds_builtins(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        smb = list_entries(conn, PROTOCOL_SMB)
        ftp = list_entries(conn, PROTOCOL_FTP)
        http = list_entries(conn, PROTOCOL_HTTP)

    assert len(smb) >= 1
    assert len(ftp) >= 1
    assert len(http) >= 1
    assert smb[0]["row_kind"] == ROW_KIND_BUILTIN
    assert ftp[0]["row_kind"] == ROW_KIND_BUILTIN
    assert http[0]["row_kind"] == ROW_KIND_BUILTIN


def test_init_db_idempotent_keeps_single_builtin_per_protocol(tmp_path: Path) -> None:
    db = tmp_path / "idempotent.db"
    init_db(db)
    init_db(db)
    with open_connection(db) as conn:
        rows = conn.execute(
            """
            SELECT protocol, COUNT(*) AS c
              FROM dorkbook_entries
             WHERE row_kind='builtin'
             GROUP BY protocol
            """
        ).fetchall()
    counts = {row["protocol"]: row["c"] for row in rows}
    assert counts == {"SMB": 1, "FTP": 1, "HTTP": 1}


def test_create_entry_blocks_exact_trimmed_duplicate_within_protocol(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        create_entry(conn, PROTOCOL_SMB, "A", "foo", "")
        conn.commit()
        with pytest.raises(DuplicateEntryError):
            create_entry(conn, PROTOCOL_SMB, "B", "  foo  ", "")


def test_create_entry_allows_same_query_across_protocols(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        smb_id = create_entry(conn, PROTOCOL_SMB, "A", "same query", "")
        ftp_id = create_entry(conn, PROTOCOL_FTP, "B", "same query", "")
        conn.commit()
    assert smb_id > 0
    assert ftp_id > 0


def test_update_entry_rejects_builtin_row(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        builtin = list_entries(conn, PROTOCOL_HTTP)[0]
        with pytest.raises(ReadOnlyEntryError):
            update_entry(conn, builtin["entry_id"], "X", "new query", "")


def test_delete_entry_rejects_builtin_row(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        builtin = list_entries(conn, PROTOCOL_FTP)[0]
        with pytest.raises(ReadOnlyEntryError):
            delete_entry(conn, builtin["entry_id"])


def test_update_entry_updates_custom_row(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        entry_id = create_entry(conn, PROTOCOL_HTTP, "Old", "http query", "n1")
        conn.commit()
        update_entry(conn, entry_id, "New", "http query v2", "n2")
        conn.commit()
        rows = list_entries(conn, PROTOCOL_HTTP)
        custom = [r for r in rows if r["entry_id"] == entry_id][0]
    assert custom["nickname"] == "New"
    assert custom["query"] == "http query v2"
    assert custom["notes"] == "n2"


def test_list_entries_orders_builtin_then_custom_alpha(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        create_entry(conn, PROTOCOL_SMB, "Zulu", "q_z", "")
        create_entry(conn, PROTOCOL_SMB, "Alpha", "q_a", "")
        conn.commit()
        rows = list_entries(conn, PROTOCOL_SMB)

    assert rows[0]["row_kind"] == ROW_KIND_BUILTIN
    assert rows[1]["nickname"] == "Alpha"
    assert rows[2]["nickname"] == "Zulu"


def test_upsert_builtin_pack_refreshes_entry_by_builtin_key(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        upsert_builtin_pack(
            conn,
            builtins=[
                BuiltinDork(
                    builtin_key="builtin_http_default",
                    protocol=PROTOCOL_HTTP,
                    nickname="Default HTTP Dork (Updated)",
                    query='http.title:"Index of /" has_screenshot:true',
                    notes="updated",
                ),
            ],
        )
        conn.commit()
        rows = list_entries(conn, PROTOCOL_HTTP)
        builtin = [r for r in rows if r["row_kind"] == ROW_KIND_BUILTIN][0]

    assert builtin["nickname"] == "Default HTTP Dork (Updated)"
    assert builtin["query"] == 'http.title:"Index of /" has_screenshot:true'
    assert builtin["notes"] == "updated"


def test_upsert_builtin_pack_skips_conflicting_builtin_update(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        before_builtin = [r for r in list_entries(conn, PROTOCOL_HTTP) if r["row_kind"] == ROW_KIND_BUILTIN][0]
        create_entry(conn, PROTOCOL_HTTP, "Mine", "future query", "custom")
        conn.commit()

        touched = upsert_builtin_pack(
            conn,
            builtins=[
                BuiltinDork(
                    builtin_key="builtin_http_default",
                    protocol=PROTOCOL_HTTP,
                    nickname="Default HTTP Dork (Updated)",
                    query="future query",
                    notes="would collide with custom",
                ),
            ],
        )
        conn.commit()
        after_builtin = [r for r in list_entries(conn, PROTOCOL_HTTP) if r["row_kind"] == ROW_KIND_BUILTIN][0]
        custom_rows = [
            r for r in list_entries(conn, PROTOCOL_HTTP) if r["row_kind"] != ROW_KIND_BUILTIN
        ]

    assert touched == 0
    assert after_builtin["query"] == before_builtin["query"]
    assert any(r["query"] == "future query" for r in custom_rows)


def test_upsert_builtin_pack_skips_conflicting_builtin_insert(db_path: Path) -> None:
    with open_connection(db_path) as conn:
        create_entry(conn, PROTOCOL_SMB, "Mine", "collision query", "custom")
        conn.commit()

        touched = upsert_builtin_pack(
            conn,
            builtins=[
                BuiltinDork(
                    builtin_key="builtin_smb_extra",
                    protocol=PROTOCOL_SMB,
                    nickname="Extra SMB Builtin",
                    query="collision query",
                    notes="would collide with custom",
                ),
            ],
        )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM dorkbook_entries WHERE builtin_key = ?",
            ("builtin_smb_extra",),
        ).fetchone()[0]

    assert touched == 0
    assert int(count) == 0


def test_open_connection_raises_when_schema_missing_index(tmp_path: Path) -> None:
    db = tmp_path / "broken.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """
            CREATE TABLE dorkbook_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                protocol TEXT NOT NULL,
                nickname TEXT NOT NULL DEFAULT '',
                query TEXT NOT NULL,
                query_normalized TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                row_kind TEXT NOT NULL DEFAULT 'custom',
                builtin_key TEXT UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

    with pytest.raises(RuntimeError, match="UNIQUE\\(protocol, query_normalized\\)"):
        open_connection(db)
