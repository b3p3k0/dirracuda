# Web UI Roadmap

Work one card at a time. Do not batch cards unless HI explicitly asks.

## Status

- Prior waves (C0–C28): foundational WebUI shipped (auth/session/CSRF, scans/results/export/config, hardening).
- C29–C34: **SHIPPED** (2026-05-24; see Wave Completion Status in TASK_CARDS.md).
- Active: **C35** (docs/regression closeout, in progress).
- Operating mode: RA-supervised, DA-executed, single-card delivery.

## Phase A - IA And Route Cutover (C29)

**SHIPPED — 2026-05-24.** Replaced flat IA with grouped navigation and canonical nested routes.

- `Scans` became toggle-only parent with children: `shodan`, `searxng`, `reddit`.
- `Extras` became toggle-only parent with children: `dorkbook`, `keymaster`.
- Added `/export` as a real page and moved export controls off `/results`.
- Hard-cut root `/scans` and `/extras` surfaces (404).
- Canonical Shodan route is `/scans/shodan` only.

## Phase B - Shared Job Queue (C30)

**SHIPPED — 2026-05-24.** Introduced one queue view for run/probe workloads across scan surfaces.

- Kept existing `/api/scans*` contract stable for Shodan submit flow.
- Generalized queue snapshot APIs for cross-page visibility via `/api/jobs*`.
- Included runs + probes in global queue model.
- Excluded promotions from queue model.

## Phase C - SearXNG Web Flow (C31)

**SHIPPED — 2026-05-24.** Delivered functional SearXNG page with run/results/probe/promote.

- Added `/scans/searxng` page and API endpoints.
- Supported preflight/run/results.
- Supported row and bulk probe + promote actions.
- Routed run/probe jobs into shared queue.

## Phase D - Reddit Web Flow (C32)

**SHIPPED — 2026-05-24.** Delivered functional Reddit page with run/results/probe/promote.

- Added `/scans/reddit` page and API endpoints.
- Supported feed/search/user modes with validation parity.
- Supported row and bulk probe + promote actions.
- Routed run/probe jobs into shared queue.

## Phase E - Dorkbook Consistency (C33)

**SHIPPED — 2026-05-24.** Delivered web exposure plus desktop/web behavioral alignment.

- Added `/extras/dorkbook` manage/prefill surface.
- Persisted prefill changes immediately to canonical discovery config.
- Aligned desktop behavior to the same immediate-persist contract.

## Phase F - Keymaster Web MVP (C34)

**SHIPPED — 2026-05-24.** Delivered day-to-day key workflows in web without overreaching.

- Added `/extras/keymaster` with unlock/manage/apply.
- Kept secure-mode awareness explicit.
- Deferred secure-mode toggle/reset; provided clear desktop-only helper text.

## Phase G - Docs, Lessons, Regression (C35)

Goal: sync documentation to runtime truth and close validation.

- Update `README.md` and `docs/TECHNICAL_REFERENCE.md` to reflect final route/feature behavior.
- Update `docs/dev/webui/*` planning artifacts (`TASK_CARDS`, `ROADMAP`, `LESSONS_LEARNED`, `FEATURE_PARITY_MATRIX`).
- Run focused and wider regression gates and record exact results.

## Execution Rules (All Cards)

- Confirm issue reproduction first.
- State root cause explicitly.
- Apply smallest safe fix.
- Run targeted validation; broaden only when risk warrants.
- Report exact commands and PASS/FAIL honestly.
- Check touched-file line counts before and after.
- If touched file exceeds 1700 lines: stop and propose modularization before continuing.
- Never commit unless HI explicitly says `commit`.
