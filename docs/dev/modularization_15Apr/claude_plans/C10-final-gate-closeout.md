# C10 — Final Gate Closeout Plan

## Context

C9 remediation is complete. The modularization project has successfully extracted `DashboardWidget` and all browser classes from their monolithic files into separate packages (`gui/dashboard/`, `gui/browsers/`), while preserving backward-compatible shims at the original module paths. The shims bind all patch-sensitive names at module scope so frozen test monkeypatch paths remain valid.

Current state heading into C10:
- `gui/components/dashboard.py` is now a **58-line shim** (baseline C0: 3331 lines)
- `gui/components/unified_browser_window.py` is now a **105-line shim** (baseline C0: 3238 lines)
- Last known suite results: 1045 passed, 2 pre-existing failures
- Pre-existing failures are exclusively in `test_database_access_protocol_writes.py` (DB schema gap, out of scope)

**C10 goal:** Run the canonical validation suite (A–E), confirm all acceptance criteria, and close out the gate with documented evidence in `BASELINE_CONTRACTS.md`.

No code changes are expected. If a regression appears, fix minimally and re-run.

---

## Step 1 — Validation A: Compile Smoke

Run `py_compile` across all files touched during C6–C9 modularization:

```bash
python3 -m py_compile \
  gui/components/dashboard.py \
  gui/components/dashboard_scan.py \
  gui/components/dashboard_batch_ops.py \
  gui/components/unified_browser_window.py \
  gui/dashboard/__init__.py \
  gui/dashboard/widget.py \
  gui/browsers/__init__.py
```

**Accept:** Silent exit (no SyntaxError output). **Reject:** Any compile error → fix and re-run.

---

## Step 2 — Validation B: Canonical Import Smoke

Checks both shim contracts: ubw module-attribute patches (§2c/§2d) and dashboard module-scope patch-sensitive names (§2a). The dashboard block explicitly covers the names that regressed during C9 remediation (`show_ftp_scan_dialog`, `show_http_scan_dialog`) plus all others listed in §2a.

```bash
./venv/bin/python -c "
import gui.browsers
import gui.components.unified_browser_window as ubw
from gui.components.unified_browser_window import (
    open_ftp_http_browser, open_smb_browser,
    open_file_viewer, open_image_viewer,
    UnifiedBrowserCore, FtpBrowserWindow, HttpBrowserWindow, SmbBrowserWindow,
    _extract_smb_banner, _coerce_bool, _format_file_size,
)
from gui.components.dashboard import DashboardWidget as OldDash
from gui.dashboard import DashboardWidget as NewDash
assert OldDash is NewDash

# ubw module-attribute patch contracts (§2c / §2d)
assert hasattr(ubw, 'threading'), 'ubw.threading missing'
assert hasattr(ubw, 'messagebox'), 'ubw.messagebox missing'
assert hasattr(ubw, 'queue'), 'ubw.queue missing'
assert hasattr(ubw, 'tk'), 'ubw.tk missing'
assert hasattr(ubw, 'ttk'), 'ubw.ttk missing'

# dashboard shim patch-sensitive names (§2a)
import gui.components.dashboard as dash_shim
assert hasattr(dash_shim, 'messagebox'), 'dash.messagebox missing'
assert hasattr(dash_shim, 'threading'), 'dash.threading missing'
assert hasattr(dash_shim, 'tk'), 'dash.tk missing'
assert hasattr(dash_shim, 'ttk'), 'dash.ttk missing'
assert hasattr(dash_shim, 'extract_runner'), 'dash.extract_runner missing'
assert hasattr(dash_shim, 'dispatch_probe_run'), 'dash.dispatch_probe_run missing'
assert hasattr(dash_shim, 'probe_patterns'), 'dash.probe_patterns missing'
assert hasattr(dash_shim, 'get_probe_snapshot_path_for_host'), 'dash.get_probe_snapshot_path_for_host missing'
assert hasattr(dash_shim, 'show_unified_scan_dialog'), 'dash.show_unified_scan_dialog missing'
assert hasattr(dash_shim, 'show_ftp_scan_dialog'), 'dash.show_ftp_scan_dialog missing'
assert hasattr(dash_shim, 'show_http_scan_dialog'), 'dash.show_http_scan_dialog missing'
assert hasattr(dash_shim, 'show_reddit_grab_dialog'), 'dash.show_reddit_grab_dialog missing'
assert hasattr(dash_shim, 'run_ingest'), 'dash.run_ingest missing'
assert hasattr(dash_shim, 'create_quarantine_dir'), 'dash.create_quarantine_dir missing'

print('IMPORT SMOKE: PASS')
"
```

