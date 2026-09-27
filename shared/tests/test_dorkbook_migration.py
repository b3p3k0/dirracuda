"""Provider-schema upgrade checks; all databases are disposable tmp_path fixtures."""

from contextlib import closing
import sqlite3

import pytest

from experimental.dorkbook import schema, store
from experimental.dorkbook.models import DEFAULT_BUILTIN_DORKS


LEGACY_DDL = """
CREATE TABLE IF NOT EXISTS dorkbook_entries (
    entry_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    protocol          TEXT NOT NULL,
    nickname          TEXT NOT NULL DEFAULT '',
    query             TEXT NOT NULL,
    query_normalized  TEXT NOT NULL,
    notes             TEXT NOT NULL DEFAULT '',
    row_kind          TEXT NOT NULL DEFAULT 'custom',
    builtin_key       TEXT UNIQUE,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    CHECK (protocol IN ('SMB', 'FTP', 'HTTP')),
    CHECK (row_kind IN ('builtin', 'custom')),
    CHECK ((row_kind = 'builtin' AND builtin_key IS NOT NULL) OR row_kind = 'custom')
)
"""


def legacy_db(path, *, wal=False):
    conn = sqlite3.connect(str(path))
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute(LEGACY_DDL)
    conn.execute("CREATE UNIQUE INDEX ux_dorkbook_protocol_query_norm ON dorkbook_entries(protocol, query_normalized)")
    for spec in DEFAULT_BUILTIN_DORKS:
        if spec.builtin_key not in {"builtin_smb_default", "builtin_ftp_default", "builtin_http_default"}:
            continue
        conn.execute(
            """INSERT INTO dorkbook_entries
               (protocol, nickname, query, query_normalized, notes, row_kind,
                builtin_key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'builtin', ?, '2020-01-01', '2020-01-02')""",
            (spec.protocol, spec.nickname, spec.query, spec.query, spec.notes, spec.builtin_key),
        )
    conn.execute(
        """INSERT INTO dorkbook_entries
           (entry_id, protocol, nickname, query, query_normalized, notes,
            row_kind, created_at, updated_at)
           VALUES (50, 'HTTP', 'Mine', 'custom query', 'custom query',
                   'Keep my notes', 'custom', '2020-02-01', '2020-03-01')"""
    )
    conn.execute("UPDATE sqlite_sequence SET seq=9000 WHERE name='dorkbook_entries'")
    conn.commit()
    return conn


def old_rows(conn):
    fields = ", ".join(sorted(schema.LEGACY_COLUMNS))
    return conn.execute(f"SELECT {fields} FROM dorkbook_entries WHERE entry_id <= 50 ORDER BY entry_id").fetchall()


def columns(conn):
    return {row[1] for row in conn.execute("PRAGMA table_info(dorkbook_entries)")}


def test_upgrade_preserves_rows_timestamps_sequence_and_private_backup(tmp_path):
    path = tmp_path / "dorkbook.db"
    with closing(legacy_db(path)) as conn:
        before = old_rows(conn)
    store.init_db(path)
    backups = list(tmp_path.glob("dorkbook.db.pre-providers-*.bak"))
    assert len(backups) == 1
    assert backups[0].stat().st_mode & 0o077 == 0
    with closing(sqlite3.connect(str(backups[0]))) as backup:
        assert old_rows(backup) == before
        assert "provider" not in columns(backup)
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    with closing(store.open_connection(path)) as conn:
        assert [tuple(row) for row in old_rows(conn)] == before
        assert {(r[0], r[1]) for r in conn.execute("SELECT provider, topic FROM dorkbook_entries WHERE entry_id <= 50")} == {("shodan", "General")}
        next_id = store.create_entry(conn, "HTTP", "Next", "next query", "")
        assert next_id > 9000
        conn.commit()
    store.init_db(path)
    assert len(list(tmp_path.glob("*.bak"))) == 1
    with closing(store.open_connection(path)) as conn:
        assert [tuple(r) for r in old_rows(conn)][:4] == before


def test_backup_includes_uncheckpointed_wal_rows(tmp_path):
    path = tmp_path / "wal.db"
    with closing(legacy_db(path, wal=True)) as writer:
        writer.execute("UPDATE dorkbook_entries SET notes='Latest WAL value' WHERE entry_id=50")
        writer.commit()
        assert path.with_name(path.name + "-wal").stat().st_size > 0
        store.init_db(path)
        backup = next(tmp_path.glob("wal.db.pre-providers-*.bak"))
        with closing(sqlite3.connect(str(backup))) as conn:
            assert conn.execute("SELECT notes FROM dorkbook_entries WHERE entry_id=50").fetchone()[0] == "Latest WAL value"
        with closing(store.open_connection(path)) as conn:
            assert store.get_entry(conn, 50)["notes"] == "Latest WAL value"


