# Start Scan: one Dorkbook launch surface

Date: 2026-09-27
Status: implemented and automated checks PASS; HI visual acceptance pending.

## Issue and root cause

Shodan's `Edit Queries` button still opened a separate Discovery Dorks editor.
Self-hosted Search already opened Dorkbook, but its button was left-anchored in
its layout column. The controls used different widget types as well as labels,
so matching the text alone would not fix their appearance or behavior.

HI approved the [ASCII layout](ASCII_SKETCHES.md#start-scan--approved-consolidation-follow-up-2026-09-27)
and requested one shared dialog with independent provider defaults.

## Fix

Both provider sections now use identical `Dorkbook...` buttons with matching
widget type, width, padding, and right alignment. Both call
`dorkbook_events.open_provider_dorkbook(dialog, provider)`, reusing the existing
singleton library. Shodan focuses its group; Self-hosted Search focuses its
group. Both groups remain available. Provider checkbox behavior is unchanged.

The only production caller of the old editor was Start Scan. Removed that
editor, its fallback callbacks, and tests specific to the retired surface.
Custom Shodan queries are added/edited in Dorkbook. Self-hosted Search retains
its run-local query input. No storage schema, default persistence, backend,
query execution, or scan-boundary change was needed.

Applying a dork still changes only its destination: Shodan SMB, FTP, HTTP, or
Self-hosted Search. The integration test applies an EPUB dork to Shodan HTTP
and music to web search in the same form, verifies both saved queries, and
checks that the resulting request retains both providers and the music query.
No provider toggles, result caps, or scans are changed by either Apply.

Layout reference: [Tk grid geometry manager](https://web.tcl.tk/man/tcl8.6/TkCmd/grid.htm).

## Validation run

**PASS — 175 tests**, including rendered alignment at 840×680, 960×720, and
1200×800; shared-window reuse; mixed-provider defaults/request construction;
existing scan validation; and messagebox/style guardrails:

```bash
xvfb-run -a ./venv/bin/python -m pytest \
  gui/tests/test_start_scan_dorkbook.py gui/tests/test_unified_scan_dialog*.py \
  gui/tests/test_dorkbook*.py gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_scan_provider_options_searxng_scales.py \
  gui/tests/test_messagebox_guardrail.py gui/tests/test_theme_style_guardrail.py \
  shared/tests/test_dorkbook_defaults.py -q
```

**PASS — compilation of all 10 changed Python files and whitespace check:**

```bash
./venv/bin/python - <<'PY'
import py_compile, subprocess
from pathlib import Path
files = set(subprocess.check_output(['git','diff','--name-only'], text=True).splitlines())
files.update(subprocess.check_output(['git','ls-files','--others','--exclude-standard'], text=True).splitlines())
python_files = sorted(p for p in files if p.endswith('.py') and Path(p).is_file())
for path in python_files:
    py_compile.compile(path, doraise=True)
print(f'PASS: compiled {len(python_files)} changed Python files')
PY
git diff --check
```

The enumeration was run before commit. A rendered 960×720 screenshot was
visually inspected using disposable state. All GUI tests used temporary config
and sidecar files; no live searches or paid Shodan requests were issued.
README and Technical Reference were reviewed and updated, and historical
unified-validation instructions now point to this follow-up.

## HI test needed: yes

1. Launch `./dirracuda`, open Start Scan, and enable Shodan and Self-hosted Search.
2. Confirm both `Dorkbook...` buttons have equal dimensions and align on the
   right, including after resizing the dialog.
3. Click Shodan's button: Dorkbook should open at the Shodan group. Apply a
   Shodan HTTP ebook dork. Click the Self-hosted button: the same window should
   focus its other group. Apply a music dork there.
4. Confirm the web query is music and Dorkbook marks the Shodan ebook dork as
   its HTTP default. Close/reopen to confirm both persist independently.
   Neither action should start a scan or alter selected providers/result caps.

## Files changed and sizes

Before is commit `54720a9`; deleted files end at zero. All touched text files
remain below 1,700 lines. Dashboard is good, Technical Reference acceptable;
all other files are excellent. The removed editor has no production callers.

| File | Before | After |
|---|---:|---:|
| `README.md` | 803 | 807 |
| `docs/TECHNICAL_REFERENCE.md` | 1667 | 1667 |
| `docs/dev/dorkbook/ASCII_SKETCHES.md` | 193 | 215 |
| `docs/dev/dorkbook/IMPLEMENTATION_PLAN.md` | 202 | 204 |
| `docs/dev/dorkbook/LESSONS_LEARNED.md` | 57 | 66 |
| `docs/dev/dorkbook/README.md` | 30 | 32 |
| `docs/dev/dorkbook/START_SCAN_DORKBOOK.md` | 0 | 114 |
| `docs/dev/dorkbook/UNIFIED_VALIDATION.md` | 206 | 209 |
| `gui/components/dorkbook_events.py` | 61 | 62 |
| `gui/components/scan_dork_editor_dialog.py` | 475 | 0 |
| `gui/components/scan_provider_options.py` | 603 | 604 |
| `gui/components/unified_scan_dialog.py` | 1201 | 1155 |
| `gui/components/unified_scan_layout.py` | 656 | 650 |
| `gui/dashboard/widget.py` | 1380 | 1379 |
| `gui/tests/test_dashboard_scan_dialog_wiring.py` | 138 | 138 |
| `gui/tests/test_dorkbook_events.py` | 72 | 58 |
| `gui/tests/test_dorkbook_rendered.py` | 123 | 119 |
| `gui/tests/test_scan_dork_editor_dialog.py` | 248 | 0 |
| `gui/tests/test_start_scan_dorkbook.py` | 0 | 81 |
| `gui/tests/test_unified_scan_dialog_validation.py` | 400 | 315 |
