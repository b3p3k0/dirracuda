# Design Overview – Censys Search Integration for Dirracuda

## Purpose and Scope

This document proposes a self‑contained **Censys Search** module to complement Dirracuda’s existing Shodan‑based discovery.  The goal is to achieve parity with Shodan for **SMB**, **FTP** and **HTTP** discovery while using Censys’s richer dataset and respecting the constraints of a free account (100 credits/month, 5 credits per standard query page【300993859225417†L230-L301】).  The integration will be experimental; it will not alter the core unified workflow or existing UI outside of the experimental menu.

## Requirements

- **Service parity:** Support discovering FTP, SMB and HTTP hosts similar to existing Shodan modules.  FTP will be the initial implementation; architecture must allow adding SMB and HTTP.
- **Provider isolation:** Implement the module as a *sedecar* (self‑contained experimental module) following patterns used in the Reddit and SearxNG integrations.  Existing UI and workflow remain untouched.
- **Pluggable provider layer:** Introduce an abstraction so the discovery engine can support multiple search providers (Shodan, Censys).  Users can choose a provider via configuration or the experimental UI.
- **Free‑tier budget:** Target the Censys **Free** plan; queries and page fetches must respect credit limits (100 credits/month; 5 credits per query/page【300993859225417†L230-L301】).  Initially ignore IPv6 results.
- **New dependency:** Leverage the official `censys-sdk` package to interact with Censys.  Package this dependency in a `requirements-cen.txt` file in the project’s root so that users only install it when needed.

## High‑Level Architecture

### SearchProvider Interface

Define an abstract interface, `SearchProvider`, that encapsulates search operations.  Example method signature:

```
class SearchProvider:
    def search(self, protocol: str, port: int | None, banner: str | None,
               max_pages: int, query_hours: int | None) -> Iterable[Candidate]:
        """Return an iterable of candidate service endpoints."""
```

Implement two concrete providers:

- **ShodanProvider:** wraps the existing Shodan integration (reusing `shodan_query.py`).
- **CensysProvider:** handles CenQL query construction, API calls and pagination.

The unified workflow will remain unaware of the specific provider; the experimental UI will instantiate the appropriate provider based on user selection.

### Directory Structure

A proposed layout for the Censys module (mirroring existing patterns):

```
commands/
    ftp/
        censys_query.py    # search logic and CensysProvider implementation
        shodan_query.py    # existing code
        models.py          # candidate data models (extend as needed)
        ...
shared/
    search_provider.py    # abstract provider interface
requirements-cen.txt       # lists `censys-sdk` dependency
```

### Configuration

Extend the configuration schema to include Censys settings.  Example YAML structure:

```yaml
search:
  provider: censys        # values: shodan, censys
  shodan_api_key: xxx     # existing
  censys_api_key: xxx     # new personal access token
  censys_credit_budget: 50  # optional budget; 0 means unlimited within free tier
  max_pages: 5            # maximum pages to fetch per query
  query_hours: 72         # limit results to hosts scanned within last N hours
```

Only the `censys_api_key` must be provided to enable the Censys provider.  The `query_hours` parameter maps to CenQL’s time range filter (e.g., `host.services.scan_time:[now-72h TO *]`).

### Query Construction

The CensysProvider will build CenQL strings using nested service filters to ensure that all conditions apply to a single service【317031200555299†L671-L687】.  Example builder:

```
def build_cenql(protocol: str, port: int | None, banner: str | None, hours: int | None) -> str:
    clauses = [f"protocol={protocol}"]
    if port is not None:
        clauses.append(f"port={port}")
    if banner:
        clauses.append(f'banner:"{banner}"')
    if hours:
        clauses.append(f"host.services.scan_time:[now-{hours}h TO *]")
    return f"host.services:({ ' and '.join(clauses) })"
```

This generator will be reused across protocols (FTP, SMB, HTTP) with different default ports and banner patterns.  For example, an FTP search on port 21 with a banner substring becomes:

`host.services:(protocol=FTP and port=21 and banner:"welcome")`

### API Interaction

Use the Python SDK (`censys.search`) to execute global searches.  The provider will:

1. Construct the query using `build_cenql`.
2. Call `sdk.global_data.search` or `CensysHosts.search` with `fields=["host.ip", "host.services.port", "host.services.protocol", "host.services.banner", "host.services.scan_time", "host.services.banner_hash_sha256", "host.services.tls.implicit_tls"]`.
3. Iterate over pages until reaching `max_pages`, `censys_credit_budget` or `next_page_token` is `None`.  Each page costs 5 credits (assuming standard queries without regex)【300993859225417†L230-L301】.
4. For each result, iterate through `matched_services` to build `FtpCandidate` (and future `SmbCandidate`, `HttpCandidate`) objects.  Include fields like `scan_time`, `implicit_tls` and `banner_hash_sha256` in the raw metadata.
5. Handle exceptions (network errors, invalid tokens) by raising a domain‑specific `DiscoveryError`.  If credits are exhausted, abort gracefully and log a message.

### Candidate Model Extensions

The `commands/ftp/models.py` dataclasses will be extended or new ones added to accommodate Censys metadata.  Proposed additional attributes:

- `scan_time: str` – last time Censys scanned the host.
- `implicit_tls: bool` – whether the service uses implicit TLS【159627258083314†L8-L17】.
- `banner_hash: str` – SHA‑256 hash of the banner【612288861349681†L360-L366】.
- `software_product: str | None` – product name if present.

These fields will be stored in a `censys_data` dictionary to avoid polluting the core candidate model until we decide which to surface in the UI.

### Experimental UI Integration

Following the sedecar pattern, the Censys module will be accessible via an **Experimental** menu entry (e.g., “FTP Discovery (Censys)”).  This dialog will allow users to:

- Select the search provider (Shodan or Censys).
- Enter optional banner filters.
- Set max pages or hours for Censys queries.

The module will display results in the same style as current experimental features without altering the unified workflow.

## Additional Features and Future Work

- **SMB and HTTP support:** after verifying FTP, implement equivalent Censys queries for SMB (CIFS on port 445) and HTTP (port 80/443).  Use `protocol=SMB` or `protocol=HTTP` in CenQL.
- **IPv6:** design candidate models to accept IPv6 addresses and update network/port checks when enabling IPv6.
- **Credit monitoring:** add a small status display showing remaining credits and query costs.  Censys restricts free users who exhaust credits until the next month【300993859225417†L327-L333】.
- **Caching and deduplication:** implement a caching layer to avoid querying the same filter repeatedly and deduplicate identical hosts across providers using IP and banner hash.
- **Service vulnerability mapping:** once software/product fields are collected, link them to known CVEs to prioritise hosts during access verification.

## Documentation & Dependency Handling

- Add `requirements-cen.txt` containing `censys-sdk` and any transitive dependencies.  Document how to install optional providers in `docs/TECHNICAL_REFERENCE.md`.
- Update the experimental feature guide to explain the new Censys options, credit limitations and query syntax.

## Summary

This design keeps the Censys integration modular and experimental while providing a clear path toward feature parity with Shodan.  By abstracting search providers and carefully managing credit consumption, Dirracuda can leverage Censys’s broader port coverage and richer metadata without disrupting existing workflows.
