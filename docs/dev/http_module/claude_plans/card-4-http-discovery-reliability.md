# Card 4: HTTP Discovery Reliability + Count Extraction — Implementation Plan

## Context

Cards 1–3 delivered the HTTP dialog, CLI/workflow skeleton, and DB persistence layer.
Card 4 replaces the two operation stubs in `commands/http/operation.py` with real
Shodan querying, HTTP(S) verification, directory-index validation, file/dir counting
(root + one-level recursion), categorized failure reasons, and batch DB persistence.

Non-negotiables:
- SMB/FTP code paths untouched
- 0-count HTTP hosts persist (dir_count=0, file_count=0 is a valid accessible state)
- "Shares > 0" filter works via `accessible_dirs_count + accessible_files_count` (DB line 1975)
- Both HTTP and HTTPS are always attempted when both verify flags are true
- All timeouts bounded; no hanging scan lifecycle
- Additive/surgical changes only

---

## 0. Pre-Revision Reality-Check Commands

Run these before writing any code to confirm baseline health:

```bash
source venv/bin/activate

python tools/db_bootstrap_smoketest.py
python httpseek --help
python httpseek --country US --verbose 2>&1 | head -20

# Confirm http_* tables exist
sqlite3 smbseek.db ".tables" | tr ' ' '\n' | grep http

# Confirm stubs in place
grep -n "skeleton mode" commands/http/operation.py
grep -n "Card 4 will implement" shared/database.py

# Record baseline test count
xvfb-run -a python -m pytest gui/tests/ shared/tests/ -q 2>&1 | tail -5
```

Expected: stubs present, http_* tables present, test suite at known baseline.

---

## 1. Files to Create (new)

| File | Purpose |
|---|---|
| `commands/http/shodan_query.py` | `build_http_query()` + `query_http_shodan()` — mirrors `commands/ftp/shodan_query.py` |
| `commands/http/verifier.py` | Pure verification functions — mirrors `commands/ftp/verifier.py` |

## 2. Files to Modify

| File | What changes |
|---|---|
| `commands/http/models.py` | Add `HttpCandidate`, `HttpDiscoveryOutcome`, `HttpAccessOutcome` |
| `commands/http/operation.py` | Replace both stubs with real `run_discover_stage()` + `run_access_stage()` |
| `shared/database.py` | Implement both `HttpPersistence` batch stubs + `http_probe_cache` UPSERT SQL |
| `shared/config.py` | Add `get_http_config()` + two concurrent-host getters (after `get_ftp_config()` at line 290) |
| `conf/config.json.example` | Add `"http"` section |

**Not touched:** `httpseek` (CLI TLS flag deferred to Card 6 — see §10), `shared/http_workflow.py`,
`gui/`, `shared/db_migrations.py`, `tools/db_schema.sql`, any SMB/FTP file.

---

## 3. Data Contract — HTTP Outcome Models

### `HttpCandidate` (port-check survivor → Stage 2 input)

```python
@dataclass
class HttpCandidate:
    ip: str
    port: int           # from Shodan (80, 443, or other)
    scheme: str         # 'http' or 'https' (inferred: 'https' if port==443, else 'http')
    banner: str         # Shodan data/banner field; '' if absent
    title: str          # Shodan http.title; '' if absent
    country: str        # full country name
    country_code: str   # ISO alpha-2
    shodan_data: dict   # lightweight metadata: {org, isp, country_name, country_code, port, hostnames}
```

### `HttpDiscoveryOutcome` (Stage 1 port-fail record)

```python
@dataclass
class HttpDiscoveryOutcome:
    ip: str
    country: str
    country_code: str
    port: int
    scheme: str
    banner: str
    title: str
    shodan_data: str    # json.dumps(metadata dict)
    reason: str         # 'timeout' | 'connect_fail'
    error_message: str
```

### `HttpAccessOutcome` (Stage 2 result — ALL hosts, success + failure)

