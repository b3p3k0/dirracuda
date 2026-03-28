# Optional ClamAV Quarantine Scan - Draft Spec

Date: 2026-03-27
Status: Draft for HI review
Execution model: one small issue/card at a time with explicit PASS/FAIL gates.

## Locked Decisions (HI - 2026-03-27)

1. Phase 1 scope is bulk extract only.
2. Failure policy default is fail-open (feature is optional).
3. Clean files are moved (not copied) to extracted.
4. Add a quarantine subfolder for known-bad files.
5. GUI can expose more than one setting; user prefers GUI-managed settings.

## Problem Statement

Dirracuda currently writes extracted/downloaded files into quarantine but does not run antivirus checks before operator use. The requested behavior is:

- optional ClamAV scanning of files downloaded to quarantine
- clean files moved to `~/.dirracuda/extracted`
- infected files moved under quarantine known-bad path
- results summary dialog after bulk extract
- summary dialog can be muted for rest of current app session (auto-unmute on next app launch)

## Current Runtime Baseline (Observed)

1. Quarantine helpers:
- `shared/quarantine.py` creates host/date directories and logs `activity.log` entries.

2. SMB extract flows:
- `gui/utils/extract_runner.py:run_extract()` writes files to quarantine and returns summary.
- Bulk callers:
  - `gui/components/dashboard.py` (post-scan bulk extract)
  - `gui/components/server_list_window/actions/batch.py` (server-list batch extract)

3. Related but out-of-scope phase 1 flows:
- Single-host extract (`gui/components/server_list_window/details.py`)
- Browser file downloads (`gui/components/unified_browser_window.py`)

4. Config exposure today:
- `gui/components/app_config_dialog.py` has runtime path/API settings but no AV controls.

## Goals

1. Add optional ClamAV scanning for bulk extract workflows without breaking behavior when disabled.
2. Move clean files to extracted root with deterministic structure.
3. Move infected files to a quarantine known-bad subfolder.
4. Keep scanner-error files in quarantine and log clear evidence.
5. Provide actionable scan summary UI with session-only mute.
6. Keep architecture ready for future browser-download integration (do not hard-wire to current bulk-only callers).

## Non-Goals (Phase 1)

1. Real-time on-access filesystem scanning (`clamonacc`) orchestration.
2. Automatic signature updates (`freshclam`) management from GUI.
3. Browser download AV enforcement (FTP/HTTP/SMB download buttons).
4. Single-host extract AV integration.

## Proposed Functional Contract

### A) Feature toggle and defaults

- New config section: `clamav` (disabled by default).
- If disabled: bulk extract behavior remains unchanged.

Suggested defaults:

```json
"clamav": {
  "enabled": false,
  "backend": "auto",
  "clamscan_path": "clamscan",
  "clamdscan_path": "clamdscan",
  "timeout_seconds": 60,
  "promote_clean_files": true,
  "extracted_root": "~/.dirracuda/extracted",
  "known_bad_subdir": "known_bad",
  "fail_open": true,
  "show_results_dialog": true,
  "max_parallel_scans": 2
}
```

Notes:
- `backend=auto`: prefer `clamdscan`, fallback to `clamscan`.
- `fail_open=true`: extraction continues even if scanner unavailable/errors.
- Recommended known-bad name: `known_bad`.

### B) Scan and file-placement behavior

For each downloaded file in phase-1 bulk extract:

1. Scan file via selected backend.
2. If `clean` and `promote_clean_files=true`, move to extracted root.
3. If `infected`, move to quarantine known-bad subtree.
4. If `error`, leave in original quarantine location and record error detail.

Deterministic destination layout:

- Clean:
`~/.dirracuda/extracted/<host>/<YYYYMMDD>/<share>/<relative_path>`

- Infected:
`~/.dirracuda/quarantine/known_bad/<host>/<YYYYMMDD>/<share>/<relative_path>`

### C) Result model additions

Extend extract summary with AV block:

```json
"clamav": {
  "enabled": true,
  "backend_used": "clamscan",
  "files_scanned": 120,
  "clean": 114,
  "infected": 4,
  "errors": 2,
  "promoted": 114,
  "known_bad_moved": 4,
  "infected_items": [
    {"path": "...", "signature": "Eicar-Test-Signature", "moved_to": "..."}
  ],
  "error_items": [
    {"path": "...", "error": "scanner timeout"}
  ]
}
```

### D) UI behavior

1. Config dialog fields (recommended phase 1 GUI set):
- `Enable ClamAV scan for bulk extract files` (checkbox)
- `Scanner backend` (`Auto`, `clamdscan`, `clamscan`)
- `Scanner timeout (seconds)`
- `Clean files destination` (path + browse)
- `Known-bad subfolder name` (default `known_bad`)
- `Show ClamAV result dialogs` (checkbox)

2. Results dialog after bulk extract:
- Totals: scanned, clean, promoted, infected, errors.
- Table rows for infected/error files.
- Control: `Mute ClamAV result dialogs until restart`.

3. Session mute contract:
- Mute state is in-memory only for current process lifetime.
- Not persisted to `gui_settings.json`.
- On next app launch, dialog visibility resets.

## Long-Term Integration Guardrail (Avoid Self-Blocking)

Introduce a reusable post-processing seam now:

- `QuarantinePostProcessor` interface (or equivalent helper contract)
  - input: downloaded file path + metadata
  - output: placement decision + summary item

Phase 1 bulk extract uses it first.
Later phases can reuse the same contract for browser downloads without rewriting scanner logic.

## Compatibility and Risk Controls

1. Do not change behavior when `clamav.enabled=false`.
2. Guard scanner invocations with availability checks and per-file timeouts.
3. Never auto-delete infected files.
4. Keep UI thread unblocked; scans run in worker context.
5. Ensure deterministic path mapping for moved files.

## Open Decisions Remaining

None blocking for phase 1.

Locked for phase 1:
1. Bulk scope includes both dashboard post-scan bulk extract and server-list batch extract.
2. Scanner-error files remain in-place in original quarantine path.

Optional future decisions:
1. Whether `known_bad_subdir` should later become a fully separate absolute path setting.
2. Whether to add an optional `unscanned/` bucket for scanner-error files in a later phase.

