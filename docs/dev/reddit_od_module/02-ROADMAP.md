# Reddit OD Module: Objective Roadmap

Date: 2026-04-05
Mode: POC, GUI-only, isolated sidecar

## Objective 1: Sidecar Foundation
Outcome: `redseek` package scaffold + sidecar DB bootstrap and migrations.
Tasks:
1. Create module structure and typed models.
2. Implement sidecar DB path resolution and connection lifecycle.
3. Implement schema creation/migration guard by runtime state.
4. Add store methods for posts/targets/state upserts.

## Objective 2: Feed Client + Parser Core
Outcome: deterministic JSON ingestion and target extraction.
Tasks:
1. Add Reddit JSON client with User-Agent and timeout handling.
2. Implement rate-limit pacing and page cap.
3. Implement parser regex pipeline and normalization.
4. Add parse confidence and protocol classification.

## Objective 3: Ingestion Service
Outcome: end-to-end ingest orchestration for `new` and `top`.
Tasks:
1. Implement `new` incremental cursor semantics.
2. Implement `top` bounded refresh semantics with dedupe.
3. Implement `Replace cache` full wipe behavior.
4. Return structured run summary for UI logs.

## Objective 4: GUI Entry and Run Control
Outcome: analyst can run Reddit ingestion from dashboard.
Tasks:
1. Add `Reddit Grab` dashboard action.
2. Add `reddit_grab_dialog` with options (`sort`, `max posts`, `parse body`, `include nsfw`, `replace cache`).
3. Launch ingestion in background worker with progress + cancellation-safe UX.
4. Show concise run summary and failures.

## Objective 5: Reddit Browser + Explorer Bridge
Outcome: analyst can inspect ingested targets and open manually.
Tasks:
1. Add `Reddit Post DB` browser window with sortable table.
2. Add actions: `Open in Explorer`, `Open Reddit Post`, `Refresh`, `Clear DB`.
3. Implement protocol inference + fallback prompt.
4. Keep all actions user-triggered only.

## Objective 6: Validation + Handoff
Outcome: confidence package for POC promotion decision.
Tasks:
1. Add unit tests for parser/store/service critical paths.
2. Run targeted GUI flow checks for new screens.
3. Document known limitations and failure modes.
4. Produce PASS/FAIL report with exact commands.
