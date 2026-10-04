"""Host-scoped imports; callers own the merge transaction and connections."""

from __future__ import annotations

from datetime import date


# Tokens used by the snapshot and Sherlock writers, not protocol names.
_HOST_TABLES = {
    "S": ("smb_servers", "host_probe_cache", "host_user_flags"),
    "F": ("ftp_servers", "ftp_probe_cache", "ftp_user_flags"),
    "H": ("http_servers", "http_probe_cache", "http_user_flags"),
}
_SNAPSHOT_COLUMNS = {
    "id", "snapshot_hash", "host_type", "ip_address", "port",
    "protocol_server_id", "run_at", "source", "raw_snapshot_json", "created_at",
}
_CHILD_COLUMNS = {
    "probe_snapshot_entries": {
        "snapshot_id", "share_name", "entry_kind", "path", "parent_path",
        "is_truncated", "metadata_json", "created_at",
    },
    "probe_snapshot_errors": {"snapshot_id", "share_name", "message", "created_at"},
    "probe_snapshot_rce": {
        "snapshot_id", "rce_status", "verdict_summary", "analysis_json", "created_at",
    },
}
_CACHE_COLUMNS = {"server_id", "last_probe_at", "snapshot_path", "latest_snapshot_id"}
_FLAG_COLUMNS = {"server_id", "favorite", "avoid", "notes", "updated_at"}
_RESULT_COLUMNS = {
    "id", "host_type", "protocol_server_id", "ip_address", "port", "snapshot_id",
    "highest_severity", "total_hit_count", "detail_count", "truncated",
    "scanned_at", "updated_at",
}
_HIT_COLUMNS = {
    "result_id", "severity", "category", "label", "pattern", "display_path", "created_at",
}


def _host_data_columns(self, ext_conn, cur_conn, table, required, warnings):
    """Guard both schemas and return their shared columns, or None to skip."""
    valid = True
    for label, conn in (("Source", ext_conn), ("Target", cur_conn)):
        if not self._table_has_required_columns(conn, table, required):
            valid = False
            if not self._table_exists(conn, table):
                warnings.append(f"{label} DB missing {table}; host data import skipped.")
            else:
                missing = sorted(required - self._table_columns(conn, table))
                warnings.append(
                    f"{label} table {table} missing required columns "
                    f"({', '.join(missing)}); host data import skipped."
                )
    if not valid:
        return None
    return self._table_columns(ext_conn, table) & self._table_columns(cur_conn, table)


def _insert_row(conn, table, values):
    columns = ', '.join(f'"{col}"' for col in values)
    placeholders = ', '.join('?' for _ in values)
    return conn.execute(
        f'INSERT INTO {table} ({columns}) VALUES ({placeholders})', tuple(values.values())
    ).lastrowid


def _update_row(conn, table, values, key, row_id):
    assignments = ', '.join(f'"{col}" = ?' for col in values)
    conn.execute(
        f'UPDATE {table} SET {assignments} WHERE {key} = ?',
        (*values.values(), row_id),
    )


def _rows(conn, table, columns):
    # Explicit projection is essential: source cache snapshot_path must never be read.
    projection = ', '.join(f'"{col}"' for col in sorted(columns))
    return conn.execute(f'SELECT {projection} FROM {table}')


def _host_data_source_wins(self, strategy, source_time, target_time):
    return strategy == MergeConflictStrategy.KEEP_SOURCE or (
        strategy == MergeConflictStrategy.KEEP_NEWER
        and self._parse_timestamp(source_time) > self._parse_timestamp(target_time)
    )


def _host_data_endpoint(self, conn, host_type, server_id):
    table = _HOST_TABLES[host_type][0]
    # SMB has no port column; its persisted endpoint port is NULL.
    port = 'port' if 'port' in self._table_columns(conn, table) else 'NULL AS port'
    return conn.execute(
        f'SELECT ip_address, {port} FROM {table} WHERE id = ?', (server_id,)
    ).fetchone()