**Accept:** `IMPORT SMOKE: PASS`. **Reject:** Any `ImportError`, `AssertionError`, or attribute-missing → diagnose the relevant shim, fix, re-run.

---

## Step 3 — Validation C: Full Regression Suite

```bash
xvfb-run -a ./venv/bin/python -m pytest --tb=short -q > /tmp/c10_full.txt 2>&1; RESULT=$?
cat /tmp/c10_full.txt
echo "pytest exit=${RESULT}"
```

**Accept:** Exactly 2 failures — both from `test_database_access_protocol_writes.py` (`test_manual_upsert_inserts_smb_ftp_http_rows` and `test_manual_upsert_http_same_ip_different_ports_create_distinct_rows`). Any additional failure is a regression and must be root-caused and fixed before proceeding.

Record from output: **collected**, **passed**, **failed**, **skipped**, **pytest exit code**.

---

## Step 4 — Validation D: Coverage Snapshot

```bash
xvfb-run -a ./venv/bin/python -m pytest --cov=shared --cov=gui --cov-report=term-missing -q > /tmp/c10_cov.txt 2>&1; RESULT=$?
cat /tmp/c10_cov.txt
echo "pytest exit=${RESULT}"
```

**Accept:** Same pass/fail counts as Step 3. Capture total coverage % for the doc update.

---

## Step 5 — Validation E: Line Counts

```bash
wc -l gui/components/dashboard.py gui/components/unified_browser_window.py
```

**Accept:** Both counts significantly below baseline (3331 and 3238 respectively). Combined must be below 6569.

Record exact numbers for the doc update.

---

## Step 6 — Update BASELINE_CONTRACTS.md

**File:** `docs/dev/modularization_15Apr/BASELINE_CONTRACTS.md`

Two edits required:

### Edit A — Add "Appendix C — C10 Final Validation Results"

Append a new appendix after the end of the `### B.2 shared/tests/` table (the last content block in Appendix B) with three subsections:

**C.1 Line Counts (C10)**
Table with current line counts for both shim files + delta from C0 baseline.

**C.2 Test Results (C10)**
Table with: Collected / Passed / Failed / Skipped / pytest exit — pulled from Step 3 output.

**C.3 Coverage Snapshot (C10)**
Total coverage % pulled from Step 4 output, plus a note on delta vs. C0 (C0 had no coverage baseline recorded — note as first coverage capture).

### Edit B — Add C10 row to `## Change Log`

Append to the Change Log table (currently ends with the C0 row):

```
| C10 | 2026-04-15 | Final gate closeout: full validation suite; Appendix C evidence recorded | PASS | PENDING HI | PENDING HI |
```

After the HI manual gate (full regression in live app), the MANUAL and OVERALL columns get updated to PASS.

---

## Contingency — If a Regression Appears

1. Read the failure output carefully — identify the specific test and assertion.
2. Check if the failure is in a shim re-export (likely: missing module-scope binding) or in extracted logic (less likely: behavioral drift).
3. Fix minimally in the relevant file — do NOT refactor or clean surrounding code.
4. Re-run the affected test file with `-v` to confirm fix, then re-run Steps 3–5 in full.
5. Document the regression, root cause, and fix in the Appendix C section of the doc update.

---

## Acceptance Criteria Summary

| Check | Pass condition |
|---|---|
| Compile smoke | No SyntaxError from py_compile |
| Import smoke | `IMPORT SMOKE: PASS` printed |
| No new failures | Exactly 2 failures (both pre-existing DB failures) |
| Line count reduced | dashboard.py < 3331; unified_browser_window.py < 3238 |
| Doc updated | Appendix C added + C10 Change Log row present |

---

## Files to Modify

| File | Edit |
|---|---|
| `docs/dev/modularization_15Apr/BASELINE_CONTRACTS.md` | Add Appendix C + C10 Change Log row |

No code file changes expected. Shim files (`dashboard.py`, `unified_browser_window.py`) only touched if a regression surfaces.

---

## HI Manual Gate Reminder (Post-AUTOMATED)

C10 manual gate (from `## 6) Card Gates` in BASELINE_CONTRACTS.md):
> Full regression: SMB + FTP + HTTP scan, server list, browser, extract, ClamAV, Reddit grab.

After HI signoff, update the MANUAL and OVERALL columns in the C10 Change Log row to PASS.
