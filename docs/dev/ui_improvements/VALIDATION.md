# Background-window validation

## Automated checks

PASS: 175 targeted tests covering focus policy, worker completion/cancellation,
provider queues, summaries, Running Tasks, and messagebox/theme guardrails.
External scan/probe/extract services were mocked; no live scans were run.

```bash
DISPLAY=:91 ./venv/bin/python -m pytest -q gui/tests/test_background_window_focus.py gui/tests/test_dashboard_bulk_ops.py gui/tests/test_dashboard_queue_aggregation.py gui/tests/test_dashboard_provider_queue.py gui/tests/test_dashboard_scan.py gui/tests/test_scan_results_dialog.py gui/tests/test_batch_summary_dialog.py gui/tests/test_clamav_results_dialog.py gui/tests/test_server_list_running_tasks_integration.py gui/tests/test_running_tasks_registry.py gui/tests/test_running_tasks_cancel.py gui/tests/test_server_ops_scenario_matrix.py gui/tests/test_messagebox_guardrail.py gui/tests/test_theme_style_guardrail.py
```

PASS: six real Tk smoke cases on an isolated Xvfb display with KWin. They cover
scan/probe/extract/ClamAV summaries, typing in another application, and console
hide -> provider transitions -> explicit reopen. Results remain visible and
nonmodal; Enter in the original entry does not dismiss them. The smoke fixture
requires an EWMH window manager, so a bare Xvfb server cannot produce a false pass.

```bash
DIRRACUDA_FOCUS_TESTS=1 DISPLAY=:91 ./venv/bin/python -m pytest -q gui/tests/test_background_window_focus_smoke.py
```

The isolated display was started with these commands in separate processes:

```bash
Xvfb :91 -screen 0 1280x800x24 -nolisten tcp
dbus-run-session -- env -u LD_LIBRARY_PATH DISPLAY=:91 QT_QPA_PLATFORMTHEME= KWIN_COMPOSE=N kwin_x11 --replace
```

Use an unused display number when repeating. Never run the opt-in focus smoke
test against the HI's active desktop: it intentionally focuses test windows.
An exploratory KWin run with `--no-global-shortcuts` failed because that option
is unsupported locally; the successful command above omits it.

Before implementing the hint, a KWin control experiment showed a normal new
Toplevel stealing focus; the same window with the pre-map EWMH hint retained the
original entry's focus. This is evidence for KWin/X11, not a cross-platform claim.

Initial targeted runs exposed outdated fake Tk windows without the new display
interface. Updated those test doubles; added checks that reject progress grabs
and verify hidden Server List status stays hidden at finalization. Final runs
pass. No production exception handling was weakened to accommodate test doubles.

PASS: whitespace checks, working-doc local-link checks, and before/after line
counts. Every touched file remains below 1,700 lines; the two largest are the
technical reference and dashboard batch-operations module (acceptable band).
The technical reference changed from 1,667 to 1,669 lines; dashboard batch
operations stayed at 1,625. All other touched files are <=1,200 lines (excellent).

```bash
git diff --check
wc -l README.md docs/TECHNICAL_REFERENCE.md gui/components/dashboard_batch_ops.py gui/utils/background_windows.py docs/dev/ui_improvements/*.md
```

The root README and technical reference were reviewed and updated. Read-only
implementation review found no blocking issue in result lifetime, worker waits,
or Xlib argument/resource handling. No dependency, schema, auth, or CI changes.

## HI testing

1. Launch with `./dirracuda`. Start a small scan using your normal configuration.
   Continue typing in another app as providers/protocols change and results appear.
   Expected: typing stays in that app; passive results do not grab focus.
2. Hide Live Scan Output during a queued scan. Expected: later stages leave it
   hidden. Open Running Tasks and reopen it: it comes forward and takes focus.
3. While a scan completes, type in a Dirracuda input (for example a Server List
   filter). Expected: typing continues there; Enter does not dismiss another
   window's result. Click the result and verify its controls/shortcuts work.
4. With post-scan probe/extract enabled, verify work proceeds in order without
   dismissing summaries. Multiple summaries may remain open. Check ClamAV results
   if that feature is configured. Hiding progress must not cancel work.
5. Start a Server List probe or extract; hide its status window. Expected: it does
   not resurface at completion. Its summary remains available as a normal window.

Background error/cancellation/queue messageboxes and the optional Analyst question
still use their existing focus behavior; deciding how to surface those is deferred.
On non-X11 or noncompliant window managers, initial activation still needs separate
platform validation. If the X11 hint cannot be set, a warning is logged and results
remain accessible with no application-issued focus request.
