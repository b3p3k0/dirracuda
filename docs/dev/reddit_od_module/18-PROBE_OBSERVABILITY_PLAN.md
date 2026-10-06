# Reddit ingest — probe observability & safety plan

Role: PA (Planning Agent). Branch: `development`. Date: 2026-10-06.
Status: plan, pre-implementation. HI reviews this file; approval gates the DA cards.

## Background (why this plan exists)

HI reported the Reddit OD ingest "hanging" — an ~8-minute real run against an
expected 30–90s. Investigation (AA, recorded in session) isolated two distinct,
independent issues:

1. **Perceived hang.** With *Bulk probe* enabled, `run_ingest` runs the sidecar
   probe pass inside a single call with **no progress output, no cancellation,
   and no aggregate time/target bound**. A feed of many dead/slow open-directory
   hosts makes the pass run for minutes while the GUI sits frozen at
   "Fetching Reddit posts…". HI confirmed: "appears hung after run, but waiting
   shows results." Probe output is correct; it runs blind.
2. **"No results."** Reddit now hard IP-rate-limits the anonymous RSS endpoint
   (~1 request / ~20s, no `Retry-After`). The client raises `RateLimitError` on
   429; the Reddit-only path surfaces it as a cryptic "Reddit ingest failed:
   HTTP 429". Verified healthy: a live cold request parsed 10 real posts. "No
   results" = 429, not a parse bug.

Evidence labels: items above are **fact** (verified by live tests + code read),
except the exact host-count that produced 8 min, which is **inference**.

## Goal

Make the probe pass **observable, cancellable, and bounded**, and make the
rate-limit outcome **legible** — without changing network/request behavior.

## Non-goals (explicitly out of scope here)

- **Rate-limit retry/backoff (A-1a).** Changing retry/network behavior is
  genuine Elevated Risk and could reintroduce a hang if done carelessly.
  Deferred to a separate plan. This plan only improves the *message* (Card D).
- Re-enabling or redesigning the probe engine's own traversal limits.
- Any change to the RSS client, parser, feed URLs, or DB schema.
- Bulk historical backfill or changes to sync/promotion logic.

## Current state (grounded call sites)

| Concern | Location | Note |
|---|---|---|
| Probe pass loop | `experimental/redseek/service.py:280-341` (`_probe_targets_for_keys`) | `ThreadPoolExecutor(max_workers≤8)`, `as_completed` loop — the natural per-target throttle point. No progress, no cancel wiring, no budget. |
| Probe pass entry | `experimental/redseek/service.py:344-368` (`_finalize_result_with_optional_probe`) | Only caller of `_probe_targets_for_keys` (service.py:356). |
| Ingest entry | `experimental/redseek/service.py:783` (`run_ingest`) | Callers: `gui/components/dashboard_scan.py:774`, `experimental/webui/app.py:756`. |
| Per-target probe | `gui/utils/sidecar_probe.py:84` (`run_sidecar_probe`) | Already accepts `cancel_event`; passed a fresh never-set `Event()` at `service.py:105`. |
| Engine cancel honored | `gui/utils/http_probe_runner.py:162,179` | Engine checks `cancel_event.is_set()` between dirs/requests. |
| GUI worker + logging | `gui/components/dashboard_scan.py:759-767` (`_ui_log`) | Marshals to UI thread via `dash.parent.after(0, …)`; routes `_log_status_event` and (reddit-only) `_update_progress_summary`. |
| Result handler | `gui/components/dashboard_scan.py:852-866` (`_on_reddit_scan_done`) | `result.error` → generic error dialog. `result.rate_limited` computed but unused for messaging. |
| Cancel precedent | `gui/components/dashboard_searxng_scan.py:225`; `gui/components/dashboard_provider_queue.py:360` | `dash._searxng_cancel_event` set on launch, fired by `cancel_provider_queue`. Mirror for Reddit. |
| Scan output Cancel control | `gui/components/scan_dialog_layout.py:931-937` | Existing "Cancel" button → `_cancel_scan`. |
| Probe limits source | `gui/components/scan_provider_options.py:59` (`resolve_probe_limits`) | Where probe tuning is read; add budget fields here. |

## Impact

- `run_ingest` gains keyword-only params (`progress_cb`, `cancel_event`, probe
  budget). Both existing callers keep working via defaults (`None`) — verified:
  `webui/app.py:756` passes positional `options`/`db_path` only.
