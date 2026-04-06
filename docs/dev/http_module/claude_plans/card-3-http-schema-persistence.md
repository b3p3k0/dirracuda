# Card 3: HTTP Schema + Persistence Layer — Implementation Plan (Rev 2)

**Context:** Cards 1 and 2 are complete. The HTTP scan dialog, CLI entry point (`httpseek`), and workflow skeleton (`shared/http_workflow.py`) all exist but nothing writes to the DB yet. Card 3 closes that gap: define the HTTP DB schema, add idempotent startup migration, add a write persistence class, and extend the unified read layer to include HTTP rows with host_type='H'. Card 4 will replace operation.py stubs with real verification; Card 3 only needs to make the persistence layer ready to receive that data.

---

## 1. Pre-Revision Reality-Check Commands

```bash
# Confirm active repo + branch
cd /home/kevin/DEV/smbseek-smb && git log -n 5 --oneline
git status --short

# Baseline test pass rate
source venv/bin/activate
xvfb-run -a python -m pytest gui/tests/ shared/tests/ -q --tb=no 2>&1 | tail -5

# Confirm http_* tables do NOT yet exist
sqlite3 smbseek.db ".tables" | tr ' ' '\n' | sort | grep "^http" || echo "(none — expected)"

# Confirm migration entry point (inline FTP block — no _ensure_ftp_tables helper)
grep -n "FTP sidecar\|HTTP sidecar\|run_migrations\|_ensure_ftp\|_ensure_http" shared/db_migrations.py

# Confirm write-helper guard conditions
grep -n "host_type not in\|no such table: ftp_\|no such table: http_" gui/utils/database_access.py

# Confirm fallback chain
grep -n "_query_protocol_server_list\|no such table: ftp_" gui/utils/database_access.py | head -20
```

---

## 2. File / Function Touch List

| File | Change | Rationale |
|------|--------|-----------|
| `tools/db_schema.sql` | Add 4 HTTP tables + rebuild `v_host_protocols` view | Canonical schema record |
| `shared/db_migrations.py` | Add inline HTTP sidecar block after FTP block; update module docstring | Idempotent startup migration; mirrors existing FTP inline pattern exactly |
| `shared/database.py` | Add `HttpPersistence` class parallel to `FtpPersistence` | Write API for Card 4 ops |
| `gui/utils/database_access.py` | (a) Extend 5 host-type routing methods for 'H'; (b) rename `_query_protocol_server_list` → `_query_protocol_server_list_smb_ftp`, add new `_query_protocol_server_list_smb_ftp_http`; (c) add `_build_http_arm()`; (d) update fallback chain in `get_protocol_server_list()` to 3 tiers; (e) extend `_get_protocol_recent_cutoff()`; (f) fix `get_dual_protocol_count()` | Unified read layer parity |

**Not touched:** `commands/http/operation.py`, `shared/http_workflow.py`, any SMB/FTP paths, any GUI component files, any test files.

---

## 3. HTTP Schema — Corrected Table Definitions

Column names and table structure must match what the existing write helpers (`upsert_user_flags_for_host`, `upsert_probe_cache_for_host`, `upsert_extracted_flag_for_host`, `upsert_rce_status_for_host`) expect. FTP tables are the authoritative reference.

### `http_servers`
```sql
CREATE TABLE IF NOT EXISTS http_servers (
    id           INTEGER  PRIMARY KEY AUTOINCREMENT,
    ip_address   TEXT     NOT NULL UNIQUE,
    host_type    TEXT     DEFAULT 'H',
    country      TEXT,
    country_code TEXT,
    port         INTEGER  NOT NULL DEFAULT 80,
    scheme       TEXT     DEFAULT 'http',   -- 'http' | 'https'
    banner       TEXT,                       -- Server: header / Shodan banner
    title        TEXT,                       -- <title> or Shodan title
    shodan_data  TEXT,                       -- raw Shodan hit JSON
    first_seen   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scan_count   INTEGER  DEFAULT 1,         -- matches FTP default
    status       TEXT     DEFAULT 'active',
    notes        TEXT,
    updated_at   DATETIME,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_http_servers_ip      ON http_servers(ip_address);
CREATE INDEX IF NOT EXISTS idx_http_servers_country ON http_servers(country);
CREATE INDEX IF NOT EXISTS idx_http_servers_seen    ON http_servers(last_seen);
```

