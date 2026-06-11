# C6 - Bounded ZIP Import (Approved Plan)

## Status And Baseline

- Branch: `development`
- Approved implementation baseline: `7577f1d`
- Product target: `gui/utils/data_import_engine.py`
- Focused tests: `gui/tests/test_data_import_engine.py`
- Product line count before: 673
- Focused baseline: 5 passed

## Objective

Replace archive-tree extraction with deterministic validation and bounded
streaming of one selected root-level JSON or CSV payload. Import, preview, and
format-check must use the same archive pipeline, and normal Dirracuda export
ZIPs must remain compatible.

## Confirmed Root Cause

`_read_zip_file()` called `ZipFile.extractall()` before imposing member-count,
declared-size, selected-size, or streamed-byte limits. Archive member names
became destination paths. `validate_file_format()` independently scanned
`namelist()`, so it could accept archives that preview or import rejected.

## Non-Goals

- No export-format, database, schema, auth, CI, dependency, public CLI, or
  GUI-to-CLI boundary changes.
- No change to direct JSON/CSV parsing or database import behavior.

## Exact Behavior

1. Inspect no more than 32 total members.
2. Reject declared total uncompressed size above 256 MiB.
3. Eligible members must be root-level under both slash conventions,
   non-directory, and regular or lacking explicit file-type bits.
4. Eligible extensions are `.json` and `.csv`.
5. Exclude only the exact exporter metadata name `export_metadata.json`.
6. Reject duplicate eligible member names.
7. Prefer JSON; otherwise choose lexical-first CSV.
8. Select before encryption handling and reject an encrypted selected member
   without falling through.
9. Reject selected declared size above 128 MiB.
10. Stream only that member to an engine-generated `mkstemp` path, never a
    member-derived path, with an actual-byte cap of 128 MiB.
11. Reuse the existing JSON/CSV reader and remove the temporary file on success
    or failure.

`validate_file_format()`, `preview_import_data()`, and `import_data()` all run
the full select, stream, and parse pipeline.

## Cleanup And Errors

- Streaming owns its temporary file until it returns successfully.
- File descriptors close on every path.
- Cleanup is best-effort and never masks the active exception.
- Stream overrun and ZIP member decode failures surface as actionable
  `ValueError`; unrelated environment errors propagate unchanged.

## Files

### `gui/utils/data_import_engine.py`

Add fixed ZIP limits, regular-member detection, deterministic member selection,
bounded streaming, cleanup helpers, and shared ZIP handling for format-check.
Remove `extractall`.

### `gui/tests/test_data_import_engine.py`

Cover member and size boundaries, slash/backslash nesting, directories,
symlinks and missing type bits, exact metadata exclusion, duplicate names,
selection order, encryption, declared and actual-byte caps, corruption,
descriptor/temp cleanup, three-path agreement, and normal export compatibility.

Use controlled ZIP stubs for metadata states that `zipfile.writestr()` rewrites,
including encryption flags and false declared sizes.

## Validation

```bash
./venv/bin/python -m pytest gui/tests/test_data_import_engine.py -q
./venv/bin/python -m pytest gui/tests/test_db_tools_dialog.py -q
rg -n "extractall" gui/utils/data_import_engine.py
./venv/bin/python -m py_compile \
  gui/utils/data_import_engine.py \
  gui/tests/test_data_import_engine.py
git diff --check
```

Also exercise a real `DataExportEngine` ZIP through format-check, preview, and
validate-only import.

## Line Risk And Documentation

The product file remains well below the 1700-line stop gate. README and
Technical Reference require no runtime wording change for this internal
hardening card.

## Manual Gate

Import one normal Dirracuda ZIP export in validate-only mode. HI may explicitly
defer this gate when automated real-export compatibility evidence is recorded.

## Rollback

Restore the prior `_read_zip_file()` and format-check branch, remove the ZIP
helpers/constants, and remove the C6 tests. No data migration is involved.
