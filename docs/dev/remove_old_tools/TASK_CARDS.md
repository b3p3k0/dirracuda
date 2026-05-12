# Task Cards: Pry/RCE Sunset

This document is the execution contract for Claude. Complete cards in order, one at a time.

## Common Definition Of Done (All Cards)

1. Change scope is limited to active card intent.
2. Root cause is stated explicitly in report.
3. Validation commands are listed exactly as run.
4. PASS/FAIL is explicit for each validation item.
5. Touched file line counts are recorded before/after with rubric.
6. If any touched file exceeds 1700 lines, pause and propose modularization before continuing.
7. No commit unless HI explicitly says `commit`.

## Required Per-Card Report Format

1. Issue:
2. Root cause:
3. Fix:
4. Files changed:
5. Validation run:
6. Result:
7. HI test needed? (yes/no + exact steps)

## Baseline Validation Command Set

1. Compile touched modules:
- `./venv/bin/python -m py_compile <touched_python_modules>`

2. Targeted tests first:
- `./venv/bin/python -m pytest gui/tests/test_action_routing.py -q`
- `./venv/bin/python -m pytest gui/tests/test_server_ops_scenario_matrix.py -q`
- `./venv/bin/python -m pytest shared/tests/test_probe_gating.py -q` (or replacement when sunset removes this target)
- `./venv/bin/python -m pytest gui/tests/test_unified_scan_dialog_validation.py -q`

3. Tk headless fallback:
- `xvfb-run -a ./venv/bin/python -m pytest <target> -q`

4. Grep guardrails:
- `rg -n "pry|--1337|_pry_unlocked|PryDialog" dirracuda gui shared cli commands conf README.md docs/TECHNICAL_REFERENCE.md`
- `rg -n "check-rce|_rce_unlocked|rce_enabled|rce_scanner|rce_status" dirracuda gui shared cli commands conf README.md docs/TECHNICAL_REFERENCE.md`

## C0 - Contract Freeze + Touchpoint Inventory (Docs-Only)

### Objective

Freeze exact sunset scope and generate subsystem touchpoint matrix.

### Required Output

1. Touchpoint matrix grouped by subsystem:
- entrypoint/session gates
- server-list actions
- scan/detail dialogs
- probe/analyzer pipeline
- CLI/workflow path
- DB access and schema touchpoints
- docs and test touchpoints

2. Explicit non-goals frozen in writing.

### Validation

1. N/A for runtime behavior (docs-only).
2. Verify referenced files/paths exist where claimed.

---

### C0 Execution Report

1. **Issue:** C0 touchpoint inventory not yet written; subsequent cards lacked a frozen execution contract.
2. **Root cause:** C0 is the first card; no matrix existed before this run.
3. **Fix:** Wrote full 7-subsystem touchpoint matrix (below); updated ROADMAP.md status to Done; added R-07 and R-08 to RISK_REGISTER.md.
4. **Files changed:** `docs/dev/remove_old_tools/TASK_CARDS.md`, `docs/dev/remove_old_tools/ROADMAP.md`, `docs/dev/remove_old_tools/RISK_REGISTER.md`
5. **Validation run:** `git status --short -- <3-path allowlist>`; `wc -l` on all three files; `ls` to verify claimed paths; `rg -i` output captured in matrix.
6. **Result:** PASS — matrix complete, no runtime code modified.
7. **HI test needed?** No — docs-only card. HI should review the touchpoint matrix for accuracy before approving advancement to C1.

---

### C0 Grep Validation Evidence (Case-Insensitive)

**Command 1 — Pry:**
```
rg -n -i "pry|--1337|_pry_unlocked|prydialog" dirracuda gui shared cli commands conf README.md docs/TECHNICAL_REFERENCE.md
```

