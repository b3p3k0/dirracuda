# F6 Rollback Runbook

Date: 2026-03-25
Card: F6 — Full Regression + Rollback Drill

## Purpose
Rollback only F6 entrypoint finalization changes if post-release issues are found.

## Rollback Scope
- Restore legacy root launcher `xsmbseek`.
- Restore pre-F6 `dirracuda` thin-wrapper behavior.
- Revert F6-only supporting edits:
  - `gui/main.py`
  - `gui/tests/test_db_path_sync_precedence.py`

## Preconditions
1. Run from repo root.
2. Preserve any unrelated local work (`git stash -u` if needed).

## Rollback Procedure (Exact Commands)

### 1) Restore `xsmbseek` to project root
```bash
if [ -f scripts/legacy/xsmbseek_legacy.py ]; then
  mv scripts/legacy/xsmbseek_legacy.py xsmbseek
  chmod +x xsmbseek
fi
```

### 2) Restore `dirracuda` to thin wrapper mode (pre-F6 behavior)
```bash
cat > dirracuda <<'PY'
#!/usr/bin/env python3
"""Dirracuda — canonical GUI launcher (thin wrapper for xsmbseek)."""
import os
import sys

os.environ['DIRRACUDA_PROG_NAME'] = 'dirracuda'
_dir = os.path.dirname(os.path.abspath(__file__))
os.execv(sys.executable, [sys.executable, os.path.join(_dir, 'xsmbseek')] + sys.argv[1:])
PY
chmod +x dirracuda
```

### 3) Revert F6 support-file edits
```bash
git checkout -- gui/main.py gui/tests/test_db_path_sync_precedence.py
```

### 4) Optional cleanup
```bash
rmdir scripts/legacy 2>/dev/null || true
rmdir scripts 2>/dev/null || true
```

## Post-Rollback Verification
```bash
test -f xsmbseek && echo "PASS xsmbseek restored"
./venv/bin/python xsmbseek --version
./venv/bin/python dirracuda --version
xvfb-run -a ./venv/bin/python -m pytest gui/tests/ shared/tests/ -q --tb=short
```

Expected:
- `xsmbseek` exists at root and is executable.
- `dirracuda` launches by delegating to `xsmbseek`.
- Regression suite passes.

## Representative Rollback Drill (Executed)
A disposable local clone drill was executed to verify rollback command flow without touching the active workspace.

Drill command outcome:
```text
PASS rollback-drill
```

Drill assertions covered:
1. F6-like state simulation (`xsmbseek` moved out of root).
2. Rollback restoration of `xsmbseek` into root.
3. Removal of F6-only root-entrypoint state artifacts.

## Escalation/Abort Conditions
- If rollback commands fail due additional local modifications, run:
```bash
git status --short
```
then resolve conflicts file-by-file before re-running verification.
