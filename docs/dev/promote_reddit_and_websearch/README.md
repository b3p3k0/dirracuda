# Finish desktop provider promotion

Status: Implemented and automated validation passed on 2026-09-27; HI visual check pending.

Remove Reddit and Self-hosted Search from Accessories. Start New Scan is the
only desktop launch surface for these providers. Preserve provider options,
queueing, persistence, primary-table sync, cancellation, and results reporting.

The previous integration cards deliberately retained the accessory routes
during promotion. Both tabs are still registered, with separate launch code and
operator docs that point to them. This task finishes that transition.

## Boundaries

- Keep Web UI scan routes and behavior unchanged; review them in a later phase.
- Keep Database → [Legacy] Sidecar Data browsing, promotion, and migration.
- Keep shared services, storage contracts, settings keys, and package paths.
- Remove obsolete desktop launch code and tests after tracing callers.
- Update README, Technical Reference, feature docs, and agent guidance together.
- Use `./dirracuda` for desktop checks and `./venv/bin/python -m pytest` for tests.
- No live-network tests. No schema, auth, dependencies, or CI changes.
- HI permits commits for this task; do not push.

## Follow-ups and decisions

- **Web UI phase:** audit `/scans/searxng`, `/scans/reddit`, and their API routes
  against the intended mainstream provider workflow. They remain supported;
  desktop's single-entry rule does not remove them. No Web UI bug has been
  established by this task.
- **Test size:** `gui/tests/test_experimental_features_dialog.py` starts at
  2,347 lines. HI considers a broader split low priority. Prune obsolete tests
  here; defer splitting unrelated Censys/Web UI/accessory coverage. The risk is
  maintenance and hidden coupling, not runtime danger from the line count.
- **Search UI:** Start New Scan has no standalone Test or Open Results DB
  buttons. Document automatic run-time checks and Server List for current
  results; retain browser implementations for historical sidecar data.

## Validation

Baseline: 205 passed:

```bash
./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py gui/tests/test_unified_scan_dialog.py gui/tests/test_dashboard_scan_dialog_wiring.py gui/tests/test_dashboard_scan.py gui/tests/test_dashboard_provider_queue.py -q
```

An initial command used the nonexistent `gui/tests/test_provider_queue.py` and
collected no tests (exit 4). The corrected command above passed.

See [validation and file sizes](VALIDATION.md) for exact commands and results,
and [lessons learned](LESSONS_LEARNED.md) for carry-forward guardrails.

## Delivered

- Accessories now contains Web UI, Dorkbook, Keymaster, Sherlock, and Analyst.
- Deleted both retired tab modules, the standalone Reddit Grab dialog/worker,
  obsolete primary-results browser launchers, and their callbacks/busy state.
- Removed tests that existed only for those retired paths. Retained provider
  handlers, primary-DB sync, legacy sidecar browsers, and their coverage.
- Updated README, Technical Reference, Self-hosted Search reference, and AGENTS.
  Local `CLAUDE.md` was also updated; it is ignored by Git and is not committed.
- Preserved old config keys/package paths; their names are compatibility
  details, not an instruction to launch through Accessories.

## HI check

1. Launch `./dirracuda`, open Accessories, and confirm neither Reddit nor
   Self-hosted Search appears; the five remaining tabs should open normally.
2. Open Start New Scan and confirm both providers and their saved options are
   available. For an end-to-end check, run your usual small approved search
   with each provider and confirm targets appear in Server List.
3. If historical data exists, open Database → [Legacy] Sidecar Data and confirm
   the old results remain browsable. No migration is needed for this change.

No live-network run was performed by the agent.

## Sources

- [Earlier integration roadmap](../inetgrate_exp_feat/ROADMAP.md)
- [Earlier integration lessons](../inetgrate_exp_feat/LESSONS_LEARNED.md)
- [Entrypoint lessons](../entrypoint_canonicalization/LESSONS_LEARNED.md)
- [Operating guide](https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AGENT_CHARTER.md)
- [Writing guide](https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/WRITING_SOP.md)
