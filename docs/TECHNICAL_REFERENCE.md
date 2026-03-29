# Dirracuda Technical Reference

**Version:** current (`development` branch)
**Scope:** Internals for developers and security analysts who need more than the README and less than reading every file themselves.

Dirracuda scans for internet-accessible servers exposing open or weakly-authenticated directories across three protocols: SMB, FTP, and HTTP. It discovers candidates through the Shodan API, verifies access, persists results to a local SQLite database, and provides both a CLI and a Tkinter GUI for interacting with the data.

---

## Document Conventions

- File paths are relative to the repository root unless prefixed with `~/`.
- Config keys are written in dot-notation (`shodan.api_key`).
- Mermaid diagrams are used for flowcharts and the ER diagram. They render on GitHub and in VS Code with the Mermaid extension.
- The SMB RCE vulnerability analysis feature (`--check-rce`) is **experimental**. Its implementation is incomplete; descriptions in §4.2 and §8 describe design intent and current partial state, not production capability.

---

## 1. System Overview

### 1.1 High-Level Block Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│  CLI Layer                                                          │
│  cli/smbseek.py    cli/ftpseek.py    cli/httpseek.py               │
└──────────┬─────────────────┬──────────────────┬────────────────────┘
           │                 │                  │
           ▼                 ▼                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Workflow Layer                                                      │
│  shared/workflow.py       shared/ftp_workflow.py                    │
│  (UnifiedWorkflow)        (FtpWorkflow)                             │
│                           shared/http_workflow.py                   │
│                           (HttpWorkflow)                            │
└──────────┬─────────────────┬──────────────────┬────────────────────┘
           │                 │                  │
           ▼                 ▼                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Command Layer                                                       │
│  commands/discover/    commands/ftp/      commands/http/            │
│  commands/access/                                                   │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
           ┌──────────────────┼──────────────────────┐
           ▼                  ▼                       ▼
┌──────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│ shared/       │  │ tools/              │  │ conf/               │
│ config.py     │  │ db_manager.py       │  │ config.json         │
│ output.py     │  │ db_schema.sql       │  │ exclusion_list.json │
│ database.py   │  │ db_maintenance.py   │  │ ransomware_         │
│ *_browser.py  │  │                     │  │ indicators.json     │
│ rce_scanner/  │  └──────────┬──────────┘  └─────────────────────┘
└──────────────┘             │
                             ▼
                     ┌──────────────┐
                     │ SQLite        │
                     │ dirracuda.db  │
                     └──────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  GUI Layer                                                          │
│  dirracuda (entry point)                                            │
│    └─ gui/components/dashboard.py                                  │
│         ├─ unified_scan_dialog.py → scan_manager.py               │
│         │                            └─ backend_interface/         │
│         │                                (subprocess → CLI)        │
│         ├─ server_list_window/ (SMB / FTP / HTTP tabs)             │
│         ├─ db_tools_dialog.py                                      │
│         └─ [config editor, browser windows, extract dialogs]       │
│  gui/utils/ui_dispatcher.py  (thread-safe Tk marshaling)          │
│  gui/utils/settings_manager.py  (persists ~/.dirracuda/            │
│                                  gui_settings.json)                │
└─────────────────────────────────────────────────────────────────────┘
```

The GUI never calls workflow code directly. It invokes CLI scripts as subprocesses via `gui/utils/backend_interface/interface.py` and parses their stdout for progress data.

### 1.2 Core Workflow Flowchart

```mermaid
flowchart TD
    A([Start]) --> B[Shodan API query]
    B -->|API error| Z1([Exit / log error])
    B --> C[DB host filter\nget_new_hosts_filter]
    C -->|rescan_all| D
    C -->|rescan_failed| D
    C -->|skip recent ≤ rescan_after_days| Z2([Skip host])
    D[TCP port check\nconcurrent ThreadPoolExecutor] -->|unreachable| E1[Persist to failure_logs]
    D -->|reachable| E[Auth probe]
    E -->|anonymous / guest OK| F[Share / dir enumeration]
    E -->|auth fail| E1
    F --> G[Persist results\nscan_sessions / *_servers / *_access]
    G --> H([Done — print rollup])
