# ClamAV Integration — Rollback Runbook (C7)

Date: 2026-03-28
Branch: development

Use this runbook if a defect is found in the C1–C6 ClamAV integration after merge.
Three rollback levels are available; use the least destructive one that resolves the issue.

---

## Decision tree

```
Problem observed?
│
├─ Scanner unreliable / false positives only
│   └─ SOFT ROLLBACK (config toggle, no file changes)
│
├─ UI defect in C5/C6 dialog or C6 config panel, backend logic trusted
│   └─ PARTIAL ROLLBACK (suppress dialog call sites, keep backend)
│
└─ Backend defect (wrong file placement, bad verdict parsing, crash in worker)
    └─ HARD ROLLBACK (git restore + git rm, new commit required)
```

---

## Soft Rollback — config only

**Risk:** none. **Reversible:** yes, edit config again.

### Steps

```bash
# Option A: via App Config dialog
# Open App Config → ClamAV tab → uncheck "Enable ClamAV scan" → Save
# Relaunch app.

# Option B: edit conf/config.json directly
# Set: "clamav": { "enabled": false }
# Relaunch app.
```

### Verification

1. Open App Config → ClamAV tab shows enabled=false.
2. Run dashboard post-scan bulk extract.
3. Confirm: no ClamAV results dialog appears.
4. Confirm: files land in quarantine (no promotion, no known_bad movement).
5. Run server-list batch extract. Repeat step 3–4.

All checks must PASS before closing the incident.

### Post-rollback checklist

- [ ] `conf/config.json` has `"enabled": false` under `clamav`
- [ ] No ClamAV dialog appears in dashboard bulk extract
- [ ] No ClamAV dialog appears in server-list batch extract
- [ ] Files land in quarantine (unchanged from pre-integration behavior)

---

## Partial Rollback — disable UI exposure

**Risk:** low — requires 3 source edits and a new commit. **Reversible:** yes, revert the commit.
**When to use:** UI defect in C5/C6 that cannot be quickly patched; backend scanner/placement logic is still trusted.

### Files to edit

| File | What to change |
|------|---------------|
| `gui/components/app_config_dialog.py` | Suppress/hide the ClamAV tab in the dialog so users cannot enable it via UI |
| `gui/components/dashboard.py` | Suppress the `show_clamav_results_dialog` call after bulk extract |
| `gui/components/server_list_window/actions/batch_status.py` | Suppress the `show_clamav_results_dialog` calls at lines 132 and 555 |

The minimal approach: wrap each call site in `if False:` or guard with a temporary constant
`_CLAMAV_UI_ENABLED = False`. This makes ClamAV unreachable from the UI while leaving all backend
code in place.

### Verification

Same as soft rollback verification — no dialog should appear, files go to quarantine only.

Additionally:
```bash
python3 -m py_compile gui/components/app_config_dialog.py gui/components/dashboard.py \
  gui/components/server_list_window/actions/batch_status.py
```
Expected: no output (clean compile).

### Post-rollback checklist

- [ ] ClamAV tab is not visible/accessible in App Config dialog
- [ ] No ClamAV dialog appears in dashboard bulk extract
- [ ] No ClamAV dialog appears in server-list batch extract
- [ ] Files land in quarantine (unchanged from pre-integration behavior)
- [ ] New commit created and pushed

---

## Hard Rollback — file-level git restore

**Risk:** destructive — restores files to pre-C1 state and removes new files.
**Reversible:** only via `git revert` of the rollback commit. Confirm decision with team before proceeding.
**When to use:** critical defect in backend logic (placement routing, verdict parsing, worker crash) that cannot be patched quickly.

### Step 0 — find the pre-C1 hash

```bash
git log --oneline -- shared/clamav_scanner.py
```

The first line is the commit that introduced `clamav_scanner.py`. The commit **before** that is `<pre-c1-hash>`.
If C1–C6 were squashed into one commit, the parent of the introducing commit is the target.

Verify the hash is correct:
```bash
git show <pre-c1-hash>:shared/config.py | grep -c "clamav"
# Expected: 0  (no clamav references in that snapshot)
```

### Step 1 — restore modified files (files that existed pre-C1)

These files existed before C1 and were modified. `git restore --source` works on them.

```bash
git restore --source=<pre-c1-hash> -- \
  gui/utils/extract_runner.py \
  gui/components/app_config_dialog.py \
  gui/components/dashboard.py \
  gui/components/server_list_window/actions/batch.py \
  gui/components/server_list_window/actions/batch_operations.py \
  gui/components/server_list_window/actions/batch_status.py \
  shared/config.py \
  conf/config.json.example
```

### Step 2 — remove new source files (introduced by C1–C6)

These did not exist pre-C1. Do NOT include them in the `git restore` above — `git restore` with
`--source` fails on paths absent from the target commit.

```bash
git rm shared/clamav_scanner.py \
       shared/quarantine_postprocess.py \
       shared/quarantine_promotion.py \
       gui/utils/session_flags.py \
       gui/components/clamav_results_dialog.py
```

### Step 3 — remove new test files (REQUIRED)

These import the removed modules. Leaving them in place will cause import errors in the test suite.

```bash
git rm shared/tests/test_clamav_scanner.py \
       shared/tests/test_quarantine_postprocess.py \
       shared/tests/test_quarantine_promotion.py \
       gui/tests/test_extract_runner_clamav.py \
       gui/tests/test_clamav_results_dialog.py \
       gui/tests/test_app_config_dialog_clamav.py
```

### Step 4 — verify before committing

```bash
python3 -m py_compile gui/utils/extract_runner.py shared/config.py gui/components/dashboard.py
xvfb-run -a ./venv/bin/python -m pytest gui/tests/ shared/tests/ -q --tb=short
```

Expected: no import errors; full suite passes with no ClamAV-specific test failures (those files are gone).

### Step 5 — commit

```bash
git add -p  # review staged changes
git commit -m "revert: hard rollback of ClamAV integration (C1–C6)

<one-line description of the defect that triggered rollback>"
```

### Post-rollback checklist

- [ ] `import shared.clamav_scanner` raises `ModuleNotFoundError`
- [ ] `import gui.utils.session_flags` raises `ModuleNotFoundError`
- [ ] Full pytest suite passes with no ClamAV failures
- [ ] App launches: `./xsmbseek --mock`
- [ ] Rollback commit is on branch and pushed
- [ ] Incident notes record the defect, rollback timestamp, and reviewer

---

## Rollback drill record (soft rollback)

To be filled in during the C7 drill.

```
Drill date: ___________
Drilled by: ___________

Steps executed:
1. Set clamav.enabled=false in conf/config.json at: ___________
2. Relaunched app at: ___________
3. Dashboard bulk extract — no dialog: PASS / FAIL
4. Server-list batch extract — no dialog: PASS / FAIL
5. Files in quarantine (no promotion): PASS / FAIL
6. Restored clamav.enabled=true at: ___________
7. Re-verified dialog reappears: PASS / FAIL

Overall drill result: PASS / FAIL
Notes:
```