File paths with matches (excludes `__pycache__`):
- `dirracuda` (lines 603, 617–618, 1164, 1185, 1198, 1655–1658, 1672–1673, 1688)
- `docs/TECHNICAL_REFERENCE.md` (lines 167, 675, 890, 907, 909, 911–912, 918, 1193)
- `conf/config.json.example` (line 64)
- `shared/db_migrations.py` (lines 5, 60)
- `gui/components/pry_dialog.py` (whole file)
- `gui/components/pry_status_dialog.py` (line 2 — shared module)
- `gui/utils/pry_runner.py` (whole file)
- `gui/utils/wordlist_path.py` (line 2 — **caught only by case-insensitive pass**)
- `gui/utils/default_gui_settings.py` (line 117)
- `gui/components/app_config_dialog.py` (lines 150, 157, 165, 268–269, 385, 763, 903–904, 1022, 1049, 1122, 1125, 1240, 1272, 1334, 1359, 1369)
- `gui/components/reddit_browser_window.py` (line 57 — import only)
- `gui/components/se_dork_browser_window.py` (line 36 — import only)
- `gui/components/server_list_window/window.py` (lines 29–30, 70, 105–106, 156, 167, 211, 577–578, 846–859)
- `gui/components/server_list_window/actions/batch.py` (lines 29–30, 40, 54–57, 110–113, 118, 177–178, 669–767)
- `gui/components/server_list_window/actions/batch_operations.py` (lines 23–24, 742–804)
- `gui/components/server_list_window/actions/batch_status.py` (lines 20, 128, 355–356, 371, 374, 421–519, 541)
- `gui/tests/test_action_routing.py` (lines 18, 42–43, 72–74, 221, 226, 247, 1206–1266)
- `gui/tests/test_server_ops_scenario_matrix.py` (lines 106–121)
- `gui/tests/test_app_config_dialog.py` (lines 14, 75, 170, 172, 378–454)
- `gui/tests/test_app_config_dialog_dorks.py` (lines 21, 30–32, 60)
- `gui/tests/test_clamav_results_dialog.py` (line 436)
- `gui/tests/_server_ops_harness.py` (lines 369–373)
- `gui/tests/test_server_list_card4.py` (lines 60–61, 702–716)
- `gui/tests/test_server_ops_fuzz_sequences.py` (line 66)
- `gui/tests/test_db_tools_engine.py` (line 90 — schema fixture)
- `gui/tests/test_db_tools_engine_merge.py` (lines 223, 247, 825, 865, 893)
- `gui/tests/test_db_tools_engine_schema_preview.py` (lines 552, 592, 611)

**Command 2 — RCE:**
```
rg -n -i "check-rce|_rce_unlocked|rce_enabled|rce_scanner|rce_status" dirracuda gui shared cli commands conf README.md docs/TECHNICAL_REFERENCE.md
```

File paths with matches:
- `dirracuda` (lines 618, 1007, 1165, 1199)
- `docs/TECHNICAL_REFERENCE.md` (lines 15, 53, 138, 513, 558, 595, 1125, 1161)
- `cli/smbseek.py` (line 330)
- `shared/workflow.py` (line 194)
- `commands/access/rce_analyzer.py` (whole file, 120 lines)
- `commands/access/operation.py` (line 109)
- `shared/rce_scanner/verdicts.py`, `logger.py`, `reporter.py` (multiple lines each)
- `shared/config.py` (lines 575–637)
- `shared/database.py` (lines 755–790)
- `shared/db_migrations.py` (lines 121–123, 244, 260–262, 435, 454–456, 516)
- `shared/tests/test_probe_gating.py` (lines 1–2)
- `shared/tests/test_smb_parsing.py` (lines 3–4)
- `shared/tests/test_ftp_state_tables.py` (lines 116, 131, 253, 283)
- `gui/components/dashboard_scan.py` (lines 169–171, 204, 266, 288, 314)
- `gui/components/dashboard_batch_ops.py` (lines 485–486, 489, 952–955)
- `gui/components/unified_scan_dialog.py` (lines 104, 212–216, 294, 607, 641–643, 940, 1417)
- `gui/components/scan_dialog.py` (lines 139, 764, 807, 860–863)
- `gui/components/scan_dialog_layout.py` (lines 204, 246, 559)
- `gui/components/scan_preflight.py` (lines 371, 397, 399, 429–435)
- `gui/components/server_list_window/window.py` (lines 106, 550, 1183–1184)
- `gui/components/server_list_window/details.py` (lines 46, 526, 555–556, 708–748, 777, 813–815, 860, 873)
- `gui/components/server_list_window/table.py` (line 189)
- `gui/components/server_list_window/actions/batch.py` (lines 57, 379, 440–443)
- `gui/components/server_list_window/actions/batch_operations.py` (lines 1125–1131, 1196)
- `gui/components/server_list_window/actions/batch_status.py` (lines 729–731, 735–760, 820)
- `gui/dashboard/widget.py` (lines 161, 1138)
- `gui/utils/probe_runner.py` (lines 127, 144, 151)
- `gui/utils/database_access_write_methods.py` (lines 403, 408, 759–813, 1479–1545, 1602, 1614–1616)
- `gui/utils/database_access_core_methods.py` (lines 32–33, 742, 852, 875, 885)
- `gui/utils/database_access_protocol_methods.py` (lines 350, 408, 461, 624, 735, 760)
- `gui/utils/default_gui_settings.py` (lines 59, 73)
- `gui/tests/test_action_routing.py` (lines 10, 248, 336–354, 852, 868, 1192)
- `gui/tests/test_unified_scan_dialog_validation.py` (lines 89, 196, 200)
- `gui/tests/test_dashboard_scan_dialog_wiring.py` (lines 43, 84)
- `gui/tests/test_scan_preflight_probe_depth.py` (lines 83, 126, 176, 225)
- `gui/tests/test_database_access_protocol_writes.py` (lines 74, 129, 369–377, 470–484, 665–678, 893)
- `gui/tests/test_database_access_protocol_union.py` (lines 70, 125, 604–647)
- `shared/signatures/rce_smb/rules.py` (lines 18–20)
- `conf/signatures/rce_smb/CVE-2020-1206.yaml` (line 37)

