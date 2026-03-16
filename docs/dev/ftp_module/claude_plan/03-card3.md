# Card 3: FTP Schema and Persistence Layer

## Status: Implemented

---

## Context

Cards 1 and 2 delivered a wired FTP scan path (dashboard button → `scan_manager`
→ `ftpseek` CLI → `FtpWorkflow`) that streams progress output. FTP scans ran
end-to-end but wrote nothing to disk. Card 3 adds the persistence layer:

- Two new sidecar tables (`ftp_servers`, `ftp_access`)
- Idempotent migrations that fire automatically in every entry point
- A protocol-coexistence view (`v_host_protocols`)
- Write methods (`FtpPersistence`) for Card 4 to call
- Read methods in `DatabaseReader` for Card 5 GUI use

No manual migration steps. No crashes on older DBs.

---

## Files Changed

| File | Change |
|---|---|
| `tools/db_schema.sql` | Added FTP tables to DROP block; appended FTP tables, indexes, `v_host_protocols` view |
| `shared/db_migrations.py` | Appended FTP table + index + view creation block in `run_migrations()` |
| `ftpseek` | Added `load_config` + `run_migrations` call in `main()` after arg parse |
| `shared/database.py` | Added `FtpPersistence` class |
| `gui/utils/database_access.py` | Added 4 FTP read methods using `_get_connection()` pattern |

---

## Schema

### `ftp_servers` — one row per IP (host-centric, mirrors `smb_servers`)

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

**Uniqueness: `ip_address UNIQUE`.** Port is informational — stores the port at
which FTP was confirmed, updated on re-scan. No FK to `smb_servers`; the same
IP can exist in both tables independently.

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
    FOREIGN KEY (server_id)  REFERENCES ftp_servers(id)   ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE SET NULL
);
```

`auth_status` vocabulary for Card 4: `'anon'`, `'auth'`, `'denied'`,
`'error'`, `'timeout'`.

### `v_host_protocols` — protocol coexistence view

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

UNION ALL + GROUP BY works around SQLite's lack of FULL OUTER JOIN.
`idx_smb_servers_ip` and `idx_ftp_servers_ip` back the respective arms.

---

## Migration Strategy

### Two independent migration paths — only one modified

| Path | File | Scope |
|---|---|---|
| `DatabaseManager._run_migrations()` line 141 | `tools/db_manager.py` | SMB-only (`share_access.auth_status`). **Not touched.** |
| `run_migrations()` | `shared/db_migrations.py` | GUI tables + FTP sidecar. **FTP added here.** |

FTP DDL is appended to `shared/db_migrations.run_migrations()` after the
existing RCE column block, before `conn.commit()`. All statements use
`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` /
`CREATE VIEW IF NOT EXISTS` — idempotent across any number of runs.

### Auto-run hooks (no user intervention required)

| Entry point | Hook |
|---|---|
| `./smbseek` | `SMBSeekWorkflowDatabase.__init__` → `run_migrations()` |
| `./xsmbseek` / `./xsmbseek --mock` | `DatabaseReader.__init__` → `run_migrations()` |
| `./ftpseek` | New: `load_config` + `run_migrations()` in `main()` after arg parse |

### `db_schema.sql` and the DROP block

FTP tables are included in the DROP block at the top of `db_schema.sql`:

```sql
DROP TABLE IF EXISTS ftp_access;
DROP TABLE IF EXISTS ftp_servers;
```

`initialize_database()` only runs on a fresh/empty DB, so these drops are
harmless in the normal upgrade path. Including them keeps the schema file as
authoritative ground truth — a deliberate DB reset produces a clean state.

---

## Write API — `FtpPersistence` (`shared/database.py`)

```python
fp = FtpPersistence(db_path)

server_id = fp.upsert_ftp_server(
    ip="1.2.3.4", country="United States", country_code="US",
    port=21, anon_accessible=True, banner="220 vsftpd", shodan_data="{}"
)

fp.record_ftp_access(
    server_id=server_id, session_id=42,
    accessible=True, auth_status="anon",
    root_listing_available=True, root_entry_count=8,
    error_message="", access_details=""
)
```

`upsert_ftp_server` on conflict: increments `scan_count`, updates `last_seen`,
`port`, `anon_accessible`, `banner`, `country`, `country_code`, `shodan_data`,
`status`. `first_seen` is never overwritten. Returns the authoritative row id
via `SELECT id … WHERE ip_address = ?` (not `lastrowid`, which is unreliable
on the conflict-update path).

---

## Read API — `DatabaseReader` (`gui/utils/database_access.py`)

All methods use the existing `_get_connection()` context manager pattern and
guard against `sqlite3.OperationalError` (returns `[]` / `0` safely).

```python
reader.get_ftp_servers(country="US")      # List[Dict] — active FTP hosts
reader.get_ftp_server_count()             # int
reader.get_host_protocols(ip="1.2.3.4")  # List[Dict] with protocol_presence
reader.get_dual_protocol_count()          # int — IPs in both tables
```

---

## Verification

```bash
# Schema smoke test (fresh in-memory DB)
python3 -c "
import sqlite3, pathlib
sql = pathlib.Path('tools/db_schema.sql').read_text()
conn = sqlite3.connect(':memory:')
conn.executescript(sql)
tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]
views  = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='view'\").fetchall()]
print('Tables:', sorted(tables))
print('Views:', sorted(views))
assert 'ftp_servers' in tables and 'ftp_access' in tables
assert 'v_host_protocols' in views
print('OK')
"

# Full integration test (run from repo root)
python3 -c "
import tempfile, os, sqlite3, pathlib, sys
sys.path.insert(0, '.')
sql = pathlib.Path('tools/db_schema.sql').read_text()
with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
    db_path = f.name
conn = sqlite3.connect(db_path); conn.executescript(sql); conn.close()
from shared.db_migrations import run_migrations
for _ in range(3): run_migrations(db_path)  # idempotency
from shared.database import FtpPersistence
fp = FtpPersistence(db_path)
id1 = fp.upsert_ftp_server('1.2.3.4','US','US',21,True,'b','{}')
id2 = fp.upsert_ftp_server('1.2.3.4','US','US',2121,False,'b2','{\"x\":1}')
assert id1 == id2
conn = sqlite3.connect(db_path)
row = conn.execute('SELECT scan_count,port FROM ftp_servers WHERE ip_address=?',('1.2.3.4',)).fetchone()
assert row[0]==2 and row[1]==2121
conn.execute(\"INSERT INTO smb_servers (ip_address) VALUES ('9.9.9.9')\")
conn.execute(\"INSERT INTO ftp_servers (ip_address) VALUES ('9.9.9.9')\")
conn.commit()
rows = {r[0]:r[3] for r in conn.execute('SELECT ip_address,has_smb,has_ftp,protocol_presence FROM v_host_protocols').fetchall()}
assert rows['9.9.9.9']=='both' and rows['1.2.3.4']=='ftp_only'
conn.close(); os.unlink(db_path)
print('All assertions PASSED')
"

# Existing test suite
xvfb-run -a python -m pytest gui/tests/ shared/tests/ -v
```

---

## Out of Scope (Card 3)

- FTP file browser / directory listing UI
- Value/ranking filters on FTP hosts
- `ftp_file_manifests` table (Card 5)
- Real FTP network connection or auth logic (Card 4)
- Dashboard UI changes to show FTP counts or dual-protocol badges
- Wiring `commands/ftp/operation.py` / `shared/ftp_workflow.py` to call
  `FtpPersistence` (Card 4)
- `failure_logs` integration for FTP failures (Card 4 decision)
