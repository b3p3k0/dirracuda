# SearXNG Runtime Tuning - Roadmap

Last updated: 2026-06-06

| Card | Title | Status |
| --- | --- | --- |
| C11A | Retry Shortening and Cancellation | COMPLETE |
| C11B | Start Scan Tuning Controls | COMPLETE |
| C11C | Semantic Live Output Colors | PENDING |
| C11D | Reusable Live Validation | PENDING |

Cards are sequential. A later card does not begin until the prior card is
validated, reviewed by PA/RA, and committed at HI direction.

## C11A

Add bounded runtime policy values, mature-run retry shortening, interruptible
waits, and desktop cancellation that preserves completed results.

Non-goals: sliders, ANSI colorization, live harness, WebUI cancellation, and
Accessories cancellation.

## C11B

Expose request timeout and hard-retry delays in Start Scan. Persist and restore
them through GUI settings and scan templates.

## C11C

Reuse the dashboard ANSI parser and theme tags to give status messages semantic
blue, green, yellow, red, and default colors.

## C11D

Add an opt-in live SearXNG validation script using a temporary database. Validate
ordering, integrity, telemetry, partial retention, and cancellation.