```python
@dataclass
class HttpAccessOutcome:
    ip: str
    country: str
    country_code: str
    port: int           # winning port (candidate port or canonical 80/443)
    scheme: str         # winning scheme ('http' or 'https')
    banner: str
    title: str          # Shodan title carried through; overridden if live response has better title
    shodan_data: str    # json.dumps(metadata)
    accessible: bool    # True only when is_index_page=True and reason=''
    status_code: int    # 0 on network failure
    is_index_page: bool
    dir_count: int      # root dirs + recursed subdir dirs; 0 when inaccessible
    file_count: int     # root files + recursed subdir files; 0 when inaccessible
    tls_verified: bool  # True when TLS cert verified (requires allow_insecure_tls=False)
    reason: str         # '' on success; taxonomy code on failure
    error_message: str
    access_details: str # json.dumps({reason, status_code, tls_verified,
                        #  dir_count, file_count,
                        #  attempts: [{scheme, port, status_code, is_index, reason, parse_ok}],
                        #  subdirs: [{path, dir_count, file_count}]})
```

`attempts` list records every scheme tried (HTTP and/or HTTPS) for auditability.

---

## 4. Failure Reason Taxonomy

| Code | Stage | Trigger |
|---|---|---|
| `timeout` | 1 (port check) | `socket.timeout` / `TimeoutError` — **catch before `OSError`** |
| `connect_fail` | 1 (port check) | Any other `OSError` (RST, ICMP, DNS via `gaierror`) |
| `timeout` | 2 (HTTP) | `socket.timeout` / `TimeoutError` during `urllib` request |
| `connect_fail` | 2 (HTTP) | `urllib.error.URLError` with `OSError` cause |
| `dns_fail` | 2 (HTTP) | `urllib.error.URLError` where `.reason` string contains DNS text |
| `tls_error` | 2 (HTTP) | `ssl.SSLError` when `allow_insecure_tls=False` |
| `redirect_loop` | 2 (HTTP) | `urllib` too-many-redirects |
| `non_200` | 2 (HTTP) | No attempt returned `status_code=200` |
| `not_index_page` | 2 (HTTP) | Best 200 response failed `validate_index_page()` |
| `parse_error` | 2 (HTTP) | Exception raised inside `count_dir_entries()` |

`accessible=True` requires `reason == ''`.

**Critical: zero-entry index pages are success, not failure.**
When `validate_index_page()` returns True and `count_dir_entries()` returns `(0, 0, [])` without
raising an exception, the outcome is `accessible=True, dir_count=0, file_count=0, reason=''`.
There is no `empty_listing` failure code. A parse exception → `parse_error` (failure).
A genuinely empty listing → success with 0 counts.

---

## 5. Verification Strategy — `commands/http/verifier.py`

All functions are pure stdlib-only (no `requests`). Parallel to `commands/ftp/verifier.py`.

### `port_check(ip, port, timeout=5.0) → (bool, reason_str)`
Direct copy of FTP pattern. `socket.timeout` caught before `OSError`.

### `try_http_request(ip, port, scheme, allow_insecure_tls=True, timeout=10.0, path='/') → (status_code, body, tls_verified, reason)`
- Uses `urllib.request` with `ssl.create_default_context()`
- HTTPS + `allow_insecure_tls=True`: `ctx.check_hostname=False`, `ctx.verify_mode=CERT_NONE`; `tls_verified=False`
- HTTPS + `allow_insecure_tls=False`: default ctx (cert verification on); `tls_verified=True` on success, `reason='tls_error'` on `ssl.SSLError`
- HTTP always: `tls_verified=False`
- Exception catch order: `urllib.error.HTTPError` → `ssl.SSLError` → `socket.timeout`/`TimeoutError` → `urllib.error.URLError` → `Exception`
- `urllib.error.HTTPError` is a subclass of `URLError` — must be caught first

