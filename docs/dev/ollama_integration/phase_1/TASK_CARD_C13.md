# TASK CARD C13 — Desktop launch, durable task hydration, and report browser

Status: **implementation complete; focused and cumulative acceptance PASS**

## Scope

C13 exposes the completed C2–C12 pipeline through Accessories without weakening
its durability or privacy boundaries. It adds:

- a standalone-directory run service;
- a detached, owner-controlled worker launch and durable cancel/resume controls;
- startup/refresh hydration into the shared Running Tasks registry; and
- a coverage-first, paginated, read-only report browser.

C14 still owns extraction-manifest identity and the opt-in post-extract hook. C15
still owns the final full scale/crash/cancellation release matrix.

## Locked service contract

- A new run inventories one explicit absolute directory, persists the complete
  inventory and immutable C10/C11 identities atomically, then launches the exact
  repository venv worker with only `--run-id`.
- Standalone directories use `source_mode=unknown`; the UI never guesses a host
  identity. The user supplies a report label. C14 supplies authenticated host
  identity for extraction-manifest runs.
- Output is `<output-base>/_analyst/<safe-label>-<run-prefix>/`. The component is
  an ASCII slug plus a run-id prefix, so labels cannot traverse or collide.
- Worker launch uses `shell=False`, `close_fds=True`, `start_new_session=True`,
  `stdin=DEVNULL`, the repository root as cwd, and an owner-only log file under
  the canonical Analyst log directory. stdout/stderr use the same file and are
  never pipes. The log contains only the worker's closed JSON outcome.
- Cancel persists intent with `request_cancel()` before exact pidfd signaling.
  Resume launches a new worker only from a durable resumable state. A resource
  pause must be due and explicitly authorized before launch.
- Hydration reads bounded summaries from `analyst.db`, reconciles the singleton
  lease once before presenting state, and derives progress from durable file and
  chunk rows. In-memory task ids are stable `analyst:<run-id>` identities.
- Complete/abandoned runs are not Running Tasks. They remain discoverable through
  the Analyst run list/report browser.

## Ollama/UI erratum

The earlier UI mockup proposed calling `/api/tags` when the tab opens. C9B later
froze a stronger rule: every Ollama HTTP intent is precharged in the durable
contact ledger, which requires a persisted run and lease. C13 therefore performs
**no pre-run Ollama HTTP**. It displays the fixed qualified model and says that
version/tag/digest readiness is verified by the worker after launch. A future
authenticated LAN/Tailscale gateway may replace the loopback transport in a
separately reviewed card; the MVP remains loopback-only and cloud-disabled.

## Desktop surface

- Accessories → Analyst shows Source directory, Output base, Report label,
  Fast/Deep, fixed model identity, Analyze, Refresh, Resume/Cancel, and Reports.
- Inventory/create/launch, reconciliation, cancellation, and report reads run off
  the Tk thread. Tk mutation and dialog teardown remain on the Tk thread.
- Message boxes use `gui.utils.safe_messagebox`; themed widgets use named styles.
- The browser verifies the committed report manifest before exposing a completed
  run. It pages immutable SQLite inventory/findings; it never loads the entire
  JSONL/HTML or opens an unverified path.
- Coverage appears before findings and inventory. Model findings remain explicitly
  suggested/unreviewed until Accept/Reject is explicit; deterministic hits remain
  separate. Export selection is transient and defaults empty. Select all/none and
  per-row toggles export only model suggestions to atomic 0600 JSONL or guarded CSV.

## Acceptance

- Invalid source/output/label/mode and inventory failure create no run or worker.
- Create is atomic; launch failure leaves a resumable `ready` run with a closed
  service outcome.
- Detached argv/cwd/fd/session/log permissions are exact; no shell/environment
  proxy or source text/path appears in logs or result repr.
- Cancel intent precedes signal. Repeated cancel/refresh/resume is idempotent or
  fails with a closed state, never a second worker contact.
- Hydration survives GUI restart and concurrent refresh; stable task upserts do
  not duplicate rows.
- Report manifest/path tamper fails closed. Pagination enforces exact bounds and
  model rows only come from `complete_model_reviewed` files.
- GUI import has no database, subprocess, parser, Ollama, or network side effect.
- Focused service/GUI tests use temporary public fixtures only; all existing
  Analyst and GUI guardrails remain green.
