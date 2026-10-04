"""Card CM: portable host data imports through the real atomic merge path."""

from contextlib import closing
from datetime import date
import sqlite3

import pytest

from gui.utils.db_tools_engine import DBToolsEngine, MergeConflictStrategy
from shared import db_migrations


PROTOCOLS = [
    ('S', 'smb_servers', 'host_probe_cache', 'host_user_flags', None),
    ('F', 'ftp_servers', 'ftp_probe_cache', 'ftp_user_flags', 2121),
    ('H', 'http_servers', 'http_probe_cache', 'http_user_flags', 8080),
]
NEWER = '2026-09-02 12:00:00'
OLDER = '2026-09-01 12:00:00'


def execute(path, sql, params=()):
    with closing(sqlite3.connect(path)) as conn, conn:
        return conn.execute(sql, params).lastrowid


def rows(path, table):
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f'SELECT * FROM {table}')]


def insert(db_path, table, **values):
    return execute(
        db_path, f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
        tuple(values.values()),
    )


def seed_host(path, protocol, server_id, ip='192.0.2.10'):
    _, table, _, _, port = protocol
    values = dict(id=server_id, ip_address=ip, first_seen=OLDER, last_seen=OLDER)
    if port is not None:
        values['port'] = port
    insert(path, table, **values)


def seed_data(path, protocol, server_id=1, snapshot_id=10, result_id=20,
              stamp=NEWER, label='source', snapshot_hash=None):
    host_type, _, cache, flags, _ = protocol
    insert(path, 'probe_snapshots', id=snapshot_id, snapshot_hash=snapshot_hash or host_type + label,
           host_type=host_type, protocol_server_id=server_id, ip_address='stale-address', port=9999,
           run_at=stamp, source='runtime', raw_snapshot_json='{"shares": []}', created_at=stamp)
    insert(path, 'probe_snapshot_entries', snapshot_id=snapshot_id, share_name='public',
           entry_kind='file', path=label, parent_path='/', is_truncated=1,
           metadata_json='{"size": 12}', created_at=stamp)
    insert(path, 'probe_snapshot_errors', snapshot_id=snapshot_id, share_name='private',
           message=label, created_at=stamp)
    insert(path, 'probe_snapshot_rce', snapshot_id=snapshot_id, rce_status='done',
           verdict_summary=label, analysis_json='{}', created_at=stamp)
    insert(path, cache, server_id=server_id, last_probe_at=stamp,
           latest_snapshot_id=snapshot_id, snapshot_path=f'/{label}/private.json',
           status=label, indicator_matches=3, indicator_samples='["sample"]',
           extracted=1, rce_status='done', rce_verdict_summary=label, updated_at=stamp)
    insert(path, flags, server_id=server_id, favorite=1, avoid=0, notes=label, updated_at=stamp)
    insert(path, 'sherlock_results', id=result_id, host_type=host_type,
           protocol_server_id=server_id, ip_address='stale-address', port=9999,
           snapshot_id=snapshot_id, highest_severity='high', total_hit_count=1,
           detail_count=1, truncated=0, scanned_at=stamp, updated_at=stamp, display_color_tag='User1')
    insert(path, 'sherlock_hits', result_id=result_id, severity='high', category='credentials',
           label=label, pattern='*.key', display_path='/private.key', created_at=stamp, color_tag='User2')


@pytest.fixture
def databases(tmp_path, monkeypatch):
    # Migration fixtures must not consume real user settings.
    monkeypatch.setattr(db_migrations, '_import_legacy_settings', lambda cur: None)
    source, target = tmp_path / 'source.db', tmp_path / 'target.db'
    for path in (source, target):
        db_migrations.run_migrations(str(path))
    for protocol in PROTOCOLS:
        seed_host(source, protocol, 1)
        seed_host(target, protocol, 41, '192.0.2.41')
        seed_host(target, protocol, 42)
    return source, target


