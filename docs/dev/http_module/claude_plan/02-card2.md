# Card 2 Implementation Plan: HTTP Workflow + CLI Skeleton

Status: COMPLETE

---

## Objective

Wire a real HTTP scan skeleton path (launchable from GUI) with
parser-compatible progress output, while preserving SMB/FTP behaviour.

---

## Files Changed

| File | Action | Rationale |
|------|--------|-----------|
| `httpseek` | Created | HTTP CLI entrypoint; mirrors `ftpseek` |
| `shared/http_workflow.py` | Created | Skeleton workflow; emits GUI-parseable progress + success marker |
| `commands/http/__init__.py` | Created | Package init |
| `commands/http/models.py` | Created | `HttpDiscoveryError`; stub dataclass for Card 4 |
| `commands/http/operation.py` | Created | Skeleton stubs: `run_discover_stage`, `run_access_stage` (return 0, emit info lines) |
| `gui/utils/backend_interface/interface.py` | Modified | Added `http_cli_script`, `_build_http_cli_command()`, `run_http_scan()` |
| `gui/utils/backend_interface/mock_operations.py` | Modified | Added `mock_http_scan_operation()` |
| `gui/utils/scan_manager.py` | Modified | Added `start_http_scan()`, `_http_scan_worker()` |
| `gui/components/dashboard.py` | Modified | Replaced Card 1 placeholder with real `scan_manager.start_http_scan()` call |
| `gui/utils/backend_interface/progress.py` | Modified | Added `"🎉 HTTP scan completed successfully"` to success-detection block |

---

## Key Design Decisions

- `httpseek` script is a direct copy of `ftpseek` with FTP → HTTP symbol
  substitutions; no shared code path changes.
- `HttpWorkflow.run()` emits the same rollup line format as `FtpWorkflow.run()`
  (`📊 Hosts Scanned:`, `🔓 Hosts Accessible:`, `📁 Accessible Directories:`)
  so `parse_final_results()` in `progress.py` picks them up without changes.
- Config overrides in `_http_scan_worker()` use namespace `"http"` (not `"ftp"`),
  matching the key structure Card 3 will introduce in `conf/config.json`.
- TLS flags (`verify_http`, `verify_https`, `allow_insecure_tls`) and
  `bulk_probe_enabled` are forwarded into config overrides as pass-through stubs;
  the skeleton workflow ignores them (behavior in Card 4).
- No DB writes in Card 2 — that is Card 3 scope.
- `httpseek` is placed in repo root alongside `smbseek` and `ftpseek`.

---

## Verification Results

### Automated regression gate
```
set -o pipefail && xvfb-run -a python -m pytest gui/tests/ shared/tests/ --tb=no -q
```
- **232 passed, 15 failed** — identical failure set to baseline (all pre-existing,
  none introduced by Card 2).
- Pre-existing failures: `test_ftp_state_tables` (3), `test_timestamp_canonicalization` (6),
  `test_ftp_scan_dialog` (2), `test_ftp_state_tables` host-type/backfill (4).

### HTTP smoke assertions
```
AUTOMATED: PASS
```
- `python httpseek --help` → exits 0 ✓
- `python httpseek --country US --verbose` → exits 0 ✓
- Output contains `🎉 HTTP scan completed successfully` ✓

### Manual GUI checks
```
MANUAL: PENDING
```
Checklist for human verification:
- [ ] `./xsmbseek` → HTTP scan button → dialog → Start → scan button enters "scanning" state
- [ ] Log panel shows `[1/2] HTTP Discovery` then `[2/2] HTTP Access Verification`
- [ ] Scan completes cleanly, button returns to idle, no error dialogs
- [ ] FTP scan still launches and completes (no regression)
- [ ] SMB scan still launches and completes (no regression)
- [ ] `./xsmbseek --mock` → HTTP scan → progress bar animates → completes

```
OVERALL: PASS (automated) / PENDING (manual)
```
