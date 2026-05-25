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
- Open consolidated DB dialog.
- Exercise each option path once.
- Restart desktop app and verify one-time migration prompt behavior (first run with pending sidecars, then no automatic prompt after defer).

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