### `http_access`
`session_id` is `INTEGER` FK (not TEXT) to match FTP pattern.
```sql
CREATE TABLE IF NOT EXISTS http_access (
    id             INTEGER  PRIMARY KEY AUTOINCREMENT,
    server_id      INTEGER  NOT NULL,
    session_id     INTEGER,                  -- FK to scan_sessions(id), nullable
    accessible     BOOLEAN  NOT NULL DEFAULT FALSE,
    status_code    INTEGER,                  -- HTTP response code
    is_index_page  BOOLEAN  DEFAULT FALSE,   -- confirmed open directory index
    dir_count      INTEGER  DEFAULT 0,
    file_count     INTEGER  DEFAULT 0,
    tls_verified   BOOLEAN  DEFAULT FALSE,
    error_message  TEXT,
    access_details TEXT,                     -- JSON blob (raw listing entries)
    test_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id)  REFERENCES http_servers(id)   ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id)  ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_http_access_server  ON http_access(server_id);
CREATE INDEX IF NOT EXISTS idx_http_access_session ON http_access(session_id);
```

### `http_user_flags`
**Critical:** Column names must be `favorite`/`avoid`/`notes` (not `is_favorite`/`is_avoid`). No separate `id` column. No `ip_address` column. `server_id INTEGER PRIMARY KEY` is the conflict target. Matches FTP exactly.
```sql
CREATE TABLE IF NOT EXISTS http_user_flags (
    server_id  INTEGER PRIMARY KEY,
    favorite   BOOLEAN  DEFAULT 0,
    avoid      BOOLEAN  DEFAULT 0,
    notes      TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES http_servers(id) ON DELETE CASCADE
);
```

### `http_probe_cache`
**Critical:** Column names must be `status`/`last_probe_at`/`indicator_matches`/`snapshot_path` (not `probe_status`/`probed_at`). No separate `id` column. No `ip_address` column. `server_id INTEGER PRIMARY KEY` is the conflict target. Extends FTP pattern with `accessible_files_count` (HTTP-specific).
```sql
CREATE TABLE IF NOT EXISTS http_probe_cache (
    server_id              INTEGER PRIMARY KEY,
    status                 TEXT     DEFAULT 'unprobed',
    last_probe_at          DATETIME,
    indicator_matches      INTEGER  DEFAULT 0,
    indicator_samples      TEXT,
    snapshot_path          TEXT,
    accessible_dirs_count  INTEGER  DEFAULT 0,
    accessible_dirs_list   TEXT,
    accessible_files_count INTEGER  DEFAULT 0,  -- HTTP-specific: files found in index
    extracted              INTEGER  DEFAULT 0,
    rce_status             TEXT     DEFAULT 'not_run',
    rce_verdict_summary    TEXT,
    updated_at             DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES http_servers(id) ON DELETE CASCADE
);
```

**Design note on `accessible_files_count`:** HTTP open-directory indexes expose both dirs and files at index level, unlike FTP which only tracks root-level entries. Adding this column now (defaulting to 0) avoids a future schema migration in Card 4/5. The "Shares > 0" filter uses `accessible_dirs_count + accessible_files_count` as the combined accessible items signal.

---

## 4. Unified List Mapping Plan

### HTTP UNION ALL arm — exact 23-column match required

The HTTP SELECT arm must produce exactly the same columns in the same order as the SMB and FTP arms. Current column order from `_build_union_sql()`:
`host_type, protocol_server_id, row_key, ip_address, country, country_code, last_seen, scan_count, status, auth_method, total_shares, accessible_shares, accessible_shares_list, port, banner, anon_accessible, favorite, avoid, notes, probe_status, indicator_matches, extracted, rce_status`