### `validate_index_page(body, status_code) → bool`
- `status_code == 200`
- AND (`<title>Index of` OR `<title>Directory listing`) case-insensitive
- AND at least one `<a href>` present in body

### `count_dir_entries(body) → (dir_count, file_count, dir_paths)`
- `re.findall(r'<a\s+href=["\']([^"\']+)["\']', body, re.IGNORECASE)`
- Skip: `../`, `?`-prefixed sort links, hrefs starting with `/` that escape the listing
- Dir: href ends with `/`; File: everything else
- **Raises `ValueError` on parse exception** — caller catches and records `reason='parse_error'`
- Returns `(0, 0, [])` normally for a valid but empty listing — caller records success with 0 counts
- Empty vs. parse-error is distinguishable because only exceptions raise; (0,0,[]) with no exception = success

### `fetch_subdir_entries(ip, port, scheme, subdir_path, allow_insecure_tls=True, timeout=8.0) → (dir_count, file_count)`
- Fetches one subdirectory; calls `validate_index_page` + `count_dir_entries`
- Catches all exceptions (including `ValueError` from `count_dir_entries`) — returns `(0, 0)`, never raises

---

## 6. Dual-Protocol Verification Strategy

**`verify_http` and `verify_https` both flow from the dialog into config:**
- `scan_manager.py:1131` writes both flags into `config_overrides["http"]["verification"]`
- `operation.py` reads them via `workflow.config.get_http_config()["verification"]`
- Dialog always sends `verify_http=True, verify_https=True` (fixed in dialog)
- This is the authoritative source; no CLI flag for this in Card 4

**`_check_access()` logic in `operation.py`:**

```
1. Read verify_http, verify_https, allow_insecure_tls from config
   NON-NEGOTIABLE: all three flags come exclusively from
   workflow.config.get_http_config()["verification"] — never from workflow.args.
   The GUI writes them via scan_manager.py:1131–1134 into config_overrides before
   subprocess invocation, so they arrive as authoritative config values.

2. Build attempt list — candidate port first, canonical ports supplemental (deduped):
     attempts = []
     # Candidate's own (scheme, port) from Shodan — attempt first if its flag is enabled
     if candidate.scheme == 'http'  and verify_http:  attempts.append(('http',  candidate.port))
     if candidate.scheme == 'https' and verify_https: attempts.append(('https', candidate.port))
     # Canonical supplemental ports — add only if not already in list
     if verify_http  and ('http',  80)  not in attempts: attempts.append(('http',  80))
     if verify_https and ('https', 443) not in attempts: attempts.append(('https', 443))
   This ensures non-standard ports (8080, 8443, etc.) are tried before falling back to 80/443.

3. For each (scheme, port) in attempts — run independently, collect all results:
     status_code, body, tls_verified, reason = try_http_request(ip, port, scheme, ...)
     is_index = validate_index_page(body, status_code) if body else False
     if is_index:
         try:
             dir_count, file_count, dir_paths = count_dir_entries(body)
             parse_ok = True
         except ValueError:
             dir_count, file_count, dir_paths = 0, 0, []
             parse_ok = False
             reason = 'parse_error'
     else:
         dir_count, file_count, dir_paths, parse_ok = 0, 0, [], True
     record attempt result: {scheme, port, status_code, is_index, tls_verified, reason,
                              dir_count, file_count, dir_paths, parse_ok}
     # parse_ok included in access_details["attempts"] for QA/debugging

4. Winner selection (in priority order):
     a. Any attempt with is_index=True AND parse_ok=True:
          If multiple → pick highest (dir_count + file_count)
          If tied → prefer HTTPS
          parse_error attempts (is_index=True, parse_ok=False) do NOT qualify here
     b. Any attempt with parse_ok=False (parse_error):
          Pick first such attempt; reason='parse_error'; accessible=False
          (parse failures rank below successful index pages but above non-200/not-index)
     c. No index pages and no parse errors, but any attempt has status_code=200:
          Pick first 200 attempt; reason='not_index_page'
     d. No 200 responses, but at least one attempt returned a non-zero status_code
        (server responded with 3xx/4xx/5xx):
          Pick highest-status attempt; reason='non_200'
     e. All attempts failed at network level (status_code=0 for all):
          Pick HTTP attempt's reason (or HTTPS if HTTP wasn't attempted)

5. One-level recursion runs ONLY when winner.is_index=True AND winner.parse_ok=True
   (§6 step 4a is the only winning path that satisfies both; all other paths skip recursion)
   (see §7 for counting algorithm)

6. All attempt records stored in access_details["attempts"] for auditability
```

