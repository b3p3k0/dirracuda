# Censys Integration Workspace

Date: 2026-05-14
Status: C0/C1 scaffold complete, implementation cards queued

This workspace defines the supervised implementation contract for the experimental Censys integration in Dirracuda.

## Goal

Build a sidecar-only `Censys Discovery` experimental module that can discover FTP/HTTP/SMB candidates from Censys Platform v3, persist them in a module DB, and allow explicit manual promotion into main Dirracuda DB.

## Canonical Conventions

1. Workspace path: `docs/dev/censys_integration/`
2. Module package path: `experimental/censys_discovery/`
3. UI entrypoint label: `Censys Discovery`
4. Runtime entrypoint for app behavior checks: `./dirracuda`
5. One card at a time, with explicit PASS/FAIL evidence

## Locked v1 Constraints

1. Platform API v3 only; no Legacy Search API fallback.
2. PAT/Bearer auth only.
3. Experimental sidecar architecture only; do not rewrite core SMB/FTP/HTTP scan flow.
4. CenQL nested service clauses are mandatory for service-specific filtering.
5. Credit handling must be conservative and explicit (estimate + live balance/usage).
6. No secret leakage in logs, status lines, exceptions, or test fixtures.
7. Schema and data operations must be guarded by runtime DB state checks.
8. No commit unless HI explicitly says `commit`.

## Scope Shape

1. C0-C1: contract freeze and docs scaffold.
2. C2-C4: UI shell + config contract + REST client/model layer.
3. C5-C7: protocol adapters in order FTP -> HTTP -> SMB.
4. C8: results browser + manual promotion hooks.
5. C9: credit UX closeout + docs sync + RA final review.

## Workspace Contents

1. `SPEC.md` - locked product/technical contract.
2. `ROADMAP.md` - objective sequence and dependencies.
3. `TASK_CARDS.md` - card-by-card execution units with validation gates.
4. `CLAUDE_PROMPTS.md` - reusable prompt templates for card implementation and review.
5. `VALIDATION_PLAN.md` - deterministic validation commands and manual checks.
6. `RISK_REGISTER.md` - key risks, severity, mitigations, trigger checks.
7. `LESSONS_LEARNED.md` - carry-forward guardrails.
8. `FIELD_QUERY_MATRIX.md` - CenQL query and field contract by protocol.
9. `claude_plans/` - copy-paste card prompts for downstream coding agents.

## Source Baseline

Primary source set used for this contract:

1. https://docs.censys.com/docs/platform-api-transition-guide
2. https://docs.censys.com/reference/v3-globaldata-search-query
3. https://docs.censys.com/reference/v3-globaldata-search-aggregate
4. https://docs.censys.com/docs/censys-query-language
5. https://docs.censys.com/docs/platform-quickstart-guide
6. https://docs.censys.com/changelog/upcoming-changes-to-legacy-search-data-and-apis
7. https://docs.censys.com/docs/asm-host-data-definitions
8. https://docs.censys.com/docs/platform-credits-free-starter
9. https://docs.censys.com/docs/platform-credits-enterprise
10. https://docs.censys.com/reference/v3-accountmanagement-user-credits
11. https://docs.censys.com/reference/v3-accountmanagement-user-credits-usage
12. https://docs.censys.com/reference/v3-accountmanagement-org-credits
13. https://docs.censys.com/reference/v3-accountmanagement-org-credits-usage

Agency/process guides:

1. https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_FIELD_GUIDE.md
2. https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_DOC_STYLE_GUIDE.md
3. https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_CODE_REVIEW_GUIDE.md

## Working Model

1. Confirm issue and root cause before edits.
2. Apply smallest safe fix.
3. Run targeted validation for touched files.
4. Report exact commands and PASS/FAIL outcomes.
5. Pause for HI direction between cards.