def merge(databases, strategy=MergeConflictStrategy.KEEP_NEWER):
    source, target = databases
    result = DBToolsEngine(str(target)).merge_database(str(source), strategy, auto_backup=False)
    assert result.success, result.errors
    return result


def test_round_trip_and_preview(databases):
    source, target = databases
    for i, protocol in enumerate(PROTOCOLS):
        seed_data(source, protocol, snapshot_id=10 + i, result_id=20 + i)
        if protocol[0] != 'S':
            execute(source, f'UPDATE {protocol[2]} SET accessible_dirs_count=2, accessible_dirs_list=?', ('["/pub"]',))
    execute(source, 'UPDATE http_probe_cache SET accessible_files_count=9')
    preview = DBToolsEngine(str(target)).preview_merge(str(source))
    assert preview['valid']
    for key in ('total_snapshots', 'total_probe_cache', 'total_user_flags', 'total_sherlock_results'):
        assert preview[key] == 3
    result = merge(databases)
    assert not result.warnings
    assert (result.snapshots_imported, result.probe_cache_imported,
            result.user_flags_merged, result.sherlock_results_imported) == (3, 3, 3, 3)
    snapshots = {row['host_type']: row for row in rows(target, 'probe_snapshots')}
    results = {row['host_type']: row for row in rows(target, 'sherlock_results')}
    for protocol in PROTOCOLS:
        host_type, _, cache, flags, port = protocol
        snapshot = snapshots[host_type]
        assert snapshot['id'] not in (10, 11, 12)
        assert snapshot['protocol_server_id'] == 42
        assert snapshot['ip_address'] == '192.0.2.10'
        assert snapshot['port'] == port
        original = next(row for row in rows(source, 'probe_snapshots') if row['host_type'] == host_type)
        for column in ('snapshot_hash', 'run_at', 'source', 'raw_snapshot_json', 'created_at'):
            assert snapshot[column] == original[column]
        for table in ('probe_snapshot_entries', 'probe_snapshot_errors', 'probe_snapshot_rce'):
            child = next(row for row in rows(target, table) if row['snapshot_id'] == snapshot['id'])
            source_child = next(row for row in rows(source, table) if row['snapshot_id'] == original['id'])
            assert {k: v for k, v in child.items() if k not in ('id', 'snapshot_id')} == {
                k: v for k, v in source_child.items() if k not in ('id', 'snapshot_id')}
        cache_row = rows(target, cache)[0]
        assert cache_row['server_id'] == 42
        assert cache_row['snapshot_path'] is None
        assert cache_row['latest_snapshot_id'] == snapshot['id']
        for key, value in rows(source, cache)[0].items():
            if key not in ('server_id', 'snapshot_path', 'latest_snapshot_id'):
                assert cache_row[key] == value
        flag = rows(target, flags)[0]
        assert flag == dict(rows(source, flags)[0], server_id=42)
        sherlock = results[host_type]
        assert sherlock['id'] not in (20, 21, 22)
        assert sherlock['protocol_server_id'] == 42
        assert sherlock['snapshot_id'] == snapshot['id']
        assert (sherlock['ip_address'], sherlock['port']) == ('192.0.2.10', port)
        original_result = next(row for row in rows(source, 'sherlock_results') if row['host_type'] == host_type)
        for key, value in original_result.items():
            if key not in ('id', 'protocol_server_id', 'snapshot_id', 'ip_address', 'port'):
                assert sherlock[key] == value
        hit = next(row for row in rows(target, 'sherlock_hits') if row['result_id'] == sherlock['id'])
        source_hit = next(row for row in rows(source, 'sherlock_hits') if row['result_id'] == original_result['id'])
        assert {k: v for k, v in hit.items() if k not in ('id', 'result_id')} == {
            k: v for k, v in source_hit.items() if k not in ('id', 'result_id')}
    with closing(sqlite3.connect(target)) as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('protocol', PROTOCOLS)
