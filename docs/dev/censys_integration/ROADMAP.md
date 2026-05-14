# Censys Integration Roadmap

Date: 2026-05-14
Execution model: one card at a time, explicit PASS/FAIL evidence

## Objective 0: Contract Freeze (C0)

Outcome:

1. Runtime facts, endpoint choices, and drift risks are frozen before code edits.

Tasks:

1. Audit seed docs against current Censys docs.
2. Freeze API/field contracts and error taxonomy.
3. Freeze per-card validation command set.

## Objective 1: Supervisor Pack Scaffold (C1)

Outcome:

1. Full docs workspace is ready for downstream carded execution.

Tasks:

1. Publish README/SPEC/ROADMAP/TASK_CARDS/CLAUDE_PROMPTS.
2. Publish VALIDATION_PLAN/RISK_REGISTER/LESSONS_LEARNED/FIELD_QUERY_MATRIX.
3. Publish `claude_plans/` card prompts.

## Objective 2: UI Shell (C2)

Outcome:

1. Experimental `Censys Discovery` tab appears with read-only scaffolding and no regressions.

Tasks:

1. Add tab module.
2. Register in experimental registry.
3. Add focused GUI wiring tests.

## Objective 3: Config + Secret Contract (C3)

Outcome:

1. Censys config namespace is parsed/validated safely.

Tasks:

1. Add typed config accessors.
2. Add coercion bounds and UUID/PAT checks.
3. Add tests for invalid and missing paths.

## Objective 4: REST Client and Model Layer (C4)

Outcome:

1. Deterministic, tested Censys API client with pagination and credit endpoints.

Tasks:

1. Add client/model/query builder modules.
2. Add stable reason-code taxonomy.
3. Add unit tests for auth, paging, and endpoint failures.

## Objective 5: Protocol Adapter - FTP (C5)

Outcome:

1. FTP discovery run persists deterministic sidecar results.

Tasks:

1. Implement FTP query builder contract.
2. Persist runs/results with schema checks.
3. Add FTP-focused service/store tests.

## Objective 6: Protocol Adapter - HTTP (C6)

Outcome:

1. HTTP discovery run persists deterministic sidecar results.

Tasks:

1. Implement HTTP protocol mapping.
2. Validate known HTTP field handling.
3. Add HTTP-focused service/store tests.

## Objective 7: Protocol Adapter - SMB (C7)

Outcome:

1. SMB discovery run persists deterministic sidecar results.

Tasks:

1. Implement SMB protocol mapping.
2. Validate protocol-specific edge handling.
3. Add SMB-focused service/store tests.

## Objective 8: Results Browser + Promotion (C8)

Outcome:

1. Operators can review and manually promote rows safely.

Tasks:

1. Add Censys browser window.
2. Wire single/bulk promotion via sidecar promotion helpers.
3. Add browser/promotion regression tests.

## Objective 9: Credit UX + Closeout (C9)

Outcome:

1. Estimate and live credit context are visible; docs are synced to final behavior.

Tasks:

1. Add estimate + live balance/usage surfaces.
2. Update README and technical reference.
3. Publish closeout validation evidence and residual risk notes.

## Exit Criteria

1. All C2-C9 cards have automated PASS evidence and explicit manual-check status.
2. Existing SMB/FTP/HTTP scan workflows show no behavior regressions.
3. Censys module remains isolated to experimental paths.
4. Docs and implementation match exactly for click-paths, config keys, and runtime contracts.
