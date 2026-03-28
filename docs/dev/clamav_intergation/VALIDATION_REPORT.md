# ClamAV Integration — Validation Report (C7)

Date: 2026-03-28
Branch: development
Tester: automated + HI pending
Status: **AUTOMATED PASS — HI PENDING**

---

## Automated Evidence

All commands run from project root (`/home/kevin/DEV/smbseek-smb`).
Interpreter: `./venv/bin/python` unless noted.
Full commands in Appendix A.

### Section 1 — Syntax Checks

| # | File | Timestamp | Result | Output |
|---|------|-----------|--------|--------|
| 1 | `shared/clamav_scanner.py` | 2026-03-28 11:41:25 | **PASS** | no output |
| 2 | `shared/quarantine_postprocess.py` | 2026-03-28 11:41:25 | **PASS** | no output |
| 3 | `shared/quarantine_promotion.py` | 2026-03-28 11:41:25 | **PASS** | no output |
| 4 | `gui/utils/extract_runner.py` | 2026-03-28 11:41:25 | **PASS** | no output |
| 5 | `gui/utils/session_flags.py` | 2026-03-28 11:41:25 | **PASS** | no output |
| 6 | `gui/components/clamav_results_dialog.py` | 2026-03-28 11:41:25 | **PASS** | no output |
| 7 | `gui/components/app_config_dialog.py` | 2026-03-28 11:41:25 | **PASS** | no output |
| 8 | `gui/components/dashboard.py` | 2026-03-28 11:41:25 | **PASS** | no output |
| 9 | `gui/components/server_list_window/actions/batch.py` | 2026-03-28 11:41:25 | **PASS** | no output |
| 10 | `gui/components/server_list_window/actions/batch_operations.py` | 2026-03-28 11:41:25 | **PASS** | no output |
| 11 | `gui/components/server_list_window/actions/batch_status.py` | 2026-03-28 11:41:25 | **PASS** | no output |
| 12 | `shared/config.py` | 2026-03-28 11:41:25 | **PASS** | no output |

### Section 2 — Targeted Unit Tests (C1–C6 test files)

| # | Test file | Timestamp | Result | Summary line |
|---|-----------|-----------|--------|--------------|
| 13 | `shared/tests/test_clamav_scanner.py` | 2026-03-28 11:41:29 | **PASS** | `19 passed in 0.05s` |
| 14 | `shared/tests/test_quarantine_postprocess.py` | 2026-03-28 11:41:30 | **PASS** | `8 passed, 9 warnings in 0.16s` |
| 15 | `shared/tests/test_quarantine_promotion.py` | 2026-03-28 11:41:34 | **PASS** | `18 passed, 2 warnings in 0.16s` |
| 16 | `gui/tests/test_extract_runner_clamav.py` | 2026-03-28 11:41:34 | **PASS** | `29 passed, 49 warnings in 0.23s` |
| 17 | `gui/tests/test_clamav_results_dialog.py` | 2026-03-28 11:41:38 | **PASS** | `25 passed in 0.27s` |
| 18 | `gui/tests/test_app_config_dialog_clamav.py` | 2026-03-28 11:41:39 | **PASS** | `28 passed in 0.05s` |

Notes: DeprecationWarning on `datetime.utcnow()` appears in rows 14–16. Pre-existing in `shared/quarantine.py:90` and `gui/utils/extract_runner.py:703`. Not introduced by C1–C6; not blocking.

### Section 3 — Full Regression Suite

| # | Suite | Timestamp | Result | Summary line |
|---|-------|-----------|--------|--------------|
| 19 | `gui/tests/ shared/tests/` | 2026-03-28 11:42:19 | **PASS** | `628 passed, 60 warnings in 6.83s` |

Notes: 60 warnings are the same pre-existing `utcnow()` deprecations from rows 14–16. Zero failures. Zero errors.

### Section 4 — Config Round-Trip Smoke

| # | Check | Timestamp | Result | Output |
|---|-------|-----------|--------|--------|
| 20 | `get_clamav_config()` key presence + types | 2026-03-28 11:42:32 | **PASS** | `config smoke: PASS` |

Observed config: `{'enabled': False, 'backend': 'auto', 'timeout_seconds': 60, 'extracted_root': '~/.dirracuda/extracted', 'known_bad_subdir': 'known_bad', 'show_results': True}`

### Section 5 — Disabled-Path Behavior Gate

| # | Check | Timestamp | Result | Summary line |
|---|-------|-----------|--------|--------------|
| 21 | `pytest -k disabled_path` | 2026-03-28 11:42:33 | **PASS** | `2 passed, 27 deselected, 6 warnings in 0.13s` |

Tests: `test_disabled_path_no_clamav_config`, `test_disabled_path_explicit_false`.

### Section 6 — Session Flags Reset Gate (two-process)

| # | Check | Timestamp | Result | Output |
|---|-------|-----------|--------|--------|
| 22 | Process 1: set / get / clear | 2026-03-28 11:42:41 | **PASS** | `session flags process-1 gate: PASS` |
| 23 | Process 2: flag absent after restart | 2026-03-28 11:42:41 | **PASS** | `session flags process-2 (restart) gate: PASS` |

---

## HI Scenarios

Status: **PENDING — requires live GUI session**

Reviewer completes this section. For each scenario, record PASS/FAIL and observations.

