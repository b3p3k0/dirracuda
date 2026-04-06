# C7: Full Validation + Rollback Drill — Execution Plan

## Context

C1–C6 delivered the full ClamAV integration: scanner adapter, post-processor seam, bulk extract
integration, promotion/known-bad routing, results dialog + session mute, and config dialog controls.
C7 closes the project by producing durable evidence (VALIDATION_REPORT.md) and a tested rollback
procedure (ROLLBACK_RUNBOOK.md). No new code is written; C7 is purely validation and documentation.

---

## Issue

Bulk-path integration has no consolidated pass/fail evidence and no documented rollback path.
Without these, the feature is not safely shippable: an operator encountering a defect has no
structured recovery procedure, and there is no audit trail that all acceptance criteria were met.

## Root cause

C7 was intentionally deferred until C1–C6 were stable. The gap is documentation and drill, not code.

## Plan

### Phase A — Automated validation

Run the command matrix below in order. Record every command, timestamp, result code, and key output
snippet in VALIDATION_REPORT.md using the evidence table format specified below.

### Phase B — Manual HI validation

Execute each HI scenario in sequence. For each: set up preconditions, run, observe, record
PASS/FAIL with notes. Capture in a HI Scenarios table in VALIDATION_REPORT.md.

### Phase C — Rollback drill

Execute the soft rollback, verify behavior, restore, then document all steps in ROLLBACK_RUNBOOK.md.
Hard rollback is documented but not executed (destructive).

### Phase D — Write artifacts

Fill VALIDATION_REPORT.md and ROLLBACK_RUNBOOK.md to the schemas below.

---

## Files to change (C7 only)

| Action | File |
|--------|------|
| Create | `docs/dev/clamav_intergation/VALIDATION_REPORT.md` |
| Create | `docs/dev/clamav_intergation/ROLLBACK_RUNBOOK.md` |

No source files are modified in C7.

---

## Validation commands

### 1. Syntax checks (targeted — run first)

```bash
python3 -m py_compile shared/clamav_scanner.py
python3 -m py_compile shared/quarantine_postprocess.py
python3 -m py_compile shared/quarantine_promotion.py
python3 -m py_compile gui/utils/extract_runner.py
python3 -m py_compile gui/utils/session_flags.py
python3 -m py_compile gui/components/clamav_results_dialog.py
python3 -m py_compile gui/components/app_config_dialog.py
python3 -m py_compile gui/components/dashboard.py
python3 -m py_compile gui/components/server_list_window/actions/batch.py
python3 -m py_compile gui/components/server_list_window/actions/batch_operations.py
python3 -m py_compile gui/components/server_list_window/actions/batch_status.py
python3 -m py_compile shared/config.py
```

### 2. Unit tests — C1–C6 new test files (targeted)

```bash
./venv/bin/python -m pytest shared/tests/test_clamav_scanner.py        -v --tb=short
./venv/bin/python -m pytest shared/tests/test_quarantine_postprocess.py -v --tb=short
./venv/bin/python -m pytest shared/tests/test_quarantine_promotion.py   -v --tb=short
./venv/bin/python -m pytest gui/tests/test_extract_runner_clamav.py     -v --tb=short
./venv/bin/python -m pytest gui/tests/test_clamav_results_dialog.py     -v --tb=short
./venv/bin/python -m pytest gui/tests/test_app_config_dialog_clamav.py  -v --tb=short
```

### 3. Regression — full gui + shared suites

```bash
xvfb-run -a ./venv/bin/python -m pytest gui/tests/ shared/tests/ -q --tb=short 2>&1 | tee /tmp/c7_regression.txt
tail -20 /tmp/c7_regression.txt
```

### 4. Config round-trip smoke test

Asserts key presence and types only — not default values — to avoid false failures on
non-default user configs.

```bash
./venv/bin/python -c "
from shared.config import SMBSeekConfig
c = SMBSeekConfig()
cfg = c.get_clamav_config()
assert isinstance(cfg['enabled'], bool),         'enabled must be bool'
assert isinstance(cfg['backend'], str),          'backend must be str'
assert isinstance(cfg['timeout_seconds'], int),  'timeout_seconds must be int'
assert isinstance(cfg['show_results'], bool),    'show_results must be bool'
assert isinstance(cfg['extracted_root'], str),   'extracted_root must be str'
assert isinstance(cfg['known_bad_subdir'], str), 'known_bad_subdir must be str'
print('config smoke: PASS')
print(cfg)
"
```

