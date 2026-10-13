# Keymaster Workspace

Updated: 2026-10-13
Status: secure-storage recovery implementation; see the approved packet and review record.

This workspace holds Keymaster's planning history. The current security contract,
cards, and acceptance evidence are in [SECURE_STORAGE_RECOVERY.md](SECURE_STORAGE_RECOVERY.md).
The April v1 spec and sketches describe the original feature, before encryption
and the current path/provider layout; they are historical context.

## Goal

Provide a desktop-style API key manager for fast key rotation during testing:

1. Add/Edit/Delete key entries.
2. Apply a selected key through three equivalent UX paths:
   - double-click row
   - right-click context menu
   - explicit `Apply` button
3. Applying a key must both populate and persist the active runtime key.

## Contents

1. `SPEC.md` - historical v1 product and technical contract.
2. `ASCII_SKETCHES.md` - UI wireframes and interaction sketches.
3. `FLOW_CHARTS.md` - flow charts for apply and persistence paths.
4. `ROADMAP.md` - objective-level sequence.
5. `TASK_CARDS.md` - one-card-at-a-time execution plan with validation gates.
6. `CLAUDE_PROMPTS.md` - copy/paste prompts for supervised Claude execution.
7. `OPEN_QUESTIONS.md` - resolved decisions log.
8. `LESSONS_LEARNED.md` - carry-forward guardrails and anti-regression notes.
9. `SECURE_STORAGE_RECOVERY.md` - approved October recovery contract, cards, and review evidence.

## Working Model

1. One small issue/card at a time.
2. Reproduce or confirm first.
3. Apply surgical fix only.
4. Run targeted validation with `./venv/bin/python ...` commands.
5. Report exact PASS/FAIL evidence.
6. Follow the session's operating agreement for commits. This recovery effort uses PA planning checkpoints and a distinct RA for acceptance commits; HI owns push.
