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

## C20 Closeout

26. Run GUI test suites under `xvfb-run -a`, not a bare interpreter. The
   `tk_root` fixture in `test_sherlock_tab.py` skips with "no display available"
   when there is no X server, so a headless `pytest` silently turns the real-Tk
   coverage (category combobox, filter-row widgets, facet refresh, under-active-
   filter dialogs, button-order layout) into skips. Closeout evidence must show
   those tests as passed, not skipped — confirm the count, don't trust a green
   summary that quietly skipped the display-dependent cases.

27. A doc no-op is a valid, reportable closeout outcome. README,
   `docs/TECHNICAL_REFERENCE.md`, and `AGENTS.md` were updated incrementally
   during C15-C19, so the C20 sweep found them already accurate and left them
   unchanged. State "reviewed, no change needed" explicitly rather than inventing
   edits to look productive — but still re-read them, because the C7 lesson (docs
   trail runtime silently) cuts both ways.

28. Scope the dead-code scan to the card's own changes and attribute findings by
   age. The C20 unused-import scan flagged `severity_from_str` / `severity_to_str`
   in `sherlock_tab.py`; `git log -S` showed they were added in the C3 commit
   (6baed90), never used since, and are not C15-C19 work. Reporting them as a
   pre-existing finding and leaving them out of a docs-only closeout is correct;
   removing them would be an out-of-scope runtime edit. (No linter is installed
   here, so a small `ast`-based scan plus `py_compile` is the closeout tool.)

29. Near-limit files need a measured number and an explicit trigger, not just a
   "watch this" note. `sherlock_tab.py` sits at 1188 lines, 12 under the 1200
   guardrail (R23). The actionable carry-forward is concrete: the next card
   touching the pattern manager must extract a helper module before adding logic
   that would cross 1200 — recorded in ROADMAP C20 status and RISK_REGISTER R23
   so it cannot be lost to chat memory.

## C21-C26 (Two-Pane Pattern Manager)

30. Extract before you add, and make it its own card. C21 moved the pattern
   manager into a satellite module with zero behavior change *before* C22-C25
   added the two-pane layout, category actions, and grouped editing. The
   extraction card was the reason later cards had room to grow; the C20
   carry-forward trigger (R23) paid off exactly once it was honored as step one.

31. Keep the UI grouping separate from the persistence shape. Grouped value rows
   are a display projection (`shared/sherlock/grouping.py`) over one
   `SherlockPattern` per pattern string. Never let a "group" become a stored
   entity — every group action maps back to member rows, so matcher, settings,
   and export formats stayed untouched while the UI reorganized completely.

32. A comma-split input needs its unsupported case written down, not just
   handled. `split_pattern_input` splits on literal commas, which means a pattern
   that must contain a comma cannot be entered. That limitation (R26) lives in
   README and TECHNICAL_REFERENCE, not only in a test — a silent splitter looks
   like a bug to the next analyst who needs a literal comma.

33. UI-only placeholders must be inert until committed. The "Add category"
   placeholder shows in the left pane but never enters `_patterns` or flips the
   dirty flag until a real value is saved into it, and it resets on every manager
   open. Treat transient UI scaffolding as non-persistent by construction so it
   can't leak into settings, export, or the dirty state.

34. Skip, don't fake, actions that can't persist. Tag Apply is custom-only
   because built-in `color_tag` is never stored; applying a tag to a built-in
   would look like it worked and silently vanish on save. Skipping built-ins in
   the bulk action is the honest behavior, and it belongs in the docs so it reads
   as intentional rather than a missed row.

35. Use `dataclasses.replace` for "same record, one field changed." The C25.5 bug
   was a `dataclass_disabled` that rebuilt a partial `SherlockPattern`, dropping
   category/label/severity/color_tag when a built-in was disabled.
   `dataclasses.replace(pattern, enabled=False)` carries every current and future
   field through unchanged — the correct pattern for any "flip one flag" copy.

36. A docs-only closeout is the right home for cleanup deferred from an earlier
   docs-only closeout. C20 correctly left the dead `severity_from_str/to_str`
   import (out of scope for a docs card). C26 — also a closeout, but one where the
   scope explicitly permitted behavior-neutral cleanup — was the place to remove
   it, plus the unused `filedialog` import, migrating its 6 test patch sites to
   the module that actually calls it (`sherlock_value_actions`) rather than
   removing an import that tests relied on via module-object identity. Verify the
   patch seam before deleting an "unused" import.