```

This shape applies to all three protocols. Protocol-specific differences are covered in §4.

---

## 2. Directory Structure

| Path | Responsibility | Key Files |
|------|---------------|-----------|
| `cli/` | Argument parsing; invoke workflow factory; exit cleanly on error | `smbseek.py`, `ftpseek.py`, `httpseek.py` |
| `commands/discover/` | SMB Shodan query, host filtering, concurrent auth | `shodan_query.py`, `auth.py`, `operation.py`, `host_filter.py`, `connection_pool.py` |
| `commands/access/` | SMB share enumeration and accessibility testing | `operation.py`, `share_enumerator.py`, `share_tester.py`, `rce_analyzer.py` |
| `commands/ftp/` | FTP discovery and access stages | `shodan_query.py`, `verifier.py`, `operation.py`, `models.py` |
| `commands/http/` | HTTP discovery and access stages (parallel to FTP) | `shodan_query.py`, `verifier.py`, `operation.py`, `models.py` |
| `shared/` | Protocol-agnostic utilities shared by CLI and GUI | See §2.1 |
| `gui/components/` | Tkinter windows and dialogs | `dashboard.py`, `unified_scan_dialog.py`, `server_list_window/`, `db_tools_dialog.py`, `*_browser_window.py` |
| `gui/utils/` | GUI infrastructure | `ui_dispatcher.py`, `scan_manager.py`, `backend_interface/`, `probe_runner.py`, `extract_runner.py`, `settings_manager.py` |
| `tools/` | Database management utilities | `db_manager.py`, `db_schema.sql`, `db_maintenance.py`, `db_migrations.py`* |
| `signatures/rce_smb/` | YAML CVE signature definitions | `*.yaml` |
| `conf/` | Application configuration | `config.json.example`, `exclusion_list.json`, `ransomware_indicators.json` |

*`db_migrations.py` lives in `shared/` not `tools/`.

### 2.1 `shared/` Module Map

| Module | Purpose |
|--------|---------|
| `workflow.py` | `UnifiedWorkflow` — SMB 2-stage pipeline orchestrator |
| `ftp_workflow.py` | `FtpWorkflow` — FTP 2-stage pipeline orchestrator |
| `http_workflow.py` | `HttpWorkflow` — HTTP 2-stage pipeline orchestrator |
| `database.py` | `SMBSeekWorkflowDatabase` — host filtering, session tracking, wraps `DatabaseManager` |
| `config.py` | `SMBSeekConfig` — loads `conf/config.json`, deep-merge with defaults, typed accessors |
| `output.py` | `SMBSeekOutput` — formatted console output (color, verbose, quiet modes) |
| `smb_browser.py` | Read-only SMB file browser |
| `ftp_browser.py` | `FtpNavigator` — list directories, download files, cancel mid-operation |
| `http_browser.py` | HTTP directory/file browser |
| `rce_scanner/` | Signature-based SMB RCE analysis (experimental) |
| `db_migrations.py` | `run_migrations()` — additive schema migrations, called on startup |
| `smb_adapter.py` | `SMBAdapter` — unified SMB backend abstraction (smbprotocol + impacket) |
| `results.py` | `DiscoverResult`, `AccessResult` dataclasses |

---

## 3. Configuration

### 3.1 `conf/config.json`

All configuration lives in one JSON file, deep-merged against hardcoded defaults in `SMBSeekConfig.__init__`. Missing keys fall back to defaults silently; a missing file prints a warning and uses defaults entirely.

**Key sections:**

| Section | Key Fields | Notes |
|---------|-----------|-------|
| `shodan` | `api_key`, `query_limits.max_results` (1000), `query_components.base_query`, `product_filter`, `additional_exclusions`, `use_organization_exclusions`, `string_filters` | SMB only. FTP/HTTP have own `shodan` sub-blocks. |
| `workflow` | `rescan_after_days` (30), `skip_failed_hosts` (true) | Controls rescan policy in `get_new_hosts_filter()` |
| `connection` | `timeout` (30s), `port_check_timeout` (5s), `rate_limit_delay` (1s), `share_access_delay` (2s) | SMB connection and throttle settings |
| `discovery` | `max_concurrent_hosts` (10), `max_worker_cap` (20), `smart_throttling` (false) | Thread pool sizing for auth stage |
| `access` | `max_concurrent_hosts` (1 default) | SMB share enumeration concurrency |
| `file_collection` | `max_files_per_target` (3), `max_total_size_mb` (500), `max_directory_depth` (3), `included_extensions`, `excluded_extensions` | Automated file extraction limits |
| `file_browser` | `max_entries_per_dir` (5000), `max_depth` (12), `download_chunk_mb` (4), `quarantine_root` | GUI browser limits |
| `ftp` | `shodan.query_components.base_query`, `verification.{connect,auth,listing}_timeout`, `discovery/access.max_concurrent_hosts` | FTP-specific settings |
| `http` | Parallel to `ftp`; adds `verification.{allow_insecure_tls,verify_http,verify_https,subdir_timeout}` | HTTP-specific settings |
| `rce` | `enabled_default` (false), `safe_active_budget.max_requests` (2), `intrusive_mode_enabled` (false) | RCE probe budget; intrusive mode must be explicitly enabled |
| `clamav` | `enabled` (false), `backend` ("auto"), `timeout_seconds` (60), `extracted_root`, `known_bad_subdir` | Post-extraction AV scanning |
| `quarantine` | `use_tmpfs` (false), `tmpfs_size_mb` (512) | tmpfs quarantine for file downloads |
| `pry` | `wordlist_path`, `user_as_pass` (true), `stop_on_lockout` (true), `attempt_delay` (1.0s) | Password audit tool settings |

### 3.2 `SMBSeekConfig` (shared/config.py)

`load_config(config_file=None)` is the factory. Returns an `SMBSeekConfig` instance with `conf/config.json` as the default path.

Typed accessors of note:

| Method | Returns |
|--------|---------|
| `get_shodan_api_key()` | `str` — raises `ValueError` if empty |
| `get_ftp_config()` | `dict` — full FTP section with defaults merged |
| `get_http_config()` | `dict` — full HTTP section with defaults merged |
| `get_rce_config()` | `dict` — full RCE section with safe defaults |
| `get_clamav_config()` | `dict` — ClamAV settings with defaults |
| `get_max_concurrent_hosts()` | `int` — SMB access concurrency, min 1 |
| `get_max_concurrent_discovery_hosts()` | `int` — SMB discovery concurrency, min 1 |
| `get_max_concurrent_ftp_discovery_hosts()` | `int` — FTP discovery concurrency, min 1 |
| `validate_configuration()` | `bool` — checks API key, exclusion file, bounds |
| `resolve_target_countries(args_country)` | `list[str]` — parses comma-separated `--country` arg; empty list = global scan |
| `should_rescan_host(last_seen_days)` | `bool` — compares against `rescan_after_days` |
| `get_exclusion_list()` | `list[str]` — org names loaded from `conf/exclusion_list.json` |
| `get_ransomware_indicators()` | `list[str]` — patterns from `conf/ransomware_indicators.json` |

### 3.3 Two-Config System (GUI)

The GUI maintains a separation between application config and user preferences:

- **`conf/config.json`** — application settings, version-controlled, not overwritten on update
- **`~/.dirracuda/gui_settings.json`** — user preferences managed by `gui/utils/settings_manager.py` (window geometry, last-used template, theme, backend path)

Config resolution order for GUI settings: CLI arg → `gui_settings.json` value → `conf/config.json` fallback. This prevents app updates from resetting window positions or scan templates.

---

## 4. Scanning Workflows

### 4.1 SMB Workflow

**Entry point:** `cli/smbseek.py` → `create_unified_workflow(args)` → `UnifiedWorkflow.run(args)`

```mermaid
flowchart TD
    A[create_unified_workflow] --> B[load_config / create_output / create_workflow_database]
    B --> C[UnifiedWorkflow.run]
    C --> D[database.create_session\nscan_type=smbseek_unified]
    D --> E[_execute_discovery]
    E --> F[DiscoverOperation.execute\ncountry / rescan_all / rescan_failed / force_hosts / filters]
    F --> G[query_shodan\nbuild dork + org exclusions]
    G --> H[get_new_hosts_filter\nskip / rescan policy]
    H --> I[test_smb_authentication\nThreadPoolExecutor]
    I --> J{port 445 open?}
    J -->|no| K[skip]
    J -->|yes| L[test_smb_alternative\nSMBAdapter.probe_authentication]
    L -->|anon or guest OK| M[record DiscoverResult]
    L -->|fail| K
    M --> N[_execute_access_verification]
    N --> O[AccessOperation.execute]
    O --> P[enumerate_shares_detailed\nSMBAdapter.list_shares]
    P --> Q[per-share accessibility test]
    Q --> R[persist share_access / file_manifests]
    R --> S[WorkflowSummary rollup]
