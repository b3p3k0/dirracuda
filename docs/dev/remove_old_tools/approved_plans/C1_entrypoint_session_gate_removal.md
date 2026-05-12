# C1 Plan: Entrypoint + Session-Gate Removal

## Context

The Pry and RCE subsystems are legacy features being sunset per the `docs/dev/remove_old_tools/` authority docs. C1 is the first runtime card: it removes the hidden unlock model (`--1337`, `_pry_unlocked`, `_rce_unlocked`) from the application entrypoint and the direct constructor call chain it touches. C2 and C3 will clean up consumers (server list, dialogs, pipeline) in subsequent cards. After C1, the gate flags are gone at the source; consumers will see the keys absent from `window_data` and default to `False` via their existing `.get(key, False)` guards.

---

## Files Touched (2)

| File | Before | Expected After | Rubric |
|---|---|---|---|
| `dirracuda` | 1700 | ~1631 | Acceptable |
| `gui/dashboard/widget.py` | 1659 | ~1656 | Acceptable |

No file breaches 1700. No modularization needed.

---

## Changes

### 1. `dirracuda`

**Remove `LEET_MOTD_LINES` constant (lines 81–90)**
The tuple is only referenced inside `_render_1337_motd`. Delete it entirely.

**Remove `_resolve_motd_user()` function (lines 101–112)**
Only called by `_render_1337_motd`. Delete it.

**Remove `_render_1337_motd()` function (lines 115–137)**
The MOTD renderer. Delete it. The preceding blank line(s) should also be cleaned up.

**Remove `pry_unlocked: bool = False` constructor param (line 603)**
`XSMBSeekGUI.__init__` signature — drop the param entirely.

**Remove `self._pry_unlocked` and `self._rce_unlocked` assignments (lines 617–618)**
```python
# DELETE both:
self._pry_unlocked = bool(pry_unlocked)
self._rce_unlocked = self._pry_unlocked
```

**Remove `rce_unlocked=` kwarg from `_create_dashboard` (line 1007)**
```python
# BEFORE:
self.dashboard = DashboardWidget(
    self.root,
    self.db_reader,
    self.backend_interface,
    str(smbseek_config_path),
    rce_unlocked=self._rce_unlocked,
)
# AFTER:
self.dashboard = DashboardWidget(
    self.root,
    self.db_reader,
    self.backend_interface,
    str(smbseek_config_path),
)
```

**Remove gate key injection into `window_data` for drill-down window (lines 1164–1165)**
```python
# DELETE both lines:
window_data["_pry_unlocked"] = self._pry_unlocked
window_data["_rce_unlocked"] = self._rce_unlocked
```
`window_data` retains `dict(data or {})` — gate keys simply won't appear.

**Remove `show_pry_controls=` kwarg from `open_app_config_dialog` call (line 1185)**
```python
# DELETE the kwarg; function defaults to show_pry_controls=False
show_pry_controls=self._pry_unlocked,
```

**Collapse recent_activity `ServerListWindow` dict (lines 1197–1200)**
```python
# BEFORE:
server_window = ServerListWindow(
    self.root,
    self.db_reader,
    {
        "_pry_unlocked": self._pry_unlocked,
        "_rce_unlocked": self._rce_unlocked,
    },
    on_database_changed=self._refresh_after_database_change,
)
# AFTER:
server_window = ServerListWindow(
    self.root,
    self.db_reader,
    {},
    on_database_changed=self._refresh_after_database_change,
)
```

**Remove `--1337` argparse block (lines 1655–1662)**
```python
# DELETE the comment + add_argument block:
# Easter egg: pry is intentionally hidden unless this startup flag is present.
parser.add_argument(
    "--1337",
    dest="pry_unlocked",
    action="store_true",
    default=False,
    help=argparse.SUPPRESS,
)
```

**Remove `pry_unlocked` detection + MOTD print (lines 1672–1676)**
```python
# DELETE:
pry_unlocked = bool(getattr(args, "pry_unlocked", False))
if pry_unlocked:
    motd = _render_1337_motd()
    if motd:
        print(motd)
```

**Remove `pry_unlocked=pry_unlocked` from `XSMBSeekGUI(...)` call (line 1688)**
```python
# BEFORE:
app = XSMBSeekGUI(
    mock_mode=args.mock,
    config_path=args.config,
    smbseek_path=args.backend_path,
    database_path=getattr(args, 'database_path', None),
    pry_unlocked=pry_unlocked,
)
# AFTER:
app = XSMBSeekGUI(
    mock_mode=args.mock,
    config_path=args.config,
    smbseek_path=args.backend_path,
    database_path=getattr(args, 'database_path', None),
)
```

---

### 2. `gui/dashboard/widget.py`

**Remove `rce_unlocked: bool = False` from `__init__` signature (line 133)**
```python
# BEFORE:
def __init__(self, parent: tk.Widget, db_reader: DatabaseReader,
             backend_interface: BackendInterface, config_path: str = None,
             rce_unlocked: bool = False):
# AFTER:
def __init__(self, parent: tk.Widget, db_reader: DatabaseReader,
             backend_interface: BackendInterface, config_path: str = None):
```

**Remove docstring line referencing `rce_unlocked` (line 142 area)**
```python
# DELETE the docstring Args line:
rce_unlocked: Session unlock state for hidden RCE controls
```

