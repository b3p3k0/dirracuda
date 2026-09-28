# Scan dialog focus audit

Historical baseline recorded before the fix. HI approved the split below;
see [README.md](README.md) for implemented behavior and remaining scope.

Scope: desktop discovery scans, automatic post-scan work, and related Server List
probe/extract monitors. This is a source audit, not a desktop window-manager
reproduction. No application code changed and no live scans ran.

## Mechanisms

`focus_force()` explicitly requests keyboard focus even when another application
has it. Tk recommends avoiding this in normal application behavior. `lift()` and
topmost changes affect stacking; `deiconify()` can show a hidden window. Creating
or mapping a window may activate it depending on the desktop's policy. These are
not all equivalent to an explicit focus request.
[Source: Tk focus manual](https://www.tcl-lang.org/man/tcl8.6/TkCmd/focus.htm).

`grab_set()` makes a dialog modal within this application; it is not a global
keyboard grab. `wait_window()` waits for destruction while Tk processes events;
it does not itself force focus. Removing forced focus alone therefore does not
remove modal interference or preserve every caller's sequencing automatically.
[Sources: Tk grab manual](https://www.tcl-lang.org/man/tcl8.6/TkCmd/grab.htm),
[Tk tkwait manual](https://www.tcl-lang.org/man/tcl8.6/TkCmd/tkwait.htm).

Two shared helpers amplify the behavior:

- [ensure_dialog_focus](../../../gui/utils/dialog_helpers.py#L12) raises the
  dialog, forces focus, and briefly sets topmost. It exists to keep intentional
  modal dialogs visible, including in VMs and multimonitor setups.
- [safe_messagebox](../../../gui/utils/safe_messagebox.py#L95) raises and forces
  focus to the parent before a native messagebox, then does so again after it
  closes. An asynchronous notice can therefore interrupt twice. Keep the wrapper;
  changing automatic notices must not bypass the repository's messagebox rule.

## Automatic scan and post-scan surfaces

| # | Window | Trigger and current behavior | Source |
|---|--------|------------------------------|--------|
| 1 | Live Scan Output | Each Shodan protocol start and Self-hosted Search/Reddit start deiconifies, raises, and forces focus. Queue transitions reuse this path, including after the user hides the window. Nonmodal. | [Show method](../../../gui/components/dashboard_scan_output_dialog.py#L93) |
| 2 | Scan Results | Automatic completion takes a modal grab, calls the shared focus helper, and focuses Close. Enter and Escape dismiss it. This directly explains accidental dismissal while typing. | [Creation](../../../gui/components/scan_results_dialog.py#L132), [shortcuts](../../../gui/components/scan_results_dialog.py#L487) |
| 3 | Probe Status / Extract Status | Server List batch launch and completion call `show()`, which deiconifies, raises, and forces focus. Completion brings a hidden status window back before showing summaries and destroying the status window. Nonmodal. | [Show method](../../../gui/components/pry_status_dialog.py#L118), [completion](../../../gui/components/server_list_window/actions/batch_status.py#L416) |
| 4 | Preparing Bulk Operations | Automatic preparation for post-scan work opens a transient dialog, takes a modal grab, and waits for the background fetch. No explicit forced focus; no dismissal/cancellation control. | [Fetch dialog](../../../gui/components/dashboard_batch_ops.py#L438) |
| 5 | Bulk Probe Progress / Bulk Extract Progress | Automatic post-scan phase windows are transient and nonmodal, without explicit forced focus on creation. Desktop activation still needs HI testing. Their explicit Running Tasks reopen callbacks force focus. | [Probe](../../../gui/components/dashboard_batch_ops.py#L660), [extract](../../../gui/components/dashboard_batch_ops.py#L1188) |
| 6 | Probe Batch Summary / Extract Batch Summary | Automatic dashboard completion uses a modal grab and waits for dismissal. Server List uses the same builder nonmodally. No explicit forced focus in the builder. Enter/Escape dismiss either variant. | [Builder](../../../gui/components/batch_summary_dialog.py#L40), [dashboard options](../../../gui/components/dashboard_batch_ops.py#L1518), [Server List options](../../../gui/components/server_list_window/actions/batch_status.py#L587) |
| 7 | ClamAV Scan Results | Conditional automatic post-extract results. Dashboard uses modal grab/wait; Server List uses nonmodal display. No explicit forced focus in the builder. Enter/Escape dismiss. | [Builder](../../../gui/components/clamav_results_dialog.py#L121), [shortcuts](../../../gui/components/clamav_results_dialog.py#L256) |
| 8 | Scan/batch error, cancellation, and queue notices | Background completion/failure or delayed stop handling opens native messageboxes. The safe wrapper forces parent focus before and after display. Titles/call sites below. | [Wrapper](../../../gui/utils/safe_messagebox.py#L130) |
| 9 | Analyze Extracted Files? / Analyst | Optional automatic post-extract yes/no prompt, disabled by default. Accepting it can later produce an Analyst success/error notice. All use the safe wrapper. This is a decision prompt, unlike a passive summary. | [Prompt and completion notices](../../../gui/utils/analyst_post_extract.py#L43) |

### Provider and update coverage

- Live Scan Output callers: [SMB](../../../gui/components/dashboard_scan.py#L952),
  [FTP](../../../gui/components/dashboard_scan.py#L1055),
  [HTTP](../../../gui/components/dashboard_scan.py#L1099),
  [Reddit](../../../gui/components/dashboard_scan.py#L748), and
  [Self-hosted Search](../../../gui/components/dashboard_searxng_scan.py#L231).
- Ordinary [log appends](../../../gui/components/dashboard_logs.py#L80) scroll
  text but do not raise/focus windows. Provider progress, bulk-progress ticks,
  and task-registry refreshes likewise do not explicitly force focus.
- Single-provider Self-hosted Search and Reddit completion can show Scan Results.
  Mixed-provider queues suppress their individual popups. Shodan protocol queues
  aggregate results; mixed queues can defer Shodan results/post-scan work to the
  end. Queue failure notices remain separate.
- Legacy Censys completion updates status/balance; opening results is explicit.
  It adds no automatic forced-focus completion surface to this inventory.

### Automatic or delayed native notices

| Titles | Owner |
|--------|-------|
| Protocol Scan Failed (default title); Queued Scans Completed With Failures; Queued Scans Cancelled | [Protocol queue handling](../../../gui/components/dashboard_scan.py#L380) |
| Reddit Ingest Error | [Reddit completion](../../../gui/components/dashboard_scan.py#L861) |
| Self-hosted Search Scan Error | [Search completion](../../../gui/components/dashboard_searxng_scan.py#L362) |
| Scan Cancelled; Scan Monitoring Error | [Scan monitor](../../../gui/components/dashboard_scan.py#L1162) |
| Scan Stopped; Stop Failed | [Delayed stop handling](../../../gui/components/dashboard_scan.py#L1370) |
| Provider Queue Completed With Failures; provider launch error titles | [Queue completion](../../../gui/components/dashboard_provider_queue.py#L384), [launch failures](../../../gui/components/dashboard_provider_queue.py#L82) |
| Bulk Operations Error; Bulk Operations Skipped; Batch Operations Error | [Automatic post-scan operations](../../../gui/components/dashboard_batch_ops.py#L130) |
| Scan Results (fallback if the full results dialog fails) | [Results wrapper](../../../gui/components/dashboard_batch_ops.py#L1603) |
| Analyze Extracted Files?; Analyst | [Opt-in handoff](../../../gui/utils/analyst_post_extract.py#L43) |

Launch-time Scan Error, FTP Scan Error, HTTP Scan Error, configuration/API-key
notices, and provider-busy notices also use messageboxes. They normally follow a
user action, but launch failures may occur during a queued stage transition.
Keep origin/context in mind rather than classifying solely by dialog title.

## Explicit user interactions to distinguish

- Running Tasks deliberately raises/focuses on open/reopen. Registry refresh does
  not: [window implementation](../../../gui/components/running_tasks_window.py#L28).
- Reopening Live Scan Output or a probe/extract monitor deliberately raises and
  focuses it. Reuse of these methods by automatic events is the part to review.
- Start Scan, preflight confirmations, Stop Scan confirmation, Batch Extract
  Settings, and Extension Filter Editor are user-initiated setup/decision windows.
  They use focus/grabs intentionally. Provider Queue Cancelled is an explicit
  cancellation notice, separate from delayed completion notifications.
- Dorkbook, settings editors, and other unrelated modal prompts also use shared
  focus infrastructure. A global helper rewrite would broaden this task into
  unrelated workflows and conflict with the current modal-dialog guardrail.

## Proposed policy for HI review

1. Passive automatic scan/progress/results updates should not force focus, raise
   over typing, or resurrect hidden monitors. Passive result dialogs should not
   block other app windows with a modal grab.
2. Explicit open/reopen actions should still bring the requested window forward.
3. Review automatic error notices and the opt-in Analyst question separately:
   retain visibility/actionability without treating every background outcome as
   an immediate modal interruption. HI has not chosen the replacement UI yet.
4. Preserve result availability, queue ordering, cancellation, and post-scan
   execution when removing waits/modal behavior. Test typing both inside the app
   and in another application; WM activation needs testing on the real desktop.

HI subsequently approved rows 1–7 for the quiet-background policy, preserving
explicit launch/reopen focus. Rows 8–9 (background alerts/Analyst) remain deferred.

## Validation and documentation

PASS: source call chains identify forced focus and modal behavior in the inventory.
This is not a claim that desktop focus behavior has been reproduced automatically.
No pytest run was needed for documentation-only changes; no live services used.

Repeatable audit commands (from repository root):

```bash
rg -n 'focus_force|focus_set|grab_set|ensure_dialog_focus|wait_window|deiconify|\.lift\(' gui/components gui/utils
rg -n 'focus|modal|Running Tasks|Live Scan Output' README.md docs/TECHNICAL_REFERENCE.md
wc -l docs/dev/ui_improvements/README.md docs/dev/ui_improvements/FOCUS_AUDIT.md
git diff --check
```

Both original audit files started at zero lines and remained below 1,200 lines.
The later implementation, documentation review, and validation are recorded in
[README.md](README.md) and [VALIDATION.md](VALIDATION.md).