### 5. Disabled-path behavior gate

```bash
./venv/bin/python -m pytest gui/tests/test_extract_runner_clamav.py -k "disabled_path" -q --tb=short
```

### 6. Session flags reset gate

Two subprocess invocations are required — a single process cannot simulate a restart.

```bash
# Process 1: set, verify, clear
./venv/bin/python -c "
from gui.utils.session_flags import set_flag, get_flag, clear_flag, CLAMAV_MUTE_KEY
assert get_flag(CLAMAV_MUTE_KEY) == False, 'must start unset'
set_flag(CLAMAV_MUTE_KEY, True)
assert get_flag(CLAMAV_MUTE_KEY) == True,  'must be set after set_flag'
clear_flag(CLAMAV_MUTE_KEY)
assert get_flag(CLAMAV_MUTE_KEY) == False, 'must be cleared after clear_flag'
print('session flags process-1 gate: PASS')
"

# Process 2: fresh interpreter = simulated restart; flag must be absent
./venv/bin/python -c "
from gui.utils.session_flags import get_flag, CLAMAV_MUTE_KEY
assert get_flag(CLAMAV_MUTE_KEY) == False, 'new process must not inherit flag'
print('session flags process-2 (restart) gate: PASS')
"
```

> **Unblock command if py_compile fails on any file:**
> ```bash
> python3 -c "import ast; ast.parse(open('PATH').read())" && echo OK
> ```
> Expected output: `OK`. If that also fails, the file has a syntax error — fix in source before recording.

> **Unblock command if pytest import fails:**
> ```bash
> ./venv/bin/python -c "import shared.clamav_scanner; print('import ok')"
> ```
> Expected output: `import ok`. Missing import means venv is not activated or a dependency is absent.

---

## Evidence structure for VALIDATION_REPORT.md

Every automated check gets one row in this table:

```markdown
| # | Command (abbreviated) | Timestamp | Result | Key output snippet | Notes |
|---|----------------------|-----------|--------|-------------------|-------|
| 1 | py_compile shared/clamav_scanner.py | 2026-03-XX HH:MM | PASS | (no output = pass) | |
...
```

Full format per row:
- **Command**: exact shell command (abbreviated to fit — full commands in Appendix A)
- **Timestamp**: `date +"%Y-%m-%d %H:%M:%S"` captured before running
- **Result**: `PASS` or `FAIL`
- **Key output snippet**: last 3–5 lines of stdout/stderr, or test summary line
- **Notes/assumptions**: anything that affected the run (e.g., `clamd not running — expected`)

---

## Manual HI scenarios

All six require a live GUI session (`./xsmbseek --mock` is acceptable for dialog/mute scenarios;
real extract paths require `./xsmbseek` with a reachable SMB host or local quarantine fixture).

### HI-1: Clean file pathing

**Preconditions:** ClamAV enabled, `clamscan`/`clamdscan` available on PATH, test host with known
clean files accessible.
**Steps:**
1. Open App Config → ClamAV tab. Set `enabled=true`, backend=auto.
2. Run dashboard post-scan bulk extract on a host returning 1–3 small clean files.
3. After extract completes, verify results dialog shows clean count > 0, infected = 0.
4. Check filesystem: files must be under `~/.dirracuda/extracted/<host>/<date>/...`.
5. Check quarantine dir: those files must no longer be present there.

**PASS criteria:** files in extracted root, none in quarantine, dialog shows correct totals.

### HI-2: Infected file pathing (EICAR)

**Preconditions:** ClamAV enabled, `clamscan` available. Place EICAR test file into an accessible
quarantine path or use a mock fixture that returns an infected-verdict.
EICAR standard string (write to a temp file, do not execute):
`X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*`
**Steps:**
1. ClamAV settings: enabled=true, known_bad_subdir=known_bad (default).
2. Trigger bulk extract that includes the EICAR file.
3. Verify results dialog: infected count = 1, signature shown.
4. Check filesystem: EICAR file must be under `~/.dirracuda/quarantine/known_bad/...`.
5. Check extracted root: EICAR must NOT be there.

