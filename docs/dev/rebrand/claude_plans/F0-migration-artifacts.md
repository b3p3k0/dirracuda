# Plan: F0 Migration Artifacts — Dirracuda Full Migration

## Context

Dirracuda (formerly SMBSeek) is undergoing a full rebrand migration. The task is to produce three planning artifacts that map all naming contracts, sequence the migration phases, and document risks/rollbacks — before any code changes are made. These documents become the authoritative blueprint for F1–F6 implementation cards.

All three target files already exist as drafts (authored in a prior session). Research against branch `development` (commit `4d9dca1`) confirms the drafts are ~90% accurate but have specific gaps and errors that must be corrected before these can serve as the canonical F0 artifacts.

---

## Scope

**Target files (create/update):**
1. `docs/dev/rebrand/NAMING_CONTRACT_MATRIX.md`
2. `docs/dev/rebrand/F0_MIGRATION_BLUEPRINT.md`
3. `docs/dev/rebrand/F0_RISK_REGISTER.md`

**No runtime code changes.** Documentation artifacts only.

---

## Gap Analysis (confirmed against current code state)

### NAMING_CONTRACT_MATRIX.md — Gaps

**Section 8 errors (Internal Python Identifiers):**

1. `SMBSeekGUI class (if exists)` — **wrong class name and wrong conditional**. The active class is `XSMBSeekGUI` at `xsmbseek:397`. It definitely exists. Entry must be corrected.

2. KEEP rationale for class names cites "CLAUDE.md explicitly flags KEEP" — **this is incorrect**. CLAUDE.md actually says *"these need to be renamed"* (verbatim). These should be tagged `REVIEW` with a note that they are internal refactors deferred to a cleanup card after F5, with no external contract risk.

**Missing entries in Section 8 (confirmed present in repo):**
- `SMBSeekWorkflowDatabase` — `shared/database.py:23` (major class, used across CLI/GUI)
- `SMBSeekDataAccessLayer` — `tools/db_manager.py:239`
- `SMBSeekDatabaseMaintenance` — `tools/db_maintenance.py:21`
- `SMBSeekDataImporter` — `tools/db_import.py:20`
- `SMBSeekTheme` — `gui/utils/style.py:17`
- `SMBSeekGUI` — `gui/main.py:45` (deprecated entry point, actual legacy class)

**Missing entry in Section 5 (DB filenames):**
- `tools/db_import.py:33` — `smbseek.db` default in `SMBSeekDataImporter.__init__` — should be `ALIAS`

**Missing entry in Section 9 (User-facing text):**
- `shared/output.py:420` — `smbseek_report_<timestamp>.json` filename pattern — should be `REPLACE`
- `shared/db_migrations.py:508` — path to legacy `gui_settings.json` used in migration helper — should be `ALIAS` (already in Section 4 but not cross-referenced here)

**Section 6 (DB metadata) — phantom file reference (critical error):**
- Entry for "Query in db_commands matching | `WHERE tool_name='smbseek'` (operation.py:43)" — **`tools/db_commands/` directory does not exist**. Confirmed: `rg "WHERE tool_name" tools/ shared/` finds no such filter query anywhere. The concern is real but the file reference is fabricated.
- Actual concern: `v_scan_statistics` view in `tools/db_schema.sql` `GROUP BY tool_name` — will split counts between old/new values after F5 (desirable, but needs documentation). No single-value filter query exists; the concern in the matrix should be reframed as "stats views automatically split by tool_name — document expected behavior change."
- Matrix entry must be corrected: remove phantom `operation.py:43` reference; rewrite to cite `tools/db_schema.sql` `v_scan_statistics` view and `gui/utils/db_tools_engine.py` stats aggregation.

---

### F0_MIGRATION_BLUEPRINT.md — Gaps

1. **[HIGH] Invalid launcher example (line 130)**: Blueprint claims `dirracuda --country US` parity with `xsmbseek --country US`. But `xsmbseek` is the **GUI launcher** — it has no `--country` option (confirmed: `xsmbseek:946` onward shows argparse with `--mock`, `--config`, `--smbseek-path`, `--database-path`, `--debug`). `--country` belongs to `cli/smbseek.py`. The launcher parity example must be corrected to `dirracuda --mock` ↔ `xsmbseek --mock`.

