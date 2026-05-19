# C0 - Contract Freeze And Baseline Plan

## Context

Before any web UI code lands, C0 records the actual current state of the repo as a
frozen baseline. The document created here (`docs/dev/webui/BASELINE_CONTRACTS.md`)
is the reference point for every subsequent card. It must be written before source
files are touched so that "before" line counts, tab order, and test state are
authoritative.

All baseline commands (git status, line counts, test runs) are executed fresh
during C0 execution. Planning-phase snapshots are discarded; only execution-time
output is recorded in the document.

---

## Static Facts (not time-sensitive, confirmed during planning)

### Current Experimental Tab Order (registry.py:29-57)

From `gui/components/experimental_features/registry.py` `_get_features()`:

| Position | feature_id | label    |
|----------|-----------|----------|
| 0        | se_dork   | SearXNG  |
| 1        | reddit    | Reddit   |
| 2        | dorkbook  | Dorkbook |
| 3        | keymaster | Keymaster|

`Web UI` tab is **absent** (expected - added in C7).
Target order after C7: SearXNG, Reddit, **Web UI**, Dorkbook, Keymaster.

### Canonical Entrypoint Confirmation

- `./dirracuda`: shebang `#!/usr/bin/env python3`, docstring "Dirracuda - GUI".
  Canonical runtime entrypoint.
- `gui/main.py` (40 lines): docstring explicitly states
  "This module is import-compatible only. Runtime launch must use `./dirracuda`."
  No `main()` or runnable `__main__` block. Shim-only.

### xvfb-run

Available at `/usr/bin/xvfb-run`.

### dirracuda line count

`dirracuda` is **exactly 1700 lines** - at the hard limit. Any future card
touching this file must propose modularization or confirm a no-growth edit before
proceeding.

---

## Execution Steps

All commands below are run fresh at execution time. Output is captured verbatim
into `BASELINE_CONTRACTS.md`.

### Step 1 - Re-run git status at execution time

```bash
git status --short --branch
```

Record exact output. Do not use the planning-phase snapshot (docs were committed
in 47ee18e; branch should now be clean).

### Step 2 - Record current Experimental tab order

Read `gui/components/experimental_features/registry.py` and record the `_get_features()`
return list in order. (Confirmed during planning; re-verify if the file has changed.)

### Step 3 - Collect likely-touched file line counts

```bash
wc -l \
  gui/components/experimental_features/registry.py \
  gui/components/experimental_features_dialog.py \
  gui/components/experimental_features/se_dork_tab.py \
  gui/components/experimental_features/reddit_tab.py \
  gui/components/experimental_features/dorkbook_tab.py \
  gui/components/experimental_features/keymaster_tab.py \
  gui/tests/test_experimental_features_dialog.py \
  gui/main.py \
  dirracuda \
  shared/workflow.py \
  gui/utils/backend_interface/interface.py \
  gui/utils/scan_manager.py \
  requirements.txt
```

Record exact output.

### Step 4 - Confirm canonical entrypoint and shim

```bash
head -5 dirracuda
sed -n '1,30p' gui/main.py
```

Record enough output to confirm `./dirracuda` is canonical. Record the first 30
lines of `gui/main.py` so the import-only runtime-launch warning is captured.

### Step 5 - Confirm xvfb-run availability

```bash
which xvfb-run
```

Record path or "not found".

### Step 6 - Run baseline test 1 (required by C0 card)

```bash
./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q
```

Record exact output, including pass/fail counts and any failure tracebacks.

### Step 7 - Run baseline test 2 (required by C0 card)

```bash
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
```

Record exact output. If any test fails:
- Record the full failure summary.
- State whether it appears pre-existing (no web UI code exists yet).
- Do not fix failures in C0.

### Step 8 - Create BASELINE_CONTRACTS.md

Write `docs/dev/webui/BASELINE_CONTRACTS.md` containing all of the above output
verbatim plus:
- tab order table (current and target)
- entrypoint/shim confirmation
- xvfb-run status
- note on dirracuda 1700-line limit
- unblock steps for any recorded failure

### Step 9 - Verify

```bash
wc -l docs/dev/webui/BASELINE_CONTRACTS.md
git status --short --branch
```

Confirm only the new doc appears modified. Confirm the new file is under 1700 lines.

---

## Files Expected to Change

| File | Action |
|------|--------|
| `docs/dev/webui/BASELINE_CONTRACTS.md` | Create (new, docs only) |

No product source files are touched. No commit.

---

## Risks / Blockers

- Pre-existing quick-lane failure was observed during planning
  (`test_s10_se_dork_probe_task_lifecycle_success` - `FakeSeDorkConnection.commit_calls`
  == 0, expected 1). C0 records it; RA/HI should triage before C1.
- `dirracuda` is at exactly 1700 lines. Flagged in the document.
- No other blockers for C0.
