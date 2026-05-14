# Censys Field + Query Matrix (v1)

Date: 2026-05-14
Purpose: lock protocol query templates and required API fields for deterministic parsing

## Query Rules

1. Use nested service clauses for same-service semantics.
2. Keep protocol + port in same nested object.
3. Add optional freshness constraint outside nested clause.

Reference: https://docs.censys.com/docs/censys-query-language

## Protocol Query Baselines

| Protocol | Baseline CenQL | Optional freshness suffix | Notes |
|---|---|---|---|
| FTP | `host.services:(protocol=FTP and port=21)` | `and host.services.scan_time > "now-72h"` | Add banner/status constraints cautiously to reduce false negatives. |
| HTTP | `host.services:(protocol=HTTP and port=80)` | `and host.services.scan_time > "now-72h"` | HTTPS on 443 can still appear as HTTP protocol service object depending on parsed data. |
| SMB | `host.services:(protocol=SMB and port=445)` | `and host.services.scan_time > "now-72h"` | Keep SMB mapping conservative; verify service payloads before assumptions. |

## Candidate Retrieval Fields

The `search/query` request should include fields needed for `matched_services` extraction.

Required minimum field set:

1. `host.ip`
2. `host.services.port`
3. `host.services.protocol`
4. `host.services.transport_protocol`
5. `host.services.banner`
6. `host.services.scan_time`

Recommended protocol-specific additions:

1. FTP:
   1. `host.services.ftp.banner`
   2. `host.services.ftp.implicit_tls`
   3. `host.services.ftp.status_code`
2. HTTP:
   1. `host.services.endpoints.http.headers`
   2. `host.services.endpoints.http.body_hash_sha256`
3. SMB:
   1. `host.services.software.product`
   2. `host.services.software.version`

Reference for `matched_services` behavior and fields caveat:

- https://docs.censys.com/reference/v3-globaldata-search-query

## Normalization Contract

1. `ip_address`: `host.ip`
2. `port`: `matched_service.port`
3. `protocol`: `matched_service.protocol`
4. `transport_protocol`: `matched_service.transport_protocol`
5. `banner`: prefer protocol-specific banner, fallback to generic banner
6. `scan_time`: protocol-specific scan time if present, fallback to service scan time
7. `dedupe_key`: deterministic `protocol|ip|port|transport_protocol` per run

## Known Drift Watchlist

1. Seed docs referenced `host.services.tls.implicit_tls`; current data definitions list FTP implicit TLS as `host.services.ftp.implicit_tls`.
2. Credit costs differ by tier docs (Free/Starter vs Search/Enterprise); estimator must be profile-driven, not universal.
3. Legacy Search lifecycle and fields are changing in 2026; no legacy fallback paths in this module.

## Source Links

1. https://docs.censys.com/docs/censys-query-language
2. https://docs.censys.com/reference/v3-globaldata-search-query
3. https://docs.censys.com/docs/asm-host-data-definitions
4. https://docs.censys.com/docs/platform-credits-free-starter
5. https://docs.censys.com/docs/platform-credits-enterprise
6. https://docs.censys.com/changelog/upcoming-changes-to-legacy-search-data-and-apis