def test_snapshot_dedupe_preserves_children_and_remaps_dependents(databases, protocol):
    source, target = databases
    seed_data(source, protocol, snapshot_hash='same')
    seed_data(target, protocol, server_id=42, snapshot_id=90, result_id=80,
              stamp=OLDER, label='target', snapshot_hash='same')
    child_tables = ('probe_snapshot_entries', 'probe_snapshot_errors', 'probe_snapshot_rce')
    before = {table: rows(target, table) for table in child_tables}
    result = merge(databases)
    assert result.snapshots_imported == 0
    assert len(rows(target, 'probe_snapshots')) == 1
    assert {table: rows(target, table) for table in child_tables} == before
    assert rows(target, protocol[2])[0]['latest_snapshot_id'] == 90
    assert rows(target, 'sherlock_results')[0]['snapshot_id'] == 90
    assert rows(target, 'sherlock_hits')[0]['result_id'] == 80


@pytest.mark.parametrize('other_host_type', ['S', 'F'])
def test_snapshot_hash_collision_with_other_host_is_skipped(databases, other_host_type):
    source, target = databases
    seed_data(source, PROTOCOLS[0], snapshot_hash='collision')
    insert(target, 'probe_snapshots', id=90, snapshot_hash='collision', host_type=other_host_type,
           protocol_server_id=41 if other_host_type == 'S' else 42,
           ip_address='192.0.2.41', raw_snapshot_json='{}')
    before = rows(target, 'probe_snapshots')
    result = merge(databases)
    assert result.snapshots_imported == 0
    assert any('different target host' in warning for warning in result.warnings)
    assert rows(target, 'probe_snapshots') == before
    for table in ('probe_snapshot_entries', 'probe_snapshot_errors', 'probe_snapshot_rce'):
        assert rows(target, table) == []
    assert rows(target, 'host_probe_cache')[0]['latest_snapshot_id'] is None
    assert rows(target, 'sherlock_results')[0]['snapshot_id'] is None


@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('strategy,stamp,wins', [
    (MergeConflictStrategy.KEEP_NEWER, NEWER, True),
    (MergeConflictStrategy.KEEP_NEWER, OLDER, False),
    (MergeConflictStrategy.KEEP_NEWER, '2020-01-01', False),
    (MergeConflictStrategy.KEEP_NEWER, None, False),
    (MergeConflictStrategy.KEEP_SOURCE, '2020-01-01', True),
    (MergeConflictStrategy.KEEP_CURRENT, NEWER, False),
])
def test_cache_and_sherlock_strategies(databases, protocol, strategy, stamp, wins):
    source, target = databases
    seed_data(source, protocol, stamp=stamp)
    seed_data(target, protocol, server_id=42, snapshot_id=90, result_id=80,
              stamp=OLDER, label='target')
    cache_before = rows(target, protocol[2])
    results_before = rows(target, 'sherlock_results')
    hits_before = rows(target, 'sherlock_hits')
    result = merge(databases, strategy)
    assert result.probe_cache_imported == int(wins)
    assert result.sherlock_results_imported == int(wins)
    assert rows(target, protocol[2])[0]['snapshot_path'] == '/target/private.json'
    if wins:
        snapshot_id = next(row['id'] for row in rows(target, 'probe_snapshots') if row['source'] == 'runtime' and row['id'] != 90)
        cache = rows(target, protocol[2])[0]
        assert cache['status'] == 'source'
        assert cache['latest_snapshot_id'] == snapshot_id
        sherlock = rows(target, 'sherlock_results')[0]
        assert sherlock['id'] == 80
        assert sherlock['snapshot_id'] == snapshot_id
        assert sherlock['scanned_at'] == stamp
        assert sherlock['ip_address'] == '192.0.2.10'
        assert sherlock['port'] == protocol[4]
        hits = rows(target, 'sherlock_hits')
        assert len(hits) == 1
        assert hits[0]['label'] == 'source'
        assert hits[0]['result_id'] == 80
    else:
        assert rows(target, protocol[2]) == cache_before
        assert rows(target, 'sherlock_results') == results_before
        assert rows(target, 'sherlock_hits') == hits_before