**Remove `self._rce_unlocked` assignment (line 161)**
```python
# DELETE:
self._rce_unlocked = bool(rce_unlocked)
```

**Hardcode `show_rce_controls=False` at line 1138**
```python
# BEFORE:
show_rce_controls=bool(getattr(self, "_rce_unlocked", False)),
# AFTER:
show_rce_controls=False,
```
The `_rce_unlocked` attribute is gone; `getattr` fallback would be `False` anyway. Hardcoding avoids dead `getattr`. The `show_rce_controls` param itself stays (removed in C3).

---

## Validation Steps (exact commands)

```bash
# 0. Scope allowlist check (must show only these two files, nothing else)
git status --short -- dirracuda gui/dashboard/widget.py

# 1. Line counts
wc -l dirracuda gui/dashboard/widget.py

# 2. Compile touched modules
./venv/bin/python -m py_compile dirracuda gui/dashboard/widget.py

# 3. Targeted tests
./venv/bin/python -m pytest gui/tests/test_action_routing.py -q
./venv/bin/python -m pytest gui/tests/test_server_ops_scenario_matrix.py -q
./venv/bin/python -m pytest gui/tests/test_dashboard_scan_dialog_wiring.py -q
# If headless needed:
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_action_routing.py gui/tests/test_server_ops_scenario_matrix.py gui/tests/test_dashboard_scan_dialog_wiring.py -q

# 4. C1 grep guardrails
rg -n -i "1337|_render_1337_motd|pry_unlocked=|_pry_unlocked|_rce_unlocked" dirracuda
rg -n -i "rce_unlocked=|show_pry_controls=" dirracuda gui
```

Expected guardrail result: zero matches in `dirracuda`; matches in `gui/` are in `window.py` and `widget.py` consumers only (those will be cleaned in C2/C3).

---

## Out of Scope for C1

- `gui/components/server_list_window/window.py` `_pry_unlocked`/`_rce_unlocked` reads → C2
- `gui/components/app_config_dialog.py` `show_pry_controls` internals → C2
- All `show_rce_controls` consumer wiring → C3
- Any DB, schema, test, or docs changes → C4–C6

---

## Execution Report

**1. Issue:** Hidden unlock model (`--1337`, `_pry_unlocked`, `_rce_unlocked`) present in app entrypoint and propagated to all child constructors, enabling hidden Pry/RCE codepaths at runtime.

**2. Root cause:** `dirracuda` parsed a suppressed `--1337` flag to set `pry_unlocked`, passed it to `XSMBSeekGUI.__init__` as a constructor param, stored it as `self._pry_unlocked` / `self._rce_unlocked`, then forwarded both flags to `DashboardWidget`, `ServerListWindow` (×2), and `open_app_config_dialog` on every app launch.

**3. Fix:** Removed `LEET_MOTD_LINES`, `_resolve_motd_user()`, `_render_1337_motd()`, `--1337` argparse block, `pry_unlocked` detection/print, `pry_unlocked` constructor param, both instance assignments, and all downstream propagation kwargs in `dirracuda`. In `gui/dashboard/widget.py`, removed the `rce_unlocked` constructor param, `self._rce_unlocked` assignment, and hardcoded `show_rce_controls=False` (replacing dead `getattr`). Updated `test_dashboard_scan_dialog_wiring.py` assertion to reflect the hardcoded value (was testing old gate behavior).

**4. Files changed:**

| File | Before | After | Rubric |
|---|---|---|---|
| `dirracuda` | 1700 | 1624 | Acceptable |
| `gui/dashboard/widget.py` | 1659 | 1656 | Acceptable |
| `gui/tests/test_dashboard_scan_dialog_wiring.py` | 93 | 93 (1 line changed) | Excellent |

**5. Validation run:**
```
git status --short -- dirracuda gui/dashboard/widget.py gui/tests/test_dashboard_scan_dialog_wiring.py
→ M dirracuda  M gui/dashboard/widget.py  M gui/tests/test_dashboard_scan_dialog_wiring.py

wc -l dirracuda gui/dashboard/widget.py
→ 1624 / 1656

./venv/bin/python -m py_compile dirracuda gui/dashboard/widget.py
→ COMPILE OK

xvfb-run -a ./venv/bin/python -m pytest test_action_routing.py test_server_ops_scenario_matrix.py test_dashboard_scan_dialog_wiring.py -q
→ 1 failed (test_s10_se_dork_probe_task_lifecycle_success — pre-existing, confirmed failing on commit 56d5a50 before C1), 55 passed

rg -n -i "1337|_render_1337_motd|pry_unlocked=|_pry_unlocked|_rce_unlocked" dirracuda
→ (no output)

rg -n -i "rce_unlocked=|show_pry_controls=" dirracuda gui/
→ gui/components/app_config_dialog.py:1369 (C2 scope, expected residual)
```

**6. Result:** PASS — entrypoint is clean; all C1-in-scope tests pass; one pre-existing SE Dork failure confirmed unrelated to C1.

**7. HI test needed?** Yes.
```bash
# Verify app launches without --1337 flag
./dirracuda &
# App window should open normally; no pry/rce controls visible

# Verify --1337 is rejected (unknown argument)
./dirracuda --1337
# Expected: error: unrecognized arguments: --1337
```
