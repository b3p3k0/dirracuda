# Card 6: QA, Hardening, and Documentation

> Implementation plan for `docs/dev/ftp_module/claude_plan/06-card6.md`

---

## Revision Notes

| Rev | Date | Change |
|-----|------|--------|
| R1 | 2026-03-16 | Initial plan written after Card 5 implementation confirmed in repo state |
| R2 | 2026-03-16 | Fix 5 issues: pass gate baseline, DB column names, find_indicator_hits return type, Card 5 git state, README stale defect |
| R3 | 2026-03-16 | Fix 5 further issues: non-GUI command baseline expectation, Card 5 state in Context+R-C6-1+Step 10, README gap claim, snapshot key ip→ip_address, test_ftp_probe table column alignment |
| R4 | 2026-03-16 | Fix 4 final issues: preamble Card 5 state, R-C6-2 "Card 5→6 complete", README.md critical-files status, §4.1 section break |

---

## 1. Context

### Why this card exists

Cards 1–5 delivered a functional FTP MVP: dashboard split (Card 1), CLI skeleton (Card 2), DB schema/persistence (Card 3), Shodan + anonymous verification (Card 4), and probe snapshot + browser download (Card 5). The pipeline is end-to-end functional but has no FTP-specific automated tests, documentation that still says "planning active", and several known risk areas inherited from Card 5 that were deferred to Card 6.

Card 6 is a stabilization card: no new features, no schema changes. The deliverables are tests, doc updates, and a clean pass/fail record.

### Card 6 definition of done (verbatim from task cards)

1. Repeatable test results recorded.
2. SMB baseline behaviors validated post-merge.
3. FTP operator documentation complete enough for first users.

### Regression scope (verbatim from task cards)

1. Dashboard launch/stop controls.
2. SMB discovery, access, browse baseline.
3. FTP discovery, browse, download baseline.

### Out of scope (verbatim from task cards)

- Post-MVP refactor to unified normalized artifact DB.

---

## 2. Current-State Audit

### 2.1 What exists after Cards 1–5

| Component | File(s) | Status |
|-----------|---------|--------|
| Dashboard scan split | `gui/components/dashboard.py` | Committed (Card 1) |
| FTP CLI + workflow skeleton | `ftpseek`, `commands/ftp/`, `shared/ftp_workflow.py` | Committed (Card 2) |
| FTP DB schema + persistence | `tools/db_schema.sql`, `shared/db_migrations.py`, `shared/database.py` (`FtpPersistence`), `gui/utils/database_access.py` | Committed (Card 3) |
| FTP discovery pipeline | `commands/ftp/shodan_query.py`, `commands/ftp/verifier.py`, extended `commands/ftp/operation.py` | Committed (Card 4) |
| FTP browser engine | `shared/ftp_browser.py` | Committed (Card 5, aa415a3) |
| FTP probe cache | `gui/utils/ftp_probe_cache.py` | Committed (Card 5, aa415a3) |
| FTP probe runner | `gui/utils/ftp_probe_runner.py` | Committed (Card 5, aa415a3) |
| FTP browser window | `gui/components/ftp_browser_window.py` | Committed (Card 5, aa415a3) |
| FTP server picker | `gui/components/ftp_server_picker.py` | Committed (Card 5, aa415a3) |
| Dashboard FTP Servers button + xsmbseek wiring | `gui/components/dashboard.py`, `xsmbseek` | Committed (Card 5, aa415a3) |
| FTP browser config section | `conf/config.json.example` | Committed (Card 5, aa415a3) |
| README FTP sections | `README.md` | Committed — FTP discovery, ftpseek CLI, and FTP browser button guidance already present |

### 2.2 What is missing (Card 6 must create or update)

| Item | Target File(s) | Gap |
|------|---------------|-----|
| FTP unit tests — navigator parsing | `gui/tests/test_ftp_browser.py` | No coverage for `FtpNavigator` LIST parsing, MLSD fallback, limit enforcement |
| FTP unit tests — probe cache/runner | `gui/tests/test_ftp_probe.py` | No coverage for cache round-trip and snapshot structure validation |
| FTP module README update | `docs/dev/ftp_module/SUMMARY.md` | Still says "Status: planning active, implementation pending" |
| Known limitations table | `docs/dev/ftp_module/SUMMARY.md` | No explicit deferred-work record |

