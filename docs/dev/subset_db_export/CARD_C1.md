# Card C1 — Subset export engine

Status: authorized by HI 2026-10-04 (plan `PLAN.md`; HI delegated card orchestration to RA). DA card.
Repo: dirracuda · Branch: development · Python: `./venv/bin/python`
Depends on: card CM (accepted, commit `885de73`). Merge now imports probe
snapshots, probe caches, user flags and Sherlock.

## Context (stands alone)

The Server List shows hosts from three tables: `smb_servers`, `ftp_servers`,
`http_servers`. Each row has a `row_key` of the form `"S:<id>"`, `"F:<id>"` or
`"H:<id>"` (`gui/utils/database_access_protocol_methods.py:330/371/437`).
The user selects rows and wants to save just those hosts into a new SQLite
file. They can share it, and the receiver opens it directly or loads it with
DB Tools → Merge.

This card builds the engine only. UI is card C2.

## Scope

New module `gui/utils/db_tools_engine_subset_methods.py`, bound to
`DBToolsEngine` like the other satellites (`bind_…` in
`gui/utils/db_tools_engine.py`). Public method:

```python
def export_subset(self, output_path: str, row_keys: Iterable[str], *,
                  include_credentials: bool = True,
                  progress_callback: Optional[Callable[[int, str], None]] = None,
                  cancel_event: Optional[threading.Event] = None) -> Dict[str, Any]
```

Returns `{'success': bool, 'output_path', 'size_bytes', 'hosts': {'S': n, 'F': n, 'H': n},
'rows': {table: n}, 'missing': [row_key, ...], 'warnings': [...], 'error': str|None,
'cancelled': bool}`.

## Build method (HI decision D1: allowlist copy — do not change)

1. Validate the inputs (see Guardrails). Parse `row_keys`. A malformed key raises `ValueError`.
2. Create `<output_path>.partial` in the destination directory and run
   `shared.db_migrations.run_migrations` on it, so the target has the
   current full schema. If `run_migrations` alone does not create every core
   table, use the same init path the app uses for a new DB, and document which.
3. On a connection to the partial file, `ATTACH` the source **read-only**
   (`file:<path>?mode=ro`, `uri=True`). Put the selected ids in a TEMP table
   (no giant `IN (...)` lists). `PRAGMA foreign_keys = ON`.
4. Copy tables in FK-safe order with `INSERT INTO main.t (cols) SELECT cols FROM src.t …`.
   `cols` = the columns present on both sides. Keep the source primary keys,
   because the target is empty and FKs stay valid without a remap.
5. Leave the `app_migration_*` rows that `run_migrations` wrote. The target
   is a real current-schema DB.
6. Check `cancel_event` between tables. On cancel or error, delete the partial
   file and return `success=False` (`cancelled=True` on cancel).
7. `PRAGMA journal_mode=DELETE`, `PRAGMA integrity_check` (must be `ok`), close,
   then `os.replace(partial, output_path)`.

## Table registry (single source of truth)

Module-level constant, for example `SUBSET_TABLE_REGISTRY: Dict[str, Rule]`,
where every rule is INCLUDE (with how to select rows) or EXCLUDE (with a
reason string).

| Table | Rule |
|---|---|
| smb_servers, ftp_servers, http_servers | INCLUDE: `id` in the selection for S / F / H |
| share_access, share_credentials, file_manifests, vulnerabilities, host_user_flags, host_probe_cache | INCLUDE: `server_id` in the S selection. `share_credentials` only when `include_credentials`. |
| ftp_access, ftp_user_flags, ftp_probe_cache | INCLUDE: `server_id` in the F selection |
| http_access, http_user_flags, http_probe_cache | INCLUDE: `server_id` in the H selection |
| `*_probe_cache` | `snapshot_path` is written as NULL. **Never copy it.** It is a local absolute path. |
| probe_snapshots | INCLUDE: `(host_type, protocol_server_id)` in the selection |
| probe_snapshot_entries, probe_snapshot_errors, probe_snapshot_rce | INCLUDE: `snapshot_id` in the copied snapshots |
| sherlock_results | INCLUDE: `(host_type, protocol_server_id)` in the selection |
| sherlock_hits | INCLUDE: `result_id` in the copied results |
| scan_sessions | INCLUDE: only ids referenced by `session_id` in rows copied above. Copy these BEFORE the child rows (FK). |
| failure_logs | EXCLUDE: scan noise, keyed by IP only |
| extract_run_summaries | EXCLUDE: local download activity |
| app_migration_state, app_migration_reports | EXCLUDE: the target writes its own |
| reddit_posts, reddit_targets, reddit_ingest_state | EXCLUDE: discovery-run provenance |
| dork_runs, dork_results | EXCLUDE: discovery-run provenance |
| censys_runs, censys_results (if present in the primary DB) | EXCLUDE: discovery-run provenance |
| any `sqlite_*` table | ignored (internal) |
| any table you find that is not listed | Classify it, add a reason, and list it in your report |

