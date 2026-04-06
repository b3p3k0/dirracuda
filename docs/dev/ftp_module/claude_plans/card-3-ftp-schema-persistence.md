# Card 3: FTP Schema and Persistence Layer — Implementation Plan (Revised)

## Context

Cards 1 and 2 delivered a wired FTP scan path in the GUI and a CLI skeleton
(`ftpseek` → `FtpWorkflow`) that streams progress output. At the end of Card 2,
FTP scans run end-to-end but write nothing to disk. Card 3 adds the persistence
layer: two new sidecar tables (`ftp_servers`, `ftp_access`), idempotent
migrations that run automatically in every entry point, a protocol-coexistence
view (`v_host_protocols`), and the read/write functions downstream code will
call. No manual migration steps, no crashes on older DBs.

---

## 1. Current-State Analysis

### Schema today (`tools/db_schema.sql`)

Lines 4–10 contain `DROP TABLE IF EXISTS` for six core tables:

```sql
DROP TABLE IF EXISTS file_manifests;
DROP TABLE IF EXISTS vulnerabilities;
DROP TABLE IF EXISTS share_access;
DROP TABLE IF EXISTS failure_logs;
DROP TABLE IF EXISTS smb_servers;
DROP TABLE IF EXISTS scan_sessions;
```

These plus `share_credentials`, `host_user_flags`, `host_probe_cache`, three
views, and a full index block complete the file (239 lines).

`initialize_database()` in `tools/db_manager.py` (line 158) runs the entire
`db_schema.sql` via `cursor.executescript(schema_sql)`. It is only called when
`_inspect_schema_state()` returns `"empty"` or the DB file is brand-new —
never on an existing DB that has `smb_servers` + `scan_sessions`.

**FTP tables do not exist anywhere today.**

### Two independent migration paths

There are **two separate migration mechanisms** that must stay aligned:

**Path A — `tools/db_manager.py:DatabaseManager._run_migrations()` (line 141)**

- Runs every time `DatabaseManager.__init__` is called
- Scope: single migration — adds `share_access.auth_status` if missing
- This path is SMB-only; FTP tables must NOT be added here

**Path B — `shared/db_migrations.py:run_migrations()` (line 15)**

- Idempotent; safe to run multiple times
- Creates `share_credentials`, `host_user_flags`, `host_probe_cache` via
  `CREATE TABLE IF NOT EXISTS`
- Adds RCE columns to `host_probe_cache` via PRAGMA check + `ALTER TABLE`
- **This is the correct home for FTP table creation**

### Where `run_migrations()` is called today — auto-run hooks

| Hook | File | Effect |
|---|---|---|
| `SMBSeekWorkflowDatabase.__init__` line 41 | `shared/database.py` | Every `./smbseek` invocation |
| `DatabaseReader.__init__` line 55 | `gui/utils/database_access.py` | Every `./xsmbseek` and `./xsmbseek --mock` |
| **`ftpseek` — missing** | `ftpseek` | **Not wired; fixed in Patch 3** |

### `DatabaseReader` actual query pattern

`DatabaseReader` at line 288 exposes `_get_connection()` as a `@contextmanager`.
Existing query methods use:

```python
with self._get_connection() as conn:
    result = conn.execute(query, params).fetchall()
    return [dict(row) for row in result]
```

There is no `_execute_query` helper. All new FTP read methods must follow the
`_get_connection()` pattern.

### `ftpseek` current structure

`ftpseek` (57 lines) takes `--config FILE` as an argument but never loads it —
the value is unused. `create_ftp_workflow(args)` and `workflow.run(args)` are
the only calls in `main()`. There is no config object, no DB path resolution.

---

## 2. Proposed Schema Design

### `ftp_servers` — one row per IP address (mirrors `smb_servers`)

