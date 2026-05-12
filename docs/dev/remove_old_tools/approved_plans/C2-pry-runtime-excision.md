# C2 — Pry Runtime Excision

## Context

C1 is confirmed complete (no `_pry_unlocked`, `show_pry_controls`, or `rce_unlocked` remain in `dirracuda` entrypoint). C2 removes all Pry runtime flow from UI/action/job paths: the button, context menu, batch job type, credential persistence method, Pry-only modules, and their imports. Legacy DB rows with `source='pry'` remain readable; schema is untouched.

User confirmed: **minimal test surgery in C2** — remove Pry-specific test methods and broken imports from the 5 validation-suite test files so the suite can collect and pass. Deeper scenario matrix work stays in C5.

---

## Files to Delete (Pry-only, no remaining callers after edits)

- `gui/components/pry_dialog.py` (287 lines)
- `gui/utils/pry_runner.py` (346 lines)
- `gui/utils/wordlist_path.py` (71 lines)

Delete **after** all import references are removed and `py_compile` passes.

---

## Execution Order (dependency-safe)

1. `batch_operations.py` — removes `_on_pry_selected` and its imports
2. `batch.py` — removes `_execute_pry_target`, pry dispatch, pry gate
3. `batch_status.py` — removes `_persist_pry_success`, pry helpers, pry_button ref
4. `window.py` — removes pry button/menu UI, `_pry_unlocked`, PryDialog import
5. `app_config_dialog.py` — removes `show_pry_controls`, wordlist field plumbing
6. Minimal test surgery (5 test files)
7. Delete 3 Pry-only files

---

## File 1: `gui/components/server_list_window/actions/batch_operations.py` (1441 lines → ~1377)

**A** — Remove `PryDialog` import (line 23):
```
from gui.components.pry_dialog import PryDialog
```

**B** — Remove `pry_runner` from utils import (line 24):
```python
# Before
from gui.utils import probe_patterns, probe_runner, extract_runner, pry_runner, session_flags
# After
from gui.utils import probe_patterns, probe_runner, extract_runner, session_flags
```

**C** — In `_prompt_probe_batch_settings` (~line 1125), fix `_rce_unlocked` fallback:
```python
# Before
rce_unlocked = bool(getattr(self, "_rce_unlocked", getattr(self, "_pry_unlocked", False)))
# After
rce_unlocked = bool(getattr(self, "_rce_unlocked", False))
```

**D** — Delete entire `_on_pry_selected` method (lines 742–804, 63 lines). Boundary: starts at `    def _on_pry_selected(self) -> None:`, ends just before `    def _on_file_browser_selected(self) -> None:`. Remove the method and its preceding blank separator line.

---

## File 2: `gui/components/server_list_window/actions/batch.py` (776 lines → ~653)

**A** — Remove `PryDialog` import (line 29):
```
from gui.components.pry_dialog import PryDialog
```

**B** — Remove `pry_runner` from the multiline utils import (lines 35–41):
```python
# Before
from gui.utils import (
    probe_patterns,
    probe_runner,
    extract_runner,
    protocol_extract_runner,
    pry_runner,
)
# After
from gui.utils import (
    probe_patterns,
    probe_runner,
    extract_runner,
    protocol_extract_runner,
)
```

**C** — Remove pry gate and fix `rce_unlocked` fallback in `_start_batch_job` (lines 54–57):
```python
# Before
        if job_type == "pry" and not getattr(self, "_pry_unlocked", False):
            messagebox.showwarning("Pry Disabled", "Pry is disabled for this session.")
            return
        rce_unlocked = bool(getattr(self, "_rce_unlocked", getattr(self, "_pry_unlocked", False)))
# After
        rce_unlocked = bool(getattr(self, "_rce_unlocked", False))
```

**D** — Remove pry dialog init block in `_start_batch_job` (lines 110–123). Change the `if job_type == "pry":` branch + its body, and promote the following `elif job_type == "probe":` to `if job_type == "probe":`.

**E** — Remove pry routing in `_run_batch_task` (lines 177–178):
```python
            if job_type == "pry":
                return self._execute_pry_target(job_id, target, options, cancel_event)
```

**F** — In `_execute_probe_target` (~line 379), fix `_rce_unlocked` fallback:
```python
# Before
        rce_unlocked = bool(getattr(self, "_rce_unlocked", getattr(self, "_pry_unlocked", False)))
# After
        rce_unlocked = bool(getattr(self, "_rce_unlocked", False))
```

**G** — Delete entire `_execute_pry_target` method (lines 669–771, ~103 lines). Boundary: starts at `    def _execute_pry_target(`, ends just before the blank line + `    # Probe status helpers` comment. Remove the method and its preceding blank separator.

---

## File 3: `gui/components/server_list_window/actions/batch_status.py` (999 lines → ~870)

