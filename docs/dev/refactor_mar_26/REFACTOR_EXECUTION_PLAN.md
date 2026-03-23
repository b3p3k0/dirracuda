# SMBSeek Refactor Execution Plan (Behavior-Preserving)

Date: 2026-03-21
Owner model: Codex (lead) + Claude (implementation worker)
Source alignment: `docs/dev/refactor_mar_26/assessment.md` + HI priority update (2026-03-21)

## Mission
Refactor and reorganize the codebase for maintainability without changing GUI behavior, CLI behavior, or runtime outputs used by existing workflows.

## Non-Negotiable Constraints
1. No UI changes and no functional behavior changes.
2. Work one small issue at a time: confirm, fix surgically, validate, report, pause.
3. Preserve compatibility for migrations, config/data contracts, and existing DB interactions.
4. Prefer root-cause modularization over cosmetic edits.
5. Keep hot paths performant (scan orchestration, subprocess I/O, GUI responsiveness).
6. Every step must include targeted validation commands and PASS/FAIL reporting.
7. No commits unless HI explicitly says `commit`.

## File Size Rubric (Authoritative)
- `<=1000`: excellent
- `1001-1200`: good
- `1201-1500`: acceptable
- `1501-1800`: poor
- `>1800`: unacceptable unless clearly justified and documented

## Current Baseline (Key Hotspots)
- `gui/utils/db_tools_engine.py`: 3047 (unacceptable)
- `gui/components/dashboard.py`: 2805 (unacceptable)
- `gui/components/scan_dialog.py`: 2075 (unacceptable)
- `shared/database.py`: 1221 (acceptable)
- `gui/components/unified_scan_dialog.py`: 1280 (acceptable)
- `gui/utils/scan_manager.py`: 1206 (good)
- `gui/components/batch_extract_dialog.py`: 1200 (good)
- `gui/components/server_list_window/window.py`: 1193 (good)
- `xsmbseek`: 1007 (good)
- `smbseek`: 373 (excellent)
- `ftpseek`: 80 (excellent)
- `httpseek`: 80 (excellent)

## Priority Alignment

### Priority 1: File Size Reduction
Goal: bring high-risk files down by responsibility-based splitting, preserving public interfaces.

First-wave split targets (already supported by analyst assessment):
1. `gui/utils/scan_manager.py`
2. `gui/components/unified_scan_dialog.py`
3. `gui/components/batch_extract_dialog.py`
4. `gui/components/server_list_window/window.py`

Second-wave split targets (largest technical debt):
1. `gui/utils/db_tools_engine.py`
2. `gui/components/dashboard.py`
3. `gui/components/scan_dialog.py`
4. `shared/database.py`

### Priority 2: Deduplication / Consolidation (Seeker-Centric)
Primary scope: the three seeker entry paths and their workflow bootstrap patterns.

Immediate dedup candidates:
1. Shared CLI bootstrap for `ftpseek` and `httpseek` (parser skeleton, conflict checks, migration preflight, exception-to-exit mapping).
2. Shared workflow factory helper for `shared/ftp_workflow.py` and `shared/http_workflow.py` (same config/output bootstrap).
3. Optional: normalize command-build logic in `gui/utils/backend_interface/interface.py` where SMB/FTP/HTTP builders are structurally duplicated.

Constraint: preserve existing flags, defaults, output text contracts, and exit codes.

### Priority 3: Tool Suite Reorganization (GUI-Centric)
Desired structure direction:
- Keep `xsmbseek` in repo root as front-and-center entry point.
- Move CLI seeker implementations under `cli/` (e.g., `cli/smbseek.py`, `cli/ftpseek.py`, `cli/httpseek.py`).
- Perform a hard cutover in this pass (no root compatibility wrappers for `smbseek|ftpseek|httpseek`).

Hard-cutover implementation strategy:
1. Move seeker implementations into `cli/`.
2. Remove root seeker entry scripts (`smbseek`, `ftpseek`, `httpseek`).
3. Update internal path checks and subprocess launch code to target `cli/*`.
4. Update docs/help text where invocation paths changed.

Rationale: fulfills GUI-centric organization goal decisively while still preserving behavior through targeted regression checks.

## Execution Sequence (One-Issue Slices)

### Slice 1 (start here): Seeker Dedup Scaffold
Scope:
1. Introduce shared bootstrap helper module for FTP/HTTP seeker entry logic.
2. Refactor `ftpseek` and `httpseek` to use helper.
3. Keep behavior/output identical.

Validation:
1. `python -m pytest gui/tests/test_backend_interface_commands.py -q`
2. `python ./ftpseek --help`
3. `python ./httpseek --help`
4. (Optional smoke) `python ./ftpseek --country US --config conf/config.json --quiet`
5. (Optional smoke) `python ./httpseek --country US --config conf/config.json --quiet`

### Slice 2: CLI Hard Cutover (No Wrappers)
Scope:
1. Create `cli/` package for seeker entry implementations.
2. Remove root `smbseek|ftpseek|httpseek` entry scripts.
3. Update backend path resolution and validations to require `cli/smbseek.py`, `cli/ftpseek.py`, `cli/httpseek.py`.