```sql
CREATE TABLE IF NOT EXISTS ftp_servers (
    id              INTEGER  PRIMARY KEY AUTOINCREMENT,
    ip_address      TEXT     NOT NULL UNIQUE,
    country         TEXT,
    country_code    TEXT,
    port            INTEGER  NOT NULL DEFAULT 21,
    anon_accessible BOOLEAN  NOT NULL DEFAULT FALSE,
    banner          TEXT,
    shodan_data     TEXT,
    first_seen      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scan_count      INTEGER  DEFAULT 1,
    status          TEXT     DEFAULT 'active',
    notes           TEXT,
    updated_at      DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**Uniqueness model: one row per IP, port is informational.**

`ip_address UNIQUE` — not `UNIQUE(ip_address, port)`. Rationale: SMBSeek's
mental model is host-centric (same as `smb_servers`). If a host runs FTP on a
non-default port, the first confirmed port is stored and updated on re-scan.
This keeps JOIN and coexistence logic simple (join on `ip_address`, not on
`ip_address + port`). A future card can add a `ftp_ports` child table for
multi-port hosts without changing this schema.

The `port` column stores the port at which FTP was confirmed. The upsert
operation (see Patch 4) will update `port` if a re-scan finds the service on a
different port, along with `last_seen` and `scan_count`. `first_seen` is never
overwritten.

No FK to `smb_servers` — the two tables are completely independent. The same IP
can exist in both; the view layer handles coexistence.

### `ftp_access` — per-session access summary (parallel to `share_access`)

```sql
CREATE TABLE IF NOT EXISTS ftp_access (
    id                     INTEGER  PRIMARY KEY AUTOINCREMENT,
    server_id              INTEGER  NOT NULL,
    session_id             INTEGER,
    accessible             BOOLEAN  NOT NULL DEFAULT FALSE,
    auth_status            TEXT,
    root_listing_available BOOLEAN  DEFAULT FALSE,
    root_entry_count       INTEGER  DEFAULT 0,
    error_message          TEXT,
    test_timestamp         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    access_details         TEXT,
    created_at             DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id)  REFERENCES ftp_servers(id)    ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id)  ON DELETE SET NULL
);
```

- FK to `ftp_servers(id)` — NOT `smb_servers`. Cascade delete stays within the
  FTP sidecar.
- `session_id` references `scan_sessions(id) ON DELETE SET NULL` — preserves
  access records if a session is purged (same choice as `share_credentials`).
- `auth_status TEXT` vocabulary for Card 4: `'anon'`, `'auth'`, `'denied'`,
  `'error'`, `'timeout'`. Card 3 defines the column; Card 4 fills it.
- `root_listing_available / root_entry_count` — quick summary of whether
  `LIST /` succeeded. Full file manifests are Card 5 scope.

### Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_ftp_servers_ip       ON ftp_servers(ip_address);
CREATE INDEX IF NOT EXISTS idx_ftp_servers_country  ON ftp_servers(country);
CREATE INDEX IF NOT EXISTS idx_ftp_servers_last_seen ON ftp_servers(last_seen);
CREATE INDEX IF NOT EXISTS idx_ftp_access_server    ON ftp_access(server_id);
CREATE INDEX IF NOT EXISTS idx_ftp_access_session   ON ftp_access(session_id);
```

---

## 3. Protocol Presence View

### `v_host_protocols`

```sql
CREATE VIEW IF NOT EXISTS v_host_protocols AS
SELECT
    ip_address,
    MAX(has_smb) AS has_smb,
    MAX(has_ftp) AS has_ftp,
    CASE
        WHEN MAX(has_smb) = 1 AND MAX(has_ftp) = 1 THEN 'both'
        WHEN MAX(has_smb) = 1                       THEN 'smb_only'
        ELSE                                              'ftp_only'
    END AS protocol_presence
FROM (
    SELECT ip_address, 1 AS has_smb, 0 AS has_ftp FROM smb_servers
    UNION ALL
    SELECT ip_address, 0 AS has_smb, 1 AS has_ftp FROM ftp_servers
) combined
GROUP BY ip_address;
```

**`ELSE 'none'` removed** — unreachable with this UNION ALL design (every row
has at least one flag set to 1). Removing it avoids dead-code confusion.