**A** — Remove `"pry": "Pry"` from job type label map in `_build_batch_task_name` (line 128):
```python
# Before
            label = {
                "probe": "Probe",
                "extract": "Extract",
                "pry": "Pry",
            }.get(...)
# After
            label = {
                "probe": "Probe",
                "extract": "Extract",
            }.get(...)
```

**B** — Delete `_is_pry_batch_active` method (lines 355–356), 2 lines:
```python
        def _is_pry_batch_active(self) -> bool:
            return any(job.get("type") == "pry" and ...)
```

**C** — Delete `_set_pry_status_button_visible` and `_show_pry_status_dialog` methods (lines 371–382). Both are no-ops / delegators that only serve the Pry flow.

**D** — Delete entire `_persist_pry_success` method (lines 421–533, ~113 lines). Boundary: starts at `        def _persist_pry_success(`, ends at `            finally:\n                conn.close()`. Remove the method and its preceding blank separator.

**E** — Remove `self.pry_button` from button tuple in `_update_action_buttons_state` (line 541):
```python
# Before
            for button in (self.probe_button, self.extract_button, self.pry_button):
# After
            for button in (self.probe_button, self.extract_button):
```

---

## File 4: `gui/components/server_list_window/window.py` (1237 lines → ~1217)

**A** — Remove `PryDialog` import (line 29):
```
from gui.components.pry_dialog import PryDialog
```

**B** — Remove `pry_runner` from module-level utils import (line 70):
```python
# Before
from gui.utils import probe_cache, probe_patterns, probe_runner, extract_runner, pry_runner
# After
from gui.utils import probe_cache, probe_patterns, probe_runner, extract_runner
```

**C** — Remove `_pry_unlocked` init and fix `_rce_unlocked` fallback (lines 105–106):
```python
# Before
        self._pry_unlocked = bool(self.window_data.get("_pry_unlocked", False))
        self._rce_unlocked = bool(self.window_data.get("_rce_unlocked", self._pry_unlocked))
# After
        self._rce_unlocked = bool(self.window_data.get("_rce_unlocked", False))
```
**Risk:** Without this fix the `_rce_unlocked` line references undefined `self._pry_unlocked` at runtime.

**D** — Remove `self.pry_button = None` init (line 156).

**E** — Remove `self.pry_status_button = None` init (line 167).

**F** — Remove pry context menu entry (lines 577–578):
```python
        if self._pry_unlocked:
            _add_selection_command("🔓 Pry Selected", self._on_pry_selected)
```

**G** — Remove pry button creation block (lines 846–859, entire if/else, 14 lines):
```python
        if self._pry_unlocked:
            self.pry_button = tk.Button(...)
            ...
        else:
            pry_spacer = tk.Frame(button_container, width=120)
            ...
            pry_spacer.pack_propagate(False)
```
`self.delete_button = tk.Button(...)` that follows becomes the direct continuation.

---

## File 5: `gui/components/app_config_dialog.py` (1376 lines → ~1314)

**A** — Remove `normalize_wordlist_path` import (line 31):
```
from gui.utils.wordlist_path import normalize_wordlist_path
```

**B** — Remove `show_pry_controls: bool = False,` from `__init__` signature (line 150).

**C** — Remove `self.show_pry_controls = bool(show_pry_controls)` (line 157).

**D** — Remove `self.wordlist_path = ""` (line 165).

**E** — Remove `"wordlist": {"valid": False, "message": ""},` from `validation_results` dict (~line 180).

**F** — Remove `self.wordlist_var: Optional[tk.StringVar] = None` attribute init (~line 191).

**G** — Remove wordlist loading from `_load_runtime_settings_from_config` (lines 268–269):
```python
        raw_wordlist = str(_get_nested(config_data, ("pry", "wordlist_path"), "") or "")
        self.wordlist_path = normalize_wordlist_path(raw_wordlist, config_path=path_obj)
```

**H** — Simplify `runtime_fields` in `_create_sections` (line 385):
```python
# Before
        runtime_fields = ("api_key", "quarantine", "wordlist") if self.show_pry_controls else ("api_key", "quarantine")
# After
        runtime_fields = ("api_key", "quarantine")
```

**I** — Remove `"wordlist": "Pry Wordlist Path",` from `FIELD_LABELS` class attr (~line 137).

**J** — Remove `"wordlist"` from `browse_needed` set in `_create_field_row` (~line 654):
```python
# Before
        browse_needed = field in {"smbseek", "database", "config", "quarantine", "wordlist"}
# After
        browse_needed = field in {"smbseek", "database", "config", "quarantine"}
```

**K** — Remove wordlist branch from `_field_var` (~lines 761–764):
```python
        if field == "wordlist":
            if self.wordlist_var is None:
                self.wordlist_var = tk.StringVar(value=self.wordlist_path)
            return self.wordlist_var
```