Analyst data is not in the primary DB (separate sidecar) and is out of scope (HI Q1).

**Runtime rule:** a table present in the source but absent from the registry
is NOT copied. Add a warning naming it. Allowlist means unknown = excluded.

## Guardrails (invariants — do not weaken)

- Refuse (`success=False`, no file written) when `realpath(output_path)` equals
  `realpath(self.current_db_path)`, or equals the path of its `-wal`/`-shm`/`-journal` files.
- Refuse when `row_keys` is empty, or when no selected host exists in the source.
  Keys whose host does not exist go in `missing`, and the export continues with the rest.
- The source is never opened writable. Add a test that the source file's
  bytes/mtime are unchanged after an export.
- Use `self._check_disk_space` with the source DB size as a ceiling estimate.
- No change to the primary DB schema, migrations, Merge code, or existing tests.
  Do not touch `requirements.txt`.

## Tests (new `gui/tests/test_db_tools_subset_export.py`)

Fixture: a source DB in `tmp_path` built with `run_migrations`, and also the
experimental stores that write into the primary DB
(`experimental/redseek/store.py`, `experimental/se_dork/store.py`, and
`experimental/censys_discovery/store.py` if it targets the primary DB), so
their tables exist. Seed 3+ hosts per protocol, with rows in every INCLUDE
table, and rows in the EXCLUDE tables.

- **Registry guardrail:** every non-`sqlite_` table in the fixture schema is in
  `SUBSET_TABLE_REGISTRY`. A new unclassified table makes the test fail with
  the table name.
- Only selected hosts and their child rows are copied. Unselected hosts' rows
  are absent from every table.
- EXCLUDE tables are empty in the output. Seed a unique marker string in each
  excluded table and assert it does not appear anywhere in the output file.
- `include_credentials=False` → `share_credentials` empty. True → copied.
- `snapshot_path` is NULL in every output probe cache row.
- `scan_sessions` holds only referenced sessions. `PRAGMA foreign_key_check` is empty.
- Unknown table in the source → not copied, warning names it.
- Refusals: active DB path, its `-wal` path, empty keys, all keys missing,
  malformed key → `ValueError`.
- `missing` lists keys whose host does not exist.
- Cancel mid-export → no output file and no `.partial` left behind.
- Injected failure mid-copy → no output file and no `.partial` left behind.
- Existing destination file is replaced only on success. On failure the old file is untouched.
- Source unchanged (bytes or mtime + size).
- **Merge round trip:** export a subset, then `DBToolsEngine(target).merge_database(subset)`
  into (a) a fresh empty DB and (b) a DB that already holds one of the hosts with
  different notes. Assert that servers, access, manifests, snapshots + entries,
  probe cache, flags (Q3 combine rule) and sherlock results/hits all arrive with
  correct remapped ids.
- Output opens with `run_migrations` again with no error (it is a valid current DB).

## Done when

```
./venv/bin/python -m pytest gui/tests/test_db_tools_subset_export.py -v
./venv/bin/python -m pytest gui/tests -k "merge or db_tools or subset" -q
./venv/bin/python -m pytest shared/tests -q
```

All pass in the real venv. Report the exact commands and output, the changed
files, any table you added to the registry with its reason, and which DB init
path step 2 uses. Do not commit.
