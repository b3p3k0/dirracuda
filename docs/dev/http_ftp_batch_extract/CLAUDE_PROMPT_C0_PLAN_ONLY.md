You are joining an existing software project in-progress.

Read these first:
- /home/kevin/DEV/dirracuda-main/README.md
- /home/kevin/DEV/dirracuda-main/docs/TECHNICAL_REFERENCE.md
- /home/kevin/DEV/dirracuda-main/docs/dev/http_module/PROJECT_GUIDELINES.md
- /home/kevin/DEV/dirracuda-main/docs/dev/http_ftp_batch_extract/SPEC_DRAFT.md
- /home/kevin/DEV/dirracuda-main/docs/dev/http_ftp_batch_extract/TASK_CARDS.md
- /home/kevin/DEV/dirracuda-main/docs/dev/http_ftp_batch_extract/QUESTIONS_FOR_HI.md

Context:
- Current bulk extract support is SMB-oriented.
- Goal is parity for FTP and HTTP with minimal regression risk.
- We are in planning mode only for this step (no code edits yet).

Task now: CARD C0 PLAN ONLY (no code edits)

Requirements for your response:
1) Confirm exact runtime call paths for extract from:
   - Dashboard post-scan bulk extract
   - Server-list batch extract
2) Identify the concrete blockers preventing FTP/HTTP extraction today.
3) Propose the smallest safe implementation shape (files, functions, control flow).
4) Propose how to source FTP/HTTP file candidates (data contracts + runtime guards).
5) Define error semantics (`failed` vs `skipped`) for missing probe snapshot and malformed snapshot.
6) List required test updates, including any existing tests that currently assert FTP skip behavior.
7) Provide targeted validation commands only.
8) Call out risks, shortcuts to avoid, and assumptions.

Constraints:
- Do not implement anything.
- No broad refactor proposals.
- Preserve existing SMB behavior.
- Treat legacy/runtime-state guards as first-class.

Use this output format:
- Issue:
- Root cause:
- Plan:
- Files to change:
- Validation plan:
- Risks:
- Assumptions:
- HI test needed? (yes/no + short steps)