2. **[MEDIUM] Parser-critical output strings list**: The blueprint mentions F2-H1 risk (parser-coupled text) and correctly says don't change the CLI completion message. But it does not include the full list of parser-critical strings from `gui/utils/backend_interface/progress.py:505-552`. Add an appendix listing all machine-parsed output patterns (for reference during F2/F4 execution).

3. **[MEDIUM] F3 migration function location**: Blueprint says "lives in `shared/db_migrations.py` or a new `shared/path_migration.py`" — this ambiguity must be resolved. Decision: new `shared/path_migration.py`. Rationale: keeps filesystem migration logic cleanly separated from DB schema migration; easier to test in isolation; parallel to existing `shared/db_migrations.py` pattern.

4. **[LOW] F4 note about `shared/output.py:131`**: The blueprint says `SMBSeekOutput` needs to carry a config reference — add a call-site inventory note (`create_output_manager()` in `shared/output.py:434` is the only factory; all callers pass config already via `SMBSeekConfig` instance, so adding a `db_path` property to the output manager is feasible without breaking callers).

5. **[LOW] Phantom `migrations_run` table claim (line 528)**: Blueprint states "A `migrations_run` table (already in `db_migrations.py` pattern) tracks which DB migrations have executed." This is false. Confirmed: `shared/db_migrations.py` has no such table. The actual idempotency mechanism is `IF NOT EXISTS` guards and `PRAGMA table_info()` column checks. The blueprint idempotency claim must be corrected to describe what actually exists.

6. **[LOW] Section cutoff**: The file ends after "No Split-Brain Writes". Missing: a brief "Phase Dependencies" subsection confirming that F2 can run in parallel with F3-start while F3→F4→F5 must be strictly sequential.

---

### F0_RISK_REGISTER.md — Gaps

1. **R12 update**: R12 says "no known consumer" of the `smbseek.db` hardcoded output string — however, the parser in `progress.py` was confirmed to match `"SMBSeek security assessment completed successfully"` (separate string). The R12 risk is about `output.py:131` specifically. Clarify that R12 is safe to change and that the progress.py match string is covered by the separate F2-H1 warning.

2. **Factual drift — db_commands reference (line 267)**: The risk register mentions `db_commands` (same phantom path as the matrix). This reference must be removed and replaced with the actual location of the query concern (`tools/db_schema.sql:229-243` v_scan_statistics view).

3. **Baseline test status note (line 269)**: Currently says "unknown (must run before F1 begins)". Add the exact command: `xvfb-run -a ./venv/bin/python -m pytest gui/tests/ shared/tests/ -q --tb=short 2>&1 | tail -5`.

4. **R16 (new risk)**: Missing risk: `smbseek_report_<timestamp>.json` filename in `shared/output.py:420` — if report files are referenced by external tooling/scripts, renaming the pattern would break them. Medium severity; needs HI awareness.

5. **Open Questions — Q4 resolution recommendation**: The blueprint recommends adding `set_backend_paths()` alias in F1 (keeping `set_smbseek_paths()` as thin wrapper). The risk register should reflect this as the proposed resolution (pending HI confirmation).

---

## Changes Per File

### 1. `NAMING_CONTRACT_MATRIX.md`

**Section 8 changes:**
- Correct `SMBSeekGUI class (if exists)` → `XSMBSeekGUI class` with location `xsmbseek:397`, tag `REVIEW`, rationale "Active GUI class; CLAUDE.md says rename needed; defer to cleanup card after F5; no external contract"
- Change all class name tags from `KEEP` to `REVIEW`; update rationale to: "Internal Python identifier; CLAUDE.md explicitly says needs renaming; no external contract; deferred to cleanup card after F5 to minimize blast radius"
- Add missing classes: `SMBSeekWorkflowDatabase`, `SMBSeekDataAccessLayer`, `SMBSeekDatabaseMaintenance`, `SMBSeekDataImporter`, `SMBSeekTheme`, `SMBSeekGUI` (gui/main.py — deprecated) — all tagged `REVIEW` with same rationale
- Add `SMBSeekGUI` at `gui/main.py:45` — tagged `KEEP` (deprecated legacy file; not worth touching)

