# HTTP Phase Task Cards (Claude-Ready)

Use one card at a time. Do not merge phases unless HI explicitly approves.

---

## Card 1: Dashboard + HTTP Scan Dialog Entry

Goal:
Introduce HTTP scan launch controls that match SMB/FTP behavior while preserving shared lock/stop semantics.

Scope:

1. Add `Start HTTP Scan` action and routing from dashboard.
2. Add HTTP scan dialog with FTP-like layout and persistence behavior.
3. Add TLS verification toggle (default: allow insecure HTTPS verification).
4. Keep one-active-scan-at-a-time behavior unchanged.
5. Ensure stop/retry/disabled states still behave correctly for existing protocols.

Primary touch targets:

1. `gui/components/dashboard.py`
2. New `gui/components/http_scan_dialog.py`
3. `gui/utils/scan_manager.py` (minimal launch-branch extension only)

Definition of done:

1. Dashboard visibly shows SMB, FTP, and HTTP scan actions.
2. SMB/FTP launch behavior remains unchanged.
3. HTTP launch route opens dialog and passes validated scan options.
4. Dialog returns explicit TLS verification preference for downstream workflow.

Regression checks:

1. Start/stop SMB scan from dashboard.
2. Start/stop FTP scan from dashboard.
3. External lock detection still disables scan actions correctly.

Out of scope:

1. HTTP backend implementation.
2. HTTP DB schema writes.

Prompt seed:

```text
Implement Card 1 from docs/dev/http_module/HTTP_PHASE_TASK_CARDS.md.
Constraints:
- Preserve SMB and FTP behavior.
- Keep scan lock semantics unchanged.
- Reuse FTP scan dialog patterns where practical.
Deliver:
- Code changes
- Brief diff summary
- Regression check notes
```

---

## Card 2: HTTP Workflow + CLI Skeleton

Goal:
Create isolated HTTP CLI/workflow path with GUI-compatible progress output.

Scope:

1. Add `httpseek` CLI entry point.
2. Add HTTP workflow shell (candidate discovery + verification placeholders).
3. Emit progress lines in existing GUI-compatible format.
4. Wire dashboard HTTP launch to backend HTTP route.

Primary touch targets:

1. `gui/utils/backend_interface/interface.py`
2. `gui/utils/scan_manager.py`
3. New `commands/http/` modules
4. New `shared/http_workflow.py`

Definition of done:

1. HTTP launch path runs without crashing.
2. Progress updates stream to dashboard for HTTP scans.
3. SMB/FTP CLI paths remain untouched.

Regression checks:

1. SMB scan lifecycle unchanged.
2. FTP scan lifecycle unchanged.
3. HTTP scan starts and reports lifecycle status.

Out of scope:

1. Real HTTP verification/count logic.
2. HTTP DB persistence.

Prompt seed:

```text
Implement Card 2 from docs/dev/http_module/HTTP_PHASE_TASK_CARDS.md.
Requirements:
- Separate HTTP module path and CLI entrypoint.
- Progress output compatible with existing GUI parser expectations.
- No functional SMB/FTP regressions.
Deliver:
- changed files
- run instructions
- regression notes
```

---

## Card 3: HTTP Schema + Persistence Layer

Goal:
Add HTTP sidecar storage with transparent startup migration.

Scope:

1. Add `http_servers` table.
2. Add `http_access` summary table for verification + dir/file count outcomes.
3. Add `http_user_flags` and `http_probe_cache` tables for unified-browser parity.
4. Add idempotent migration logic at startup.
5. Extend protocol presence/read layer to include HTTP (`host_type='H'`) in unified list APIs.

Primary touch targets:

1. `tools/db_schema.sql`
2. `shared/db_migrations.py`
3. `shared/database.py`
4. `gui/utils/database_access.py`

Definition of done:

1. HTTP rows persist across runs.
2. Same IP can appear across SMB/FTP/HTTP paths without collision.
3. Startup migration is automatic and idempotent.
4. Unified server list can return HTTP rows with share-compatible count fields.

Regression checks:

1. Existing SMB/FTP queries still work.
2. Older DB files still open and migrate with no user action.

Out of scope:

1. HTTP browser UI.
2. Deep content ranking.

Prompt seed:

```text
Implement Card 3 from docs/dev/http_module/HTTP_PHASE_TASK_CARDS.md.
Requirements:
- Additive and idempotent migrations only.
- No destructive SMB/FTP schema rewrites.
- Persist HTTP verification and count summary fields.
Deliver:
- changed files
- migration notes
- validation notes for legacy DB startup
```

---

## Card 4: HTTP Discovery Reliability + Count Extraction

Goal:
Implement reliable HTTP(S) candidate verification and directory/file counting.

Scope:

1. Use Shodan query baseline: `http.title:"Index of /"`.
2. Verify candidate reachability and response validity for both HTTP and HTTPS.
3. Respect dialog-controlled TLS verification mode for HTTPS checks.
4. Confirm directory-index semantics (not title-only false positives).
5. Extract and persist directory/file counts.
6. Include one-level recursion in probe/count collection.
7. Persist categorized failures and reason codes.

