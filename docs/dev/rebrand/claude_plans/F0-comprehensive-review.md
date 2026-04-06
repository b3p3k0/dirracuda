# Dirracuda Full Migration - Comprehensive Plan Review

**Date:** 2026-03-25  
**Status:** Planning mode (read-only analysis)  
**User Request:** Read and analyze all rebrand documentation + implementation files

---

## Summary of Deliverables Provided

### Documentation Artifacts (Planning)

1. **PRODUCT_SPEC.md** — Product intent and hard requirements
   - Full toolkit migration to Dirracuda now
   - No functional regressions
   - Existing users require zero manual steps
   - Backward compatibility bridges required

2. **TASK_CARDS.md** — Phased execution cards (F0-F6)
   - F0: Contract inventory (planning only)
   - F1: Compatibility bridge layer
   - F2: Canonical UI + entry point switch
   - F3: User data path migration
   - F4: DB filename migration
   - F5: Metadata label transition
   - F6: Full regression + rollback drill

3. **NAMING_CONTRACT_MATRIX.md** — Detailed contract inventory
   - 12 categories of naming contracts (launchers, configs, paths, DB, env vars, classes, UI text, GitHub URLs, tests)
   - KEEP / REPLACE / ALIAS / REVIEW tags
   - 8 locked decisions (Q1-Q8) from human input

4. **F0_MIGRATION_BLUEPRINT.md** — Implementation guide
   - Phased cutover sequence (F1→F2→F3→F4→F5→F6)
   - Detailed scope for each phase
   - Compatibility bridge design patterns
   - Phase dependencies and ordering
   - Parser-critical output strings (Appendix)

5. **F0_RISK_REGISTER.md** — Risk assessment + validation
   - 16 identified risks (HIGH/MEDIUM/LOW)
   - 8 high-priority failure modes from FTP module lessons
   - Rollback strategy for each phase
   - Validation matrix (automated + manual HI gates)
   - Known baseline (pre-migration state as of 2026-03-24)

### Implementation Files (Current State)

**Entry Point & Config:**
- `xsmbseek` — Main GUI launcher (~875 lines)
- `conf/config.json.example` — Configuration template
- `dirracuda` — New canonical launcher (planned, will be created in F2)

**Settings & Logging:**
- `gui/utils/settings_manager.py` — Settings management (941 lines)
  - Currently uses `~/.smbseek/` hardcoded
  - DB path detection logic at line 528
  - Methods: `get_smbseek_path()`, `set_smbseek_paths()` (need aliasing in F1)
  
- `gui/utils/logging_config.py` — Logging setup (71 lines)
  - `GUI_LOGGER_NAME = "smbseek_gui"` (needs F2 rename to "dirracuda_gui")
  - Checks only `XSMBSEEK_DEBUG_*` env vars (needs dual-check in F1)

**Backend Interface (Process Wrapper):**
- `gui/utils/backend_interface/interface.py` — Main interface (~875 lines)
  - Subprocess wrapper for CLI execution
  - DB path default: `../backend/smbseek.db` (line 648, needs detection logic in F4)
  - Environment variable checks: only `XSMBSEEK_DEBUG_*` (line 141, 154, 168)

- `gui/utils/backend_interface/progress.py` — Output parsing (~631 lines)
  - **Parser-critical string:** `"SMBSeek security assessment completed successfully"` (line 548)
  - Matches: `"✓ Found" + "accessible SMB servers"` (two-part string)
  - Matches: `"FTP scan completed successfully"`, `"HTTP scan completed successfully"`
  - Output parsing is tightly coupled to progress extraction

- `gui/utils/backend_interface/process_runner.py` — Process execution (~338 lines)
  - Subprocess creation and termination
  - Calls `progress.parse_output_stream()` and `progress.parse_final_results()`
  - Handles timeouts and cancellation

---

## Migration State Analysis

### Pre-F1 Baseline (2026-03-24)

**Completed (from 4d9dca1 rebrand commit):**
- GUI text: ~80% updated (titles, headers)
- Some internal references changed
- Dialog text: partially updated

