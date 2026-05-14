# Censys Integration Task Cards (Claude-Ready)

Date: 2026-05-14
Execution model: one small card at a time, explicit PASS/FAIL evidence

## Global Rules (All Cards)

1. Reproduce or confirm issue before editing.
2. Apply smallest safe fix.
3. Preserve behavior outside card scope.
4. Run targeted validation for touched components.
5. Report exact commands with PASS/FAIL outcomes.
6. No commit unless HI explicitly says `commit`.
7. If blocked, report blocker + exact HI unblock commands + expected output.
8. Check touched file line counts before and after every card.

## File Size Rubric (Required)

- `<=1200`: excellent
- `1201-1500`: good
- `1501-1800`: acceptable
- `1801-2000`: poor
- `>2000`: unacceptable unless explicitly justified

Stop-and-plan rule:

- If any touched file exceeds 1700 lines, stop and provide modularization plan before continuing.

## Completion Semantics (Required)

```text
AUTOMATED: PASS | FAIL
MANUAL:    PASS | FAIL | PENDING
OVERALL:   PASS | FAIL | PENDING
```

## Required Response Format (Per Card)

- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + short steps)

---

## C0 - Contract Freeze + Drift Audit (Plan Only)

Goal:

1. Freeze factual contracts before production edits.

Scope:

1. Compare `INITIAL_PLANNING/` docs vs current Censys docs.
2. Confirm v3 endpoints and required fields.
3. Confirm runtime integration seams in current repo.
4. Freeze validation command set for C1-C9.

Definition of done:

1. No code edits.
2. Drift matrix completed.
3. Unknowns resolved or explicitly tracked as assumptions.

Validation:

```bash
rg -n "censys|Censys|experimental_features|registry" docs/dev/censys_integration gui/components -g '*.md' -g '*.py'
rg -n "./dirracuda|gui/main.py|experimental" README.md docs/TECHNICAL_REFERENCE.md CLAUDE.md
```

HI test needed:

- No.

---

## C1 - Workspace Scaffold + Source-Backed Docs

Issue:

Missing decision-complete implementation pack for downstream coding agents.

Scope:

1. Create `README/SPEC/ROADMAP/TASK_CARDS/CLAUDE_PROMPTS`.
2. Create `VALIDATION_PLAN/RISK_REGISTER/LESSONS_LEARNED/FIELD_QUERY_MATRIX`.
3. Create `claude_plans/` prompts for C0-C9.
4. Ensure all references are real URLs, no placeholders.

Primary touch targets:

1. `docs/dev/censys_integration/*.md`
2. `docs/dev/censys_integration/claude_plans/*.md`

Definition of done:

1. Scaffold files exist and cross-link correctly.
2. Sources section cites official Censys docs used for decisions.
3. No fake placeholders or unresolved citation markers.

Validation:

```bash
rg -n "example.com|https://github.com//|TBD|PLACEHOLDER|\[\d+\]" docs/dev/censys_integration
rg -n "https://docs.censys.com|raw.githubusercontent.com/b3p3k0" docs/dev/censys_integration
```

HI test needed:

- No.

---

## C2 - Experimental Tab Scaffold (`Censys Discovery`)

Issue:

No Censys experimental tab exists.

Scope:

1. Add `gui/components/experimental_features/censys_discovery_tab.py`.
2. Register `Censys Discovery` in experimental registry.
3. Add read-only shell actions (`Run` disabled until C3/C4 wired).
4. Preserve existing `SearXNG`, `Reddit`, `Dorkbook`, `Keymaster` behaviors.

Primary touch targets:

1. `gui/components/experimental_features/censys_discovery_tab.py` (new)
2. `gui/components/experimental_features/registry.py`
3. `gui/tests/test_experimental_features_dialog.py`

Definition of done:

1. Tab is visible in Experimental dialog.
2. No regressions in existing tab routing tests.
3. Placeholder text clearly states experimental scope and no-live-run state.

Validation:

```bash
./venv/bin/python -m py_compile \
  gui/components/experimental_features/censys_discovery_tab.py \
  gui/components/experimental_features/registry.py
./venv/bin/python -m pytest \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py -q
```

HI test needed:

