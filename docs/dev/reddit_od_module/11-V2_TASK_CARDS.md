# Reddit OD Module: V2 Task Cards (Claude-Ready)

Use one card at a time. No card merging without HI approval.

Read first:
1. `docs/dev/reddit_od_module/09-V2_LOCKED_DECISIONS.md`
2. `docs/dev/reddit_od_module/10-V2_ROADMAP.md`
3. `docs/dev/reddit_od_module/12-V2_VALIDATION_PLAN.md`

---

## Card V2-0: Plan-Only Reality Check (No Code)

Goal:
1. Confirm exact file-touch plan for V2 cards against current repo state.

Scope:
1. Confirm how `notes` is currently written and where to override safely.
2. Confirm internal browser launch APIs for FTP/HTTP.
3. Confirm reusable Add Record hooks for prefill launch from Reddit browser.
4. Confirm test deltas needed per card.

Definition of done:
1. No code changes.
2. Explicit touch list and risk list.
3. PASS/FAIL gates per implementation card.

---

## Card V2-1: Target Notes Preview Capture (A2)

Goal:
1. Store first-120 title/body previews in `reddit_targets.notes`.

Scope:
1. Add deterministic preview helper(s) in `experimental/redseek/service.py`.
2. Apply preview note to each target before `upsert_targets` in both `_run_new` and `_run_top`.
3. Keep parser extraction behavior unchanged; service controls stored-note policy.
4. Add service tests for:
   - normal title/body preview
   - body omitted when parse-body off or deleted/removed
   - 120-char truncation behavior

Primary touch targets:
1. `experimental/redseek/service.py`
2. `shared/tests/test_redseek_service.py`

Definition of done:
1. Stored target rows carry deterministic preview note text.
2. No schema changes required.
3. Existing service tests still pass.

---

## Card V2-2: Internal Explorer First + Fallback Prompt (B4)

Goal:
1. Open targets internally when possible; otherwise present operator-safe fallback choices.

Scope:
1. Extend `experimental/redseek/explorer_bridge.py`:
   - infer normalized URL/protocol as today
   - attempt internal launch for FTP/HTTP(S) targets
   - on failure show explicit 3-option prompt:
     - open in system browser
     - copy address
     - cancel
2. Keep no-probe/no-guess policy.
3. Ensure prompt includes brief failure reason.
4. Add unit tests for:
   - internal launch success
   - unsupported/failed internal launch -> fallback prompt
   - copy-address action
   - cancel path

Primary touch targets:
1. `experimental/redseek/explorer_bridge.py`
2. `shared/tests/test_redseek_explorer_bridge.py`

Definition of done:
1. System browser is no longer default first path for supported targets.
2. Fallback prompt is deterministic and tested.

---

## Card V2-3: Reddit Browser Context Menu -> Add to dirracuda DB (C1, D1)

Goal:
1. Promote Reddit targets into main DB through existing Add Record UX.

Scope:
1. Add right-click context menu in Reddit browser with `Add to dirracuda DB`.
2. Introduce prefill-capable Add Record entrypoint in Server List path.
3. Wire Reddit browser action to call same Add Record logic via callback/injection.
4. Prefill host/protocol/port only (D1).
5. For non-IP targets (current main DB limitation), show clear user guidance and do not write.

Primary touch targets:
1. `gui/components/reddit_browser_window.py`
2. `gui/components/server_list_window/window.py`
3. `gui/components/server_list_window/actions/batch_operations.py`
4. `gui/tests/test_reddit_browser_window.py`
5. `gui/tests/test_server_list_card4.py` (or focused new test file)

Definition of done:
1. Context action exists and is user-confirmed (dialog save).
2. Reused Add Record logic is shared path, not duplicated business rules.
3. No silent writes and no scan-pipeline coupling.

---

## Card V2-4: README Entry-Point Clarity + Validation Report Refresh

Goal:
1. Make Reddit EXP button locations obvious to new users.

Scope:
1. Update README with explicit click paths:
   - `Start Scan` dialog -> `Reddit Grab (EXP)`
   - `Servers` window -> `Reddit Post DB (EXP)`
2. Keep wording concise; no broad README restructuring.
3. Update validation report/checklist artifacts for V2 deltas.
4. match existing tone, tempo and verbosity. apply style guide at https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/AI_AGENT_DOC_STYLE_GUIDE.md

Primary touch targets:
1. `README.md`
2. `docs/dev/reddit_od_module/12-V2_VALIDATION_PLAN.md` (if needed)

Definition of done:
1. New operators can find both Reddit actions from README alone.
2. Existing README sections remain intact.