- Probe counts already include `unprobed`/`skipped`; `IngestResult` already has
  `probe_skipped`. Budget-skipped targets reuse these — no new result fields.
- No change to request count, timeouts, URLs, or DB writes.

## Task cards

One card = one patch = one reviewable diff. Sequence is A → B → C → D; C depends
on B's `cancel_event`. Each card commits `wip:` checkpoints freely; RA gives the
acceptance commit.

---

### Card A — Probe progress reporting  (was B-2)

**Goal.** Emit a live "Probing N/M" indicator so a running probe is never
mistaken for a hang. No console flooding.

**Change.**
- Add keyword-only `progress_cb: Optional[Callable[[str], None]] = None` to
  `run_ingest` → `_finalize_result_with_optional_probe` → `_probe_targets_for_keys`.
- In the `as_completed` loop (`service.py:300`), after each target resolves, call
  `progress_cb` **once per completed target** with a terse line:
  `Probing targets {done}/{total} — {clean} clean, {issue} flagged, {unprobed} unprobed`.
- In `dashboard_scan.py` `_worker`, pass a `progress_cb` that routes to the
  live summary via `_update_progress_summary` (in-place line), **not** the
  scrollback log. Emit exactly one `_log_status_event` at probe start
  (`Probing {N} targets…`) and one at end (`Probe complete: …`).
- Do **not** use the engine's per-directory `progress_callback`
  (`http_probe_runner.py:166`) — it fires per request and would flood.

**Non-literals.** No magic numbers introduced.

**Acceptance criteria.**
- With bulk probe on and ≥2 targets, the GUI shows an updating `Probing n/m`
  line during the pass; scrollback gains only start/end lines.
- `progress_cb=None` (default, webui path) behaves exactly as today.
- Progress text matches the final probe summary counts.

**Validation.**
- `./venv/bin/python -m pytest shared/tests/test_redseek_service.py gui/tests/test_probe_limits_providers.py gui/tests/test_dashboard_scan.py -q`
- New unit test: a fake `progress_cb` records one call per target with monotonic
  `done` and correct final counts.
- `./venv/bin/python -m py_compile experimental/redseek/service.py gui/components/dashboard_scan.py`
- Manual (HI): bulk-probe `feed/new` run shows live progress, no hang feel.

---

### Card B — Cancellable probe pass  (was B-3)

**Goal.** A Cancel/Stop actually stops the probe, reusing the engine's existing
`cancel_event` support.

**Change.**
- Add keyword-only `cancel_event: Optional[threading.Event] = None` to
  `run_ingest` → `_finalize_result_with_optional_probe` → `_probe_targets_for_keys`.
- Thread it into `run_sidecar_probe(cancel_event=cancel_event)` (replace the
  fresh `Event()` at `service.py:105`/`292`).
- In the `as_completed` loop: before submitting remaining work and on each
  completion, if `cancel_event.is_set()`, stop scheduling, count the rest as
  `skipped`, and return current counts (connection still commits what completed).
- GUI wiring (mirror searxng): in `start_reddit_scan`, create
  `cancel_event = threading.Event()`, store `dash._reddit_cancel_event`, pass to
  `run_ingest`; clear to `None` in `_on_reddit_scan_done`.
- Fire it from the two existing cancel entry points:
  - queue path: add a `current == "reddit"` branch in `cancel_provider_queue`
    (`dashboard_provider_queue.py`) that sets `dash._reddit_cancel_event`.
  - reddit-only path: the scan output dialog Cancel/close
    (`scan_dialog_layout.py:931`, `_cancel_scan`) sets the same event.

**Acceptance criteria.**
- Cancelling mid-probe stops within one target's worst case (~one request
  timeout, ≤~50s default) and the summary reports the rest as skipped.
- Already-probed targets persist; no partial-write corruption.
- `cancel_event=None` default path unchanged.

**Validation.**
- New test: set the event after the first target resolves → remaining counted
  as skipped, no further `run_sidecar_probe` calls (monkeypatched).
- `./venv/bin/python -m pytest shared/tests/test_redseek_service.py gui/tests/test_dashboard_provider_queue.py -q`
- Manual (HI): start a bulk-probe run, hit Cancel, confirm it stops promptly.

---

### Card C — Probe-pass safety budget  (was B-1)

**Goal.** A pathological feed can never silently run for many minutes, even with
progress + cancel. Bounds the aggregate.

**Change.**
- Add budget inputs to `IngestOptions` + read in `resolve_probe_limits`
  (`scan_provider_options.py:59`): `probe_max_targets`, `probe_deadline_seconds`.
- Named defaults in `service.py` (proposed, pending HI confirm — see Open):
  - `PROBE_PASS_MAX_TARGETS = 50` — a 100-post feed rarely yields more concrete
    HTTP/FTP targets; caps worst-case fan-out.
  - `PROBE_PASS_DEADLINE_SECONDS = 180` — generous ceiling vs the 30–90s norm;
    well under the observed 8-min pathological case.
- Enforce in `_probe_targets_for_keys`: cap submitted targets at
  `probe_max_targets` (extras → `skipped`); track `time.monotonic()` start, and
  once elapsed > `probe_deadline_seconds`, set the shared `cancel_event`
  (Card B) so in-flight probes wind down and the remainder are `skipped`.
- Surface budget-skips in the end-of-run summary and the probe rollup so the
  user knows coverage was capped (SOP: "No silent caps").

**Acceptance criteria.**
- > `probe_max_targets` candidates → only the cap is probed; excess `skipped`
  and reported.
- A pass exceeding the deadline terminates near the deadline (+≤ one request
  timeout) with remaining `skipped`.
- Defaults are named constants with inline rationale; values are settings-overridable.

**Validation.**
- New tests: (a) cap enforcement with a stub probe; (b) deadline enforcement
  using injected monotonic time / a fast stub that exceeds a tiny deadline.
- `./venv/bin/python -m pytest shared/tests/test_redseek_service.py gui/tests/test_probe_limits_providers.py -q`
- Manual (HI): a large `feed/new` bulk-probe run ends within the deadline and the
  summary states how many targets were skipped by the cap.

---

### Card D — Rate-limit message clarity  (A-1b only)

**Goal.** Replace the cryptic "Reddit ingest failed: HTTP 429" with an
actionable rate-limit message. GUI-only; no network behavior change.

**Change.**
- In `_on_reddit_scan_done` (`dashboard_scan.py:852`), branch on
  `result.rate_limited` **before** the generic `result.error` dialog: show
  `"Reddit rate-limited this request. Wait ~30–60s and try again (space new/top
  runs apart)."` Keep the queue-managed path reporting failure to the queue.

**Acceptance criteria.**
- A 429 result shows the rate-limit guidance, not "failed: HTTP 429".
- Non-429 errors are unchanged.

**Validation.**
- New test: `IngestResult(rate_limited=True, error="HTTP 429")` → rate-limit
  message path (assert the shown text / hook args).
- `./venv/bin/python -m pytest gui/tests/test_dashboard_scan.py -q`
- Manual (HI): fire two runs back-to-back to trip 429; confirm the new message.

## Risks

| Risk | Mitigation |
|---|---|
| Progress callback runs on the worker thread touching Tk | Route only through existing `_ui_log`/`_update_progress_summary`, which already marshal via `after(0, …)`. |
| Cancel leaves a half-written DB | Loop commits only completed targets; cancel stops *scheduling*, never aborts mid-write. |
| Budget defaults too tight → under-probing | Values are named, justified, and settings-overridable; surfaced in summary; HI confirms defaults (Open). |
| Signature change breaks a caller | Keyword-only params default to `None`; both callers enumerated (dashboard_scan.py:774, webui/app.py:756). |

## Rollback

Each card is an isolated `wip:` series on `development`. Revert the card's commit
range; defaults (`progress_cb=None`, `cancel_event=None`, budget unset → behaves
as uncapped) restore prior behavior. No schema/data migration to undo.

## Elevated-Risk assessment

Cards A, B, D: instrumentation / existing-mechanism wiring / UI text — **no**
change to request construction, timeouts, URLs, or auth. Not Elevated Risk.
Card C: changes probe *scope* (a safety limit that reduces work) — low risk, but
HI confirms the default ceilings. The genuine Elevated-Risk item (retry/backoff,
A-1a) is **deferred** to a separate plan.

## Open (HI decisions)

1. Confirm Card C defaults: `PROBE_PASS_MAX_TARGETS = 50`,
   `PROBE_PASS_DEADLINE_SECONDS = 180` — or set your own ceilings.
2. Confirm sequence A → B → C → D and that each lands as its own card/patch.
3. DA assignment: fast-lane (one agent wears DA+RA, stated) for A/B/D, or
   full-process with RA separate from DA? (Charter: RA must differ from DA.)