**UNION ALL + GROUP BY** — standard workaround for SQLite's lack of FULL OUTER
JOIN. Both `idx_smb_servers_ip` and `idx_ftp_servers_ip` back the respective
arms. Acceptable performance at typical SMBSeek scale.

`CREATE VIEW IF NOT EXISTS` is idempotent — safe to re-run on every migration.

### How downstream code consumes it

**`gui/utils/database_access.py`** — using the actual `_get_connection()` pattern:

```python
def get_host_protocols(self, ip: str = None) -> List[Dict]:
    query = "SELECT ip_address, has_smb, has_ftp, protocol_presence FROM v_host_protocols"
    params: tuple = ()
    if ip:
        query += " WHERE ip_address = ?"
        params = (ip,)
    with self._get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

def get_dual_protocol_count(self) -> int:
    with self._get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM v_host_protocols WHERE protocol_presence = 'both'"
        ).fetchone()
        return row[0] if row else 0

def get_ftp_server_count(self) -> int:
    with self._get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM ftp_servers WHERE status = 'active'"
        ).fetchone()
        return row[0] if row else 0

def get_ftp_servers(self, country: str = None) -> List[Dict]:
    query = "SELECT * FROM ftp_servers WHERE status = 'active'"
    params: tuple = ()
    if country:
        query += " AND country_code = ?"
        params = (country,)
    with self._get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
```

All four methods follow the same `_get_connection()` + `conn.execute()` +
`dict(row)` pattern as `_query_dashboard_summary()` (line 382).

---

## 4. Automatic Migration Design

### FTP migration code location

All FTP DDL goes into `shared/db_migrations.run_migrations()`, appended after
the existing RCE column block (currently line 111), before `conn.commit()`.

`DatabaseManager._run_migrations()` (tools/db_manager.py line 141) is SMB-only
(`share_access.auth_status`). Do not touch it. FTP does not belong there.

### `db_schema.sql` strategy: add FTP to the DROP block

Add FTP tables to the `DROP TABLE IF EXISTS` block at the top of the file:

```sql
DROP TABLE IF EXISTS ftp_access;
DROP TABLE IF EXISTS ftp_servers;
```

**Rationale:** `initialize_database()` only runs on a fresh/empty DB, so these
DROP statements are harmless in the normal upgrade path. However, if the DB is
ever reset (e.g., via `db_manager.py` direct invocation or a future DB reset
UI), the schema file should produce a clean, consistent state — including
removing stale FTP objects. Not including FTP in the DROP block would leave
ghost tables after a deliberate reset, violating the schema file's role as
authoritative ground truth.

Append the `CREATE TABLE IF NOT EXISTS ftp_servers`, `CREATE TABLE IF NOT
EXISTS ftp_access`, indexes, and `v_host_protocols` view definitions after the
existing views (currently line 238). Use `IF NOT EXISTS` throughout for
consistency with the rest of the file.

### `ftpseek` DB path resolution

`ftpseek` currently has no config object. The fix is to load config using the
existing `shared.config.load_config()` function (same call `smbseek` makes)
and then call `run_migrations()` with the resolved DB path:

```python
# In ftpseek main(), after arg parsing and before workflow creation:
try:
    from shared.config import load_config
    from shared.db_migrations import run_migrations
    _cfg = load_config(args.config)   # args.config is None or a path string
    _db_path = _cfg.get_database_path()  # SMBSeekConfig method (config.py line 244)
    run_migrations(_db_path)
except Exception:
    pass  # Non-fatal; consistent with SMBSeekWorkflowDatabase.__init__ line 42
```

`load_config(path)` returns an `SMBSeekConfig` instance (`config.py` line 504).
`get_database_path()` (`config.py` line 244) resolves the path from the config
file, falling back to `"smbseek.db"`. Never use `.get("database_path", …)` —
`SMBSeekConfig.get()` takes section/key/default positional args, not a flat key.

### Why this coverage is sufficient

