# Card 4: FTP Discovery Reliability — Implementation Plan

**Status:** Planning — Revision 5
**Date:** 2026-03-15
**Depends on:** Cards 1–3 complete and validated

---

## Revision Notes

Changes from Revision 4:

**R10 — Single error-output boundary (Low)**
Step 6 emitted `out.error(str(e))` in `ftp_workflow.run()` before re-raising, while
Step 7 also printed in `ftpseek` main's except block — producing double output.
Fixed: remove `out.error()` call from `ftp_workflow.run()`. `FtpDiscoveryError`
propagates silently to `ftpseek` main, which is the sole print-and-exit boundary.

**R11 — max_results zero-value handling (Low)**
`ftp_lim or smb_lim or 1000` would treat a configured value of `0` as unset.
Fixed: use explicit `is not None` checks throughout — see Section 3.

---

Changes from Revision 3:

**R8 — Lazy shodan import (Medium)**
`import shodan` at module level in `commands/ftp/shodan_query.py` causes a bare
`ModuleNotFoundError` traceback on missing dependency, bypassing `FtpDiscoveryError`.
Fixed: move `import shodan` inside `query_ftp_shodan()`, wrapped in a try/except
`ImportError` that raises `FtpDiscoveryError("shodan package not installed: ...")`.
All other imports in that file are stdlib and stay at module level.

**R9 — Richer access_details in discovery-stage batch (Low)**
`persist_discovery_outcomes_batch()` wrote `access_details=""` for connect_fail/timeout
rows, inconsistent with stage-2 records which write structured JSON. Fixed: write
`json.dumps({"reason": outcome.reason, "error": outcome.error_message})` so both
stages produce the same field shape. Step 2 (database.py) and the `ftp_access` field
mapping table updated accordingly.

---

Changes from Revision 2:

**R6 — port_check exception order (High)**
`socket.timeout` is a subclass of `OSError` in Python 3.3+. The Revision 2 pseudocode
caught `OSError` first, which swallowed all `socket.timeout` exceptions as
`connect_fail`. Fixed: catch `(socket.timeout, TimeoutError)` before `OSError` in
`port_check`. Section 4 note corrected to state the subclass relationship. Section 9
risk note updated accordingly.

**R7 — API error partial-persistence scope (Medium)**
Added explicit note to Section 8.6 and Section 9: the "no DB writes" guarantee applies
only when `FtpDiscoveryError` is raised before any batch write begins (i.e., during the
Shodan query). Exceptions raised after `persist_discovery_outcomes_batch` commits will
leave stage-1 writes in place.

---

Changes from Revision 1:

**R1 — Persistence architecture (High)**
Removed SQL duplication in `operation.py`. Added two batched methods to `FtpPersistence`
(`persist_discovery_outcomes_batch`, `persist_access_outcomes_batch`). Both open one
connection per stage and commit once. `operation.py` calls these; SQL stays in
`shared/database.py`.

**R2 — max_results config source (High)**
Canonical lookup order defined: (1) `ftp.shodan.query_limits.max_results`, (2)
`shodan.query_limits.max_results`, (3) hard default `1000`. Step 2 and Step 6 now
match this order exactly.

**R3 — Reason-code mapping (High)**
Single authoritative mapping table defined in Section 4. All other sections reference
it. Key settlements: `EOFError` during login → `auth_fail`; `EOFError` during listing →
`list_fail`; `socket.timeout` in port check → `timeout` (not `connect_fail`);
`OSError` in port check → `connect_fail`. `port_check()` returns `tuple[bool, str]`
to carry the reason through.

**R4 — Error propagation boundary (Medium)**
`FtpDiscoveryError` added to `commands/ftp/models.py`. `shodan_query.py` raises it;
does not call `sys.exit`. `ftpseek` main catches it and exits with code 1. No
`sys.exit` inside library code.

**R5 — Hosts Scanned semantics (Medium)**
`📊 Hosts Scanned` = total Shodan candidates entering stage 1 (Option A).
`run_discover_stage()` returns `(list[FtpCandidate], int)` where `int` is the
Shodan total. Stage 1 log summarises both totals. Rollup and all stage summaries are
now consistent.

---

## 1. Current-State Analysis

### FTP skeleton entry chain

| File | Role | Key lines |
|------|------|-----------|
| `ftpseek` | CLI entry | loads config, `run_migrations()`, `create_ftp_workflow(args).run(args)` |
| `shared/ftp_workflow.py` | Orchestrator | `FtpWorkflow.run()` → `run_discover_stage()` → `run_access_stage()` |
| `commands/ftp/operation.py` | Stage impls | Both functions are sleep-loop stubs; return `0` |
| `commands/ftp/models.py` | Data types | `FtpScanResult` exists; `FtpCandidate` and `FtpDiscoveryError` to be added |

### SMB Shodan reference (to mirror safely)

| File | Function | Purpose |
|------|----------|---------|
| `commands/discover/shodan_query.py:5` | `query_shodan()` | Executes Shodan search, populates `op.shodan_host_metadata` |
| `commands/discover/shodan_query.py:79` | `build_targeted_query()` | Assembles query from config |
| `shared/config.py:234` | `get_shodan_config()` | Config accessor pattern to mirror |

FTP Shodan reuses the same API key (`config.get_shodan_api_key()`), country resolution
(`config.resolve_target_countries()`), and error-handling structure. FTP-specific
query string and max_results resolution are described in Section 3.

### FTP persistence (Card 3 — to be extended)

| Method | File | Lines | Card 4 role |
|--------|------|-------|-------------|
| `FtpPersistence.upsert_ftp_server()` | `shared/database.py` | 804–848 | SQL source of truth; reused inside new batch methods |
| `FtpPersistence.record_ftp_access()` | `shared/database.py` | 850–878 | SQL source of truth; reused inside new batch methods |
| `FtpPersistence.persist_discovery_outcomes_batch()` | `shared/database.py` | **new** | Single-connection stage-1 batch writer |
| `FtpPersistence.persist_access_outcomes_batch()` | `shared/database.py` | **new** | Single-connection stage-2 batch writer |

The two existing per-call methods remain unchanged. The new batch methods share their
SQL strings to prevent schema drift.

### Config API (critical constraint)

