# Lessons Learned: FTP Module (This Instance)

Date: 2026-03-19  
Scope: retrospective of this AI-HI-AI collaboration cycle, focused on planning, review, and execution handoff quality.

---

## 1) What Worked Well

### Tight review loops improved output quality
- Repeated plan reviews (R1 -> R2 -> R3 -> R4) caught factual drift, invalid assumptions, and command-level mistakes before they became implementation bugs.
- PASS/FAIL gates forced objective checkpoints and reduced ambiguity.

### Human steering was high quality
- The human operator consistently asked for clear QA/QC, explicit verification, and cleanup of "small issues."
- This prevented technical debt from quietly accumulating and kept standards high.

### Incremental hardening was effective
- First, get MVP behavior working.
- Then, harden with tests and docs.
- This sequencing helped the team ship value and then stabilize it in a controlled way.

### Cross-checking claims against reality paid off
- Verifying reports against real repo state (tests, commit history, schema, file contents) prevented false confidence.
- Several "reported complete" items required correction after direct validation.

### Handoff mindset improved continuity
- Creating explicit handoff artifacts (plan files, status summaries, commit notes) reduced dependence on memory.
- Future-agent onboarding becomes faster when "why" and "what remains" are documented.

---

## 2) What Did Not Work Well

### State drift between plan text and repo reality
- Some plan revisions still carried stale statements (commit status, README status, assumptions already resolved).
- This caused avoidable review churn and extra revision cycles.

### Command-level inaccuracies in plans
- A few verification commands had wrong schema fields or wrong expected output shapes.
- These were small but high-impact because they break confidence in the test plan.

### Hidden gitignore behavior caused friction
- Important files under `docs/dev/` and `gui/tests/` were ignored, which made changes "disappear" from normal `git status`.
- This created confusion during commit/closeout and required force-adding.

### Ambiguous completion language
- "Card complete" was reported while manual GUI gates remained unexecuted.
- Better labeling would have been "automated complete, manual validation pending."

### Interruptions during git operations
- Parallel/overlapping git actions produced `index.lock` issues and unnecessary confusion.
- Sequential commit flow is safer for reliability and auditability.

---

## 3) Collaboration Lessons (AI-HI-AI)

### Positive pattern: AI drafts -> human critique -> AI correction
- This pattern worked extremely well when critiques were concrete (specific defects, exact expected fixes).
- Best results came from short, direct correction cycles with explicit pass/fail criteria.

### Positive pattern: insist on verification, not narrative
- Requiring commands, outputs, and schema checks prevented hand-wavy completion claims.

### Improvement needed: stronger "single source of truth" discipline
- Plans, reports, and execution status should be synchronized to current repo state before each revision handoff.
- Every revision should include a quick "state refresh" section (branch, latest commit, working tree status, baseline tests).

### Improvement needed: clearer "done" semantics
- Separate:
  1. Automated validation done
  2. Manual validation pending
  3. Fully complete
- Avoid using "complete" unless all required gates are actually closed.

---

## 4) Practical Process Improvements for Future Modules (HTTP Docs)

1. Add a standard "Pre-Revision Reality Check" block to every plan update:
- `git log -n 5`
- `git status --short`
- baseline test command + result
- known expected failures list

2. Add a "Validation Command QA" step before approving plans:
- verify DB column names against schema
- verify expected output types against actual function contracts
- verify file paths actually exist

3. Use explicit status labels in reports:
- `AUTOMATED: PASS/FAIL`
- `MANUAL: PASS/FAIL/PENDING`
- `OVERALL: PASS/FAIL/PENDING`

4. Add a repo-specific note about ignored paths near the top of planning docs:
- if test/docs paths are ignored, document `git add -f` requirement early.

5. Avoid parallel git operations when committing:
- stage -> verify diff -> commit -> verify log
- this prevents lock conflicts and partial state confusion.

6. Keep a lightweight "known baseline failures" registry in module docs:
- one source of truth for expected failing tests
- prevents recurring confusion in "no new failures" validation.