| # | Scenario | Preconditions | Result | Notes |
|---|----------|---------------|--------|-------|
| HI-1 | Clean file pathing | ClamAV enabled, backend=auto, clean files | — | |
| HI-2 | EICAR infected file pathing | ClamAV enabled, EICAR fixture in quarantine | — | |
| HI-3 | Scanner unavailable / fail-open | `clamscan_path=/nonexistent` in conf/config.json | — | |
| HI-4 | Disabled — behavior unchanged | `clamav.enabled=false` (dashboard + server-list) | — | |
| HI-5 | Mute + restart reset | ClamAV enabled, scan produces results | — | |
| HI-6 | Both entry points (clean pathing) | Dashboard bulk + server-list batch | — | |

### HI scenario steps (reference)

**HI-1 — Clean file pathing**
1. App Config → ClamAV tab: enabled=true, backend=auto.
2. Dashboard post-scan bulk extract, 1–3 small clean files.
3. Verify results dialog: clean > 0, infected = 0.
4. Check `~/.dirracuda/extracted/<host>/<date>/...` — files present.
5. Check quarantine dir — files absent.

PASS criteria: files in extracted root, none remaining in quarantine, dialog totals correct.

**HI-2 — Infected file pathing (EICAR)**

EICAR string (write to temp file, do not execute):
`X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*`

1. App Config: enabled=true, known_bad_subdir=known_bad (default).
2. Place EICAR file in quarantine fixture path. Trigger bulk extract including that file.
3. Verify results dialog: infected = 1, signature shown.
4. Check `~/.dirracuda/quarantine/known_bad/...` — EICAR present.
5. Check `~/.dirracuda/extracted/` — EICAR absent.

PASS criteria: EICAR in known_bad, not in extracted, dialog accurate.

**HI-3 — Scanner unavailable / fail-open**

Note: `clamscan_path` is not exposed in the GUI; edit `conf/config.json` directly.
1. Set `"clamav": {"enabled": true, "backend": "clamscan", "clamscan_path": "/nonexistent/clamscan"}`.
2. Run bulk extract on any host.
3. Extract must complete without crash or hang.
4. Results dialog must show scanner-error count; files remain in quarantine.
5. No exception traceback visible.

PASS criteria: fail-open triggered, extract completes, error items in dialog.

**HI-4 — Disabled ClamAV — behavior unchanged**
1. App Config: verify enabled=false.
2. Dashboard bulk extract → no ClamAV dialog, files land in quarantine only.
3. Server-list batch extract → same.

PASS criteria: zero behavioral change; no promotion, no known_bad movement, no dialog.

**HI-5 — Results dialog mute + restart reset**
1. ClamAV enabled. Bulk extract → dialog appears.
2. Click "Mute ClamAV result dialogs until restart".
3. Second bulk extract in same session → dialog must NOT appear.
4. Close and relaunch `./xsmbseek`.
5. Bulk extract → dialog appears again.

PASS criteria: mute suppresses in-session; new process resets.

**HI-6 — Both entry points (clean pathing)**

Repeat HI-1 steps via:
- Entry point A: Dashboard post-scan bulk extract.
- Entry point B: Server List batch extract action.

PASS criteria: identical clean-promotion behavior from both.

---

## Sign-Off

```
Reviewer: ___   Date: ___________   HI result: PASS / FAIL

Notes:
```

---

## Appendix A — Full Commands

```bash
# Section 1 — syntax checks
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

# Section 2 — targeted unit tests
./venv/bin/python -m pytest shared/tests/test_clamav_scanner.py        -v --tb=short
./venv/bin/python -m pytest shared/tests/test_quarantine_postprocess.py -v --tb=short
./venv/bin/python -m pytest shared/tests/test_quarantine_promotion.py   -v --tb=short
./venv/bin/python -m pytest gui/tests/test_extract_runner_clamav.py     -v --tb=short
./venv/bin/python -m pytest gui/tests/test_clamav_results_dialog.py     -v --tb=short
./venv/bin/python -m pytest gui/tests/test_app_config_dialog_clamav.py  -v --tb=short

# Section 3 — full regression
xvfb-run -a ./venv/bin/python -m pytest gui/tests/ shared/tests/ -q --tb=short 2>&1 | tee /tmp/c7_regression.txt

# Section 4 — config smoke
./venv/bin/python -c "
from shared.config import SMBSeekConfig
c = SMBSeekConfig()
cfg = c.get_clamav_config()
assert isinstance(cfg['enabled'], bool)
assert isinstance(cfg['backend'], str)
assert isinstance(cfg['timeout_seconds'], int)
assert isinstance(cfg['show_results'], bool)
assert isinstance(cfg['extracted_root'], str)
assert isinstance(cfg['known_bad_subdir'], str)
print('config smoke: PASS'); print(cfg)
"

# Section 5 — disabled-path gate
./venv/bin/python -m pytest gui/tests/test_extract_runner_clamav.py -k "disabled_path" -q --tb=short

# Section 6 — session flags (two processes)
./venv/bin/python -c "
from gui.utils.session_flags import set_flag, get_flag, clear_flag, CLAMAV_MUTE_KEY
assert get_flag(CLAMAV_MUTE_KEY) == False
set_flag(CLAMAV_MUTE_KEY, True)
assert get_flag(CLAMAV_MUTE_KEY) == True
clear_flag(CLAMAV_MUTE_KEY)
assert get_flag(CLAMAV_MUTE_KEY) == False
print('session flags process-1 gate: PASS')
"
./venv/bin/python -c "
from gui.utils.session_flags import get_flag, CLAMAV_MUTE_KEY
assert get_flag(CLAMAV_MUTE_KEY) == False
print('session flags process-2 (restart) gate: PASS')
"
```
