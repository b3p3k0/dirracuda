# SearXNG Dork Module - Task Cards (Claude-Ready)

Date: 2026-04-18
Execution model: one small issue/card at a time, explicit PASS/FAIL evidence

## Global Rules (All Cards)

1. Reproduce/confirm issue first.
2. Apply smallest safe fix (surgical edits only).
3. Run targeted validation for touched components.
4. Report exact commands with PASS/FAIL.
5. No commit unless HI explicitly says `commit`.
6. If blocked, report blocker + exact HI unblock commands + expected result.
7. Check touched file line counts before and after edits.

## File Size Rubric (Required on touched files)

- `<=1200`: excellent
- `1201-1500`: good
- `1501-1800`: acceptable
- `1801-2000`: poor
- `>2000`: unacceptable unless explicitly justified

Stop-and-plan rule:
- If a touched file exceeds 1700 lines, pause and provide modularization plan before continuing.

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

## C0 - Contract Freeze + Baseline (Plan Only)

Goal:
1. Confirm touch points and validation seams before code edits.

Scope:
1. Confirm placeholder-tab replacement path in Experimental registry.
2. Confirm SearXNG preflight contract (`/config`, `/search?...&format=json`).
3. Confirm HTTP verifier/probe reuse path.
4. Freeze card-level validation commands.

Definition of done:
1. No code changes.
2. Concrete touch list for C1-C6.
3. Risks and assumptions documented.

Validation:
```bash
rg -n "placeholder|experimental_features|registry" gui/components -g '*.py'
rg -n "run_http_probe|try_http_request|validate_index_page|dispatch_probe_run" gui commands -g '*.py'
curl -sS -D - 'http://192.168.1.20:8090/search?q=hello&format=json' -o /tmp/sx.json | head -n 20
```

HI test needed:
- No.

---

## C1 - Replace Placeholder With SearXNG Dorking Tab Scaffold

Issue:
Experimental dialog still shows placeholder tab.

Scope:
1. Add `gui/components/experimental_features/se_dork_tab.py`.
2. Register `SearXNG Dorking` tab in registry.
3. Remove placeholder tab registration and obsolete placeholder module usage.
4. Add non-functional UI shell (instance URL, query, test/run buttons, status area).

Primary touch targets:
1. `gui/components/experimental_features/registry.py`
2. `gui/components/experimental_features/se_dork_tab.py` (new)
3. `gui/components/experimental_features/__init__.py` (if needed)
4. `experimental/placeholder/__init__.py` (remove usage or retire)
5. `gui/tests/test_experimental_features_dialog.py`

Definition of done:
1. Tabs are `Reddit` + `SearXNG Dorking`.
2. No placeholder tab in UI.
3. Existing Reddit tab behavior unchanged.

Validation:
```bash
python3 -m py_compile \
  gui/components/experimental_features/registry.py \
  gui/components/experimental_features/se_dork_tab.py
./venv/bin/python -m pytest \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py -q
```

HI test needed:
- Yes.
- Steps:
1. Open Experimental dialog.
2. Confirm `SearXNG Dorking` tab exists and placeholder tab is gone.

---

## C2 - SearXNG Preflight (Instance Test)

Issue:
Need deterministic instance validation before running dork query.

Scope:
1. Add client function for preflight checks.
2. Test `/config` and `/search?q=hello&format=json`.
3. Surface actionable reason codes in UI (including `format=json` 403).
4. Persist instance URL setting.

Primary touch targets:
1. `experimental/se_dork/client.py` (new)
2. `experimental/se_dork/models.py` (new)
3. `gui/components/experimental_features/se_dork_tab.py`
4. `gui/tests/test_se_dork_tab.py` (new)
5. `shared/tests/test_se_dork_client.py` (new)

Definition of done:
1. Test button returns PASS/FAIL with reason.
2. `http://192.168.1.20:8090` works in local validation.
3. URL persistence works across reopen/restart.

Validation:
```bash
python3 -m py_compile \
  experimental/se_dork/client.py \
  experimental/se_dork/models.py \
  gui/components/experimental_features/se_dork_tab.py
./venv/bin/python -m pytest \
  shared/tests/test_se_dork_client.py \
  gui/tests/test_se_dork_tab.py -q
```

HI test needed:
- Yes.
- Steps:
1. Enter instance URL and click `Test Instance`.
2. Confirm success path and one forced-failure path (bad host or port).

---

## C3 - Dork Search Service + Sidecar Store

Issue:
No persisted run/result pipeline for SearXNG dork searches.

Scope:
1. Add service orchestrator for search runs.
2. Add sidecar DB with run/results tables.
3. Normalize and dedupe URLs per run.
4. Persist raw result metadata (`url`, `title`, `engine`, etc.).