**PASS criteria:** EICAR in known_bad path, not in extracted, dialog accurately reports it.

### HI-3: Scanner unavailable / timeout fail-open

**Preconditions:** ClamAV enabled, but `clamscan` binary is not present (rename or unset PATH entry).
**Steps:**
1. Set `enabled=true`, backend=clamscan. Note: `clamscan_path` is not exposed in the GUI; edit
   `conf/config.json` directly and set `"clamscan_path": "/nonexistent/clamscan"`.
2. Run bulk extract on any host.
3. Confirm extract completes (does not crash or hang).
4. Confirm results dialog shows scanner-error count, files remain in quarantine (not promoted).
5. No exception traceback visible in GUI.

**PASS criteria:** fail-open triggered, extract completes, error items noted in dialog.

### HI-4: Disabled ClamAV — behavior unchanged

**Preconditions:** `clamav.enabled=false` (default).
**Steps:**
1. Open App Config → verify ClamAV tab shows enabled=false.
2. Run dashboard post-scan bulk extract.
3. Confirm: no ClamAV results dialog appears.
4. Confirm: files land in quarantine as before (no promotion, no known_bad movement).
5. Run server-list batch extract. Repeat checks.

**PASS criteria:** zero behavioral change vs pre-C1 baseline when disabled.

### HI-5: Results dialog mute + restart reset

**Preconditions:** ClamAV enabled, scan completes with at least one file.
**Steps:**
1. Run bulk extract — ClamAV dialog appears.
2. Click "Mute ClamAV result dialogs until restart". Dialog closes.
3. Run a second bulk extract in same session — dialog must NOT appear.
4. Close application. Relaunch `./xsmbseek`.
5. Run bulk extract — dialog must appear again (mute reset).

**PASS criteria:** mute suppresses in-session, next launch resets it.

### HI-6: Both entry points exercised

Repeat HI-1 (clean pathing) via:
- Entry point A: **Dashboard** post-scan bulk extract button.
- Entry point B: **Server List** batch extract action.

**PASS criteria:** identical clean-promotion behavior from both entry points.

---

## Rollback runbook structure (for ROLLBACK_RUNBOOK.md)

### Soft rollback (config-only, zero file changes)

```bash
# Edit conf/config.json (or via App Config dialog):
# Set: "clamav": { "enabled": false }
# Reload app.
```

**Verification:** Run HI-4. No dialog, files go to quarantine, no promotion. Should PASS.
**When to use:** ClamAV scanner unreliable or producing false positives. Zero risk to files.

### Partial rollback (disable UI exposure, keep backend safe)

Disable the ClamAV tab in `app_config_dialog.py` and suppress dialog calls in `dashboard.py` and
`batch_status.py` without removing any backend code. This keeps scanner/promotion code in place
but makes it unreachable from the UI.
**Files touched:** `gui/components/app_config_dialog.py`, `gui/components/dashboard.py`,
`gui/components/server_list_window/actions/batch_status.py` (lines 132, 555)
**Verification:** `config.enabled=false` branch is the only reachable path. Run HI-4.
**When to use:** UI defect in C5/C6 discovered post-merge; backend still trusted.

### Hard rollback (file-level git restore)

```bash
# Identify the last commit before C1 work began:
git log --oneline -- shared/clamav_scanner.py  # find first introducing commit hash

# Step 1: Restore files that existed pre-C1 and were modified by C1–C6.
# Do NOT include new files here — git restore with --source fails on paths that
# didn't exist in that commit.
git restore --source=<pre-c1-hash> -- \
  gui/utils/extract_runner.py \
  gui/components/app_config_dialog.py \
  gui/components/dashboard.py \
  gui/components/server_list_window/actions/batch.py \
  gui/components/server_list_window/actions/batch_operations.py \
  gui/components/server_list_window/actions/batch_status.py \
  shared/config.py \
  conf/config.json.example

# Step 2: Remove files introduced by C1–C6 (did not exist pre-C1).
git rm shared/clamav_scanner.py \
       shared/quarantine_postprocess.py \
       shared/quarantine_promotion.py \
       gui/utils/session_flags.py \
       gui/components/clamav_results_dialog.py

# Remove new test files (REQUIRED — they import the removed modules and will fail):
git rm shared/tests/test_clamav_scanner.py \
       shared/tests/test_quarantine_postprocess.py \
       shared/tests/test_quarantine_promotion.py \
       gui/tests/test_extract_runner_clamav.py \
       gui/tests/test_clamav_results_dialog.py \
       gui/tests/test_app_config_dialog_clamav.py
```

