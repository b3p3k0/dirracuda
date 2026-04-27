# Scan Dialog Lessons Learned

Date: 2026-04-20

## Guardrails

1. Do not use app-wide `grab_set()` for scan launch windows when cross-window workflows (like Dorkbook copy/paste) are required.
2. Keep scan launch windows single-instance per dialog type; repeated opens should focus existing windows.
3. Preserve existing return contracts (`"start" | "cancel" | None`) when adding singleton guards.
4. Treat child prompts (messageboxes/simpledialogs) as acceptable temporary modal interactions; avoid broad window locks.
5. Add entrypoint-level regression tests for re-entry behavior (`existing -> focus`, `new -> create`, `close -> clear singleton`).
6. Keep `UnifiedScanDialog._open_query_editor` as the only routing seam for future replacement of `Edit Queries` with `Open Dorkbook`.
7. Keep discovery-dork config paths/defaults/validation in one shared helper so App Config and scan-time editors cannot drift.
8. Route all Dorkbook-to-scan population through one explicit scan-editor API (`populate_discovery_dork_from_dorkbook`) to avoid split logic.
9. Keep Dorkbook population manual-save only; never auto-write config on row use actions.
10. In shared batch operations, never assume SMB-only behavior; branch explicitly by host type (`S`/`F`/`H`) for extract/probe state writes and runtime routing.
11. Preserve bulk-extract dependency gating in both unified and legacy scan dialogs: `Skip extract on hosts with malware indicators` must be disabled unless bulk extract is enabled, and state sync must run on initial render and template/form-state apply paths.
12. Keep probe depth controls centralized via one persisted setting key (`probe.max_depth_levels`) and clamp at load/save boundaries (`1..3`) so preflight, server-list batch probe, and per-host probe dialogs stay in sync.
