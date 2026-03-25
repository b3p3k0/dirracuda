# F6 Validation Report

Date: 2026-03-25
Card: F6 — Full Regression + Rollback Drill

## Scope Executed
- Final regression and release-safety validation after F0–F5.
- Root entrypoint finalized to `dirracuda` in project root.
- Legacy root launcher script moved out of root to `scripts/legacy/xsmbseek_legacy.py`.

## F6 Acceptance Gates
1. Automated and manual gates reported with exact commands.  
Status: PASS (automated complete; HI manual checklist included below)
2. Rollback tested on representative fixtures.  
Status: PASS (disposable rollback drill executed)
3. Sole entrypoint for application is `dirracuda` in project root.  
Status: PASS (`xsmbseek` no longer present at root)

## Code/Path Changes Executed in F6
- `dirracuda`: promoted from thin wrapper to full primary launcher implementation.
- `xsmbseek`: moved to `scripts/legacy/xsmbseek_legacy.py`.
- `gui/main.py`: deprecated guidance updated to point to `./dirracuda`.
- `gui/tests/test_db_path_sync_precedence.py`: loader path updated from `xsmbseek` to `dirracuda`.

## Automated Validation

### 1) Syntax/compile checks
```bash
./venv/bin/python -m py_compile dirracuda gui/main.py gui/tests/test_db_path_sync_precedence.py
```
Result: PASS

### 2) Entrypoint/version/help smoke
```bash
./venv/bin/python dirracuda --version
```
Output:
```text
dirracuda 1.0.0
```
Result: PASS

```bash
./venv/bin/python dirracuda --help | head -n 3
```
Output:
```text
usage: dirracuda [-h] [--mock] [--config FILE] [--smbseek-path PATH]
                 [--database-path PATH] [--debug] [--version]
```
Result: PASS

### 3) Root entrypoint assertion
```bash
ls -1 | rg -n "^(dirracuda|xsmbseek)$"
```
Output:
```text
8:dirracuda
```
Result: PASS (`xsmbseek` absent from root)

### 4) Full regression (headless)
```bash
xvfb-run -a ./venv/bin/python -m pytest gui/tests/ shared/tests/ -q --tb=short
```
Output summary:
```text
440 passed in 6.65s
```
Result: PASS

## HI Manual Sign-Off Checklist (Required)
1. Run `./dirracuda --mock`; verify GUI opens and no startup errors.
2. Trigger setup/config screens; confirm no launcher-path breakage.
3. Run a quick scan path; verify DB path and session creation still function.
4. Confirm legacy helper script exists only under `scripts/legacy/xsmbseek_legacy.py`.

Current status: Pending HI execution.

## Final Result
PASS (automated) / HI pending.
