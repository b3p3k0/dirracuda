# Data Organization Lessons Learned

Date: 2026-04-26
Scope: Home-first layout-v2 migration and path canonicalization.

## Guardrails To Carry Forward

1. Keep filesystem migration logic and runtime path resolution centralized in `shared/path_service.py`; avoid ad-hoc `Path.home() / ".dirracuda"` in feature modules.
2. Treat migration as fail-open: startup continues, fallback paths remain usable, and users get one explicit warning/report path.
3. Mark migration complete only when required canonical paths are present and read/write accessible.
4. Read-path fallback should be file-aware for caches/templates (directory existence alone is not enough once canonical dirs are pre-created).
5. Preserve CLI overrides (`--config`, `--database-path`) as highest precedence and do not downgrade them to canonical defaults.
6. Seed/copy behavior must be non-destructive: copy only missing conf assets and never overwrite user-edited files.
7. Legacy compatibility should remain explicit in module docs/tests to avoid regressions during future cleanup.
8. Keep migration reporting auditable (`state.json` + per-run reports + timestamped backups).
9. tmpfs quarantine runtime must stay detect-only (no `mount`/`umount` calls) so normal app operation never requires root privileges.
10. When docs are edited concurrently, re-verify README and technical reference against code paths (`shared/path_service.py`, `shared/tmpfs_quarantine.py`, `install.sh`) before considering the task done.
11. Successful layout-v2 marker alone is not enough for DB safety: if canonical DB is missing, always re-check legacy DB candidates and run targeted DB recovery instead of hard no-op.
12. Do not seed home config from checkout `conf/config.json` ahead of `config.json.example`; local repo edits can leak stale absolute paths into user runtime state.
13. Missing known legacy/repo-local persisted DB targets must be treated as stale and skipped so resolver can fall through to canonical detection.
14. Canonical and explicit custom DB paths remain strict even when missing; only known stale legacy/repo-local targets should be auto-corrected.
15. GUI helper dialogs should never default to `Path.cwd()/conf/config.json`; use canonical runtime config resolution to avoid repo-local drift.
16. Config validators must normalize filesystem paths (`expanduser`/`resolve` as appropriate) before existence checks; otherwise valid `~/.dirracuda/...` installs fail as false negatives.
17. Startup migration notifications must not classify canonical runtime targets (`~/.dirracuda/conf/config.json`, `~/.dirracuda/data/dirracuda.db`, `~/.dirracuda/state/gui_settings.json`) as fallback paths.
18. Discovery query paths should not manually paginate Shodan by default; use a single `search(..., limit=max_results)` request unless multi-call behavior is explicitly required and documented with its query-credit cost.
19. Avoid overlapping discovery-volume controls in the GUI; keep one authoritative knob (query budget) so estimate text and runtime fetch windows cannot diverge.

## Pitfalls Avoided

- Split-brain path writes (repo-local config/db vs home-canonical paths).
- Blocking startup on partial migrations.
- Silent fallback without visible operator signal.
- Unconditional coercion of custom user paths into defaults.
- Drift between docs and runtime behavior after concurrent/manual README saves.
