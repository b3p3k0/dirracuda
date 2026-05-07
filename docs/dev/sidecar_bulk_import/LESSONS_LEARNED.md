# Lessons Learned

- Fix root cause first: sidecar windows were dispatching only `_selected_row()` for add-to-db actions.
- Preserve legacy compatibility by keeping single-row API paths untouched while adding opt-in bulk callback wiring.
- Keep DB/UI safety explicit: run bulk DB work in background, marshal progress updates onto Tk main thread, and refresh dashboard once per batch.
- Use best-effort summaries to prevent one bad row from blocking useful imports.
