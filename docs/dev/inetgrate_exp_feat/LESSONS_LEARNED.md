# Integrate Experimental Features Into Main - LESSONS LEARNED

Status: Seeded
Last updated: 2026-06-05

## Carry Forward

1. Promote behavior, not folder names, first. A safe adapter can graduate features before risky package moves.
2. Treat Censys as suspended unless HI explicitly re-activates it in writing.
3. Keep Start Scan backward compatible while adding providers; do not regress SMB/FTP/HTTP.
4. Runtime schema checks are required on every sidecar/main DB bridge path.
5. Sidecar imports should fail per-row, not all-or-nothing, so operators can recover incrementally.
6. Provider validation must be explicit and actionable; implicit coercion causes silent bad scans.
7. Keep subprocess execution argument-list based and `shell=False` to avoid injection regressions.
8. Avoid global config sprawl; provider-specific settings belong near provider workflows.
9. Rename/IA changes require synchronized docs/tests in the same card to prevent drift.
10. When reviewing DA work, provide findings and constraints, then let DA choose fix details.
11. Promotion is not desktop-only: WebUI contracts must be updated in the same wave, not deferred.
12. Legacy sidecar DB browsing should not remain in WebUI after promotion; keep migration messaging clear and desktop-owned.
13. One-time migration prompts must be stateful (`not_started`, `deferred`, `completed`, `failed`) to avoid operator fatigue and ambiguous behavior.
14. Defer means defer: once operator chooses `No defer`, do not auto-prompt again; require explicit manual migration trigger.
15. File-size hard-stop modularization rule applies to production code, not test/docs files (which should still be kept practical).
16. Canonical runtime docs can drift even when runtime is stable. After config-path/port migrations, verify all operator and agent docs (`README.md`, `docs/TECHNICAL_REFERENCE.md`, `AGENTS.md`, `CLAUDE.md`, and `experimental/webui/README.md`) still match current WebUI defaults/paths in the same closeout wave (C8), not later.

## Additions During Execution

**C8 (2026-05-29):**
- Drift found across 13 files: port 5480 (5 files), conf/webui.json path (4 files),
  Experimental→Accessories label (README.md, TECHNICAL_REFERENCE.md, CLAUDE.md), stale RCE
  section in CLAUDE.md (module files gone since C3/C7), 6 stale sidecar-browse route rows in
  TECHNICAL_REFERENCE.md (removed from app.py in C6), missing Censys suspension note in AGENTS.md.
- ROADMAP.md card statuses were not updated past C0; each card should close its own status row.
- docs/dev/webui/ active planning docs (non-approved_plans) also drifted on port and config path;
  include them explicitly in any future closeout scope.
- Validation commands in task cards can drift; `gui/tests/test_readme_examples.py` was referenced by C8 but does not exist.
  Add a preflight check that each scripted validation target exists before promoting it to required evidence.

**C9 (2026-05-29):**
- Provider cutovers need entrypoint parity, not just service changes. SearXNG required updates in dashboard scan launch, Accessories tab run flow, and WebUI `/api/searxng/run` to prevent split-write behavior.
- Auto-sync summaries must be deterministic and non-throwing (`selected/processed/inserted/updated/skipped/failed/cancelled`) so UI/job layers can report outcomes without branching on exceptions.
- Primary-backed browser mode should disable obsolete actions rather than silently no-op. Hiding `Add to dirracuda DB` in SearXNG primary mode reduced operator ambiguity while preserving a legacy sidecar path for historical data.

**C10 (2026-06-04):**
- Three entrypoints, not two: `dashboard_scan.py`, `gui/dashboard/scan_controls.py`, and `webui/app.py` all needed to be repointed. Missing `scan_controls.py` was identified during planning review; always enumerate every call site that reaches `run_ingest` before starting cutover work.
- `replace_cache=True` in `scan_controls.py` accepts user input from the dialog, making it the highest-blast-radius path before the D2 guard was in place. Hard sequencing rule (D2 guard tests must pass before any entrypoint repoint) prevented this from being a live wipe risk.
- Path inference (`_is_sidecar_path(db_path)`) for replace_cache scope is too fragile. Explicit `replace_cache_scope: Literal["full", "state_only"]` on `IngestOptions` with a hard error for unknown values is the correct pattern. A bare `else` in the dispatch would have silently routed unknown scopes.
- Mapper duplication is a real maintenance hazard. `_build_prefill` (browser) and `_row_to_prefill` (sync) were functionally identical; extracting `experimental/redseek/mapper.py` as the shared source eliminated the divergence risk. Both callers now explicitly pass `promotion_source` and `snapshot_source` labels rather than having them hardcoded in the mapper.
- Probe metadata carry-forward (probe cache, snapshot) must be explicit in planning. It was easy to forget that the sync path needed `_probe_cache`, `_probe_snapshot_source`, and `_probe_snapshot` fields — not just host/port. The mapper handles this now for both paths.
- Browser `_build_prefill` tests that called the callback directly (asserting specific return values) became stale when the function was refactored to delegate to the mapper. Update callback-invocation tests whenever the callback implementation changes, not just the signature.
- 5 tests in `test_experimental_features_dialog.py` tested the OLD server-list-getter–based promotion wiring of `open_reddit_post_db`. After C10, that function opens primary DB with `allow_promotion=False`. These tests needed updating — they were asserting behavior that no longer exists. When changing a function's contract, scan all tests that call it directly, not just the ones in the same file.
- Snapshot source label for sync path should not contain "sidecar" (`reddit:run_sync`, not `sidecar:reddit:run_sync`) — mirrors C9's `searxng:run_sync`. Keep labels consistent with the actual storage context.

**C10.1 (2026-06-04):**
- Platform-owned unofficial endpoints can disappear without a local regression. Reddit's unauthenticated JSON listing/search endpoints began returning 403, so the safe adaptation was an anonymous RSS cutover rather than OAuth, browser-token reuse, proxy scraping, or HTML scraping.
- Preserve internal contracts when adapting transport. Mapping Atom entries back into the existing raw-post dict shape kept C10's primary-DB write/sync flow intact and avoided broad service rewrites.
- Removed modes need explicit stale-config handling. `user` mode is hidden in normal UI, coerced to `feed` from saved preferences, and rejected directly by service/WebUI/dashboard guards with a clear anonymous-RSS message.
- RSS has reduced metadata and no cursor. Document single-snapshot behavior and best-effort NSFW handling instead of implying old JSON pagination still exists.

**Live completion rollups (2026-06-05):**
- In-process providers do not inherit CLI completion output automatically. SearXNG and Reddit needed an explicit raw-log rollup to match the Shodan completion signal.
- Keep transient and durable completion surfaces separate: the popup gives immediate results, while Live Scan Output preserves the final counts after the popup closes.
- Emit a multiline rollup as one queue item. This preserves ordering and avoids timestamping every metric as a separate controller status event.

Append new lessons after each completed card:
- What failed or nearly failed.
- Which guardrail prevented recurrence.
- What to enforce in future cards.