**README.md (broad token check, case-insensitive):**
```
rg -n -i "pry|rce|smb.audit|wordlist|signature|_1337|check.rce" README.md
```
Match: line 82 — `PyYAML | Loads RCE vulnerability signatures from conf/signatures/rce_smb/*.yaml`

Verdict: one in-scope RCE reference in README.md. Will be updated in C6; verify whether PyYAML is used elsewhere before removing the dep entry.

---

### C0 Line Count Rubric (Before Any Changes)

Scale: <=1200 Excellent | 1201-1500 Good | 1501-1800 Acceptable | 1801-2000 Poor | >2000 Unacceptable

| File | Lines | Rubric |
|---|---|---|
| `dirracuda` | 1700 | Acceptable |
| `gui/dashboard/widget.py` | 1659 | Acceptable |
| `gui/utils/database_access_write_methods.py` | 1623 | Acceptable |
| `gui/components/unified_scan_dialog.py` | 1515 | Acceptable |
| `gui/components/dashboard_batch_ops.py` | 1507 | Acceptable |
| `gui/tests/test_action_routing.py` | 1497 | Good |
| `gui/components/server_list_window/actions/batch_operations.py` | 1441 | Good |
| `gui/components/app_config_dialog.py` | 1376 | Good |
| `gui/components/server_list_window/window.py` | 1237 | Good |
| `gui/components/server_list_window/details.py` | 1174 | Excellent |
| `shared/db_migrations.py` | 1153 | Excellent |
| `gui/components/scan_dialog.py` | 1092 | Excellent |
| `gui/components/dashboard_scan.py` | 1044 | Excellent |
| `gui/components/scan_dialog_layout.py` | 1032 | Excellent |
| `gui/components/server_list_window/actions/batch_status.py` | 999 | Excellent |
| `gui/utils/database_access_core_methods.py` | 957 | Excellent |
| `shared/database.py` | 813 | Excellent |
| `gui/utils/database_access_protocol_methods.py` | 792 | Excellent |
| `gui/components/server_list_window/actions/batch.py` | 776 | Excellent |
| `shared/config.py` | 687 | Excellent |
| `gui/tests/test_server_ops_scenario_matrix.py` | 535 | Excellent |
| `shared/rce_scanner/reporter.py` | 519 | Excellent |
| `gui/components/server_list_window/table.py` | 467 | Excellent |
| `gui/tests/test_app_config_dialog.py` | 454 | Excellent |
| `gui/components/scan_preflight.py` | 446 | Excellent |
| `cli/smbseek.py` | 435 | Excellent |
| `commands/access/operation.py` | 434 | Excellent |
| `gui/utils/pry_runner.py` | 346 | Excellent |
| `gui/utils/probe_runner.py` | 343 | Excellent |
| `gui/components/pry_dialog.py` | 287 | Excellent |
| `shared/workflow.py` | 239 | Excellent |
| `shared/rce_scanner/logger.py` | 225 | Excellent |
| `gui/components/pry_status_dialog.py` | 176 | Excellent |
| `gui/utils/default_gui_settings.py` | 169 | Excellent |
| `shared/rce_scanner/verdicts.py` | 149 | Excellent |
| `commands/access/rce_analyzer.py` | 120 | Excellent |
| `gui/utils/wordlist_path.py` | 71 | Excellent |
| `shared/tests/test_probe_gating.py` | 26 | Excellent |
| `shared/rce_scanner/` (7-file module total) | 2464 | N/A — module |

No file is Poor or Unacceptable. Five files are Acceptable; none exceed 1800. No modularization needed before C1. `dirracuda` (1700 Acceptable) drops to ~1670 after C1's `--1337` removal.

---

### C0 Touchpoint Matrix

#### 1. Entrypoint / Session Gates

| Symbol/Function | File | Lines | Current Behavior | Removal Card | Compat Risk |
|---|---|---|---|---|---|
| `--1337` argparse flag + `_render_1337_motd()` | `dirracuda` | 115, 1655–1658 | Hidden CLI flag; sets `pry_unlocked=True`; prints ASCII motd | C1 | Low — hidden flag |
| `pry_unlocked=` constructor param | `dirracuda` | 603, 617 | Sets `self._pry_unlocked`; propagates to window_data and child calls | C1 | Trace all 5 propagation sites |
| `self._pry_unlocked` / `self._rce_unlocked` | `dirracuda` | 617–618 | Instance flags; `_rce_unlocked` derived from `_pry_unlocked` | C1 | Cascade removal |
| `window_data["_pry_unlocked"]` / `["_rce_unlocked"]` | `dirracuda` | 1164–1165, 1198–1199 | Passed to `ServerListWindow` | C1 | |
| `show_pry_controls=self._pry_unlocked` | `dirracuda` | 1185 | Passed to `open_settings_dialog()` | C1 | |
| `rce_unlocked=self._rce_unlocked` | `dirracuda` | 1007 | Passed to `DashboardWidget` init | C1 | |
| `pry_unlocked=bool(...)` at app launch | `dirracuda` | 1688 | Passes arg to `App(...)` constructor | C1 | |