Primary touch targets:

1. New `commands/http/shodan_query.py`
2. New `commands/http/verifier.py`
3. New `commands/http/index_parser.py` (or equivalent parser helper)
4. `commands/http/operation.py`
5. `shared/database.py` HTTP persistence calls

Definition of done:

1. Verified HTTP index targets are stored with count metadata.
2. Failure outcomes are categorized (`connect_fail`, `timeout`, `not_index`, `parse_fail`, etc.).
3. Targets with 0 accessible dirs/files are explicitly represented for downstream filtering logic.
4. HTTP rows expose count fields that align with server-list `Shares > 0` filter behavior.

Regression checks:

1. SMB/FTP discovery flows remain clean.
2. HTTP timeouts/failures do not hang scan lifecycle.

Out of scope:

1. Deep recursive site crawling.
2. Ranking/value scoring.

Prompt seed:

```text
Implement Card 4 from docs/dev/http_module/HTTP_PHASE_TASK_CARDS.md.
Requirements:
- Use Shodan baseline query: http.title:"Index of /"
- Add runtime verification to reduce false positives.
- Persist dir/file counts and categorized failure reasons.
- Keep logs and progress operator-friendly.
Deliver:
- changed files
- reason-code mapping
- sample output lines and DB row examples
```

---

## Card 5: HTTP Probe Snapshot + Browser Download MVP

Goal:
Enable in-app HTTP browse/probe/download flow with quarantine safeguards.

Scope:

1. Add HTTP navigator/list/download helper.
2. Add full built-in HTTP browser path within existing server-list UX patterns.
3. Save HTTP probe snapshots to JSON cache.
4. Keep download targets inside quarantine path.

Primary touch targets:

1. New `shared/http_browser.py` (or protocol extension in existing browser helper)
2. New `gui/utils/http_probe_cache.py`
3. New `gui/utils/http_probe_runner.py`
4. New `gui/components/http_browser_window.py` (or protocol branch in existing window)
5. Dashboard/xsmbseek drill-down routing as needed

Definition of done:

1. Operator can browse discovered HTTP index entries from app.
2. Operator can download HTTP files to quarantine.
3. Probe snapshot JSON writes and reloads correctly.
4. User-facing workflow and layout match existing SMB/FTP browsing conventions.

Regression checks:

1. SMB and FTP browse/download remain unchanged.
2. Cancel/timeout behavior remains responsive.

Out of scope:

1. Full normalized HTTP artifact DB tables.
2. Bulk recursive mirroring.

Prompt seed:

```text
Implement Card 5 from docs/dev/http_module/HTTP_PHASE_TASK_CARDS.md.
Requirements:
- Read-only remote listing operations.
- Quarantine download parity.
- Probe snapshot save/load behavior.
- No SMB/FTP browser regressions.
Deliver:
- changed files
- manual test notes
- known limitations list
```

---

## Card 6: QA, Hardening, and Documentation

Goal:
Stabilize HTTP MVP, enforce regression gates, and document known limits.

HI Guidance: adapt the existing document as neccessary but do not make major structural changes or large edits. adhere to the style guide at https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/AI_AGENT_DOC_STYLE_GUIDE.md and align with existing document style and tone

Scope:

1. Add/expand tests for HTTP scan, parser, persistence, and browser flows.
2. Add regression checklist covering SMB + FTP + HTTP.
3. Update root and module docs with HTTP behavior and constraints.
4. Capture known limitations and deferred work.
5. Add HTTP browser image-view parity with SMB/FTP browse windows by reusing the
   existing file-viewer/image-preview path (no protocol-specific duplicate renderer).

Primary touch targets:

1. Test files in `gui/tests` and `shared/tests`
2. `README.md` and relevant docs under `docs/`
3. `docs/dev/http_module/` summary/handoff docs

Definition of done:

1. HTTP behavior is documented and validated.
2. No new SMB/FTP failures introduced.
3. Handoff package is complete for future agents.
4. HTTP browser can preview supported image files with behavior consistent with SMB/FTP.

Regression checks:

1. Dashboard launch controls for all protocols.
2. SMB/FTP/HTTP scan lifecycle sanity checks.
3. SMB/FTP browse paths and HTTP browse path.
4. HTTP image preview opens and renders for supported image types.

Out of scope:

1. Post-MVP cross-protocol normalization refactor.

Prompt seed:

```text
Implement Card 6 from docs/dev/http_module/HTTP_PHASE_TASK_CARDS.md.
Requirements:
- Add tests and report exact pass/fail counts.
- Update docs for operator and developer audiences.
- Explicitly list residual risks and follow-ups.
- Add HTTP image preview parity in browser flow by reusing the existing viewer path.
Deliver:
- changed files
- test summary
- open follow-up list
```

**Delivered: 2026-03-19.** 14 new HTTP tests (5 browser, 7 probe, 2 progress). Image preview added to `HttpBrowserWindow`. See `claude_plan/06-card6.md` for full QA report.
