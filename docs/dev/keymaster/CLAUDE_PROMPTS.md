# Keymaster Claude Prompts

Date: 2026-04-25

Use these prompts in sequence. Start with Prompt A.

---

## Prompt A - Plan Only (No Code Changes)

```text
You are implementing Keymaster in /home/kevin/DEV/dirracuda.

Before proposing any code edits:
1) Read:
   - docs/dev/keymaster/README.md
   - docs/dev/keymaster/SPEC.md
   - docs/dev/keymaster/ASCII_SKETCHES.md
   - docs/dev/keymaster/FLOW_CHARTS.md
   - docs/dev/keymaster/ROADMAP.md
   - docs/dev/keymaster/TASK_CARDS.md
   - docs/dev/keymaster/LESSONS_LEARNED.md
   - docs/dev/keymaster/OPEN_QUESTIONS.md
2) Also inspect existing integration seams:
   - gui/components/dashboard_experimental.py
   - gui/components/experimental_features/registry.py
   - gui/components/dorkbook_window.py
   - experimental/dorkbook/store.py
   - gui/tests/test_experimental_features_dialog.py
   - gui/tests/test_dashboard_api_key_gate.py

Treat the following decisions as LOCKED (do not reopen unless you find a hard blocker):
1. Shodan-only scope in v1.
2. Keep a lightweight generic provider contract in storage/model layers for future expansion.
3. Apply writes only to active config `shodan.api_key`.
4. Running scans continue using key captured at scan start; applied key affects future scans only.
5. Key preview in table is first 4 + asterisks + last 4.
6. Delete confirmation has no mute option in v1.

Task:
Produce a surgical implementation plan for C1-C4 only, with:
- exact file list to add/edit
- risk list
- test list (must use ./venv/bin/python)
- assumptions (only if required; minimize new assumptions)

Rules:
- NO code edits in this step.
- NO commits.
- Keep plan one-card-at-a-time.
- Prioritize root-cause correctness and regression safety.
- Include line-count risk watch for touched files.

Output format:
- Proposed assumptions
- File-by-file plan
- Validation commands
- Risks/blockers
```

---

## Prompt B - Implement One Card (After Plan Approval)

```text
Implement only card: <CARD_ID> from docs/dev/keymaster/TASK_CARDS.md.

Hard rules:
1) Confirm/reproduce the specific issue for this card.
2) Apply smallest safe fix only.
3) Run targeted validation using ./venv/bin/python commands only.
4) Do not commit.
5) If blocked, provide exact human unblock commands and expected outputs.
6) Check touched file line counts before and after; apply rubric from TASK_CARDS.md.
7) If any touched file exceeds 1700 lines, stop and provide modularization plan.

Required response format:
- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + short steps)

Also update docs/dev/keymaster/LESSONS_LEARNED.md with any new guardrail discovered in this card.
```

---

## Prompt C - Card QA/Review Pass

```text
Review the completed <CARD_ID> implementation with a code-review mindset.

Focus:
1) bugs
2) regressions
3) missing tests
4) config/data safety
5) apply-path parity (button/context/double-click all call same logic)

Return findings first, ordered by severity, with file:line references.
Then list residual risks and exact additional tests to run (using ./venv/bin/python).
```

---

## Prompt D - C7 Plan Only

```text
Create a PLAN ONLY for card C7 from /home/kevin/DEV/dirracuda/docs/dev/keymaster/TASK_CARDS.md.
Do not edit code yet.

Before planning, read:
- /home/kevin/DEV/dirracuda/docs/dev/keymaster/README.md
- /home/kevin/DEV/dirracuda/docs/dev/keymaster/SPEC.md
- /home/kevin/DEV/dirracuda/docs/dev/keymaster/TASK_CARDS.md
- /home/kevin/DEV/dirracuda/docs/dev/keymaster/LESSONS_LEARNED.md
- /home/kevin/DEV/dirracuda/gui/components/keymaster_window.py
- /home/kevin/DEV/dirracuda/gui/tests/test_keymaster_window.py

Locked decisions for C7:
1) Query Credits only (not scan credits) in Keymaster grid.
2) Run one all-keys check on Keymaster window startup.
3) Recheck supports BOTH all-keys and selected-key refresh.
4) Runtime-only cache for balances; no DB schema changes/migrations.
5) Display states:
   - success -> numeric credits
   - invalid key -> "Invalid key"
   - transient/network/API failure -> "Error"
   - not checked -> "Not checked"
   - in-progress -> "Checking..."
6) Keep existing Apply behavior unchanged.
7) Do not log or expose raw API key values.
8) Must use ./venv/bin/python for validation commands.

Implementation constraints:
- Keep UI responsive (no blocking calls on Tk main thread).
- Use safe Tk-thread UI updates for async work.
- Smallest safe surgical edits.
- Respect file-size rubric and stop-and-plan rule from TASK_CARDS.

Output format:
- Proposed assumptions
- File-by-file plan
- Test plan (new/updated tests)
- Validation commands
- Risks/blockers
```
