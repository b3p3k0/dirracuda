# SearXNG Runtime Tuning - Decisions

Last updated: 2026-06-06

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

## Output Colors

| Meaning | Color |
| --- | --- |
| Routine details | Default foreground |
| Provider starts and headings | Blue |
| Completed checkpoints | Green |
| Warnings, retries, partial results, cancellation | Yellow |
| Terminal failures | Red |

The dashboard applies ANSI codes. Service and WebUI data remain plain text.