- Yes.
- Steps:
1. Open `⚗ Experimental`.
2. Confirm `Censys Discovery` tab appears and other tabs still work.

---

## C3 - Config + Secret Validation Contract

Issue:

No safe config path for PAT/org/profile/defaults.

Scope:

1. Add config accessors for `censys.*` namespace.
2. Add explicit coercion/validation for bounds and UUID.
3. Add no-log PAT safety checks in error/status paths.
4. Add tests for missing/invalid config values.

Primary touch targets:

1. `shared/config.py`
2. `shared/tests/test_config_validation_paths.py` (extend)
3. `shared/tests/test_censys_config_contract.py` (new)

Definition of done:

1. Invalid config fails safely and explicitly.
2. PAT never appears in logs/messages.
3. Existing non-censys config behavior unchanged.

Validation:

```bash
./venv/bin/python -m py_compile shared/config.py
./venv/bin/python -m pytest \
  shared/tests/test_config_validation_paths.py \
  shared/tests/test_censys_config_contract.py -q
```

HI test needed:

- Yes.
- Steps:
1. Save invalid UUID and confirm clear UI/runtime message.
2. Save empty PAT and confirm run preflight is blocked safely.

---

## C4 - REST Client + Models + Query Builder

Issue:

No runtime Censys client exists.

Scope:

1. Add `experimental/censys_discovery/{models,query_builder,client}.py`.
2. Implement `search/query`, pagination, and normalized result mapping.
3. Implement credit endpoints (user/org balance + usage).
4. Add stable reason-code taxonomy for API failures.

Primary touch targets:

1. `experimental/censys_discovery/models.py` (new)
2. `experimental/censys_discovery/query_builder.py` (new)
3. `experimental/censys_discovery/client.py` (new)
4. `shared/tests/test_censys_client.py` (new)
5. `shared/tests/test_censys_query_builder.py` (new)

Definition of done:

1. Client handles `401/403/422/500` as explicit reason codes.
2. Paging token flow is deterministic.
3. Query builder enforces nested service clauses.

Validation:

```bash
./venv/bin/python -m py_compile \
  experimental/censys_discovery/models.py \
  experimental/censys_discovery/query_builder.py \
  experimental/censys_discovery/client.py
./venv/bin/python -m pytest \
  shared/tests/test_censys_query_builder.py \
  shared/tests/test_censys_client.py -q
```

HI test needed:

- No.

---

## C5 - FTP Adapter + Sidecar Store

Issue:

No protocol persistence pipeline exists for Censys FTP candidates.

Scope:

1. Add sidecar DB store with schema guard checks.
2. Add service orchestration for FTP runs.
3. Persist run/result rows with deterministic dedupe.
4. Capture protocol tag + source payload for auditability.

Primary touch targets:

1. `experimental/censys_discovery/store.py` (new)
2. `experimental/censys_discovery/service.py` (new)
3. `experimental/censys_discovery/models.py`
4. `shared/tests/test_censys_store.py` (new)
5. `shared/tests/test_censys_service_ftp.py` (new)

Definition of done:

1. FTP run writes one run row and N deduped result rows.
2. Runtime schema checks fail loudly on drift.
3. No writes occur when preflight/auth fails.

Validation:

```bash
./venv/bin/python -m py_compile \
  experimental/censys_discovery/store.py \
  experimental/censys_discovery/service.py
./venv/bin/python -m pytest \
  shared/tests/test_censys_store.py \
  shared/tests/test_censys_service_ftp.py -q
```

HI test needed:

- Yes.
- Steps:
1. Run FTP discovery from Censys tab.
2. Confirm sidecar run/results rows created.

---

## C6 - HTTP Adapter

Issue:

HTTP path is not implemented for Censys protocol mapping.

Scope:

1. Extend service/query contracts for HTTP protocol.
2. Map HTTP-specific fields from matched service payload.
3. Preserve FTP behavior while adding HTTP path.

Primary touch targets:

1. `experimental/censys_discovery/query_builder.py`
2. `experimental/censys_discovery/service.py`
3. `shared/tests/test_censys_query_builder.py`
4. `shared/tests/test_censys_service_http.py` (new)

Definition of done:

1. HTTP run persists normalized rows.
2. Existing FTP tests remain green.
3. No UI hot-path blocking introduced.

