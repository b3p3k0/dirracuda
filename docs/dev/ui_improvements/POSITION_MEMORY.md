# Remembered window positions

## Issue and decision

HI has a four-monitor desktop with gaps in its combined bounding rectangle.
Start Scan recalculated a parent-centered position and clamped it to that
rectangle every time; the dashboard centered on the overall screen. Neither
remembered a manually chosen location. Other working windows had independent
default placement, while Dorkbook and Keymaster already saved full geometry.

Approved scope: main working windows plus scan dialogs. Save position only across
application restarts, separately by window type. No automatic monitor fitting,
new size preferences, native file-picker changes, or messagebox changes.

## Implementation

`gui/utils/window_positions.py` uses the existing SettingsManager and canonical
GUI preferences. Each integration restores after its default placement, before
normal display/focus handling. The setting is `windows.<key>.position`.

| Windows | Keys |
|---------|------|
| Dashboard, Server List | `main_window`, `server_list` |
| SMB, FTP, HTTP browsers | `smb_browser`, `ftp_browser`, `http_browser` |
| Start Scan, Running Tasks | `start_scan`, `running_tasks` |
| Live Scan Output, Scan Results | `scan_output`, `scan_results` |
| Server List Probe/Extract Status | `probe_status`, `extract_status` |
| Post-scan preparation/progress | `bulk_preparation`, `bulk_probe`, `bulk_extract` |
| Probe/Extract summaries, ClamAV results | `probe_summary`, `extract_summary`, `clamav_results` |

- Store Tk's position suffix, including signed/negative coordinates. Restore it
  without screen-bound clamping or changing width/height. Invalid values fall
  back to existing placement; legacy `position: center` remains valid as a fallback.
- Debounce saves by 400 ms. Flush pending movement on Hide or Destroy using the
  last normal Configure snapshot, so teardown never queries a destroyed widget.
- Ignore child Configure events, initial 1x1 geometry, and minimized/maximized
  geometry. On X11, maximization needs the `-zoomed` attribute check because
  `state()` can still return `normal`.
- Position restoration does not map, raise, or focus a window. The previous
  quiet-background behavior and explicit user-open focus paths are preserved.
- Dorkbook and Keymaster keep their existing `geometry` keys and behavior.
  No settings migration, new dependency, schema, auth, or CI change is needed.

The root README and technical reference were reviewed and updated. All touched
files remain <=1,700 lines. The runtime entrypoint stays `./dirracuda`; the legacy
`gui/main.py` shim is untouched.

## Validation

PASS: 295 distinct targeted test cases and eight isolated desktop smoke cases.
The final added Start Scan reopen case passed with its 15-test layout suite.
File-size and whitespace checks pass. Largest touched files: `dirracuda`
1,699 -> 1,698 lines, technical reference 1,669 -> 1,671, and dashboard batch
operations 1,625 -> 1,629; each remains within the 1,700-line pause threshold.

Tests use temporary settings stores or fake settings, with external services
mocked. Real Tk checks run on an isolated Xvfb/KWin display, not the HI desktop.

```bash
DISPLAY=:91 ./venv/bin/python -m pytest -q gui/tests/test_window_positions.py gui/tests/test_unified_scan_dialog_layout.py gui/tests/test_unified_scan_dialog.py gui/tests/test_background_window_focus.py gui/tests/test_dashboard_bulk_ops.py gui/tests/test_batch_summary_dialog.py gui/tests/test_server_ops_scenario_matrix.py gui/tests/test_clamav_results_dialog.py gui/tests/test_dirracuda_tmpfs_warning_dialog_schedule.py gui/tests/test_server_list_running_tasks_integration.py gui/tests/test_messagebox_guardrail.py gui/tests/test_theme_style_guardrail.py
DISPLAY=:91 ./venv/bin/python -m pytest -q gui/tests/test_smb_browser_window.py gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py gui/tests/test_server_list_card4.py gui/tests/test_start_scan_dorkbook.py gui/tests/test_running_tasks_cancel.py gui/tests/test_scan_results_dialog.py gui/tests/test_dashboard_queue_aggregation.py gui/tests/test_dashboard_provider_queue.py
DISPLAY=:91 ./venv/bin/python -m pytest -q gui/tests/test_unified_scan_dialog_layout.py
DIRRACUDA_FOCUS_TESTS=1 DISPLAY=:91 ./venv/bin/python -m pytest -q gui/tests/test_window_positions_smoke.py gui/tests/test_background_window_focus_smoke.py
git diff --check
```

The desktop checks cover three fresh Tk/settings cycles without decoration drift,
save on Hide/Destroy, unchanged sizes, no forced focus/remapping, and the previous
background-focus regressions. Unit tests cover malformed/legacy values, negative
coordinates, independent window keys, save debouncing, and retry after save failure.
Start Scan has a direct move/close/reopen layout test.

## HI test

1. Run `./dirracuda`. Move the dashboard and Start Scan onto the desired monitors.
2. Close/reopen Start Scan; it should return to that location at its normal size.
3. Quit/relaunch the app. Both locations should persist. Repeat with Server List
   and a browser or result window; each type keeps its own position.
4. Hide a running monitor after moving it. It should stay hidden during background
   updates; explicit reopening should show it at the chosen location and focus it.

No automatic repositioning is attempted when monitor layout changes, per HI's
requested scope. Native messageboxes and file pickers remain desktop-managed.

Reference: [Tk window-manager geometry semantics](https://www.tcl-lang.org/man/tcl8.6.13/TkCmd/wm.htm).