| Unified column | HTTP source | Notes |
|---|---|---|
| `host_type` | `'H'` literal | |
| `protocol_server_id` | `hs.id` | |
| `row_key` | `'H:' \|\| CAST(hs.id AS TEXT)` | Collision-safe identity |
| `ip_address` | `hs.ip_address` | |
| `country` | `hs.country` | |
| `country_code` | `hs.country_code` | |
| `last_seen` | `hs.last_seen` | |
| `scan_count` | `hs.scan_count` | |
| `status` | `hs.status` | |
| `auth_method` | `'http'` literal | No SMB-style auth |
| `total_shares` | `COALESCE(hpc.accessible_dirs_count,0) + COALESCE(hpc.accessible_files_count,0)` | Combined accessible items |
| `accessible_shares` | same as `total_shares` | HTTP has no inaccessible-share concept |
| `accessible_shares_list` | `COALESCE(hpc.accessible_dirs_list,'')` | Dir paths |
| `port` | `hs.port` | |
| `banner` | `hs.banner` | |
| `anon_accessible` | `0` literal | N/A for HTTP |
| `favorite` | `COALESCE(huf.favorite,0)` | |
| `avoid` | `COALESCE(huf.avoid,0)` | |
| `notes` | `COALESCE(huf.notes,'')` | |
| `probe_status` | `COALESCE(hpc.status,'unprobed')` | Note: column is `status`, alias is `probe_status` |
| `indicator_matches` | `COALESCE(hpc.indicator_matches,0)` | |
| `extracted` | `COALESCE(hpc.extracted,0)` | |
| `rce_status` | `COALESCE(hpc.rce_status,'not_run')` | |

**"Shares > 0" filter:** `accessible_shares = dirs + files`. HTTP server with 0 accessible items persists in DB, is hidden by filter. HTTP server with any accessible content is shown. Both requirements satisfied.

---

## 5. Migration Safety Strategy

### Structure in `shared/db_migrations.py`

FTP migration is **inline** in `run_migrations()` (no `_ensure_ftp_tables()` helper exists — the plan previously referenced a nonexistent function). HTTP block follows the same inline pattern, placed after the FTP block.

```python
# --- HTTP sidecar tables (additive; SMB and FTP schemas untouched) ---
cur.execute("CREATE TABLE IF NOT EXISTS http_servers (...)")
cur.execute("CREATE TABLE IF NOT EXISTS http_access (...)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_http_servers_ip ON http_servers(ip_address)")
# ... remaining indexes ...

# Migration: explicit protocol identity on HTTP rows
cur.execute("PRAGMA table_info(http_servers)")
http_cols = [row[1] for row in cur.fetchall()]
if "host_type" not in http_cols:
    cur.execute("ALTER TABLE http_servers ADD COLUMN host_type TEXT DEFAULT 'H'")
cur.execute("UPDATE http_servers SET host_type='H' WHERE host_type IS NULL OR TRIM(host_type)=''")

cur.execute("CREATE TABLE IF NOT EXISTS http_user_flags (...)")
cur.execute("CREATE TABLE IF NOT EXISTS http_probe_cache (...)")

# Idempotent column backfill for http_probe_cache
cur.execute("PRAGMA table_info(http_probe_cache)")
http_pc_cols = [row[1] for row in cur.fetchall()]
if "accessible_files_count" not in http_pc_cols:
    cur.execute("ALTER TABLE http_probe_cache ADD COLUMN accessible_files_count INTEGER DEFAULT 0")
# ... additional backfill guards for future columns ...
```

**Timestamp normalization:** `_normalize_existing_timestamps()` currently iterates `("smb_servers", "ftp_servers")`. Extend to `("smb_servers", "ftp_servers", "http_servers")` so any T-format timestamps from Shodan data in HTTP rows are normalized before `_get_protocol_recent_cutoff()` reads them. The function already catches `"no such table"` per-table, so adding `http_servers` is safe on any DB.

### v_host_protocols view rebuild

Drop-and-recreate is the correct pattern for views (no `IF NOT EXISTS` for column additions). The new view adds `has_http` and extends `protocol_presence`. **Preserves existing 'both', 'smb_only', 'ftp_only' values exactly** so `get_dual_protocol_count()` and any external code are not broken.