@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('target_notes,source_notes,expected,changed', [
    (None, 'source', 'source', True),
    ('', 'source', 'source', True),
    ('target', None, 'target', False),
    ('target', '', 'target', False),
    (' target ', 'target', ' target ', False),
    ('target', 'source', f'target\n--- merged {date.today().isoformat()} ---\nsource', True),
])
def test_flag_notes_cases_and_no_clearing(databases, protocol, target_notes, source_notes, expected, changed):
    source, target = databases
    table = protocol[3]
    insert(source, table, server_id=1, favorite=0, avoid=0, notes=source_notes, updated_at=NEWER)
    insert(target, table, server_id=42, favorite=1, avoid=1, notes=target_notes, updated_at=OLDER)
    result = merge(databases, MergeConflictStrategy.KEEP_SOURCE)
    row = rows(target, table)[0]
    assert (row['favorite'], row['avoid'], row['notes']) == (1, 1, expected)
    assert (row['updated_at'] != OLDER) == changed
    assert result.user_flags_merged == int(changed)


@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('strategy', list(MergeConflictStrategy))
def test_flags_combine_independently_of_strategy(databases, protocol, strategy):
    source, target = databases
    table = protocol[3]
    insert(source, table, server_id=1, favorite=0, avoid=1, notes='same')
    insert(target, table, server_id=42, favorite=1, avoid=0, notes='same', updated_at=OLDER)
    result = merge(databases, strategy)
    row = rows(target, table)[0]
    assert (row['favorite'], row['avoid'], row['notes']) == (1, 1, 'same')
    assert row['updated_at'] != OLDER
    assert result.user_flags_merged == 1


@pytest.mark.parametrize('existing', [False, True])
def test_unmapped_snapshot_is_null_and_empty_source_hits_replace_target(databases, existing):
    source, target = databases
    seed_data(source, PROTOCOLS[0])
    execute(source, 'UPDATE host_probe_cache SET latest_snapshot_id=999')
    execute(source, 'UPDATE sherlock_results SET snapshot_id=999')
    execute(source, 'DELETE FROM sherlock_hits')
    if existing:
        seed_data(target, PROTOCOLS[0], server_id=42, snapshot_id=90, result_id=80, label='target')
    merge(databases, MergeConflictStrategy.KEEP_SOURCE)
    assert rows(target, 'host_probe_cache')[0]['latest_snapshot_id'] is None
    assert rows(target, 'sherlock_results')[0]['snapshot_id'] is None
    assert rows(target, 'sherlock_hits') == []


SCHEMA_GAPS = [
    ('probe_snapshots', 'raw_snapshot_json'),
    ('probe_snapshot_entries', 'entry_kind'),
    ('probe_snapshot_errors', 'message'),
    ('probe_snapshot_rce', 'analysis_json'),
    ('host_probe_cache', 'last_probe_at'),
    ('ftp_probe_cache', 'last_probe_at'),
    ('http_probe_cache', 'last_probe_at'),
    ('host_user_flags', 'favorite'),
    ('ftp_user_flags', 'favorite'),
    ('http_user_flags', 'favorite'),
    ('sherlock_results', 'scanned_at'),
    ('sherlock_hits', 'pattern'),
]


@pytest.mark.parametrize('side', ['source', 'target'])
@pytest.mark.parametrize('gap', ['table', 'column'])
@pytest.mark.parametrize('table,column', SCHEMA_GAPS)
def test_missing_schema_warns_and_succeeds(databases, side, gap, table, column):
    source, target = databases
    for i, protocol in enumerate(PROTOCOLS):
        seed_data(source, protocol, snapshot_id=10+i, result_id=20+i)
    path = source if side == 'source' else target
    if gap == 'table':
        execute(path, f'DROP TABLE {table}')
    else:
        execute(path, f'ALTER TABLE {table} DROP COLUMN {column}')
    result = merge(databases)
    assert any(side.title() in warning and table in warning and
               ('missing ' + table if gap == 'table' else 'missing required columns') in warning
               for warning in result.warnings), result.warnings
    assert result.servers_skipped == 3