def _import_probe_snapshots(self, ext_conn, cur_conn, host_id_maps, result):
    snapshot_id_map = {}
    inserted = {}
    columns = self._host_data_columns(
        ext_conn, cur_conn, 'probe_snapshots', _SNAPSHOT_COLUMNS, result.warnings
    )
    if columns is None:
        return snapshot_id_map, inserted
    for row in _rows(ext_conn, 'probe_snapshots', columns):
        server_id = host_id_maps.get(row['host_type'], {}).get(row['protocol_server_id'])
        if server_id is None:
            continue
        existing = cur_conn.execute(
            'SELECT id, host_type, protocol_server_id FROM probe_snapshots WHERE snapshot_hash = ?',
            (row['snapshot_hash'],),
        ).fetchone()
        if existing is not None:
            # Hashes cover only JSON, not the separately supplied host identity.
            if (existing['host_type'], existing['protocol_server_id']) != (row['host_type'], server_id):
                result.warnings.append(
                    f"Snapshot {row['id']} hash belongs to a different target host; skipped."
                )
                continue
            snapshot_id_map[row['id']] = existing['id']
            continue
        endpoint = self._host_data_endpoint(cur_conn, row['host_type'], server_id)
        values = {col: row[col] for col in columns - {'id'}}
        values.update(protocol_server_id=server_id, **dict(endpoint))
        snapshot_id = _insert_row(cur_conn, 'probe_snapshots', values)
        snapshot_id_map[row['id']] = snapshot_id
        inserted[row['id']] = snapshot_id
        result.snapshots_imported += 1
    return snapshot_id_map, inserted


def _import_probe_snapshot_children(self, ext_conn, cur_conn, inserted_snapshots, result):
    for table, required in _CHILD_COLUMNS.items():
        columns = self._host_data_columns(ext_conn, cur_conn, table, required, result.warnings)
        if columns is None:
            continue
        for row in _rows(ext_conn, table, columns - {'id'}):
            snapshot_id = inserted_snapshots.get(row['snapshot_id'])
            if snapshot_id is None:
                continue
            values = dict(row)
            values['snapshot_id'] = snapshot_id
            _insert_row(cur_conn, table, values)


def _import_probe_caches(self, ext_conn, cur_conn, host_id_maps, snapshot_id_map, strategy, result):
    for host_type, (_, table, _) in _HOST_TABLES.items():
        columns = self._host_data_columns(ext_conn, cur_conn, table, _CACHE_COLUMNS, result.warnings)
        if columns is None:
            continue
        for row in _rows(ext_conn, table, columns - {'snapshot_path'}):
            server_id = host_id_maps[host_type].get(row['server_id'])
            if server_id is None:
                continue
            target = cur_conn.execute(
                f'SELECT last_probe_at FROM {table} WHERE server_id = ?', (server_id,)
            ).fetchone()
            if target is not None and not self._host_data_source_wins(
                strategy, row['last_probe_at'], target['last_probe_at']
            ):
                continue
            values = dict(row)
            values['latest_snapshot_id'] = snapshot_id_map.get(row['latest_snapshot_id'])
            if target is None:
                values.update(server_id=server_id, snapshot_path=None)
                _insert_row(cur_conn, table, values)
            else:
                del values['server_id']
                _update_row(cur_conn, table, values, 'server_id', server_id)
            result.probe_cache_imported += 1


def _import_user_flags(self, ext_conn, cur_conn, host_id_maps, result):
    for host_type, (_, _, table) in _HOST_TABLES.items():
        columns = self._host_data_columns(ext_conn, cur_conn, table, _FLAG_COLUMNS, result.warnings)
        if columns is None:
            continue
        for row in _rows(ext_conn, table, columns):
            server_id = host_id_maps[host_type].get(row['server_id'])
            if server_id is None:
                continue
            target = cur_conn.execute(
                f'SELECT favorite, avoid, notes FROM {table} WHERE server_id = ?', (server_id,)
            ).fetchone()
            if target is None:
                values = dict(row)
                values['server_id'] = server_id
                _insert_row(cur_conn, table, values)
            else:
                favorite = int(bool(target['favorite'] or row['favorite']))
                avoid = int(bool(target['avoid'] or row['avoid']))
                notes = target['notes']
                target_text = (notes or '').strip()
                source_text = (row['notes'] or '').strip()
                if not target_text:
                    notes = row['notes']
                elif source_text and source_text != target_text:
                    notes += f'\n--- merged {date.today().isoformat()} ---\n' + row['notes']
                if (favorite, avoid, notes) == (target['favorite'], target['avoid'], target['notes']):
                    continue
                cur_conn.execute(
                    f'UPDATE {table} SET favorite = ?, avoid = ?, notes = ?, '
                    'updated_at = CURRENT_TIMESTAMP WHERE server_id = ?',
                    (favorite, avoid, notes, server_id),
                )
            result.user_flags_merged += 1


