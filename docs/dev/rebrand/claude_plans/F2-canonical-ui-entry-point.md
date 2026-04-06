# F2: Canonical UI + Entry Point Switch

## Context
F1 delivered the compatibility bridge (key aliases, config migration hooks). F2 adds the canonical
`dirracuda` GUI launcher and updates every user-visible "SMBSeek" string in the GUI. Legacy
`xsmbseek` must keep working identically. No data-path or DB-filename migration (F4).

---

## Issue
1. No `dirracuda` executable exists.
2. User-facing dialog/error strings in the launcher and several GUI components still read "SMBSeek".
3. `GUI_LOGGER_NAME` emits `"smbseek_gui"` prefix in every log line.
4. `conf/config.json.example` and the `xsmbseek` runtime default both point `github_repo` at
   the old `smbseek` repo URL.

## Root Cause
F1 focused on backend/config-key compatibility. User-visible copy in `xsmbseek` dialogs, GUI
validation messages, scan-results error panel, and the logger name were deferred to F2.

---

## Planned Changes (12 areas, 10 files)

### Area 1 — New `dirracuda` launcher (NEW file: `dirracuda`)

Use `sys.executable` so the wrapper stays in whatever Python environment was used to invoke it
(venv or system), preserving the same interpreter for xsmbseek:

```python
#!/usr/bin/env python3
import os, sys
os.environ['DIRRACUDA_PROG_NAME'] = 'dirracuda'
_dir = os.path.dirname(os.path.abspath(__file__))
os.execv(sys.executable, [sys.executable, os.path.join(_dir, 'xsmbseek')] + sys.argv[1:])
```

`chmod +x dirracuda` after creating.

**Why `sys.executable`:** `os.execv(xsmbseek_path, ...)` would re-enter the shebang (`python3`),
which may resolve to system Python rather than the active venv. `sys.executable` is always the
exact interpreter running the wrapper.

**Why `DIRRACUDA_PROG_NAME` env var:** `os.execv` with a Python path sets `sys.argv[0]` to the
script path, not `'dirracuda'`. The env var lets `xsmbseek` detect the canonical prog name
without inspecting `sys.argv[0]`.

### Area 2 — `xsmbseek` argparse + runtime default (4 lines)

Add before the `ArgumentParser` call:
```python
_prog = os.environ.get('DIRRACUDA_PROG_NAME', os.path.basename(sys.argv[0]))
```

Then update:

| Location | Current | Change |
|----------|---------|--------|
| `ArgumentParser(prog=...)` | `prog='xsmbseek'` | `prog=_prog` |
| `--version` action | `version="xsmbseek 1.0.0"` | `version=f"{_prog} 1.0.0"` |
| line 109 (`_default_gui_section`) | `"github_repo": "https://github.com/b3p3k0/smbseek"` | `"https://github.com/b3p3k0/dirracuda"` |

Effect: `./dirracuda --help` → `dirracuda`, `./xsmbseek --help` → `xsmbseek`. Both correct.

### Area 3 — `xsmbseek` setup-dialog, validation, and init-error copy (15 user-facing strings)

Re-read lines before editing; line numbers are current-state references only.

