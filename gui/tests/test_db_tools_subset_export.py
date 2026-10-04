"""Card C1: allowlist isolation, atomic exports, and real Merge round trips."""

from contextlib import closing
from datetime import date
import sqlite3
import threading

import pytest

from experimental.censys_discovery import store as censys_store
from experimental.redseek import store as reddit_store
from experimental.se_dork import store as dork_store
from gui.utils.db_tools_engine import DBToolsEngine
from gui.utils import db_tools_engine_subset_methods as subset
from shared import db_migrations


PROTOCOLS = (
    ('S', 'smb_servers', 'share_access', 'host_probe_cache', 'host_user_flags'),
    ('F', 'ftp_servers', 'ftp_access', 'ftp_probe_cache', 'ftp_user_flags'),
    ('H', 'http_servers', 'http_access', 'http_probe_cache', 'http_user_flags'),
)
SELECTED = {'S': 11, 'F': 22, 'H': 33}
KEYS = ['S:11', 'F:22', 'H:33']
STAMP = '2026-10-01 12:00:00'


def insert(conn, table, **values):
    conn.execute(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                 tuple(values.values()))


def rows(path, table):
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f'SELECT * FROM {table}')]


def table_names(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not row[0].startswith('sqlite_')}


def seed_host(conn, kind, table, server_id, ip, notes='source notes'):
    values = dict(id=server_id, ip_address=ip, first_seen=STAMP, last_seen=STAMP, notes=notes)
    if kind != 'S':
        values['port'] = 2121 if kind == 'F' else 8080
    insert(conn, table, **values)


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(db_migrations, '_import_legacy_settings', lambda cur: None)
    path = tmp_path / 'source.db'
    db_migrations.run_migrations(str(path))
    # Store initializers commit but leave connections for GC. Close them here
    # deterministically before testing a standalone, byte-stable source file.
    connections = []
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        connections.append(conn)
        return conn

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', connect)
        try:
            reddit_store.init_db(path)
            dork_store.init_db(path)
            # Normally a sidecar; exercise the explicitly allowed primary-DB case too.
            censys_store.init_db(path)
        finally:
            for conn in connections:
                conn.close()
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute('PRAGMA foreign_keys=ON')
        for session_id in (11, 22, 33, 1011, 1022, 1033, 9999):
            insert(conn, 'scan_sessions', id=session_id, notes=f'session-{session_id}')
        for p, (kind, servers, access, cache, flags) in enumerate(PROTOCOLS):
            for n in (11, 22, 33):
                marker = f'{kind}-{n}'
                snapshot_id, result_id = 100 + p * 100 + n, 1000 + p * 100 + n
                seed_host(conn, kind, servers, n, f'192.0.2.{n}')
                access_values = dict(id=500 + n, server_id=n, session_id=n, accessible=1,
                                     access_details=marker)
                if kind == 'S':
                    access_values['share_name'] = 'public'
                insert(conn, access, **access_values)
                insert(conn, 'probe_snapshots', id=snapshot_id, snapshot_hash=marker,
                       host_type=kind, protocol_server_id=n, ip_address=f'192.0.2.{n}',
                       run_at=STAMP, raw_snapshot_json='{"marker":"' + marker + '"}')
                insert(conn, 'probe_snapshot_entries', id=snapshot_id, snapshot_id=snapshot_id,
                       entry_kind='file', share_name='public', path=f'/{marker}.key', metadata_json='{}')
                insert(conn, 'probe_snapshot_errors', id=snapshot_id, snapshot_id=snapshot_id,
                       message=marker)
                insert(conn, 'probe_snapshot_rce', snapshot_id=snapshot_id, rce_status='done',
                       verdict_summary=marker, analysis_json='{}')
                insert(conn, cache, server_id=n, latest_snapshot_id=snapshot_id,
                       snapshot_path=f'/private-local-only/{marker}.json', last_probe_at=STAMP,
                       status='probed', indicator_matches=n)
                insert(conn, flags, server_id=n, favorite=1, avoid=0, notes=marker, updated_at=STAMP)
                insert(conn, 'sherlock_results', id=result_id, host_type=kind, protocol_server_id=n,
                       snapshot_id=snapshot_id, ip_address=f'192.0.2.{n}', highest_severity='high',
                       total_hit_count=1, detail_count=1, scanned_at=STAMP, display_color_tag='User1')
                insert(conn, 'sherlock_hits', id=result_id, result_id=result_id, severity='high',
                       category='credentials', label=marker, pattern='*.key', display_path=f'/{marker}.key',
                       color_tag='User2')
                if kind == 'S':
                    insert(conn, 'share_credentials', id=600 + n, server_id=n, session_id=1000 + n,
                           share_name='public', username=marker, password=f'secret-{n}')
                    insert(conn, 'file_manifests', id=700 + n, server_id=n, session_id=n,
                           share_name='public', file_path=f'/{marker}.key', file_name=marker)
                    insert(conn, 'vulnerabilities', id=800 + n, server_id=n, session_id=n,
                           vuln_type='exposure', severity='high', title=marker)
        excluded = {
            'failure_logs': dict(session_id=9999, ip_address='192.0.2.99'),
            'extract_run_summaries': dict(ip_address='192.0.2.99', summary_json='{}'),
            'app_migration_state': dict(key='source-only'),
            'app_migration_reports': dict(migration_name='source-only', source='source', reason_code='skip'),
            'reddit_posts': dict(post_id='post', post_title='title', post_created_utc=0,
                                 source_sort='new', last_seen_at=STAMP),
            'reddit_targets': dict(post_id='post', target_raw='raw', target_normalized='normalized',
                                   created_at=STAMP, dedupe_key='key'),
            'reddit_ingest_state': dict(subreddit='test', sort_mode='new'),
            'dork_runs': dict(run_id=1, started_at=STAMP, instance_url='https://invalid.test',
                              query='query', max_results=1, status='done'),
            'dork_results': dict(run_id=1, url='https://invalid.test', url_normalized='https://invalid.test'),
            'censys_runs': dict(run_id=1, started_at=STAMP, protocol='smb', query_text='query',
                                max_pages=1, page_size=1, status='done'),
            'censys_results': dict(run_id=1, protocol='smb', ip_address='192.0.2.99', port=445,
                                   source_json='{}', dedupe_key='key'),
        }
        marker_columns = ('failure_reason', 'stop_reason', 'value', 'detail', 'post_author', 'notes',
                          'last_post_id', 'error_message', 'snippet', 'error_message', 'banner')
        for (table, values), column in zip(excluded.items(), marker_columns):
            values[column] = f'EXCLUDED_UNIQUE_{table}_MARKER'
            insert(conn, table, **values)
        for table, rule in subset.SUBSET_TABLE_REGISTRY.items():
            if rule.action == 'EXCLUDE' and table not in excluded:
                conn.execute(f'CREATE TABLE {table} (value TEXT)')
                insert(conn, table, value=f'EXCLUDED_UNIQUE_{table}_MARKER')
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    return path