**`allow_insecure_tls` resolution:**
```python
http_cfg = workflow.config.get_http_config()
verif = http_cfg.get("verification", {})
allow_insecure_tls = verif.get("allow_insecure_tls", True)
verify_http  = verif.get("verify_http",  True)
verify_https = verif.get("verify_https", True)
```

---

## 7. Parsing / Counting Strategy (Root + One-Level Recursion)

Runs on the winner from §6 if `is_index=True AND parse_ok=True` (§6 step 4a guarantees this):

```
root_dirs, root_files, dir_paths = winner["dir_count"], winner["file_count"], winner["dir_paths"]

total_dirs  = root_dirs
total_files = root_files
subdirs_list = []

for path in dir_paths[:MAX_SUBDIRS]:   # MAX_SUBDIRS = 20 constant in operation.py
    sub_d, sub_f = fetch_subdir_entries(
        ip, winner_port, winner_scheme, path, allow_insecure_tls, subdir_timeout)
    total_dirs  += sub_d
    total_files += sub_f
    subdirs_list.append({"path": path, "dir_count": sub_d, "file_count": sub_f})

# Final counts (include both root and subdir content)
outcome.dir_count  = total_dirs
outcome.file_count = total_files
outcome.accessible = True
outcome.reason     = ''
```

`MAX_SUBDIRS = 20` bounds worst-case per-host time to ~160s at 8s subdir timeout.

**Zero-entry case**: If `root_dirs=0` and `root_files=0` with no exception in `count_dir_entries`,
there are no subdir paths to recurse into. `accessible=True, dir_count=0, file_count=0, reason=''`.
This is a valid empty listing, not a failure.

---

## 8. Persistence Mapping

### "Shares > 0" filter alignment

`database_access.py` lines 1975–1978 map HTTP "shares" as:
```sql
COALESCE(hpc.accessible_dirs_count, 0) + COALESCE(hpc.accessible_files_count, 0)
    AS total_shares,
... AS accessible_shares,
```

Therefore: a host with `dir_count=5, file_count=3` must persist `accessible_dirs_count=5,
accessible_files_count=3` in `http_probe_cache`. Both counts must be written correctly —
files are not optional. A host with `dir_count=0, file_count=5` would have `accessible_shares=5`
and show in "Shares > 0" results.

### `persist_discovery_outcomes_batch(outcomes: List[HttpDiscoveryOutcome])`

Per outcome (single transaction):
- `upsert_http_server(ip, country, country_code, port, scheme, banner, title, shodan_data)` → `server_id`
- INSERT into `http_access`: `accessible=0, status_code=0, is_index_page=0, dir_count=0,
  file_count=0, tls_verified=0, error_message=o.error_message,
  access_details=json.dumps({"reason": o.reason})`
- **No** `http_probe_cache` entry for Stage 1 failures

### `persist_access_outcomes_batch(outcomes: List[HttpAccessOutcome])`

Per outcome (single transaction):
- `upsert_http_server(...)` → `server_id`
- INSERT into `http_access`: all fields (bool → 0/1)
- UPSERT into `http_probe_cache`:

```sql
INSERT INTO http_probe_cache
    (server_id, accessible_dirs_count, accessible_files_count,
     accessible_dirs_list, updated_at)
VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
ON CONFLICT(server_id) DO UPDATE SET
    accessible_dirs_count  = excluded.accessible_dirs_count,
    accessible_files_count = excluded.accessible_files_count,
    accessible_dirs_list   = excluded.accessible_dirs_list,
    updated_at             = CURRENT_TIMESTAMP
```

Values:
- `accessible_dirs_count`: `o.dir_count` (always; 0 when inaccessible)
- `accessible_files_count`: `o.file_count` (always; 0 when inaccessible)
- `accessible_dirs_list`: comma-joined `path` values from `access_details["subdirs"]` ('' when empty)

Written for ALL Stage 2 outcomes including failures — required for "Shares > 0" filter correctness.

---

## 9. Progress / Log Output Contract

All lines are operator-friendly AND parser-safe. No format changes to existing emitted lines.

### Stage 1 (discover)
```
ℹ  Querying Shodan for HTTP servers in: US, GB
ℹ  Found N HTTP candidates in Shodan database
ℹ  Checking port reachability for N hosts...
📊 Progress: 1/N (X.X%) | Success: 1, Failed: 0 | Active: 10 threads
📊 Progress: 10/N (X.X%) | ...
✓  Port check complete: N reachable, M unreachable (T total)
```

Cadence: first, every 10, final — via `_should_report_progress(completed, total, batch_size=10)`.

### Stage 2 (access)
```
ℹ  Testing HTTP(S) access for N reachable hosts...
📊 Progress: 1/N (X.X%) | Success: 1, Failed: 0 | Active: 4 threads
...
✓  Access verification complete: A accessible of N tested
```

### Rollup (already in `shared/http_workflow.py` — no changes needed)
```
📊 Hosts Scanned: N
🔓 Hosts Accessible: A
📁 Accessible Directories: D
🎉 HTTP scan completed successfully
```

`workflow.last_accessible_directory_count = sum(o.dir_count for o in outcomes if o.accessible)`
set at end of `run_access_stage()`.

---

## 10. CLI Flag Deferral (Deliberate Out-of-Scope)

`--allow-insecure-tls` is **not added to `httpseek` in Card 4**. Reasons:
- `store_true + default=True` cannot express False (no way to set `allow_insecure_tls=False` from CLI)
- `BooleanOptionalAction` requires Python 3.9+ (project targets 3.8+)
- GUI path already works correctly via `config_overrides["http"]["verification"]["allow_insecure_tls"]`
- Adding a broken/no-op CLI flag is worse than omitting it

Card 6 (hardening) can add proper `--no-verify-tls / --verify-tls` paired flags or use
`argparse.Action` to wire the value into the config before workflow creation.

---

## 11. `shared/config.py` Addition

Add after `get_ftp_config()` at line ~322:

```python
def get_http_config(self) -> Dict[str, Any]:
    """Get HTTP configuration section with defaults."""
    return self.get("http") or {
        "shodan": {"query_limits": {"max_results": None}},
        "verification": {
            "connect_timeout": 5,
            "request_timeout": 10,
            "subdir_timeout": 8,
            "allow_insecure_tls": True,
            "verify_http": True,
            "verify_https": True,
        },
        "discovery": {"max_concurrent_hosts": 10},
        "access": {"max_concurrent_hosts": 4},
    }

def get_max_concurrent_http_discovery_hosts(self) -> int:
    value = self.get_http_config().get("discovery", {}).get("max_concurrent_hosts", 10)
    return value if isinstance(value, int) and value >= 1 else 10

def get_max_concurrent_http_access_hosts(self) -> int:
    value = self.get_http_config().get("access", {}).get("max_concurrent_hosts", 4)
    return value if isinstance(value, int) and value >= 1 else 4
```

---

## 12. Shodan Query — `commands/http/shodan_query.py`

**Locked base query**: `http.title:"Index of /"`

