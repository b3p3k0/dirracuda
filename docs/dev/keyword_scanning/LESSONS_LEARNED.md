# Sherlock Lessons Learned

Seeded before implementation. Append after each major card.

## Carry Forward

1. Keep PA/RA and implementer roles separate. Planning agents write/review
   plans and prompts; implementation agents make code changes only after the
   relevant card is approved.
2. Snapshot-only must be treated as a security invariant, not a UI preference.
   Any content read, download, authentication change, or network probe belongs
   outside Sherlock V1.
3. The Risk column is alert-only. Blank means no visible Sherlock finding, not a
   textual `Clear` state.
4. Long analyst-maintained pattern lists must not resize dialogs. Use a
   fixed-height scrollable table and validate with Xvfb/default-size checks.
5. Near-limit files should receive thin wiring only. Put Sherlock logic in new
   helper modules instead of growing large Server List or Web UI modules.
6. Preserve legacy compatibility with additive schema changes and real runtime
   table/column checks.
7. DA prompts say "do not commit." The RA commits accepted cards locally as
   rollback checkpoints after QAQC and HI acceptance.
8. Accepted-card decisions must be written into the planning packet or next
   prompt before handoff. Do not rely on chat memory for C0 carry-forward.
9. Keep prompts sequential. Do not present the next card until the current card
   is accepted and committed.
10. SQLite schema work needs two layers of safety: migrations can be skipped or
   swallowed, so runtime helpers must guard every table/column they touch.
11. SQLite foreign keys are connection-local and often off here. Delete child
   rows explicitly and verify unique-index shape, not just index names.
12. Dialog visual QA must exercise the real host shell, including banners,
   notebook tabs, footer rows, and default geometry. A standalone tab frame can
   pass screenshots while the actual Accessories dialog still clips controls.
13. Server List Sherlock display is intentionally lossy and quiet: stale,
   malformed, zero-hit, no-snapshot, and unscanned states render as blank, while
   details/summaries carry the explanatory state.
14. Runtime-facing controls should not live only under Accessories. If a setting
   affects immediate scan/probe behavior, mirror it near that workflow while
   preserving the same underlying config shard.
15. Read-only display helpers still need full runtime schema guards, including
   columns used only for ordering or joins. Partial-table tests should cover
   those hidden SQL dependencies.
16. Web UI Sherlock values are untrusted display data. Render persisted labels,
   paths, and patterns through DOM text nodes / `textContent`, never dynamic
   HTML.

## C7 Closeout

17. Closeout needs its own validation matrix, not just a re-run of the latest
   card. Map every shipped surface (matcher, settings, persistence, Accessories
   tab, Server List Risk/scan, post-probe hook, Quick Scan toggle, desktop
   details, Web UI) to its test file and run them together plus a full-suite
   sanity pass.
18. Agent-facing technical references trail runtime silently. `CLAUDE.md`,
   `AGENTS.md`, and `docs/TECHNICAL_REFERENCE.md` had zero Sherlock mention after
   C1-C6 even though the user-facing README was current. Closeout must sweep the
   DB table lists, Accessories lists, and Server List sections in those files.
19. Visual QA must run isolated. Point `HOME` at a temp dir and seed a temp
   migrated DB (the `test_sherlock_persistence.py` pattern) so default-size
   screenshots never touch the real `~/.dirracuda` tree.
20. Headless screenshots need explicit window placement and filter state. Under
   xvfb there is no window manager, so large windows open off-origin; pin
   geometry to `+0+0`. The Server List `Show Only Shares >0` default also hides
   zero-share seeded hosts — disable it to render the Risk column row.
21. Full-suite ordering can mask or fake regressions. `test_daemon_modules_import_without_tkinter`
   (`experimental/webui/tests/test_daemon_cli.py`) fails only when a GUI test
   earlier in the run leaves `tkinter` in `sys.modules`; the daemon import chain
   itself stays clean. Verify suspected regressions in isolation before
   attributing them to the current card.

## C13 Closeout

22. Closeout triage needs two explicit buckets, not one pass/fail count. Run the
   full suite, then separate the known `test_daemon_cli` tkinter ordering
   artifact (re-confirm it passes in isolation) from any real V2 failure touching
   `shared/sherlock`, `database_access_sherlock_methods`, `sherlock_risk_display`,
   or the batch-summary path. Reporting "1 failed" without that split hides
   whether the feature is actually green.
23. Tint precedence must be QA'd with both states. The user-tag-then-severity
   contract only proves out when a screenshot shows a user-tagged row tinted with
   its User color *and* an untagged row falling back to the severity color. The
   C13 probe-summary shot (HIGH row in User1 blue, LOW row in severity yellow,
   no-finding row blank) is the canonical evidence shape.
24. Staged-edit modals split the QA surface. The pattern manager persists nothing
   until the main tab Save, so visual checks must confirm the modal renders the
   `User Tag` column and color-tag dropdown, while persistence tests separately
   confirm staged edits only land on Save. Screenshot ≠ persistence proof here.
25. Reuse prior cards' Xvfb harnesses verbatim where they exist. The C10 and C12
   QA scripts re-ran unchanged against committed V2 code; the only gotcha was a
   missing `sys.path`/`PYTHONPATH` to the repo root for the standalone probe
   harness. Don't rebuild screenshot scaffolding from scratch at closeout.
