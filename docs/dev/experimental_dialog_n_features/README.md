# Experimental Dialog + Features Workspace

Date: 2026-04-17
Status: Planning artifacts ready for HI review

## Purpose

This workspace defines the spec and execution plan for consolidating experimental UI entrypoints into a single `Experimental` dialog.

Primary target in this cycle:
1. Keep existing Reddit experimental capability.
2. Add a second tab/module scaffold: `placeholder`.
3. Remove Reddit entrypoint buttons from:
   - Start Scan dialog
   - Server List header
4. Keep `Experimental` as a permanent dashboard control.
5. Add one-time experimental warning text with dismiss checkbox behavior.
6. Preserve behavior quality and avoid regressions in scan/server workflows.

## Artifacts

1. `SPEC.md`
   - Product/UX/architecture spec.
2. `ASCII_SKETCHES.md`
   - Before/after UI sketches + ASCII workflow flowcharts.
3. `ROADMAP.md`
   - Objective-level sequence from planning through validation.
4. `TASK_CARDS.md`
   - One-card-at-a-time execution plan with PASS/FAIL gates.
5. `CLAUDE_PROMPTS.md`
   - Copy/paste prompts for Claude execution + review loops.
6. `OPEN_QUESTIONS.md`
   - Resolved HI decisions captured for execution alignment.

## Working model

- One card at a time.
- Surgical edits only.
- Exact validation commands and PASS/FAIL evidence required per card.
- No commits unless HI explicitly says `commit`.
