# HTTP + FTP Bulk Extract — Task Cards

Date: 2026-04-01
Execution model: one card at a time, with explicit PASS/FAIL gates.

## Locked Intent

1. Extend bulk extract support to FTP and HTTP.
2. Keep SMB behavior stable.
3. Prefer surgical changes over broad refactors.
4. Use deterministic extraction source with clear runtime guards.

## Card C0: Plan + Contract Inventory (No code edits)

Issue:
- We need an implementation plan grounded in real call paths and existing data contracts.

Deliverables:
1. Confirm active extract call paths for dashboard + server list.
2. Confirm current protocol-specific blockers and test expectations.
3. Propose implementation sequence with risk controls.

Acceptance:
1. Plan explicitly names files and contracts.
2. Plan calls out assumptions + unknowns.
3. Plan includes validation commands.

HI test needed:
- No.

## Card C1: Protocol-Aware Extraction Core

Issue:
- Extraction engine is SMB-only; FTP/HTTP lack a shared bulk runner contract.

Scope:
1. Add protocol-aware extract helpers for FTP/HTTP while preserving SMB entrypoint.
2. Factor reusable guardrail logic (limits/extensions/summary updates) into shared internal helpers.
3. Keep output summary contract aligned with existing logs/dialogs.

Likely files:
1. `gui/utils/extract_runner.py`
2. New tests under `gui/tests/` (and/or `shared/tests/`) for helper contracts

Acceptance:
1. SMB runner behavior unchanged.
2. FTP/HTTP helper contracts deterministic and unit-tested.
3. ClamAV integration seam remains fail-open.

Validation:
```bash
python3 -m py_compile gui/utils/extract_runner.py
./venv/bin/python -m pytest gui/tests/test_extract_runner_clamav.py -q
```

HI test needed:
- No.

## Card C2: FTP Bulk Extract Implementation

Issue:
- FTP rows are currently skipped in server-list extract and unsupported in dashboard extract path.

Scope:
1. Build FTP file candidates from FTP probe snapshot.
2. Download via `FtpNavigator.download_file` with existing limits/filter semantics.
3. Surface actionable skip reason when probe snapshot is missing.

Likely files:
1. `gui/utils/extract_runner.py`
2. `gui/components/server_list_window/actions/batch.py`
3. `gui/components/dashboard.py`
4. New/updated tests in `gui/tests/`

Acceptance:
1. FTP extract no longer returns hardcoded "not yet supported." 
2. Missing snapshot -> deterministic skipped result (not failure/crash).
3. Extract summary + extracted flag behavior consistent with SMB path semantics.

Validation:
```bash
python3 -m py_compile gui/utils/extract_runner.py gui/components/server_list_window/actions/batch.py gui/components/dashboard.py
./venv/bin/python -m pytest gui/tests/test_action_routing.py -q
./venv/bin/python -m pytest gui/tests/test_dashboard_bulk_ops.py -q
```

HI test needed:
- Yes.
- Steps:
1. Probe at least one FTP host from server list.
2. Run batch extract on that FTP row.
3. Verify files appear in quarantine/extracted (per ClamAV settings), no SMB-transport error.

## Card C3: HTTP Bulk Extract Implementation

Issue:
- HTTP rows are currently skipped in server-list extract and can route into SMB-only logic in dashboard flow.

Scope:
1. Build HTTP file candidates from endpoint-aware HTTP probe snapshot.
2. Download via `HttpNavigator.download_file` with existing limits/filter semantics.
3. Handle endpoint identity robustly (`ip + port` cache lookup with safe fallback).

Likely files:
1. `gui/utils/extract_runner.py`
2. `gui/components/server_list_window/actions/batch.py`
3. `gui/components/dashboard.py`
4. New/updated tests in `gui/tests/`

Acceptance:
1. HTTP extract no longer returns hardcoded "not yet supported." 
2. Dashboard bulk extract for HTTP protocol does not call SMB transport logic.
3. Missing snapshot -> deterministic skipped result (not crash).

Validation:
```bash
python3 -m py_compile gui/utils/extract_runner.py gui/components/server_list_window/actions/batch.py gui/components/dashboard.py
./venv/bin/python -m pytest gui/tests/test_action_routing.py -q
./venv/bin/python -m pytest gui/tests/test_dashboard_bulk_ops.py -q
```

HI test needed:
- Yes.
- Steps:
1. Probe at least one HTTP host from server list.
2. Run batch extract on that HTTP row.
3. Verify downloaded files and protocol-correct behavior.

## Card C4: UI/Workflow Wiring Hardening

Issue:
- Multiple extract entry points can drift (dashboard, server-list, detail popup callback/fallback).

Scope:
1. Ensure all batch extract entry points route through protocol-aware handlers.
2. Remove stale SMB-only assumptions in user-facing messages.
3. Confirm per-row host_type handling is explicit.

Likely files:
1. `gui/components/server_list_window/actions/batch.py`
2. `gui/components/dashboard.py`
3. `gui/components/server_list_window/details.py` (only if fallback path needs guard/update)

Acceptance:
1. No remaining hardcoded FTP/HTTP extract skip for batch path.
2. No FTP/HTTP rows entering SMB-only extraction code.

Validation:
```bash
python3 -m py_compile gui/components/server_list_window/actions/batch.py gui/components/dashboard.py gui/components/server_list_window/details.py
./venv/bin/python -m pytest gui/tests/test_action_routing.py -q
```

HI test needed:
- Yes (quick smoke across all launch points).

## Card C5: Focused Regression + Docs

Issue:
- Behavior expansion needs targeted proof and updated operator docs.

Scope:
1. Add/adjust targeted tests for FTP/HTTP extraction behavior and previous skip assumptions.
2. Update docs describing bulk extract scope and any probe prerequisite.
3. Record automated and manual validation evidence.

Likely files:
1. `gui/tests/test_action_routing.py`
2. New/updated extraction tests in `gui/tests/`
3. `README.md` and this workspace docs if needed

Acceptance:
1. Protocol extraction behavior covered by tests.
2. Existing SMB tests remain green.
3. Documentation reflects new behavior and constraints.

Validation:
```bash
./venv/bin/python -m pytest gui/tests/test_action_routing.py gui/tests/test_dashboard_bulk_ops.py -q
./venv/bin/python -m pytest gui/tests/test_extract_runner_clamav.py gui/tests/test_browser_clamav.py -q
```

HI test needed:
- Yes.
- Steps:
1. SMB/FTP/HTTP batch extract smoke in active environment.
2. Confirm no regressions in SMB-only behavior and summary dialogs.
