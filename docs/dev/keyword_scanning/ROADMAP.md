# Sherlock Roadmap

## C0 - Contract Freeze

Freeze the scope, source anchors, runtime constraints, task cards, and review
loop. No runtime code.

Exit criteria:
- This planning workspace exists.
- Scope and non-goals are explicit.
- Claude prompts are ready for one-card-at-a-time execution.

## C1 - Matcher And Settings Model

Build pure matching and settings helpers, with no GUI and no DB writes.

Exit criteria:
- Pattern model and validation are deterministic.
- Snapshot-path adapters never read content, storage, network, or protocol
  clients from the matcher.
- Color and pattern validation tests pass.

## C2 - Persistence Contract

Add additive DB tables and DatabaseReader store helpers for latest Sherlock
summaries and capped hit rows.

Exit criteria:
- Migrations are idempotent and runtime guarded.
- Missing tables/columns degrade safely.
- Store tests cover stale snapshot and latest-result replacement.

## C3 - Accessories Sherlock Tab

Add the `Sherlock` tab for settings, colors, and pattern management.

Exit criteria:
- Pattern list is fixed-height and scrollable.
- Color hex inputs validate before save.
- Dialog opens at default size under Xvfb with visible controls.

## C4 - Server List Display And Standalone Scan

Add Risk column, row tinting, and `Scan Sherlock Selected` for existing hosts.

Exit criteria:
- Findings show high/med/low text and tint.
- Clear/no-hit/no-snapshot rows show blank Risk cells.
- Standalone scan skips missing snapshots with counts.
- The scan-and-persist helper is reusable by C5.

## C5 - Post-Probe Hook

Run Sherlock after successful probe snapshot persistence when enabled.

Exit criteria:
- Sherlock runs only after snapshots exist.
- It never triggers network work.
- Probe behavior and ransomware indicator semantics remain unchanged.

## C6 - Details And Web UI Read-Only Display

Expose Sherlock summaries and hit details in desktop details and Web UI results.

Exit criteria:
- Web UI is read-only for Sherlock.
- No auth, CSRF, or action surface is added.
- Web UI DB helpers stay below file-size guardrails via helper modules.

## C7 - Closeout, Visual QA, Docs

Run targeted validation, Xvfb dialog checks, README/technical docs sync, and
lessons learned update.

Exit criteria:
- Validation output is captured.
- README and technical reference match runtime behavior.
- Lessons learned record new guardrails and pitfalls.

Status: complete. Targeted matrix (220 Sherlock tests) passed. Full-suite
sanity found one pre-existing tkinter import-ordering artifact in
`test_daemon_cli.py`, not a Sherlock failure. Default-size Xvfb screenshots were
captured for the Accessories tab, Quick Scan row, Server List Risk column, and
detail popup. README, `AGENTS.md`, and `docs/TECHNICAL_REFERENCE.md` were synced;
`CLAUDE.md` was updated locally but remains gitignored. Sherlock V1 closed.
