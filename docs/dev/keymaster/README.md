# Keymaster Workspace

Date: 2026-04-25
Status: Decisions locked by HI, ready for Claude plan iteration

This workspace holds the implementation contract for the experimental `Keymaster` feature.

## Goal

Provide a desktop-style API key manager for fast key rotation during testing:

1. Add/Edit/Delete key entries.
2. Apply a selected key through three equivalent UX paths:
   - double-click row
   - right-click context menu
   - explicit `Apply` button
3. Applying a key must both populate and persist the active runtime key.

## Contents

1. `SPEC.md` - product and technical contract.
2. `ASCII_SKETCHES.md` - UI wireframes and interaction sketches.
3. `FLOW_CHARTS.md` - flow charts for apply and persistence paths.
4. `ROADMAP.md` - objective-level sequence.
5. `TASK_CARDS.md` - one-card-at-a-time execution plan with validation gates.
6. `CLAUDE_PROMPTS.md` - copy/paste prompts for supervised Claude execution.
7. `OPEN_QUESTIONS.md` - resolved decisions log.
8. `LESSONS_LEARNED.md` - carry-forward guardrails and anti-regression notes.

## Working Model

1. One small issue/card at a time.
2. Reproduce or confirm first.
3. Apply surgical fix only.
4. Run targeted validation with `./venv/bin/python ...` commands.
5. Report exact PASS/FAIL evidence.
6. Do not commit unless HI explicitly says `commit`.