| Scenario | Migration fires? |
|---|---|
| `./smbseek --country US` | ✓ via `SMBSeekWorkflowDatabase.__init__` |
| `./xsmbseek` | ✓ via `DatabaseReader.__init__` |
| `./xsmbseek --mock` | ✓ via same `DatabaseReader.__init__` |
| Old SMB-only DB opened by GUI | ✓ same |
| Fresh install | ✓ schema.sql (DROP+CREATE) then run_migrations() (IF NOT EXISTS no-ops) |
| `./ftpseek --country US` | ✓ via new Patch 3 hook |

### Failure behavior

- `CREATE TABLE IF NOT EXISTS` and `CREATE VIEW IF NOT EXISTS` failures
  (permissions, disk full) propagate out of `run_migrations()`. The existing
  `try/finally` always closes the connection (line 114). The existing
  `except Exception: pass` wrapper in `SMBSeekWorkflowDatabase.__init__`
  (line 42) and the identical wrapper in the `ftpseek` patch make the CLI
  best-effort at the entry-point level. Do not add a new swallow inside
  `run_migrations()` itself.

---

## 5. Patch Sequence

### Patch 1 — `tools/db_schema.sql`

1. Add `DROP TABLE IF EXISTS ftp_access;` and `DROP TABLE IF EXISTS ftp_servers;`
   to the DROP block at the top (before the CREATE TABLE statements).
2. Append after the last existing CREATE VIEW (line 238):
   - `CREATE TABLE IF NOT EXISTS ftp_servers ( … )`
   - `CREATE TABLE IF NOT EXISTS ftp_access ( … )`
   - Five `CREATE INDEX IF NOT EXISTS` statements
   - `CREATE VIEW IF NOT EXISTS v_host_protocols AS …`

**Verification:** `sqlite3 :memory: < tools/db_schema.sql` completes without
errors; `.tables` shows `ftp_servers` and `ftp_access`; `.schema
v_host_protocols` shows the view.

### Patch 2 — `shared/db_migrations.py`

Append FTP block after line 111 (the last RCE column check), before
`conn.commit()`:

```python
# --- FTP sidecar tables ---
cur.execute("CREATE TABLE IF NOT EXISTS ftp_servers ( … )")
cur.execute("CREATE TABLE IF NOT EXISTS ftp_access ( … )")
cur.execute("CREATE INDEX IF NOT EXISTS idx_ftp_servers_ip ON ftp_servers(ip_address)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_ftp_servers_country ON ftp_servers(country)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_ftp_servers_last_seen ON ftp_servers(last_seen)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_ftp_access_server ON ftp_access(server_id)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_ftp_access_session ON ftp_access(session_id)")
cur.execute("CREATE VIEW IF NOT EXISTS v_host_protocols AS …")
```

Update the module docstring at the top to list the new additions.

**Verification:** Open an existing `smbseek.db`. Run `run_migrations(db_path)`
once — `ftp_servers`, `ftp_access`, `v_host_protocols` now exist. Run it again
— no errors. Run `PRAGMA integrity_check` — returns `ok`.

### Patch 3 — `ftpseek`

Add config loading + migration call inside `main()`, after `args = parser.parse_args()`
and before the workflow block:

```python
try:
    from shared.config import load_config
    from shared.db_migrations import run_migrations
    _cfg = load_config(args.config)
    _db_path = _cfg.get("database_path", "smbseek.db")
    run_migrations(_db_path)
except Exception:
    pass
```

**Verification:** Delete `smbseek.db`. Run `./ftpseek --country US`.
Confirm `sqlite3 smbseek.db ".tables"` includes `ftp_servers`.

### Patch 4 — `shared/database.py`