7. Treat docs as product surface:
- verify naming/keys against real config/schema during doc updates
- minor doc inaccuracies can mislead operators as much as code bugs.

---

## 5) Suggested Working Agreement for Next Module

- Prefer short review cycles over large rewrites.
- Require evidence for all completion claims.
- Fix low-cost correctness issues immediately when found.
- Keep reports explicit about what is verified vs assumed.
- Preserve human control on scope boundaries and risk decisions.

---

## 6) Bottom Line

This phase succeeded because the collaboration stayed disciplined: iterative reviews, concrete verification, and willingness to correct details. The biggest opportunities for improvement are state synchronization, command accuracy, and clearer completion semantics. Applying those three improvements should make future module builds faster, cleaner, and less error-prone.

---

## 7) Addendum: Late-Phase Runtime Integration Lessons (Same Instance)

This addendum captures lessons from the second half of this instance, when "plan complete" did not initially match runtime behavior.

### What Worked Well

#### Fast pivot from plan confidence to runtime truth
- Once runtime mismatch was observed (UI not reflecting planned merge behavior), the team shifted quickly from document review to direct behavior tracing.
- This was the right move and prevented more plan churn.

#### Narrow, user-visible acceptance checks were effective
- Validating concrete UX outcomes ("single button", "Type column visible", "FTP rows in server list", "probe status updates") exposed gaps much faster than internal-only checks.
- Manual HI verification drove useful, high-signal bug reports.

#### Incremental commits at meaningful checkpoints reduced risk
- Committing at known-good milestones helped stabilize progress and made rollback/traceability easier.
- Explicitly sharing commit hash/message improved human continuity and reduced context loss.

### What Did Not Work Well

#### QA focused too much on code/test artifacts, not enough on end-to-end runtime
- Work was reviewed as "complete" while critical runtime behaviors were still broken.
- This is the main process gap observed in this instance.

#### Integration boundary mismatch (CLI flow vs GUI flow)
- Code changes landed in some protocol-aware paths, but active runtime paths still used legacy/SMB-only behavior in places.
- Result: "implemented" features were not always exercised by real user flow.

#### Over-planning cycle before execution
- Several rounds became plan refinement loops with diminishing returns.
- Valuable, but delayed "real behavior on the table" validation.

#### Settings persistence architecture had hidden coupling
- FTP dialog persistence behaved in-session but not reliably across app restarts.
- Root cause was architectural (settings manager instance usage + defaults alignment), not only dialog form code.

### Concrete Improvements for Next Module (HTTP Docs)

1. Add a mandatory "Runtime Gate" after each major card:
- Not just tests; verify exact UI/UX behavior that the user experiences.

2. Require "Path Coverage" in every implementation report:
- For each feature, list the exact runtime call path exercised (entrypoint -> worker -> DB write -> UI readback).
- This catches "implemented but not wired" issues early.

3. Use "execute sooner, polish later" once plan quality is above threshold:
- Set a hard cap on plan iterations, then move to implementation + validation.

4. Standardize commit reporting:
- Always include commit hash + message in completion replies.
- This materially improved operator context in this instance.

5. Validate persistence at two levels every time:
- In-session reopen
- Full app restart
- Both must pass before marking persistence done.

6. Track architecture-level risks explicitly (not just function-level TODOs):
- Example from this instance: singleton/shared settings usage and default schema alignment.

7. Keep the "small issues fixed now" discipline
- Addressing low-severity sharp edges immediately prevented repeated churn later.

### Bottom Line for This Addendum

The biggest lesson from this later phase is that end-to-end runtime validation must be treated as a first-class gate, equal to tests and code review. The collaboration improved significantly once the team prioritized observed behavior over completion narratives.

---

## 8) Final Wrap-Up for This Instance (Refactor -> Product Hardening)

This instance was strongest when it moved from "plan says done" to "runtime proves done." Most late wins came from validating real GUI behavior against user expectations, then tightening wiring, wording, and persistence details.

### Additional Positive Observations

- SMB-as-baseline worked well as a decision framework.
  - Using SMB behavior as the reference reduced debate and made acceptance criteria concrete.