| Approx line | Current | Change |
|-------------|---------|--------|
| 238 | `"SMBSeek directory not found at: …"` | `"Dirracuda directory not found at: …"` |
| 246 | `"SMBSeek executable not found at: …"` | `"Dirracuda executable not found at: …"` |
| 306 | `"Please specify a path to SMBSeek"` | `"Please specify a path to Dirracuda"` |
| 315 | messagebox title `"Invalid SMBSeek Installation"` | `"Invalid Dirracuda Installation"` |
| 333 | `"SMBSeek Installation Required"` | `"Dirracuda Installation Required"` |
| 339 | `"xsmbseek requires the SMBSeek security toolkit to function."` | `"Dirracuda requires the Dirracuda backend toolkit to function."` |
| 348 | `"1. If you haven't installed SMBSeek yet:"` | `"1. If you haven't installed Dirracuda yet:"` |
| 349 | `"   • Click 'Open SMBSeek Repository' below"` | `"   • Click 'Open Dirracuda Repository' below"` |
| 353 | `"2. If SMBSeek is already installed:"` | `"2. If Dirracuda is already installed:"` |
| 354 | `"   • Enter the path to your SMBSeek directory below"` | `"   • Enter the path to your Dirracuda directory below"` |
| 363 | LabelFrame `"SMBSeek Path"` | `"Dirracuda Path"` |
| 380 | Button `"Open SMBSeek Repository"` | `"Open Dirracuda Repository"` |
| 544 | `f"SMBSeek validation failed after setup: …"` | `f"Dirracuda validation failed after setup: …"` |
| 551 | `f"Failed to initialize SMBSeek interface: …"` | `f"Failed to initialize Dirracuda interface: …"` |
| 768 | `f"Failed to initialize xsmbseek: {error}"` | `f"Failed to initialize {os.environ.get('DIRRACUDA_PROG_NAME', 'xsmbseek')}: {error}"` — uses env var so both launchers show their own name; no module-level constant needed |

### Area 4 — `gui/utils/settings_manager.py` validation messages (4 lines)

| Approx line | Current | Change |
|-------------|---------|--------|
| 611 | `'smbseek executable not found in directory'` | `'Dirracuda executable not found in directory'` |
| 623 | `'Valid SMBSeek installation ({version})'` | `'Valid Dirracuda installation ({version})'` |
| 625 | `'SMBSeek installation found (version check failed)'` | `'Dirracuda installation found (version check failed)'` |
| 627 | `'SMBSeek installation found (version check failed)'` | `'Dirracuda installation found (version check failed)'` |

### Area 5 — `gui/components/app_config_dialog.py` validation messages (4 lines)

| Approx line | Current | Change |
|-------------|---------|--------|
| 458 | `"Missing smbseek executable."` | `"Missing Dirracuda executable."` |
| 468 | `"smbseek executable failed version check."` | `"Dirracuda executable failed version check."` |
| 470 | `"smbseek found; version check skipped."` | `"Dirracuda found; version check skipped."` |
| 472 | `"Valid SMBSeek installation."` | `"Valid Dirracuda installation."` |

### Area 6 — `gui/components/dashboard.py` scan-error dialog copy (3 strings)

All three appear inside `messagebox.showerror("Scan Error", ...)` calls:

| Approx line | Current | Change |
|-------------|---------|--------|
| 1300 | `f"• SMBSeek CLI not found: {smbseek_cli}"` | `f"• Dirracuda CLI not found: {smbseek_cli}"` |
| 1313 | `"…Please ensure SMBSeek is properly installed and configured."` | `"…Please ensure Dirracuda is properly installed and configured."` |
| 1327 | `"• SMBSeek backend is not installed or not in expected location\n"` | `"• Dirracuda backend is not installed or not in expected location\n"` |

### Area 7 — `gui/components/scan_results_dialog.py` error panel (3 strings)

| Approx line | Current | Change |
|-------------|---------|--------|
| 356 | `"This error suggests the SMBSeek backend may not be available…"` | `"…the Dirracuda backend…"` |
| 367 | `"Get the latest SMBSeek backend:"` | `"Get the latest Dirracuda backend:"` |
| 375 | `webbrowser.open("https://github.com/b3p3k0/smbseek")` | `"https://github.com/b3p3k0/dirracuda"` |

### Area 9 — `gui/utils/logging_config.py` logger name (1 constant + 1 docstring)

| Approx line | Current | Change |
|-------------|---------|--------|
| 15 | `GUI_LOGGER_NAME = "smbseek_gui"` | `GUI_LOGGER_NAME = "dirracuda_gui"` |
| 66 | docstring `"…smbseek_gui namespace"` | `"…dirracuda_gui namespace"` |

