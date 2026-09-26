# Claude Prompt Pack - Keyboard Control Enhancement

Date: 2026-06-30

## PA/RA to DA Planning Prompt

```text
You are Claude acting as DA for Dirracuda.

Prepare a plan for Card C{N} from docs/dev/kbd_ctrl_improve/TASK_CARDS.md.
Do not code yet.

Read first:
- AGENTS.md
- CLAUDE.md
- README.md keyboard section
- docs/KBD_QUICKREF.md
- docs/dev/kbd_ctrl_improve/README.md
- docs/dev/kbd_ctrl_improve/SPEC.md
- docs/dev/kbd_ctrl_improve/TASK_CARDS.md
- docs/dev/kbd_ctrl_improve/VALIDATION_PLAN.md

Constraints:
- Preserve behavior outside the card.
- Runtime entrypoint remains ./dirracuda.
- Do not bypass GUI-to-CLI subprocess boundaries.
- Use gui/utils/keybindings.py for shared shortcut policy.
- No new bind_all usage except existing app-global Ctrl/Cmd+Q/H/T.
- Check touched-file line counts before and after.
- Pause if any touched file exceeds 1700 lines.
- No commits.

Plan output:
- Issue:
- Suspected root cause:
- Files likely touched:
- Exact change steps:
- Tests/commands:
- Line-count risks:
- README/docs impact:
- HI manual test:
- Risks/assumptions:
```

## PA/RA Review Prompt

```text
Review Claude/DA's plan or implementation for Card C{N}.

Use docs/dev/kbd_ctrl_improve/SPEC.md and TASK_CARDS.md as the contract.
Also apply:
- https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_CODE_REVIEW_GUIDE.md

Focus:
1. Does the plan solve the card and only the card?
2. Does it preserve existing callbacks and confirmations?
3. Does it add/adjust tests for the risky behavior?
4. Does it avoid runtime entrypoint and GUI-to-CLI boundary violations?
5. Does it report exact validation and line counts?
6. Does it update README/docs only when behavior changed?

Return:
- Summary: acceptable / needs revision / HI decision needed
- High-Risk Issues:
- Required Fixes:
- Suggested Improvements:
- Evidence:
- Open Questions:
```

## DA Implementation Closeout Template

```text
- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + exact steps)
- Touched file line counts:
- README review:
- Risks/assumptions:
```
