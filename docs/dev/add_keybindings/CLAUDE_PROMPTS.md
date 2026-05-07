# Claude Prompt Pack - Keyboard Accessibility Phase 1

## Prompt Template (Per Card)

```text
Implement Card C{N} from docs/dev/add_keybindings/TASK_CARDS.md.

Constraints:
- Preserve behavior outside requested card scope.
- Use shared keybinding helper patterns; do not add ad-hoc per-dialog drift.
- Keep Enter behavior focus-safe for multiline Text widgets.
- No ad-hoc bind_all usage (only explicit app-global `Ctrl/Cmd+Q/H/T` bindings are allowed).
- No commits.

Deliver:
- Issue / Root cause / Fix summary
- Files changed
- Exact validation commands + PASS/FAIL
- Touched file line counts (before/after) with rubric
- Risks/assumptions
- HI manual test checklist
```

## Reviewer Prompt

```text
Review the implementation for Card C{N} against docs/dev/add_keybindings/SPEC.md and TASK_CARDS.md.

Focus:
1) Contract correctness:
   - Esc/Enter/Ctrl+S/Ctrl+W behavior
   - Multiline Text Enter exception
   - Dashboard Alt mapping and reserved keys
2) Regression risk:
   - Existing callbacks still used
   - No destructive behavior bypass
   - No unintended bind_all/global event leakage outside explicit app-global keys
3) Validation evidence:
   - Commands are real and pass/fail is explicit
   - File-size rubric included

Return:
- Findings ordered by severity
- Missing tests/validation
- Exact remediation diffs required
```
