# F1 QA Fixes — Post-Review Regressions

## Context

F1 (Compatibility Bridge Layer) shipped clean against its original validation suite, but a subsequent QA/QC review found one startup regression introduced by F1, one missed env-var alias site, and a fragile smoke-test command. All three are contained to ≤2 lines each.

---

## Fixes

### 1. High — Startup regression on malformed `gui_app` (null/non-dict)

**Files:** `xsmbseek`
**Locations:** `_load_config()` (~line 85) **and** `_ensure_gui_section()` (lines 112–116)

**Problem — two crash points:**

1. `_load_config()` migration block: `config.get(self.GUI_SECTION, {})` returns the stored value verbatim when the key is present — so `gui_app: null` (JSON null → Python `None`) makes `gui` equal `None`. The subsequent `if "smbseek_path" in gui` raises `TypeError: argument of type 'NoneType' is not iterable`. Pre-F1 this block didn't exist, so pre-F1 HEAD didn't crash here.

2. `_ensure_gui_section()`: the guard is `if self.GUI_SECTION not in self.config`. When `gui_app: null`, the key IS present so this is `False`, and the method returns `None`. Every downstream caller (`get_smbseek_path`, `set_smbseek_path`, `get_database_path`, `set_database_path`) then does `.get(...)` or `["..."] = ...` on `None`, raising `AttributeError`. This is the real startup path — `XSMBSeekConfig.__init__` calls `_load_config()`, and then normal startup calls `get_smbseek_path()` → `_ensure_gui_section()`.

**Fixes:**

`_load_config()` — add `isinstance` guard so migration skips non-dict gui sections:
```python
# before (line ~85)
gui = config.get(self.GUI_SECTION, {})
if "smbseek_path" in gui and not gui.get("backend_path"):

# after
gui = config.get(self.GUI_SECTION)
if isinstance(gui, dict) and "smbseek_path" in gui and not gui.get("backend_path"):
```

`_ensure_gui_section()` — treat non-dict values (null, string, list) same as absent key; wrap write in non-blocking try/except (same pattern as `_load_config()`):
```python
# before (lines 112–116)
def _ensure_gui_section(self) -> Dict[str, Any]:
    if self.GUI_SECTION not in self.config:
        self.config[self.GUI_SECTION] = self._default_gui_section()
        self._write_config(self.config)
    return self.config[self.GUI_SECTION]

# after
def _ensure_gui_section(self) -> Dict[str, Any]:
    if not isinstance(self.config.get(self.GUI_SECTION), dict):
        self.config[self.GUI_SECTION] = self._default_gui_section()
        try:
            self._write_config(self.config)
        except Exception as e:
            _logger.warning("Could not persist gui_app defaults (config may be read-only): %s", e)
    return self.config[self.GUI_SECTION]
```

`not isinstance(..., dict)` covers: absent key (`None` from `.get()`), JSON null, string, list — all get replaced with defaults in-memory. The `try/except` ensures a read-only config file never aborts startup — in-memory repair always proceeds, disk write failure is logged and ignored.

---

### 2. Medium — Missed `DIRRACUDA_DEBUG_PARSING` alias in `dashboard.py`

**File:** `gui/components/dashboard.py`
**Location:** ~line 1457

**Problem:** The bulk-ops debug gate checks only the legacy var:
```python
if os.getenv("XSMBSEEK_DEBUG_PARSING"):
```
F1 aliased this env var in `progress.py` and `logging_config.py` but missed this site.

**Fix:**
```python
if os.getenv("XSMBSEEK_DEBUG_PARSING") or os.getenv("DIRRACUDA_DEBUG_PARSING"):
```

---

### 3. Low — Smoke-test command is environment-fragile

**Not a code change.** `./xsmbseek` uses the `#!/usr/bin/env python3` shebang which may pick up system Python (missing `impacket` and other venv deps). Smoke tests must use:

```bash
./venv/bin/python xsmbseek --smbseek-path . --version
./venv/bin/python xsmbseek --backend-path . --version
```

---

## Critical Files

| File | Location | Change |
|---|---|---|
| `xsmbseek` | `_load_config()` ~line 85 | `gui = config.get(...)` (no default) + `isinstance(gui, dict) and` guard |
| `xsmbseek` | `_ensure_gui_section()` lines 112–116 | `not isinstance(..., dict)` replaces `not in self.config` |
| `gui/components/dashboard.py` | ~line 1457 | `or os.getenv("DIRRACUDA_DEBUG_PARSING")` added |

---

## Verification

```bash
# Compile check
./venv/bin/python -m py_compile xsmbseek gui/components/dashboard.py

# High fix case 1: null gui_app survives construction AND get_smbseek_path()
./venv/bin/python -c "
import tempfile, json, shutil
from pathlib import Path
from importlib.machinery import SourceFileLoader
mod = SourceFileLoader('xsmbseek', './xsmbseek').load_module()
XSMBSeekConfig = mod.XSMBSeekConfig
d = tempfile.mkdtemp()
try:
    cfg_dir = Path(d) / 'conf'
    cfg_dir.mkdir()
    (cfg_dir / 'config.json').write_text(json.dumps({'gui_app': None}))
    try:
        xc = XSMBSeekConfig(str(cfg_dir / 'config.json'))
        xc.get_smbseek_path()  # exercises _ensure_gui_section
        print('PASS: null gui_app does not crash through get_smbseek_path()')
    except (TypeError, AttributeError) as e:
        print(f'FAIL: {e}')
finally:
    shutil.rmtree(d)
"

# High fix case 2: null gui_app + read-only config — in-memory repair, no crash
./venv/bin/python -c "
import tempfile, json, shutil, stat
from pathlib import Path
from importlib.machinery import SourceFileLoader
mod = SourceFileLoader('xsmbseek', './xsmbseek').load_module()
XSMBSeekConfig = mod.XSMBSeekConfig
d = tempfile.mkdtemp()
cfg_file = None
try:
    cfg_dir = Path(d) / 'conf'
    cfg_dir.mkdir()
    cfg_file = cfg_dir / 'config.json'
    cfg_file.write_text(json.dumps({'gui_app': None}))
    cfg_file.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)  # read-only
    try:
        xc = XSMBSeekConfig(str(cfg_file))
        xc.get_smbseek_path()
        print('PASS: null gui_app + read-only config does not crash')
    except Exception as e:
        print(f'FAIL: {e}')
finally:
    if cfg_file:
        cfg_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    shutil.rmtree(d)
"

# Medium fix: both aliases present at dashboard site
grep -n "XSMBSEEK_DEBUG_PARSING\|DIRRACUDA_DEBUG_PARSING" gui/components/dashboard.py

# Low fix: smoke tests with venv python
./venv/bin/python xsmbseek --smbseek-path . --version
./venv/bin/python xsmbseek --backend-path . --version
```