@pytest.mark.parametrize('side', ['source', 'target'])
def test_optional_columns_copy_only_when_shared(databases, side):
    source, target = databases
    seed_data(source, PROTOCOLS[2])
    path = source if side == 'source' else target
    for table, column in [('http_probe_cache', 'accessible_files_count'),
                          ('sherlock_results', 'display_color_tag'), ('sherlock_hits', 'color_tag')]:
        execute(path, f'ALTER TABLE {table} DROP COLUMN {column}')
    result = merge(databases)
    assert not result.warnings
    assert result.probe_cache_imported == result.sherlock_results_imported == 1


def test_source_snapshot_path_is_not_read(databases, monkeypatch):
    source, target = databases
    seed_data(source, PROTOCOLS[0])
    connect = sqlite3.connect

    def guarded_connect(database, *args, **kwargs):
        conn = connect(database, *args, **kwargs)
        if str(database) == f'file:{source}?mode=ro':
            def authorize(action, table, column, *_):
                if action == sqlite3.SQLITE_READ and table.endswith('probe_cache') and column == 'snapshot_path':
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            conn.set_authorizer(authorize)
        return conn

    monkeypatch.setattr(sqlite3, 'connect', guarded_connect)
    merge(databases)
    assert rows(target, 'host_probe_cache')[0]['snapshot_path'] is None


def test_unmapped_hosts_are_not_imported_or_counted(databases):
    source, target = databases
    seed_data(source, PROTOCOLS[0], server_id=999)
    preview = DBToolsEngine(str(target)).preview_merge(str(source))
    for key in ('total_snapshots', 'total_probe_cache', 'total_user_flags', 'total_sherlock_results'):
        assert preview[key] == 0
    result = merge(databases)
    assert (result.snapshots_imported, result.probe_cache_imported,
            result.user_flags_merged, result.sherlock_results_imported) == (0, 0, 0, 0)


def test_preview_excludes_protocol_whose_target_server_table_is_missing(databases):
    source, target = databases
    for i, protocol in enumerate(PROTOCOLS):
        seed_data(source, protocol, snapshot_id=10+i, result_id=20+i)
    execute(target, 'DROP TABLE ftp_servers')
    preview = DBToolsEngine(str(target)).preview_merge(str(source))
    assert preview['valid']
    for key in ('total_snapshots', 'total_probe_cache', 'total_user_flags', 'total_sherlock_results'):
        assert preview[key] == 2


@pytest.mark.parametrize('method', [
    '_import_probe_snapshots', '_import_probe_snapshot_children', '_import_probe_caches',
    '_import_user_flags', '_import_sherlock_results',
])
def test_failure_rolls_back_entire_merge(databases, monkeypatch, method):
    source, target = databases
    seed_data(source, PROTOCOLS[0])
    seed_data(target, PROTOCOLS[0], server_id=42, snapshot_id=90, result_id=80,
              label='target', stamp=OLDER)
    seed_host(source, PROTOCOLS[0], 2, '192.0.2.100')
    with closing(sqlite3.connect(target)) as conn:
        before = list(conn.iterdump())
    original = getattr(DBToolsEngine, method)

    def fail_after_import(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError('injected host import failure')

    monkeypatch.setattr(DBToolsEngine, method, fail_after_import)
    result = DBToolsEngine(str(target)).merge_database(str(source), auto_backup=False)
    assert not result.success
    assert 'injected host import failure' in result.errors
    with closing(sqlite3.connect(target)) as conn:
        assert list(conn.iterdump()) == before
