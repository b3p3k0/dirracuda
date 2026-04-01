# HTTP + FTP Bulk Extract Workspace

Status: planning in progress (pre-implementation)
Date: 2026-04-01

This folder is the planning/supervision workspace for extending bulk extract beyond SMB so FTP and HTTP rows can be extracted through the same operator workflows.

## Problem Snapshot

Current behavior is inconsistent across paths:

1. `gui/components/server_list_window/actions/batch.py` explicitly skips FTP/HTTP extract targets.
2. `gui/components/dashboard.py` post-scan bulk extract calls SMB-only `extract_runner.run_extract(...)` for all rows.
3. `gui/utils/extract_runner.py` is SMB transport-specific (impacket SMBConnection only).
4. FTP/HTTP already have probe snapshot runners and browser download primitives we can reuse.

## Locked Intent (from HI)

1. Expand bulk downloads to FTP and HTTP (not SMB-only).
2. Use surgical, low-risk changes.
3. Iterate on a strong plan first (AI-HI-AI loop) before coding.
4. Keep existing behavior stable outside the requested change.

## Doc Map

1. `SPEC_DRAFT.md` — product/technical spec draft with proposed architecture.
2. `TASK_CARDS.md` — one-card-at-a-time execution plan for Claude.
3. `QUESTIONS_FOR_HI.md` — open decisions + recommended defaults.
4. `VALIDATION_GATES.md` — automated + manual acceptance gates.
5. `CLAUDE_PROMPT_C0_PLAN_ONLY.md` — prompt to collect Claude's first implementation plan (no code edits).
