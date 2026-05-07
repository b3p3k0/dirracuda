# Sidecar Bulk Import Phase 1

Date: 2026-05-07

Scope:
- Enable bulk import of selected hosts from sidecar browser windows.
- Targets: SearXNG results browser and Reddit Post DB browser.
- Keep single-row add/promote behavior unchanged.

Decision locks:
- Bulk path is direct-promotion only.
- No repeated legacy Add Record dialog loops for bulk.
- Best-effort import summary with imported/updated/skipped/failed totals.
