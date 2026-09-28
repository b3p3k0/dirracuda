# UI improvements

## Current task: quiet background scan windows

Status: approved scope implemented; automated validation complete, HI testing next.
This task is separate from the completed Dorkbook work.

The reported problem is background scan activity interrupting typing by activating
status/results dialogs. Enter can then dismiss a result before the user reads it.
See [FOCUS_AUDIT.md](FOCUS_AUDIT.md) for the original inventory and
[VALIDATION.md](VALIDATION.md) for validation commands and HI steps.

HI approved quiet automatic progress/results and intentional focus for explicit
open/reopen actions. Background alerts and the optional Analyst decision prompt
remain deferred, with their existing behavior preserved.

## Implemented behavior

- Live Scan Output initially opens without requesting focus; later provider and
  protocol starts update its title without raising it or undoing Hide.
- Server List Probe/Extract Status still focuses on explicit launch/reopen, but
  completion no longer calls its focus-taking `show()` method.
- Scan Results, probe/extract summaries, and automatic ClamAV results remain open
  without modal grabs or dismissal waits. Multiple summaries can coexist; each
  retains its own data and controls. Clicking a result still permits its shortcuts.
- Preparing Bulk Operations and bulk progress windows remain nonmodal. Worker
  waits still preserve fetch -> probe -> extract ordering and UI-thread teardown.
- Passive windows no longer stay stacked above their parent. On X11, a small
  helper uses the existing system X11 library to set the standard no-activation
  hint before mapping. No package/dependency change is needed. If the hint cannot
  be set, it logs a warning and leaves the window accessible without forcing focus.
- Other Tk backends retain their desktop's initial mapping policy. No delayed
  attempt to restore focus runs, avoiding a second interruption/race.

## Working constraints

- Use `./dirracuda` as the runtime entrypoint; `gui/main.py` is a legacy shim.
- Preserve the GUI-to-CLI subprocess boundary, scan sequencing, cancellation,
  result persistence, and existing settings/data contracts.
- Use `./venv/bin/python -m pytest` with targeted GUI tests.
  No separate lint command was identified in AGENTS.md or pytest.ini.
- Keep external services mocked in tests. No live scans are needed for validation.
- Keep Tk lifecycle changes on the UI thread and retain safe messagebox routing.
  The current modal-dialog focus guardrail must be reconciled explicitly with
  any proposed modeless conversion; do not disable the shared helper globally.
- Check touched file lengths before and after changes. Pause for modularization
  if a touched file exceeds 1,700 lines. New audit notes are below 1,200 lines.
- Make the smallest safe change after review. HI authorized a completion commit;
  do not push.
- Review README and technical documentation at wrap-up; record reusable lessons
  when implementation establishes new behavior or guardrails.

## Decisions and lessons from the audit

- Background display and explicit user reopening need separate policies. Several
  existing methods serve both, which makes a blanket removal risky.
- Nonmodal does not mean nonintrusive: a nonmodal window can still force focus.
- A local modal grab and a forced keyboard-focus request are different mechanisms.
  Removing only the latter leaves other app windows blocked by the former.
- `wait_window()` also controls some post-scan sequencing. Preserve that sequencing
  deliberately when making results nonblocking.
- Per-line log updates were already quiet; provider/protocol transitions caused
  the repeated focus requests.
- Withdraw a new passive Toplevel immediately, before geometry/theme calls can
  map it. Apply the X11 activation hint to Tk's WM wrapper, not its inner widget.
- Do not rely on `focusmodel("passive")`: it is already Tk's default and does not
  prevent the WM activating a new window. A KWin/Xvfb control experiment reproduced
  focus loss without the hint and retention with it.
- Keep explicit user-open focus helpers intact. Existing modal prompts still need
  their visibility guardrail; this change removes modality only from passive UI.

## Documentation review

Reviewed the root README's provider completion and monitor descriptions and
`docs/TECHNICAL_REFERENCE.md` sections on Running Tasks and long-running monitors.
Updated both to describe passive results, preserved hidden monitors, explicit
reopening, worker sequencing, and the scope of the X11 activation protection.

Implementation references: [Tk focus](https://www.tcl-lang.org/man/tcl8.6/TkCmd/focus.htm),
[Tk grabs](https://www.tcl-lang.org/man/tcl8.6/TkCmd/grab.htm),
[Tk event-loop waits](https://www.tcl-lang.org/man/tcl8.6/TkCmd/tkwait.htm), and
[EWMH user-time hint](https://specifications.freedesktop.org/wm/1.5/ar01s05.html).

Startup references: [AI-HI field guide](https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_FIELD_GUIDE.md),
[documentation style guide](https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_DOC_STYLE_GUIDE.md),
[repository instructions](../../../AGENTS.md), and prior lessons in
[scan dialogs](../scan_dialogs/LESSONS_LEARNED.md),
[provider promotion](../promote_reddit_and_websearch/LESSONS_LEARNED.md), and
[keyboard controls](../kbd_ctrl_improve/LESSONS_LEARNED.md).
