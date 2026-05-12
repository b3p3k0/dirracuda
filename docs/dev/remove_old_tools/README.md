# Pry/RCE Sunset Workspace

This workspace is the source of truth for supervised sunset work that permanently removes Pry and RCE capabilities from Dirracuda runtime paths, while preserving legacy database compatibility.

## Objective

Ship a safe, reversible, one-card-at-a-time removal of Pry and RCE from runtime, UI, CLI, tests, and selected user-facing documentation:

- `README.md`
- `docs/TECHNICAL_REFERENCE.md`

No destructive schema migration is included in this scope.

## Locked Decisions

1. Schema strategy: runtime sunset only (no schema drop in this project).
2. Legacy data policy: preserve historical rows by default.
3. Docs scope: update only user + technical docs listed above, plus this workspace.
4. Delivery mode: one card at a time with PA/RA supervision and explicit gate approval.

## Execution Model

For each card:

1. Confirm issue and touchpoints.
2. Identify root cause.
3. Apply smallest safe fix.
4. Run targeted validation first, broader regression only where risk warrants.
5. Report with exact command evidence and PASS/FAIL.
6. Do not commit unless HI explicitly says `commit`.

## Guardrails

1. Preserve behavior outside Pry/RCE scope.
2. Preserve legacy DB compatibility; do not assume live schema shape.
3. Use deterministic checks over assumptions.
4. Track line counts for every touched file.
5. If any touched file exceeds 1700 lines, stop and propose modularization before continuing.
6. Reject bundled refactors that are unrelated to the active card.

## Link Map

- Spec: `SPEC.md`
- Roadmap: `ROADMAP.md`
- Task cards: `TASK_CARDS.md`
- Risk register: `RISK_REGISTER.md`
- Lessons learned: `LESSONS_LEARNED.md`
- Claude prompts: `CLAUDE_PROMPTS.md`

## Security Rationale Sources

1. OWASP Attack Surface Analysis Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Attack_Surface_Analysis_Cheat_Sheet.html
2. OWASP Secrets Management Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
3. CISA/FBI Product Security Bad Practices (2025): https://www.cisa.gov/resources-tools/resources/product-security-bad-practices
4. NIST SP 800-171r3 (Least Functionality): https://nvlpubs.nist.gov/nistpubs/SpecialPublications/800-171r3/NIST.SP.800-171r3.html
