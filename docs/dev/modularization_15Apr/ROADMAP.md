# Modularization Refactor Roadmap (15 Apr 2026)

Date: 2026-04-15  
Branch: `development`  
Execution model: one card at a time, explicit PASS/FAIL evidence

## Current Baseline

- `gui/components/dashboard.py`: 3331 lines
- `gui/components/unified_browser_window.py`: 3238 lines
- Both modules exceed the project maintainability rubric and mix multiple concerns.

## Goals

1. Decompose dashboard and browser monoliths into focused modules with stable public APIs.
2. Remove duplicated helper logic (`bool/int coercion`, `file-size formatting`, config parsing patterns).
3. Preserve runtime behavior and UX contracts for existing SMB/FTP/HTTP flows.
4. Keep migrations/data contracts and parser/output contracts safe during refactor.
5. Improve maintainability without introducing regressions or hot-path performance drops.

## Non-Goals

1. No protocol behavior redesign.
2. No schema redesign or destructive migration changes.
3. No broad UI/UX redesign.
4. No speculative architecture rewrites unrelated to modularization scope.

## Hard Constraints (Locked)

1. Root-cause fixes only; no symptom suppression.
2. No accumulation of known failures.
3. Legacy compatibility is first-class (especially DB/data contracts and startup behavior).
4. Runtime-state guards required for schema/data operations.
5. Keep UI/runtime hot paths regression-free.
6. Type coercion/validation must be explicit and bounded.
7. Surgical edits only; no broad refactors unless card-scoped.
8. No commits unless HI explicitly says: `commit`.

## Objective Sequence

## Objective 0: Freeze Contracts and Baseline

Outcome: public symbols, runtime call paths, parser-coupled output, and validation gates are explicitly documented before code movement.

Tasks:
1. Inventory import contracts and monkeypatch-sensitive symbols.
2. Capture baseline tests and manual checks.
3. Record known risks and fallback plan per card.

## Objective 1: Consolidate Shared Utilities

Outcome: duplicated helper behavior is centralized and reused safely.

Tasks:
1. Introduce shared GUI utility modules for coercion and file-size formatting.
2. Replace duplicate helper implementations with imports/adapters.
3. Add targeted unit coverage for coercion and formatting equivalence.

## Objective 2: Decompose Unified Browser Module

Outcome: browser core and per-protocol implementations are separated while existing imports remain valid.

Tasks:
1. Create browser package scaffold and helper modules.
2. Extract `UnifiedBrowserCore` into its own module.
3. Extract FTP, HTTP, SMB implementations into protocol modules.
4. Keep compatibility via `gui/components/unified_browser_window.py` re-exports.

## Objective 3: Decompose Dashboard Module

Outcome: dashboard behavior is split by concern (layout/status/scan orchestration/batch ops) with stable external API.

Tasks:
1. Extract runtime status + shared helper logic into dedicated modules.
2. Extract scan orchestration and queued-scan control logic.
3. Extract batch probe/extract orchestration logic.
4. Keep compatibility via `gui/components/dashboard.py` public `DashboardWidget` contract.

## Objective 4: Compatibility and Guardrails

Outcome: import paths, test patches, and parser-output expectations stay stable throughout refactor.

Tasks:
1. Maintain compatibility shims and explicit exports.
2. Add/update targeted tests for factory routing and key dashboard entrypoints.
3. Verify no changes to parser-coupled output contracts unless explicitly approved.

## Objective 5: Final Hardening and Documentation

Outcome: refactor lands with clear evidence and reproducible validation.

Tasks:
1. Run card-specific validation + focused regression.
2. Run manual HI smoke checklist for critical GUI flows.
3. Publish final PASS/FAIL report and residual risks.

## Planned Card Flow

`C0 -> C1 -> C2 -> C3 -> C4 -> C5 -> C6 -> C7 -> C8 -> C9 -> C10`

(see `docs/dev/modularization_15Apr/TASK_CARDS.md`)

## Exit Criteria

1. No behavioral regressions in SMB/FTP/HTTP scan + browse workflows.
2. Legacy DB startup and core list views still function.
3. Dashboard and browser modules are decomposed with stable import compatibility.
4. Validation evidence includes exact commands and PASS/FAIL status.
5. Manual HI checks completed (or explicitly marked pending with reason).