Safety: grep confirms `GUI_LOGGER_NAME` is accessed only via the constant inside
`logging_config.py`. No other file hardcodes the raw string `"smbseek_gui"`.

### Area 10 — `gui/utils/database_access.py` import-recommendation strings (4 lines)

These values flow into `database_setup_dialog.py:394` which embeds them in a raised `ValueError`
shown to the user. Fix the source strings here.

| Approx line | Current | Change |
|-------------|---------|--------|
| 190 | `'Full SMBSeek database - ready for import'` | `'Full Dirracuda database - ready for import'` |
| 193 | `'Partial SMBSeek database - core data available'` | `'Partial Dirracuda database - core data available'` |
| 198 | `'Basic SMBSeek database - limited functionality'` | `'Basic Dirracuda database - limited functionality'` |
| 203 | `'Not a compatible SMBSeek database'` | `'Not a compatible Dirracuda database'` |

### Area 11 — `gui/components/database_setup_dialog.py` backend-check error (1 line)

| Approx line | Current | Change |
|-------------|---------|--------|
| 495 | `raise RuntimeError("SMBSeek backend not available")` | `raise RuntimeError("Dirracuda backend not available")` |

### Area 12 — `conf/config.json.example` URL (1 field)

| Approx line | Current | Change |
|-------------|---------|--------|
| 170 | `"github_repo": "https://github.com/b3p3k0/smbseek"` | `"https://github.com/b3p3k0/dirracuda"` |

---

## Ambiguous Replacements — Safest Option Proposed

| String | Decision | Reason |
|--------|----------|--------|
| `"SMBSeek.Horizontal.TProgressbar"` (4 files: `style.py:638`, `db_tools_dialog.py:1164`, `dashboard.py:1874`, `database_setup_dialog.py:225`) | **SKIP — F3** | Internal Tkinter style name, never visible to users; 4-file coordinated rename. |
| Class names `XSMBSeekConfig`, `XSMBSeekGUI` in `xsmbseek` | **SKIP — F3** | Internal identifiers; no user impact. |
| `"SMBSeek security assessment completed successfully"` (`progress.py:548`) | **SKIP — explicit constraint #4** | Parser-coupled; emitter+parser must change together. |
| `_logger = get_logger("xsmbseek")` at `xsmbseek:44` | **SKIP** | Sub-logger; parent logger rename (Area 9) is the canonical change. |
| Docstrings/internal comments throughout | **SKIP** | Not user-facing. |
| `~/.smbseek`, `smbseek.db` references | **SKIP — explicit constraint #5** | F4. |

---

## Files to Change

| File | Areas | New? |
|------|-------|------|
| `dirracuda` | 1 | YES — create |
| `xsmbseek` | 2, 3 | modify |
| `gui/utils/settings_manager.py` | 4 | modify |
| `gui/components/app_config_dialog.py` | 5 | modify |
| `gui/components/dashboard.py` | 6 | modify |
| `gui/components/scan_results_dialog.py` | 7 | modify |
| `gui/utils/logging_config.py` | 9 | modify |
| `gui/utils/database_access.py` | 10 | modify |
| `gui/components/database_setup_dialog.py` | 11 | modify |
| `conf/config.json.example` | 12 | modify |

---

## Validation Plan

### Automated (no pipe — don't hide exit code)
```bash
xvfb-run -a venv/bin/python -m pytest gui/tests/ shared/tests/ -v
```

### CLI smoke tests (all exit immediately — no GUI mainloop)
Use `venv/bin/python` directly for deterministic interpreter; direct script invocations
(`./xsmbseek`, `./dirracuda`) are shebang-dependent and suitable only as optional manual checks.

