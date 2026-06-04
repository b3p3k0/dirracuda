# Integrate Experimental Features Into Main - TASK CARDS

Cards are DA-executable and RA-reviewed. Execute one card at a time.

Mandatory read before each card:
- `README.md`
- `CLAUDE.md`
- `docs/TECHNICAL_REFERENCE.md`
- `docs/dev/inetgrate_exp_feat/README.md`
- `docs/dev/inetgrate_exp_feat/SPEC.md`
- `docs/dev/inetgrate_exp_feat/ARCHITECTURE.md`
- `docs/dev/inetgrate_exp_feat/ROADMAP.md`
- `docs/dev/inetgrate_exp_feat/LESSONS_LEARNED.md`
- `docs/dev/inetgrate_exp_feat/RISK_REGISTER.md`

Global guardrails:
- Censys is suspended and out of scope unless HI explicitly re-activates it.
- Preserve `./dirracuda` and `python -m experimental.webui.server` entrypoint contracts.
- No commits unless HI explicitly says `commit`.
- Check touched-file line counts before and after.
- If any touched **production code** file exceeds 1700 lines, stop and propose modularization.
- Test/docs files may exceed 1700 when necessary, but still prefer practical size discipline.

---

## C0 - Baseline Contracts Freeze (COMPLETE)

Issue:
Capture current-state contracts and constraints before promotion work.

Delivered:
- `docs/dev/inetgrate_exp_feat/BASELINE_CONTRACTS.md`

---

## C1 - Accessories Shell Cutover

Issue:
`Experimental` naming and surface now conflict with planned core promotion.

Root cause:
Dashboard and dialog naming still treat all non-Shodan modules as experimental.

Scope:
- Dashboard button label/callback surface
- Experimental dialog shell/title/warning language
- Registry semantics for accessory-only modules

Requirements:
1. Rename dashboard action semantics from `Experimental` to `Accessories`.
2. Keep access to `Web UI`, `Dorkbook`, `Keymaster`.
3. Do not break existing SearXNG/Reddit flows yet.
4. Keep UI changes minimal and theme-consistent.

Validation:
```bash
./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q
```

HI test needed:
- Launch `./dirracuda`.
- Confirm new button label and accessory dialog opens.

---

## C2 - Core Provider Registry For Start Scan

Issue:
Start Scan is protocol-only and does not expose SearXNG/Reddit as first-class providers.

Root cause:
`UnifiedScanDialog` only models SMB/FTP/HTTP protocol queueing.

Scope:
- Start-scan provider selection scaffolding
- Provider option panel scaffolding
- No provider execution rewrites yet

Requirements:
1. Add provider selection controls for `Shodan`, `SearXNG`, `Reddit`.
2. Preserve existing SMB/FTP/HTTP option behavior.
3. Block Censys exposure in this card.
4. Add strict validation for provider-selection minimums.

Validation:
```bash
./venv/bin/python -m pytest gui/tests/test_unified_scan_dialog.py -q
```

HI test needed:
- Open Start Scan.
- Verify providers appear and validation blocks empty selection.

---

## C3 - SearXNG Core Promotion Path

Issue:
SearXNG run path is only reachable from the accessory/experimental surface.

Root cause:
No start-scan dispatch integration for SearXNG service.

Scope:
- Wire SearXNG launch into core scan path
- Surface run status/results handoff affordances
- Reuse existing sidecar service/storage and promotion utilities
- Update WebUI SearXNG page behavior to match promoted core contracts

Requirements:
1. Launch SearXNG from core provider flow.
2. Keep existing sidecar-backed results/probe/promote behavior stable.
3. Report clear errors when preflight/instance validation fails.
4. Do not refactor package layout.
5. Ensure WebUI SearXNG surface follows the same promoted behavior contract and does not introduce new sidecar-only affordances.

Validation:
```bash
./venv/bin/python -m pytest gui/tests/test_dashboard_scan.py -q
./venv/bin/python -m pytest experimental/webui/tests/test_searxng_routes.py -q
./venv/bin/python -m pytest experimental/webui/tests/test_pages.py -q
```

HI test needed:
- Run one SearXNG query from Start Scan.
- Verify results become available and can be promoted.

---

## C4 - Reddit Core Promotion Path

Issue:
Reddit ingestion run path is only reachable from accessory/experimental surface.

Root cause:
No start-scan dispatch integration for Reddit ingest service.

Scope:
- Wire Reddit launch into core provider flow
- Preserve mode-specific validation (`feed`, `search`, `user`)
- Reuse existing sidecar results/probe/promote flow
- Update WebUI Reddit page behavior to match promoted core contracts

Requirements:
1. Launch Reddit from core provider flow.
2. Validate per-mode required fields explicitly.
3. Preserve existing runtime guards and sidecar behavior.
4. Keep Censys untouched.
5. Ensure WebUI Reddit surface follows promoted behavior contract and does not introduce new sidecar-only affordances.

