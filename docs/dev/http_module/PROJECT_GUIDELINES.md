# Project Guidelines: HTTP Module

Date: 2026-03-19
Source: Consolidated from `docs/dev/ftp_module/LESSONS.md` (10 sections, multi-instance retrospective)

Read this before starting any HTTP module task. It encodes what worked and what failed during FTP module development so you don't repeat the same mistakes.

Full retrospective: `docs/dev/ftp_module/LESSONS.md`

---

## What Worked

- **Tight review loops** — Short plan → critique → correction cycles outperformed large rewrites. PASS/FAIL gates reduced ambiguity.
- **SMB as baseline reference** — Using SMB behavior as the acceptance target for FTP reduced design debate and made criteria concrete. Apply the same for HTTP.
- **Incremental hardening** — Ship MVP behavior first, then stabilize with tests and docs. Don't polish before the feature works end-to-end.
- **Narrow, user-visible acceptance checks** — Validating concrete UX outcomes ("button visible", "row appears in list") found gaps faster than internal-only checks.
- **Incremental, scoped commits** — One behavioral unit per commit with explicit acceptance notes in the message. Frequent meaningful checkpoints improved traceability and made rollback safe.
- **Verification over narrative** — Requiring actual commands, outputs, and schema checks prevented hand-wavy completion claims.
- **Handoff artifacts** — Explicit plan files, status summaries, and commit notes reduced context loss across instances.
- **Fast pivot from plan to runtime** — When plan confidence and runtime behavior diverged, shifting quickly to direct behavior tracing was the right call.
- **Small-task sequencing** — Breaking work into small, testable tasks kept feedback loops tight and momentum high.
- **Surgical, tightly-scoped fixes** — Narrow fixes with minimal disruption to surrounding flow reduced regression risk.

---

## What Failed

- **"Plan complete" ≠ "runtime complete"** — The biggest recurring failure. Items marked done in plans/reports still failed in running UI. Do not conflate the two.
- **QA on artifacts, not end-to-end behavior** — Tests and code reviews passed while critical runtime paths were still broken. Internal checks are insufficient alone.
- **Integration boundary mismatch** — Code landed in one path; the active runtime path still used legacy behavior. "Implemented" features weren't always exercised by real user flow.
- **State drift in plan documents** — Plans carried stale statements (commit status, schema assumptions already resolved). Every revision needs a fresh state check.
- **Command-level inaccuracies** — Verification commands with wrong schema fields or wrong expected output shapes break confidence in the test plan. Verify commands against actual code.
- **Over-planning before execution** — Multiple plan refinement rounds had diminishing returns. Set a cap on iterations, then move to implementation + validation.
- **Environment/path drift** — Active repo path or venv path carryover caused "did the fix land?" confusion. Always confirm the active checkout at task start.
- **Legacy DB assumptions too narrow** — Migration fixes validated against modern local DB shape failed on older schemas in the VM. Fixing one missing table at a time caused sequential failures.
- **Terminology drift** — FTP surfaces retained SMB labels ("shares") after behavior was correct, creating credibility friction with operators.
- **Hidden gitignore behavior** — Files under `docs/dev/` and `gui/tests/` were gitignored. Changes "disappeared" from `git status`. Use `git add -f` for these paths and document it.

---

## Completion Semantics

Never use "complete" unless all required gates are closed. Use explicit labels:

```
AUTOMATED: PASS | FAIL
MANUAL:    PASS | FAIL | PENDING
OVERALL:   PASS | FAIL | PENDING
```

- **Automated complete** = tests pass, code reviewed
- **Manual validation pending** = automated done, runtime not yet verified by human
- **Fully complete** = both gates closed, commit made, human confirmed

---

## Checklist: Task Start

Before writing any code, confirm execution context:

- [ ] Active repo path (confirm you're in `/home/kevin/DEV/smbseek-smb`)
- [ ] Python interpreter path (venv active?)
- [ ] Config path in use (`conf/config.json` vs. override)
- [ ] Database path in use
- [ ] `git log -n 5` and `git status --short` — confirm working state
- [ ] Baseline test run result + known expected failures

---

## Checklist: Before Commit

- [ ] Verify all DB column/table names against `tools/db_schema.sql`
- [ ] Verify file paths referenced in the plan actually exist
- [ ] Run protocol terminology check (see Terminology section below)
- [ ] Confirm field semantics: all user-visible totals computed from correct data model
- [ ] Stage specific files only — avoid `git add -A` (risks committing secrets/binaries)
- [ ] Sequential commit flow: stage → diff → commit → verify log (no parallel git ops)

---

## Checklist: Module Parity (when mirroring SMB → HTTP)

When adding HTTP behavior that parallels SMB, verify parity across:

- [ ] Fields present (dialog inputs, result columns)
- [ ] Layout (window sizing, widget placement)
- [ ] Persistence (in-session reopen AND full app restart)
- [ ] Action routing (scan → worker → DB write → UI readback path)
- [ ] Error handling (what shows when connection fails, times out, etc.)
- [ ] Protocol-specific terminology (no SMB labels on HTTP surfaces)

---

## Runtime Gates

Two gates are required before marking anything done:

**Gate A — Automated:** tests pass, code reviewed, validation commands confirmed correct
**Gate B — Manual:** human operator verifies the exact UX behavior in the active environment

Gate B is non-negotiable. Most issues in the FTP module were caught only by Gate B. Automated passes without Gate B is "automated complete, manual validation pending" — not done.

For each feature, document the runtime call path exercised:

```
entrypoint → worker → DB write → UI readback
```

This catches "implemented but not wired" issues early.

---

## Legacy Compatibility

Every data-model change needs a legacy smoke test before calling it done:

1. Open app with current DB — verify dashboard loads
2. Open app with a pre-migration DB snapshot — verify primary list views load and core actions work
3. Verify startup schema self-check catches all missing tables at once (fail fast with one message, not serial "no such table" errors)

Do not validate only against the modern local DB shape. The VM may have a DB from months ago.

Persistence must also be validated at two levels:
- In-session reopen
- Full app restart

Both must pass before marking persistence done.

---

## Collaboration Norms

**Roles:**
- Human sets priorities, acceptance criteria, and performs manual Gate B validation
- AI owns implementation, plan revisions, debugging, and documentation

**Feedback style:** Concrete beats comprehensive. "Still broken — output shows X, expected Y" is more useful than a detailed theory. Provide exact runtime snippets when reporting failures.

**Commit reporting:** Always include the commit hash and message in completion replies. This materially reduces context loss when switching between tasks or instances.

**Scope discipline:** Address low-severity sharp edges immediately when found — deferring them creates recurring churn. Keep fixes scoped to one behavioral unit per commit.

**When unsure:** Pause and ask. Conflicting instructions or risky operations require clarification, not guessing.

---

## Terminology Checklist

Run before every UX-facing commit. Confirm HTTP-appropriate language is used consistently across:

- Dialog titles and labels
- Server list column headers
- Summary/rollup phrases (e.g., "endpoints found", not "shares found")
- Status messages and error text
- Parser-facing output terms (these are machine-read — changes have downstream effects)

Define parser/output contracts before refactors: which lines are machine-parsed, which may be reformatted safely, expected cadence.

---

## Known Local Caveats

- `docs/dev/` and `gui/tests/` paths may be gitignored — use `git add -f` if changes disappear from `git status`
- GUI tests require a display; use `xvfb-run -a` for headless runs
- `XSMBSEEK_DEBUG_SUBPROCESS=1` and `XSMBSEEK_DEBUG_PARSING=1` env vars enable subprocess/parsing debug logs
