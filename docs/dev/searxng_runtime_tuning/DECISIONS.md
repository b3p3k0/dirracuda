# SearXNG Runtime Tuning - Decisions

Last updated: 2026-06-07

## Runtime Defaults

| Setting | Default | Minimum | Maximum |
| --- | ---: | ---: | ---: |
| Request timeout | 15 seconds | 5 | 60 |
| Short retry | 30 seconds | 5 | 60 |
| Long retry | 180 seconds | 60 | 300 |

Service code clamps every value. C11B will add themed sliders with snapped
increments of 1, 5, and 30 seconds respectively.

## Retry Policy

- A productive page adds at least one unique URL and finishes page processing.
- A run is mature after five productive pages or 50 unique URLs.
- Early runs may use short and long hard retries.
- Mature runs use the short retry only.
- A valid `Retry-After` header takes precedence and remains bounded to 300 seconds.
- Productive-page warning backoff remains fixed at 10, 20, and 30 seconds.

## Cancellation

- `run_dork_search()` accepts an optional `threading.Event`.
- Cancellation stops new work at the next safe boundary.
- Active network calls may run until their configured timeout.
- Completed retained rows remain durable and receive the existing final sync.
- The run is recorded as cancelled, not failed.
- A cancelled unified queue never advances to another provider.

## Output Colors (C11C)

| Semantic level | ECMA-48 SGR | Meaning |
| --- | --- | --- |
| Normal | — | Routine metrics, pacing, storage details |
| Blue (`\x1b[94m`) | 94 | Provider starts, reachability checks, page requests, headings |
| Green (`\x1b[92m`) | 92 | Instance reachable, page classification/probe checkpoints, successful completion |
| Yellow (`\x1b[93m`) | 93 | Warnings, retries, nonterminal errors, partial completion, cancellation |
| Red (`\x1b[91m`) | 91 | Terminal reachability, fetch, processing, database, sync, or provider failure |

### Implementation approach

Color is applied **at display time** inside `append_log_line`
(`gui/components/log_semantic_color.py::colorize_for_display`). `log_history`
always stores the original input, so C11C adds no ANSI escapes to Copy All.
Pre-existing Shodan subprocess ANSI remains unchanged.

`_log_status_event(message)` signature is unchanged; all one-argument callers
and test doubles continue to work.

### Classification scope

Only exact SearXNG, Reddit, and provider-queue message prefixes are colored.
Generic keywords are not used — Shodan `[status …]` lines return normal.

Rollup coloring is triggered only on multiline strings (`"\n" in line`);
a standalone `SUMMARY_TITLE` line emitted by Shodan with CLI colors disabled
passes through unchanged.

### Sync-line classification rule

`🔄 Primary DB Sync` lines: green requires explicit `failed == 0` (parsed from
the line). Unknown formats (no `"N failed"` token) return normal. Failed > 0
returns red; cancelled > 0 returns yellow.

### Provider queue finished message

`_finish_provider_queue` emits one of two messages so the classifier can
distinguish outcomes without generic keyword matching:

- Success: `"Provider queue finished: N/N providers completed."`
- Partial failure: `"Provider queue finished: N/N providers attempted (K failed)."` (K ≥ 1)

Service and WebUI data remain plain text.
