# Tmpfs Quarantine v1 - Validation Plan

Date: 2026-03-28

## Automated Validation

### A1 - Syntax checks

```bash
python3 -m py_compile \
  shared/tmpfs_quarantine.py \
  shared/quarantine.py \
  shared/config.py \
  gui/components/app_config_dialog.py \
  dirracuda \
  gui/main.py
```

PASS criteria:
- No output and zero exit code.

### A2 - New unit tests

```bash
./venv/bin/python -m pytest \
  shared/tests/test_tmpfs_quarantine.py \
  gui/tests/test_app_config_dialog_tmpfs.py \
  -q
```

PASS criteria:
- All tests pass.

### A3 - Affected regression suites

```bash
./venv/bin/python -m pytest \
  gui/tests/test_app_config_dialog.py \
  gui/tests/test_app_config_dialog_clamav.py \
  gui/tests/test_browser_clamav.py \
  gui/tests/test_extract_runner_clamav.py \
  -q
```

PASS criteria:
- No new failures in quarantine/config/browser/extract paths.

## HI Manual Validation

### H1 - Linux: tmpfs enabled + mount failure fallback

Steps:
1. Set `quarantine.use_tmpfs=true` in App Config.
2. Start app as non-root in environment where mount is expected to fail.
3. Observe one fallback warning modal.
4. Run a browser download or extract action.
5. Confirm output lands under disk quarantine path.

PASS criteria:
- Warning appears once.
- Operations continue and write to disk quarantine.

### H2 - Linux: tmpfs enabled + active tmpfs close warning

Precondition:
- tmpfs mount succeeds.

Steps:
1. Enable tmpfs and perform at least one quarantine write.
2. Attempt app close.
3. Validate warning modal appears.
4. Click Cancel -> app remains open.
5. Close again and click Confirm.
6. Verify tmpfs quarantine content is removed.

PASS criteria:
- Warning/cancel/confirm behavior matches contract.
- Cleanup performed.

### H3 - Non-Linux UI behavior

Steps:
1. Open App Config dialog.
2. Inspect tmpfs controls.

PASS criteria:
- Controls are visible but disabled.
- Explanatory note indicates Linux-only support.

### H4 - Disabled-path no-regression

Steps:
1. Set `quarantine.use_tmpfs=false`.
2. Run SMB browser download, FTP/HTTP browser download, and SMB extract.

PASS criteria:
- Destinations and behavior match pre-feature baseline.

## Evidence Capture Template

For each run capture:
1. Command executed.
2. Timestamp.
3. PASS/FAIL.
4. Key output line(s).
5. Notes (warnings, caveats, environment constraints).