def _import_sherlock_results(self, ext_conn, cur_conn, host_id_maps, snapshot_id_map, strategy, result):
    columns = self._host_data_columns(
        ext_conn, cur_conn, 'sherlock_results', _RESULT_COLUMNS, result.warnings
    )
    hit_columns = self._host_data_columns(
        ext_conn, cur_conn, 'sherlock_hits', _HIT_COLUMNS, result.warnings
    )
    # Results and their details are replaced together, or both are left alone.
    if columns is None or hit_columns is None:
        return
    hit_columns -= {'id'}
    hit_projection = ', '.join(f'"{col}"' for col in sorted(hit_columns))
    for row in _rows(ext_conn, 'sherlock_results', columns):
        server_id = host_id_maps.get(row['host_type'], {}).get(row['protocol_server_id'])
        if server_id is None:
            continue
        target = cur_conn.execute(
            'SELECT id, scanned_at FROM sherlock_results WHERE host_type = ? AND protocol_server_id = ?',
            (row['host_type'], server_id),
        ).fetchone()
        if target is not None and not self._host_data_source_wins(
            strategy, row['scanned_at'], target['scanned_at']
        ):
            continue
        endpoint = self._host_data_endpoint(cur_conn, row['host_type'], server_id)
        values = {col: row[col] for col in columns - {'id'}}
        values.update(
            protocol_server_id=server_id, snapshot_id=snapshot_id_map.get(row['snapshot_id']),
            **dict(endpoint),
        )
        if target is None:
            result_id = _insert_row(cur_conn, 'sherlock_results', values)
        else:
            result_id = target['id']
            _update_row(cur_conn, 'sherlock_results', values, 'id', result_id)
            cur_conn.execute('DELETE FROM sherlock_hits WHERE result_id = ?', (result_id,))
        for hit in ext_conn.execute(
            f'SELECT {hit_projection} FROM sherlock_hits WHERE result_id = ?', (row['id'],)
        ):
            hit_values = dict(hit)
            hit_values['result_id'] = result_id
            _insert_row(cur_conn, 'sherlock_hits', hit_values)
        result.sherlock_results_imported += 1


def _preview_merge_host_data(self, ext_conn, cur_conn, warnings):
    """Count source rows belonging to servers eligible for the existing importers."""
    counts = dict(total_snapshots=0, total_probe_cache=0, total_user_flags=0, total_sherlock_results=0)
    eligible_hosts = {}
    for host_type, (table, _, _) in _HOST_TABLES.items():
        required = {
            'S': REQUIRED_SERVER_COLUMNS,
            'F': REQUIRED_FTP_SERVER_COLUMNS,
            'H': REQUIRED_HTTP_SERVER_COLUMNS,
        }[host_type] | {'id'}
        if all(self._table_has_required_columns(conn, table, required) for conn in (ext_conn, cur_conn)):
            eligible_hosts[host_type] = table

    specs = [
        ('probe_snapshots', _SNAPSHOT_COLUMNS, 'total_snapshots', None),
        ('sherlock_results', _RESULT_COLUMNS, 'total_sherlock_results', None),
    ]
    for host_type, (_, cache, flags) in _HOST_TABLES.items():
        specs.extend(((cache, _CACHE_COLUMNS, 'total_probe_cache', host_type),
                      (flags, _FLAG_COLUMNS, 'total_user_flags', host_type)))
    for table, required, counter, fixed_host_type in specs:
        if self._host_data_columns(ext_conn, cur_conn, table, required, warnings) is None:
            continue
        for host_type, server_table in eligible_hosts.items():
            if fixed_host_type is not None and fixed_host_type != host_type:
                continue
            key = 'server_id' if fixed_host_type is not None else 'protocol_server_id'
            condition = '' if fixed_host_type is not None else ' WHERE data.host_type = ?'
            params = () if fixed_host_type is not None else (host_type,)
            counts[counter] += ext_conn.execute(
                f'SELECT COUNT(*) FROM {table} AS data '
                f'JOIN {server_table} AS server ON server.id = data.{key}{condition}', params,
            ).fetchone()[0]
    return counts


def bind_db_tools_engine_merge_host_data_methods(engine_cls, shared_symbols):
    """Attach host-data imports using the engine's existing satellite convention."""
    globals().update(shared_symbols)
    for name in (
        '_host_data_columns', '_host_data_source_wins', '_host_data_endpoint',
        '_import_probe_snapshots', '_import_probe_snapshot_children', '_import_probe_caches',
        '_import_user_flags', '_import_sherlock_results', '_preview_merge_host_data',
    ):
        setattr(engine_cls, name, globals()[name])