### 2.3 What is unstable / at risk

**Assumption marker used below:** Items marked `[Assumption]` are inferences from the code structure that must be verified before implementation.

| Risk ID | Area | Description |
|---------|------|-------------|
| R-C6-1 | ~~Card 5 not committed~~ | Resolved — Card 5 is committed (aa415a3); all FTP files are on the Python path. No action needed. |
| R-C6-2 | xsmbseek drill-down wiring | `[Assumption]` Card 5 Step 7 added `elif window_type == "ftp_server_list"` in `xsmbseek._open_drill_down_window`. If missing, FTP Servers button silently does nothing. Must verify before declaring Card 6 complete. |
| R-C6-3 | LIST fallback on real servers | Unix and DOS regex parsers in `shared/ftp_browser.py` were designed against spec, not tested against real-world FTP banners with extra whitespace, non-ASCII names, or mixed formats. |
| R-C6-4 | Thread/TclError on window close | `FtpBrowserWindow._on_close()` cancels ops and disconnects, but `after()` calls inside threads may still fire after destroy. Pattern should match `FileBrowserWindow` — needs verification. |
| R-C6-5 | Probe + interactive navigator overlap | Two `FtpNavigator` instances open on window creation (probe fires first, then interactive connects lazily). Servers with `max_per_ip=1` may reject the second. Probe failure should be non-fatal. `[Assumption]` `_run_probe_background` catches exceptions. |
| R-C6-6 | `conf/config.json.example` validity | Modified but unverified JSON. A syntax error breaks `_load_ftp_browser_config()` silently (falls back to defaults). Must lint before declaring ready. |

---

## 3. Defect Triage

### 3.1 Confirmed Defects

| ID | Description | Severity | Recommended Action |
|----|-------------|----------|--------------------|
| D1 | Zero FTP-specific automated tests | High | Write `gui/tests/test_ftp_browser.py` and `gui/tests/test_ftp_probe.py` before Card 6 exit |
| D2 | `docs/dev/ftp_module/SUMMARY.md` status stale | Low | Update status line and add capability summary |

### 3.2 Likely Risks (require verification — not confirmed defects)

| ID | Description | Severity | Recommended Action |
|----|-------------|----------|--------------------|
| R-C6-2 | xsmbseek drill-down missing `ftp_server_list` branch | High | Verify by running `./xsmbseek --mock` and clicking "FTP Servers" button; fix if absent |
| R-C6-3 | LIST fallback parser edge cases | Medium | Test against `pyftpdlib` local server (both MLSD and non-MLSD modes); fix regex if failures observed |
| R-C6-4 | TclError on window close during listing | Medium | Verify by opening browser, starting navigation, immediately closing; check console for `TclError` |
| R-C6-5 | Probe fails on per-IP connection limit | Low | Non-fatal by design; verify `_run_probe_background` has `except Exception` guard |
| R-C6-6 | `conf/config.json.example` invalid JSON | Medium | Run `python3 -m json.tool conf/config.json.example`; fix any errors |

### 3.3 Deferred Enhancements (out of scope — do not implement in Card 6)

| Item | Rationale |
|------|-----------|
| Full normalized FTP artifact DB (`ftp_files` / `ftp_shares` tables) | Post-MVP; Card 5 DoD uses JSON probe cache only |
| FTP content ranking / value scoring | Deferred from Card 5 |
| Recursive directory crawl in probe runner | MVP is one level deep |
| Folder/batch download | Files only in MVP |
| Full-featured FTP server list window (parity with `server_list_window/`) | `FtpServerPickerDialog` is the MVP launch path |
| Non-anonymous FTP authentication | Card 5 scope is anonymous only |
| Binary file preview / image viewer | Text preview is acceptable; image viewer deferred |
| xsmbseek window focus/restore tracking for FTP windows | Card 5 plan noted this as optional Card 6 work; defer |

---

## 4. Verification Strategy