#### 2. Server-List Actions

| Symbol/Function | File | Lines | Current Behavior | Removal Card | Compat Risk |
|---|---|---|---|---|---|
| `self._pry_unlocked`, `self._rce_unlocked` | `window.py` | 105–106 | Read from `window_data`; gate pry/rce controls | C2 | Action routing tests must re-run post-C2 |
| `self.pry_button`, `self.pry_status_button` | `window.py` | 156, 167, 211 | Button instance attrs | C2 | |
| `"🔓 Pry Selected"` context menu entry | `window.py` | 577–578 | Right-click menu entry when unlocked | C2 | |
| Pry toolbar button creation block | `window.py` | 846–859 | Creates `pry_button` + spacer when unlocked | C2 | |
| `_on_pry_selected()` | `batch_operations.py` | 742–804 | Opens `PryDialog`, dispatches batch pry job | C2 | `test_action_routing.py` refs; update in C5 |
| `_execute_pry_target()` | `batch.py` | 669–767 | Runs pry job via `pry_runner.run_pry()` | C2 | |
| `_start_batch_job("pry",...)` routing | `batch.py` | 54–57, 110–113, 177–178 | Gates + dispatches pry job type | C2 | |
| `_is_pry_batch_active()` | `batch_status.py` | 355–356 | Checks for active pry jobs | C2 | |
| `_set_pry_status_button_visible()` | `batch_status.py` | 371 | Toggles pry status button | C2 | |
| `_show_pry_status_dialog()` | `batch_status.py` | 374 | Opens pry status dialog | C2 | |
| `_persist_pry_success()` + credential writes | `batch_status.py` | 421–519 | Writes credentials to `share_credentials` with `source='pry'` | C2 (write path); C4 (read compat preserved) | Historical `source='pry'` rows stay readable |
| `(probe_button, extract_button, pry_button)` tuple | `batch_status.py` | 541 | Button cleanup loop | C2 | |
| `show_rce_column=self._rce_unlocked` | `window.py` | 550 | Passes RCE column visibility to table | C3 | |
| `rce_status_callback=...`, `show_rce_controls=...` | `window.py` | 1183–1184 | Wires RCE status into probe dialog | C3 | |

#### 3. Scan / Detail Dialogs

| Symbol/Function | File | Lines | Current Behavior | Removal Card | Compat Risk |
|---|---|---|---|---|---|
| `PryDialog` class | `gui/components/pry_dialog.py` | 1–287 (whole file) | GUI for pry config | C2 (delete file) | `test_app_config_dialog.py` imports; update C5 |
| `"pry"` entry in job type label map | `batch_status.py` | 128 | `"pry": "Pry"` display label | C2 (entry only) | Do not delete `pry_status_dialog.py` — shared by probe/extract |
| `rce_enabled_var` + RCE checkbox | `unified_scan_dialog.py` | 104, 212–216, 294, 607, 641–643, 940, 1417 | RCE toggle; forced False when not unlocked | C3 | `test_unified_scan_dialog_validation.py`; update C5 |
| `rce_enabled_var` + RCE checkbox | `scan_dialog.py` | 139, 764, 807, 860–863 | Same in legacy scan dialog | C3 | |
| `rce_enabled` layout wiring | `scan_dialog_layout.py` | 204, 246, 559 | Layout binding for RCE checkbox | C3 | |
| `rce_enabled` gating + probe summary string | `scan_preflight.py` | 371, 397, 399, 429–435 | Strips `rce_enabled`; appends "RCE On/Off" to summary | C3 | Rewrite summary string at line 399 |
| `show_pry_controls` param + wordlist field | `app_config_dialog.py` | 150, 157, 385, 1049, 1122, 1272, 1334, 1359, 1369 | Conditionally shows wordlist field in settings dialog | C2 | Config write at 1334 must still write non-pry keys |
| `show_rce_controls` param | `unified_scan_dialog.py`, `scan_dialog.py`, `details.py` | multiple | Controls RCE checkbox visibility | C3 | |
| Probe dialog RCE pref reads/writes | `details.py` | 555–556, 813–815, 860 | Reads/writes `probe_dialog.rce_enabled` setting | C3 | Orphan key in GUI settings JSON is harmless |
| `rce_status_callback` param + invocation chain | `details.py` | 46, 526, 708–748, 777, 873 | Passes RCE status back to parent | C3 | |

#### 4. Probe / Analyzer Pipeline

