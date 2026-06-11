# C7 - FTP Remote-Path Control Validation (Approved Plan)

## Status And Baseline

- Branch: `development`
- Approved implementation baseline: `ecb8540`
- Product targets:
  - `shared/ftp_browser.py`
  - `gui/browsers/ftp_browser.py`
  - `gui/utils/protocol_extract_runner.py`
- Focused baseline: 13 passed

## Objective

Reject ASCII C0 controls and DEL in FTP remote file paths before any FTP
command, keepalive, or local extraction filesystem operation. User-visible
FTP paths must replace rejected controls with printable `U+XXXX` tokens without
changing ordinary or Unicode paths.

## Confirmed Root Cause

`FtpNavigator` interpolated unchecked remote paths into `SIZE` and `RETR`.
Validation inside `get_file_size()`'s existing `try` would be swallowed as an
unsupported-size result, while `download_file()` and `read_file()` issued a
`NOOP` before validation. GUI and extraction callers also rendered raw paths,
and the extractor called `mkdir` and `unlink` before the navigator could reject
the path.

## Non-Goals

- No change to directory listing, MLSD/LIST parsing, path depth or length
  limits, authentication, passive mode, reconnection, cancellation, or file
  size limits.
- No rejection of C1 controls or non-ASCII Unicode.
- No change to HTTP or SMB reporting, shared report builders, dependencies,
  schema, auth, CI, public CLI behavior, or the GUI-to-CLI boundary.

## Exact Behavior

1. `validate_remote_path()` rejects U+0000 through U+001F and U+007F with
   `ValueError`.
2. The error contains only a fixed description and the offending `U+XXXX`
   token, never the raw path or control character.
3. `display_safe_path()` replaces those characters with `<U+XXXX>` and leaves
   all other text unchanged.
4. `get_file_size()` validates before its `try`.
5. `download_file()` and `read_file()` validate before `_ensure_connected()`.
6. FTP browser view, download-preflight, and worker status surfaces use safe
   display names while retaining raw paths for protocol logic.
7. FTP extraction validates each file path before local path creation or
   deletion. Rejected entries produce a sanitized error and are skipped.
8. FTP extraction uses sanitized paths for progress, reports, ClamAV
   accumulation, and quarantine logs. Filtering and post-processing retain the
   raw path after successful validation.

## Files

### `shared/ftp_browser.py`

Add the shared validator and display sanitizer, export both, and invoke the
validator at the three file-operation entrypoints.

### `gui/browsers/ftp_browser.py`

Sanitize FTP-only view, download-preflight, and download-worker display text.
Do not modify the protocol-neutral browser core.

### `gui/utils/protocol_extract_runner.py`

Validate FTP file paths before filesystem mutation and sanitize FTP-only
display/report/log values. Keep `_append_skip`, `_append_error`, and HTTP paths
protocol-neutral.

### Tests

Extend FTP navigator, browser-window, and protocol-extractor tests.

## Edge And Failure Cases

- Cover NUL, CR, LF, another C0 character, and DEL at the start, middle, and
  end of paths.
- Assert `voidcmd`, `sendcmd`, and `retrbinary` are untouched on rejection.
- Assert rejected downloads create no file and rejected extraction creates no
  control-bearing path.
- Verify oversized and directory prompts, view dispatch, progress, reports,
  and errors contain no raw controls.
- Preserve spaces, punctuation, Unicode, normal FTP extraction, and HTTP
  extraction behavior.

## Validation

```bash
./venv/bin/python -m pytest \
  gui/tests/test_ftp_browser.py \
  gui/tests/test_ftp_browser_window.py -q
./venv/bin/python -m pytest \
  gui/tests/test_protocol_extract_runner.py \
  gui/tests/test_ftp_probe.py \
  gui/tests/test_browser_clamav.py -q
./venv/bin/python -m pytest -q
./venv/bin/python -m py_compile \
  shared/ftp_browser.py \
  gui/browsers/ftp_browser.py \
  gui/utils/protocol_extract_runner.py
git diff --check
```

## Line Risk And Documentation

All product files remain below 1200 lines. README and Technical Reference need
no update because neither documents an FTP remote-path control contract.

## Manual Gate

None. Mocked adversarial tests cover the card.

## Rollback

Remove the validator and sanitizer, restore the three prior call paths, and
remove the C7 tests. No configuration, schema, or data migration is involved.