**Verification after hard rollback:**
```bash
python3 -m py_compile gui/utils/extract_runner.py shared/config.py gui/components/dashboard.py
xvfb-run -a ./venv/bin/python -m pytest gui/tests/ shared/tests/ -q --tb=short
```
Expected: no import errors, full test suite passes (all ClamAV-specific test files removed).

**When to use:** Critical defect in backend logic (placement routing, scanner verdict parsing) that
cannot be patched quickly. Last resort — requires a new commit after restore.

### Post-rollback checklist (applies to all three levels)

- [ ] Soft: `config.clamav.enabled=false` confirmed in loaded config
- [ ] Soft: HI-4 passes (disabled path unchanged)
- [ ] Partial: no ClamAV dialog appears in any bulk extract path
- [ ] Hard: `import shared.clamav_scanner` raises `ModuleNotFoundError`
- [ ] Hard: full pytest suite passes with no ClamAV-related failures

---

## Risk checklist (mapped to known-failure prevention items)

| Risk item (from TASK_CARDS Known-Failure Prevention) | Validation coverage | Addressed by |
|------------------------------------------------------|--------------------|----|
| Both bulk extract entry points exercised | HI-6 | HI-1 repeated at dashboard + server-list |
| Disabled-path behavior unchanged | HI-4 + regression suite | automated + manual |
| Scanner-unavailable treated as explicit state, not crash | HI-3 | fail-open scenario |
| Placement deterministic (clean/extracted, infected/known_bad, errors/quarantine) | HI-1, HI-2, HI-3 | manual + EICAR |
| No UI-thread blocking during scan/promotion | HI-2 (time UI during EICAR batch) | observe no freeze |
| Session mute resets on app restart | HI-5 | manual restart step |

---

## Risks / assumptions

1. **EICAR availability**: HI-2 requires writing an EICAR string to a local file. This is a standard
   inert test vector — not an actual threat. Assumption: ClamAV is installed and definitions are
   current enough to detect EICAR.

2. **clamd vs clamscan**: The integration supports both. HI-1 and HI-2 are most naturally run with
   `clamscan` (no daemon required). Assumption: `clamscan` is on PATH in the test environment.
   If not: `sudo apt install clamav` and run `clamscan --version` to confirm before HI tests.

3. **Real SMB host not required**: `--mock` mode is acceptable for HI-4, HI-5, and HI-6 dialog
   verification. HI-1, HI-2, HI-3 require actual file placement — a local quarantine fixture
   (manually created directory with test files) is sufficient; no live SMB connection needed.
   Assumption: HI runner creates fixture files manually before each scenario.

4. **Pre-C1 git hash**: The hard rollback requires knowing the hash of the commit immediately
   before C1. Assumption: this is identifiable via `git log --oneline -- shared/clamav_scanner.py`.
   If the file was committed in a squash, the parent of the introducing commit is the restore target.

5. **No regressions in unrelated tests**: The regression suite (step 3) may surface pre-existing
   failures unrelated to ClamAV. Assumption: any failure not in the six ClamAV-specific test files
   is a pre-existing issue and should be flagged separately, not attributed to C7.

6. **clamd availability**: Confirmed — `clamav-daemon` is installed and running on the test machine.
   HI-1 and HI-2 can use either `clamdscan` (faster, via daemon) or `clamscan` (standalone).
   Unit tests mock subprocess calls and pass regardless.

---

## HI test needed?

**Yes** — final sign-off required.

Steps summary:
1. Run automated command matrix (phases 1–6 above). Record all results in VALIDATION_REPORT.md.
2. Execute HI-1 through HI-6 with a local quarantine fixture + clamscan. Record in HI Scenarios
   table in VALIDATION_REPORT.md.
3. Execute soft rollback drill. Record in ROLLBACK_RUNBOOK.md. Restore and re-verify.
4. HI reviewer signs off: add `Reviewer: <initials>  Date: <YYYY-MM-DD>  PASS` to report header.
