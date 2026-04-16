# Modularization Refactor - Risk Register

Date: 2026-04-15

## R1 - Import Contract Breakage During File Moves

Risk:
- Existing runtime and tests import from `gui.components.dashboard` and `gui.components.unified_browser_window`.
- Moving classes/functions can silently break import paths and monkeypatch targets.

Mitigation:
1. Keep compatibility shim modules with explicit re-exports.
2. Add import-contract tests before/after each extraction card.
3. Do not remove legacy paths until explicit HI approval.

## R2 - Behavior Drift in Parser-Coupled or UI-Critical Paths

Risk:
- Dashboard scan lifecycle and browser factory paths are tightly coupled to existing call expectations.

Mitigation:
1. Preserve output/message contracts unless card explicitly authorizes change.
2. Run targeted regression tests per card.
3. Require manual HI gate for cards touching scan/browse runtime behavior.

## R3 - Legacy/Data Contract Regressions

Risk:
- Refactor may accidentally alter startup DB assumptions or path resolution behavior.

Mitigation:
1. No schema changes in modularization cards unless explicitly scoped.
2. Guard data/config access by runtime state.
3. Include legacy-open smoke checks in manual validation set.

## R4 - Known Failures Accumulating During Multi-Card Refactor

Risk:
- Small unresolved failures can compound across card sequence and increase debug cost.

Mitigation:
1. Require PASS/FAIL evidence before advancing cards.
2. Fix discovered low-cost correctness issues immediately within active card scope.
3. Track unresolved items explicitly in card report output.

## R5 - Performance Regression in UI Hot Paths

Risk:
- Additional indirection from extraction may impact dashboard refresh, log rendering, or browser operations.

Mitigation:
1. Keep helper calls lightweight and synchronous behavior identical.
2. Avoid extra polling/threads/event-loop churn.
3. Validate responsiveness in manual HI checks for scan and browse flows.

## R6 - Type Coercion/Validation Drift

Risk:
- Consolidating helper functions can change truthiness/bounds semantics unexpectedly.

Mitigation:
1. Add focused coercion unit tests for mixed inputs.
2. Preserve current accepted values and defaults unless explicitly approved.
3. Use explicit min/max clamping where integer coercion is required.

## R7 - Oversized Card Scope

Risk:
- Large extraction in one pass can increase rollback difficulty and hide root causes.

Mitigation:
1. Keep one-card-at-a-time discipline.
2. Stop and re-scope if diff grows beyond planned touch list.
3. Prefer reversible, mechanical moves followed by focused behavior validation.

## R8 - Tooling/Execution Blockers

Risk:
- Test environment or path mismatch can block validation.

Mitigation:
1. Report blocker immediately.
2. Provide exact human-run commands and expected output.
3. Mark status as `PENDING` rather than `PASS` when gates are unverified.