Add a new `FtpPersistence` class after `SMBSeekWorkflowDatabase`. It takes
`db_path: str` and connects directly (no `DatabaseManager` dependency — FTP
writes don't need SMB infrastructure):

```python
class FtpPersistence:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def upsert_ftp_server(self, ip: str, country: str, country_code: str,
                          port: int, anon_accessible: bool,
                          banner: str, shodan_data: str) -> int:
        """
        Insert or update ftp_servers row for ip.
        On conflict: updates all mutable fields including country, shodan_data.
        first_seen is never overwritten.
        Returns the authoritative row id (always via SELECT after upsert,
        because lastrowid is unreliable on the conflict-update path).
        """
        upsert_sql = """
            INSERT INTO ftp_servers
                (ip_address, country, country_code, port, anon_accessible,
                 banner, shodan_data, last_seen, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(ip_address) DO UPDATE SET
                last_seen       = CURRENT_TIMESTAMP,
                scan_count      = ftp_servers.scan_count + 1,
                port            = excluded.port,
                anon_accessible = excluded.anon_accessible,
                banner          = excluded.banner,
                country         = excluded.country,
                country_code    = excluded.country_code,
                shodan_data     = excluded.shodan_data,
                status          = 'active',
                updated_at      = CURRENT_TIMESTAMP
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(upsert_sql, (ip, country, country_code, port,
                                      anon_accessible, banner, shodan_data))
            conn.commit()
            row = conn.execute(
                "SELECT id FROM ftp_servers WHERE ip_address = ?", (ip,)
            ).fetchone()
            return row[0]

    def record_ftp_access(self, server_id: int, session_id: Optional[int],
                          accessible: bool, auth_status: str,
                          root_listing_available: bool, root_entry_count: int,
                          error_message: str, access_details: str) -> None:
        """Insert one ftp_access row. One row per session per server."""
        sql = """
            INSERT INTO ftp_access
                (server_id, session_id, accessible, auth_status,
                 root_listing_available, root_entry_count,
                 error_message, access_details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(sql, (server_id, session_id, accessible, auth_status,
                               root_listing_available, root_entry_count,
                               error_message, access_details))
            conn.commit()
```

**Verification:** Call `upsert_ftp_server` twice for `"1.2.3.4"`. Confirm
`scan_count = 2`, `first_seen` unchanged, `last_seen` updated, and the
returned `id` is identical both times (same row, not a new insert).
Also confirm `country` and `shodan_data` reflect the second call's values.

### Patch 5 — `gui/utils/database_access.py`

Add the four FTP read methods to `DatabaseReader` using the `_get_connection()`
pattern. Place them after the existing SMB query methods (after the last
`get_*` method in the file). All four are shown in §3 above with exact
signatures and bodies.

Each method should gracefully return an empty list/zero if `ftp_servers` or
`v_host_protocols` doesn't exist yet (e.g., wrap with try/except
`sqlite3.OperationalError` returning `[]` or `0`). This guards against the
edge case where `run_migrations()` was skipped (shouldn't happen with the hooks,
but belt-and-suspenders).

**Verification:** In `--mock` mode, manually `INSERT INTO ftp_servers VALUES
(NULL,'1.1.1.1',…)` and call `get_ftp_servers()` — confirms row returned.

---

## 6. Regression Plan

### Pre-patch baseline

```bash
xvfb-run -a python -m pytest gui/tests/ shared/tests/ -v
python tools/db_bootstrap_smoketest.py
sqlite3 smbseek.db "PRAGMA integrity_check;"
sqlite3 smbseek.db "SELECT COUNT(*) FROM smb_servers;"  # record count
```

### Post-patch regression checklist

| Check | Method | Pass if |
|---|---|---|
| Old SMB-only DB opens without intervention | Copy pre-patch DB, launch `./xsmbseek` | GUI loads; `ftp_servers` appears in `.tables` |
| Fresh install has FTP tables | Delete DB, run `./smbseek --country US` | `.tables` shows `ftp_servers`, `ftp_access` |
| Fresh install via `ftpseek` | Delete DB, run `./ftpseek --country US` | Same |
| SMB server count unchanged | `SELECT COUNT(*) FROM smb_servers` | Matches pre-patch |
| Existing pytest suite | `xvfb-run -a python -m pytest gui/tests/ shared/tests/ -v` | All previously passing tests still pass |
| SMB-only host in view | Insert `1.2.3.4` in `smb_servers` only | `v_host_protocols` returns `smb_only` |
| FTP-only host in view | Insert `5.6.7.8` in `ftp_servers` only | `v_host_protocols` returns `ftp_only` |
| Dual-protocol host | Insert `9.9.9.9` in both tables | `v_host_protocols` returns `both` |
| Migration idempotency | Call `run_migrations(db_path)` three times | No exceptions |
| upsert preserves first_seen | Call `upsert_ftp_server` twice, same IP | `first_seen` identical, `scan_count = 2` |
| DB integrity | `PRAGMA integrity_check` | Returns `ok` |
| Schema reset is clean | `DatabaseManager(path)` on empty file | All tables including FTP created |
| `--mock` GUI | `./xsmbseek --mock` | Loads without migration errors |

