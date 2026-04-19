# Dorkbook Workspace

Date: 2026-04-19  
Status: Active implementation workspace

This folder is the planning + execution hub for the Dorkbook experimental feature.

## Canonical Scope (v1)

1. Add `Dorkbook` tab under Experimental dialog.
2. Launch singleton modeless Dorkbook window.
3. Store recipes in sidecar DB (`~/.dirracuda/dorkbook.db`).
4. Protocol tabs: SMB, FTP, HTTP.
5. Built-ins are read-only and italic.
6. Custom rows support add/edit/delete/copy.
7. Search is current-tab only.
8. Delete confirmation supports session-only mute.
9. Persist window geometry and active protocol tab.

## Source of Truth Files

1. `SPEC.md` — behavior and data contracts.
2. `ROADMAP.md` — objective sequence.
3. `TASK_CARDS.md` — card-by-card execution and gates.
4. `ASCII_SKETCHES.md` — mandatory UI contract.
5. `CLAUDE_PROMPTS.md` — prompt templates for implementation/review.
6. `OPEN_QUESTIONS.md` — unresolved decisions (should remain short/empty once locked).
7. `VALIDATION_REPORT.md` — final evidence and PASS/FAIL.
8. `LESSONS_LEARNED.md` — carry-forward implementation guardrails from completed work.
