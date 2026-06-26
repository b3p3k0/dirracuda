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
7. Accepted-card decisions must be written into the planning packet or next
   prompt before handoff. Do not rely on chat memory for C0 carry-forward.
8. Keep prompts sequential. Do not present the next card until the current card
   is accepted and committed.
