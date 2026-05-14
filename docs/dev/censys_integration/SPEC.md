# Censys Integration v1 Spec

Date: 2026-05-14
Status: Locked for carded implementation
Scope: Experimental sidecar module only

## Problem

Dirracuda currently centers discovery on Shodan. We need an experimental Censys-backed workflow for defensive research that:

1. Uses current Censys Platform APIs (not Legacy Search API)
2. Preserves existing runtime behavior outside requested scope
3. Controls credit consumption explicitly
4. Keeps promotion into main DB explicit and operator-driven

## Goals

1. Add `Censys Discovery` tab in Experimental Features.
2. Support protocol discovery for FTP, HTTP, SMB in one module.
3. Persist run/result data in dedicated sidecar DB.
4. Offer deterministic query construction with CenQL nested fields.
5. Provide credit estimate + live balance/usage visibility.
6. Provide manual single/bulk promotion into main DB via existing promotion contract.

## Non-Goals (v1)

1. No replacement of core SMB/FTP/HTTP scan dialogs/workflows.
2. No automatic provider failover between Shodan and Censys.
3. No Legacy Search API compatibility layer.
4. No automated or background promotion into main DB.
5. No public-provider plugin framework.

## Locked Decisions

1. API target: Censys Platform v3 only.
2. Auth: PAT via Bearer token.
3. Transport layer: direct REST client (`urllib` stdlib style, matching existing codebase preferences).
4. Storage: one sidecar DB (`censys_discovery.db`) under `~/.dirracuda/data/experimental/`.
5. Protocol rollout order: FTP -> HTTP -> SMB.
6. UI label: `Censys Discovery`.
7. Promotion mode: manual single + bulk only.
8. Credit model defaults: moderate/free-safe profile unless HI overrides.

## Architecture Contract

```text
Experimental Tab (gui/components/experimental_features/censys_discovery_tab.py)
  -> Service Orchestrator (experimental/censys_discovery/service.py)
  -> REST Client (experimental/censys_discovery/client.py)
  -> CenQL Builder (experimental/censys_discovery/query_builder.py)
  -> Sidecar Store (experimental/censys_discovery/store.py)
  -> Results Browser (future C8 window)
  -> Existing sidecar promotion helper (gui/utils/sidecar_promotion.py)
```

Module package contract:

1. `experimental/censys_discovery/models.py`
2. `experimental/censys_discovery/client.py`
3. `experimental/censys_discovery/query_builder.py`
4. `experimental/censys_discovery/store.py`
5. `experimental/censys_discovery/service.py`

## Config Contract (new keys)

Namespace: `censys`

1. `censys.personal_access_token`: string, required for API calls.
2. `censys.organization_id`: string UUID, optional; used when org-scoped requests are needed.
3. `censys.credit_profile`: enum (`free_starter`, `search_enterprise`).
4. `censys.defaults.max_pages`: int, safe bounded range.
5. `censys.defaults.query_hours`: int, safe bounded range.
6. `censys.defaults.page_size`: int, default <=100.
7. `censys.defaults.ipv6_enabled`: bool, default false in v1.

Validation rules:

1. Never print raw PAT in errors or logs.
2. Invalid UUID, malformed URL, or empty PAT must fail with actionable message.
3. Coercion must be explicit and bounded.

## Query Contract

Query construction must use nested clauses for protocol-level accuracy.

Examples:

1. FTP baseline: `host.services:(protocol=FTP and port=21)`
2. HTTP baseline: `host.services:(protocol=HTTP and port=80)`
3. SMB baseline: `host.services:(protocol=SMB and port=445)`

Optional freshness filter pattern:

1. `host.services.scan_time > "now-<Nh>"`

CenQL constraints to preserve:

1. Do not rely on non-nested `host.services.port=... and host.services.protocol=...` when same-service matching is required.
2. Keep field names current with Platform/Host data definitions.

## API Contract

Required primary endpoints:

1. `POST /v3/global/search/query`
2. `POST /v3/global/search/aggregate` (for estimate/preview support)
3. `GET /v3/accounts/users/credits`
4. `GET /v3/accounts/users/credits/usage`
5. `GET /v3/accounts/organizations/{organization_id}/credits`
6. `GET /v3/accounts/organizations/{organization_id}/credits/usage`
7. `GET /v3/global/asset/host/{host_id}`
8. `GET /v3/global/asset/webproperty/{webproperty_id}`

Request behaviors:

1. Always send `Authorization: Bearer <PAT>`.
2. Prefer `organization_id` query parameter when org context is needed.
3. Respect server pagination token (`page_token`).
4. Handle `401`, `403`, `422`, `500` paths explicitly with stable reason codes.

## Sidecar Schema Contract

DB file: `~/.dirracuda/data/experimental/censys_discovery.db`

Tables:

1. `censys_runs`
   1. `run_id` PK
   2. `started_at`, `finished_at`
   3. `protocol` (`FTP|HTTP|SMB`)
   4. `query_text`
   5. `query_hours`, `max_pages`, `page_size`
   6. `fetched_count`, `deduped_count`
   7. `status`, `error_message`
2. `censys_results`
   1. `result_id` PK
   2. `run_id` FK
   3. `protocol`
   4. `ip_address`
   5. `port`
   6. `transport_protocol`
   7. `banner`
   8. `scan_time`
   9. `source_json`
   10. `dedupe_key` UNIQUE per run

Runtime schema guards:

1. Required column checks at open.
2. Required unique/index checks at open.
3. FK integrity check at open.
4. Raise explicit runtime errors on mismatch; do not silently continue with drift.

## UI Contract

Experimental tab shell (C2):

1. Provider status text
2. Protocol selector (FTP/HTTP/SMB)
3. Query/freshness controls
4. Credit estimate + balance panel
5. `Run` and `Open Results` actions

Results browser (C8):

1. Filterable table
2. Single-row and multi-row promotion actions
3. Error summaries for skipped/failed rows
4. No auto-promotion side effects

## Credit UX Contract

1. Estimate must be shown before run.
2. Live balance should be fetched when possible.
3. Live usage should be fetchable for selected date range.
4. When live values are unavailable, show explicit degraded status, not guessed balances.
5. Tier profile selection changes estimate model only, not source-of-truth balances.

## Security and Safety Contract

1. PAT is never logged raw.
2. PAT must not be surfaced in UI errors.
3. Sidecar store checks runtime schema state before reads/writes.
4. All network operations run off Tk main thread.
5. Promotion path must reuse shared validation helpers; no bespoke DB write bypass.

## Acceptance Criteria (C2-C9)

1. `Censys Discovery` tab appears and existing experimental tabs remain stable.
2. Config keys validate safely and predictably.
3. REST client handles auth/paging/errors with tests.
4. FTP/HTTP/SMB protocol runs persist deterministic sidecar rows.
5. Manual promotion works with/without Server List Browser open.
6. Credit estimate + live balance/usage are visible and honest about fallback conditions.
7. `README.md` and `docs/TECHNICAL_REFERENCE.md` remain in sync with final behavior.

## Source References

1. https://docs.censys.com/docs/platform-api-transition-guide
2. https://docs.censys.com/reference/v3-globaldata-search-query
3. https://docs.censys.com/reference/v3-globaldata-search-aggregate
4. https://docs.censys.com/docs/censys-query-language
5. https://docs.censys.com/docs/platform-quickstart-guide
6. https://docs.censys.com/changelog/upcoming-changes-to-legacy-search-data-and-apis
7. https://docs.censys.com/docs/asm-host-data-definitions
8. https://docs.censys.com/docs/platform-credits-free-starter
9. https://docs.censys.com/docs/platform-credits-enterprise
10. https://docs.censys.com/reference/v3-accountmanagement-user-credits
11. https://docs.censys.com/reference/v3-accountmanagement-user-credits-usage
12. https://docs.censys.com/reference/v3-accountmanagement-org-credits
13. https://docs.censys.com/reference/v3-accountmanagement-org-credits-usage
14. https://docs.censys.com/reference/v3-globaldata-asset-host
15. https://docs.censys.com/reference/v3-globaldata-asset-webproperty