| Symbol/Function | File | Lines | Current Behavior | Removal Card | Compat Risk |
|---|---|---|---|---|---|
| `shared/rce_scanner/` module | 7 files, 2464 lines | Signature-based RCE analysis engine | C3 (delete directory) | Confirm all import guards removed first |
| `from shared.rce_scanner.probes import SafeProbeRunner` | `commands/access/operation.py` | 109 | Conditional import inside function body | C3 | Already guarded |
| `from shared.rce_scanner import scan_rce_indicators` | `gui/utils/probe_runner.py` | 127 | Conditional import inside probe run | C3 | Surrounding probe logic must remain intact |
| `commands/access/rce_analyzer.py` | whole file, 120 lines | Orchestrates `SafeProbeRunner` + `_persist_rce_status()` | C3 (delete file) | Only called via `operation.py` |
| `shared/signatures/rce_smb/rules.py` | ~20 lines | Imports `Verdict` from rce_scanner | C3 (delete file) | Confirm no other callers |
| `conf/signatures/rce_smb/CVE-2020-1206.yaml` | 1 file | Signature loaded at runtime | C3 (delete file) | No effect once scanner removed |
| `_handle_rce_status_update()` | `batch_status.py` | 746–760 | Updates in-memory row with rce_status | C3 | `test_action_routing.py` refs; update C5 |
| `_determine_rce_status()` | `batch_status.py` | 735–744 | Reads rce_status from DB for display | C3 | |
| `_rce_status_to_emoji()` | `batch_status.py` | 820–835 | Maps status string to emoji | C3 | |
| `rce_status`/`rce_status_emoji` dict fields | `batch_status.py` | 729–731 | Loads RCE display fields from DB | C3 | DB column remains |
| `rce_status_emoji` render | `table.py` | 189 | Renders RCE emoji column | C3 | Check column header for index dependency |
| `"RCE: <status>"` note append | `batch.py` | 440–443 | Appends to probe notes | C3 | |
| `rce_unlocked` gating in batch dispatch | `batch.py` | 57, 379 | Guards RCE path in job execution | C3 | |
| `rce_enabled` / `rce_status` in scan result | `dashboard_batch_ops.py` | 485–486, 489, 952–955 | Passes `rce_enabled`; handles `rce_status` in result | C3 | Surrounding logic must remain intact |
| `rce_enabled` in scan request | `dashboard_scan.py` | 169–171, 204, 266, 288, 314 | Forces `rce_enabled=False` when not unlocked | C3 | |
| `_rce_unlocked` in `dashboard/widget.py` | `widget.py` | 161, 1138 | Propagates `_rce_unlocked` to scan dialog | C1/C3 boundary | |

#### 5. CLI / Workflow Path

| Symbol/Function | File | Lines | Current Behavior | Removal Card | Compat Risk |
|---|---|---|---|---|---|
| `--check-rce` argparse option | `cli/smbseek.py` | 330 | Adds RCE scan to CLI; maps to `check_rce` | C3 | `workflow.py` uses `getattr(args, 'check_rce', False)` with default; safe |
| `getattr(args, 'check_rce', False)` | `shared/workflow.py` | 194 | Reads `check_rce` and passes to workflow | C3 | `getattr` default means no crash; still clean up |
| `pry_runner` import | `window.py`, `batch_operations.py`, `batch.py` | 29–30, 40, 70 | Imports `pry_runner` module | C2 | |
| `from gui.components.pry_dialog import PryDialog` | `window.py` 29, `batch.py` 29, `batch_operations.py` 23 | Import | C2 | |
| `from gui.components.pry_status_dialog import BatchStatusDialog` | `window.py` 30, `batch.py` 30 (pry callers only) | Import | C2 (remove from pry callers only) | Do not remove from `reddit_browser_window.py` or `se_dork_browser_window.py` — shared |

#### 6. DB Access / Schema Touchpoints