```sql
DROP VIEW IF EXISTS v_host_protocols;
CREATE VIEW v_host_protocols AS
SELECT
    ip_address,
    MAX(has_smb)  AS has_smb,
    MAX(has_ftp)  AS has_ftp,
    MAX(has_http) AS has_http,
    CASE
        WHEN MAX(has_smb)=1 AND MAX(has_ftp)=1 AND MAX(has_http)=1 THEN 'smb+ftp+http'
        WHEN MAX(has_smb)=1 AND MAX(has_ftp)=1                      THEN 'both'
        WHEN MAX(has_smb)=1                     AND MAX(has_http)=1 THEN 'smb+http'
        WHEN                    MAX(has_ftp)=1  AND MAX(has_http)=1 THEN 'ftp+http'
        WHEN MAX(has_smb)=1                                          THEN 'smb_only'
        WHEN                    MAX(has_ftp)=1                       THEN 'ftp_only'
        ELSE                                                               'http_only'
    END AS protocol_presence
FROM (
    SELECT ip_address, 1 AS has_smb, 0 AS has_ftp, 0 AS has_http FROM smb_servers
    UNION ALL
    SELECT ip_address, 0 AS has_smb, 1 AS has_ftp, 0 AS has_http FROM ftp_servers
    UNION ALL
    SELECT ip_address, 0 AS has_smb, 0 AS has_ftp, 1 AS has_http FROM http_servers
) combined
GROUP BY ip_address;
```
**Why explicit aliases:** SQLite rejects the `combined(col1, col2, ...)` subquery column-list syntax (near `(`: syntax error). Must use `AS` aliases in each SELECT arm instead — matches the working pattern at `db_migrations.py:314-316`.

### Legacy DB smoke test expectations

- SMB-only DB → creates FTP + HTTP tables + new view, no crash
- SMB + FTP DB → creates HTTP tables only, rebuilds view, FTP untouched
- Full DB → all IF NOT EXISTS no-ops, view rebuilt (harmless)
- Pre-migration DB opened with new code before `run_migrations()` fires → 3-tier fallback protects unified list

---

## 6. `database_access.py` Change Details

### 6a. 3-tier fallback in `get_protocol_server_list()`

**Current structure (2 tiers):** Full S+F query → SMB-only fallback on `ftp_` error.

**New structure (3 tiers):**

**Edge case noted (not blocking):** If `http_` tables exist but `ftp_` tables are absent (atypical — requires manual table drops), the tier-2 S+F path will itself error on `ftp_`, falling to tier-3 S-only, causing HTTP rows to disappear. This is an acceptable limitation of the normal upgrade path where tables are only ever additive. It is not worth a fix in Card 3.
```python
try:
    return self._query_protocol_server_list_smb_ftp_http(...)
except sqlite3.OperationalError as exc:
    msg = str(exc).lower()
    if "no such table: http_" in msg:
        try:
            return self._query_protocol_server_list_smb_ftp(...)  # renamed from current method
        except sqlite3.OperationalError as exc2:
            if "no such table: ftp_" in str(exc2).lower():
                return self._query_protocol_server_list_smb_only(...)
            raise
    elif "no such table: ftp_" in msg:
        return self._query_protocol_server_list_smb_only(...)
    raise
```

**Rename:** `_query_protocol_server_list` → `_query_protocol_server_list_smb_ftp` (S+F only, used as tier-2 fallback)

**New method:** `_query_protocol_server_list_smb_ftp_http` (S+F+H, primary path)

**New helper:** `_build_http_arm(http_where: str) -> str` — returns the HTTP SELECT arm. Full 3-protocol UNION is `_build_union_sql(smb_where, ftp_where) + "\nUNION ALL\n" + _build_http_arm(http_where)`. `_build_union_sql` is **unchanged**.

### 6b. `_get_protocol_recent_cutoff()` — add HTTP

Extend inner UNION to include HTTP:
```sql
SELECT MAX(datetime(last_seen)) AS ts FROM http_servers WHERE status = 'active'
```
Update exception handler to also catch `"no such table: http_"` (fall back to SMB+FTP only, then to SMB-only).

### 6c. `get_dual_protocol_count()` — fix metric

Change from `protocol_presence = 'both'` (fragile) to column-based check (robust):
```python
"SELECT COUNT(*) FROM v_host_protocols WHERE has_smb = 1 AND has_ftp = 1"
```

### 6d. Host-type routing methods — add 'H'

Five methods need `'H'` added to their allowed-types guard and a new routing branch:

| Method | Guard to update | New 'H' routing |
|--------|----------------|-----------------|
| `upsert_user_flags_for_host` | `not in ('S','F')` → `not in ('S','F','H')` | server_table=`http_servers`, flags_table=`http_user_flags` |
| `upsert_probe_cache_for_host` | same | server_table=`http_servers`, cache_table=`http_probe_cache`; new branch with `accessible_files_count` column |
| `upsert_extracted_flag_for_host` | same | cache_table=`http_probe_cache` |
| `upsert_rce_status_for_host` | same | cache_table=`http_probe_cache` |
| `get_rce_status_for_host` | `(host_type or "S").upper()` (currently falls through to FTP only) | add explicit 'H' branch querying `http_probe_cache JOIN http_servers` |

For all methods: graceful degradation on `OperationalError` extended to catch `"no such table: http_"` alongside `"no such table: ftp_"`.

**`upsert_probe_cache_for_host` signature change:** Add `accessible_files_count: Optional[int] = None` parameter. Used only in the 'H' branch. Existing callers ('S' and 'F') are unaffected (default None).

### 6e. `bulk_delete_rows()` — add 'H' delete block

```python
http_ips = list({ip for ht, ip in row_specs if ht == "H" and ip})
# --- HTTP delete ---
for i in range(0, len(http_ips), batch_size):
    batch = http_ips[i:i + batch_size]
    try:
        # ... same pattern as FTP delete block ...
        # http_user_flags and http_probe_cache CASCADE from http_servers
        cur.execute(f"DELETE FROM http_servers WHERE ip_address IN ({fp})", found_http)
        ...
    except sqlite3.OperationalError as exc:
        if "no such table: http_servers" in str(exc).lower():
            error_parts.append("HTTP tables not yet migrated; HTTP rows not deleted.")
        else:
            error_parts.append(f"HTTP delete error: {exc}")
```

Note: `deleted_smb_ips` tracking is SMB-specific (used by caller for file-based probe cache cleanup); HTTP and FTP do not need it.

---

## 7. `shared/database.py` — `HttpPersistence` Class

Parallel to `FtpPersistence`. Minimal for Card 3 (Card 4 will flesh out the batch methods):

```python
class HttpPersistence:
    """Decoupled write operations for HTTP sidecar tables."""

    def __init__(self, db_path: str) -> None: ...

    def upsert_http_server(self, ip, country, country_code, port, scheme,
                           banner, title, shodan_data) -> int:
        """INSERT or UPDATE http_servers. Returns server_id."""
        # ON CONFLICT(ip_address) DO UPDATE: last_seen, scan_count+1, port, scheme,
        # banner, title, country, country_code, shodan_data, status, updated_at

    def record_http_access(self, server_id, session_id, accessible, status_code,
                           is_index_page, dir_count, file_count, tls_verified,
                           error_message, access_details) -> None:
        """INSERT single http_access row."""

    def persist_discovery_outcomes_batch(self, outcomes) -> None:
        """Stub — Card 4 will implement. Batch persist stage-1 port-failed hosts."""

    def persist_access_outcomes_batch(self, outcomes) -> None:
        """Stub — Card 4 will implement. Batch persist stage-2 results + probe_cache sync."""
```

---

## 8. Regression Risk List and Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| UNION ALL column count/order mismatch between arms | High | Explicitly count all 23 columns in HTTP arm before commit; run validation query A5 |
| `upsert_*_for_host` ValueError for 'H' (guard still blocks 'H') | High (certain w/o fix) | Add 'H' to all 5 guard sets |
| FTP rows disappear on pre-HTTP DB (2-tier fallback replaced by wrong 3-tier) | High (certain w/o fix) | 3-tier: http_ error → try S+F → ftp_ error → try S-only |
| `_build_union_sql` changes break existing S+F fallback path | Medium | `_build_union_sql` is NOT changed; new `_build_http_arm` is additive |
| `v_host_protocols` view 'both' value preserved | Low | CASE explicitly maps SMB=1+FTP=1+HTTP=0 → 'both' |
| `get_dual_protocol_count` breaks if 'both' value was preserved | None | Changing to `has_smb=1 AND has_ftp=1` is more robust anyway |
| `_get_protocol_recent_cutoff` crashes on missing http_ table | Medium | Extend catch to include `"no such table: http_"` |
| `accessible_files_count` breaks `upsert_probe_cache_for_host` for S/F | None | New param defaults to None; S/F branches never reference it |
| `scan_count DEFAULT 1` vs old plan's `DEFAULT 0` | None | Matches FTP pattern; first insert records 1 scan correctly |
| `session_id INTEGER` vs old plan's `session_id TEXT` | None | Matches FTP pattern; no semantic difference for null values |

