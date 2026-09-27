# Lessons from desktop entrypoint cleanup

- Promotion needs an explicit retirement step. Leaving old tabs registered
  preserves duplicate workers, busy flags, settings expectations, and tests
  after the main flow is established.
- Trace launchers separately from storage and history. Removing scan tabs must
  not remove shared services, main-DB sync, or legacy-data migration browsers.
- Desktop's single-entry rule does not apply to Web UI routes. HI confirmed
  Web UI is a later phase; keep that scope decision in the handoff.
- Rewrite operator instructions from the retained UI. Start New Scan has no
  standalone Test/Open Results DB buttons, and its search query defaults to
  blank. New results belong in the main database and Server List.
- Prune tests by behavior, not by nearby names. `_StatusWidget` looked like a
  retired Reddit test helper but was also used by Web UI tab tests. Focused
  validation caught the accidental removal; the shared helper was restored.
- Prefer relative-order assertions when a test concerns two surviving tabs.
  One exact registry-order test can cover the complete Accessories inventory.
- Record test-size debt without turning a scoped removal into a broad rewrite.
  HI deferred test modularization; all touched production files stay below
  1,700 lines. Keep old settings/config names for compatibility.
