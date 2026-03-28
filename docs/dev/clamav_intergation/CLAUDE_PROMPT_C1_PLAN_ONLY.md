You are joining an existing software project in-progress.

Read these first:
- /home/kevin/DEV/smbseek-smb/README.md
- /home/kevin/DEV/smbseek-smb/CLAUDE.md
- /home/kevin/DEV/smbseek-smb/docs/dev/clamav_intergation/SPEC_DRAFT.md
- /home/kevin/DEV/smbseek-smb/docs/dev/clamav_intergation/TASK_CARDS.md
- /home/kevin/DEV/smbseek-smb/docs/dev/clamav_intergation/RESEARCH_2026-03-27.md

Context and locked decisions:
- Phase 1 scope is bulk extract only.
- Included callers: dashboard post-scan bulk extract + server-list batch extract.
- ClamAV is optional and defaults fail-open.
- Clean files moved to extracted root.
- Infected files moved to quarantine known-bad subtree.
- Scanner-error files stay in original quarantine path.
- We want architecture that can later be reused by browser downloads.

Task now: CARD C1 PLAN ONLY (no code edits yet)
- Card: "ClamAV Backend Adapter"
- Goal: produce a surgical implementation plan for `shared/clamav_scanner.py` and tests.

Requirements for your response:
1) Confirm exact runtime call sites that will consume the adapter later (from real code paths).
2) Propose adapter API with explicit input/output schema.
3) Define command invocation strategy for `auto`, `clamdscan`, and `clamscan`.
4) Define deterministic mapping for exit/result parsing to `clean|infected|error`.
5) Define timeout + missing-binary behavior for fail-open workflows.
6) List precise files to change for C1 and C1 tests only.
7) Provide validation commands for C1 only.
8) Provide risk list + mitigation list.
9) Explicitly call out any assumptions.

Constraints:
- No broad refactors.
- No speculative features outside C1.
- Do not implement yet.
- Keep plan tightly scoped, reversible, and testable.

Use this output format:
- Issue:
- Root cause:
- Plan:
- Files to change (C1 only):
- Validation plan:
- Risks:
- Assumptions:
- HI test needed? (yes/no + short steps)
