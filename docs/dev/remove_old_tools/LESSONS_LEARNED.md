# Lessons Learned: Pry/RCE Sunset

Use this log to prevent repeat failures and preserve guardrails for future agents.

## Seed Guardrails

1. Remove root causes, not only UI toggles.
2. Delete dead paths from runtime wiring, not just presentation layers.
3. Preserve legacy compatibility first; never assume schema shape.
4. Keep changes surgical and card-scoped to simplify rollback and blame.
5. Use deterministic grep + targeted tests to prove sunset completeness.
6. Monitor file sizes continuously; pause for modularization before risk compounds.
7. Keep docs synchronized with actual code behavior before closeout.

## Common Pitfalls To Avoid

1. Removing a dialog file that also hosts shared generic components.
2. Dropping DB artifacts prematurely and breaking existing installations.
3. Deleting test coverage without adding replacement assertions for absence behavior.
4. Leaving stale config accessors that silently no-op but confuse maintainers.

## Entry Template (Append Per Card)

1. Date:
2. Card:
3. What changed:
4. Root cause prevented:
5. Regression caught/avoided:
6. New guardrail added:
7. Follow-up needed:
