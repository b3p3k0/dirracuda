# C6 QA fix: guard clamav_timeout_var in _validate_and_save

## Context

C6 is complete except for one regression found in QA:
`_validate_and_save()` reads `self.clamav_timeout_var.get()` unconditionally at line 825,
while every other ClamAV var read in the same block already uses an `if self.xxx_var`
guard. Lightweight test stubs (used by `test_app_config_dialog.py`) never call
`_create_clamav_card`, so `clamav_timeout_var` is `None` → `AttributeError`.

## Fix

Single line change in `gui/components/app_config_dialog.py` around line 824–827:

```python
# Before
try:
    _clamav_timeout = max(1, int(self.clamav_timeout_var.get()))
except (TypeError, ValueError):
    _clamav_timeout = 60

# After
_timeout_var = getattr(self, "clamav_timeout_var", None)
try:
    _clamav_timeout = max(1, int(_timeout_var.get())) if _timeout_var else 60
except (TypeError, ValueError):
    _clamav_timeout = 60
```

`getattr` with a default handles both cases: attribute absent entirely (`__new__`-constructed
stubs) and attribute present but `None` (normal non-UI paths).

## Validation

```bash
./venv/bin/python -m pytest gui/tests/test_app_config_dialog.py gui/tests/test_app_config_dialog_clamav.py -q
```

Expected: all pass, 0 failures.
