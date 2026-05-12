# C4 Compatibility Cleanup — Implementation Plan

## Context

C0–C3 removed Pry and RCE runtime flows, unlock gates, UI/CLI wiring, and runtime modules. C4 cleans up the remaining active config defaults, data-access writers, and read projections that referenced those removed runtimes — without touching schema, migrations, or historical rows.

**Hard constraints:**
- No `shared/db_migrations.py` edits (no schema drops)
- No `share_credentials.source DEFAULT 'pry'` change
- No commit unless HI explicitly says "commit"
- Stop if any target file would exceed 1700 lines after edits

---

## Pre-Edit Baseline Line Counts

| File | Current |
|---|---|
| `conf/config.json.example` | 257 |
| `gui/utils/default_gui_settings.py` | 170 |
| `shared/config.py` | 687 |
| `shared/database.py` | 813 |
| `gui/utils/database_access.py` | 100 |
| `gui/utils/database_access_core_methods.py` | 957 |
| `gui/utils/database_access_protocol_methods.py` | 792 |
| `gui/utils/database_access_write_methods.py` | 1623 |
| `gui/components/app_config_dialog.py` | 1308 |

---

## Edit Sequence

### 1. `conf/config.json.example`

**Remove the `"pry"` block (lines 64–71):**
```json
  "pry": {
    "wordlist_path": "",
    "user_as_pass": true,
    "stop_on_lockout": true,
    "verbose": false,
    "attempt_delay": 1.0,
    "max_attempts": 0
  },
```
The surrounding `"output"` closing `},` and `"_note"` key remain; no comma fixup needed.

**Remove the `"rce"` block (lines 238–256) AND fix trailing comma on `"clamav"`:**
- Change `"clamav"` closing brace (line 237) from `  },` → `  }` (it becomes the last property)
- Remove lines 238–256 (`"rce": { ... }`)

Expected post-edit: ~230 lines.

---

### 2. `gui/utils/default_gui_settings.py`

**Remove `'rce_enabled': False` from `scan_dialog` block (line 59).**

**Remove `'rce_enabled': False` from `unified_scan_dialog` block (line 73).**

**Remove entire `'pry'` block (lines 117–124):**
```python
    'pry': {
        'wordlist_path': '',
        'user_as_pass': True,
        'stop_on_lockout': True,
        'verbose': False,
        'attempt_delay': 1.0,
        'max_attempts': 0
    },
```

Expected post-edit: ~160 lines.

---

### 3. `shared/config.py`

**Remove `_DEFAULT_CONFIG_RCE_JSONL_PATH` constant (line 25):**
```python
_DEFAULT_CONFIG_RCE_JSONL_PATH = "~/.dirracuda/logs/rce_analysis.jsonl"
```

**Remove the entire "RCE Module Configuration Methods" block (lines 573–637):**
All seven methods: `get_rce_config`, `get_rce_safe_budget`, `is_rce_enabled_by_default`, `is_intrusive_mode_enabled`, `get_rce_logging_path`, `is_ms17_010_enabled`, `is_smbghost_enabled`.

Expected post-edit: ~620 lines.

---

### 4. `shared/database.py`

**Remove `upsert_rce_status()` method (lines 755–794).**
The `close()` method immediately after stays.

Expected post-edit: ~774 lines.

---

### 5. `gui/utils/database_access.py`

**Remove `_ensure_rce_columns()` call block in `__init__` (lines 69–73):**
```python
        # Ensure new RCE columns exist even on older databases (idempotent)
        try:
            self._ensure_rce_columns()
        except Exception:
            pass
```
The `_ensure_http_columns()` call block immediately below stays.

Expected post-edit: ~95 lines.

---

### 6. `gui/utils/database_access_core_methods.py`

**6a. Remove `_ensure_rce_columns()` function definition (lines 20–40).**
`_ensure_http_columns` at line 42 stays.

**6b. Remove `"_ensure_rce_columns"` from `method_names` tuple in `bind_database_access_core_methods` (line 927).**

**6c. Remove `rce_status` from `_load_probe_cache_map()` SQL SELECT (line 875):**
```python
    SELECT s.ip_address, pc.status, pc.indicator_matches, pc.extracted, pc.rce_status
```
→ remove `, pc.rce_status`

**6d. Remove `rce_status` from `_load_probe_cache_map()` return dict (line 885):**
```python
            "rce_status": row["rce_status"] or "not_run",
```

**6e. Remove `rce_status` from primary server list result builder (line 742):**
```python
            "rce_status": probe.get("rce_status", "not_run"),
```

**6f. Remove `rce_status` from legacy server list result builder (line 852):**
```python
            "rce_status": probe.get("rce_status", "not_run"),
```

Note: 6c/6d must be done together. 6e/6f can follow. All four eliminate the read-then-forward chain that feeds `rce_status` into server list result dicts.

Expected post-edit: ~949 lines.

---

### 7. `gui/utils/database_access_protocol_methods.py`

**7a. Remove `rce_status` from UNION ALL SMB arm (line 350):**
```python
        COALESCE(pc.rce_status, 'not_run')          AS rce_status
```