| Symbol/Function | File | Lines | Current Behavior | Removal Card | Compat Risk |
|---|---|---|---|---|---|
| `upsert_rce_status()` SMB compat shim | `shared/database.py` | 755–790 | Writes `rce_status` to `host_probe_cache` | C3 (stop calling); C4 (remove method) | Legacy DB rows untouched |
| `upsert_rce_status_for_host()` | `database_access_write_methods.py` | 759–813 | Writes `rce_status` to SMB/FTP probe caches | C3 (stop calling); C4 (remove) | |
| `upsert_rce_status()` shim | `database_access_write_methods.py` | 1542–1545 | Delegates to `upsert_rce_status_for_host` | C4 | |
| `get_rce_status()` / `get_rce_status_for_host()` | `database_access_write_methods.py` | 1479–1538 | Reads `rce_status` from probe cache | C4 (remove methods; DB column stays) | |
| `rce_status` in `__all__` | `database_access_write_methods.py` | 1602, 1614–1616 | Exports rce_status methods | C4 | |
| `rce_status` in `SELECT` queries | `database_access_protocol_methods.py` | 350, 408, 461, 624, 735, 760 | `COALESCE(pc.rce_status, 'not_run')` | C3/C4 | Clean all downstream key expectations too |
| `rce_status` in probe result dicts | `database_access_core_methods.py` | 742, 852, 875, 885 | Populates `rce_status` in returned dicts | C4 | |
| Runtime migration guard (duplicate) | `database_access_core_methods.py` | 32–33 | `ALTER TABLE ... ADD COLUMN rce_status` | C4 (remove duplicate; `db_migrations.py` handles it) | Column already exists in migrated DBs |
| `rce_status` schema in `db_migrations.py` | `shared/db_migrations.py` | 121–123, 244, 260–262, 435, 454–456, 516 | Adds `rce_status` columns | **Do not touch** | Column must stay for legacy DB compat |
| `share_credentials.source DEFAULT 'pry'` | `shared/db_migrations.py` | 60 | Schema default for credential source | **Do not touch** | Historical `source='pry'` rows must stay readable |
| `pry` block in `conf/config.json.example` | `conf/config.json.example` | 64 | Default pry config | C4 | Users retain existing `config.json`; harmless |
| `pry` defaults in `default_gui_settings.py` | `gui/utils/default_gui_settings.py` | 117 | `'pry': {'wordlist_path': '', ...}` | C4 | |
| `rce_enabled` defaults | `default_gui_settings.py` | 59, 73 | `'rce_enabled': False` | C4 | |
| `get_rce_config()` and related accessors | `shared/config.py` | 575–637 | Typed accessors for `rce.*` config keys | C4 | Confirm no non-RCE caller |
| `_DEFAULT_CONFIG_RCE_JSONL_PATH` | `shared/config.py` | 25 | Default RCE JSONL log path | C4 | |

#### 7. Docs / Test Touchpoints

| Symbol/Function | File | Lines | Current Behavior | Removal Card | Compat Risk |
|---|---|---|---|---|---|
| Pry sections | `docs/TECHNICAL_REFERENCE.md` | 167, 675, 890, 907–912, 918, 1193 | `§6.7 Pry Password Audit` and related tables | C6 | In-scope docs |
| RCE sections | `docs/TECHNICAL_REFERENCE.md` | 15, 53, 138, 513, 558, 595, 1125, 1161 | `--check-rce`, rce_scanner, `rce_status` schema | C6 | In-scope docs |
| PyYAML / RCE dep entry | `README.md` | 82 | `PyYAML | Loads RCE vulnerability signatures` | C6 | Verify PyYAML usage outside rce_scanner before removing dep entry |
| `gui/utils/wordlist_path.py` | whole file, 71 lines | Pry-specific wordlist path normalization | C2 (delete file) | Only callers: `pry_dialog.py` (C2) and `app_config_dialog.py` wordlist field (C2) |
| `test_probe_gating.py` | `shared/tests/test_probe_gating.py` | 1–26 (whole file) | Tests `SafeProbeRunner` | C5 (delete) | |
| RCE imports in `test_smb_parsing.py` | `shared/tests/test_smb_parsing.py` | 3–4 | Imports `SafeProbeRunner`, `Verdict` | C5 | |
| `rce_status` assertions in `test_ftp_state_tables.py` | `shared/tests/test_ftp_state_tables.py` | 116, 131, 253, 283 | Asserts column exists and is populated | C5 (update: column present but not written) | |
| Pry/RCE tests in `test_action_routing.py` | `gui/tests/test_action_routing.py` | 1206–1266 (pry), 336–354 (rce_status) | Tests `_on_pry_selected` + `_handle_rce_status_update` | C5 | 1497-line file; will shrink post-removal |
| `test_s3_pry_mixed_selection_blocks_launch` | `gui/tests/test_server_ops_scenario_matrix.py` | 106–121 | Pry launch gate scenario | C5 (delete + add sunset assertion) | |
| Pry tests in `test_app_config_dialog.py` | `gui/tests/test_app_config_dialog.py` | 413–454 | `test_pry_defaults_*` | C5 (delete) | |
| Pry refs in `test_app_config_dialog_dorks.py` | `gui/tests/test_app_config_dialog_dorks.py` | 21, 30–32, 60 | `show_pry_controls`, `wordlist_path` | C5 | |
| `"pry"` in fuzz job type choices | `gui/tests/test_server_ops_fuzz_sequences.py` | 66 | `rng.choice(["probe", "extract", "pry"])` | C5 | |
| `PryHarness` class | `gui/tests/_server_ops_harness.py` | 369–373 | Harness with `_pry_unlocked=True` | C5 (delete class) | |
| Pry stubs in `test_server_list_card4.py` | `gui/tests/test_server_list_card4.py` | 60–61, 702–716 | Stubs `pry_dialog` + `pry_status_dialog` | C5 | |
| `_set_pry_status_button_visible` mock | `gui/tests/test_clamav_results_dialog.py` | 436 | `MagicMock()` assignment | C5 | |
| `dash._rce_unlocked` setup | `gui/tests/test_dashboard_scan_dialog_wiring.py` | 43, 84 | Sets RCE flag in test fixture | C5 | |
| `rce_enabled_var` assertions | `gui/tests/test_unified_scan_dialog_validation.py` | 89, 196, 200 | Asserts RCE forced False | C5 (replace with sunset assertion) | |
| `"rce_enabled": False` in fixture dicts | `gui/tests/test_scan_preflight_probe_depth.py` | 83, 126, 176, 225 | Scan option fixture field | C5 (remove field) | |
| `upsert_rce_status*` / `get_rce_status*` tests | `gui/tests/test_database_access_protocol_writes.py` | 369–377, 470–484, 665–678, 893 | Tests for removed methods | C5 (delete; retain schema-presence tests) | |
| `get_rce_status_for_host` tests | `gui/tests/test_database_access_protocol_union.py` | 604–647 | Tests for removed method | C5 (delete) | |
| `source TEXT DEFAULT 'pry'` schema fixtures | `gui/tests/test_db_tools_engine*.py` | multiple | Inline schema fixtures | **Do not change** — legacy DB compat | |

