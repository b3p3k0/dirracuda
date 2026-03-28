# ClamAV Integration Decisions (Locked)

Date: 2026-03-27

## Locked

1. Scope:
- Phase 1 is bulk extract only.
- Included bulk callers:
  - dashboard post-scan bulk extract
  - server-list batch extract
- Long-term architecture should remain reusable for browser-download integration.

2. Failure policy:
- Optional feature defaults fail-open (`fail_open=true`).

3. Promotion policy:
- Clean files are moved to extracted root.

4. Infected handling:
- Infected files move to quarantine known-bad subtree.
- Default known-bad folder name: `known_bad`.

5. Scanner-error handling:
- Scanner-error files remain in original quarantine location.

6. GUI controls to expose in phase 1:
- enable scan checkbox
- backend selector
- timeout seconds
- clean destination path
- known-bad subfolder name
- show-results dialog toggle

## No Blocking Questions

All phase-1 behavior decisions required for implementation are locked.

