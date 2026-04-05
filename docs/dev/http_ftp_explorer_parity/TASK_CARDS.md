# HTTP/FTP Explorer Parity — Task Cards

Date: 2026-04-04  
Execution model: one card at a time, explicit PASS/FAIL evidence.

## Locked Intent

1. Keep SMB behavior unchanged.
2. Add FTP/HTTP tuning surface parity in unified explorer.
3. Use shared persistence keys: `file_browser.download_worker_count`, `file_browser.download_large_file_mb`.
4. FTP gets full runtime parity for worker+large-threshold behavior.
5. HTTP gets worker-count runtime parity only in this phase.
6. HTTP large-file control remains visible but disabled, with explicit UX messaging.
7. Document the HTTP limitation in operator/developer docs.

## Card C0 — Workspace Setup + Contract Inventory (Plan Only)

Status:
- Completed (Codex+HI, 2026-04-04)

Issue:
- Need a decision-complete contract before code edits.

Deliverables:
1. `docs/dev/http_ftp_explorer_parity/README.md`
2. `docs/dev/http_ftp_explorer_parity/SPEC_DRAFT.md`
3. `docs/dev/http_ftp_explorer_parity/TASK_CARDS.md`
4. `docs/dev/http_ftp_explorer_parity/RISK_REGISTER.md`

Validation:
```bash
rg -n "_build_window|_start_download_thread|_download_thread_fn|workers_var|large_mb_var" gui/components/unified_browser_window.py
rg -n "download_worker_count|download_large_file_mb|max_file_bytes" conf/config.json conf/config.json.example
```

HI test needed:
- No.

## Card C1 — Shared Tuning Surface + Persistence Plumbing

Issue:
- FTP/HTTP explorers do not expose download tuning fields.

Scope:
1. Add FTP/HTTP tuning strip in `UnifiedBrowserCore` UI:
   - `Worker count` (spinbox, clamp 1..3)
   - `Large files limit (MB)` (spinbox)
2. HTTP-specific UX:
   - large-file control visible but disabled.
   - add inline explanation that HTTP large-file split is not active yet.
3. Load/save via shared settings keys:
   - `file_browser.download_worker_count`
   - `file_browser.download_large_file_mb`
4. No SMB behavior/UI changes.

Likely files:
1. `gui/components/unified_browser_window.py`
2. `gui/tests/test_ftp_browser_window.py`
3. `gui/tests/test_http_browser_window.py`
4. `gui/tests/test_browser_clamav.py` (or equivalent focused parity test file)

Acceptance:
1. FTP shows both controls editable.
2. HTTP shows both controls; large control disabled with explanatory text.
3. Values persist through settings manager keys above.
4. Existing SMB tests/behavior unaffected.

Validation:
```bash
python3 -m py_compile gui/components/unified_browser_window.py
./venv/bin/python -m pytest gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py gui/tests/test_browser_clamav.py -q
```

HI test needed:
- Yes.
- Steps:
1. Open FTP browser window and confirm both controls are editable.
2. Change values, close/reopen browser, confirm persisted values.
3. Open HTTP browser window and confirm large-file control is visible but disabled with clear note.

## Card C2 — Runtime Behavior Parity (FTP full, HTTP worker-only)

Issue:
- FTP/HTTP downloads do not currently honor the new tuning behavior.

Scope:
1. FTP runtime:
   - apply `worker_count` concurrency.
   - apply SMB-style large-file threshold queue routing.
2. HTTP runtime:
   - apply `worker_count` concurrency.
   - do NOT apply large-file split.
3. Preserve:
   - cancellation behavior,
   - ClamAV post-processing,
   - existing fail-open policy and completion messaging.

Likely files:
1. `gui/components/unified_browser_window.py`
2. `gui/tests/test_browser_clamav.py`
3. Additional focused tests for FTP queue routing + HTTP worker behavior.

Acceptance:
1. FTP worker count affects runtime concurrency.
2. FTP large-file threshold affects queue selection behavior.
3. HTTP worker count affects runtime concurrency.
4. HTTP large split remains intentionally inactive.
5. Cancel remains responsive and non-crashing.
6. Existing ClamAV browser-download integration remains correct.

Validation:
```bash
python3 -m py_compile gui/components/unified_browser_window.py shared/ftp_browser.py shared/http_browser.py
./venv/bin/python -m pytest gui/tests/test_browser_clamav.py gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py -q
```

HI test needed:
- Yes.
- Steps:
1. FTP: run multi-file download with different worker counts and observe throughput/order behavior.
2. FTP: include a file above threshold and verify large-file path behavior remains stable.
3. HTTP: run multi-file download with worker count changes and verify no large-threshold behavior is implied.

## Card C3 — Docs + Risk Notes + Final Regression

Issue:
- Operator/dev docs do not yet state new tuning behavior and HTTP limitation.

Scope:
1. Update `README.md` file-browser section:
   - unified worker tuning availability.
   - large split active for SMB/FTP.
   - HTTP large control visible but disabled (worker-only in current phase).
2. Update `docs/TECHNICAL_REFERENCE.md` with same contract.
3. Update workspace status in `docs/dev/http_ftp_explorer_parity/README.md`.

Likely files:
1. `README.md`
2. `docs/TECHNICAL_REFERENCE.md`
3. `docs/dev/http_ftp_explorer_parity/README.md`

Acceptance:
1. Docs match actual runtime behavior.
2. HTTP limitation is explicit and unambiguous.
3. Targeted regression evidence is recorded in card report.

Validation:
```bash
./venv/bin/python -m pytest gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py gui/tests/test_browser_clamav.py -q
python3 -m py_compile gui/components/unified_browser_window.py
```

HI test needed:
- Yes.
- Steps:
1. Sanity-check docs wording against live explorer behavior after C1/C2.
2. Confirm no mismatch between UI text and documentation language.

### C3 Report

Issue: Operator/dev docs did not state new tuning behavior or HTTP large-file limitation.
Root cause: C1/C2 added runtime behavior; docs deferred to C3 per scope discipline.
Fix: Added worker count and large-file routing documentation to README.md (Browsing Shares +
Configuration sections) and TECHNICAL_REFERENCE.md (§3.1 table + §6.6 prose). Wording reflects
that tuning is UI-controlled and persisted in GUI settings keys (not conf/config.json). HTTP
limitation is explicit and unambiguous in both locations. R3 closure note added to RISK_REGISTER.md.
Files changed:
  - README.md
  - docs/TECHNICAL_REFERENCE.md
  - docs/dev/http_ftp_explorer_parity/RISK_REGISTER.md (R3 closure note)
  - docs/dev/http_ftp_explorer_parity/README.md
  - docs/dev/http_ftp_explorer_parity/TASK_CARDS.md (this report)

Validation run:
  py_compile: exit 0
  pytest: 55 passed, 8 warnings in 0.27s

Result: PASS
HI test needed: Yes.
  1. Open live HTTP browser; confirm large-file spinbox is disabled and note text matches docs wording.
  2. Open live FTP browser; confirm both controls are enabled and values persist after close/reopen.

AUTOMATED: PASS
MANUAL:    PENDING
OVERALL:   PENDING