### 4.1 Automated Tests — New Files to Create

#### `gui/tests/test_ftp_browser.py`

Tests `shared/ftp_browser.FtpNavigator` without a live FTP server (mock `ftplib.FTP`).

| Test Name | Coverage Target | Mock Technique |
|-----------|----------------|----------------|
| `test_list_dir_mlsd_success` | MLSD happy path returns correct `Entry` list | `patch("ftplib.FTP.mlsd")` yields structured facts |
| `test_list_dir_mlsd_fallback_to_list` | MLSD `error_perm` triggers LIST fallback | `mlsd` raises `ftplib.error_perm`; `retrlines` returns unix-format lines |
| `test_list_dir_unix_format` | Unix LIST line parser (`_list_via_LIST`) | Feed real-format unix `ls -l` strings; assert `Entry.is_dir`, `Entry.name`, `Entry.size` |
| `test_list_dir_dos_format` | DOS/Windows LIST line parser | Feed DOS `DIR`-format strings; assert `Entry.is_dir`, `Entry.name`, `Entry.size` |
| `test_list_dir_truncation` | `max_entries` limit enforces `ListResult.truncated=True` | MLSD yields N+1 entries where N = `max_entries` |
| `test_enforce_limits_depth` | `max_depth` raises `ValueError` | Call `_enforce_limits("/a/b/c/d/e/f/g/h/i/j/k/l/m")` with `max_depth=3` |
| `test_enforce_limits_path_length` | `max_path_length` raises `ValueError` | Pass path string of length `max_path_length + 1` |
| `test_normalize_path_root` | `_normalize_path("/")` returns `"/"` | Direct method call |
| `test_normalize_path_trailing_slash` | `_normalize_path("/foo/bar/")` returns `"/foo/bar"` | Direct method call |
| `test_download_file_too_large` | `get_file_size` returns > `max_file_bytes` → raises `FtpFileTooLargeError` | Mock `SIZE` response |
| `test_download_cancel_clears_connection` | Cancel during RETR sets `self._ftp = None` | Patch `retrbinary` to call cancel before raising `FtpCancelledError`; assert `nav._ftp is None` |
| `test_ensure_connected_reconnects` | NOOP failure triggers reconnect | `voidcmd("NOOP")` raises; assert `connect()` called again |

