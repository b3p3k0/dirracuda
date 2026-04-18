# Reddit OD Module: V3 Task Cards (Claude-Ready)

Date: 2026-04-17
Execution model: one card at a time, with explicit PASS/FAIL evidence

Read first:
1. `docs/dev/reddit_od_module/14-V3_SPEC.md`
2. `docs/dev/reddit_od_module/15-V3_ASCII_SKETCHES.md`
3. `docs/dev/reddit_od_module/09-V2_LOCKED_DECISIONS.md`

## Global Rules (All Cards)

1. Confirm card scope before touching code.
2. Surgical edits only; no broad refactors.
3. Preserve existing behavior outside card scope.
4. Add/adjust tests for every new behavior branch.
5. Run targeted validation and report exact commands + PASS/FAIL.
6. No commit unless HI explicitly says `commit`.
7. If blocked, report blocker + exact HI unblock commands + expected result.

## File Size Rubric (Touched files)

- <=1200: excellent
- 1201-1500: good
- 1501-1800: acceptable
- 1801-2000: poor
- >2000: unacceptable unless explicitly justified

Stop-and-plan rule:
- If a touched file exceeds 1700 lines, stop and provide modularization plan before continuing.

## Required Response Format (Per Card)

- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + short steps)

---

## Card V3-0: Plan-Only Reality Check (No Code)

Goal:
1. Confirm V3 file-touch plan and test deltas against current repo state.

Scope:
1. Confirm current client URL construction points and top-week hardcode location.
2. Confirm service option validation and mode dispatch shape.
3. Confirm dialog field/validation hooks for adding mode/query/username/top-window controls.
4. Confirm regression test suites that must stay green.

Definition of done:
1. No code changes.
2. Concrete touch list per upcoming card.
3. Validation command list per upcoming card.

---

## Card V3-1: Top Window Expansion (Feed top)

Goal:
1. Support top windows: `hour/day/week/month/year/all`.

Scope:
1. Add top-window option to ingest options and validation path.
2. Add top-window UI control in Reddit Grab dialog (visible when sort=top).
3. Pass selected top-window into client request for top mode.
4. Preserve `new` mode behavior unchanged.
5. Add compatibility read fallback for legacy ingest-state key `top` when selected window is `week`.
6. Save top state under `top:<window>`.

Primary touch targets:
1. `experimental/redseek/client.py`
2. `experimental/redseek/service.py`
3. `experimental/redseek/models.py` (if options/state structures require update)
4. `experimental/redseek/store.py` (if state-key helper logic added)
5. `gui/components/reddit_grab_dialog.py`
6. `shared/tests/test_redseek_client.py`
7. `shared/tests/test_redseek_service.py`

Definition of done:
1. Top requests emit correct `t=` for all six windows.
2. Feed top window selection is configurable in UI.
3. Legacy `top` state remains compatible for week path.

Validation:
```bash
python3 -m py_compile \
  experimental/redseek/client.py \
  experimental/redseek/service.py \
  gui/components/reddit_grab_dialog.py
./venv/bin/python -m pytest \
  shared/tests/test_redseek_client.py \
  shared/tests/test_redseek_service.py \
  gui/tests/test_dashboard_reddit_wiring.py -q
```

HI test needed:
- Yes
- Steps:
1. Open Reddit Grab and select sort=top.
2. Verify top-window selector appears and accepts all six values.
3. Run at least two windows (for example `hour` and `year`) and confirm clean completion.

---

## Card V3-2: Search Mode (Subreddit-only)

Goal:
1. Add keyword search mode scoped to r/opendirectories.

Scope:
1. Add `mode=search` in options + UI.
2. Add required `query` field validation (non-empty).
3. Implement search endpoint request:
   - `/r/opendirectories/search.json`
   - `q=<query>`
   - `restrict_sr=1`
   - `sort=<new|top>`
   - `t=<window>` only when sort=top