**Section 5 additions:**
- Add `tools/db_import.py:33` — `smbseek.db` default — `ALIAS`

**Section 9 additions:**
- Add `shared/output.py:420` — `smbseek_report_<timestamp>.json` — `REVIEW` (possible external script consumers)

**Section 12 (Ambiguities) additions:**
- Q7: Class name rename scope — which cleanup card, which phasing?
- Q8: Report filename pattern — safe to rename or needs compat?

### 2. `F0_MIGRATION_BLUEPRINT.md`

**Fix F2 launcher example (line 130):**
- Replace `dirracuda --country US` example with `dirracuda --mock` (xsmbseek is a GUI launcher; `--country` is a CLI flag belonging to `cli/smbseek.py`, not xsmbseek). The wrapper parity examples must use actual xsmbseek flags: `--mock`, `--config`, `--smbseek-path`/`--backend-path`, `--database-path`.

**Add to F3 section:**
- Resolve ambiguity: migration function goes in new `shared/path_migration.py` (decision, not option). Rationale: filesystem migration logic stays separate from DB schema migration; easier to test in isolation.

**Add to F4 section:**
- Add call-site note for `shared/output.py:131`: all callers of `create_output_manager()` already pass `SMBSeekConfig`; add `db_path` property to `SMBSeekOutput.__init__` (set from `config.get_database_path()`) to enable dynamic path in output string.

**Add after Schema/Data Safety section:**
- "Parser-Critical Output Strings" appendix — exact content from `gui/utils/backend_interface/progress.py:505-552`:

  **Regex-matched stat fields (emitter format must match):**
  | Pattern | Emitter |
  |---------|---------|
  | `Hosts Scanned: <N>` (emoji optional) | `shared/output.py` |
  | `Hosts Accessible: <N>` (emoji optional) | `shared/output.py` |
  | `Accessible Shares: <N>` OR `Accessible Directories: <N>` (emoji optional) | `shared/output.py` |
  | `Shodan Results: <N>` | legacy — safe to remove from emitter, kept in parser for compat |
  | `Hosts Tested: <N>` | legacy |
  | `Successful Auth: <N>` | legacy |
  | `Failed Auth: <N>` | legacy |
  | `session: <N>` | `shared/output.py` or workflow |

  **String-matched success indicators (line 548-552) — exact strings must not change:**
  | String | Note |
  |--------|------|
  | `"SMBSeek security assessment completed successfully"` | **MUST NOT CHANGE** without paired parser update; contains old branding |
  | `"✓ Found"` + `"accessible SMB servers"` | two-part match |
  | `"✓ Discovery completed:"` | |
  | `"FTP scan completed successfully"` | |
  | `"HTTP scan completed successfully"` | |

  **Action for F2:** The string `"SMBSeek security assessment completed successfully"` contains old branding but is machine-parsed. It must NOT change in F2 (text-only card). In a future card, change both emitter and `progress.py:548` in the same commit: replace with `"Dirracuda security assessment completed successfully"`.