def export(source, tmp_path, **kwargs):
    output = tmp_path / 'subset.db'
    result = DBToolsEngine(str(source)).export_subset(str(output), KEYS, **kwargs)
    assert result['success'], result
    return output, result


def test_registry_covers_runtime_schema(source):
    with closing(sqlite3.connect(source)) as conn:
        unclassified = table_names(conn) - subset.SUBSET_TABLE_REGISTRY.keys()
    assert not unclassified, f'Unclassified tables: {sorted(unclassified)}'
    assert all(rule.reason for rule in subset.SUBSET_TABLE_REGISTRY.values() if rule.action == 'EXCLUDE')


@pytest.mark.parametrize('include_credentials', [True, False])
def test_exact_subset_and_no_excluded_bytes(source, tmp_path, include_credentials):
    output, result = export(source, tmp_path, include_credentials=include_credentials)
    assert result['hosts'] == {'S': 1, 'F': 1, 'H': 1}
    assert result['missing'] == result['warnings'] == []
    assert result['error'] is None and not result['cancelled']
    assert result['output_path'] == str(output)
    assert result['size_bytes'] == output.stat().st_size
    snapshot_ids = {r['id'] for r in rows(source, 'probe_snapshots')
                    if SELECTED[r['host_type']] == r['protocol_server_id']}
    result_ids = {r['id'] for r in rows(source, 'sherlock_results')
                  if SELECTED[r['host_type']] == r['protocol_server_id']}
    session_ids = {11, 22, 33} | ({1011} if include_credentials else set())
    table_kinds = {table: kind for kind, *tables in PROTOCOLS for table in tables}
    table_kinds.update({table: 'S' for table in ('share_credentials', 'file_manifests', 'vulnerabilities')})
    with closing(sqlite3.connect(output)) as conn:
        output_tables = table_names(conn)
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert conn.execute('PRAGMA journal_mode').fetchone() == ('delete',)
    for table, rule in subset.SUBSET_TABLE_REGISTRY.items():
        if rule.action == 'EXCLUDE':
            if table in output_tables:
                assert rows(output, table) == []
            assert f'EXCLUDED_UNIQUE_{table}_MARKER'.encode() not in output.read_bytes()
            continue
        expected = []
        for row in rows(source, table):
            if table == 'scan_sessions':
                keep = row['id'] in session_ids
            elif table in ('probe_snapshots', 'sherlock_results'):
                keep = SELECTED[row['host_type']] == row['protocol_server_id']
            elif table.startswith('probe_snapshot_'):
                keep = row['snapshot_id'] in snapshot_ids
            elif table == 'sherlock_hits':
                keep = row['result_id'] in result_ids
            else:
                keep = row.get('server_id', row.get('id')) == SELECTED[table_kinds[table]]
            if table == 'share_credentials' and not include_credentials:
                keep = False
            if keep:
                if table.endswith('_probe_cache'):
                    row['snapshot_path'] = None
                expected.append(row)
        assert rows(output, table) == expected, table
        assert result['rows'].get(table, 0) == len(expected), table
    assert b'private-local-only' not in output.read_bytes()
    if not include_credentials:
        assert b'secret-' not in output.read_bytes()
    db_migrations.run_migrations(str(output))
    assert rows(output, 'smb_servers')[0]['id'] == 11