---

## 9. Validation Checklist

### Automated

```bash
# A1 — Baseline tests still pass
xvfb-run -a python -m pytest gui/tests/ shared/tests/ -q --tb=short 2>&1 | tail -10
# AUTOMATED: [ ] PASS  [ ] FAIL

# A2 — HTTP tables created in fresh DB
python -c "
from shared.db_migrations import run_migrations
import sqlite3, tempfile, os
with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f: p = f.name
run_migrations(p)
tables = [r[0] for r in sqlite3.connect(p).execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\").fetchall()]
print(tables)
assert 'http_servers' in tables
assert 'http_access' in tables
assert 'http_user_flags' in tables
assert 'http_probe_cache' in tables
print('OK')
os.unlink(p)
"
# AUTOMATED: [ ] PASS  [ ] FAIL

# A3 — Migration idempotent (run twice, no error)
python -c "
from shared.db_migrations import run_migrations
import tempfile, os
with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f: p = f.name
run_migrations(p); run_migrations(p); print('idempotent OK')
os.unlink(p)
"
# AUTOMATED: [ ] PASS  [ ] FAIL

# A4 — Legacy SMB-only DB migrates without crash
cp smbseek.db /tmp/smbseek_legacy_test.db
sqlite3 /tmp/smbseek_legacy_test.db "DROP TABLE IF EXISTS ftp_servers; DROP TABLE IF EXISTS ftp_access; DROP TABLE IF EXISTS ftp_user_flags; DROP TABLE IF EXISTS ftp_probe_cache; DROP VIEW IF EXISTS v_host_protocols;"
python -c "
from shared.db_migrations import run_migrations
import sqlite3
run_migrations('/tmp/smbseek_legacy_test.db')
tables = [r[0] for r in sqlite3.connect('/tmp/smbseek_legacy_test.db').execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]
assert 'http_servers' in tables and 'ftp_servers' in tables
print('legacy migration OK:', sorted(tables))
"
# AUTOMATED: [ ] PASS  [ ] FAIL

# A5 — v_host_protocols: 3-protocol UNION works; 'both' preserved
python -c "
import sqlite3, tempfile, os
from shared.db_migrations import run_migrations
with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f: p = f.name
run_migrations(p)
conn = sqlite3.connect(p)
conn.execute(\"INSERT INTO smb_servers(ip_address,host_type,country,country_code,auth_method) VALUES ('1.2.3.4','S','US','US','anonymous')\")
conn.execute(\"INSERT INTO ftp_servers(ip_address,host_type) VALUES ('1.2.3.4','F')\")
conn.execute(\"INSERT INTO http_servers(ip_address,host_type) VALUES ('1.2.3.5','H')\")
conn.commit()
rows = {r[0]:r for r in conn.execute('SELECT ip_address,has_smb,has_ftp,has_http,protocol_presence FROM v_host_protocols ORDER BY ip_address').fetchall()}
assert rows['1.2.3.4'][4] == 'both', f'expected both, got {rows[\"1.2.3.4\"][4]}'
assert rows['1.2.3.5'][4] == 'http_only', f'expected http_only, got {rows[\"1.2.3.5\"][4]}'
print('view OK:', rows)
os.unlink(p)
"
# AUTOMATED: [ ] PASS  [ ] FAIL

# A6 — HttpPersistence round-trip (upsert idempotent)
python -c "
import tempfile, os
from shared.db_migrations import run_migrations
from shared.database import HttpPersistence
with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f: p = f.name
run_migrations(p)
hp = HttpPersistence(p)
sid = hp.upsert_http_server('10.0.0.1','US','US',80,'http',None,None,None)
sid2 = hp.upsert_http_server('10.0.0.1','US','US',80,'http',None,None,None)
assert sid == sid2, f'expected same id: {sid} vs {sid2}'
print('HttpPersistence upsert OK, server_id =', sid)
os.unlink(p)
"
# AUTOMATED: [ ] PASS  [ ] FAIL

# A7 — get_protocol_server_list() returns H rows
python -c "
import tempfile, os
from shared.db_migrations import run_migrations
from shared.database import HttpPersistence
from gui.utils.database_access import DatabaseReader
with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f: p = f.name
run_migrations(p)
HttpPersistence(p).upsert_http_server('10.0.0.2','US','US',80,'http',None,None,None)
rows, total = DatabaseReader(p).get_protocol_server_list(limit=10)
htypes = [r['host_type'] for r in rows]
assert 'H' in htypes, f'HTTP row missing: {htypes}'
print('unified list OK, host_types:', htypes, 'total:', total)
os.unlink(p)
"
# AUTOMATED: [ ] PASS  [ ] FAIL

# A8 — Host-type 'H' routing: no ValueError, no crash
python -c "
import tempfile, os
from shared.db_migrations import run_migrations
from shared.database import HttpPersistence
from gui.utils.database_access import DatabaseReader
with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f: p = f.name
run_migrations(p)
HttpPersistence(p).upsert_http_server('10.0.0.3','US','US',80,'http',None,None,None)
dr = DatabaseReader(p)
dr.upsert_user_flags_for_host('10.0.0.3', 'H', favorite=True)
dr.upsert_probe_cache_for_host('10.0.0.3', 'H', status='probed', indicator_matches=0)
dr.upsert_extracted_flag_for_host('10.0.0.3', 'H', extracted=True)
dr.upsert_rce_status_for_host('10.0.0.3', 'H', 'not_run')
print('all H routing OK')
os.unlink(p)
"
# AUTOMATED: [ ] PASS  [ ] FAIL

# A9 — Pre-HTTP DB (S+F only): FTP rows still visible (3-tier fallback)
python -c "
import tempfile, os, sqlite3
from shared.db_migrations import run_migrations
from shared.database import FtpPersistence
from gui.utils.database_access import DatabaseReader
with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f: p = f.name
run_migrations(p)
FtpPersistence(p).upsert_ftp_server('10.0.0.4', 'US', 'US', 21, True, None, None)
# Simulate pre-HTTP state by dropping http tables
conn = sqlite3.connect(p)
conn.execute('DROP TABLE IF EXISTS http_probe_cache')
conn.execute('DROP TABLE IF EXISTS http_user_flags')
conn.execute('DROP TABLE IF EXISTS http_access')
conn.execute('DROP TABLE IF EXISTS http_servers')
conn.commit(); conn.close()
rows, total = DatabaseReader(p).get_protocol_server_list(limit=10)
htypes = [r['host_type'] for r in rows]
assert 'F' in htypes, f'FTP row missing in S+F fallback: {htypes}'
assert 'H' not in htypes, f'H row should not appear: {htypes}'
print('3-tier fallback OK:', htypes)
os.unlink(p)
"
# AUTOMATED: [ ] PASS  [ ] FAIL
```