`SMBSeekConfig.get(section, key=None, default=None)` ([shared/config.py:207](shared/config.py#L207)) is exactly
two-level. Nested access beyond that requires chaining standard dict `.get()` calls on
the returned section dict.

**Correct pattern:**
```python
ftp_cfg = workflow.config.get_ftp_config()        # returns full "ftp" section dict
verif   = ftp_cfg.get("verification", {})
timeout = verif.get("connect_timeout", 5)
```

`get_ftp_config()` is a new helper added to `SMBSeekConfig` (see Patch Step 0).

### What Card 4 does NOT touch

- `gui/` — no GUI changes
- `tools/db_schema.sql` / `shared/db_migrations.py` — schema is complete
- `shared/workflow.py` / `commands/discover/` / `commands/access/` — SMB path untouched

---

## 2. Discovery Architecture Design

### End-to-end flow

```
ftpseek --country US
  │
  ├─ load_config() + run_migrations()
  │
  └─ FtpWorkflow.run(args)        ← catches FtpDiscoveryError → sys.exit(1)
       │
       ├─ [1/2] FTP Discovery
       │    └─ run_discover_stage(workflow)
       │         ├─ query_ftp_shodan()
       │         │    ├─ ImportError       → raise FtpDiscoveryError (missing dep)
       │         │    ├─ APIError/Exception → raise FtpDiscoveryError (no success marker)
       │         │    └─ empty results    → return ([], 0)  ← not an error
       │         ├─ for each candidate: port_check(ip, 21, timeout=5s) → (bool, reason)
       │         │    ├─ FAIL → collect into port_failed list
       │         │    └─ PASS → collect into reachable list
       │         ├─ emit 📊 Progress: i/total per IP
       │         ├─ FtpPersistence.persist_discovery_outcomes_batch(port_failed)
       │         └─ return (reachable_list, shodan_total_count)
       │
       ├─ [2/2] FTP Access Verification
       │    └─ run_access_stage(workflow, candidates)
       │         ├─ for each candidate: try_anon_login() → conditionally try_root_listing()
       │         ├─ emit 📊 Progress: i/total per candidate
       │         ├─ FtpPersistence.persist_access_outcomes_batch(outcomes)
       │         └─ return accessible_count
       │
       └─ emit rollup + 🎉 (only on clean exit)
```

### Stage data contracts

**Stage 1 returns:** `tuple[list[FtpCandidate], int]`
- `list[FtpCandidate]` — port-reachable hosts only (passed to stage 2)
- `int` — total Shodan candidates that entered the port-check loop (used in `Hosts Scanned` rollup)

Port-failed hosts are persisted inside stage 1 and excluded from the returned list.

**Stage 2 returns:** `int` — accessible count (used in `Hosts Accessible` rollup).

### `FtpCandidate` (added to `commands/ftp/models.py`)

```python
@dataclass
class FtpCandidate:
    ip: str
    port: int          # 21 for Card 4
    banner: str        # Shodan banner field; '' if absent
    country: str       # full country name
    country_code: str  # ISO alpha-2
    shodan_data: dict  # raw Shodan match metadata; serialised to JSON for DB
```

### `FtpDiscoveryError` (added to `commands/ftp/models.py`)

```python
class FtpDiscoveryError(Exception):
    """Raised by shodan_query.py on API/import failure. Caught at CLI boundary."""
```

### Timeout policy

| Operation | Timeout | Retries |
|-----------|---------|---------|
| TCP port check | 5 s | 0 |
| FTP anonymous login | 10 s | 0 |
| FTP root listing | 15 s | 0 |

### Cancellation

`ftpseek` runs as a subprocess; cancellation = SIGTERM from
`gui/utils/backend_interface/process_runner.py`. All socket operations carry explicit
timeouts, so no blocking I/O outlasts its window. `KeyboardInterrupt` is already
caught in `ftpseek:main()`.

---

## 3. Shodan Query Strategy

### Default query string

```
port:21 "230 Login successful"
```

Shodan indexes FTP banner text. `"230 Login successful"` is the standard reply emitted
by vsftpd, ProFTPD, Pure-FTPd, and IIS FTP after a successful login — Shodan observed
a completed login, a strong signal of anonymous accessibility. More precise than
`port:21 anonymous` which also matches servers that mention anonymous in rejection
messages.

**Wider fallback** (configure via `ftp.shodan.query_components.base_query`):
```
port:21 "Anonymous"
```

### max_results canonical lookup order

In `commands/ftp/shodan_query.py`, resolve `max_results` as:

```python
ftp_cfg  = workflow.config.get_ftp_config()
ftp_lim  = ftp_cfg.get("shodan", {}).get("query_limits", {}).get("max_results")
smb_lim  = workflow.config.get_shodan_config().get("query_limits", {}).get("max_results")
max_results = ftp_lim if ftp_lim is not None else (smb_lim if smb_lim is not None else 1000)
```

1. FTP-specific `ftp.shodan.query_limits.max_results`
2. Global `shodan.query_limits.max_results`
3. Hard default `1000`

Uses `is not None` throughout — a configured value of `0` is honoured, not treated as
unset. `conf/config.json.example` must include `ftp.shodan.query_limits.max_results`
so operators can set it without touching the global SMB limit.

### Deduplication

Results loaded into a `dict` keyed by `ip_str`; last-wins per IP. Shodan `search()`
returns at most one record per IP per call, so duplicates are a non-issue in practice.

### Empty results (zero matches)

Zero matches is a **valid outcome**, not an error:
- Log `ℹ  No FTP candidates found in Shodan for query: <query>`
- Return `([], 0)` from `query_ftp_shodan()`
- Workflow continues to stage 2 with empty list → rollup `0 / 0 / 0`
- Emit `🎉 FTP scan completed successfully` (zero-result scan is still successful)

### API failure

Shodan API errors prevent meaningful work and must not emit the success marker
(which would set `results["success"] = True` in `progress.py:569`):

| Failure | Handling |
|---------|---------|
| `ImportError` (shodan not installed) | Raise `FtpDiscoveryError("shodan package not installed: ...")` |
| `shodan.APIError` | Raise `FtpDiscoveryError(str(e))` |
| Generic `Exception` during Shodan call | Raise `FtpDiscoveryError(str(e))` |

`ftpseek` main catches `FtpDiscoveryError`, prints `✗  <message>` to stderr,
calls `sys.exit(1)`. `ftp_workflow.run()` re-raises without printing — `ftpseek` main
is the sole output boundary; no double output.

---

## 4. Authoritative Reason-Code Mapping

This table is the single source of truth. All pseudocode, persistence mapping, and
risk notes below must match it exactly.

### Port check (`port_check()` returns `tuple[bool, str]`)

| Exception | `(success, reason)` |
|-----------|---------------------|
| `socket.timeout` / `TimeoutError` | `(False, 'timeout')` |
| `OSError` (refused, unreachable, network error) | `(False, 'connect_fail')` |
| Clean connect | `(True, '')` |

`socket.timeout` is a subclass of `OSError` (Python 3.3+), so it **must be caught
first** in exception handlers. A DROP rule (no RST, no response) raises `socket.timeout`;
a RST raises `ConnectionRefusedError` (also an `OSError`). Catching `socket.timeout`
before `OSError` is what gives each its distinct reason code.

### Anonymous login (`try_anon_login()` returns `tuple[bool, str, str]` = `(ok, banner, reason)`)

| Exception / Condition | `reason` |
|----------------------|---------|
| `ftplib.error_perm` (530 or similar 5xx reply) | `'auth_fail'` |
| `socket.timeout`, `TimeoutError` | `'timeout'` |
| `EOFError` | `'auth_fail'` — server terminated connection before login completed; a server-side rejection, not a network timeout |
| Any other `Exception` | `'auth_fail'` |
| Login succeeds | `''` |

### Root listing (`try_root_listing()` returns `tuple[bool, int, str]` = `(ok, count, reason)`)

| Exception / Condition | `reason` |
|----------------------|---------|
| `ftplib.error_perm` (550 or similar 5xx reply) | `'list_fail'` |
| `socket.timeout`, `TimeoutError` | `'timeout'` |
| `EOFError` | `'list_fail'` — server closed connection before listing completed; treated as listing failure |
| Any other `Exception` | `'list_fail'` |
| Listing succeeds (including empty root) | `''` |

### Full outcome table

| Condition | `accessible` | `auth_status` | `root_listing_available` |
|-----------|-------------|---------------|--------------------------|
| Port: `OSError` | False | `connect_fail` | False |
| Port: `socket.timeout` | False | `timeout` | False |
| Login: `error_perm` | False | `auth_fail` | False |
| Login: `socket.timeout` / `TimeoutError` | False | `timeout` | False |
| Login: `EOFError` / other | False | `auth_fail` | False |
| Listing: `error_perm` | False | `list_fail` | False |
| Listing: `socket.timeout` / `TimeoutError` | False | `timeout` | False |
| Listing: `EOFError` / other | False | `list_fail` | False |
| Login + listing pass | True | `anonymous` | True |

---

## 5. Persistence Design

### Batch methods added to `FtpPersistence` ([shared/database.py](shared/database.py))

The two existing per-host methods (`upsert_ftp_server`, `record_ftp_access`) are
**unchanged**. Two new batch methods are appended to the class. They share the same
SQL strings as the per-host methods (defined as class-level constants to prevent
drift).

#### `FtpDiscoveryOutcome` and `FtpAccessOutcome` (new dataclasses in `commands/ftp/models.py`)

```python
@dataclass
class FtpDiscoveryOutcome:
    """One entry per port-failed host (stage 1)."""
    ip: str
    country: str
    country_code: str
    port: int
    banner: str           # Shodan banner (FTP connect didn't complete)
    shodan_data: str      # json.dumps(metadata)
    reason: str           # 'connect_fail' or 'timeout'
    error_message: str    # short human-readable description

@dataclass
class FtpAccessOutcome:
    """One entry per reachable host (stage 2)."""
    ip: str
    country: str
    country_code: str
    port: int
    banner: str           # FTP connect banner
    shodan_data: str      # json.dumps(metadata)
    accessible: bool
    auth_status: str      # from authoritative table in Section 4
    root_listing_available: bool
    root_entry_count: int
    error_message: str
    access_details: str   # json.dumps({"reason": ..., ...})
```

#### `persist_discovery_outcomes_batch(outcomes: list[FtpDiscoveryOutcome]) -> None`

```
Opens one sqlite3.connect(self.db_path) context.
For each outcome:
  - Execute _UPSERT_SQL (same SQL as upsert_ftp_server, anon_accessible=False)
  - SELECT id for server_id
  - Execute _ACCESS_SQL (same SQL as record_ftp_access)
    with: accessible=False, auth_status=outcome.reason,
          root_listing_available=False, root_entry_count=0,
          session_id=None,
          access_details=json.dumps({"reason": outcome.reason,
                                     "error": outcome.error_message})
conn.commit() once at end of loop.
```

#### `persist_access_outcomes_batch(outcomes: list[FtpAccessOutcome]) -> None`

```
Opens one sqlite3.connect(self.db_path) context.
For each outcome:
  - Execute _UPSERT_SQL (anon_accessible=outcome.accessible)
  - SELECT id for server_id
  - Execute _ACCESS_SQL with all outcome fields
conn.commit() once at end of loop.
```

Both methods use class-level SQL constants `_UPSERT_SQL` and `_ACCESS_SQL`,
defined once and shared by all four methods. This guarantees schema alignment.

### `ftp_servers` field mapping

| Field | Source |
|-------|--------|
| `ip_address` | `outcome.ip` |
| `country` | `outcome.country` (Shodan metadata) |
| `country_code` | `outcome.country_code` (Shodan metadata) |
| `port` | `outcome.port` (21) |
| `anon_accessible` | False for discovery failures; `outcome.accessible` for access outcomes |
| `banner` | Shodan banner for port-failed; FTP connect banner for auth/listing outcomes |
| `shodan_data` | `outcome.shodan_data` |

`ON CONFLICT(ip_address) DO UPDATE` — re-scans increment `scan_count`, update mutable
fields, preserve `first_seen`.

### `ftp_access` field mapping

| Field | Value |
|-------|-------|
| `server_id` | Resolved via `SELECT id FROM ftp_servers WHERE ip_address = ?` within batch |
| `session_id` | `None` — Card 4 defers session tracking; FK is nullable |
| `accessible` | From authoritative table, Section 4 |
| `auth_status` | From authoritative table, Section 4 |
| `root_listing_available` | True only on full success |
| `root_entry_count` | `len(ftp.nlst())` on success; `0` otherwise |
| `error_message` | Short description; `''` on success |
| `access_details` | Stage 1: `json.dumps({"reason": outcome.reason, "error": outcome.error_message})` — Stage 2: `json.dumps({"reason": auth_status, "banner": banner})` |

### Session linkage

`session_id=None` in Card 4. The `ftp_access.session_id` FK is nullable per schema.
Operators can group results by `ftp_access.test_timestamp`. Session ID creation is
Card 6 scope.

---

## 6. Progress / Log Design

### Metric semantics

**`📊 Hosts Scanned` = total Shodan candidates entering the port-check loop.**

This answers "how many Shodan results did we process?" The port-reachable subset is
surfaced in the stage-1 summary log line. Both counts are visible to the operator;
only `Hosts Scanned` feeds the GUI-parsed rollup.

### Per-stage output

**Stage 1 (Discovery):**
```
ℹ  Querying Shodan for FTP servers in: US
ℹ  Found 342 FTP candidates in Shodan database
ℹ  Checking port reachability for 342 hosts...
📊 Progress: 1/342 (0.3%)
...
📊 Progress: 342/342 (100.0%)
ℹ  Port check complete: 287 reachable, 55 unreachable (342 total)
```

**Stage 2 (Access Verification):**
```
ℹ  Testing anonymous FTP access for 287 reachable hosts...
📊 Progress: 1/287 (0.3%)
...
📊 Progress: 287/287 (100.0%)
ℹ  Access verification complete: 41 accessible of 287 tested
```

**Per-host verbose lines** (`--verbose` only):
```
ℹ    192.0.2.1 — connect_fail (OSError)
ℹ    192.0.2.2 — timeout (port check)
ℹ    192.0.2.3 — auth_fail
ℹ    192.0.2.4 — anonymous OK, 12 root entries
```

**Rollup + success (clean exit only):**
```
📊 Hosts Scanned: 342
🔓 Hosts Accessible: 41
📁 Accessible Shares: 0
🎉 FTP scan completed successfully
```

**API error exit (no rollup, no success marker):**
```
✗  Shodan API error: <message>
```
→ process exits with code 1. Single output line from `ftpseek` main only.

### Parser compatibility

All patterns are already registered in `gui/utils/backend_interface/progress.py`.
No changes to `progress.py` needed for Card 4.

Emit `ℹ  Querying Shodan...` **before** the blocking Shodan API call to prevent an
apparent freeze in the GUI log pane.

---

## 7. Patch Sequence (Ordered)

### Step 0 — `shared/config.py` (add method)

Add `get_ftp_config()` to `SMBSeekConfig`, mirroring `get_shodan_config()`:

```python
def get_ftp_config(self) -> dict:
    """Get FTP configuration section with defaults."""
    return self.get("ftp") or {
        "shodan": {
            "query_components": {
                "base_query": "port:21 \"230 Login successful\"",
                "additional_exclusions": []
            },
            "query_limits": {"max_results": None}   # None → triggers fallback to global
        },
        "verification": {
            "connect_timeout": 5,
            "auth_timeout": 10,
            "listing_timeout": 15
        }
    }
```

### Step 1 — `commands/ftp/models.py` (extend)

Append four new items after `FtpScanResult`:
- `FtpCandidate` dataclass
- `FtpDiscoveryOutcome` dataclass
- `FtpAccessOutcome` dataclass
- `FtpDiscoveryError(Exception)` class

### Step 2 — `shared/database.py` (extend `FtpPersistence`)

Extract SQL into class-level constants `_UPSERT_SQL` and `_ACCESS_SQL` (same SQL
already in `upsert_ftp_server` and `record_ftp_access`). Refactor those methods to
reference the constants. **No behaviour change to existing methods.**

Add two new methods:
- `persist_discovery_outcomes_batch(outcomes: list[FtpDiscoveryOutcome]) -> None`
- `persist_access_outcomes_batch(outcomes: list[FtpAccessOutcome]) -> None`

Each opens one connection, loops through outcomes executing upsert + access record per
item, commits once at the end. `persist_discovery_outcomes_batch` writes
`access_details=json.dumps({"reason": ..., "error": ...})` (not empty string) to match
the field shape used by `persist_access_outcomes_batch`.

### Step 3 — `commands/ftp/shodan_query.py` (new file)

Stdlib imports only at module level. `shodan` is imported lazily inside the function.

**`query_ftp_shodan(workflow, country, custom_filters) -> list[FtpCandidate]`**
- **Lazy import**: first line of function body:
  ```python
  try:
      import shodan
  except ImportError as e:
      raise FtpDiscoveryError(f"shodan package not installed: {e}")
  ```
- Resolves `max_results` via canonical 3-tier order with `is not None` checks (Section 3)
- Reads `workflow.config.get_ftp_config()["shodan"]["query_components"]` for query parts
- Reads `workflow.config.get_shodan_api_key()` for API key
- Calls `shodan.Shodan(api_key).search(query, limit=max_results)`
- On `shodan.APIError` or `Exception`: raises `FtpDiscoveryError(str(e))`
- Returns `list[FtpCandidate]` (empty list on zero results — not an error)

**`build_ftp_query(workflow, countries, custom_filters) -> str`**
- Default `base_query`: `port:21 "230 Login successful"` (from config, with fallback)
- Country filter: same logic as `commands/discover/shodan_query.py:96–102`
- No product filter or org exclusions in Card 4

### Step 4 — `commands/ftp/verifier.py` (new file)

Imports: `socket`, `ftplib` (stdlib only, no new deps).

**`port_check(ip, port, timeout=5.0) -> tuple[bool, str]`**
```
try: socket.create_connection((ip, port), timeout)       → (True, '')
except (socket.timeout, TimeoutError):                   → (False, 'timeout')
except OSError:                                          → (False, 'connect_fail')
```
`socket.timeout` caught before `OSError` — it is a subclass; reversed order would fold
all timeouts into `connect_fail`.

**`try_anon_login(ip, port, timeout=10.0) -> tuple[bool, str, str]`** → `(ok, banner, reason)`
```
try: ftplib.FTP(timeout).connect().login().quit()   → (True, banner, '')
except ftplib.error_perm:                            → (False, '', 'auth_fail')
except (socket.timeout, TimeoutError):               → (False, '', 'timeout')
except EOFError:                                     → (False, '', 'auth_fail')
except Exception:                                    → (False, '', 'auth_fail')
```

**`try_root_listing(ip, port, timeout=15.0) -> tuple[bool, int, str]`** → `(ok, count, reason)`
```
try: connect().login().nlst().quit()                 → (True, len(entries), '')
except ftplib.error_perm:                            → (False, 0, 'list_fail')
except (socket.timeout, TimeoutError):               → (False, 0, 'timeout')
except EOFError:                                     → (False, 0, 'list_fail')
except Exception:                                    → (False, 0, 'list_fail')
```

### Step 5 — `commands/ftp/operation.py` (rewrite)

**`run_discover_stage(workflow) -> tuple[list[FtpCandidate], int]`**:
1. Get timeouts from `workflow.config.get_ftp_config()["verification"]`
2. Call `query_ftp_shodan(workflow, country, custom_filters)` — raises `FtpDiscoveryError` on fail
3. `shodan_total = len(candidates)`. If zero, emit log, return `([], 0)`
4. Port-check loop with `📊 Progress` per host; collect `port_failed_outcomes` and `reachable`
5. `FtpPersistence(workflow.db_path).persist_discovery_outcomes_batch(port_failed_outcomes)`
6. Return `(reachable, shodan_total)`

**`run_access_stage(workflow, candidates: list[FtpCandidate]) -> int`**:
1. Get timeouts from `workflow.config.get_ftp_config()["verification"]`
2. For each candidate: `try_anon_login()` then conditionally `try_root_listing()`
3. Build `FtpAccessOutcome` per candidate; append to outcomes list
4. `📊 Progress` per candidate
5. `FtpPersistence(workflow.db_path).persist_access_outcomes_batch(outcomes)`
6. Return `sum(1 for o in outcomes if o.accessible)`

### Step 6 — `shared/ftp_workflow.py` (extend)

Add `config`, `db_path` to `FtpWorkflow.__init__`. Update `create_ftp_workflow()`:

```python
def create_ftp_workflow(args):
    from shared.config import load_config
    config  = load_config(getattr(args, 'config', None))
    db_path = config.get_database_path()
    output  = _FtpOutput(verbose=..., no_colors=...)
    return FtpWorkflow(output, config, db_path)
```

Update `run()`:
- Unpack `(reachable, shodan_total) = run_discover_stage(self)`
- Change `run_access_stage(self, candidates)` call
- Catch `FtpDiscoveryError` → **re-raise immediately, no `out.error()` call**;
  `ftpseek` main is the sole print-and-exit boundary (prevents double output)
- Rollup uses `shodan_total` for `Hosts Scanned`, `accessible` for `Hosts Accessible`

### Step 7 — `ftpseek` (extend `main()`)

Add explicit catch for `FtpDiscoveryError` before the generic `Exception` handler:

```python
from commands.ftp.models import FtpDiscoveryError
...
except FtpDiscoveryError as exc:
    print(f"✗  {exc}", file=sys.stderr)
    sys.exit(1)
except Exception as exc:
    ...
```

### Step 8 — `conf/config.json.example` (extend)

Add FTP section (additive only):

```json
"ftp": {
  "shodan": {
    "query_components": {
      "base_query": "port:21 \"230 Login successful\"",
      "additional_exclusions": []
    },
    "query_limits": {
      "max_results": 1000
    }
  },
  "verification": {
    "connect_timeout": 5,
    "auth_timeout": 10,
    "listing_timeout": 15
  }
}
```

---

## 8. Verification Plan

### 8.1 Import smoke checks

```bash
source venv/bin/activate
python -c "from commands.ftp.models import FtpCandidate, FtpDiscoveryError, FtpDiscoveryOutcome, FtpAccessOutcome; print('OK')"
python -c "from commands.ftp.shodan_query import query_ftp_shodan, build_ftp_query; print('OK')"
python -c "from commands.ftp.verifier import port_check, try_anon_login, try_root_listing; print('OK')"
python -c "from commands.ftp import operation; print('OK')"
python -c "from shared.ftp_workflow import create_ftp_workflow; print('OK')"
python -c "from shared.config import SMBSeekConfig; c = SMBSeekConfig(); print(c.get_ftp_config()); print('OK')"
python -c "from shared.database import FtpPersistence; p = FtpPersistence('test.db'); print('OK')"
./ftpseek --help
./smbseek --help
```

### 8.2 Verifier unit checks (no Shodan or real FTP needed)

```python
from commands.ftp.verifier import port_check

# connect_fail: RST from refused connection
ok, reason = port_check("127.0.0.1", 1)   # port 1 refused on most systems
assert ok is False and reason == 'connect_fail', f"got {reason}"

# timeout: RFC 5737 TEST-NET — verifies handler order is correct
ok, reason = port_check("192.0.2.1", 21, timeout=2.0)
assert ok is False and reason in ('connect_fail', 'timeout')
# If this returns 'connect_fail' on a known-DROP address, exception order is wrong

# EOFError path tested in integration (requires cooperating server)
```

### 8.3 Full pipeline checks (requires controlled FTP server)

Use local vsftpd or Docker container per scenario:

| Scenario | Server config | Expected DB state |
|----------|--------------|-------------------|
| Port unreachable (refused) | Stop FTP daemon | `auth_status='connect_fail'`, `accessible=0` |
| Port drop (timeout) | iptables DROP on 21 | `auth_status='timeout'`, `accessible=0` |
| Auth fail | `anonymous_enable=NO` | `auth_status='auth_fail'`, `accessible=0` |
| Login OK, listing fail | anonymous OK, 550 on root | `auth_status='list_fail'`, `accessible=0` |
| Success | `anonymous_enable=YES` | `auth_status='anonymous'`, `accessible=1`, `root_listing_available=1` |

```bash
sqlite3 smbseek.db "SELECT ip_address, anon_accessible FROM ftp_servers ORDER BY last_seen DESC LIMIT 5;"
sqlite3 smbseek.db "SELECT auth_status, accessible, root_entry_count FROM ftp_access ORDER BY test_timestamp DESC LIMIT 5;"
```

Also verify `access_details` is structured JSON (not empty string) for both
discovery-stage and access-stage rows.

### 8.4 Re-scan idempotency

Run `./ftpseek --country XX` twice against same target.
- `ftp_servers.scan_count` increments by 1
- `ftp_servers.first_seen` unchanged
- Two `ftp_access` rows for same IP

### 8.5 max_results fallback chain

Set `ftp.shodan.query_limits.max_results` to `null` (or omit key); verify fallback
reads from `shodan.query_limits.max_results`. Remove both; verify hard default `1000`.
Set either to `0`; verify `0` is used (not overridden by fallback).

### 8.6 Shodan API error → exit(1), no success marker

**Missing dependency test:**
```bash
pip uninstall -y shodan
./ftpseek --country US
# Expect: single ✗ line on stderr, exit 1, no 🎉, no DB rows
pip install shodan
```

**Invalid API key test:**
```bash
# Set invalid key in conf/config.json
./ftpseek --country US
# Expect: single ✗ line on stderr (not doubled), exit 1, no 🎉, no DB rows
```

**Scope of "no DB writes" guarantee:** Holds only when `FtpDiscoveryError` is raised
inside `query_ftp_shodan()`, before any port checks or batch persistence. An exception
raised after `persist_discovery_outcomes_batch()` commits will leave stage-1 writes
intact — acceptable, as upsert is idempotent.

### 8.7 Cancel behavior

Start FTP scan from dashboard, immediately click Stop.
Subprocess terminates within `connect_timeout + auth_timeout + listing_timeout` = 30s max.
GUI returns to idle; no hang.

**Note:** Do not use `./xsmbseek --mock` to validate Card 4 logic. The mock path
bypasses all real FTP I/O.

### 8.8 SMB regression

```bash
xvfb-run -a python -m pytest gui/tests/ shared/tests/ -v
```

### Expected pass criteria

- [ ] All import smoke checks pass
- [ ] `port_check` returns `connect_fail` for refused, `timeout` for DROP (verifies handler order)
- [ ] All five failure/success scenarios produce correct DB rows
- [ ] `access_details` is structured JSON for both stage-1 and stage-2 rows
- [ ] Re-scan increments `scan_count`, preserves `first_seen`
- [ ] API error: single `✗` line, exit 1, no success marker, no DB writes
- [ ] Missing shodan dep: same as API error (via `FtpDiscoveryError`, not bare traceback)
- [ ] max_results fallback chain works; `0` is honoured
- [ ] Stop terminates subprocess ≤ 30s
- [ ] All existing pytest tests pass

---

## 9. Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| `conf/config.json` missing `ftp` section | Medium | `get_ftp_config()` returns full default dict; no `KeyError` possible |
| Shodan API key absent | Medium | `get_shodan_api_key()` raises `ValueError`; caught as `FtpDiscoveryError` in main |
| `shodan` package not installed | Medium | Lazy import with `ImportError` → `FtpDiscoveryError`; clean single-line error output |
| `ftplib` blocks longer than timeout | Low | All `ftplib` calls use `timeout=` parameter; stdlib-enforced |
| Batch commit fails mid-stage (disk full, lock) | Low | Stage transaction is atomic — all or nothing; upsert is idempotent on re-run |
| `EOFError` misclassified | Resolved | Section 4 table is definitive: login `EOFError` = `auth_fail`, listing `EOFError` = `list_fail` |
| port_check timeout misclassified as `connect_fail` | Resolved | `(socket.timeout, TimeoutError)` caught **before** `OSError`; order is mandatory due to subclass relationship |
| Double error output on `FtpDiscoveryError` | Resolved | `ftp_workflow.run()` re-raises silently; only `ftpseek` main prints |
| `max_results = 0` treated as unset | Resolved | All fallback checks use `is not None` |
| Partial persistence after Shodan success | Low | Stage-1 batch commit is atomic; stage-2 crash leaves stage-1 rows (correct, idempotent). Re-run overwrites via upsert. |
| Long Shodan API call stalls log pane | Medium | `ℹ  Querying Shodan...` emitted before the blocking call |
| Shodan returns duplicate IPs | Low | `dict` keyed by `ip_str`; inherent deduplication |
| `ftp.nlst()` errors on binary filenames | Low | Caught by `except Exception → list_fail` |
| SQL constants drift between per-host and batch methods | Low | All four `FtpPersistence` methods reference shared `_UPSERT_SQL` / `_ACCESS_SQL` |

---

## 10. Out-of-Scope Confirmation

Card 4 does **NOT** implement:

1. FTP directory browser / navigation (Card 5)
2. File download / quarantine (Card 5)
3. FTP probe snapshots (Card 5)
4. Session ID linkage for `ftp_access` (Card 6)
5. GUI results table update for FTP hosts (Card 5/6)
6. Organization exclusions for FTP Shodan queries (post-MVP)
7. Recursive listing (Card 5)
8. FTPS / SFTP support (post-MVP; anonymous plain FTP only)
9. FTP server version fingerprinting (post-MVP)
10. Selective re-scan of failed IPs (post-MVP)
11. Test suite expansion (Card 6)
12. `📊 Hosts Scanned` = port-reachable count (Option B) — Option A (Shodan total) is used
