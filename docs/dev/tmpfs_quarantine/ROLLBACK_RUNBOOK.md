# Tmpfs Quarantine v1 - Rollback Runbook

Date: 2026-03-28

Use the least destructive rollback level that restores stable behavior.

## Level 1 - Soft Rollback (Config Only)

Use when:
- tmpfs feature causes operational confusion but app behavior is otherwise stable.

Steps:
```bash
# In App Config dialog:
#   Disable "Use memory (tmpfs) for quarantine"
# or edit config directly:
#   "quarantine": { "use_tmpfs": false, "tmpfs_size_mb": 512 }
```

Verification:
1. Restart app.
2. Run browser download and extract.
3. Confirm paths resolve to disk quarantine roots.

Expected result:
- No tmpfs mount behavior is exercised.
- Existing disk quarantine behavior is restored.

## Level 2 - Partial Rollback (Disable UI + Lifecycle Hooks)

Use when:
- Runtime manager logic is acceptable, but startup/close UX introduces risk.

Scope:
1. Disable tmpfs controls in App Config UI.
2. Remove startup warning modal hook.
3. Remove close-time destructive warning hook.
4. Keep disk quarantine behavior intact.

Verification:
```bash
python3 -m py_compile gui/components/app_config_dialog.py dirracuda gui/main.py
```

Expected result:
- Operators cannot enable tmpfs from UI.
- App startup/close behavior returns to previous UX.

## Level 3 - Hard Rollback (Code Revert)

Use when:
- tmpfs runtime integration causes functional regression and cannot be patched quickly.

Targets to revert/remove:
1. `shared/tmpfs_quarantine.py` (new)
2. tmpfs integration in `shared/quarantine.py`
3. tmpfs settings wiring in `shared/config.py`
4. App Config tmpfs controls in `gui/components/app_config_dialog.py`
5. Startup/close tmpfs hooks in `dirracuda` and `gui/main.py`
6. tmpfs test files in `shared/tests/` and `gui/tests/`
7. tmpfs docs under `docs/dev/tmpfs_quarantine/`

Verification:
```bash
python3 -m py_compile shared/quarantine.py shared/config.py gui/components/app_config_dialog.py dirracuda gui/main.py
./venv/bin/python -m pytest gui/tests/test_app_config_dialog.py gui/tests/test_extract_runner_clamav.py gui/tests/test_browser_clamav.py -q
```

Expected result:
- All quarantine flows behave exactly as pre-tmpfs baseline.
- No tmpfs references in active runtime paths.

## Manual Recovery Commands (if cleanup/unmount stuck)

Inspect mount state:
```bash
findmnt -T ~/.dirracuda/quarantine_tmpfs
```

Manual purge:
```bash
rm -rf ~/.dirracuda/quarantine_tmpfs/*
```

Manual unmount:
```bash
umount ~/.dirracuda/quarantine_tmpfs
```

If unmount reports busy:
```bash
lsof +D ~/.dirracuda/quarantine_tmpfs
```

Resolve holding process and retry unmount.