def test_unknown_table_excluded_with_warning(source, tmp_path):
    with closing(sqlite3.connect(source)) as conn, conn:
        conn.execute('CREATE TABLE surprise_private_data (value TEXT)')
        conn.execute("INSERT INTO surprise_private_data VALUES ('UNIQUE_UNKNOWN_SECRET')")
    output, result = export(source, tmp_path)
    assert any('surprise_private_data' in warning for warning in result['warnings'])
    with closing(sqlite3.connect(output)) as conn:
        assert 'surprise_private_data' not in table_names(conn)
    assert b'UNIQUE_UNKNOWN_SECRET' not in output.read_bytes()


@pytest.mark.parametrize('suffix', ['', '-wal', '-shm', '-journal'])
def test_refuses_source_and_journals(source, suffix):
    before = source.read_bytes()
    output = str(source) + suffix
    result = DBToolsEngine(str(source)).export_subset(output, KEYS)
    assert not result['success'] and result['error']
    assert source.read_bytes() == before
    if suffix:
        assert not source.with_name(source.name + suffix).exists()
    assert not source.with_name(source.name + suffix + '.partial').exists()


def test_refuses_source_symlink_and_partial_collision(source, tmp_path):
    alias = tmp_path / 'alias.db'
    alias.symlink_to(source)
    assert not DBToolsEngine(str(source)).export_subset(str(alias), KEYS)['success']
    partial_source = tmp_path / 'output.db.partial'
    partial_source.write_bytes(source.read_bytes())
    before = partial_source.read_bytes()
    assert not DBToolsEngine(str(partial_source)).export_subset(str(tmp_path / 'output.db'), KEYS)['success']
    assert partial_source.read_bytes() == before


@pytest.mark.parametrize('keys', [[], ['S:999', 'F:999', 'H:999']])
def test_empty_or_missing_selection(source, tmp_path, keys):
    output = tmp_path / 'subset.db'
    result = DBToolsEngine(str(source)).export_subset(str(output), keys)
    assert not result['success'] and result['error'] and not result['cancelled']
    assert result['missing'] == keys
    assert not output.exists() and not output.with_suffix('.db.partial').exists()


@pytest.mark.parametrize('key', ['X:1', 'S:-1', 'S:0', 'S:1.5', 's:1', 'S: 1', 'S:1\n',
                                 'S:1:2', 'H:', '', None, 42, f'F:{2**63}'])
def test_malformed_key_raises(source, tmp_path, key):
    with pytest.raises(ValueError, match='Malformed row key'):
        DBToolsEngine(str(source)).export_subset(str(tmp_path / 'subset.db'), ['S:11', key])
    assert not list(tmp_path.glob('subset.db*'))