Primary touch targets:
1. `experimental/se_dork/service.py` (new)
2. `experimental/se_dork/store.py` (new)
3. `experimental/se_dork/models.py`
4. `gui/components/experimental_features/se_dork_tab.py`
5. `shared/tests/test_se_dork_service.py` (new)
6. `shared/tests/test_se_dork_store.py` (new)

Definition of done:
1. Run writes one `dork_runs` row and N `dork_results` rows.
2. Dedupe behavior is deterministic.
3. Run summary appears in UI.

Validation:
```bash
python3 -m py_compile \
  experimental/se_dork/service.py \
  experimental/se_dork/store.py
./venv/bin/python -m pytest \
  shared/tests/test_se_dork_store.py \
  shared/tests/test_se_dork_service.py \
  gui/tests/test_se_dork_tab.py -q
```

HI test needed:
- Yes.
- Steps:
1. Run `site:* intitle:"index of /"` query.
2. Confirm run summary and persisted sidecar rows.

---

## C4 - Verification + Classification Via Existing HTTP Path

Issue:
Need automatic triage of noisy SERP results using existing HTTP verification.

Scope:
1. Reuse existing verifier/probe logic for candidate checks.
2. Classify rows into `OPEN_INDEX|MAYBE|NOISE|ERROR`.
3. Persist verdict + reason code + status.
4. Keep runtime bounded (timeouts/caps/cancel-safe).

Primary touch targets:
1. `experimental/se_dork/service.py`
2. `experimental/se_dork/classifier.py` (new)
3. `commands/http/verifier.py` (read-only expected; edit only if bug found)
4. `shared/tests/test_se_dork_service.py`
5. `shared/tests/test_se_dork_classifier.py` (new)

Definition of done:
1. Existing HTTP verification path is called for candidates.
2. Verdict distribution appears in summary.
3. Known noise samples classify as non-open.

Validation:
```bash
python3 -m py_compile \
  experimental/se_dork/service.py \
  experimental/se_dork/classifier.py
./venv/bin/python -m pytest \
  shared/tests/test_se_dork_classifier.py \
  shared/tests/test_se_dork_service.py -q
```

HI test needed:
- Yes.
- Steps:
1. Run a dork query.
2. Confirm at least one `OPEN_INDEX` and one non-open verdict appears.

---

## C5 - Results Browser + Optional Promotion Hooks

Issue:
Need a review surface for triaged URLs.

Scope:
1. Add browser window for dork results.
2. Include row actions: `Open URL`, `Copy URL`.
3. Optional hook for `Add to dirracuda DB` via existing add-record path where possible.
4. No automatic promotion.

Primary touch targets:
1. `gui/components/se_dork_browser_window.py` (new)
2. `gui/components/experimental_features/se_dork_tab.py`
3. `experimental/se_dork/store.py`
4. `gui/tests/test_se_dork_browser_window.py` (new)

Definition of done:
1. Results window opens from tab.
2. Row actions are functional.
3. Promotion hook behavior is explicit and safe.

Validation:
```bash
python3 -m py_compile \
  gui/components/se_dork_browser_window.py \
  gui/components/experimental_features/se_dork_tab.py
./venv/bin/python -m pytest \
  gui/tests/test_se_dork_browser_window.py \
  gui/tests/test_se_dork_tab.py -q
```

HI test needed:
- Yes.
- Steps:
1. Open results window after a run.
2. Verify row actions and manual promotion behavior.

---

## C6 - Docs + Regression Closeout

Issue:
Need operator-ready setup docs and final regression confidence.

Scope:
1. Update README with SearXNG Dorking entrypoint + SearXNG setup.
2. Document `format=json` 403 troubleshooting.
3. Update technical reference for module architecture and sidecar schema.
4. Run focused regression suites.

Primary touch targets:
1. `README.md`
2. `docs/TECHNICAL_REFERENCE.md`
3. `docs/dev/searxng_dork_module/` docs pack
4. Relevant test files touched in earlier cards

Definition of done:
1. Docs match actual runtime behavior.
2. Setup and troubleshooting steps are copy-paste ready.
3. Regression suite passes.

Validation:
```bash
./venv/bin/python -m pytest \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py \
  gui/tests/test_se_dork_tab.py \
  gui/tests/test_se_dork_browser_window.py \
  shared/tests/test_se_dork_client.py \
  shared/tests/test_se_dork_classifier.py \
  shared/tests/test_se_dork_service.py \
  shared/tests/test_se_dork_store.py -q
rg -n "SearXNG Dorking|SearXNG Dork Module|format=json|403|Not available|se_dork" \
  README.md docs/TECHNICAL_REFERENCE.md docs/dev/searxng_dork_module/
```

HI test needed:
- Yes.
- Steps:
1. Fresh-start setup with documented steps.
2. Validate success path and intentional 403-misconfig path.

