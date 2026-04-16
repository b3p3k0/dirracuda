You are joining an existing software project in-progress.

Startup requirements (before first fix):
1) Read relevant onboarding and working docs:
- https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/AI_AGENT_FIELD_GUIDE.md
- /home/kevin/DEV/dirracuda/README.md
- /home/kevin/DEV/dirracuda/CLAUDE.md
- /home/kevin/DEV/dirracuda/docs/TECHNICAL_REFERENCE.md
- /home/kevin/DEV/dirracuda/docs/dev/modularization_15Apr/CONCEPT.md
- /home/kevin/DEV/dirracuda/docs/dev/modularization_15Apr/ROADMAP.md
- /home/kevin/DEV/dirracuda/docs/dev/modularization_15Apr/TASK_CARDS.md
- /home/kevin/DEV/dirracuda/docs/dev/modularization_15Apr/RISK_REGISTER.md
- /home/kevin/DEV/dirracuda/docs/dev/ftp_module/LESSONS.md
- /home/kevin/DEV/dirracuda/docs/dev/http_module/PROJECT_GUIDELINES.md

2) Summarize key constraints back in 5-10 bullets before changing code.

Carry-forward lessons (must apply):
- Fix root causes, not symptom suppression.
- Prevent known failures from accumulating.
- Treat legacy compatibility (especially migrations/data contracts) as first-class.
- Guard schema/data operations by runtime state (no structure assumptions).
- Avoid performance regressions on UI/runtime hot paths.
- Be explicit and safe with type coercion/validation logic.
- If command execution is blocked, provide exact unblock steps.

Working model:
- One small issue at a time.
- No bulk triage unless requested.
- Confirm, fix surgically, validate, report, wait.

Execution rules:
1) Reproduce/confirm the specific issue/card scope.
2) Apply the smallest safe fix.
3) Run targeted validation for touched components.
4) Report concise PASS/FAIL with exact commands.
5) Do NOT commit.
6) If blocked: state why, give exact human-run commands, state expected result.

Task now: C0 PLAN ONLY (no code edits)

- Card: `C0 - Contract Freeze + Baseline (Plan Only)`
- Source of truth: `/home/kevin/DEV/dirracuda/docs/dev/modularization_15Apr/TASK_CARDS.md`

Required outputs for C0:
1) Public import/API contract inventory for:
   - `gui.components.dashboard.DashboardWidget`
   - `gui.components.unified_browser_window` public symbols
2) Patch-sensitive test inventory (module-path monkeypatch dependencies).
3) Baseline automated command set for C1-C10.
4) Proposed `BASELINE_CONTRACTS.md` structure and exact sections.
5) Top risks + mitigation mapping aligned to `RISK_REGISTER.md`.
6) PASS/FAIL gate definitions for every upcoming card.
7) Explicit assumptions and blockers.

Constraints:
- No code edits.
- No speculative redesign beyond roadmap/task-cards scope.
- Keep plan tightly scoped, reversible, and testable.

Use this response format:
- Issue:
- Root cause:
- Plan:
- Files to change (C0 only):
- Validation plan:
- Risks:
- Assumptions:
- HI test needed? (yes/no + short steps)