```

**Auth sequence** (`commands/discover/auth.py`):

1. `check_port(ip, 445)` — TCP connect with `port_check_timeout`
2. `test_smb_alternative(op, ip)` — routes through `SMBAdapter.probe_authentication()`:
   - Tries `smbprotocol` first 
   - Falls back to `impacket` in legacy mode
3. Auth cache: successful `auth_method` is cached in `op._auth_method_cache` per IP to avoid redundant probes

**Cautious mode** (`--cautious` flag):
- `require_signing=True` on the SMB `Connection`
- Dialects restricted to SMB 2.0.2, 2.1, 3.0.2, 3.1.1 (SMB1 rejected)
- Hosts that return unsigned sessions or require SMB1 are silently excluded

**Concurrency and throttling:**

`get_optimal_workers(op, total_hosts, max_concurrent)` scales the thread pool:
- ≤10 hosts: `min(3, max_concurrent, total_hosts)`
- >10 hosts: `min(max_concurrent, total_hosts, max_worker_cap)`

With `smart_throttling=true`, `throttled_auth_wait()` adjusts the rate-limit delay dynamically based on active thread count and adds ±20% jitter. With it disabled, `basic_throttled_auth_wait()` applies a flat `rate_limit_delay` between attempts.

Progress is reported on the first host, every 10 hosts, and the final host.

**Share enumeration** (`commands/access/share_enumerator.py`):

`enumerate_shares_detailed(op, ip, username, password)` calls `SMBAdapter.list_shares()`. Fatal status codes (`DEPENDENCY_MISSING`, `NORMALIZATION_ERROR`) abort enumeration for that host immediately rather than retrying.

### 4.2 FTP Workflow

**Entry point:** `cli/ftpseek.py` → `create_ftp_workflow(args)` → `FtpWorkflow.run(args)`

`FtpWorkflow` is a slim orchestrator. All stage logic lives in `commands/ftp/operation.py`.

**Stage 1 — Discovery** (`run_discover_stage`):

1. `query_ftp_shodan()` — Shodan dork: `port:21 "230 Login successful"` (+ optional country filter and custom filters)
2. Concurrent TCP port checks via `ThreadPoolExecutor` (up to `ftp.discovery.max_concurrent_hosts`, default 10)
3. Port-failed hosts are persisted immediately via `FtpPersistence.persist_discovery_outcomes_batch()`
4. Returns `(reachable_candidates, shodan_total)` — only reachable hosts proceed to stage 2

**Stage 2 — Access** (`run_access_stage`):

1. Concurrent `try_anon_login(ip, port, timeout=auth_timeout)` via `ThreadPoolExecutor` (up to `ftp.access.max_concurrent_hosts`, default 4)
2. On successful login: `try_root_listing(ip, port, timeout=listing_timeout, include_entries=True)` — returns `(ok, entry_count, reason, root_entries)`
3. All outcomes (success and failure) batched to `FtpPersistence.persist_access_outcomes_batch()` in a single commit

**Failure codes** returned in `FtpAccessOutcome.auth_status`:
- `connect_fail` — TCP connection refused or timeout
- `auth_fail` — anonymous login rejected
- `list_fail` — login succeeded but `LIST` command failed
- `timeout` — operation exceeded configured timeout

**Progress:** matches SMB cadence — `_should_report_progress(completed, total, batch_size=10)` and `_report_concurrent_progress()` emit identical-format lines.

**Rollup** (stdout markers parsed by `gui/utils/backend_interface/progress.py`):
```
📊 Hosts Scanned: N
🔓 Hosts Accessible: N
📁 Accessible Directories: N
🎉 FTP scan completed successfully
```
The success marker is only emitted on the non-error path; its absence signals failure to the GUI's progress parser.

### 4.3 HTTP Workflow

**Entry point:** `cli/httpseek.py` → `create_http_workflow(args)` → `HttpWorkflow.run(args)`

Structurally identical to FTP. Implementation lives in `commands/http/operation.py`.

**Shodan dork:** `http.title:"Index of /"` — locked in `commands/http/shodan_query.py`, not user-overridable.

**Verifier** checks both HTTP and HTTPS on the discovered port; `allow_insecure_tls` controls whether TLS cert errors are fatal. `is_index_page` flag on `http_access` records rows distinguishes confirmed open-directory indexes from other accessible responses.

### 4.4 Rescan Policies

`SMBSeekWorkflowDatabase.get_new_hosts_filter(shodan_ips, rescan_all, rescan_failed)` compares the incoming Shodan IP set against `smb_servers.last_seen`:

| Flag | Behavior |
|------|---------|
| (none) | Skip hosts seen within `workflow.rescan_after_days` (default 30 days) |
| `--rescan-failed` | Include hosts with `failure_logs` entries in addition to new hosts |
| `--rescan-all` | Scan everything Shodan returned regardless of last_seen |

FTP and HTTP have equivalent filtering via `FtpPersistence` and `HttpPersistence` (checked against `ftp_servers.last_seen` / `http_servers.last_seen`).

---

## 5. Database & Data Model

### 5.1 ER Diagram

```mermaid
erDiagram
    scan_sessions {
        int id PK
        text tool_name
        text scan_type
        datetime timestamp
        datetime started_at
        datetime completed_at
        text status
        int total_targets
        int successful_targets
        int failed_targets
        text country_filter
        text config_snapshot
    }

    smb_servers {
        int id PK
        text ip_address UK
        text host_type
        text country
        text auth_method
        datetime first_seen
        datetime last_seen
        int scan_count
        text status
    }

    share_access {
        int id PK
        int server_id FK
        int session_id FK
        text share_name
        bool accessible
        text auth_status
        text permissions
    }

    file_manifests {
        int id PK
        int server_id FK
        int session_id FK
        text share_name
        text file_path
        text file_name
        int file_size
        bool is_ransomware_indicator
        bool is_sensitive
    }

    vulnerabilities {
        int id PK
        int server_id FK
        int session_id FK
        text vuln_type
        text severity
        text title
        decimal cvss_score
        text cve_ids
        text status
    }

    host_user_flags {
        int server_id PK_FK
        bool favorite
        bool avoid
        text notes
    }

    host_probe_cache {
        int server_id PK_FK
        text status
        datetime last_probe_at
        int indicator_matches
        text snapshot_path
    }

    share_credentials {
        int id PK
        int server_id FK
        text share_name
        text username
        text password
        text source
    }

    failure_logs {
        int id PK
        int session_id FK
        text ip_address
        text failure_type
        text failure_reason
        int retry_count
        bool resolved
    }

    ftp_servers {
        int id PK
        text ip_address UK
        text host_type
        text country
        int port
        bool anon_accessible
        text banner
        datetime first_seen
        datetime last_seen
    }

    ftp_access {
        int id PK
        int server_id FK
        int session_id FK
        bool accessible
        text auth_status
        bool root_listing_available
        int root_entry_count
    }

    ftp_user_flags {
        int server_id PK_FK
        bool favorite
        bool avoid
        text notes
    }

    ftp_probe_cache {
        int server_id PK_FK
        text status
        datetime last_probe_at
        int indicator_matches
        int accessible_dirs_count
        int extracted
        text rce_status
    }

    http_servers {
        int id PK
        text ip_address
        int port
        text scheme
        text banner
        text title
        datetime first_seen
        datetime last_seen
        UNIQUE ip_address_port
    }

    http_access {
        int id PK
        int server_id FK
        int session_id FK
        bool accessible
        int status_code
        bool is_index_page
        int dir_count
        int file_count
        bool tls_verified
    }

    http_user_flags {
        int server_id PK_FK
        bool favorite
        bool avoid
        text notes
    }

    http_probe_cache {
        int server_id PK_FK
        text status
        datetime last_probe_at
        int indicator_matches
        int accessible_dirs_count
        int accessible_files_count
        int extracted
        text rce_status
    }

    smb_servers ||--o{ share_access : "server_id"
    smb_servers ||--o{ file_manifests : "server_id"
    smb_servers ||--o{ vulnerabilities : "server_id"
    smb_servers ||--o| host_user_flags : "server_id"
    smb_servers ||--o| host_probe_cache : "server_id"
    smb_servers ||--o{ share_credentials : "server_id"
    scan_sessions ||--o{ share_access : "session_id"
    scan_sessions ||--o{ file_manifests : "session_id"
    scan_sessions ||--o{ vulnerabilities : "session_id"
    scan_sessions ||--o{ failure_logs : "session_id (nullable)"
    ftp_servers ||--o{ ftp_access : "server_id"
    ftp_servers ||--o| ftp_user_flags : "server_id"
    ftp_servers ||--o| ftp_probe_cache : "server_id"
    scan_sessions ||--o{ ftp_access : "session_id (nullable)"
    http_servers ||--o{ http_access : "server_id"
    http_servers ||--o| http_user_flags : "server_id"
    http_servers ||--o| http_probe_cache : "server_id"
    scan_sessions ||--o{ http_access : "session_id (nullable)"
```

### 5.2 Schema Notes

**Protocol isolation.** SMB, FTP, and HTTP each have their own server registry (`smb_servers`, `ftp_servers`, `http_servers`). An IP can appear in all three. The `v_host_protocols` view resolves which protocols are present per IP:

```sql
SELECT ip_address, has_smb, has_ftp, has_http, protocol_presence
FROM v_host_protocols
WHERE ip_address = '1.2.3.4';
```

**`host_type` values:** `'S'` = SMB, `'F'` = FTP, `'H'` = HTTP.

**`scan_sessions.config_snapshot`** stores a JSON blob of the effective config at scan time for retrospective analysis.

**`failure_logs.session_id`** is nullable (`ON DELETE SET NULL`) so failure records survive session deletion.

**`share_credentials`** is populated by the Pry password audit tool. Unique index on `(server_id, share_name, source)`.

**`scan_sessions.scan_type`** values by tool:
- `smbseek_unified` — `cli/smbseek.py`
- `ftpseek` — `cli/ftpseek.py`
- `httpseek` — `cli/httpseek.py`

### 5.3 Views

| View | Purpose |
|------|---------|
| `v_active_servers` | Per-SMB-server summary: accessible share count, files discovered, open vulnerability count |
| `v_vulnerability_summary` | Aggregate vuln counts grouped by type and severity, sorted by CVSS severity tier |
| `v_scan_statistics` | Per-tool daily session stats: targets, success rate |
| `v_host_protocols` | Cross-protocol IP presence map (see §5.2) |

### 5.4 Database Layer Internals

**`tools/db_manager.py`:**
- `DatabaseManager` — owns the SQLite connection, exposes `execute_query()` (returns `list[dict]`)
- `SMBSeekDataAccessLayer` — wraps `DatabaseManager` with named query methods

**`shared/database.py`:**
- `SMBSeekWorkflowDatabase` — workflow-level operations: `create_session()`, `get_new_hosts_filter()`, `show_database_status()`
- Calls `run_migrations(db_path)` on construction before touching any tables

**`shared/db_migrations.py`:**
- `run_migrations()` is called on every CLI startup
- Migrations are additive only (all use `IF NOT EXISTS` guards); no destructive migrations
- FTP and HTTP sidecar tables were added as separate migrations to leave the SMB schema untouched

**`commands/ftp/operation.py` and equivalent HTTP file use `FtpPersistence` / `HttpPersistence`** (also in `shared/database.py`) which connect directly to the DB path without going through `SMBSeekWorkflowDatabase`.

---

## 6. Graphical User Interface

### 6.1 Entry Point and Component Hierarchy

`dirracuda` is the authoritative GUI entry point. `gui/main.py` is a deprecated legacy entry point; it prints a deprecation warning and redirects.

```
dirracuda
└─ Dirracuda GUI (gui/components/dashboard.py)
   ├─ UnifiedScanDialog (gui/components/unified_scan_dialog.py)
   │    └─ ScanManager (gui/utils/scan_manager.py)
   │         └─ BackendInterface (gui/utils/backend_interface/interface.py)
   │              ├─ ProcessRunner   — subprocess lifecycle
   │              ├─ ProgressParser  — stdout regex field matching
   │              ├─ ErrorParser     — stderr classification
   │              └─ MockOperations  — fake backend for --mock mode
   ├─ ServerListWindow (gui/components/server_list_window/)
   │    ├─ SMB tab
   │    ├─ FTP tab
   │    └─ HTTP tab
   ├─ DBToolsDialog (gui/components/db_tools_dialog.py)
   │    └─ DBToolsEngine (gui/utils/db_tools_engine.py)
   └─ [config editor, scan dialogs, browser windows, extract dialogs]
```

### 6.2 Thread Safety

Tkinter is not thread-safe. All GUI mutations from worker threads must go through `UIDispatcher` (`gui/utils/ui_dispatcher.py`):

```python
dispatcher = UIDispatcher(root)     # created at startup
dispatcher.schedule(widget.config, text="Updated")  # safe from any thread
```

Internally: `schedule()` pushes `(callback, args, kwargs)` to a `queue.Queue`. The dispatcher polls the queue via `root.after()` every 50ms (`POLL_INTERVAL_MS`), processing up to 20 items per tick (`MAX_ITEMS_PER_POLL`) to avoid blocking the main loop during bursts. `stop()` must be called before `root.destroy()`.

### 6.3 Scan Lifecycle (GUI → CLI)

1. User configures and starts a scan in `UnifiedScanDialog`
2. `ScanManager.start_scan()` launches the appropriate CLI script as a subprocess via `BackendInterface`
3. `ProgressParser` reads stdout line-by-line and extracts fields via regex patterns (matching the emoji-prefixed rollup lines emitted by workflows, e.g. `📊 Hosts Scanned: N`)
4. `ErrorParser` classifies stderr to distinguish expected failures from unexpected crashes
5. Cancellation: `ProcessRunner` sends SIGTERM and waits for graceful exit
6. `--mock` mode substitutes `MockOperations` for the subprocess, enabling GUI testing without a real backend

### 6.4 Dashboard Controls

| Control | Function |
|---------|---------|
| Start Scan | Opens `UnifiedScanDialog` (protocol selector + scan options) |
| Server List | Opens `ServerListWindow` with SMB / FTP / HTTP tabs |
| DB Tools | Opens `DBToolsDialog` |
| Configuration | Opens config editor |
| Dark/Light toggle | Switches ttkthemes theme; persisted in `gui_settings.json` |

### 6.5 Server List

Displays hosts from `smb_servers`, `ftp_servers`, `http_servers` in separate tabs. Per-row actions:

| Action | Backend |
|--------|---------|
| Copy IP | Clipboard |
| Probe | `probe_runner.py` (SMB) / `ftp_probe_runner.py` / `http_probe_runner.py` — runs a quick directory listing; result cached in `host_probe_cache` / `ftp_probe_cache` / `http_probe_cache` |
| Browse | Opens `SMBBrowserWindow` / `FtpBrowserWindow` / `HttpBrowserWindow` via `smb_browser.py` / `ftp_browser.py` / `http_browser.py` |
| Extract | `extract_runner.py` — downloads files per `file_collection` limits; optional ClamAV scan post-extract |
| Pry | `pry_runner.py` — wordlist-based password audit; stores found credentials in `share_credentials` |
| Favorite / Avoid / Compromised | Sets flags in `host_user_flags` / `ftp_user_flags` / `http_user_flags` |
| Delete | Cascades via FK `ON DELETE CASCADE` |

### 6.6 File Browser

All three protocol browsers are read-only. Navigation traverses directories up to `file_browser.max_depth` (12) with a max of `max_entries_per_dir` (5000) entries per listing. File viewing:
- Text files: decoded as UTF-8 (fallback to Latin-1) up to `viewer.max_view_size_mb` (5MB)
- Image files: displayed inline up to `viewer.max_image_size_mb` (15MB) / `max_image_pixels` (20M px)
- Binary files: hex view at 16 bytes/row

Downloads are staged to `file_browser.quarantine_root` (`~/.dirracuda/quarantine` by default). If `quarantine.use_tmpfs` is true, the quarantine root is a tmpfs mount of `tmpfs_size_mb` size.

### 6.7 Pry Password Audit

Pry is a proof-of-concept wordlist-based SMB credential tester. It is not a drop-in replacement for Hydra or Medusa — it lacks protocol-level optimisations and has limited error recovery.

Config keys under `pry`:
- `wordlist_path` — required; no default; empty string disables Pry
- `user_as_pass` — also try each username as its own password
- `stop_on_lockout` — abort on lockout detection
- `attempt_delay` — seconds between attempts per host
- `max_attempts` — 0 = unlimited

Found credentials are stored in `share_credentials` with `source='pry'`. The unique index on `(server_id, share_name, source)` means re-running Pry upserts rather than duplicates.

### 6.8 DB Tools Dialog

Backed by `gui/utils/db_tools_engine.py`. Capabilities:

- **Import/merge** — load an external `dirracuda.db`; conflict resolution is timestamp-based (most recent `last_seen` wins per IP)
- **Export/backup** — copy to dated file in `database.backup_directory`
- **Statistics** — server count by country, protocol breakdown
- **Maintenance** — SQLite VACUUM, integrity check (`PRAGMA integrity_check`), cascade-deletion preview before purging old sessions

---

## 7. Security Considerations

### 7.1 Operating Environment

The README recommends and these are worth repeating:
- Run in a VM, not on a primary workstation
- Route traffic through a VPN
- Isolate the scanning host from the rest of your network
- Never run as root

SMB scanning requires port 445 outbound. FTP requires 21 (and a passive data port range if the server uses passive mode). HTTP/HTTPS require 80/443.

### 7.2 SMB Mode Selection

| Mode | Dialects | Signing | Use When |
|------|---------|---------|----------|
| Default | SMB 1/2/3 (library default) | Not required | Broad discovery |
| Cautious (`--cautious`) | SMB 2.0.2, 2.1, 3.0.2, 3.1.1 | Required | Assessing targets where you care about session integrity |
| Legacy | SMB 1 permitted (library fallback) | Not required | Old targets that won't negotiate SMB2+ |

Cautious mode is implemented in `test_smb_auth()` (`commands/discover/auth.py`): sets `require_signing=True` and restricts `dialects` to the SMB2+ set on the `Connection` object.

### 7.3 File Handling

Downloaded files land in `quarantine_root` (default `~/.dirracuda/quarantine`). If `quarantine.use_tmpfs=true`, a tmpfs of `tmpfs_size_mb` is mounted at that path on Linux — files never touch persistent storage. Tmpfs setup requires appropriate OS permissions.

ClamAV integration (`clamav.enabled=true`, `backend=auto`) runs `clamscan` or connects to `clamd` (auto-detected) after extraction. Flagged files are moved to `clamav.known_bad_subdir` under `extracted_root`.

### 7.4 RCE Probe Limits

RCE analysis is disabled by default (`rce.enabled_default=false`). When enabled via `--check-rce`:
- Probe budget: 2 requests per host (`safe_active_budget.max_requests`)
- Per-host timeout: 5 seconds
- Jitter: 250ms between probes
- `intrusive_mode_enabled` is hardcoded off in `is_intrusive_mode_enabled()` unless explicitly set `true` in config — only do this for authorised active testing

### 7.5 Ethical Use

This tool is for authorised security research and auditing only. Running it against systems you do not own or lack explicit permission to test is illegal in most jurisdictions. The Shodan dorks target publicly indexed hosts; that does not constitute permission to access them.

---

## 8. Extensibility

### 8.1 Adding a New Protocol

The FTP and HTTP modules were added without touching the SMB codebase. The pattern:

1. **Command package** — create `commands/<proto>/` with:
   - `models.py` — dataclasses for candidates and outcomes (`<Proto>Candidate`, `<Proto>DiscoveryOutcome`, `<Proto>AccessOutcome`), plus `<Proto>DiscoveryError`
   - `shodan_query.py` — Shodan dork + `build_<proto>_query()`
   - `verifier.py` — `port_check()`, `try_auth()`, `try_listing()`
   - `operation.py` — `run_discover_stage(workflow)` and `run_access_stage(workflow, candidates)` following the FTP pattern exactly

2. **Workflow** — create `shared/<proto>_workflow.py` with `<Proto>Workflow` and `create_<proto>_workflow(args)` factory mirroring `shared/ftp_workflow.py`

3. **Database sidecar** — add `<proto>_servers`, `<proto>_access`, `<proto>_user_flags`, `<proto>_probe_cache` tables to `tools/db_schema.sql` using `CREATE TABLE IF NOT EXISTS`. Add a migration in `shared/db_migrations.py`.

4. **Persistence class** — add `<Proto>Persistence` to `shared/database.py` following `FtpPersistence`

5. **CLI entry point** — `cli/<proto>seek.py` with argparse and `create_<proto>_workflow().run(args)`

6. **GUI** — new scan dialog (`gui/components/<proto>_scan_dialog.py`), browser window (`gui/components/<proto>_browser_window.py`), probe runner (`gui/utils/<proto>_probe_runner.py`), probe cache helper (`gui/utils/<proto>_probe_cache.py`); add a tab to `ServerListWindow`

### 8.2 Adding RCE Signatures

Drop a YAML file into `signatures/rce_smb/`. The signature format and required fields are documented in `docs/guides/RCE_SIGNATURE_GUIDE.md`. The scanner (`shared/rce_scanner/scanner.py`) loads all `*.yaml` files from that directory at runtime.

The RCE feature is experimental; signatures you add will be loaded but the scoring and verdict pipeline is not production-ready.

### 8.3 Adding GUI Components

- **Simple dialog** — single file in `gui/components/`; follow `gui/components/scan_dialog.py` as the template
- **Complex multi-panel window** — use the `gui/components/server_list_window/` package pattern: a directory with `__init__.py` and an `actions/` sub-package for row-level operations
- All worker threads must route UI mutations through `UIDispatcher.schedule()` (see §6.2)
- New scan-related dialogs should use `ScanManager` for subprocess lifecycle rather than spawning processes directly

---

## 9. Glossary

| Term | Definition |
|------|-----------|
| **SMB** | Server Message Block — network file-sharing protocol (ports 445 / 139); versions 1, 2, 3 |
| **FTP** | File Transfer Protocol — port 21 control channel |
| **HTTP** | HyperText Transfer Protocol — used here to mean open directory listing pages served over HTTP/HTTPS |
| **Shodan** | Internet-wide scanner and search engine; Dirracuda uses its search API to discover candidate hosts |
| **Dork** | A Shodan search query string targeting specific service characteristics |
| **RCE** | Remote Code Execution — unintended arbitrary command execution on a remote host |
| **CVSS** | Common Vulnerability Scoring System — numerical severity score (0.0–10.0) |
| **CVE** | Common Vulnerabilities and Exposures — standardised vulnerability identifier (e.g. CVE-2017-0144) |
| **CLI** | Command-Line Interface |
| **GUI** | Graphical User Interface — the Tkinter dashboard |
| **ERD** | Entity-Relationship Diagram |
| **YAML** | YAML Ain't Markup Language — format used for RCE signature definitions |
| **NTLM** | NT LAN Manager — Microsoft authentication protocol used in SMB sessions |
| **tmpfs** | Temporary filesystem backed by RAM (Linux); used here for ephemeral quarantine storage |
| **ClamAV** | Open-source antivirus engine; used for optional post-extraction scanning |
| **Pry** | Dirracuda's built-in SMB wordlist password auditor (proof-of-concept) |
| **smbprotocol** | Pure-Python SMB2/3 library; primary SMB backend |
| **Impacket** | Python library with SMB1/2/3 support; fallback SMB backend and share enumeration backend |
| **Cautious mode** | SMB scan mode requiring SMB2+ and session signing; rejects SMB1 and unsigned sessions |
| **Legacy mode** | SMB scan mode that permits SMB1 negotiation |
| **`v_host_protocols`** | SQLite view resolving which protocols (SMB/FTP/HTTP) are present for each IP address |