```python
def build_http_query(workflow, countries, custom_filters=None) -> str:
    # base = 'http.title:"Index of /"'
    # country suffix mirrors FTP: country:US or country:US,GB,CA
    # custom_filters appended verbatim

def query_http_shodan(workflow, country=None, custom_filters=None) -> List[HttpCandidate]:
    # Raises HttpDiscoveryError on API failure (caught at CLI boundary)
    # Deduplicates by IP (last-wins dict)
    # Lightweight shodan_data: {org, isp, country_name, country_code, port, hostnames}
    # scheme = 'https' if port == 443 else 'http'
    # title  = match.get("http", {}).get("title", "") or ""
    # banner = match.get("data", "") or ""
    # max_results: http_cfg["shodan"]["query_limits"]["max_results"] → global shodan limit → 1000
```

---

## 13. Implementation Order

1. **`commands/http/models.py`** — add three dataclasses (unblocks all imports)
2. **`shared/config.py`** — add `get_http_config()` and two getters
3. **`conf/config.json.example`** — add `"http"` section (verify_http, verify_https, allow_insecure_tls included)
4. **`commands/http/verifier.py`** (new) — five pure functions in dependency order:
   `port_check` → `try_http_request` → `validate_index_page` → `count_dir_entries` → `fetch_subdir_entries`
5. **`commands/http/shodan_query.py`** (new) — depends on models + config
6. **`shared/database.py`** — implement both `HttpPersistence` batch stubs + `http_probe_cache` UPSERT SQL
7. **`commands/http/operation.py`** — replace stubs; FTP helper functions verbatim;
   `_check_access()` with dual-scheme logic + winner selection + one-level recursion

**Implementation constraint for step 7:** `verify_http`, `verify_https`, and `allow_insecure_tls`
must be read exclusively from `workflow.config.get_http_config()["verification"]`. Do not read
from `workflow.args`. This is required for the GUI path to work correctly (scan_manager writes
these into config_overrides which become authoritative config values before subprocess launch).

---

## 14. Regression + Manual Validation Checklist

### Automated

```
[ ] PASS/FAIL  xvfb-run -a python -m pytest gui/tests/ shared/tests/ -q
               Must match or exceed baseline pass count; no new failures
[ ] PASS/FAIL  python tools/db_bootstrap_smoketest.py
[ ] PASS/FAIL  python -c "from commands.http.verifier import port_check; print(port_check('1.1.1.1', 80))"
[ ] PASS/FAIL  python -c "
               from commands.http.verifier import validate_index_page, count_dir_entries
               body = '<title>Index of /</title><a href=\"dir/\">dir/</a><a href=\"file.txt\">f</a>'
               assert validate_index_page(body, 200) == True
               assert count_dir_entries(body) == (1, 1, ['dir/'])
               body2 = '<title>Index of /</title>'  # no anchors — empty listing
               assert validate_index_page(body2, 200) == False  # no anchors → not valid
               print('verifier OK')"
[ ] PASS/FAIL  python -c "
               from commands.http.verifier import validate_index_page, count_dir_entries
               # Empty listing with anchors (valid empty dir)
               body = '<title>Index of /</title><a href=\"../\">Parent</a>'
               # After skipping ../ → 0 dirs, 0 files
               d, f, paths = count_dir_entries(body)
               assert d == 0 and f == 0
               print('zero-entry listing OK')"
[ ] PASS/FAIL  python -c "from shared.config import load_config; c = load_config(); print(c.get_http_config())"
[ ] PASS/FAIL  set -o pipefail && output=$(python httpseek --country US --verbose 2>&1) && ! grep -q "skeleton mode" <<<"$output"
               # pipefail ensures httpseek crash ≠ PASS; negative grep confirms no skeleton output
```

### DB Checks (after any real or injected scan)