Validation:

```bash
./venv/bin/python -m py_compile \
  experimental/censys_discovery/query_builder.py \
  experimental/censys_discovery/service.py
./venv/bin/python -m pytest \
  shared/tests/test_censys_service_ftp.py \
  shared/tests/test_censys_service_http.py \
  shared/tests/test_censys_query_builder.py -q
```

HI test needed:

- Yes.
- Steps:
1. Run HTTP discovery and inspect results in Censys sidecar browser.

---

## C7 - SMB Adapter

Issue:

SMB path is not implemented for Censys protocol mapping.

Scope:

1. Extend service/query contracts for SMB protocol.
2. Map SMB-specific fields and edge behavior.
3. Preserve FTP/HTTP behavior while adding SMB path.

Primary touch targets:

1. `experimental/censys_discovery/query_builder.py`
2. `experimental/censys_discovery/service.py`
3. `shared/tests/test_censys_service_smb.py` (new)

Definition of done:

1. SMB run persists normalized rows.
2. FTP + HTTP regression tests remain green.
3. Cross-protocol dedupe behavior stays deterministic.

Validation:

```bash
./venv/bin/python -m py_compile \
  experimental/censys_discovery/query_builder.py \
  experimental/censys_discovery/service.py
./venv/bin/python -m pytest \
  shared/tests/test_censys_service_ftp.py \
  shared/tests/test_censys_service_http.py \
  shared/tests/test_censys_service_smb.py -q
```

HI test needed:

- Yes.
- Steps:
1. Run SMB discovery and confirm rows are protocol-tagged as SMB.

---

## C8 - Results Browser + Promotion Hooks

Issue:

No operator review/promotion surface for Censys rows.

Scope:

1. Add Censys browser window for sidecar results.
2. Wire manual single + bulk promotion via existing sidecar promotion contract.
3. Keep promotion resilient with best-effort summary reporting.

Primary touch targets:

1. `gui/components/censys_browser_window.py` (new)
2. `gui/components/experimental_features/censys_discovery_tab.py`
3. `gui/components/dashboard_experimental.py`
4. `gui/tests/test_censys_browser_window.py` (new)

Definition of done:

1. Browser opens from tab.
2. Promotions work with and without Server List Browser open.
3. Failures produce actionable per-row reasons.

Validation:

```bash
./venv/bin/python -m py_compile \
  gui/components/censys_browser_window.py \
  gui/components/experimental_features/censys_discovery_tab.py \
  gui/components/dashboard_experimental.py
./venv/bin/python -m pytest \
  gui/tests/test_censys_browser_window.py \
  gui/tests/test_experimental_features_dialog.py -q
```

HI test needed:

- Yes.
- Steps:
1. Promote one row and one multi-row batch from Censys browser.
2. Confirm inserted/updated summary and refreshed dashboard counts.

---

## C9 - Credit UX + Docs/Closeout

Issue:

Final user-facing credit context and docs parity are incomplete.

Scope:

1. Add tier-profile estimate line and live credit data panel.
2. Add fallback messaging when live balance/usage is unavailable.
3. Update `README.md` and `docs/TECHNICAL_REFERENCE.md` with final behavior.
4. Update lessons learned and produce RA closeout review.

Primary touch targets:

1. `gui/components/experimental_features/censys_discovery_tab.py`
2. `README.md`
3. `docs/TECHNICAL_REFERENCE.md`
4. `docs/dev/censys_integration/LESSONS_LEARNED.md`

Definition of done:

1. Credit estimate + live panel are visible and truthful.
2. Docs match real click paths and contracts.
3. Final PASS/FAIL summary published with residual risk notes.

Validation:

```bash
./venv/bin/python -m pytest \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_censys_browser_window.py \
  shared/tests/test_censys_client.py \
  shared/tests/test_censys_store.py -q
rg -n "Censys Discovery|censys_discovery|censys\.personal_access_token|censys\.credit_profile" \
  README.md docs/TECHNICAL_REFERENCE.md docs/dev/censys_integration/
```

HI test needed:

- Yes.
- Steps:
1. Open Censys tab and verify estimate/live panel behavior.
2. Verify docs describe exactly the same workflow.