**NOT Yet Changed:**
- Launcher scripts (`xsmbseek` primary, `dirracuda` doesn't exist yet)
- Config key names (`smbseek_path` still primary)
- User data paths (all `~/.smbseek/*`)
- DB default filename (`smbseek.db` everywhere)
- Logger name (`smbseek_gui`)
- Env var checks (only `XSMBSEEK_*`)
- Method names (`set_smbseek_paths()`, `get_smbseek_path()`)
- Class names (SMBSeekConfig, XSMBSeekGUI, etc.)
- Parser output strings (unchanged, still reference "SMBSeek")
- Tool CLI defaults (db_manager.py, etc.)

---

## Critical Implementation Points

### F1 Priority (Compatibility Bridge Layer)

**Must happen first; nothing breaks after F1:**

1. **Env var dual-checking** (4 files)
   - `gui/utils/logging_config.py:34` — Add `DIRRACUDA_DEBUG_*` checks
   - `gui/utils/backend_interface/interface.py:141,154,168` — Add checks
   - `gui/utils/backend_interface/progress.py` — Add checks
   - `gui/utils/backend_interface/process_runner.py` — Add checks

2. **CLI flag aliasing** (xsmbseek argparse)
   - Add `--backend-path` as canonical, keep `--smbseek-path` working

3. **Config key aliasing** (xsmbseek + settings_manager.py)
   - `get_smbseek_path()` — read from `backend_path` OR `smbseek_path` (prefer new)
   - `set_smbseek_path()` — write `backend_path`, remove `smbseek_path` to avoid split-brain
   - `conf/config.json.example` — add `backend_path` comment, mark `smbseek_path` deprecated

4. **Settings manager method alias** (settings_manager.py)
   - Add `set_backend_paths()` as canonical wrapper
   - Keep `set_smbseek_paths()` as thin wrapper calling new method

5. **DB detection aliasing** (settings_manager.py:528)
   - Look for `dirracuda.db` first, fall back to `smbseek.db`

### F2 Priority (UI Text + Launcher)

**Only user-facing text changes; legacy flags still work:**

1. **Create `dirracuda` launcher** (new file, shell wrapper)
   ```bash
   #!/bin/bash
   exec "$(dirname "$0")/xsmbseek" "$@"
   ```

2. **Setup dialog text cleanup** (xsmbseek)
   - All "SMBSeek" → "Dirracuda" in user-visible strings

3. **Version string** (xsmbseek:982)
   - `version='dirracuda 1.0.0'`

4. **Logger name switch** (logging_config.py:15)
   - `GUI_LOGGER_NAME = "dirracuda_gui"`

5. **GitHub URLs**
   - xsmbseek:98, conf/config.json.example:169 → dirracuda repo

**Parser safety:** Logger name change is internal only; progress.py does NOT parse logger output.

**WARNING:** Do NOT change `progress.py:548` string in F2 — it needs paired parser update in separate commit.

### F3 Priority (User Data Paths)

**New file + atomic path updates:**

1. **New file: `shared/path_migration.py`**
   - `migrate_user_data_root()` function
   - Copy `~/.smbseek` → `~/.dirracuda` on first startup
   - Write marker file to prevent re-copy
   - Leave `~/.smbseek` intact

2. **Fallback read policy**
   - probe_cache.py, ftp_probe_cache.py, http_probe_cache.py
   - If item not in `~/.dirracuda/`, read from `~/.smbseek/`
   - Always write to `~/.dirracuda/`

3. **Path constants (11 files)**
   - All `~/.smbseek` → `~/.dirracuda`
   - Atomic update in F3 card

### F4 Priority (DB Filename)

**Detection + new defaults:**

1. **DB resolution priority** (settings_manager.py, interface.py)
   ```
   1. --database-path CLI arg
   2. gui_settings.json last_database_path
   3. conf/config.json database.path
   4. Auto-detect: dirracuda.db → smbseek.db
   5. Default: dirracuda.db
   ```

2. **Config defaults change**
   - shared/config.py:199 — `"smbseek.db"` → `"dirracuda.db"`
   - All tool defaults (db_manager.py, etc.)
   - conf/config.json.example:47

3. **Dynamic path in output** (shared/output.py:131)
   - Replace hardcoded `"smbseek.db"` with `config.get_database_path()`

4. **Mock defaults** (mock_operations.py:41)
   - `'../backend/dirracuda.db'`

### F5 Priority (Metadata Labels)

**Requires HI sign-off on Q1-Q8; last data change before F6:**

1. **GUI session tool_name** (batch_status.py:339)
   - `'xsmbseek'` → `'dirracuda'`

2. **DB migration defaults** (db_migrations.py)
   - Default for new DB creates: `tool_name DEFAULT 'dirracuda'`
   - Backfill NULL rows: keep `'smbseek'` (don't misattribute)
   - `scan_type`: keep `'smbseek_unified'` (per Q1 lock)

3. **Import tool_name** (db_tools_engine.py:1492)
   - `'smbseek'` → `'db_import'` (per Q3 lock)

4. **Query compatibility**
   - All stats queries use IN-list matching: `WHERE tool_name IN ('smbseek', 'dirracuda')`

---

## High-Risk Items from Lessons (FTP Module)

### From `docs/dev/ftp_module/LESSONS.md` & `http_module/PROJECT_GUIDELINES.md`

**F3-H1: Missing full-schema compatibility gate**
- Before F3/F4 mark done: open app with pre-migration DB, verify no crashes
- All primary list views must load
- No "no such table" errors

**F4-H1: DB detection logic mismatch**
- Both `settings_manager.py:528` AND `interface.py:648` must be updated in same commit
- Verify via `XSMBSEEK_DEBUG_SUBPROCESS=1` which DB subprocess opens

**F2-H1: Parser-coupled output text**
- String `"SMBSeek security assessment completed successfully"` at `progress.py:548`
- **DO NOT CHANGE** in F2 without paired parser update
- This is separate from other text changes

**Completion semantics:**
- AUTOMATED: tests pass, code reviewed
- MANUAL: human verifies exact UX in running app
- OVERALL: both gates closed

---

## Locked Decisions (Q1-Q8)

From NAMING_CONTRACT_MATRIX.md section 12:

| # | Question | Locked Answer |
|---|----------|---------------|
| Q1 | `scan_type='smbseek_unified'` rename? | **Keep as-is** (internal, no benefit) |
| Q2 | NULL-row backfill: change from `'smbseek'`? | **Keep `'smbseek'`** (legacy attribution) |
| Q3 | DB import `tool_name` after F5? | **`'db_import'`** (protocol-neutral) |
| Q4 | `set_smbseek_paths()` rename scope? | **F1**: add `set_backend_paths()`, keep wrapper |
| Q5 | `~/.smbseek` retention after F3? | **Never auto-delete** (user decides) |
| Q6 | Config key deprecation notice? | **Silent migrate + one-time log warning** |
| Q7 | Class name rename card? | **Dedicated cleanup card after F5** |
| Q8 | Report filename alias? | **ALIAS during transition** (symlink/copy) |

---

## Testing & Validation Strategy

### Automated Gates (per phase)

1. **Syntax checks:** `python3 -m py_compile <files>`
2. **Test suite:** `xvfb-run -a ./venv/bin/python -m pytest gui/tests/ shared/tests/ -q`
3. **Naming inventory:** `rg -n -i "\b(dirracuda|smbseek|xsmbseek)\b" xsmbseek gui shared cli tools conf`
4. **Config smoke:** `python3 -c "from shared.config import SMBSeekConfig; c = SMBSeekConfig(); print(c.get_database_path())"`

### Manual HI Gates (per phase)

| Gate | Phase | Test |
|------|-------|------|
| HI-F1-A | F1 | `./xsmbseek --mock` with both `--smbseek-path` and `--backend-path` |
| HI-F1-B | F1 | `DIRRACUDA_DEBUG_SUBPROCESS=1` shows debug output |
| HI-F2-A | F2 | `./dirracuda --mock` title shows "Dirracuda" |
| HI-F2-B | F2 | Setup dialog shows no "SMBSeek" text |
| HI-F3-A | F3 | `~/.smbseek` present → `~/.dirracuda` created, `~/.smbseek` intact |
| HI-F3-B | F3 | Templates/probes accessible after migration |
| HI-F3-C | F3 | Pre-migration DB opens without errors |
| HI-F4-A | F4 | New install creates `dirracuda.db` |
| HI-F4-B | F4 | Existing `smbseek.db` detected and used |
| HI-F4-C | F4 | SMB scan end-to-end works |
| HI-F5-A | F5 | GUI scan: new row has `tool_name='dirracuda'` |
| HI-F5-B | F5 | Legacy rows still visible in DB tools |
| HI-F5-C | F5 | DB tools import/merge works |
| HI-F6-A | F6 | Rollback test: revert F4, `smbseek.db` opens |

---

## Rollback Strategy

### Per-Phase Rollback

| Phase | Rollback | Data Risk |
|-------|----------|-----------|
| F1 | `git revert <hash>` | None; no data change |
| F2 | `git revert <hash>` + `rm -f dirracuda` | None; UI text reverts |
| F3 | `git revert <hash>` | `~/.dirracuda` orphaned; `~/.smbseek` intact |
| F4 | `git revert <hash>` + restore config | User must point back to `smbseek.db` |
| F5 | `git revert <hash>` | Rows with `tool_name='dirracuda'` orphaned |

**Safety checks before reverting:**
- `sqlite3 smbseek.db "PRAGMA integrity_check;"`
- `ls -la ~/.smbseek ~/.dirracuda`
- `git log --oneline -10`
- `git stash` any local changes

---

## Files Requiring Changes (Per Phase)

### F1 (Compatibility Bridge)
- xsmbseek
- gui/utils/logging_config.py
- gui/utils/backend_interface/interface.py
- gui/utils/backend_interface/progress.py
- gui/utils/backend_interface/process_runner.py
- gui/utils/settings_manager.py
- conf/config.json.example

### F2 (UI Text + Launcher)
- xsmbseek
- dirracuda (new file)
- gui/components/scan_results_dialog.py
- gui/utils/logging_config.py
- conf/config.json.example

### F3 (User Data Paths)
- shared/path_migration.py (new file)
- gui/utils/probe_cache.py
- gui/utils/ftp_probe_cache.py
- gui/utils/http_probe_cache.py
- gui/utils/extract_runner.py
- gui/utils/template_store.py
- gui/utils/settings_manager.py
- gui/components/server_list_window/window.py
- gui/components/server_list_window/details.py
- gui/components/server_list_window/actions/batch.py
- shared/quarantine.py
- shared/db_migrations.py
- shared/config.py
- gui/components/app_config_dialog.py
- conf/config.json.example

### F4 (DB Filename)
- shared/config.py
- xsmbseek
- gui/utils/settings_manager.py
- gui/utils/database_access.py
- gui/utils/backend_interface/interface.py
- gui/utils/backend_interface/mock_operations.py
- shared/output.py
- tools/db_manager.py
- tools/db_maintenance.py
- tools/db_query.py
- tools/add_share_summary_view.py
- tools/add_share_summary_view.sql
- gui/components/app_config_dialog.py
- conf/config.json.example

### F5 (Metadata Labels)
- gui/components/server_list_window/actions/batch_status.py
- shared/db_migrations.py
- shared/database.py
- gui/utils/db_tools_engine.py
- tools/db_manager.py

### F6 (Validation + Rollback)
- docs/dev/rebrand/F6_VALIDATION_REPORT.md (new)
- docs/dev/rebrand/F6_ROLLBACK_RUNBOOK.md (new)

---

## Known Caveats & Edge Cases

1. **Parser-output sensitivity:** Line 548 of `progress.py` is checked during normal operation by real backend subprocess. Changing it breaks progress tracking unless parser is updated too.

2. **Hidden gitignore:** Files under `docs/dev/` and `gui/tests/` may be gitignored. Use `git add -f` if changes don't show in `git status`.

3. **Legacy DB assumptions:** Migration logic must work with pre-migration DB schema; cannot assume all tables exist.

4. **Settings persistence:** Both in-session reopen AND full app restart must work; settings_manager is a singleton.

5. **Environment drift:** GUI must use effective backend path (cwd checkout preference over stale settings).

---

## Next Steps (For Execution)

This document is a planning artifact. When ready to execute:

1. **Start with F1** — Get compatibility layer in place; zero user-visible changes
2. **Validate F1** — All automated + HI gates must pass
3. **Proceed to F2** — Add canonical UI text and launcher
4. **Proceed to F3** — Migrate user data paths
5. **Proceed to F4** — Switch DB default filename
6. **Proceed to F5** — Update metadata labels
7. **Proceed to F6** — Full regression drill and rollback validation

Each phase should be:
- Small and reviewable (one behavioral unit)
- Followed by explicit gate validation
- Committed with clear message including phase number and gate status

---

**Total analysis tokens used: ~90k of 200k budget**  
**Status: Planning complete. Ready for execution on user's signal.**
