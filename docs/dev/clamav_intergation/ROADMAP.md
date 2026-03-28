# ClamAV Optional Integration Roadmap

Date: 2026-03-27
Status: Draft

## Objective 1: Ship safe optional ClamAV for bulk extract (phase 1)

Tasks:
1. Build scanner adapter with deterministic verdict parsing.
2. Add config-backed controls (enabled, backend, timeout, destinations).
3. Integrate scanner in bulk extract paths only.

Definition of done:
- Bulk extract behavior is unchanged when disabled.
- Enabled mode yields deterministic per-file AV outcomes.

## Objective 2: Implement placement contracts

Tasks:
1. Move clean files to extracted root.
2. Move infected files to `quarantine/known_bad` subtree.
3. Leave scanner-error files in original quarantine path.
4. Log placement decisions in summary + activity logs.

Definition of done:
- Clean/infected/error outcomes are deterministic and auditable.

## Objective 3: Add operator feedback controls

Tasks:
1. Add ClamAV summary dialog for bulk extract results.
2. Add session-only mute toggle (until restart).
3. Wire dialog for both bulk entry points.

Definition of done:
- Dialog behavior is consistent, useful, and suppressible per session.

## Objective 4: Keep architecture ready for phase 2+

Tasks:
1. Introduce reusable post-processing seam now.
2. Avoid coupling scanner logic directly to dashboard/server-list caller specifics.
3. Document browser-download extension path.

Definition of done:
- Browser-download integration can reuse scanner/placement logic without major refactor.

## Objective 5: Harden and validate

Tasks:
1. Add focused tests for scanner, integration, placement, and mute behavior.
2. Run targeted regression for touched components.
3. Run final GUI/shared suite and manual EICAR validation.
4. Prepare rollback runbook and validation report.

Definition of done:
- Automated + manual gates are closed with explicit PASS/FAIL evidence.

## Suggested Implementation Order

1. C1 scanner adapter
2. C2 reusable post-processor seam
3. C3 bulk extract integration
4. C4 placement routing
5. C5 results dialog + session mute
6. C6 expanded config UI
7. C7 full validation and rollback drill

## Major Risks To Watch

1. UI freeze from synchronous scan calls on large batches.
2. Cross-filesystem move edge cases during promotion/routing.
3. Scanner-missing behavior causing operator confusion.
4. Drift between post-scan bulk and server-list batch behavior.

## Exit Criteria

1. Phase 1 works for bulk extract entry points.
2. Clean files land under extracted root.
3. Infected files land under quarantine known-bad path.
4. Session mute works and resets on app restart.
5. No regressions when ClamAV is disabled.