**File pattern** (mirrors existing tests):

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from unittest.mock import patch, MagicMock, call
from shared.ftp_browser import FtpNavigator, FtpCancelledError, FtpFileTooLargeError
```

---

#### `gui/tests/test_ftp_probe.py`

Tests cache round-trip and snapshot format produced by `run_ftp_probe()`.

| Test Name | Coverage Target | Mock Technique |
|-----------|----------------|----------------|
| `test_cache_save_and_load` | `save_ftp_probe_result` + `load_ftp_probe_result` round-trip equality | `tmp_path` fixture; no mocking needed |
| `test_cache_clear` | `clear_ftp_probe_result` removes file; load returns `None` after clear | `tmp_path` fixture |
| `test_cache_ip_sanitization` | IP `"1.2.3.4"` maps to filename `"1.2.3.4.json"` with no path traversal characters | `tmp_path` fixture |
| `test_snapshot_protocol_field` | `run_ftp_probe()` snapshot has `"protocol": "ftp"` | Mock `FtpNavigator`; fake `list_dir` returns 3 `Entry` objects |
| `test_snapshot_share_name` | Snapshot `["shares"][0]["share"] == "ftp_root"` | Same mock |
| `test_snapshot_probe_patterns_compatible` | `find_indicator_hits(snapshot, [])` returns `{"is_suspicious": False, "matches": []}` | Synthetic snapshot dict; no network needed |

### 4.2 Manual GUI Checks

Execute in order. Each requires the GUI to be visible (use `xvfb-run` or a real display).

1. **Smoke test**: `./xsmbseek --mock` launches without error; both SMB and FTP scan buttons visible.
2. **FTP Servers button**: Click header "📡 FTP Servers" button → `FtpServerPickerDialog` opens.
3. **Picker with mock data**: Verify picker shows table headers (ip, port, country, banner, last_seen); filter field is functional.
4. **Browser launch**: Double-click a picker row → `FtpBrowserWindow` opens and attempts connection.
5. **Navigation**: Navigate a directory, click Up — breadcrumb updates, parent listing loads.
6. **Download**: Select a file → Download → file appears at expected quarantine path; `activity.log` written.
7. **Cancel**: Start a download, click Cancel within 2s → partial file removed; subsequent navigation works.
8. **Close during listing**: Open browser, start navigation in a slow directory, immediately close window → no `TclError` in stderr.
9. **Probe snapshot**: After window open, verify `~/.smbseek/ftp_probes/<ip>.json` exists and is valid JSON.
10. **SMB scan smoke**: Start/stop SMB scan cycle from dashboard — no regression.

### 4.3 SMB Regression Matrix

All items must pass unchanged. Run the automated suite first; use manual checks for GUI-only items.

| # | Check | Method |
|---|-------|--------|
| S1 | All existing `gui/tests/` pass | Automated |
| S2 | All existing `shared/tests/` pass | Automated |
| S3 | `./xsmbseek --mock` launches and shows SMB scan controls | Manual |
| S4 | SMB scan start → progress lines appear in dashboard log | Manual |
| S5 | SMB scan stop → "Stopped" state reached; buttons re-enable | Manual |
| S6 | SMB server list window opens for an SMB host | Manual (mock) |
| S7 | `FileBrowserWindow` opens, navigates, downloads to quarantine | Manual |
| S8 | SMB probe snapshot saves to `~/.smbseek/probes/` | Manual |
| S9 | DB tools dialog opens without error | Manual |
| S10 | External scan lock detection disables scan buttons | Manual (mock) |

### 4.4 FTP Regression Matrix

Covers all FTP capabilities delivered in Cards 1–5.

| # | Check | Method |
|---|-------|--------|
| F1 | New FTP unit tests pass (test_ftp_browser.py) | Automated |
| F2 | New FTP unit tests pass (test_ftp_probe.py) | Automated |
| F3 | FTP scan button visible and separate from SMB button | Manual |
| F4 | FTP scan starts → progress lines stream to dashboard | Manual |
| F5 | FTP scan completes → `ftp_servers` table populated | Manual or DB query |
| F6 | FTP server count metric card shows correct count | Manual |
| F7 | "📡 FTP Servers" button → `FtpServerPickerDialog` opens | Manual |
| F8 | Picker rows populated from `get_ftp_servers()` | Manual |
| F9 | Double-click picker row → `FtpBrowserWindow` opens | Manual |
| F10 | Browser lists root `/` on connect | Manual |
| F11 | Navigate: double-click directory enters subdirectory | Manual |
| F12 | Navigate: Up button returns to parent | Manual |
| F13 | Download: file appears at correct quarantine path | Manual |
| F14 | Download: file is NOT executable (`chmod & 0o666` applied) | Manual (`ls -l`) |
| F15 | Download: `activity.log` entry written | Manual (`cat`) |
| F16 | Probe snapshot: `~/.smbseek/ftp_probes/<ip>.json` exists | Manual |
| F17 | Probe snapshot: valid JSON, `protocol=ftp`, `share=ftp_root` | Manual (`python3 -m json.tool`) |
| F18 | Cancel: download stops within 5s, partial file removed | Manual |
| F19 | Cancel: subsequent navigation works in same window | Manual |
| F20 | Close: no `TclError` in console on close-during-listing | Manual |
| F21 | GUI non-frozen during listing (runs in thread) | Manual (click other controls during list) |
| F22 | `conf/config.json.example` is valid JSON | Automated command |

### 4.5 Pass/Fail Gates

Card 6 is complete only when ALL of the following are met:

- [ ] Automated tests introduce no new failures vs. baseline (pre-existing baseline: 125 passed, 2 failed — `test_rce_reporter`, `test_rce_verdicts`): `S1`, `S2`, `F1`, `F2`
- [ ] All SMB regression matrix items pass: `S3`–`S10`
- [ ] All FTP regression matrix items pass: `F3`–`F22`
- [ ] `docs/dev/ftp_module/SUMMARY.md` updated (no "planning active" in status)
- [ ] `README.md` FTP usage section present and accurate
- [ ] Known MVP limits table published (see Section 6.3)

---

## 5. Command Plan

### 5.1 Environment Check

```bash
# Verify venv
/home/kevin/venvs/smbseek/venv-desktop/bin/python --version
# Expected: Python 3.x.x (3.8+)