Validation:
```bash
./venv/bin/python -m pytest gui/tests/test_dashboard_scan.py -q
./venv/bin/python -m pytest experimental/webui/tests/test_reddit_routes.py -q
./venv/bin/python -m pytest experimental/webui/tests/test_pages.py -q
```

HI test needed:
- Run each Reddit mode once from Start Scan.
- Verify results can be probed/promoted.

---

## C5 - Database Surface Consolidation

Issue:
Database operations are split across multiple entrypoints and sidecar access is discoverability-poor.

Root cause:
Dashboard exposes separate controls and sidecar import paths are not centralized.

Scope:
- Single dashboard DB entrypoint
- Option routing for main DB view, DB tools, and sidecar legacy/import
- One-time desktop startup migration notice for legacy sidecar data
- WebUI migration-status notification plumbing (desktop-owned execution model)

Requirements:
1. Replace split DB controls with one consolidated DB action.
2. Keep existing server-list and DB tools behavior available.
3. Add explicit sidecar legacy/import option path.
4. Reuse existing sidecar promotion utilities where feasible.
5. On desktop startup, detect pending sidecar migration and show one-time operator prompt (`Yes migrate now` / `No defer`).
6. Persist migration state and last summary so WebUI can show a clear migration status notice.
7. Lock defer semantics: choosing `No defer` suppresses all future automatic startup prompts; migration is manual-only afterward.

Validation:
```bash
./venv/bin/python -m pytest gui/tests/test_dashboard_widget.py -q
./venv/bin/python -m pytest gui/tests/test_db_tools_dialog.py -q
./venv/bin/python -m pytest experimental/webui/tests -k migration -q || true
```

HI test needed:
- Open consolidated `Database` dialog and verify `View Servers` opens the server list.
- Re-open `Database` dialog and verify `DB Tools` opens the DB tools dialog.
- Re-open `Database` dialog, open `[Legacy] Sidecar Data`, then verify `SearXNG Dork Results` route opens.
- Re-open `[Legacy] Sidecar Data`, then verify `Reddit Open Directory Posts` route opens.
- Re-open `[Legacy] Sidecar Data`, then verify `Migrate All to Main DB` starts migration when a DB reader is active.
- Verify `Migrate All to Main DB` shows a safe error and does not start a worker when no DB reader is active.
- Restart desktop app and verify one-time startup migration prompt behavior (prompt appears with pending sidecar data, then no future automatic prompt after defer).

---

## C6 - Provider-Scoped Config Localization

Issue:
Global config UI still carries provider-specific clutter (e.g., Shodan API key/defaults).

Root cause:
Legacy config design predates multi-provider core promotion.

Scope:
- App config cleanup for provider-owned fields
- Provider-owned configuration entrypoints
- WebUI cleanup for legacy sidecar DB surfaces

Requirements:
1. Reduce provider-specific fields from global config where safe.
2. Keep backward-compatible read/write behavior during transition.
3. Preserve existing key loading behavior unless explicitly migrated.
4. Document any compatibility shims.
5. Remove legacy sidecar DB browsing controls from WebUI operator pages.
6. Keep WebUI migration communication as notice/status only, pointing operators to desktop for migration actions.

Validation:
```bash
./venv/bin/python -m pytest gui/tests/test_app_config_dialog.py -q
./venv/bin/python -m pytest experimental/webui/tests/test_pages.py -q
```

HI test needed:
- Verify provider settings are discoverable in provider context.
- Confirm existing config still loads.

---

## C7 - Runtime Hardening + Regression

Issue:
Promotion work can introduce subtle regressions across scan, provider, and DB flows.

Root cause:
Multiple UI and orchestration touchpoints change across C1-C6.

Scope:
- Targeted regression for touched components
- Wider quick-lane regression when risk warrants

Requirements:
1. Re-run focused test suites for touched areas.
2. Run quick lane and record exact PASS/FAIL.
3. If failures occur, classify pre-existing vs introduced with evidence.

Validation:
```bash
./venv/bin/python -m pytest gui/tests -q
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
```

HI test needed:
- Manual smoke: Start Scan + provider runs + DB entrypoint + Accessories.

---

## C8 - Docs And Reference Closeout

Issue:
Runtime behavior and docs drift after promotion changes.

Root cause:
Feature promotion modifies user-facing workflow and architecture contracts.

Scope:
- `README.md`
- `docs/TECHNICAL_REFERENCE.md`
- `docs/dev/inetgrate_exp_feat/*`

Requirements:
1. Update docs to exactly match shipped behavior.
2. Record new lessons-learned and retire invalid assumptions.
3. Ensure Censys suspension is explicitly documented.

Validation:
```bash
./venv/bin/python -m pytest gui/tests/test_readme_examples.py -q || true
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
```

HI test needed:
- Spot-check updated docs against UI behavior.

---

## C9 - SearXNG Hard Cutover To Primary DB (COMPLETE)

