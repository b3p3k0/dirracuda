# SOP and Execution Constraints

Status: locked for this workstream

## Startup Requirements

Every PA, RA, or DA session must read:

1. Root `AGENTS.md` instructions supplied for the repository.
2. `README.md`, `CLAUDE.md`, and `docs/TECHNICAL_REFERENCE.md`.
3. This workspace's `README.md`, `SPEC.md`, `TASK_CARDS.md`,
   `VALIDATION_PLAN.md`, `RISK_REGISTER.md`, and `LESSONS_LEARNED.md`.
4. The approved plan for the active card.
5. The current versions of the field, development, documentation, role, and
   code-review SOPs cited in `CLAUDE_PROMPTS.md`.

Before changing product code, the acting agent must report 5-10 relevant
constraints and confirm the current branch, commit, worktree, entrypoint, test
commands, and touched-file line counts.

## Repository Guardrails

- Runtime GUI entrypoint: `./dirracuda`.
- `gui/main.py` is a legacy import shim and is never an implementation target.
- The GUI to CLI subprocess boundary must not be bypassed.
- Configuration is accessed through `SMBSeekConfig`; runtime code must not read
  `conf/config.json` directly.
- User paths come from `shared.path_service.get_paths()`.
- GUI message boxes use `gui.utils.safe_messagebox`.
- New modal dialogs use `ensure_dialog_focus()` after `grab_set()`.
- Tk dialogs are never destroyed from worker threads.
- GUI styles use named `SMBSeekTheme` styles.
- External services and live targets are mocked in tests.
- No database schema or migration changes are in scope.
- No auth, dependency, or CI changes are in scope.

## File-Size Rules

Check line counts before and after each implementation card:

| Lines | Rating |
|---:|---|
| `<=1200` | excellent |
| `1201-1500` | good |
| `1501-1800` | acceptable |
| `1801-2000` | poor |
| `>2000` | unacceptable unless explicitly justified |

If any touched file exceeds 1700 lines, stop before implementation and present
a modularization plan to HI/RA.

## Card Discipline

- One fresh Claude DA instance per card.
- Claude remains DA throughout the card. Its first phase is a slice-level
  implementation plan with no file edits.
- HI/RA approval in the UI authorizes the same Claude instance to execute that
  approved plan immediately; no second handoff prompt is required.
- PA refers only to Codex's completed workstream-level planning role.
- Do not combine cards because they touch the same file.
- Confirm the issue before editing.
- State root cause before proposing the fix.
- Use the smallest change that satisfies the approved contract.
- Preserve unrelated behavior.
- Add regression coverage for each fixed failure mode.
- Run targeted checks before broad checks.
- Review `README.md` and `docs/TECHNICAL_REFERENCE.md` at every card close.

## Approval Semantics

The following are distinct approvals:

1. Planning-pack approval authorizes Codex to transition from PA to RA.
2. Card-plan approval authorizes the same Claude DA instance to begin
   implementation immediately.
3. Card acceptance authorizes recording the card as complete.
4. The exact instruction `commit` authorizes one intentional commit.

None of these approvals authorizes a push.

## Required Card Report

```text
Issue:
Root cause:
Fix:
Files changed:
Validation run:
Result:
Line counts:
Residual risk:
HI test needed:
AUTOMATED: PASS | FAIL
MANUAL:    PASS | FAIL | PENDING
OVERALL:   PASS | FAIL | PENDING
```

## Blocked Work

If execution is blocked:

1. State the exact blocker.
2. Provide exact commands for HI to run.
3. State the expected result.
4. Do not invent validation evidence.
5. Leave the card `PENDING` or `FAIL`; never report partial work as complete.
