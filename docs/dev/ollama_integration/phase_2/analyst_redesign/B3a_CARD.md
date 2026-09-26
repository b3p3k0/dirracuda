# B3a — Large dir fails visibly: periodic stale-lease reconcile

Status: HELD for DA (codex). One card. Commit per card, no push. Builds on B1/B2 (committed).

## Problem / goal

A worker that dies hard (OOM `SIGKILL` on a large dir like DOE) never runs its release handlers, so
the run stays `state='running'` with a stale lease and NO report and NO error. The only recovery,
`reconcile_lease`, runs **once at dashboard hydration** (guarded by `_analyst_hydration_reconciled`)
and never again while a view stays open — so the operator sees "Running" forever.

Make stale-lease recovery periodic so a dead worker's run flips to `interrupted` (resumable/
deletable) in-session, visibly, without an app restart. Clean failures already land as `interrupted`
via the phase release handlers; this covers the hard-kill case.

Safe by construction: `service.reconcile_for_hydration()` → `lease.reconcile_lease()` first does a
read-only lease-row check and returns `NO_LEASE`/`REATTACHED` with NO write when there is no lease or
the lease is healthy; a stale-but-still-alive worker is `BLOCK_STALE_LIVE` (left alone); it writes
(clears to `interrupted`/`cancelled_pending_resume`) ONLY when the lease-holder process is actually
gone. So running it every refresh is cheap and never yanks a live worker.

## Changes

### 1. Analyst tab refresh — `gui/components/experimental_features/analyst_tab.py`

In `_refresh_runs`'s `work()` thread (analyst_tab.py:1082-1090), reconcile before listing, best-effort
so a reconcile error never breaks the refresh:
```
def work() -> None:
    try:
        from experimental.analyst.service import (
            list_run_summaries,
            reconcile_for_hydration,
        )
        try:
            reconcile_for_hydration()
        except Exception:
            pass
        summaries = list_run_summaries()
    except Exception:
        self._schedule(lambda: self._finish_refresh(None))
        return
    self._schedule(lambda: self._finish_refresh(summaries))
```

### 2. Dashboard hydration loop — `gui/components/dashboard_experimental.py`

The background hydration `_work` (dashboard_experimental.py) reconciles only once (guarded by
`_analyst_hydration_reconciled`) then just re-lists every 5 s. Make it reconcile each cycle,
best-effort and separate from the list so a reconcile error does not blank the summaries:
```
def _work() -> None:
    try:
        from experimental.analyst.service import (
            list_run_summaries,
            reconcile_for_hydration,
        )
        try:
            reconcile_for_hydration()
        except Exception:
            pass
        summaries = list_run_summaries()
    except Exception:
        summaries = None
    _schedule(lambda: _apply(summaries))
```
Remove the now-unused `_analyst_hydration_reconciled` flag set/checks (the init assignment and the
one-shot branch). Leave every other hydration flag and the 5 s cadence unchanged.

## Tests

- `gui/tests/test_analyst_b3a.py` (xvfb, `gui_smoke`): patch `list_run_summaries` and
  `reconcile_for_hydration`; assert `_refresh_runs` calls `reconcile_for_hydration` and still applies
  the summaries; assert that when `reconcile_for_hydration` raises, the refresh still lists (summaries
  applied, not blanked). Prefer driving the tab's `_refresh_runs` worker synchronously (call
  `work()` / the reconcile+list block directly, or stub `threading.Thread` to run inline) rather than
  sleeping on the real thread.
- Existing suites green: `./venv/bin/python -m pytest shared/tests -k analyst -q` and
  `xvfb-run -a ./venv/bin/python -m pytest gui/tests -k analyst -q` (run areas separately).

## Out of scope

Inventory progress/cancel (B3b), a distinct `failed` state or a worker failure-reason string, and
the memory/throughput rework so DOE-scale dirs COMPLETE (all deferred). Do not change lease/reconcile
semantics — only where and how often reconcile is invoked.
