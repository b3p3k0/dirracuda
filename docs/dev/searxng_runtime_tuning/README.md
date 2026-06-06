# SearXNG Runtime Tuning

Status: Active
Mode: PA/RA supervised, DA implemented, one card at a time
Started: 2026-06-06

This work follows the sequential page pipeline committed in `662d2a2`.

The goal is to make long SearXNG runs easier to control and understand without
changing the existing primary-database flow or the productive-page soft pacing.

## Locked Constraints

- Keep productive-page soft backoff at 10, 20, and 30 seconds.
- Use five productive pages or 50 unique URLs as the mature-run threshold.
- Mature runs get one short hard retry. Early runs may use short and long retries.
- Preserve and sync completed retained rows when a desktop run is cancelled.
- Keep ANSI styling at the dashboard presentation boundary.
- Live SearXNG tests are explicit, temporary-database checks and never run in pytest.
- No schema, authentication, dependency, sidecar, or public request-shape changes.
- No commit or push without HI direction.

## Planning Set

- `SPEC.md` - locked behavior and interfaces.
- `TASK_CARDS.md` - one-card-at-a-time execution boundaries.
- `ASCII_SKETCHES.md` - UI and runtime-flow sketches.
- `RISK_REGISTER.md` - implementation and compatibility risks.
- `VALIDATION_PLAN.md` - deterministic and opt-in live checks.
- `CLAUDE_PROMPTS.md` - draft DA prompts; PA/RA sends one only after HI review.
- `REVIEW_LOG.md` - baseline and review evidence.

Claude has not been prompted for C11 planning. The prompt file is preparation
only.

## Source References

- Python thread events: https://docs.python.org/3/library/threading.html#event-objects
- Tk themed controls: https://docs.python.org/3/library/tkinter.ttk.html
- SearXNG search API: https://docs.searxng.org/dev/search_api.html