def test_missing_and_duplicate_keys(source, tmp_path):
    output = tmp_path / 'subset.db'
    result = DBToolsEngine(str(source)).export_subset(str(output), iter(['S:11', 'F:999', 'S:11', 'H:888']))
    assert result['success']
    assert result['hosts'] == {'S': 1, 'F': 0, 'H': 0}
    assert result['missing'] == ['F:999', 'H:888']


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('failure', ['cancel', 'copy', 'integrity', 'replace'])
def test_atomic_failure_and_cancel(source, tmp_path, monkeypatch, existing, failure):
    output = tmp_path / 'subset.db'
    if existing:
        output.write_bytes(b'original destination')
    cancel = threading.Event()
    original_copy = subset._copy_table

    def copy(conn, table, rule, columns):
        count = original_copy(conn, table, rule, columns)
        if table == 'share_access':
            if failure == 'cancel':
                cancel.set()
            elif failure == 'copy':
                raise sqlite3.OperationalError('injected copy failure')
        return count

    monkeypatch.setattr(subset, '_copy_table', copy)
    if failure == 'integrity':
        real_connect = sqlite3.connect

        class BadIntegrity(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql == 'PRAGMA main.integrity_check':
                    return super().execute("SELECT 'injected integrity failure'")
                return super().execute(sql, *args, **kwargs)

        monkeypatch.setattr(subset.sqlite3, 'connect',
                            lambda *a, **k: real_connect(*a, factory=BadIntegrity, **k))
    if failure == 'replace':
        def fail_replace(*args):
            raise OSError('injected replace failure')
        monkeypatch.setattr(subset.os, 'replace', fail_replace)
    result = DBToolsEngine(str(source)).export_subset(str(output), KEYS, cancel_event=cancel)
    assert not result['success'] and result['error']
    assert result['cancelled'] == (failure == 'cancel')
    assert not list(tmp_path.glob('subset.db.partial*'))
    assert output.read_bytes() == b'original destination' if existing else not output.exists()


def test_success_replaces_existing_destination(source, tmp_path):
    (tmp_path / 'subset.db').write_bytes(b'old destination')
    output, result = export(source, tmp_path)
    assert output.read_bytes().startswith(b'SQLite format 3') and result['success']


def test_disk_space_and_preexisting_partial(source, tmp_path, monkeypatch):
    output = tmp_path / 'subset.db'
    engine = DBToolsEngine(str(source))
    checked = []
    monkeypatch.setattr(engine, '_check_disk_space', lambda size, path: checked.append((size, path)) or False)
    result = engine.export_subset(str(output), KEYS)
    assert not result['success'] and 'space' in result['error']
    assert checked == [(source.stat().st_size, str(tmp_path))]
    assert not list(tmp_path.glob('subset.db*'))
    monkeypatch.setattr(engine, '_check_disk_space', lambda *args: True)
    partial = tmp_path / 'subset.db.partial'
    partial.write_bytes(b'belongs to another export')
    assert not engine.export_subset(str(output), KEYS)['success']
    assert partial.read_bytes() == b'belongs to another export'


def test_source_unchanged_and_attached_readonly(source, tmp_path, monkeypatch):
    before, stat = source.read_bytes(), source.stat()
    original_copy = subset._copy_table
    attempts = []

    def copy(conn, table, rule, columns):
        if table == 'smb_servers':
            with pytest.raises(sqlite3.OperationalError, match='readonly'):
                conn.execute("UPDATE src.smb_servers SET notes='must not write'")
            attempts.append(True)
        return original_copy(conn, table, rule, columns)

    monkeypatch.setattr(subset, '_copy_table', copy)
    export(source, tmp_path)
    assert attempts == [True]
    assert source.read_bytes() == before
    assert (source.stat().st_mtime_ns, source.stat().st_size) == (stat.st_mtime_ns, stat.st_size)


def test_common_columns_only(source, tmp_path):
    with closing(sqlite3.connect(source)) as conn, conn:
        conn.execute('ALTER TABLE smb_servers ADD COLUMN future_column TEXT')
        conn.execute("UPDATE smb_servers SET future_column='SOURCE_ONLY_COLUMN_MARKER'")
        conn.execute('ALTER TABLE sherlock_hits DROP COLUMN color_tag')
    output, _ = export(source, tmp_path)
    assert b'SOURCE_ONLY_COLUMN_MARKER' not in output.read_bytes()
    assert all(row['color_tag'] is None for row in rows(output, 'sherlock_hits'))


def test_target_migration_metadata_is_preserved(source, tmp_path, monkeypatch):
    real_migrations = subset.run_migrations

    def migrate(path):
        real_migrations(path)
        with closing(sqlite3.connect(path)) as conn, conn:
            insert(conn, 'app_migration_state', key='target-only', value='initialized')
            insert(conn, 'app_migration_reports', migration_name='target-only', source='init',
                   reason_code='initialized')

    monkeypatch.setattr(subset, 'run_migrations', migrate)
    output, _ = export(source, tmp_path)
    assert [row['key'] for row in rows(output, 'app_migration_state')] == ['target-only']
    assert [row['migration_name'] for row in rows(output, 'app_migration_reports')] == ['target-only']


def test_live_wal_source_is_read_consistently(source, tmp_path):
    with closing(sqlite3.connect(source)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute("UPDATE smb_servers SET notes='committed WAL data' WHERE id=11")
        writer.commit()
        before, stat = source.read_bytes(), source.stat()

        def progress(percent, message):
            if message == 'Copying smb_servers':
                writer.execute("UPDATE smb_servers SET notes='later WAL data' WHERE id=11")
                writer.commit()

        output, _ = export(source, tmp_path, progress_callback=progress)
        assert rows(output, 'smb_servers')[0]['notes'] == 'committed WAL data'
        assert source.read_bytes() == before
        assert source.stat().st_mtime_ns == stat.st_mtime_ns


@pytest.mark.parametrize('existing', [False, True])
def test_merge_round_trip(source, tmp_path, existing):
    output, _ = export(source, tmp_path)
    target = tmp_path / 'target.db'
    db_migrations.run_migrations(str(target))
    if existing:
        with closing(sqlite3.connect(target)) as conn, conn:
            # One selected host already exists, with a different id and notes.
            seed_host(conn, 'S', 'smb_servers', 77, '192.0.2.11', notes='target notes')
            insert(conn, 'host_user_flags', server_id=77, favorite=0, avoid=1, notes='target flag notes')
    result = DBToolsEngine(str(target)).merge_database(str(output), auto_backup=False)
    assert result.success, result.errors
    assert not result.warnings
    assert result.snapshots_imported == result.probe_cache_imported == result.sherlock_results_imported == 3
    assert result.user_flags_merged == 3
    session_ids = {r['id'] for r in rows(target, 'scan_sessions')}
    # Merge intentionally maps imported activity to one new import session.
    assert len(session_ids) == 1 and not session_ids & {11, 22, 33, 1011}
    for kind, servers, access, cache, flags in PROTOCOLS:
        host = rows(target, servers)[0]
        assert len(rows(target, servers)) == 1
        assert host['id'] != SELECTED[kind]
        assert host['ip_address'] == f'192.0.2.{SELECTED[kind]}'
        access_row = rows(target, access)[0]
        assert access_row['server_id'] == host['id']
        assert access_row['session_id'] in session_ids
        assert access_row['access_details'] == f'{kind}-{SELECTED[kind]}'
        snapshot = next(r for r in rows(target, 'probe_snapshots') if r['host_type'] == kind)
        assert snapshot['protocol_server_id'] == host['id']
        assert snapshot['id'] not in {r['id'] for r in rows(output, 'probe_snapshots')}
        assert snapshot['snapshot_hash'] == f'{kind}-{SELECTED[kind]}'
        for table in ('probe_snapshot_entries', 'probe_snapshot_errors', 'probe_snapshot_rce'):
            child = next(r for r in rows(target, table) if r['snapshot_id'] == snapshot['id'])
            original = next(r for r in rows(output, table)
                            if r['snapshot_id'] == next(s['id'] for s in rows(output, 'probe_snapshots')
                                                       if s['host_type'] == kind))
            assert {k: v for k, v in child.items() if k not in ('id', 'snapshot_id')} == {
                k: v for k, v in original.items() if k not in ('id', 'snapshot_id')}
        cache_row = rows(target, cache)[0]
        assert cache_row['server_id'] == host['id']
        assert cache_row['latest_snapshot_id'] == snapshot['id']
        assert cache_row['snapshot_path'] is None
        flag = rows(target, flags)[0]
        assert flag['server_id'] == host['id'] and flag['favorite'] == 1
        if existing and kind == 'S':
            assert flag['avoid'] == 1
            assert flag['notes'] == f'target flag notes\n--- merged {date.today().isoformat()} ---\nS-11'
        else:
            assert flag['avoid'] == 0 and flag['notes'] == f'{kind}-{SELECTED[kind]}'
        sherlock = next(r for r in rows(target, 'sherlock_results') if r['host_type'] == kind)
        assert sherlock['protocol_server_id'] == host['id']
        assert sherlock['snapshot_id'] == snapshot['id']
        assert sherlock['id'] not in {r['id'] for r in rows(output, 'sherlock_results')}
        hit = next(r for r in rows(target, 'sherlock_hits') if r['result_id'] == sherlock['id'])
        assert hit['label'] == f'{kind}-{SELECTED[kind]}' and hit['color_tag'] == 'User2'
    smb_id = rows(target, 'smb_servers')[0]['id']
    for table in ('file_manifests', 'share_credentials', 'vulnerabilities'):
        row = rows(target, table)[0]
        assert row['server_id'] == smb_id and row['session_id'] in session_ids
    assert rows(target, 'file_manifests')[0]['file_path'] == '/S-11.key'
    with closing(sqlite3.connect(target)) as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