def test_failed_rebuild_rolls_back_and_keeps_backup(tmp_path, monkeypatch):
    path = tmp_path / "failure.db"
    with closing(legacy_db(path)) as conn:
        before = old_rows(conn)
    original = schema._migrate_legacy

    def fail_after_drop(conn):
        conn.execute("DROP TABLE dorkbook_entries")
        raise sqlite3.OperationalError("injected failure")

    monkeypatch.setattr(schema, "_migrate_legacy", fail_after_drop)
    with pytest.raises(RuntimeError, match="original schema retained.*backup at"):
        store.init_db(path)
    with closing(sqlite3.connect(str(path))) as conn:
        assert old_rows(conn) == before
        assert "provider" not in columns(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert len(list(tmp_path.glob("*.bak"))) == 1
    monkeypatch.setattr(schema, "_migrate_legacy", original)
    store.init_db(path)
    with closing(store.open_connection(path)) as conn:
        assert [tuple(r) for r in old_rows(conn)] == before


def test_seed_failure_also_rolls_back_migration(tmp_path, monkeypatch):
    path = tmp_path / "seed_failure.db"
    with closing(legacy_db(path)) as conn:
        before = old_rows(conn)

    def fail_seed(_conn):
        raise RuntimeError("seed failure")

    monkeypatch.setattr(store, "upsert_builtin_pack", fail_seed)
    with pytest.raises(RuntimeError, match="seed failure"):
        store.init_db(path)
    with closing(sqlite3.connect(str(path))) as conn:
        assert old_rows(conn) == before
        assert "provider" not in columns(conn)


def test_backup_failure_prevents_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "backup_failure.db"
    with closing(legacy_db(path)) as conn:
        before = old_rows(conn)

    def fail_backup(_path):
        raise OSError("no backup space")

    monkeypatch.setattr(schema, "_backup_legacy", fail_backup)
    with pytest.raises(OSError, match="no backup space"):
        store.init_db(path)
    with closing(sqlite3.connect(str(path))) as conn:
        assert old_rows(conn) == before
        assert "provider" not in columns(conn)


@pytest.mark.parametrize("extra", [
    "ALTER TABLE dorkbook_entries ADD COLUMN future_value TEXT",
    "ALTER TABLE dorkbook_entries ADD COLUMN computed_value TEXT GENERATED ALWAYS AS (nickname || query) VIRTUAL",
    "CREATE TABLE user_annotations (value TEXT)",
    "CREATE INDEX custom_index ON dorkbook_entries(nickname)",
    "CREATE TRIGGER custom_trigger AFTER DELETE ON dorkbook_entries BEGIN SELECT 1; END",
    "CREATE TRIGGER sqliteXcustom AFTER DELETE ON dorkbook_entries BEGIN SELECT 1; END",
    "CREATE VIEW custom_view AS SELECT * FROM dorkbook_entries",
])
def test_unknown_legacy_schema_is_not_rebuilt(tmp_path, extra):
    path = tmp_path / "unknown.db"
    with closing(legacy_db(path)) as conn:
        conn.execute(extra)
        conn.commit()
        before = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    with pytest.raises(RuntimeError, match="unsupported"):
        store.init_db(path)
    with closing(sqlite3.connect(str(path))) as conn:
        assert conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall() == before
    assert not list(tmp_path.glob("*.bak"))


def test_partial_legacy_index_cannot_impersonate_required_uniqueness(tmp_path):
    path = tmp_path / "partial.db"
    with closing(legacy_db(path)) as conn:
        conn.execute("DROP INDEX ux_dorkbook_protocol_query_norm")
        conn.execute("CREATE UNIQUE INDEX ux_dorkbook_protocol_query_norm ON dorkbook_entries(protocol, query_normalized) WHERE row_kind='builtin'")
        conn.commit()
    with pytest.raises(RuntimeError, match="missing UNIQUE"):
        store.init_db(path)
    assert not list(tmp_path.glob("*.bak"))


def test_newer_version_is_not_modified(tmp_path):
    path = tmp_path / "future.db"
    store.init_db(path)
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="newer schema version 99"):
        store.init_db(path)
    with pytest.raises(RuntimeError, match="newer schema version 99"):
        store.open_connection(path)


def test_schema_rejects_wrong_current_partial_predicate(tmp_path):
    path = tmp_path / "bad_predicate.db"
    store.init_db(path)
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute("DROP INDEX ux_dorkbook_self_hosted_query")
        conn.execute("CREATE UNIQUE INDEX ux_dorkbook_self_hosted_query ON dorkbook_entries(provider, query_normalized) WHERE provider='self_hosted' AND row_kind='builtin'")
    with pytest.raises(RuntimeError, match="provider-scoped"):
        store.open_connection(path)


def test_empty_sidecar_is_initialized_without_backup(tmp_path):
    path = tmp_path / "empty.db"
    path.touch()
    store.init_db(path)
    assert not list(tmp_path.glob("*.bak"))


def test_init_rejects_unrelated_database(tmp_path):
    path = tmp_path / "wrong.db"
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute("CREATE TABLE smb_servers (id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="unrecognized database"):
        store.init_db(path)
    with closing(sqlite3.connect(str(path))) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("smb_servers",)]


def test_unmodified_builtins_do_not_change_timestamps(tmp_path):
    path = tmp_path / "timestamps.db"
    store.init_db(path)
    with closing(store.open_connection(path)) as conn:
        conn.execute("UPDATE dorkbook_entries SET updated_at='2000-01-01'")
        conn.commit()
    store.init_db(path)
    with closing(store.open_connection(path)) as conn:
        assert store.upsert_builtin_pack(conn) == 0
        assert {r[0] for r in conn.execute("SELECT updated_at FROM dorkbook_entries")} == {"2000-01-01"}


def test_simultaneous_initializers_migrate_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "concurrent.db"
    legacy_db(path).close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(store.init_db, [path, path]))
    assert len(list(tmp_path.glob("*.bak"))) == 1
    with closing(store.open_connection(path)) as conn:
        assert len(store.list_entries(conn, provider=None)) == len(DEFAULT_BUILTIN_DORKS) + 1


@pytest.mark.parametrize("provider,protocol", [
    ("shodan", None), ("self_hosted", "HTTP"), ("other", None),
])
def test_sql_constraints_enforce_destination_pairs(tmp_path, provider, protocol):
    path = tmp_path / "destinations.db"
    store.init_db(path)
    with closing(store.open_connection(path)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO dorkbook_entries
                   (provider, protocol, query, query_normalized, created_at, updated_at)
                   VALUES (?, ?, 'q', 'q', '2020', '2020')""", (provider, protocol),
            )