Validation:
1. `python -m pytest gui/tests/test_backend_interface_commands.py -q`
2. `python -m pytest gui/tests/test_scan_manager_config_path.py -q`
3. `python ./cli/smbseek.py --help && python ./cli/ftpseek.py --help && python ./cli/httpseek.py --help`
4. `./xsmbseek --mock` startup smoke (ensure GUI subprocess wiring still resolves seeker paths)

### Slice 3: `scan_manager.py` Modular Split
Scope:
1. Extract lock management + protocol executor concerns into dedicated modules.
2. Keep `get_scan_manager()` and existing public call signatures stable.

Validation:
1. `python -m pytest gui/tests/test_backend_progress_ftp.py -q`
2. `python -m pytest gui/tests/test_backend_progress_http.py -q`
3. `python -m pytest gui/tests/test_scan_manager_config_path.py -q`

### Slice 4: `unified_scan_dialog.py` + `batch_extract_dialog.py` Splits
Scope:
1. Extract template/validation/util blocks into focused helpers.
2. Preserve UI layout and callback wiring exactly.

Validation:
1. `python -m pytest gui/tests/test_ftp_scan_dialog.py -q`
2. Targeted GUI smoke via `./xsmbseek --mock` (HI/manual checklist)

### Slice 5+: Remaining oversize modules
Process repeats with narrow scope and tests per module.

## QA/QC Gate Template (for each slice)
Report using this exact structure:
- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + short steps)

## Risks and Controls
1. Risk: hidden behavior changes from refactor.
   Control: preserve public interfaces and output strings; add regression tests before/with split.
2. Risk: path breakage after hard cutover.
   Control: explicit path-resolution updates + focused GUI/backend interface tests + mock startup smoke.
3. Risk: GUI scan orchestration regressions.
   Control: keep `BackendInterface` contracts unchanged; run targeted progress/config tests each slice.
4. Risk: long-running branch drift.
   Control: small slices, isolated commits (when requested), and per-slice PASS/FAIL gate.

## Immediate Next Step
Execute Slice 1 through Claude under Codex review; Codex performs final QA/QC and HI report.

---

## Refactor Closeout (2026-03-23)

### Overall Status
- PASS
- Closeout sweep result: **644 passed / 0 failed**
- Pre/post verification porcelain snapshots were identical: **no code edits applied during closeout sweep**

### Final Verification Gates (Slice 14A)

| Gate | Description | Status | Tests |
|---|---|---|---|
| Pre | Baseline porcelain snapshot | PASS | — |
| 1 | Syntax sweep (`compileall`) | PASS | — |
| 2 | API/import smoke | PASS | — |
| 3 | No-back-import asserts (7 modules) | PASS | — |
| 4 | Data-layer regression | PASS | 162 |
| 5 | DB tools dialog regression | PASS | 37 |
| 6 | Scan manager/progress regression | PASS | 32 |
| 7 | Scan dialog family regression (xvfb) | PASS | 170 |
| 8 | Dashboard/browser/routing regression | PASS | 147 |
| 9 | Shared-layer regression | PASS | 96 |
| 10 | Line-count snapshot + rubric bucketing | PASS | — |
| Post | Final porcelain snapshot | PASS (identical to pre) | — |

### Rubric Summary

| Bucket | Count |
|---|---|
| <=1000 | 98 |
| 1001-1200 | 0 |
| 1201-1500 | 0 |
| 1501+ | 0 |

All tracked files are now in the `<=1000` bucket.

### Top Line-Count Snapshot (Post-Closeout)

| Lines | File |
|---:|---|
| 952 | `gui/utils/database_access.py` |
| 940 | `gui/utils/settings_manager.py` |
| 919 | `gui/components/dashboard_bulk_ops.py` |
| 917 | `gui/components/http_scan_dialog.py` |
| 900 | `gui/utils/db_tools_engine.py` |
| 899 | `gui/components/ftp_scan_dialog.py` |
| 878 | `gui/utils/style.py` |
| 845 | `shared/db_migrations.py` |
| 843 | `gui/components/scan_dialog.py` |
| 818 | `gui/components/unified_scan_dialog.py` |
| 806 | `shared/database.py` |
| 796 | `gui/components/file_browser_window.py` |
| 732 | `gui/components/app_config_dialog.py` |
| 724 | `gui/components/ftp_browser_window.py` |
| 689 | `gui/utils/scan_manager.py` |

### Major Hotspot Reductions (Program Highlights)

| File | Before | After |
|---|---:|---:|
| `gui/utils/db_tools_engine.py` | 3047 | 900 |
| `gui/components/dashboard.py` | 2805 | 669 |
| `gui/components/scan_dialog.py` | 2075 | 843 |
| `shared/database.py` | 1221 | 806 |
| `gui/components/db_tools_dialog.py` | 1305 | 591 |
| `gui/components/file_browser_window.py` | 1197 | 796 |
| `gui/utils/scan_manager.py` | 996 | 689 |

### Final Statement
Behavior unchanged except explicitly listed bugfixes.  
Closeout required no additional bugfixes.
