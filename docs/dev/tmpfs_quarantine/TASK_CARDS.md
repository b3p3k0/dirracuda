# Tmpfs Quarantine v1 - Task Cards

Date: 2026-03-28  
Execution model: one card at a time, explicit PASS/FAIL evidence.

## Locked Decisions

1. Scope includes all quarantine-producing flows (browser downloads + extract flows).
2. Mountpoint is fixed: `~/.dirracuda/quarantine_tmpfs`.
3. Non-Linux behavior: tmpfs controls visible but disabled.
4. Mount/setup failure behavior: fallback to disk + one-time warning modal.
5. No privilege escalation (`sudo`) from application code.

## C0 - Architecture + Contracts (Docs Only)

Issue:
- Feature spans shared runtime, GUI config, and shutdown behavior; needs one source of truth.

Scope:
- Documentation artifacts only.

Deliverables:
1. `docs/dev/tmpfs_quarantine/ARCHITECTURE.md`
2. `docs/dev/tmpfs_quarantine/TASK_CARDS.md`
3. `docs/dev/tmpfs_quarantine/RISK_REGISTER.md`
4. `docs/dev/tmpfs_quarantine/VALIDATION_PLAN.md`
5. `docs/dev/tmpfs_quarantine/ROLLBACK_RUNBOOK.md`

Validation:
```bash
ls -1 docs/dev/tmpfs_quarantine/
```

HI test needed:
- No.

## C1 - Shared tmpfs Runtime Manager

Issue:
- No central runtime owner for tmpfs mount/fallback/cleanup decisions.

Scope:
- Add `shared/tmpfs_quarantine.py` with stateful runtime API.

Acceptance:
1. Linux-only gating and mountpoint checks are implemented.
2. Mount attempt uses size option and conditional `noswap` branch.
3. Mount failure produces fallback reason + one-time warning payload.
4. Cleanup helper purges tmpfs contents and unmounts only when mounted by app.

Validation:
```bash
python3 -m py_compile shared/tmpfs_quarantine.py
./venv/bin/python -m pytest shared/tests/test_tmpfs_quarantine.py -q
```

HI test needed:
- No.

## C2 - Quarantine Root Integration

Issue:
- Quarantine path creation currently assumes disk roots from callers.

Scope:
- Route quarantine root selection through tmpfs runtime manager.

Acceptance:
1. `shared/quarantine.py` resolves effective root through tmpfs manager.
2. Existing behavior remains unchanged when tmpfs is disabled.
3. tmpfs-enabled runs write under tmpfs mountpoint or disk fallback root.

Validation:
```bash
python3 -m py_compile shared/quarantine.py
./venv/bin/python -m pytest shared/tests/test_tmpfs_quarantine.py -q
```

HI test needed:
- No.

## C3 - Config Contract + App Config UI

Issue:
- tmpfs settings need persistence and explicit operator controls.

Scope:
- Add config schema keys and App Config controls.

Acceptance:
1. New config keys:
   - `quarantine.use_tmpfs` (bool)
   - `quarantine.tmpfs_size_mb` (int)
2. App Config dialog includes tmpfs checkbox + size input.
3. Quarantine chooser is disabled when tmpfs mode is enabled.
4. Non-Linux renders tmpfs controls disabled with explanatory note.

Validation:
```bash
python3 -m py_compile shared/config.py gui/components/app_config_dialog.py
./venv/bin/python -m pytest gui/tests/test_app_config_dialog_tmpfs.py -q
```

HI test needed:
- Yes.
- Steps:
1. Open App Config on Linux and toggle tmpfs checkbox.
2. Verify quarantine browse/entry disable when enabled.
3. Save, reopen dialog, confirm persistence.

## C4 - Startup Bootstrap + One-Time Fallback Warning

Issue:
- tmpfs bootstrap must happen once per app start and warn clearly on fallback.

Scope:
- Integrate bootstrap + warning in both entrypoints.

Acceptance:
1. `dirracuda` bootstraps tmpfs runtime at startup.
2. `gui/main.py` bootstraps tmpfs runtime at startup.
3. Fallback warning modal shows once per session when provided.

Validation:
```bash
python3 -m py_compile dirracuda gui/main.py
./venv/bin/python -m pytest shared/tests/test_tmpfs_quarantine.py -q
```

HI test needed:
- Yes.
- Steps:
1. Enable tmpfs and force mount failure (no privilege).
2. Start app and verify one warning modal appears.
3. Continue app usage and verify no repeated fallback modal.

## C5 - Exit Warning + Cleanup/Unmount

Issue:
- tmpfs data is volatile; users need explicit destructive-close warning.

Scope:
- Add close-time warning and cleanup hooks in both entrypoints.

Acceptance:
1. If tmpfs active + quarantine content exists, close prompts for confirmation.
2. Cancel blocks shutdown.
3. Confirm purges tmpfs content.
4. Unmount happens only if app mounted tmpfs.

Validation:
```bash
python3 -m py_compile dirracuda gui/main.py
./venv/bin/python -m pytest shared/tests/test_tmpfs_quarantine.py -q
```

HI test needed:
- Yes.
- Steps:
1. Enable tmpfs, download/extract at least one file.
2. Attempt app close and verify warning appears.
3. Cancel and verify app stays open.
4. Close again, confirm, then verify tmpfs content removed.

## C6 - Regression + Final Evidence

Issue:
- Broad quarantine-path behavior needs explicit no-regression proof.

Scope:
- Run targeted tests + key regression suites and produce PASS/FAIL evidence.

Acceptance:
1. New tmpfs tests pass.
2. Existing app-config and quarantine-related tests pass.
3. No regressions in browser/extract quarantine destinations with tmpfs disabled.

Validation:
```bash
python3 -m py_compile shared/tmpfs_quarantine.py shared/quarantine.py shared/config.py gui/components/app_config_dialog.py dirracuda gui/main.py
./venv/bin/python -m pytest shared/tests/test_tmpfs_quarantine.py gui/tests/test_app_config_dialog_tmpfs.py gui/tests/test_browser_clamav.py gui/tests/test_extract_runner_clamav.py -q
```

HI test needed:
- Yes (final sign-off).
