# C11 - SearXNG Runtime Controls and Observability

Status: PA/RA specification
Last updated: 2026-06-06

## Objective

Improve long SearXNG runs without increasing upstream pressure:

1. Shorten late-run recovery waits after useful results are already durable.
2. Make desktop SearXNG work cancellable without discarding completed rows.
3. Expose three bounded recovery controls in Start Scan.
4. Add semantic color to Live Scan Output.
5. Turn the one-off live validation into a reusable opt-in script.

## Baseline

Commit `662d2a2` processes SearXNG sequentially:

```text
fetch page
  -> store raw rows
  -> classify
  -> retain open indexes
  -> probe retained rows
  -> wait only the unused pacing window
  -> fetch next page
```

Productive pages with upstream warnings use fixed 10, 20, and 30 second
backoff windows. This contract does not change in C11.

## Runtime Policy

### User-tunable values

| Value | Default | Minimum | Maximum | UI step |
| --- | ---: | ---: | ---: | ---: |
| SearXNG request timeout | 15s | 5s | 60s | 1s |
| Short hard retry | 30s | 5s | 60s | 5s |
| Long hard retry | 180s | 60s | 300s | 30s |

The service clamps values independently of the UI. The request timeout applies
to SearXNG `/config` reachability and `/search` requests. Classification and
probe timeouts retain their existing contracts.

### Mature-run threshold

A productive page:

- adds at least one run-unique URL; and
- completes persistence, classification, retention, and optional probing.

A run becomes mature after either:

- five productive pages; or
- 50 run-unique URLs.

### Hard recovery

- Early run: short retry, then long retry.
- Mature run: short retry only.
- Hard retry allowance remains bounded across the entire run.
- A valid HTTP `Retry-After` value replaces the configured delay for that retry
  slot and remains bounded to 300 seconds.
- An empty clean page is ordinary exhaustion and does not retry.
- Partial results remain usable when retry allowance is exhausted.

## Cancellation Contract

`run_dork_search()` accepts an optional `threading.Event` as a keyword argument.
The event is execution control and is not stored in `RunOptions`.

Cancellation checks occur:

- before reachability;
- after each blocking SearXNG request;
- before and after page persistence;
- between classification rows;
- before probe submission and while collecting probe futures;
- before pacing or cooldown waits;
- before finalization and main-table synchronization.

Waits use `Event.wait(timeout)` when an event is present. An active network call
may continue until it returns or reaches the configured request timeout.

### Data handling

| Cancellation point | Required result |
| --- | --- |
| Before run row | Return cancelled with no run row. |
| Before page insert | Ignore the fetched page. |
| After insert, before classification commit | Remove current-page unclassified rows. |
| After classification commit | Preserve retained rows. |
| During probe | Persist completed outcomes; leave remaining retained rows unprobed. |
| During pacing/cooldown | Stop immediately and preserve completed pages. |

Existing run tables accept free-form status text, so `cancelled` is additive and
requires no schema migration. A cancelled run has `finished_at`, final counts,
`error_message=NULL`, and a `RunResult` with `error=None`.

Desktop completion performs the existing one-time primary HTTP-table sync for
retained rows. Cancellation is not reported as an error and does not show an
error popup. A cancelled unified provider queue does not advance.

## Desktop Controls

Start Scan owns the tuning controls. Saved preferences and scan templates carry
all three values. Older preferences/templates use defaults; out-of-range values
are clamped.

Accessories and WebUI continue using service defaults in C11. Their request
forms and public API shapes do not change.

## Live Output Semantics

The dashboard already parses ANSI and maps it through theme colors. Service,
database, and WebUI layers remain plain text.

| Level | ANSI/theme color | Use |
| --- | --- | --- |
| normal | none/default | counts, storage details, ordinary waits |
| info | bright blue | provider starts, phase headings, page requests |
| success | bright green | completed checkpoints and final success |
| warning | bright yellow | engine warnings, retries, partial results, cancellation |
| error | bright red | terminal failures |

`_log_status_event()` remains backward compatible by adding an optional semantic
level. SearXNG and Reddit progress adapters classify their stable message
prefixes at the dashboard boundary. Multiline rollups are colored line by line.
Shodan output remains unchanged.

## Live Validation Contract

The reusable script:

- lives under `scripts/`;
- requires `--confirm-live`;
- never runs in pytest or CI;
- uses a temporary DB unless `--keep-db` is supplied;
- never opens or syncs the primary DB;
- defaults probing off and requires `--probe` to enable it;
- accepts explicit instance URL/query overrides;
- can read canonical SearXNG GUI preferences read-only when overrides are absent;
- handles `Ctrl+C` by setting the same cancellation event;
- returns nonzero on failed invariants and prints exact cleanup information.

It validates ordering, DB integrity, telemetry, partial retention, and
cancellation without assuming every upstream engine is available.

## Compatibility

- Keep 1,000-result and 40-page ceilings.
- Keep primary DB persistence and one-time final protocol-table sync.
- Keep provider serialization and GUI-to-CLI boundaries.
- No schema, authentication, dependency, sidecar, or WebUI API changes.
- No live network access from automated tests.

