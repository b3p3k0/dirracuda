# Jan 26 Refactor – TODO / Work Packages (management tracker)
Status: planning (no code changes applied yet)  
Entrypoint: **xsmbseek** is the single supported GUI launcher (gui/main.py is legacy/delegate only).  
Target platform: Linux preferred; macOS/Windows only if trivially low‑risk.  
UI “advanced” toggle: only the server-list filter expander; no other mode toggles exist.

## Work Package 1 – Thread-safe UI updates (highest priority)
- Goal: ensure all Tk widgets are mutated on the main thread.
- Scope: scan/probe/extract progress callbacks, scan manager, dashboard/server-list updates; add a dispatcher/queue (`after`-based) as needed.
- Deliverables: patched callbacks and helper; notes on touched files; smoke test plan (`./xsmbseek --mock`, start/stop/cancel mid-scan) on Linux.
- Acceptance: zero widget calls from worker threads; manual run without crashes or UI glitches.

## Work Package 2 – Scan path unification & entrypoint cleanup
- Goal: single authoritative scan flow via xsmbseek/ScanManager.
- Scope: make gui/main.py delegate or mark legacy; remove duplicate progress plumbing; ensure close/cancel paths align.
- Deliverables: updated launch flow, brief doc note (README/dev notes), mock run validation.
- Acceptance: starting a scan uses the unified path; legacy entry clearly noted if kept.

## Work Package 3 – Import/package hygiene (post-stability)
- Goal: remove sys.path hacks; formalize packages.
- Scope: add __init__.py to gui/, switch to package imports, adjust entry scripts accordingly; compileall smoke test.
- Deliverables: import updates, `python3 -m compileall gui` success, doc note on new import style.
- Acceptance: no runtime import errors; xsmbseek and tests still launch.

## Work Package 4 – UI/UX polish (existing features only)
- Goal: incremental usability improvements without new features.
- Scope: dashboard scrollability for overflow; apply theme vars to dialogs/log colors; improve Stop button state/label during cancel.
- Deliverables: targeted tweaks; before/after notes; mock run verification.
- Acceptance: no layout regressions; cancel UX clearer.

## Work Package 5 – Logging cleanup
- Goal: reduce ad-hoc prints; keep controllable debug.
- Scope: replace stray prints with logging; ensure subprocess lifecycle/cancel paths logged; retain optional debug flag.
- Deliverables: logging adjustments; confirm no noisy stdout in normal runs.
- Acceptance: behavior unchanged; debug remains opt-in.

## Supervision / execution rules
- Work packages run sequentially to avoid scope drift.
- Claude must present a 5W micro-plan per package (files/functions, rationale, tests, risks/rollback) and await explicit approval before coding.
- Claude must read the Field Guide before proposing changes: https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/AI_AGENT_FIELD_GUIDE.md
- Edge cases to keep in view: cancel during summary; DB lock handling; orphaned subprocesses on window close.
