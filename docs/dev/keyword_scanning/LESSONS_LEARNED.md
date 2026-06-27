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
