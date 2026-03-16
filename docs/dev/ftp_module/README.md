# FTP Module

Status: **MVP complete** — Cards 1–6 delivered and tested (2026-03-16).

This folder holds the FTP expansion plan and documentation for SMBSeek. The FTP module adds anonymous FTP discovery and browsing as a parallel path to the existing SMB pipeline.

## Capabilities

- Discover anonymous FTP servers via Shodan query (`port:21 "230 Login successful"`).
- Verify reachability, anonymous login, and root directory listing.
- Browse discovered FTP server directory trees from the GUI.
- Download individual files to a local quarantine directory (no execute permissions set).
- Save probe snapshots to `~/.smbseek/ftp_probes/<ip>.json` for offline indicator analysis.

## Locked Decisions

1. FTP is a separate module/workflow from SMB for MVP.
2. Dashboard has `Start SMB Scan` and `Start FTP Scan` buttons; only one scan runs at a time.
3. SMB and FTP are discovered separately.
4. If an IP has both services, the DB records coexist with protocol presence flags (`has_smb`, `has_ftp`, `both`).
5. MVP uses JSON probe snapshots + summary rows, not full normalized artifact tables.
6. Value/ranking filtering is deferred until after reliable MVP functionality.

## Architecture Snapshot

```text
+------------------------------------------------------+
|                      Dashboard                       |
|    [Start SMB Scan]     [Start FTP Scan]            |
+-----------------------------+------------------------+
                              |
        +---------------------+---------------------+
        |                                           |
 SMB workflow (existing)                     FTP workflow (new)
 smbseek CLI                                 ftpseek CLI
 smb_* tables                                ftp_* tables
 snapshots: ~/.smbseek/probes/               snapshots: ~/.smbseek/ftp_probes/
        |                                           |
        +---------------------+---------------------+
                              |
                     protocol presence layer
                   (has_smb / has_ftp / both)
```

## File Map (delivered)

| File | Card | Role |
|------|------|------|
| `gui/components/dashboard.py` | 1 | SMB/FTP scan split, FTP Servers button |
| `ftpseek` | 2 | FTP CLI entry point |
| `commands/ftp/` | 2–4 | FTP workflow, Shodan query, verifier, operation |
| `shared/ftp_workflow.py` | 2 | FTP workflow orchestration |
| `tools/db_schema.sql` | 3 | `ftp_servers` + `ftp_access` tables |
| `shared/db_migrations.py` | 3 | Idempotent FTP table migrations |
| `shared/database.py` (`FtpPersistence`) | 3 | FTP DB write layer |
| `gui/utils/database_access.py` | 3 | `get_ftp_servers()` read layer |
| `shared/ftp_browser.py` | 5 | FtpNavigator: list, download, cancel |
| `gui/utils/ftp_probe_cache.py` | 5 | JSON probe snapshot save/load/clear |
| `gui/utils/ftp_probe_runner.py` | 5 | run_ftp_probe(): one-level-deep walker |
| `gui/components/ftp_browser_window.py` | 5 | Tkinter FTP browser window |
| `gui/components/ftp_server_picker.py` | 5 | FTP server picker dialog |
| `conf/config.json.example` | 5 | `ftp_browser` config section |
| `gui/tests/test_ftp_browser.py` | 6 | FtpNavigator unit tests (12 tests) |
| `gui/tests/test_ftp_probe.py` | 6 | Probe cache + runner unit tests (6 tests) |

## Known MVP Limits

| Limitation | Scope | Deferred To |
|-----------|-------|-------------|
| Anonymous FTP only | `shared/ftp_browser.py`, `commands/ftp/verifier.py` | Post-MVP |
| Probe snapshot is 1-level deep | `gui/utils/ftp_probe_runner.py` | Post-MVP |
| No `ftp_files` / `ftp_shares` DB table | `shared/database.py` | Post-MVP |
| No content ranking / value scoring | `commands/ftp/operation.py` | Post-MVP |
| No batch folder download | `gui/components/ftp_browser_window.py` | Post-MVP |
| FTP server picker (not full server list window) | `gui/components/ftp_server_picker.py` | Post-MVP |
| No window focus/restore tracking in xsmbseek for FTP | `xsmbseek` | Post-MVP |
| Binary file preview / image viewer | `gui/components/ftp_browser_window.py` | Post-MVP |

## Planning Documents

- `FTP_MVP_ACTION_PLAN.md`: end-to-end phased delivery plan, dependencies, gates, and risks.
- `FTP_PHASE_TASK_CARDS.md`: Claude-ready phase task cards with Goal, Scope, DoD, and regression checks.
- `claude_plan/`: per-card implementation plans (01-card1.md through 06-card6.md).