```
[ ] PASS/FAIL  sqlite3 smbseek.db "SELECT * FROM http_servers LIMIT 1;"
[ ] PASS/FAIL  sqlite3 smbseek.db "SELECT * FROM http_access LIMIT 1;"
[ ] PASS/FAIL  sqlite3 smbseek.db "SELECT * FROM http_probe_cache LIMIT 1;"
[ ] PASS/FAIL  sqlite3 smbseek.db "
               SELECT s.ip_address, a.accessible, a.dir_count, a.file_count,
                      p.accessible_dirs_count, p.accessible_files_count
               FROM http_servers s
               JOIN http_access a ON a.server_id=s.id
               JOIN http_probe_cache p ON p.server_id=s.id
               LIMIT 5;"
[ ] PASS/FAIL  # Verify a/d/f counts are coherent (p.dirs == a.dir_count for accessible rows)
[ ] PASS/FAIL  sqlite3 smbseek.db "SELECT COUNT(*) FROM http_access WHERE accessible=0;"
               # Must be > 0 — failures persist
[ ] PASS/FAIL  sqlite3 smbseek.db "SELECT COUNT(*) FROM http_probe_cache WHERE accessible_dirs_count=0 AND accessible_files_count=0;"
               # Must be > 0 — 0-count hosts persist
```

### Manual (operator runtime)

```
[ ] PASS/FAIL  SMB scan start → stop → dashboard totals unchanged
[ ] PASS/FAIL  FTP scan start → stop → dashboard totals unchanged
[ ] PASS/FAIL  HTTP scan mock mode: xsmbseek --mock → Start HTTP Scan → progress updates appear
[ ] PASS/FAIL  HTTP scan real (--country US --verbose):
               - Shodan query logged
               - Port check progress lines (📊 Progress: ...) appear
               - Access verification progress lines appear
               - Both HTTP and HTTPS attempts logged per host (visible in verbose)
               - Rollup "Hosts Scanned / Accessible / Directories" appears
               - "🎉 HTTP scan completed successfully" appears
               - http_servers, http_access, http_probe_cache rows present
[ ] PASS/FAIL  "Shares > 0" filter: hides 0-count HTTP rows, shows accessible ones
[ ] PASS/FAIL  TLS verification: at least one host shows tls_verified in access_details
               when allow_insecure_tls=False (toggle via dialog)
```

---

## 15. Out-of-Scope (Card 5/6)

- HTTP browser window / probe snapshot JSON — Card 5
- Quarantine downloads — Card 5
- Ransomware indicator matching for HTTP — Card 5
- RCE signature scanning — Card 5/6
- GUI server list HTTP row routing + deletion semantics — DB import Cards 4–5
- Pytest unit tests for verifier/shodan_query — Card 6
- CLI `--allow-insecure-tls` / `--no-verify-tls` flag — Card 6 hardening
- Deep recursive crawling (beyond one level) — explicit non-goal

---

## 16. Copy-Paste Implementation Prompt (Phase 2)

```
Implement HTTP Card 4 exactly per the approved plan at
/home/kevin/.claude/plans/radiant-painting-music.md.

Implementation order (strict):
1. commands/http/models.py — add HttpCandidate, HttpDiscoveryOutcome, HttpAccessOutcome
2. shared/config.py — add get_http_config() and two concurrent-host getters (after line 321)
3. conf/config.json.example — add "http" section
4. commands/http/verifier.py (NEW) — five pure functions
5. commands/http/shodan_query.py (NEW) — build_http_query + query_http_shodan
6. shared/database.py — implement both HttpPersistence stubs + http_probe_cache UPSERT
7. commands/http/operation.py — replace both stubs; dual-scheme _check_access() with
   winner-selection logic + one-level recursion

Do NOT modify: httpseek, shared/http_workflow.py, shared/db_migrations.py,
tools/db_schema.sql, any SMB file, any FTP file, any GUI file.

After implementation, run the automated checks from section 14 and report results
with PASS/FAIL against each item.
```
