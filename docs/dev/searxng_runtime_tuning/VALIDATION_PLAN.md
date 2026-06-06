# C11 Validation Plan

Last updated: 2026-06-06

## C11A

Deterministic tests must cover:

- default and clamped runtime values;
- early versus mature retry allowance;
- `Retry-After` replacing, not adding, a retry slot;
- cancellation before run creation;
- cancellation during fetch, pacing, cooldown, classification, and probing;
- cleanup of unclassified current-page rows;
- preservation of classified rows and completed probe outcomes;
- cancelled run status/counts;
- sync-after-cancel;
- no popup and no provider-queue advancement;
- duplicate/stale cancellation callbacks.

Run targeted service/store/dashboard/queue/running-task suites, compilation,
quick lane, then full pytest.

## C11B

Test defaults, bounds, snapping, settings persistence, template round-trip,
legacy coercion, disabled state, request propagation, and default/minimum
geometry under Xvfb.

## C11C

Test exact ANSI codes and reset boundaries for every semantic level, multiline
rollups, provider queue messages, SearXNG/Reddit progress, and unchanged Shodan
output.

## C11D

### Automated

- argument and safety-guard tests use mocked network calls;
- temporary DB cleanup and `--keep-db` behavior;
- same-page retry does not violate stage-order checks;
- `Ctrl+C` sets cancellation rather than leaving an incomplete process.

### Live

Run only after HI approval:

```bash
./venv/bin/python scripts/live_test_searxng.py \
  --confirm-live \
  --max-results 100
```

The script reports:

- pages and unique URLs;
- stage ordering;
- productive and hard-retry delay;
- retained/probed counts;
- run status;
- SQLite integrity and foreign-key checks;
- temporary database cleanup path.

No live result-count guarantee is used because upstream engine availability is
external and variable.