- Small UX corrections had outsized impact.
  - Dialog wording, color semantics, batching cadence, and protocol-specific labels materially improved operator trust.
- Iterative correction with explicit user feedback was highly effective.
  - "Still broken" reports were handled quickly when backed by concrete output samples.
- Commit cadence improved stability.
  - Focused commits for each meaningful fix made progress auditable and reduced merge confusion.

### Additional Negative Observations

- Environment/path drift was a recurring hidden failure mode.
  - The GUI repeatedly executed an older checkout due persisted path settings, creating false signals that recent fixes "didn't work."
- Several issues were "integration truth" problems, not pure code problems.
  - The code could be correct in-repo while runtime still reflected stale config/path state.
- Terminology drift created user-facing confusion.
  - FTP retained SMB terms ("shares") in a few places, leading to inconsistent interpretation of scan outcomes.
- Data semantics were initially inconsistent at rollup.
  - FTP completion output reported a hardcoded zero share metric while other fields implied non-zero meaningful results.

### Suggestions to Improve Future Modules (HTTP Docs)

1. Add a mandatory "Runtime Context Check" at task start:
- active checkout path
- effective backend path
- effective config path
- effective database path

2. Add a protocol terminology checklist before UI sign-off:
- labels
- summary phrases
- dialog text
- parser-facing output terms

3. Require "field semantics parity" checks for end-of-scan summaries:
- ensure all displayed totals are computed from the same data model intent
- avoid placeholder constants in user-visible rollups

4. Distinguish clearly between:
- code correctness
- wiring correctness
- environment correctness

5. Keep one "known local caveats" section in module docs:
- display-dependent tests
- ignored paths requiring force-add
- any expected local-only differences

### Final Takeaway (Instance Scope)

The biggest improvement opportunity from this instance is to treat environment/wiring validation as part of "definition of done," not a postscript. Once that became standard practice, issue resolution accelerated and user confidence increased.

---

## 9) Addendum: Refactor-to-Finalization Lessons (This Instance Only)

Date: 2026-03-19  
Scope: final polish phase in this instance, focused on aligning FTP behavior to SMB patterns and fixing runtime regressions discovered through manual VM testing.

### What Worked Well

#### Clear parity target reduced ambiguity
- Using SMB as the behavioral and layout reference for FTP dialog updates made implementation decisions faster and safer.
- "Lift existing SMB logic where practical" prevented unnecessary redesign and reduced regression risk.

#### Small-task sequencing kept momentum high
- Breaking work into small, testable tasks (dialog field parity, window sizing, probe listing behavior, render bug, migration fixes) kept feedback loops tight.
- Frequent manual validation between tasks quickly confirmed whether each change actually helped.

#### Strong user acceptance gate improved quality
- Requiring manual pass before commit kept risky assumptions out of history.
- Immediate human feedback caught issues that automated checks did not expose.

#### Surgical fixes succeeded when scoped tightly
- Probe output enhancement (showing subdirs + file/dir cap behavior) was implemented with minimal disruption to existing flow.
- Shared behavior updates across SMB and FTP improved consistency for researchers.

### What Did Not Work Well

#### Runtime-first bugs required multiple attempts
- The server list blank-render issue needed repeated fixes before the true runtime behavior was resolved.
- This indicates we validated implementation intent faster than actual UI draw lifecycle behavior.

#### Local success did not guarantee legacy compatibility
- Migration changes passed local expectations but failed in VM with older DBs (`no such table smb_servers`, then `no such table share_access`).
- Root cause: migration assumptions were too narrow for legacy schemas and were validated against modern local DB shape.

#### Missing full-schema compatibility gate
- Fixing one missing table at a time led to sequential runtime failures.
- A broader "legacy DB opens cleanly" check should have been part of first-pass migration validation.

#### Verification tooling friction slowed confidence
- In this environment, some normal test invocation output was not reliable, which reduced fast evidence gathering.
- When this happens, explicit fallback validation scripts should be used immediately and documented.

