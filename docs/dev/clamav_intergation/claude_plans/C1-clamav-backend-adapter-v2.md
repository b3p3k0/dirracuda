# Plan: Card C1 — ClamAV Backend Adapter

## Context

Both target files already exist as untracked in the repo:
- `shared/clamav_scanner.py`
- `shared/tests/test_clamav_scanner.py`

They were drafted prior to this session. The implementation passes 29/30 spec checks and all 16 test coverage targets. One fix is required before validation.

## Issue Found

**`subprocess.Popen` missing explicit `shell=False`**

The spec requires:
```python
subprocess.Popen([...], shell=False, stdout=PIPE, stderr=PIPE, text=True, errors="replace")
```

The current call omits `shell=False` (defaults to `False` implicitly — correct behavior but not spec-compliant).

Location: `shared/clamav_scanner.py`, `_invoke()`, line 90.

## Fix

Add `shell=False` to the existing Popen call:

```python
proc = subprocess.Popen(
    [binary_path, "--no-summary", path],
    shell=False,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    errors="replace",
)
```

The existing `test_shell_false_always` test already validates this (checks `captured_kwargs.get("shell") is not True`) — the test will continue to pass either way, but the explicit parameter satisfies the spec requirement.

## Fix 2 — Tighten test_shell_false_always assertion

Current (passes even when `shell` is omitted):
```python
assert captured_kwargs.get("shell") is not True
```

Required (fails if `shell=False` is not explicitly passed):
```python
assert captured_kwargs["shell"] is False
```

Location: `shared/tests/test_clamav_scanner.py`, `test_shell_false_always`.

## Files Changed

| File | Action |
|------|--------|
| `shared/clamav_scanner.py` | Edit: add `shell=False` to Popen call (~line 91) |
| `shared/tests/test_clamav_scanner.py` | Edit: tighten assertion in `test_shell_false_always` |

## Validation

```bash
python3 -m py_compile shared/clamav_scanner.py
./venv/bin/python -m pytest shared/tests/test_clamav_scanner.py -q
```

Expected: compile clean, all tests pass.

## Output Format (as requested)

- **Issue:** Missing explicit `shell=False` in `subprocess.Popen` call in `_invoke()`
- **Root cause:** Spec requires explicit param; default is `False` but not stated
- **Fix:** Add `shell=False` to Popen keyword args
- **Files changed:** `shared/clamav_scanner.py` only
- **Validation run:** py_compile + pytest
- **HI test needed:** No (unit-level, no real ClamAV binary needed)
