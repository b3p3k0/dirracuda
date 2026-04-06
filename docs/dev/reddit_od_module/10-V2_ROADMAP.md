# Reddit OD Module: V2 Objective Roadmap

Date: 2026-04-06  
Mode: GUI-first, experimental, reversible

## Objective 7: Make Reddit rows carry human triage context
Outcome: each stored target row includes short title/body preview text in `notes`.
Tasks:
1. Generate deterministic title/body previews in ingestion service.
2. Apply previews to stored target rows for both `new` and `top` modes.
3. Add tests that confirm truncation, normalization, and parse-body behavior.

## Objective 8: Prefer internal browse experience over system browser
Outcome: analyst opens Reddit targets in built-in FTP/HTTP explorers when possible.
Tasks:
1. Extend explorer bridge to route supported targets to internal browser.
2. Add explicit fallback prompt (`Open in system browser`, `Copy address`, `Cancel`).
3. Add tests for internal-success and fallback-choice branches.

## Objective 9: Add practical bridge from Reddit target to main DB workflow
Outcome: right-click action in Reddit browser can launch prefilled Add Record flow.
Tasks:
1. Add Reddit browser context menu action `Add to dirracuda DB`.
2. Reuse Server List Add Record logic with prefill support.
3. Keep user-confirmed write semantics (no silent auto-insert).
4. Handle non-IP promotion limitations explicitly in UX.

## Objective 10: Reduce user confusion on entry points
Outcome: README shows exactly where Reddit experimental actions live in the UI.
Tasks:
1. Add explicit click-paths for `Reddit Grab (EXP)` and `Reddit Post DB (EXP)`.
2. Keep wording concise and operator-facing.
3. Re-run targeted docs sanity and GUI regression checks.

