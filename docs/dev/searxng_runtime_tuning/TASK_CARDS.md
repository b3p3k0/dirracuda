# C11 Task Cards

Last updated: 2026-06-07

## C11A - Retry Shortening and Cancellation

### Deliverable

Add bounded runtime policy values, mature-run hard-retry shortening, explicit
cancelled status, interruptible waits, and desktop cancellation.

### Required behavior

- Preserve fixed 10/20/30 productive-page soft pacing.
- Clamp timeout/retry values in the service.
- Apply the five-page or 50-URL mature threshold.
- Preserve completed rows and sync retained rows after cancellation.
- Connect Running Tasks and unified queue cancellation to one active SearXNG
  event.
- Suppress result/error popups on cancellation.
- Never advance a cancelled provider queue.

### Implementation boundary

- A focused runtime-control satellite should own policy coercion, maturity, and
  interruptible-wait helpers.
- Extract desktop SearXNG orchestration before adding enough logic to push
  `dashboard_scan.py` beyond 1,700 lines.
- The Running Tasks registry already stores cancel callbacks. Add only the
  smallest UI/action needed to invoke a selected task's callback.

### Acceptance

- Cancellation is responsive during pacing and cooldown.
- Network cancellation latency is bounded by request timeout.
- Cancellation during classification removes only unclassified current-page
  rows.
- Cancellation during probing retains classified rows and completed probe data.
- Final status and desktop behavior distinguish cancelled from failed.

## C11B - Start Scan Tuning Controls

### Deliverable

Add three themed, snapped sliders to the SearXNG section and carry their values
through preferences, templates, validation, request construction, and
`RunOptions`.

### Acceptance

- Defaults/ranges match `DECISIONS.md`.
- Current value is visible beside every slider.
- Keyboard and mouse changes snap to the documented step.
- Older saved state loads safely.
- Unchecked SearXNG disables the controls with the rest of its panel.
- Default and minimum dialog sizes remain usable without horizontal overflow.

## C11C - Semantic Live Output Colors

### Deliverable

Add backward-compatible semantic status levels and color SearXNG/Reddit
provider progress, queue transitions, and rollups through the existing ANSI
theme parser.

### Acceptance

- Routine lines remain default foreground. ✓
- Starts/headings are blue. ✓
- Major completed checkpoints are green. ✓
- Warnings/retries/cancellation are yellow. ✓
- Terminal failures are red. ✓
- Copied/history text retains the existing log behavior. ✓
- Shodan output is unchanged. ✓

**Status: COMPLETE (2026-06-07)**

## C11D - Reusable Live Validation

### Deliverable

Add an explicit live SearXNG validation script and complete C11 documentation.

### Acceptance

- Refuses network execution without `--confirm-live`.
- Uses and cleans a temporary DB by default.
- Never opens the primary DB or performs main-table sync.
- Correctly handles same-page retries when checking stage order.
- Supports cancellation through `Ctrl+C`.
- Prints PASS/FAIL for ordering, integrity, counts, telemetry, and cleanup.
- Full regression and one HI-approved live run pass.

**Status: COMPLETE (2026-06-07)**