**7b. Remove `rce_status` from UNION ALL FTP arm (line 408):**
```python
        COALESCE(fpc.rce_status, 'not_run') AS rce_status
```

**7c. Remove `rce_status` from UNION ALL HTTP arm (line 461):**
```python
        COALESCE(hpc.rce_status, 'not_run')       AS rce_status
```

**7d. Remove `rce_status` from SMB-only SQL arm (line 624):**
```python
            COALESCE(pc.rce_status, 'not_run')          AS rce_status
```

**7e. Remove `"rce_status": "not_run"` from mock SMB row (line 735).**

**7f. Remove `"rce_status": "not_run"` from mock FTP row (line 760).**

All UNION ALL arms drop from 23 to 22 columns consistently. The SMB-only arm drops likewise. Mock data dicts shrink by one key each.

Expected post-edit: ~786 lines.

---

### 8. `gui/utils/database_access_write_methods.py`

**8a. Remove `probe_snapshot_rce` DELETE in `upsert_probe_snapshot_for_host` (line 356):**
```python
            cur.execute("DELETE FROM probe_snapshot_rce WHERE snapshot_id = ?", (snapshot_id,))
```
The sibling DELETEs for `probe_snapshot_entries` and `probe_snapshot_errors` stay.

**8b. Remove the `rce_analysis` INSERT block (lines 398–414):**
```python
            rce = snapshot.get("rce_analysis")
            if isinstance(rce, dict) and rce:
                cur.execute(
                    """
                    INSERT INTO probe_snapshot_rce
                        (snapshot_id, rce_status, verdict_summary, analysis_json, created_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        snapshot_id,
                        rce.get("rce_status"),
                        json.dumps(rce.get("verdict_summary"), default=str)
                        if isinstance(rce.get("verdict_summary"), (dict, list))
                        else rce.get("verdict_summary"),
                        json.dumps(rce, default=str),
                    ),
                )
```

**8c. Remove the `# --- RCE status helpers ---` section and all four functions (lines 1477–1545):**
- `get_rce_status()`
- `get_rce_status_for_host()`
- `upsert_rce_status()` (SMB shim)
- `upsert_rce_status_for_host()`

**8d. Remove four entries from `method_names` tuple in `bind_database_access_write_methods` (~lines 1602, 1614–1616):**
```python
        "upsert_rce_status_for_host",
        ...
        "get_rce_status",
        "get_rce_status_for_host",
        "upsert_rce_status",
```

Expected post-edit: ~1555 lines. (Well within 1700 limit.)

---

### 9. `gui/components/app_config_dialog.py`

**Remove `wordlist_path: str = ""` parameter from `_apply_runtime_settings` (line 1257).**
The function body has no references to `wordlist_path`; no body edits needed.

Expected post-edit: ~1307 lines.

---

## Minimal C4 Test Surgery

Only tests that directly call removed methods or assert on removed config keys need updates.

### `gui/tests/test_app_config_dialog.py`

Remove two functions that will KeyError after `DEFAULT_GUI_SETTINGS["pry"]` is removed:
- `test_default_gui_settings_wordlist_is_blank` (lines 328–329)
- `test_config_example_wordlist_is_blank` (lines 332–335)

### `gui/tests/test_app_config_dialog_clamav.py`

**Will break (TypeError: too many positional args) if missed.**
Fix positional arg in `_apply()` helper (line 107). Remove 4th positional `""` (was `wordlist_path`):
```python
# Before
dlg._apply_runtime_settings(config_data, "", "", "", clamav_settings=clamav)
# After
dlg._apply_runtime_settings(config_data, "", "", clamav_settings=clamav)
```

### `gui/tests/test_app_config_dialog_tmpfs.py`

**Will break (unexpected keyword argument) if missed.**
- Line 60: Remove `dlg.wordlist_path = ""` (stale attribute setup)
- Line 109: Remove `wordlist_path=""` keyword arg from `_apply_runtime_settings` call

### `gui/tests/test_database_access_protocol_writes.py`

Remove four test functions that will AttributeError on removed methods:
- `test_rce_protocol_isolation` (lines 358–384)
- `test_wrapper_upsert_rce_status_defaults_to_smb` (lines 470–488+)
- `test_rce_invalid_status_normalized_to_unknown` (lines 664–685)
- `test_rce_for_host_ftp_missing_tables_no_exception` (lines 892–910)

The `_reader` fixture docstring at line 177 mentions `_ensure_rce_columns no-op'd` — leave stale wording for C5/C6.

### `gui/tests/test_database_access_protocol_union.py`

Remove two test functions that will AttributeError on `get_rce_status_for_host`:
- `test_get_rce_status_for_host_smb` (lines 608–621)
- `test_get_rce_status_for_host_ftp` (lines 626–647)

The `rce_status TEXT DEFAULT 'not_run'` lines in schema fixtures (lines 70–71, 125–126) **stay** — those define the actual table columns, which are preserved for DB compat.

---

## Not in C4 Scope

