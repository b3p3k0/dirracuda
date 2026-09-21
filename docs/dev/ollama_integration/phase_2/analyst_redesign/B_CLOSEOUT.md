# AN1 closeout — stability & lifecycle fixes (B1–B3b)

Date: 2026-09-20. Branch `feature/ollama-analyst` (not pushed). Each card codex-implemented,
Claude-validated in the real venv, committed per card.

## Context

AN1 (the LLM host READ that actually analyzes) shipped in `1e3757d` and, on a good run, produces
the read-first report intended (owner/subject, contacts, semantic top exposures, ranked FACTS). Real
operator testing then surfaced stability/UX bugs that blocked calling AN1 done. These are those
fixes.

## Shipped

- **B1 — blank report → read retry + honest busy state** (`826a7bc`). The sidecar runs in DELETE
  journal mode (readers/writers exclusive) with a 250 ms timeout and no retry on the viewer's reads,
  so opening a report while a run was writing hit `SQLITE_BUSY`, the error was swallowed, and the
  view rendered blank (or a misleading "Legacy run"). Added `store.run_read` (bounded read-only busy
  retry, 2 s, 6 attempts), routed the viewer reads through it, made busy distinguishable
  (`AnalystStoreBusy` / `ServiceFailure.BUSY`), and gave the report window a "busy — Retry" state
  that preserves any shown report. Journal mode + owner-only 0600 guards unchanged.
- **B2 — delete run (print-jobs style)** (`e06defa`). `store.delete_run` removes all run-scoped rows
  across the sidecar in one transaction (`PRAGMA defer_foreign_keys=ON` for the cyclic
  chunks↔attempts FK), guarded terminal-only + no active lease; `service.delete_run` best-effort
  removes the run's `output_root` dir + `<run_id>-*.log` (never following a symlink, only the run's
  own owner-owned artifacts). GUI Delete danger button, multi-select, enabled only for terminal runs,
  with a confirm dialog.
- **B3a — periodic stale-lease reconcile** (`6c03b4f`). A hard-killed (OOM) worker left the run
  stuck `running` forever because `reconcile_lease` ran only once at hydration. Now reconcile runs on
  each analyst-tab refresh and each dashboard hydration cycle, best-effort. `reconcile_lease` is
  read-only unless a dead lease needs clearing and leaves a live worker alone, so it's cheap. A dead
  worker's run now flips to `interrupted` (resumable/deletable) in-session.
- **B3b — interruptible inventory + progress** (`174f34c`). The pre-run whole-tree hash showed a
  static label with no count and could not be stopped. Added a progress callback (every 512 entries →
  "Inventorying… N files"), forwarded `cancel_check`/`progress_callback` through
  `create_and_launch`/`create_directory_run`/`inventory_tree`, and a Cancel button shown only during
  a launch. Does not make DOE-scale dirs COMPLETE — makes the inventory phase visible and cancellable.

## Evidence

`./venv/bin/python -m pytest shared/tests -k analyst -q` → 1553 passed.
`xvfb-run -a ./venv/bin/python -m pytest gui/tests -k analyst -q` → 48 passed.
(Run areas separately; see the known cross-suite Tk abort note.) Each card added targeted tests
(B1 retry/busy surfacing + window state; B2 delete row removal/guards/disk safety; B3a reconcile
before-list + failure tolerance; B3b progress/cancel + kwarg forwarding).

## Deferred (recorded, not done)

- Sidecar → WAL (operator chose retry over WAL for B1).
- DOE memory/throughput rework so 10k+ dirs actually COMPLETE (double full-file hash; full handoff
  materialized then re-loaded/compared in phase 2). B3a/B3b only make such runs fail visibly and be
  cancellable.
- A distinct `failed` run state + a worker failure-reason string surfaced from the worker outcome
  (currently a fatal worker exit becomes `interrupted` via reconcile, visible but without a reason).
- AN2 (content excerpts) and AN3 (facts noise) — analysis-depth work, resumes after stability.

## Operator re-test

1. Open a report while a run is actively writing → shows "busy — Retry" (not blank); a prior report
   stays visible; Retry reloads.
2. Select finished runs → **Delete** → rows and report files gone, list refreshes; Delete is disabled
   for active/resumable runs.
3. A worker that dies (or is killed) → within a refresh cycle the run shows **Interrupted** (Resume
   or Delete available), not "Running" forever.
4. Launch a large dir → status counts up ("Inventorying… N files"); **Cancel** aborts it with
   "Inventory cancelled."