---

## 7. Risks, Edge Cases, and Mitigations

### Partial DDL if migration crashes mid-block

SQLite auto-commits each DDL statement (`CREATE TABLE`, `CREATE INDEX`) as its
own implicit transaction unless wrapped in an explicit `BEGIN`. The existing
`run_migrations()` code does not use explicit transactions for DDL. This is
consistent with existing behavior. Recovery: every statement uses `IF NOT
EXISTS`, so the next run of `run_migrations()` creates whatever was missed.
Accept as-is; do not change the broader transaction model in Card 3.

### Stale DB from before Card 3

FTP tables missing → `run_migrations()` via `CREATE TABLE IF NOT EXISTS` adds
them on next run. Covered by the hooks at both CLI and GUI entry points.

### View created before tables exist

In `run_migrations()`, always create `ftp_servers` and `ftp_access` tables
BEFORE `CREATE VIEW IF NOT EXISTS v_host_protocols`. SQLite parses view body at
query time, not create time, so the order matters logically. Enforce this order
in the patch.

### Naming consistency: `ftp_servers` (not `ftp_server`)

The exact name `ftp_servers` must appear consistently in:
- DROP statement in `db_schema.sql`
- CREATE TABLE in `db_schema.sql` and `db_migrations.py`
- UNION ALL arm of `v_host_protocols`
- All `ON ftp_servers(…)` index definitions
- All INSERT/SELECT in `shared/database.py`
- All SELECT in `gui/utils/database_access.py`

Post-implementation grep check: `grep -rn "ftp_server[^s_]" tools/ shared/ gui/`
should return nothing.

### `DatabaseManager._run_migrations()` alignment

`tools/db_manager.py:_run_migrations()` (line 141) only touches
`share_access.auth_status`. It must NOT be modified for Card 3. FTP lives in
`shared/db_migrations.run_migrations()` exclusively. Confirm no cross-wiring
during code review.

### Concurrent `run_migrations()` from two processes

Two processes opening the same DB simultaneously will serialize on SQLite's
write lock. `CREATE TABLE IF NOT EXISTS` is safe under this scenario — one
process wins, the other finds the table already exists. No additional locking
needed.

### `get_ftp_servers()` and view methods called before migration fires

Guard each new `DatabaseReader` method with try/except `sqlite3.OperationalError`
returning `[]` or `0`. This covers the unlikely edge case where
`run_migrations()` was skipped or rolled back. Do not suppress other exception
types.

---

## 8. Out-of-Scope Confirmation for Card 3

- FTP file browser or directory listing UI
- Value/ranking filters on FTP hosts
- `ftp_file_manifests` table (Card 5 domain)
- Real FTP network connection or auth logic (Card 4)
- Dashboard UI changes to display FTP counts or dual-protocol badges
- Wiring `commands/ftp/operation.py` or `shared/ftp_workflow.py` to call
  `FtpPersistence` (Card 4 does this)
- `failure_logs` integration for FTP failures (Card 4 decision)

---

## Critical Files

| File | Change |
|---|---|
| `tools/db_schema.sql` | Add FTP to DROP block; append FTP tables, indexes, view |
| `shared/db_migrations.py` | Append FTP table + index + view creation block in `run_migrations()` |
| `ftpseek` | Add `load_config` + `run_migrations` call in `main()` after arg parse |
| `shared/database.py` | Add `FtpPersistence` class |
| `gui/utils/database_access.py` | Add 4 FTP read methods using `_get_connection()` pattern |