### Manual (Gate B — requires human)

```
M1 — xsmbseek (real DB): dashboard opens, no crash, SMB+FTP server list loads
     MANUAL: [ ] PASS  [ ] FAIL  [ ] PENDING

M2 — sqlite3 smbseek.db ".tables" confirms all 4 http_* tables after startup
     MANUAL: [ ] PASS  [ ] FAIL  [ ] PENDING

M3 — Server list SMB and FTP rows unchanged (spot-check 2-3 known rows)
     MANUAL: [ ] PASS  [ ] FAIL  [ ] PENDING

M4 — xsmbseek --mock: mock list loads without crash
     MANUAL: [ ] PASS  [ ] FAIL  [ ] PENDING

M5 — Start SMB scan from dashboard: lifecycle completes normally
     MANUAL: [ ] PASS  [ ] FAIL  [ ] PENDING

M6 — Start FTP scan from dashboard: lifecycle completes normally
     MANUAL: [ ] PASS  [ ] FAIL  [ ] PENDING

M7 — Start HTTP scan: skeleton workflow runs, no "no such table" error in output
     MANUAL: [ ] PASS  [ ] FAIL  [ ] PENDING

M8 — xsmbseek with pre-card-3 DB snapshot (if available): startup migrates silently
     MANUAL: [ ] PASS  [ ] FAIL  [ ] PENDING
```