**Fix phantom `migrations_run` table claim (line 528):**
- Replace "A `migrations_run` table (already in `db_migrations.py` pattern) tracks which DB migrations have executed" with accurate description of the actual idempotency mechanism: `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `PRAGMA table_info()` column checks before `ALTER TABLE ADD COLUMN`, and `IF NOT EXISTS` guards throughout. No tracking table exists; idempotency is structural. For path migration specifically, a filesystem marker file `~/.dirracuda/.migrated_from_smbseek` will serve as the idempotency signal.

**Add "Phase Dependencies" subsection:**
```
F1 (bridge layer) → prerequisite for all
F2 (UI text)      → can start after F1; independent of data migration
F3 (user paths)   → after F1; must complete before F4
F4 (DB filename)  → after F3
F5 (metadata)     → after F4; HI sign-off required
F6 (validation)   → after F5
```

### 3. `F0_RISK_REGISTER.md`

**Update R12:** Clarify scope: R12 is the `"smbseek.db"` literal in output.py:131 (not the progress parser match string, which is a separate F2-H1 concern). Safe to update in F4.

**Update Baseline section:** Add exact baseline test command.

**Add R16:** New risk — `smbseek_report_*.json` filename pattern; medium severity; HI awareness needed.

**Update Open Questions Q4:** Add proposed resolution: add `set_backend_paths()` as alias in F1, keep `set_smbseek_paths()` as thin wrapper, defer removal.

---

## Validation Plan

**After writing artifacts:**

1. **Naming inventory check** (confirm expected tokens still present — this is a doc-only change so no token should disappear from code):
   ```bash
   rg -n -i "\b(dirracuda|smbseek|xsmbseek)\b" xsmbseek gui shared cli tools conf | wc -l
   ```

2. **Key contract inventory** (confirm all matrix entries reference real file:line locations):
   ```bash
   rg -n -i "smbseek_path|\.smbseek|smbseek\.db|XSMBSEEK_|tool_name|scan_type" xsmbseek gui shared cli tools conf | wc -l
   ```

3. **Class name spot-checks** (confirm newly added matrix entries are accurate):
   ```bash
   grep -n "class SMBSeekWorkflowDatabase" shared/database.py
   grep -n "class SMBSeekDataAccessLayer" tools/db_manager.py
   grep -n "class XSMBSeekGUI" xsmbseek
   grep -n "class SMBSeekTheme" gui/utils/style.py
   ```

4. **Document structure check** (no broken links or forward references):
   - Each phase cites specific file:line — manually spot-checked during research phase ✓

---

## Critical Files (no code changes, reference only)

| File | Purpose |
|------|---------|
| `xsmbseek` | XSMBSeekConfig, XSMBSeekGUI — class names, config key aliases, argparse flags |
| `gui/utils/settings_manager.py` | ~/.smbseek default, set_smbseek_paths(), DB detection |
| `gui/utils/logging_config.py` | GUI_LOGGER_NAME = "smbseek_gui", XSMBSEEK_DEBUG_* env vars |
| `shared/config.py` | SMBSeekConfig class, DB path defaults |
| `shared/database.py` | SMBSeekWorkflowDatabase, tool_name defaults |
| `shared/db_migrations.py` | scan_sessions defaults, backfill logic |
| `shared/output.py` | SMBSeekOutput, hardcoded smbseek.db, smbseek_report_*.json |
| `tools/db_schema.sql` | scan_sessions tool_name DEFAULT 'smbseek' |
| `gui/utils/probe_cache.py`, `ftp_probe_cache.py`, `http_probe_cache.py` | ~/.smbseek path constants |
| `gui/utils/template_store.py` | ~/.smbseek/templates path |
| `gui/utils/extract_runner.py` | ~/.smbseek/extract_logs path |
| `gui/utils/backend_interface/interface.py` | smbseek.db default, XSMBSEEK_DEBUG_SUBPROCESS |
| `gui/utils/backend_interface/progress.py` | parser-critical CLI output strings |

---

## Response Format

After execution:
- **Issue:** Existing F0 draft artifacts have class name errors, missing entries, and ambiguous recommendations
- **Root cause:** Prior drafting session missed ~6 internal class names, used incorrect class name (`SMBSeekGUI` vs `XSMBSeekGUI`), and incorrectly cited CLAUDE.md as justification for KEEP on class names that CLAUDE.md explicitly says need renaming
- **Fix:** Update three artifact files with corrections and additions per gap analysis above
- **Files changed:** `docs/dev/rebrand/NAMING_CONTRACT_MATRIX.md`, `docs/dev/rebrand/F0_MIGRATION_BLUEPRINT.md`, `docs/dev/rebrand/F0_RISK_REGISTER.md`
- **Validation run:** Class name spot-checks + naming inventory confirms no code-state changes; all new matrix entries verified against current repo
- **HI test needed:** Yes — review artifacts and confirm ambiguous items (Q1–Q8) before F1 implementation begins
