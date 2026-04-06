# Reddit OD Module: Task Cards (Claude-Ready)

Use one card at a time. Do not merge cards unless HI explicitly approves.

---

## Card 0: Plan-Only Reality Check

Goal:
Produce a concrete implementation plan against current repo state, with risks and validation commands, without writing code.

Scope:
1. Confirm file touch list for each objective.
2. Confirm migration and sidecar DB behavior.
3. Confirm `new` vs `top` ingestion semantics.
4. Propose exact tests and manual HI validation steps.

Definition of done:
1. No code changes.
2. Explicit blockers/assumptions listed.
3. Clear PASS/FAIL gates for each later card.

---

## Card 1: redseek Scaffold + Sidecar Store

Goal:
Create isolated module package and sidecar DB layer.

Scope:
1. Add `redseek` package with models/store skeleton.
2. Implement DB init/migration with runtime schema checks.
3. Add tables: `reddit_posts`, `reddit_targets`, `reddit_ingest_state`.
4. Add safe full-wipe API used by `Replace cache`.

Primary touch targets:
1. `experimental/redseek/__init__.py`
2. `experimental/redseek/models.py`
3. `experimental/redseek/store.py`
4. Minimal integration file(s) only if needed for path/config access

Definition of done:
1. Sidecar DB initializes idempotently.
2. Wipe operation is explicit and isolated.
3. No main DB table changes.

Regression checks:
1. Existing app startup unaffected.
2. Existing DB tooling unaffected.

---

## Card 2: Reddit Client + Parser

Goal:
Implement feed client and deterministic target parsing.

Scope:
1. Add `experimental/redseek/client.py` for JSON fetch + pagination.
2. Add `experimental/redseek/parser.py` for extraction and normalization.
3. Add confidence/protocol classification and dedupe key helper.
4. Enforce rate-limit pacing and 429 abort behavior.

Definition of done:
1. Parser returns stable normalized outputs for same input.
2. 429 path returns explicit fail status (no partial hidden success).
3. `new` and `top` feed fetch helpers available.

Regression checks:
1. No scan workflow files modified.
2. Unit tests pass for parser edge cases.

---

## Card 3: Ingestion Service (`new` + `top`)

Goal:
Implement orchestration from fetch -> parse -> store -> state update.

Scope:
1. Add `experimental/redseek/service.py` run options and result summary model.
2. Implement `new` early-stop via `(created_utc, post_id)` cursor compare.
3. Implement `top` bounded refresh semantics with dedupe.
4. Implement replace-cache full wipe before run.

Definition of done:
1. Repeat run on same window does not duplicate targets.
2. Cursor behavior is deterministic and tested.
3. Run summary reports stored posts/targets/skips/errors.

Regression checks:
1. Sidecar DB only.
2. Existing scan CLI/GUI unaffected.

---

## Card 4: GUI Reddit Grab Dialog + Dashboard Hook

Goal:
Expose ingestion controls in GUI.

Scope:
1. Add dashboard button `Reddit Grab`.
2. Add `reddit_grab_dialog.py` with required options.
3. Run service in background with responsive UI.
4. Display concise run result and error details.

Definition of done:
1. Dialog validates input and triggers run.
2. UI remains responsive during ingestion.
3. No impact to Start Scan lock behavior.

Regression checks:
1. SMB/FTP/HTTP scan launch still works.
2. Dashboard actions remain stable.

---

## Card 5: Reddit Browser + Explorer Bridge

Goal:
Allow analyst review and manual exploration.

Scope:
1. Add `reddit_browser_window.py` table view.
2. Add actions: open target, open post URL, refresh, clear DB.
3. Add `explorer_bridge.py` protocol inference + prompt fallback.
4. Keep unknown-protocol actions explicit and safe.

Definition of done:
1. Rows load from sidecar DB and sort/filter correctly.
2. Open action infers protocol when possible.
3. Prompt appears only when unresolved.

Regression checks:
1. Existing browser windows unchanged.
2. No automatic probe/extract behavior introduced.

---

## Card 6: Validation, Docs, and POC Exit Criteria

Goal:
Stabilize module and document real limits.

Scope:
1. Add/expand tests for parser/store/service.
2. Add manual HI test checklist for GUI flows.
3. Update docs with caveats and known limits.
4. Publish PASS/FAIL report with command evidence.

Definition of done:
1. Automated checks pass for touched components.
2. Manual HI checklist executed or marked pending.
3. Remaining risks are explicit.

Regression checks:
1. Targeted checks for dashboard and existing scan actions.
2. No schema changes outside sidecar DB.