```bash
./venv/bin/python xsmbseek --version                          # → "xsmbseek 1.0.0"
./venv/bin/python dirracuda --version                         # → "dirracuda 1.0.0"
./venv/bin/python xsmbseek --help 2>&1 | grep "^usage:"      # → "usage: xsmbseek …"
./venv/bin/python dirracuda --help 2>&1 | grep "^usage:"     # → "usage: dirracuda …"
./venv/bin/python xsmbseek --smbseek-path . --version        # → "xsmbseek 1.0.0" (F1 alias)
./venv/bin/python dirracuda --smbseek-path . --version       # → "dirracuda 1.0.0"
```

### Pre-flight discovery grep (run BEFORE editing — broad scope)
Scanning only changed files cannot prove full coverage. Run against all GUI Python files AND
`xsmbseek` explicitly. Note: `--include="*.py"` only applies to recursive directory traversal,
not to explicitly named file arguments — so `xsmbseek` must be listed separately to ensure it
is scanned.

```bash
{ grep -rn "SMBSeek" gui/ --include="*.py"; grep -n "SMBSeek" xsmbseek; } \
  | grep -v \
      -e "security assessment completed successfully" \
      -e "SMBSeek\.Horizontal" \
      -e "XSMBSeekConfig\|XSMBSeekGUI\|SMBSeekOutput"
# Review ALL output — docstrings/comments: skip; user-facing strings: fix before proceeding
```

### Post-edit spot-check (same command after all edits)
```bash
{ grep -rn "SMBSeek" gui/ --include="*.py"; grep -n "SMBSeek" xsmbseek; } \
  | grep -v \
      -e "security assessment completed successfully" \
      -e "SMBSeek\.Horizontal" \
      -e "XSMBSeekConfig\|XSMBSeekGUI\|SMBSeekOutput"
# Remaining hits should be docstrings/comments and known-deferred items only
```

### Area 9 static check (logger rename — direct PASS/FAIL gate)
```bash
grep -n "smbseek_gui" gui/utils/logging_config.py \
  && echo "FAIL: old logger name still present" \
  || echo "PASS: smbseek_gui removed"
grep -n "dirracuda_gui" gui/utils/logging_config.py  # should return 2 lines
```

### HI/Manual test (required — yes)
1. `./dirracuda --mock` → GUI opens, no "SMBSeek" visible in window title or any dialog
2. `./xsmbseek --mock` → same; both launchers behave identically
3. In `--mock` mode: open any config/path dialog → confirm all labels say "Dirracuda"
4. Trigger the scan-error panel (mock a backend failure if possible) → confirm error text
   reads "Dirracuda backend" not "SMBSeek backend"

---

## Rollback Notes
Fully reversible — no schema, data-path, or DB changes:
```bash
rm dirracuda
git checkout -- xsmbseek \
  gui/components/dashboard.py \
  gui/components/app_config_dialog.py \
  gui/components/scan_results_dialog.py \
  gui/components/database_setup_dialog.py \
  gui/utils/settings_manager.py \
  gui/utils/logging_config.py \
  gui/utils/database_access.py \
  conf/config.json.example
```

---

## Risks / Assumptions

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Line numbers shift before implementation | Low | Re-read each file before editing; don't use line numbers as anchors |
| Automated test parses `--help` output for exact "xsmbseek" string | Possible | Run full test suite first; fix string-match tests if needed |
| `sys.executable` in wrapper resolves to system Python (user didn't activate venv) | Low | Same behavior as running `./xsmbseek` without venv — existing limitation, not new |
| Another file hardcodes `"smbseek_gui"` string | Very low | Grep confirmed only `logging_config.py` references it |

## Explicitly Out of Scope (F2)
- `SMBSeek.Horizontal.TProgressbar` style rename (F3, 4-file coordinated)
- Class renames `XSMBSeekConfig` / `XSMBSeekGUI` (F3)
- `"SMBSeek security assessment completed successfully"` (paired emitter change, F3+)
- `~/.smbseek` → `~/.dirracuda` (F4)
- `smbseek.db` → `dirracuda.db` (F4)
- CLI argparse in `cli/smbseek.py`, `cli/ftpseek.py`, `cli/httpseek.py`
