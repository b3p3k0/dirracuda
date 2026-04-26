# CLAUDE_PROMPTS - Entrypoint Canonicalization

## Prompt A - Plan Audit (No Code)

```text
Create a PLAN ONLY for entrypoint canonicalization closure.
Do not edit code yet.

Read first:
- /home/kevin/DEV/dirracuda/docs/dev/entrypoint_canonicalization/README.md
- /home/kevin/DEV/dirracuda/docs/dev/entrypoint_canonicalization/SPEC.md
- /home/kevin/DEV/dirracuda/docs/dev/entrypoint_canonicalization/TASK_CARDS.md
- /home/kevin/DEV/dirracuda/docs/TECHNICAL_REFERENCE.md
- /home/kevin/DEV/dirracuda/CLAUDE.md
- /home/kevin/DEV/dirracuda/dirracuda
- /home/kevin/DEV/dirracuda/gui/main.py

Locked decisions:
1) Canonical runtime is ./dirracuda only.
2) gui/main.py runtime invocation must exit non-zero.
3) from gui.main import SMBSeekGUI remains import-compatible.
4) Tests are canonical-first with one legacy shim smoke test.

Output:
- Proposed assumptions
- Implementation plan
- Test plan
- Risks/blockers
```

## Prompt B - QA Review

```text
Review the entrypoint canonicalization implementation with a bug/regression lens.
Prioritize:
1) Runtime behavior mismatch risk
2) Test contract drift risk
3) Documentation contradiction risk

Return findings first with file:line references.
Then list residual risks and exact validation commands (use ./venv/bin/python).
```