**Overall gate:**
```
AUTOMATED: [ ] PASS  [ ] FAIL
MANUAL:    [ ] PASS  [ ] FAIL  [ ] PENDING
OVERALL:   [ ] PASS  [ ] FAIL  [ ] PENDING
```

---

## 10. Out-of-Scope Confirmation

**Card 4:** Real Shodan query, real HTTP(S) verification, index parser, populating `http_access` with real `dir_count`/`file_count`, backfilling `http_probe_cache` from `http_access`, categorized failure reason codes.

**Card 5:** HTTP browser window, `shared/http_browser.py`, `gui/utils/http_probe_runner.py` / `http_probe_cache.py`, populating `snapshot_path`/`probed_at`.

**Card 6:** Formal test files, README/user guide updates.

---

## 11. Implementation Prompt (Copy-Paste After HI Approval)

```text
Implement Card 3 from docs/dev/http_module/HTTP_PHASE_TASK_CARDS.md using the approved plan at /home/kevin/.claude/plans/atomic-mixing-trinket.md.

Exact deliverables:

1. tools/db_schema.sql
   - Add http_servers, http_access, http_user_flags, http_probe_cache (see plan §3 for exact DDL)
   - Replace v_host_protocols view with 3-protocol version (see plan §5)

2. shared/db_migrations.py
   - Add inline HTTP sidecar block after the FTP block (NOT a new function — match the existing
     inline structure starting at line 133)
   - Column names: favorite/avoid/notes (not is_favorite/is_avoid); server_id PRIMARY KEY (no id);
     status/last_probe_at (not probe_status/probed_at); session_id INTEGER FK
   - scan_count DEFAULT 1 (matches FTP)
   - Rebuild v_host_protocols view (DROP VIEW + CREATE VIEW) with has_http column
   - Update module docstring to list http_* tables
   - idempotent column backfill guard for accessible_files_count
   - Extend _normalize_existing_timestamps() to include "http_servers" in the table loop
   - v_host_protocols view SQL must use explicit AS aliases in each SELECT arm (not the
     column-list-on-alias syntax which SQLite rejects): each arm must be
     SELECT ip_address, N AS has_smb, N AS has_ftp, N AS has_http FROM <table>

3. shared/database.py
   - Add HttpPersistence class parallel to FtpPersistence (see plan §7)
   - upsert_http_server() returns server_id; scan_count increments on conflict
   - record_http_access() inserts one row
   - persist_discovery_outcomes_batch() and persist_access_outcomes_batch() are stubs for Card 4

4. gui/utils/database_access.py
   - Add _build_http_arm(http_where) method — 23 columns in exact same order as existing arms
   - Add _query_protocol_server_list_smb_ftp_http() — primary 3-protocol query using
     _build_union_sql(smb_where, ftp_where) + UNION ALL + _build_http_arm(http_where)
   - Rename _query_protocol_server_list → _query_protocol_server_list_smb_ftp (S+F fallback)
   - Update get_protocol_server_list() with 3-tier fallback (see plan §6a)
   - Update _get_protocol_recent_cutoff() to include http_servers UNION arm (see plan §6b)
   - Fix get_dual_protocol_count() to use has_smb=1 AND has_ftp=1 (see plan §6c)
   - Extend upsert_user_flags_for_host, upsert_probe_cache_for_host,
     upsert_extracted_flag_for_host, upsert_rce_status_for_host, get_rce_status_for_host
     to accept 'H' host_type (see plan §6d)
   - Add accessible_files_count: Optional[int] = None to upsert_probe_cache_for_host signature
   - Add HTTP delete block to bulk_delete_rows() (see plan §6e)

Non-negotiables:
- _build_union_sql() is NOT changed (S+F fallback must remain stable)
- v_host_protocols 'both' value is preserved for SMB+FTP rows
- All http_user_flags/http_probe_cache columns match exact names the helpers use
- 3-tier fallback: http_ error → S+F path, ftp_ error from that → S-only path
- Graceful degradation on "no such table: http_" in all 5 routing methods

After implementation, run automated checks A1–A9 from plan §9 and report exact pass/fail.
```