- `shared/db_migrations.py` — no edits (schema must remain compatible)
- `shared/path_service.py` — `rce_analysis_log_file` stays (layout migration system)
- `gui/utils/probe_cache.py` — `rce_analysis: None` compat shim stays (C5)
- `gui/utils/probe_snapshot_details.py` — `_format_rce_summary` stays (C5)
- `test_action_routing.py:1120` — stale `"rce_status": None` in manually-built dict, won't break (C5)
- `test_scan_preflight_probe_depth.py` — `rce_enabled: False` in manually-built dicts, won't break
- `test_unified_scan_dialog_validation.py:89` — `dlg.rce_enabled_var` on stub, won't break

---

## Verification

```bash
# 1. JSON config sanity check
./venv/bin/python -c "
import json, pathlib
json.loads(pathlib.Path('conf/config.json.example').read_text())
print('config.json.example: valid JSON')
"

# 2. Syntax check all edited Python files
./venv/bin/python -m py_compile \
  gui/utils/default_gui_settings.py \
  shared/config.py \
  shared/database.py \
  gui/utils/database_access.py \
  gui/utils/database_access_core_methods.py \
  gui/utils/database_access_protocol_methods.py \
  gui/utils/database_access_write_methods.py \
  gui/components/app_config_dialog.py \
  && echo "compile: PASS"

# 3. Core routing test
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_action_routing.py -v \
  || ./venv/bin/python -m pytest gui/tests/test_action_routing.py -v

# 4. Edited test files
xvfb-run -a ./venv/bin/python -m pytest \
  gui/tests/test_database_access_protocol_writes.py \
  gui/tests/test_database_access_protocol_union.py \
  gui/tests/test_app_config_dialog.py \
  gui/tests/test_app_config_dialog_clamav.py \
  gui/tests/test_app_config_dialog_tmpfs.py \
  gui/tests/test_app_config_dialog_dorks.py \
  -v \
  || ./venv/bin/python -m pytest \
  gui/tests/test_database_access_protocol_writes.py \
  gui/tests/test_database_access_protocol_union.py \
  gui/tests/test_app_config_dialog.py \
  gui/tests/test_app_config_dialog_clamav.py \
  gui/tests/test_app_config_dialog_tmpfs.py \
  gui/tests/test_app_config_dialog_dorks.py \
  -v

# 5. Baseline guardrail tests
xvfb-run -a ./venv/bin/python -m pytest \
  gui/tests/test_probe_cache_dispatch.py \
  gui/tests/test_unified_scan_dialog_validation.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  -v \
  || ./venv/bin/python -m pytest \
  gui/tests/test_probe_cache_dispatch.py \
  gui/tests/test_unified_scan_dialog_validation.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  -v

# 5b. Legacy DB compatibility test
./venv/bin/python -m pytest shared/tests/test_ftp_state_tables.py -v

# 6. Grep guardrails — should return zero active-code hits
grep -rn "upsert_rce_status\|get_rce_status\|_ensure_rce_columns\|get_rce_config\|is_rce_enabled_by_default\|is_intrusive_mode_enabled\|is_ms17_010_enabled\|is_smbghost_enabled\|get_rce_logging_path\|get_rce_safe_budget" \
  shared/ gui/ cli/ --include="*.py" | grep -v "test_" | grep -v ".pyc"

# 7. Config key guardrail
grep -n '"pry"\|"rce"\|rce_enabled' conf/config.json.example
grep -n "'pry'\|rce_enabled" gui/utils/default_gui_settings.py

# 8. Post-edit line counts (all must be ≤ 1700)
wc -l \
  gui/utils/database_access_write_methods.py \
  gui/utils/database_access_core_methods.py \
  gui/utils/database_access_protocol_methods.py \
  shared/config.py \
  shared/database.py \
  gui/components/app_config_dialog.py
```

---

## Actual Results (executed 2026-05-12)

| Check | Result |
|---|---|
| `conf/config.json.example` valid JSON | PASS |
| Python compile (9 files) | PASS |
| Edited test files (84 tests) | PASS |
| Baseline guardrail tests (84 tests) | PASS |
| Legacy DB compat (`test_ftp_state_tables`, 13 tests) | PASS |
| Grep guardrail — zero active-code RCE references | PASS |
| Config key guardrail | PASS |

**Post-edit line counts:**

| File | Before | After |
|---|---|---|
| `database_access_write_methods.py` | 1623 | 1464 |
| `database_access_core_methods.py` | 957 | 931 |
| `database_access_protocol_methods.py` | 792 | 786 |
| `shared/config.py` | 687 | 618 |
| `shared/database.py` | 813 | 772 |
| `app_config_dialog.py` | 1308 | 1307 |

## HI Test Instructions

After implementation:
1. Run `./venv/bin/python -m pytest` — all tests should pass with the four RCE write tests and two RCE read tests removed.
2. Run grep guardrails above — all should be zero hits.
3. Confirm `conf/config.json.example` has no `"pry"` or `"rce"` top-level keys and is valid JSON.
4. Start the GUI with `./dirracuda` against a real (or test) DB — server list, scan dialog, config dialog must open and function normally.
