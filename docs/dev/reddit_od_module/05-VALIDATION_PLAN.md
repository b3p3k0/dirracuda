# Reddit OD Module: Validation Plan

Date: 2026-04-05
Status labels:
- `AUTOMATED: PASS|FAIL`
- `MANUAL: PASS|FAIL|PENDING`
- `OVERALL: PASS|FAIL|PENDING`

## Targeted Automated Checks

### Parser and service tests
Commands:
```bash
python3 -m pytest -q gui/tests -k "reddit or redseek"
```
Expected:
1. New tests for parser/store/service pass.
2. No unrelated failures introduced by touched files.

### Static sanity for touched modules
Commands:
```bash
python3 -m py_compile experimental/redseek/*.py gui/components/reddit_*.py
```
Expected:
1. No syntax/runtime import errors in newly added paths.

## Targeted Regression Checks
Commands:
```bash
python3 -m pytest -q gui/tests -k "dashboard or scan_manager"
```
Expected:
1. Dashboard action wiring remains stable.
2. Existing scan action routing unaffected.

## Manual HI Gate (Required)

### Flow A: Ingestion run (`new`)
1. Open dashboard and click `Reddit Grab`.
2. Run with `sort=new`, `parse body=on`, `include nsfw=on`, `replace cache=off`.
3. Confirm progress and final summary appear.
4. Re-run immediately and confirm duplicate explosion does not occur.

### Flow B: Ingestion run (`top`)
1. Run with `sort=top`.
2. Confirm bounded pull behavior (no runaway pagination).
3. Confirm rows are written and deduped across repeated runs.

### Flow C: Reddit browser actions
1. Open `Reddit Post DB`.
2. Use `Open in Explorer` on:
   - full URL row
   - host:port row
   - bare host row
3. Confirm inference on known cases and prompt on unresolved case.

### Flow D: Isolation regression
1. Launch SMB scan dialog.
2. Launch FTP scan dialog.
3. Launch HTTP scan dialog.
4. Confirm no behavior change and no errors from Reddit module load.

## Exit Criteria
1. `AUTOMATED: PASS` on touched component checks.
2. `MANUAL: PASS` for core HI flows above.
3. Risks and limitations documented.
4. No commits unless HI says `commit`.
