# C8 - SMB Windows Basename Compatibility (Approved Plan)

## Baseline

- Branch: `development`
- Implementation baseline: `be48cc2`
- Product target: `shared/smb_browser.py`
- Product line count before: 363
- Existing focused test path from the task card did not exist.

## Objective

Use Windows path semantics for unstructured SMB downloads. Reject empty,
root-only, and traversal basenames before local filesystem mutation or
`getFile`. Structured downloads must not change.

## Root Cause

`SMBNavigator._normalize_path()` produces backslash-separated SMB paths.
`Path(norm_path).name` uses POSIX semantics on Linux, so
`\folder\file.txt` remains one literal filename instead of becoming
`file.txt`.

## Behavior

For `preserve_structure=False`:

1. Compute the local filename with `PureWindowsPath(norm_path).name`.
2. Reject `""`, `"."`, and `".."` with `ValueError`.
3. Reject before creating `dest_dir`, opening a local file, or calling
   `conn.getFile`.
4. Pass the unchanged normalized SMB path to `conn.getFile`.

The `preserve_structure=True` branch retains its existing `_safe_parts()` and
`joinpath()` behavior.

## Files

- `shared/smb_browser.py`
  - Import `PureWindowsPath`.
  - Validate the non-structured basename before `mkdir`.
- `shared/tests/test_smb_browser.py`
  - New focused owner for the shared navigator behavior.

## Tests

Cover:

- `\folder\file.txt` saving as `file.txt`;
- deeper Windows paths collapsing to one basename;
- empty, root-only, and `..` rejection before filesystem or SMB activity;
- unchanged remote path passed to `getFile`;
- unchanged structured layout.

```bash
./venv/bin/python -m pytest shared/tests/test_smb_browser.py -q
./venv/bin/python -m pytest gui/tests/test_browser_clamav.py -q
./venv/bin/python -m py_compile \
  shared/smb_browser.py \
  shared/tests/test_smb_browser.py
git diff --check
```

## Documentation And Line Risk

The product file remains below 400 lines. README and Technical Reference do
not expose this latent API branch, so no runtime wording is needed.

## Rollback

Restore POSIX basename extraction and the previous `mkdir` ordering, then
remove the focused test file. No schema, configuration, or stored data changes.