**L** — Remove wordlist branch from `_browse_path` (~lines 836–848, `if field == "wordlist"` block).

**M** — Remove wordlist branch from `_validate_field` (~lines 902–907, `if field == "wordlist"` block).

**N** — Delete `_validate_wordlist_path` method (~lines 1022–1033).

**O** — Simplify `_validate_all_fields` (~lines 1047–1052):
```python
# Before
    def _validate_all_fields(self) -> None:
        fields = ["smbseek", "database", "config", "api_key", "quarantine"]
        if self.show_pry_controls:
            fields.append("wordlist")
        for field in fields:
            self._validate_field(field)
# After
    def _validate_all_fields(self) -> None:
        for field in ["smbseek", "database", "config", "api_key", "quarantine"]:
            self._validate_field(field)
```

**P** — Remove `new_wordlist` assignment in `_validate_and_save` (~lines 1122–1125):
```python
        if self.show_pry_controls and self.wordlist_var is not None:
            new_wordlist = self.wordlist_var.get().strip()
        else:
            new_wordlist = self.wordlist_path
```

**Q** — Remove `new_wordlist` positional arg from both `_apply_runtime_settings` call sites (~lines 1201–1208, ~1223–1230). Each call site passes `new_wordlist` as the 3rd positional arg; remove it.

**R** — Remove `self.wordlist_path = new_wordlist` update (~line 1240).

**S** — Remove wordlist warning block (~lines 1272–1278):
```python
            if self.show_pry_controls and not self.validation_results["wordlist"]["valid"]:
                messagebox.showwarning(...)
```

**T** — Make `wordlist_path` a deprecated optional kwarg in `_apply_runtime_settings` signature (~line 1322) — keep it for backward compat so existing callers (`test_app_config_dialog_tmpfs.py` line 105, `test_app_config_dialog_clamav.py` line 107) don't break with `TypeError`. Remove `_set_nested(config_data, ("pry", "wordlist_path"), wordlist_path)` from its body (~line 1334) so it no longer writes the pry config key. Do NOT remove the param signature itself in C2.

```python
# Before
    def _apply_runtime_settings(
        self,
        config_data: Dict[str, Any],
        api_key: str,
        quarantine_path: str,
        wordlist_path: str,
        clamav_settings: Optional[Dict[str, Any]] = None,
        quarantine_tmpfs_settings: Optional[Dict[str, Any]] = None,
    ) -> None:
# After
    def _apply_runtime_settings(
        self,
        config_data: Dict[str, Any],
        api_key: str,
        quarantine_path: str,
        wordlist_path: str = "",
        clamav_settings: Optional[Dict[str, Any]] = None,
        quarantine_tmpfs_settings: Optional[Dict[str, Any]] = None,
    ) -> None:
```

Remove only the `_set_nested(config_data, ("pry", "wordlist_path"), wordlist_path)` line from the body — the parameter itself stays as an ignored optional arg. Tests in `test_app_config_dialog_tmpfs.py` and `test_app_config_dialog_clamav.py` that pass `wordlist_path` positionally continue to work.

**U** — Remove `show_pry_controls: bool = False,` from `open_app_config_dialog` factory function (~line 1359) and its use `show_pry_controls=show_pry_controls,` inside the function (~line 1369).

---

## Minimal Test Surgery (enables validation suite to collect and pass)

### `gui/tests/test_app_config_dialog.py`

- Remove import line 14: `from gui.components.pry_dialog import PryDialog`
- Remove lines 75, 84, 94 from `_build_dialog` helper (`show_pry_controls`, `wordlist_var`, `wordlist_path` assignments — harmless dynamic attrs but confusing dead code)
- Update `_base_validation` to remove `wordlist_valid` param and `"wordlist"` key (only used by deleted tests)
- Delete `test_validate_and_save_wordlist_warning_uses_dialog_parent` (lines ~152–168)
- Delete `test_validate_and_save_skips_wordlist_warning_when_pry_controls_hidden` (lines ~170–178)
- Delete `test_app_config_load_clears_missing_legacy_wordlist` (lines ~374–390)
- Delete `test_app_config_load_preserves_existing_legacy_wordlist` (lines ~392–410)
- Delete `test_pry_defaults_clear_missing_legacy_wordlist` (lines ~413–428)
- Delete `test_pry_defaults_preserve_custom_wordlist` (lines ~430–445)
- Keep `test_default_gui_settings_wordlist_is_blank` and `test_config_example_wordlist_is_blank` — these test config defaults not changed until C4/C5.

### `gui/tests/test_action_routing.py`

