# C3 Bug Fix: dashboard.py clamav_cfg reset

## Context

C3 was implemented and all tests pass green, but a post-review functional finding identified that `_execute_batch_extract` in dashboard.py always forwards an empty `clamav_cfg` to workers. The value loaded from config at line 2236 is immediately overwritten by a re-declaration at line 2240, so ClamAV is effectively always disabled in the dashboard bulk extract path despite a valid config.

## Root Cause

```python
# line 2236 — inside try block
clamav_cfg = config_data.get("clamav", {})   # ← loads correctly
# ... except / pass ...

clamav_cfg: Dict[str, Any] = {}               # ← line 2240: RESETS to empty, wiping line 2236
results = []
```

The initialization that was meant to be the default declaration was placed after the config-loading block, not before it.

## Fix

File: `gui/components/dashboard.py`

Move the declaration `clamav_cfg: Dict[str, Any] = {}` to just before the `if config_path` block (alongside the other defaults: `included_extensions`, `excluded_extensions`, `quarantine_base_path`). Remove the erroneous line 2240 re-declaration.

Result:
```python
included_extensions: List[str] = []
excluded_extensions: List[str] = []
quarantine_base_path: Optional[Path] = None
clamav_cfg: Dict[str, Any] = {}           # ← default here
config_path = ...
if config_path and Path(config_path).exists():
    try:
        ...
        clamav_cfg = config_data.get("clamav", {})   # ← overrides default when present
    except Exception:
        pass
# no re-declaration after this point
results = []
```

## Additional test

Add one test to `gui/tests/test_extract_runner_clamav.py`:

`test_execute_batch_extract_forwards_nonempty_clamav_cfg` — patch `_extract_single_server` to capture its positional args; create a fake config file containing `{"clamav": {"enabled": true}}`; call `_execute_batch_extract` with that config path wired through `settings_manager`; assert the captured `clamav_config` arg is `{"enabled": True}` (not `{}`).

Because `_execute_batch_extract` creates a Tk progress dialog, mock `tk.Toplevel` and the progress widgets, or use the existing `object.__new__` pattern with the relevant attributes stubbed out.

## Verification

```bash
python3 -m py_compile gui/components/dashboard.py
./venv/bin/python -m pytest gui/tests/test_extract_runner_clamav.py -v
```

All 22 existing tests must still pass; new test must also pass.
