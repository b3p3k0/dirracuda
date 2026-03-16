# FTP Phase Task Cards (Claude-Ready)

Use one card at a time. Do not merge phases unless explicitly instructed.

---

## Card 1: Dashboard Scan Split

Goal:
Introduce separate scan launch actions for SMB and FTP while preserving current lock/stop behavior.

Scope:

1. Rename current primary scan button label to `Start SMB Scan`.
2. Add new `Start FTP Scan` button and handler route.
3. Keep one-active-scan-at-a-time behavior.
4. Ensure stop/retry/disabled states still work.

Primary touch targets:

1. `gui/components/dashboard.py`
2. `gui/utils/scan_manager.py` (only if handler routing requires minimal extension)

Definition of done:

1. Dashboard visibly shows both SMB and FTP scan actions.
2. SMB path executes exactly as before.
3. FTP action can enter a dedicated launch route without affecting SMB state.

Regression checks:

1. Start/stop SMB scan from dashboard.
2. External lock detection still disables scan actions correctly.
3. Retry stop flow still works.

Out of scope:

1. FTP backend implementation.
2. FTP DB schema.

Prompt seed:

```text
Implement Card 1 from docs/dev/ftp_module/FTP_PHASE_TASK_CARDS.md.
Constraints:
- Preserve SMB behavior.
- Keep scan lock semantics unchanged.
- Make minimal, focused edits.
Deliver:
- Code changes
- Brief diff summary
- Regression test notes
```

---

## Card 2: FTP Workflow and CLI Skeleton

Goal:
Create an isolated FTP command path with familiar CLI ergonomics and progress output.

Scope:

1. Add FTP workflow shell (discover/access stages).
2. Add FTP CLI entry point (new command or explicit mode path).
3. Emit progress lines in GUI-compatible format.
4. Wire dashboard FTP launch route to this new path.

Primary touch targets:

1. `gui/utils/backend_interface/interface.py` (FTP run path)
2. `gui/utils/scan_manager.py` (protocol-specific launch branch)
3. New FTP workflow/command modules under `commands/` and/or `shared/`.

Definition of done:

1. FTP launch path runs without crashing.
2. Progress updates stream to dashboard for FTP scans.
3. SMB CLI path remains untouched.

Regression checks:

1. SMB scan start/stop still passes.
2. FTP scan can start and report lifecycle states.

Out of scope:

1. Real FTP auth/list verification logic.
2. FTP storage schema writes.

Prompt seed:

```text
Implement Card 2 from docs/dev/ftp_module/FTP_PHASE_TASK_CARDS.md.
Requirements:
- Separate FTP module path.
- Progress output compatible with existing GUI parser expectations.
- No functional SMB regressions.
```

---

## Card 3: FTP Schema and Persistence Layer

Goal:
Add FTP sidecar storage and protocol coexistence visibility.

Scope:

1. Add `ftp_servers` table.
2. Add `ftp_access` summary table.
3. Add idempotent migration logic.
4. Add protocol presence query/view resolving `has_smb`, `has_ftp`, `both`.

Primary touch targets:

1. `tools/db_schema.sql`
2. `shared/db_migrations.py`
3. `shared/database.py`
4. `gui/utils/database_access.py`

Definition of done:

1. FTP records persist across runs.
2. SMB and FTP can coexist for same IP without collisions.
3. Presence layer correctly marks dual-protocol hosts.

Regression checks:

1. Existing SMB queries still work.
2. Database open/migration path still works for older DB files.

Out of scope:

1. FTP browser logic.
2. Value/ranking filters.

Prompt seed:

```text
Implement Card 3 from docs/dev/ftp_module/FTP_PHASE_TASK_CARDS.md.
Requirements:
- Sidecar FTP tables (no destructive SMB schema rewrite).
- Idempotent migrations.
- Presence view/query for has_smb/has_ftp/both.
```

---

## Card 4: FTP Discovery Reliability

Goal:
Implement robust anonymous FTP discovery and verification.

Scope:

1. Shodan candidate query for FTP.
2. Port 21 reachability verification.
3. Anonymous login verification.
4. Root listing verification.
5. Persist verified/failed outcomes with reason codes.

Primary touch targets:

1. New FTP discover modules under `commands/`.
2. FTP workflow execution path.
3. `shared/database.py` FTP persistence calls.

Definition of done:

1. Verified FTP hosts are stored as successful.
2. Failures include categorized reason (`connect_fail`, `auth_fail`, `list_fail`, `timeout`).
3. Scan summary reports candidate vs verified counts.

Regression checks:

1. SMB discovery still runs cleanly.
2. FTP timeouts do not hang scan lifecycle.

Out of scope:

1. Advanced value scoring.
2. Deep recursive content prioritization.

Prompt seed:

```text
Implement Card 4 from docs/dev/ftp_module/FTP_PHASE_TASK_CARDS.md.
Requirements:
- Anonymous FTP verification requires login + listing success.
- Write categorized failure reasons.
- Keep logs/progress operator-friendly.
```

---

## Card 5: FTP Probe Snapshot and Browser Download MVP

Goal:
Enable FTP browse/download from GUI with SMB-like safety constraints.

Scope:

1. Add FTP navigator helper for list/read/download.
2. Add FTP browser window path (or protocol branch in shared browser component).
3. Save FTP probe snapshots to JSON cache.
4. Download to quarantine only.

Primary touch targets:

1. New FTP browser helper under `shared/`.
2. `gui/components/file_browser_window.py` or FTP-specific browser component.
3. `gui/components/server_list_window/*` or FTP server list equivalent.
4. `shared/quarantine.py` integration points as needed.

Definition of done:

1. Operator can browse FTP directory tree from app.
2. Operator can download files to quarantine.
3. FTP probe snapshot is written and re-openable.

Regression checks:

1. SMB browse/download unchanged.
2. Cancel/timeout behavior remains responsive.

Out of scope:

1. Full normalized artifact DB persistence.
2. Content ranking.

Prompt seed:

```text
Implement Card 5 from docs/dev/ftp_module/FTP_PHASE_TASK_CARDS.md.
Requirements:
- Read-only remote operations.
- Quarantine download parity.
- Keep SMB browser behavior unchanged.
```

---

## Card 6: QA, Hardening, and Documentation

Goal:
Stabilize FTP MVP, verify no SMB regressions, and document known limits.

Scope:

1. Add/expand tests for FTP pipeline reliability.
2. Add regression checklist for SMB flows.
3. Update user/developer documentation.
4. Capture known MVP limitations explicitly.

Primary touch targets:

1. Test files and/or QA docs.
2. Root `README.md` and relevant docs under `docs/`.
3. `docs/dev/ftp_module/` summary updates.

Definition of done:

1. Repeatable test results recorded.
2. SMB baseline behaviors validated post-merge.
3. FTP operator documentation complete enough for first users.

Regression checks:

1. Dashboard launch/stop controls.
2. SMB discovery, access, browse baseline.
3. FTP discovery, browse, download baseline.

Out of scope:

1. Post-MVP refactor to unified normalized artifact DB.

Prompt seed:

```text
Implement Card 6 from docs/dev/ftp_module/FTP_PHASE_TASK_CARDS.md.
Requirements:
- Deliver a concise test report.
- Update docs with capabilities and limitations.
- Explicitly call out deferred refactor work.
```

---

## Operator Notes for Prompting

1. Run one card at a time.
2. Require file-level change summaries and regression notes on every card.
3. Block next card until current card passes DoD and regression checks.
