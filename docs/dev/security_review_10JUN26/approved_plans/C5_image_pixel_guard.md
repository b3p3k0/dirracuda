# C5 - Pre-Decode Image Pixel Guard (Approved Plan)

## Status And Baseline

- Branch: `development`
- Approved implementation baseline: `c642457`
- Product target: `gui/components/image_viewer_window.py`
- Focused test target: `gui/tests/test_image_viewer_window.py`
- Baseline product line count: 209
- Existing HTTP, FTP, and viewer-keybinding tests: 39 passed

## Objective

Inspect image dimensions after Pillow's lazy `Image.open()` and before
`Image.load()`. Reject zero, negative, malformed, and over-limit dimensions
without decoding pixels, while preserving Pillow's decompression-bomb behavior
and the browser's existing user-readable error path.

## Confirmed Root Cause

`ImageViewerWindow._load_image_safe()` called `img.load()` before reading
`img.size` and applying the application's `max_pixels` limit. An attacker could
therefore force the full decode before the lower project limit was enforced.

Pillow's own guard is complementary. It runs during `Image.open()` and may warn
or raise before the project check for sufficiently large declared dimensions.
The implementation must not change or disable `Image.MAX_IMAGE_PIXELS`.

## Non-Goals

- Do not change the configured pixel limit or its browser plumbing.
- Do not modify SMB, FTP, or HTTP viewer callers.
- Do not change rendering, saving, keybindings, dependencies, schema, auth,
  CI, or the GUI-to-CLI boundary.

## Exact Behavior

`_load_image_safe(content)` keeps its signature and return type:

1. Open the bytes lazily with `Image.open()`.
2. Read and unpack `img.size`.
3. Require two positive integer dimensions.
4. Reject when `width * height > max_pixels`.
5. Call `img.load()` only after all project checks pass.
6. Return the decoded image.

An image exactly at the limit decodes. Corrupt input and Pillow
`DecompressionBombError` continue to propagate to the existing browser
exception handler and safe messagebox.

## File Changes

### `gui/components/image_viewer_window.py`

Move `img.load()` below dimension validation and the existing strict
over-limit check. Preserve the existing over-limit message.

### `gui/tests/test_image_viewer_window.py`

Add direct, headless unit tests using `ImageViewerWindow.__new__()`:

- over-limit rejection occurs before `load()`;
- zero, negative, non-integer, boolean, and wrong-arity dimensions fail closed
  before `load()`;
- an image exactly at the limit calls `load()` before return;
- one pixel over the limit is rejected without `load()`;
- corrupt input still raises `UnidentifiedImageError`;
- a real Pillow `DecompressionBombError` propagates when the threshold is
  temporarily lowered by the test.

## Edge Cases

- `pixels == max_pixels`: accepted and decoded.
- `pixels > max_pixels`: rejected before decode.
- zero, negative, non-integer, boolean, or malformed arity: rejected before
  decode.
- corrupt bytes: `Image.open()` error propagates.
- truncated payload with a valid header: dimension check runs, then the
  `load()` error propagates.
- Pillow bomb threshold exceeded: Pillow's own warning/error behavior remains.

## Validation

```bash
./venv/bin/python -m pytest gui/tests/test_image_viewer_window.py -v
./venv/bin/python -m pytest \
  gui/tests/test_image_viewer_window.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_ftp_browser_window.py -q
./venv/bin/python -m pytest \
  gui/tests/test_browser_viewer_keybindings.py \
  gui/tests/test_c2_browser_import_contracts.py -q
./venv/bin/python -m py_compile \
  gui/components/image_viewer_window.py \
  gui/tests/test_image_viewer_window.py
git diff --check
```

## Documentation And Line Risk

README and Technical Reference already describe the configured image limit and
require no runtime documentation change. Both touched files remain far below
the 1700-line stop gate.

## Manual Gate

Open one normal image from an SMB, FTP, or HTTP browser and confirm it renders,
shows its dimensions, and produces no error.

## Rollback

Restore the original `_load_image_safe()` ordering and remove the focused C5
test file. No data or configuration migration is involved.