- Remove `"gui.components.pry_dialog"` stub entry from `_stub_module` setup (lines ~42, 72). Keep `pry_status_dialog` stub — it's shared.
- Remove `self.pry_button = None` and `self.pry_status_button = None` from test harness class body if they appear in the base harness (not needed after C2 removes these from `window.py` init).
- Remove `self._pry_unlocked = True` from harness if it appears only in pry-test context.
- Delete entire pry test block (lines 1206–1266): `test_pry_blocked_for_ftp_row`, `test_pry_blocked_when_session_locked`, `test_start_batch_job_pry_blocked_when_session_locked`, and their section comment.

### `gui/tests/test_app_config_dialog_dorks.py`

- Delete `test_validate_all_fields_includes_wordlist_when_pry_controls_enabled` (lines ~30–38).
- Remove `wordlist_path="/tmp/words.txt"` kwarg from `_apply_runtime_settings` call (~line 60). After C2 the method no longer accepts this parameter; passing it raises `TypeError`.

### `gui/tests/test_server_list_card4.py`

- Remove `"gui.components.pry_dialog": {"PryDialog": ...}` stub entry from fixture setups (lines ~60, 702–716). Keep `"gui.components.pry_status_dialog"` stubs — shared dialog.

### `gui/tests/test_server_ops_scenario_matrix.py`

- No collection error (test uses stubs pre-patched before imports). Only Pry-specific test methods fail at runtime. Delete `test_s3_pry_mixed_selection_blocks_launch` (lines ~106–121).

---

## Files NOT Touched in C2

- `gui/components/pry_status_dialog.py` — shared; do not delete or modify
- `shared/db_migrations.py` — schema untouched
- `gui/utils/default_gui_settings.py` — `'pry'` defaults stay until C4
- `conf/config.json.example` — pry block stays until C4

---

## Validation Sequence

```bash
# 1. Path allowlist (scoped to touched files only)
TOUCHED=(
  gui/components/server_list_window/window.py
  gui/components/server_list_window/actions/batch_operations.py
  gui/components/server_list_window/actions/batch.py
  gui/components/server_list_window/actions/batch_status.py
  gui/components/app_config_dialog.py
  gui/tests/test_app_config_dialog.py
  gui/tests/test_action_routing.py
  gui/tests/test_app_config_dialog_dorks.py
  gui/tests/test_server_list_card4.py
  gui/tests/test_server_ops_scenario_matrix.py
  gui/components/pry_dialog.py
  gui/utils/pry_runner.py
  gui/utils/wordlist_path.py
)
git status --short -- "${TOUCHED[@]}"

# 2. Line counts (all must stay under 1800)
wc -l gui/components/server_list_window/window.py \
       gui/components/server_list_window/actions/batch_operations.py \
       gui/components/server_list_window/actions/batch.py \
       gui/components/server_list_window/actions/batch_status.py \
       gui/components/app_config_dialog.py

# 3. Compile all touched modules
./venv/bin/python -m py_compile \
    gui/components/server_list_window/window.py \
    gui/components/server_list_window/actions/batch_operations.py \
    gui/components/server_list_window/actions/batch.py \
    gui/components/server_list_window/actions/batch_status.py \
    gui/components/app_config_dialog.py

# 4. Targeted tests
xvfb-run -a ./venv/bin/python -m pytest \
    gui/tests/test_action_routing.py \
    gui/tests/test_server_ops_scenario_matrix.py \
    gui/tests/test_app_config_dialog.py \
    gui/tests/test_app_config_dialog_dorks.py \
    gui/tests/test_server_list_card4.py \
    -q

# 5. Pry guardrail grep (report all hits; expected residuals documented below)
rg -n -i "pry|PryDialog|pry_runner|_on_pry_selected|_execute_pry_target|show_pry_controls|wordlist_path" \
    dirracuda gui shared cli commands conf
```

**Expected guardrail residuals after C2 (not errors):**
- `shared/db_migrations.py` — `source DEFAULT 'pry'` schema line (intentionally preserved)
- `gui/utils/default_gui_settings.py` — `'pry': {'wordlist_path': ''}` (C4 scope)
- `conf/config.json.example` — pry block (C4 scope)
- `gui/tests/test_app_config_dialog.py` — `test_default_gui_settings_wordlist_is_blank`, `test_config_example_wordlist_is_blank` (test config defaults, not active runtime)
- Any `docs/` paths — C6 scope
- `gui/components/pry_status_dialog.py` — intentionally preserved (shared dialog)

---

## Report Format (fill in during execution)

1. **Issue:** C2 Pry Runtime Excision
2. **Root cause:** Pry credential audit feature retired; code still wired into UI/job/action paths
3. **Fix:** (listed above)
4. **Files changed:** (list)
5. **Validation run:** (exact commands + PASS/FAIL each)
6. **Result:** PASS/FAIL
7. **HI test needed?** Yes — launch `./dirracuda`, open Server List, verify no Pry button/menu entry visible; open Settings dialog and verify no Pry Wordlist field.