---

### C0 Non-Goals (Frozen)

1. **No destructive schema migration.** `rce_status` columns in `host_probe_cache`, `ftp_probe_cache`, `http_probe_cache`, and `vulnerabilities` stay. `share_credentials.source DEFAULT 'pry'` unchanged.
2. **Preserve legacy historical DB rows.** Rows with `source='pry'` in `share_credentials` or `rce_status` values in probe caches remain readable. App must open existing DBs without migration failures.
3. **No global docs archive scrub.** Only `README.md` and `docs/TECHNICAL_REFERENCE.md` updated in C6. `docs/dev/*` outside `remove_old_tools/` is not touched.
4. **No broad architecture refactors.** Pry/RCE removal only.
5. **`pry_status_dialog.py` is not deleted.** Shared by probe, extract, Reddit browser, and SE dork browser flows. Only the `"pry"` job type branch is removed in C2.

## C1 - Entrypoint + Session-Gate Removal

### Objective

Remove hidden unlock model and gate propagation for Pry/RCE.

### Required Changes

1. Remove `--1337` and any related unlock path.
2. Remove `_pry_unlocked` and `_rce_unlocked` runtime propagation.
3. Remove launch/request payload fields used only for these gates.

### Validation Focus

1. App still launches via `./dirracuda`.
2. No Pry/RCE unlock references remain in entrypoint wiring.

## C2 - Pry Runtime Excision

### Objective

Remove Pry action flow from active runtime/UI paths.

### Required Changes

1. Remove Pry action/menu/button/dialog/job flow in server list.
2. Remove new Pry credential persistence behavior.
3. Keep legacy `share_credentials` read compatibility for historical data.

### Validation Focus

1. No Pry controls in UI tests/snapshots where applicable.
2. Non-Pry server actions remain functional.

## C3 - RCE Runtime Excision

### Objective

Remove RCE runtime flow from CLI, workflow, probe pipeline, and UI.

### Required Changes

1. Remove `--check-rce` CLI option and call chain.
2. Remove RCE analysis toggles/notes/status callbacks.
3. Remove RCE status columns/presentation in relevant GUI surfaces.

### Validation Focus

1. Core scan/probe operations still run without RCE path.
2. No residual RCE flags in request plumbing.

## C4 - Compatibility Cleanup (No Destructive Migration)

### Objective

Remove active config/runtime usage while preserving legacy DB compatibility.

### Required Changes

1. Remove active defaults and accessors tied only to Pry/RCE.
2. Stop creating/updating Pry/RCE runtime records.
3. Keep legacy DB structures readable and harmless.
4. Add runtime schema guards where access paths can encounter old shape differences.

### Validation Focus

1. Existing DB opens without crash.
2. No new Pry/RCE records are produced.

## C5 - Tests + Scenario Matrix Update

### Objective

Align tests and scenario docs with sunset behavior.

### Required Changes

1. Delete/replace Pry/RCE-specific assertions with sunset assertions.
2. Update scenario matrix to remove Pry-specific expectations.
3. Add regressions for absence of Pry/RCE controls/flags.
4. Verify SMB/FTP/HTTP core workflows unchanged.

### Validation Focus

1. Targeted pytest passes for touched suites.
2. Guardrail `rg` checks confirm no active runtime references remain.

---

### C5 Execution Report