# Verify xvfb-run availability (preferred for GUI tests)
which xvfb-run
# If absent, use fallback (Section 5.2 Fallback Path)

# Validate config JSON (F22)
/home/kevin/venvs/smbseek/venv-desktop/bin/python -m json.tool conf/config.json.example > /dev/null
# Expected: silent success (exit code 0); any output = syntax error to fix
```

### 5.2 Automated Test Runs

**Primary path (xvfb-run available):**

```bash
cd /home/kevin/Documents/_Code/git/smbseek
xvfb-run -a /home/kevin/venvs/smbseek/venv-desktop/bin/python \
  -m pytest gui/tests/ shared/tests/ -v \
  --cov=gui/components --cov=gui/utils --cov=shared \
  2>&1 | tee /tmp/card6_test_run.txt
```

Expected: test count increases by the new FTP tests; failure count stays at 2 (pre-existing `test_rce_reporter`, `test_rce_verdicts` only); coverage output shows `ftp_browser.py`, `ftp_probe_cache.py`, `ftp_probe_runner.py` covered.

**Fallback path (no xvfb-run):**

```bash
Xvfb :99 -screen 0 1024x768x24 &
export DISPLAY=:99
/home/kevin/venvs/smbseek/venv-desktop/bin/python \
  -m pytest gui/tests/ shared/tests/ -v \
  --cov=gui/components --cov=gui/utils --cov=shared \
  2>&1 | tee /tmp/card6_test_run.txt
```

If `Xvfb` is also unavailable, run GUI checks on a real display and keep automated execution to non-GUI tests (next command).

**Non-GUI tests only (no display required):**

```bash
/home/kevin/venvs/smbseek/venv-desktop/bin/python \
  -m pytest shared/tests/ gui/tests/test_ftp_browser.py gui/tests/test_ftp_probe.py \
  gui/tests/test_db_tools_engine.py -v \
  2>&1 | tee /tmp/card6_nonui_test_run.txt
```

Expected: all pass without display. New FTP tests in `test_ftp_browser.py` and `test_ftp_probe.py` do not require a display (no Tkinter in those files). `test_rce_reporter` and `test_rce_verdicts` are excluded here because they are known pre-existing failures unrelated to Card 6.

### 5.3 Specific Verification Commands

**Probe snapshot structure (F16, F17):**

```bash
cat ~/.smbseek/ftp_probes/127.0.0.1.json | \
  /home/kevin/venvs/smbseek/venv-desktop/bin/python -m json.tool | head -20
# Expected: {"ip_address": "127.0.0.1", "port": 21, "protocol": "ftp", "shares": [{"share": "ftp_root", ...}], ...}
```

**Quarantine path and permissions (F13, F14, F15):**

```bash
ls -lh ~/.smbseek/quarantine/<ip>/$(date +%Y%m%d)/ftp_root/
# Expected: file present, permissions -rw-rw-r-- (no execute bit)
cat ~/.smbseek/quarantine/<ip>/$(date +%Y%m%d)/activity.log
# Expected: timestamped download entry
```

**DB table populated (F5):**

```bash
sqlite3 smbseek.db "SELECT ip_address, port, anon_accessible, status FROM ftp_servers LIMIT 10;"
# Expected: rows present after FTP scan; anon_accessible=1 for verified hosts
```

**FTP server count metric (F6):**

```bash
sqlite3 smbseek.db "SELECT COUNT(*) FROM ftp_servers WHERE status='active' AND anon_accessible=1;"
# Expected: non-zero count after a scan
```

**probe_patterns compatibility check:**

```bash
/home/kevin/venvs/smbseek/venv-desktop/bin/python -c "
import sys; sys.path.insert(0, '.')
from gui.utils.ftp_probe_cache import load_ftp_probe_result
from gui.utils.probe_patterns import find_indicator_hits
snap = load_ftp_probe_result('127.0.0.1')
if snap:
    hits = find_indicator_hits(snap, [])
    print('OK — is_suspicious:', hits['is_suspicious'], '| matches:', hits['matches'])
