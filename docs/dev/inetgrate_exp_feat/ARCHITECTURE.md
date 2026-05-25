# Integrate Experimental Features Into Main - ARCHITECTURE

Status: Draft canonical contract (PA/RA maintained)
Last updated: 2026-05-25

## Current Runtime Shape (Observed)

```text
Dashboard
  -> Start Scan (UnifiedScanDialog)
     -> SMB/FTP/HTTP protocol queue only
  -> Experimental button
     -> ExperimentalFeaturesDialog (Notebook tabs)
        -> SearXNG tab (sidecar DB flow)
        -> Reddit tab (sidecar DB flow)
        -> Web UI tab
        -> Dorkbook tab
        -> Keymaster tab
  -> Servers button (main DB viewer/drilldown)
  -> DB Tools button

WebUI
  -> /scans/shodan
  -> /scans/searxng
  -> /scans/reddit
  -> current provider pages include sidecar-oriented results/probe/promote flows
```

Data/storage:
- Main DB: runtime server/probe records.
- Sidecars:
  - `~/.dirracuda/data/experimental/se_dork.db`
  - `~/.dirracuda/data/experimental/reddit_od.db`
  - plus accessory sidecars (`dorkbook`, `keymaster`).
- Existing direct sidecar promotion path exists via `gui/utils/sidecar_promotion.py`.

## Target Architecture (This Wave)

```text
Dashboard
  -> Start Scan (core providers)
     -> Shodan provider adapter
     -> SearXNG provider adapter
     -> Reddit provider adapter
  -> Database button
     -> Main DB viewer path
     -> DB tools path
     -> Sidecar review/import path
  -> Accessories button
     -> Web UI
     -> Dorkbook
     -> Keymaster

WebUI
  -> promoted provider pages operate on main-DB contracts
  -> migration-status notice when legacy sidecar promotion is pending
  -> no direct sidecar DB browsing surfaces in standard operator pages
```

## Integration Strategy

1. Keep existing provider engines in place initially (`experimental/se_dork`, `experimental/redseek`) and wrap them with stable adapter contracts.
2. Promote UX/workflow first; defer risky package relocation until behavior is stable and test-backed.
3. Reuse existing sidecar promotion utilities during transition; do not force one-shot DB migration in early cards.
4. Keep Censys backend suspended and excluded from new provider registry.
5. Add desktop-owned one-time migration decision flow for legacy sidecar data.
6. Keep WebUI migration handling informational (status + instructions), not authoritative execution.

## Provider Adapter Contract (Wave Contract)

Each promoted provider exposes a thin adapter with predictable behavior:

- `validate_options(options) -> list[str]`
- `run(options, callbacks) -> RunSummary`
- `fetch_recent_results(filters) -> list[ResultRow]`
- `probe_rows(row_ids, options) -> ProbeSummary`
- `promote_rows(row_ids) -> PromoteSummary`

Callbacks should support at least:
- `on_status(message)`
- `on_result(result_row)`
- `on_error(message)`
- `on_complete(summary)`

## UI Contracts

1. Start-scan provider UI must support independent provider selection and provider-owned option panes.
2. Existing SMB/FTP/HTTP launch path remains functional during rollout.
3. Accessories surface must remain modeless/lightweight and registry-driven.
4. DB surface must include explicit legacy sidecar labeling and import affordances.
5. WebUI provider pages must not surface raw sidecar DB browser affordances after cleanup.
6. WebUI must expose migration pending/completed status and direct the operator to desktop flow when action is needed.

## Data Contracts

1. Sidecar reads/imports must perform runtime schema checks (table + column) before query/write.
2. Promotion/import operations should be idempotent where feasible (upsert semantics preferred).
3. No silent DB creation for operations that should fail on missing source (`mode=rw` patterns where applicable).
4. Migration state must persist explicitly (`not_started`, `deferred`, `in_progress`, `completed`, `failed`) with timestamps and summary counts.
5. Migration should be resumable/re-runnable without duplicate corruption (dedupe/upsert required).

## Security Contracts

1. Preserve subprocess safety (`shell=False`, argv-list only).
2. Do not log API secrets/credentials.
3. Keep API-driven providers aligned with provider terms/rate-limit restrictions.

## Planned Touch Zones

- `gui/dashboard/widget.py`
- `gui/components/experimental_features_dialog.py` and registry descendants
- `gui/components/unified_scan_dialog.py`
- `gui/components/dashboard_experimental.py` (or renamed accessories shell)
- `gui/components/app_config_dialog.py`
- `gui/utils/sidecar_promotion.py` and provider-specific import helpers
- `experimental/webui/*` scan/result/provider pages and migration notice surfaces
- `docs/README` + technical reference updates

## Censys Suspension Contract

- `experimental/censys_discovery/` remains untouched unless HI explicitly re-activates Censys.
- No new UI route/button/prompt in this wave may require Censys.