Issue:
New SearXNG runs still wrote runtime artifacts to `se_dork.db`, requiring manual promotion before primary DB surfaces showed results.

Root cause:
Dashboard, Accessories, and WebUI SearXNG entrypoints still selected the sidecar path and did not run an automatic primary DB sync after completion.

Delivered:
- `experimental/se_dork/main_db_sync.py`
- SearXNG run entrypoints now pass the active primary DB path.
- Retained HTTP/HTTPS SearXNG rows auto-sync into main HTTP tables.
- Primary-backed SearXNG browser hides manual promotion.
- Legacy sidecar browsing remains available for historical data.

Validation:
```bash
./venv/bin/python -m pytest gui/tests/test_dashboard_scan.py -q
./venv/bin/python -m pytest gui/tests/test_se_dork_tab.py -q
./venv/bin/python -m pytest experimental/webui/tests/test_searxng_routes.py -q
./venv/bin/python -m pytest gui/tests/test_se_dork_browser_window.py -q
./venv/bin/python -m pytest shared/tests/test_se_dork_main_db_sync.py -q
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
```

Known caveat:
- `shared/tests/test_se_dork_service.py::test_run_dork_search_classifies_results` remains a pre-existing mock-call mismatch (`progress_cb` argument), not introduced by C9.

---

## C10 - Reddit Hard Cutover To Primary DB (COMPLETE)

Issue:
New Reddit runs still write posts, targets, probe artifacts, and ingest cursor state to `reddit_od.db`. Operators must manually promote rows before primary DB protocol surfaces show results.

Root cause:
Reddit entrypoints still select the sidecar DB path or call `run_ingest(options)` without `db_path`, and there is no C9-style automatic sync helper for retained Reddit targets.

Planning scope:
- Produce a decision-complete implementation plan before code changes.
- Scope is locked to Reddit/Redseek only.
- Preserve legacy Reddit sidecar browsing for historical data.
- Do not change Reddit network fetch behavior, terms/policy posture, auth, requirements, or DB schema/migration logic without explicit HI approval.

Likely implementation scope after plan approval:
1. Add focused read helpers in `experimental/redseek/store.py` for full target rows by current-run dedupe keys or run-equivalent scope.
2. Add a Reddit primary-sync helper modeled on C9's SearXNG helper, returning deterministic `selected/processed/inserted/updated/skipped/failed/cancelled` counts and never raising.
3. Repoint Start Scan Reddit path (`gui/components/dashboard_scan.py`) to active primary DB path and call sync after successful ingest/probe.
4. Repoint WebUI `/api/reddit/run` (`experimental/webui/app.py`) to `request.app.state.db_path` and include sync totals in job metadata.
5. Repoint standard Reddit browser open path to primary-backed mode and hide `Add to dirracuda DB`; keep legacy sidecar route available from `[Legacy] Sidecar Data`.
6. Update completion/status copy to remove new-run sidecar/manual-promotion language.
7. Update README, Technical Reference, lessons learned, and relevant tests after implementation.

Open planning decisions:
- Whether `replace_cache=True` should wipe only Reddit runtime tables in the active primary DB context or be disabled/translated for primary-backed runs.
- Whether `reddit_posts`, `reddit_targets`, and `reddit_ingest_state` can be safely created in the primary DB by `redseek.store.init_db(db_path)` without migration changes, or whether that requires explicit HI approval as a schema-contract change.
- Whether current-run sync should use `_probe_candidate_keys`, `created_at`/`last_seen_at`, or a new explicit ingest-run identifier to avoid all-runs scans.
- Whether primary-backed Reddit browser should show only Reddit runtime targets or all rows in `reddit_targets` from the active DB.

Planning validation:
```bash
git status --short
rg -n "reddit_od_db_file|run_ingest\\(|show_reddit_browser_window|Add to dirracuda DB|reddit sidecar" gui experimental docs README.md
wc -l gui/components/dashboard_scan.py gui/components/reddit_browser_window.py experimental/redseek/store.py experimental/redseek/service.py experimental/webui/app.py
```

Implementation validation candidates after plan approval:
```bash
./venv/bin/python -m pytest gui/tests/test_dashboard_scan.py -q
./venv/bin/python -m pytest gui/tests/test_dashboard_reddit_wiring.py -q
./venv/bin/python -m pytest gui/tests/test_reddit_browser_window.py -q
./venv/bin/python -m pytest experimental/webui/tests/test_reddit_routes.py -q
./venv/bin/python -m pytest shared/tests/test_redseek_service.py -q
./venv/bin/python -m pytest shared/tests/test_redseek_store.py -q
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
```

HI test needed after implementation:
- `./dirracuda` -> Start Scan -> Reddit feed/search/user as applicable.
- Confirm new entries appear in primary DB protocol surfaces without manual promotion.
- Accessories -> Reddit -> Open Post DB should hide manual promotion in primary-backed mode.
- Database -> `[Legacy] Sidecar Data` -> Reddit should still open historical sidecar data.