### Suggestions to Improve Future Work (HTTP Docs Module)

1. Add a mandatory parity checklist when mirroring an existing module:
- Fields present
- Layout parity
- Persistence parity
- Action routing parity
- Error handling parity

2. Add a "legacy artifact" test lane early:
- Open with current DB
- Open with pre-migration DB
- Verify dashboard load + primary list views + core actions

3. Require a startup schema self-check that validates all runtime-critical tables at once:
- Fail fast with one actionable message instead of serial "no such table" errors.

4. Use a two-environment gate before calling a fix complete:
- Local dev environment
- Clean VM with older real-world data

5. Keep commits scoped to one behavioral unit and always attach user-visible acceptance criteria in notes.

### Bottom Line for This Instance Addendum

This instance succeeded because the collaboration stayed iterative, direct, and outcome-focused. The main improvement opportunity is upfront legacy-runtime validation: not just "does the new path work," but "does the old world still load cleanly under the new code."

---

## 10) Full-Instance Collaboration Retrospective (This Session Only)

Date: 2026-03-19  
Scope: complete conversation arc for this Codex session, from kickoff, plan review loops, Claude implementation QA, repeated runtime gap closure, and final polish tasks.

### Positive Observations

- Triad workflow (you + Codex + Claude) worked best when roles were explicit.
  - You set priorities and acceptance criteria.
  - Claude produced implementation plans and patches.
  - Codex performed plan critique, QA, and integration sanity checks.

- SMB baseline as a reference standard reduced design churn.
  - "Match SMB behavior" gave a clear north star for output cadence, color semantics, dialog behavior, and viewer reuse.

- Fast, concrete feedback accelerated fixes.
  - Providing exact runtime snippets ("still seeing 90/100 ... 100/100") immediately narrowed defect scope and reduced speculation.

- Commit discipline improved traceability.
  - Frequent "yes, please commit" checkpoints created clean audit points and lowered risk of accidental mixed changes.

- Manual operator validation found critical issues automation missed.
  - Environment/path mismatches, legacy DB schema misses, and UI behavior gaps were caught by real usage, not just local tests.

### Negative Observations

- Environment drift repeatedly caused false negatives.
  - Active repo path changes (`/home/kevin/Documents/...` vs `/home/kevin/DEV/smbseek-smb`) and temporary venv path carryover caused confusion about whether fixes were actually live.

- "Plan complete" and "runtime complete" diverged multiple times.
  - Several items marked done in plan/report still failed in running UI until additional passes.

- Terminology consistency lagged behind feature completion.
  - FTP surfaces retained SMB language ("shares") after behavior was otherwise functional, creating credibility friction.

- Output-contract sensitivity was underestimated at first.
  - Parser-coupled lines and GUI expectations meant seemingly small output changes had outsized downstream effects.

- Local test success did not fully represent deployed behavior.
  - Headless/display differences and legacy database variants produced failures not seen in default local validation.

### Suggestions To Improve Next Module (HTTP Docs)

1. Add a required "execution context header" at the start of each task cycle:
- repo path
- python interpreter path
- backend/config/database paths in use

2. Enforce a two-gate completion rule:
- Gate A: tests and code review pass
- Gate B: manual runtime acceptance in the active operator environment

3. Keep a protocol vocabulary checklist and run it before every UX-facing commit:
- labels
- summary text
- dialog titles
- result metrics

4. Define parser/output contracts explicitly before refactors:
- which lines are machine-parsed
- which may be reformatted safely
- expected cadence (first/every N/last)

5. Add a lightweight "legacy compatibility smoke" run for each data-model change:
- open app with older DB snapshot
- verify primary list views load
- run one minimal scan path

6. Continue small, scoped commits with explicit acceptance notes.
- This was one of the strongest practices in this instance and should be preserved.

### Session-Level Takeaway

This instance showed that disciplined HI-in-the-loop feedback can reliably turn partially complete refactors into production-quality behavior, but only when runtime context validation is treated as equal to test success. The biggest single improvement for future work is to make environment and wiring checks mandatory at every handoff.