1. **Issue:** Stale Pry/RCE fixture residue in 8 test files; pry scenario test removed in C2 with no replacement sunset assertion; `PryOperationsHarness` orphaned after C2.
2. **Root cause:** C1–C4 removed runtime symbols but left test stubs/fixture attrs that still named them — creating false test coverage and a misleading picture of sunset completeness.
3. **Fix:** Removed stale lines from all 8 files; added two sunset regression tests (`test_s3_pry_sunset_no_pry_methods_on_batch_mixin`, `test_rce_enabled_absent_from_scan_request`); removed orphaned `PryOperationsHarness` class and its `ServerListWindowBatchOperationsMixin` import.
4. **Files changed:**
   - `gui/tests/test_clamav_results_dialog.py` — removed `_set_pry_status_button_visible` mock
   - `gui/tests/_server_ops_harness.py` — deleted `PryOperationsHarness` class; removed unused `ServerListWindowBatchOperationsMixin` import
   - `gui/tests/test_server_ops_fuzz_sequences.py` — removed `"pry"` from job-type pool
   - `gui/tests/test_unified_scan_dialog_validation.py` — removed `show_rce_controls` param + `rce_enabled_var` + `show_rce_controls` attr from `_make_dialog`; added `test_rce_enabled_absent_from_scan_request`
   - `gui/tests/test_dashboard_scan_dialog_wiring.py` — removed `dash._rce_unlocked` from both test fixtures
   - `gui/tests/test_action_routing.py` — removed stale `_on_pry_selected` docstring line; removed `self._rce_unlocked` from `_BatchMixinStub`
   - `gui/tests/test_scan_preflight_probe_depth.py` — removed `"rce_enabled": False` from 4 fixture dicts
   - `gui/tests/test_server_ops_scenario_matrix.py` — added `test_s3_pry_sunset_no_pry_methods_on_batch_mixin`
   - `docs/dev/remove_old_tools/ROADMAP.md`, `TASK_CARDS.md`, `LESSONS_LEARNED.md` — status and evidence
5. **Validation run:**
   - `py_compile` all 8 touched modules: **PASS**
   - `test_action_routing.py`: **36 passed**
   - `test_server_ops_scenario_matrix.py`: **12 passed, 1 pre-existing failure** (`test_s10_se_dork_probe_task_lifecycle_success` — confirmed failing at C4 baseline via `git stash` rerun)
   - `test_unified_scan_dialog_validation.py`: **19 passed**
   - `test_dashboard_scan_dialog_wiring.py`: **2 passed**
   - `test_server_ops_fuzz_sequences.py`: **30 passed**
   - `test_clamav_results_dialog.py`: **25 passed**
   - `shared/tests/test_ftp_state_tables.py`: **13 passed**
   - `test_scan_preflight_probe_depth.py`: **5 passed**
   - Guardrail grep: only sunset assertions and intentional compat residuals (`pry_status_dialog` stubs, schema DDL fixtures)
6. **Result:** PASS — all targeted suites pass; no C5 regressions introduced; `test_s10` pre-existing.
7. **HI test needed?** No new manual HI steps. Pre-existing `test_s10` failure should be tracked separately (SE Dork lifecycle test, not Pry/RCE related).

**Line count rubric (post-C5):**

| File | Before | After | Rubric |
|---|---|---|---|
| `gui/tests/test_action_routing.py` | 1355 | 1353 | Good |
| `gui/tests/_server_ops_harness.py` | 641 | 614 | Excellent |
| `gui/tests/test_server_ops_scenario_matrix.py` | 509 | 524 | Excellent |
| `gui/tests/test_unified_scan_dialog_validation.py` | 392 | 400 | Excellent |
| `gui/tests/test_clamav_results_dialog.py` | 471 | 470 | Excellent |
| `gui/tests/test_dashboard_scan_dialog_wiring.py` | 90 | 88 | Excellent |
| `gui/tests/test_server_ops_fuzz_sequences.py` | 319 | 319 | Excellent |
| `gui/tests/test_scan_preflight_probe_depth.py` | 240 | 236 | Excellent |

No file exceeds 1700 lines.

**Guardrail grep residual classification:**

| File | Lines | Symbol | Classification |
|---|---|---|---|
| `test_server_ops_scenario_matrix.py` | 104–116 | `_on_pry_selected`, `_execute_pry_target` | **Sunset assertions** (C5 additions) |
| `test_unified_scan_dialog_validation.py` | 196–203 | `rce_enabled` | **Sunset assertion** (C5 addition) |
| `test_action_routing.py` | 40, 70 | `pry_status_dialog` stubs | **Intentional** — module still exists (shared) |
| `test_server_list_card4.py` | 60, 701, 713 | `pry_status_dialog` stubs | **Intentional** — same reason |
| `test_db_tools_engine*.py` | multiple | `source DEFAULT 'pry'`, `'pry'` VALUES | **Intentional** — legacy DB compat schema fixtures |

## C6 - Docs Sync + Lessons + Closeout

### Objective

Finalize documentation and closeout evidence.

### Required Changes

1. Update `README.md` and `docs/TECHNICAL_REFERENCE.md` to remove suspended-language and reflect full sunset.
2. Append final lessons and recurring pitfalls in `LESSONS_LEARNED.md`.
3. Add final consolidated validation report and residual risk list.

### Validation Focus

1. Doc statements match implemented code state.
2. Final guardrail grep results captured.

## Final Validation Report Template (For C6)

1. Card completion summary by C0-C6 with PASS/FAIL.
2. Exact commands run and outcomes.
3. Residual risks and recommended follow-up actions.
