# SearXNG Dork Module Roadmap

Date: 2026-04-18
Execution model: one card at a time, explicit PASS/FAIL evidence

## [DONE] Objective 0: Contract Freeze and Runtime Baseline

Outcome:
1. Runtime seams and existing reusable HTTP verification paths are confirmed.

Tasks:
1. Confirm Experimental tab registry replacement path (`placeholder` -> `SearXNG Dorking`).
2. Confirm SearXNG preflight checks and expected failure modes.
3. Confirm existing HTTP verifier/probe integration points.

## [DONE] Objective 1: SearXNG Dorking Tab Scaffold

Outcome:
1. New tab exists in Experimental dialog with stable UI shell.

Tasks:
1. Add `SearXNG Dorking` tab module and register it.
2. Remove placeholder tab/module wiring.
3. Add persisted UI settings keys for instance URL/query/options.

## [DONE] Objective 2: SearXNG Instance Preflight

Outcome:
1. User can test instance and see actionable pass/fail details.

Tasks:
1. Implement `/config` and `/search?format=json` checks.
2. Map HTTP failures to explicit reason codes.
3. Show setup hint for `format=json` 403 case.

## [DONE] Objective 3: Dork Search Service + Sidecar Persistence

Outcome:
1. Query run writes deterministic run/result records in sidecar DB.

Tasks:
1. Build SearXNG client and orchestrator.
2. Store run metadata and raw result rows.
3. Apply URL normalization/dedupe per run.

## [DONE] Objective 4: Candidate Verification and Classification

Outcome:
1. Results are triaged into actionable verdicts.

Tasks:
1. Reuse existing HTTP verification/probe path.
2. Classify each candidate as OPEN_INDEX/MAYBE/NOISE/ERROR.
3. Persist verdict and reason code.

## [DONE] Objective 5: Results Browser + Promotion Hooks

Outcome:
1. User can review outcomes and hand-off candidates for deeper workflows.

Tasks:
1. Add dork results browser window.
2. Add row actions (`Open URL`, `Copy URL`, optional `Promote/Add Record`).
3. Keep promotion explicit/manual.

## [DONE] Objective 6: Docs, Regression, and Closeout

Outcome:
1. Module ships with clear setup instructions and validation evidence.

Tasks:
1. Update README and technical reference docs.
2. Add focused tests for tab wiring, preflight, service, and classifier integration.
3. Publish final validation report with exact commands and outcomes.

## Exit Criteria

1. SearXNG Dorking replaces placeholder in Experimental dialog.
2. Instance preflight is reliable and actionable.
3. Query run + classification pipeline works end-to-end against configured SearXNG.
4. Behavior outside requested scope is unchanged.
5. Docs include concrete SearXNG setup and 403-format troubleshooting.

