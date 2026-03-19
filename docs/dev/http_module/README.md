# HTTP Module Workspace

Status: planning kickoff (2026-03-19)

This folder is the planning and handoff workspace for adding an HTTP(S) module to SMBSeek with the same operational feel as the SMB and FTP modules.

## Locked Inputs From HI (2026-03-19)

1. Build HTTP(S) as a parallel module, similar to SMB/FTP behavior and UX.
2. Prefer reuse of existing logic and patterns over risky refactors.
3. Breaking existing SMB/FTP functionality is unacceptable.
4. Current Shodan candidate query baseline: `http.title:"Index of /"`.
5. Required parity outcomes include:
   - scan launch dialog (layout + functionality),
   - probe behavior (iteration + counting existing dirs/files),
   - DB entry behavior (including filtering/handling hosts with 0 accessible dirs/files).
6. Any DB schema changes must migrate automatically at startup with no user scripts or manual steps.

## Locked Decisions (HI Follow-Up, 2026-03-19)

1. HTTP will be integrated into DB-backed unified server browsing in MVP (not deferred).
2. Verification scope includes both HTTP and HTTPS targets.
3. HTTPS discovery/probe allows insecure TLS verification by default (misconfigured hosts are in-scope).
4. Add dialog toggle for TLS behavior in HTTP scan dialog (legacy-style operator control).
5. Hosts with 0 accessible dirs/files must persist in DB.
6. Advanced filter behavior: when `Shares > 0` is enabled, those 0-count HTTP rows must be hidden.
7. Probe behavior includes one-level recursion.
8. UX target is full built-in browser parity (same look/feel model as current SMB/FTP server browsing).

## Working Model

- Human: priorities, acceptance criteria, and manual UI/runtime validation.
- Codex: planning, risk review, prompt generation, and quality gate enforcement.
- Claude Code: implementation worker per approved card.

## Document Map

- `PROJECT_GUIDELINES.md`: lessons and guardrails from FTP module.
- `HTTP_MVP_ACTION_PLAN.md`: end-to-end phased delivery strategy.
- `HTTP_PHASE_TASK_CARDS.md`: Claude-ready cards, one at a time.
- `claude_plan/`: per-card implementation plans and handoff artifacts.
- `db_import/`: reserved for HTTP-specific host-list/import integration cards.

## Completion Labels (Required in Reports)

```text
AUTOMATED: PASS | FAIL
MANUAL:    PASS | FAIL | PENDING
OVERALL:   PASS | FAIL | PENDING
```