4. Use dedupe-based ingest semantics (no cursor-stop assumptions).

Primary touch targets:
1. `experimental/redseek/client.py`
2. `experimental/redseek/service.py`
3. `gui/components/reddit_grab_dialog.py`
4. `shared/tests/test_redseek_client.py`
5. `shared/tests/test_redseek_service.py`

Definition of done:
1. Search mode available and validates query.
2. Requests include `restrict_sr=1` always.
3. Search ingest stores/dedupes without regressions.

Validation:
```bash
python3 -m py_compile \
  experimental/redseek/client.py \
  experimental/redseek/service.py \
  gui/components/reddit_grab_dialog.py
./venv/bin/python -m pytest \
  shared/tests/test_redseek_client.py \
  shared/tests/test_redseek_service.py \
  gui/tests/test_dashboard_reddit_wiring.py -q
```

HI test needed:
- Yes
- Steps:
1. Open Reddit Grab -> mode Search.
2. Verify empty query is blocked.
3. Run with a sample query and confirm completion + rows in Reddit Post DB.

---

## Card V3-3: User Submitted Mode

Goal:
1. Add username ingestion mode for submitted posts.

Scope:
1. Add `mode=user` in options + UI.
2. Add required username validation (basic syntax and non-empty).
3. Implement user submitted endpoint request:
   - `/user/<username>/submitted.json`
   - `sort=<new|top>`
   - `t=<window>` only when sort=top
4. Keep comments out of scope.
5. Use dedupe-based ingest semantics.

Primary touch targets:
1. `experimental/redseek/client.py`
2. `experimental/redseek/service.py`
3. `gui/components/reddit_grab_dialog.py`
4. `shared/tests/test_redseek_client.py`
5. `shared/tests/test_redseek_service.py`

Definition of done:
1. User mode available and username validated.
2. Submitted endpoint used (not comments endpoint).
3. Ingest path remains stable.

Validation:
```bash
python3 -m py_compile \
  experimental/redseek/client.py \
  experimental/redseek/service.py \
  gui/components/reddit_grab_dialog.py
./venv/bin/python -m pytest \
  shared/tests/test_redseek_client.py \
  shared/tests/test_redseek_service.py \
  gui/tests/test_dashboard_reddit_wiring.py -q
```

HI test needed:
- Yes
- Steps:
1. Open Reddit Grab -> mode User.
2. Verify empty username is blocked.
3. Run with a known username and confirm clean completion + row updates.

---

## Card V3-4: Input Persistence + Regression Closeout

Goal:
1. Persist and restore V3 mode inputs with no regression in existing workflows.

Scope:
1. Persist:
   - mode
   - sort
   - top_window
   - query
   - username
   - existing toggles/max-post settings
2. Restore values on dialog reopen.
3. Add targeted tests for persistence behavior.
4. Run focused regression suite for Reddit/experimental flows.

Primary touch targets:
1. `gui/components/reddit_grab_dialog.py`
2. `experimental/redseek/service.py` (if option defaults or validation touched)
3. `gui/tests/test_dashboard_reddit_wiring.py`
4. `gui/tests/test_experimental_features_dialog.py`
5. `shared/tests/test_redseek_service.py` (if needed)

Definition of done:
1. Values persist and reload correctly.
2. No regressions in existing Reddit open flows.

Validation:
```bash
python3 -m py_compile \
  gui/components/reddit_grab_dialog.py \
  experimental/redseek/service.py
./venv/bin/python -m pytest \
  shared/tests/test_redseek_client.py \
  shared/tests/test_redseek_service.py \
  gui/tests/test_dashboard_reddit_wiring.py \
  gui/tests/test_reddit_browser_window.py \
  gui/tests/test_experimental_features_dialog.py -q
```

HI test needed:
- Yes
- Steps:
1. Set non-default mode/query/username/window and close dialog.
2. Reopen dialog and confirm values are restored.
3. Run one ingest per mode and confirm behavior remains stable.