else:
    print('No snapshot found — run a browser session first')
"
# Expected: OK — is_suspicious: False | matches: []
```

**quarantine.build_quarantine_path sanity check:**

```bash
/home/kevin/venvs/smbseek/venv-desktop/bin/python -c "
import sys; sys.path.insert(0, '.')
from shared.quarantine import build_quarantine_path
p = build_quarantine_path('1.2.3.4', 'ftp_root', purpose='ftp')
print(p)
"
# Expected: a Path under ~/.smbseek/quarantine/1.2.3.4/YYYYMMDD/ftp_root
```

### 5.4 Documentation Validation

```bash
# Verify README.md has FTP section
grep -n "FTP" README.md | head -20
# Expected: lines covering ftpseek CLI usage and xsmbseek FTP browser

# Verify ftp_module README no longer says "planning active"
grep "planning active" docs/dev/ftp_module/SUMMARY.md
# Expected: no output (string removed)

# Confirm known limits table present
grep -n "Known MVP Limits\|Deferred" docs/dev/ftp_module/SUMMARY.md
# Expected: matching lines
```

---

## 6. Documentation Hardening Plan

### 6.1 Files to Update

| File | Change Required |
|------|----------------|
| `docs/dev/ftp_module/SUMMARY.md` | Update status line (remove "planning active"); add capability summary, architecture note, known MVP limits table |
| `README.md` | Already has FTP discovery, `ftpseek` CLI, and FTP browser button guidance. No substantive additions required — verify existing content is accurate after Card 6 testing; update only if specific inaccuracies are found. |

**No changes to:**
- `docs/dev/XSMBSEEK_DEVNOTES.md` — FTP module is too new for deep integration; defer to post-MVP.
- `docs/guides/XSMBSEEK_USER_GUIDE.md` — Defer; only update root README for MVP.
- `CLAUDE.md` — FTP module is not yet stable enough to add as a critical-file reference.

### 6.2 Operator-Facing Capability/Limitation Statements

Include the following statements verbatim (or equivalently) in `README.md` and/or `docs/dev/ftp_module/SUMMARY.md`:

**Capabilities:**
- Discover anonymous FTP servers via Shodan query (`port:21 "230 Login successful"`).
- Verify reachability, anonymous login, and root directory listing.
- Browse discovered FTP server directory trees from the GUI.
- Download individual files to a local quarantine directory (no execute permissions set).
- Save probe snapshots to `~/.smbseek/ftp_probes/<ip>.json` for offline indicator analysis.

**Known limitations (MVP):**
- Anonymous FTP only; authenticated FTP is not supported.
- Probe snapshot is one directory level deep; full recursive crawl requires manual navigation.
- No per-file DB persistence; file metadata is in the JSON probe cache only.
- Batch/folder download not supported; single file at a time only.
- FTP server list is a lightweight picker, not the full-featured server list window (SMB parity deferred).

### 6.3 Known MVP Limits Table

This table must appear in `docs/dev/ftp_module/SUMMARY.md` as a permanent record:

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

---

## 7. Exit Criteria

### 7.1 Ordered Implementation Checklist

Execute in order. Mark each step complete before starting the next.

- [ ] **Step 0 — Pre-flight verification:** Run `python3 -m json.tool conf/config.json.example`. Confirm `xsmbseek` has `elif window_type == "ftp_server_list"` branch. Fix either issue before continuing.
- [ ] **Step 1 — Write `gui/tests/test_ftp_browser.py`:** Implement 12 tests listed in Section 4.1. Run non-GUI test suite; confirm all pass.
- [ ] **Step 2 — Write `gui/tests/test_ftp_probe.py`:** Implement 6 tests listed in Section 4.1. Run non-GUI test suite; confirm all pass.
- [ ] **Step 3 — Full automated suite:** Run full test suite via `xvfb-run` (or fallback path). Zero new failures required.
- [ ] **Step 4 — Manual GUI walkthrough:** Execute all items in Section 4.2 (Manual GUI Checks 1–10). Record pass/fail for each.
- [ ] **Step 5 — SMB regression sign-off:** Confirm all S1–S10 items pass.
- [ ] **Step 6 — FTP regression sign-off:** Confirm all F1–F22 items pass. Fix any defects before continuing.
- [ ] **Step 7 — Update `docs/dev/ftp_module/SUMMARY.md`:** Status, capability summary, MVP limits table (Section 6.3).
- [ ] **Step 8 — Verify `README.md`:** Confirm existing FTP sections (line ~78) are still accurate post-testing; update only if specific inaccuracies are found.
- [ ] **Step 9 — Final test run:** Re-run full suite to confirm doc/config edits introduced no regressions.
- [ ] **Step 10 — Commit:** Stage and commit Card 6 changes only (Card 5 is already committed at aa415a3). Changes include: new test files, `docs/dev/ftp_module/SUMMARY.md` update, and any targeted fixes from the verification steps.

### 7.2 Rollback / Safety Notes

| Action | Risk | Rollback |
|--------|------|---------|
| Writing new test files | Low — additive only | Delete the new test file; no other code affected |
| Updating `docs/dev/ftp_module/SUMMARY.md` | Low | `git checkout -- docs/dev/ftp_module/SUMMARY.md` |
| Any fix to `conf/config.json.example` | Medium — shared config | `git diff conf/config.json.example` before editing; `git checkout -- conf/config.json.example` to revert |
| Any fix to `xsmbseek` (drill-down wiring) | Medium — entry point | `git diff xsmbseek` before editing; test with `--mock` after any change |
| Any fix to `shared/ftp_browser.py` (R-C6-3, R-C6-4) | Medium — core navigator | Run `test_ftp_browser.py` suite after any fix; verify all 12 tests still pass |

---

## 8. Critical Files Table

| File | Status | Card 6 Role |
|------|--------|-------------|
| `shared/ftp_browser.py` | Committed (Card 5) | Subject of unit tests in `test_ftp_browser.py`; may need fixes for R-C6-3, R-C6-4 |
| `gui/utils/ftp_probe_cache.py` | Committed (Card 5) | Subject of `test_ftp_probe.py` cache tests |
| `gui/utils/ftp_probe_runner.py` | Committed (Card 5) | Subject of `test_ftp_probe.py` snapshot tests |
| `gui/components/ftp_browser_window.py` | Committed (Card 5) | Manual GUI check target; thread/cancel behavior |
| `gui/components/ftp_server_picker.py` | Committed (Card 5) | Manual GUI check target |
| `xsmbseek` | Committed (Card 5) | Verify `ftp_server_list` drill-down branch present |
| `conf/config.json.example` | Committed (Card 5) | Lint before use; fix if invalid JSON |
| `gui/tests/test_ftp_browser.py` | Does not exist | Create in Step 1 |
| `gui/tests/test_ftp_probe.py` | Does not exist | Create in Step 2 |
| `docs/dev/ftp_module/SUMMARY.md` | Stale | Update in Step 7 |
| `README.md` | Committed — FTP content present | Verify accuracy in Step 8; update only if inaccuracies found |

---

## Assumptions

| Assumption | How to Validate |
|------------|----------------|
| All Card 5 FTP files are importable from repo root (sys.path) | Run `python3 -c "from shared.ftp_browser import FtpNavigator"` from repo root |
| `FtpBrowserWindow._run_probe_background` wraps probe call in `except Exception` | Read `gui/components/ftp_browser_window.py` at `_run_probe_background()` implementation (near the bottom of the file) |
| `xsmbseek` stores `db_reader` as `self.db_reader` accessible in `_open_drill_down_window` | Read `xsmbseek` for `_open_drill_down_window` method at impl time |
| `build_quarantine_path` still accepts `purpose="ftp"` keyword arg | Run sanity check from Section 5.3 |
| `find_indicator_hits` does not branch on share name value | Run probe_patterns compat check from Section 5.3 |
