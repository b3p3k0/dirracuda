# Integrate Experimental Features Into Main - LESSONS LEARNED

Status: Seeded
Last updated: 2026-06-07

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

**HTTP browser endpoint fidelity (2026-06-05):**
- A correct saved URL contract includes scheme, port, hostname, and path. Server List browsing used only scheme/port, so virtual-hosted results opened the IP root even though Copy URL was correct.
- Copy, browse, and background probe actions should share one endpoint resolver. The IP remains the database/cache identity while `probe_host` and `probe_path` drive HTTP authority, HTTPS SNI, and initial navigation.

**Server List action data (2026-06-05):**
- Command actions must use row-key-backed model data, not positional Treeview values. Display columns can be inserted or reordered, silently turning a hardcoded IP index into another field such as the rendered share count.

**Provider yield limits (2026-06-05):**
- UI limits and upstream transport limits are different contracts. Reddit accepted a 200-post preference while the RSS request omitted `limit`, yielding only the upstream default 25 entries; live checks established a 100-entry snapshot ceiling.
- A high result setting is ineffective when a hidden page cap is lower. SearXNG's fetch loop stopped at page 10 even when the configured instance still returned results, so page safety caps and unique-result deduplication must be reviewed together when increasing yield.

**Unified provider serialization (2026-06-05):**
- WAL permits readers alongside a writer but does not permit multiple simultaneous SQLite writers. Launching Shodan, SearXNG, and Reddit together after primary-DB cutover created deterministic lock contention.
- Serialize provider workflows above their existing internal queues and advance only after persistence plus sync complete. Generation tokens prevent duplicate or cancelled callbacks from restarting pending work.
- Keep provider order registry-driven with numeric priorities so adding a future provider does not require rewriting a fixed three-provider sequence.

**Compact operational dialogs (2026-06-05):**
- Dense operator workflows benefit from responsive grids, restrained separators, and a fixed action footer more than nested cards and oversized section bars. Keep scrolling as a small-screen fallback, not the default layout.
- Extract layout construction into a satellite before redesigning a near-limit controller. This keeps persistence, validation, and launch behavior stable while making visual iteration easier to test and reverse.
- Enforce mutually exclusive input modes at three layers: widget state, saved-state restoration, and request validation. This prevents templates or programmatic callers from bypassing a visual guard.

**SearXNG upstream pacing (2026-06-06):**
- A healthy self-hosted SearXNG process does not imply healthy upstream engines. Back-to-back pagination can cause SearXNG to suspend shared engines even when the local server has ample capacity.
- Apply adaptive delay before later pages and inspect `unresponsive_engines`; transport success alone is not evidence that every upstream engine accepted the request.
- Do not equate individual engine suspension with query failure. If SearXNG still returns results, preserve them and continue with escalating soft backoff; reserve hard retries for empty throttled pages or direct instance-level HTTP 429 responses.
- Bound hard recovery attempts across the entire run. A 30-second then 180-second retry ladder preserves useful partial results without turning a throttled run into an unbounded wait loop.
- Explicit capability tests and normal execution have different request budgets. Normal runs should not issue a throwaway search before the real page-1 query.
- Use pacing windows for real sequential work before adding concurrency. Persisting, classifying, filtering, and probing one page before requesting the next reduced idle delay while keeping SQLite writes short and deterministic.

**C11A — SearXNG retry shortening and cancellation (2026-06-06):**
- `_paginate_results` must catch `_Cancelled` internally and return a partial `_FetchOutcome`; propagating the sentinel to the caller loses all accumulated pagination telemetry (pages fetched, retry counts, delays, engine labels).
- Delay telemetry must be recorded from the return value of cooldown/pacing helpers after the wait, not from the requested delay before it. Helper functions should return actual elapsed seconds and let the caller decide whether to raise.
- Classification cleanup requires a `classification_committed` flag wrapping both classification and persistence; an exception caught at one level without the flag either over-cleans (deletes committed data) or under-cleans (leaks unclassified rows).
- `try/finally` around run-row insertion guarantees the connection closes on every path: cancel return, exception, and success. An explicit close before each early return is fragile and easy to miss on new paths.
- Both `start_searxng_scan` and `_on_searxng_scan_done` must be re-exported from `dashboard_scan.py` after satellite extraction; a single re-export misses direct calls from tests, and an omission of either misses provider-queue dispatch or test coverage of the completion handler.
- `_set_searxng_task_running` lifecycle stubs in tests must accept `**kwargs`; `_call_dashboard_hook` swallows `TypeError`, making a missing kwarg in the mock invisible at runtime.
- Bounded probe submission (`FIRST_COMPLETED` refill loop) is required to limit futures in flight; an unconstrained submit loop can enqueue every row before a cancel check fires, making the cancel check ineffective at stopping unnecessary work.
- Queue-managed and standalone cancel callbacks must differ: queue-managed routes through `cancel_provider_queue` (which then signals the event); standalone goes directly to the event. A single `event.set` callback from a queue-managed task would strand the provider queue without advancing or cancelling it.
- Cancel events must be cleared on every terminal path, including the `parent.after(...)` scheduling failure inside the worker, not only on thread startup failure.
- Sync must use an explicit allowlist of terminal states (`DONE | CANCELLED`); `status != ERROR` could sync unknown future status values silently.

**C11C — Semantic live output colors (2026-06-07):**
- Generic keyword classification (e.g., checking for "completed" or "failed" anywhere in a message) recolors unrelated Shodan `[status …]` lines. Use an exact-prefix allowlist keyed to the known SearXNG/Reddit/queue message catalog; everything else returns normal.
- A standalone `SUMMARY_TITLE` line emitted by Shodan (CLI colors disabled) matches `line.startswith(SUMMARY_TITLE)` and would enter the rollup coloring path. Guard rollup coloring on `"\n" in line` so only multiline in-process blocks are affected.
- Sync-line green must require explicit `failed == 0`. A sync line with no `"N failed"` token (unknown format) must return normal, not green. Without this guard, a format change silently turns unverified lines green.
- `(0 failed)` in a finished-queue message must not trigger the yellow branch. Use `([1-9]\d* failed)` or parse and check `> 0`; `\d+` matches zero.
- `append_log_line` returns immediately when `log_text_widget is None`. A test that sets `log_text_widget=None` to test history writes will silently verify nothing. Always provide a mock Text widget.
- Nonterminal errors (e.g., probe save failure at service.py:948) should be yellow, not red. The run continues; reserve red for operations that cause `run_dork_search` to return early with an error status.
- Colorize-at-display (rather than colorize-at-emit) keeps `log_history` plain and makes Copy All correct without any special stripping. It also keeps `_log_status_event` signature unchanged, preserving all test doubles and `_hook` callers.

Append new lessons after each completed card:
- What failed or nearly failed.
- Which guardrail prevented recurrence.
- What to enforce in future cards.
