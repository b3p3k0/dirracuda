# Risk and Edge Case Analysis – Censys Search Integration

This document identifies potential risks and edge cases associated with integrating Censys Search into Dirracuda, and proposes mitigation strategies.  These considerations are critical to planning and engineering the feature to be robust under real‑world conditions.

## 1. API and Service Risks

### 1.1 API Deprecation

- **Risk:** Censys’s legacy search API will be retired in September 2026【760461715497418†L312-L332】.  Using the wrong API could result in broken queries.
- **Mitigation:** Target the **Platform v3 API** exclusively.  Confirm endpoints (`/v3/global/search/query`) and test queries during integration.  Monitor Censys release notes for future deprecations.

### 1.2 Rate Limits and Credit Exhaustion

- **Risk:** The free plan provides **100 credits per month**【300993859225417†L230-L246】.  Each search query and page load costs 5 credits (or 8 with regex)【300993859225417†L260-L301】.  Exhausting credits prevents further searches until the next month【300993859225417†L327-L333】.
- **Mitigation:** Implement configurable `max_pages` and `credit_budget` to cap consumption.  Track consumed credits during runtime.  Provide user warnings when approaching limits.

### 1.3 API Errors and Network Failures

- **Risk:** Invalid tokens, network timeouts, or service errors may disrupt searches.
- **Mitigation:** Catch exceptions from the SDK, retry transient network errors with back‑off, and surface meaningful error messages.  Expose a `DiscoveryError` type to differentiate search failures from internal exceptions.

### 1.4 Dependency Maintenance

- **Risk:** Adding the `censys-sdk` introduces dependency management.  Upstream changes or vulnerabilities could impact Dirracuda.
- **Mitigation:** Pin SDK versions in `requirements-cen.txt` to known stable releases.  Monitor release notes for breaking changes.  Keep the optional dependency separate so unaffected users are not impacted.

## 2. Data and Query Risks

### 2.1 Incorrect Query Translation

- **Risk:** Misconstructing CenQL queries could return no results or the wrong hosts (e.g., forgetting to nest clauses so they apply to the same service【317031200555299†L671-L687】).
- **Mitigation:** Encapsulate query construction in a tested builder function; unit test it with sample inputs.  Reference Censys’s examples for each protocol.  Provide developer documentation and examples.

### 2.2 Ambiguous Service Classification

- **Risk:** Hosts may have multiple services on the same port (e.g., 21/TCP used for custom protocols).  Censys labels services by protocol and service name, but classification errors are possible.
- **Mitigation:** Always include `protocol=<proto>` in the CenQL filter and check `matched_services.protocol` in code.  Discard results whose `protocol` does not match the requested service.  Optionally allow the user to inspect raw data for misclassified results.

### 2.3 Data Freshness and Staleness

- **Risk:** Censys’s scan frequency is <24 h for new services【760318777499469†L15-L69】, but results older than `query_hours` might still appear if not filtered correctly.  Stale data could lead to connecting to offline hosts.
- **Mitigation:** Always specify a time range (e.g., `scan_time:[now-72h TO *]`) in CenQL.  Expose this as a tunable parameter with a reasonable default (e.g., 72 h).  Record `scan_time` in the candidate for later analysis.

### 2.4 Missing or Extra Fields

- **Risk:** Some services may lack expected fields (e.g., no banner or TLS info).  Conversely, the `matched_services` list may contain additional services not requested.
- **Mitigation:** Handle missing fields gracefully by assigning defaults (e.g., empty string for banners).  Iterate through `matched_services` to find the matching service; ignore extraneous entries.  Use `banner_hash_sha256` to deduplicate when `banner` is missing【612288861349681†L360-L366】.

### 2.5 Protocol Differences

- **Risk:** SMB uses UDP/TCP combinations, multiple ports (445, 139), and may not be fully supported by Censys.  HTTP results may include HTTPS (TLS) or HTTP/2 variations.
- **Mitigation:** For SMB, test queries on common ports (`port=445` and `protocol=SMB`).  For HTTP, use `protocol=HTTP` and specify `port` if necessary (80, 443).  Document known limitations and plan to refine filters based on testing.

## 3. Candidate Processing Risks

### 3.1 Duplicate or Overlapping Results

- **Risk:** The same host may be discovered by both Shodan and Censys, leading to duplicate work.
- **Mitigation:** Implement de‑duplication across providers using IP and service banner hash.  Consider storing a search source field on each candidate to track origin.

### 3.2 Large Result Sets

- **Risk:** Queries with broad filters (e.g., no banner) may return thousands of hosts.  Fetching all pages could consume credits and overwhelm downstream processing.
- **Mitigation:** Encourage specific banner filters by default.  Limit the number of pages via configuration.  Provide a pre‑fetch result count if Censys exposes total hits; if the number exceeds a threshold, prompt the user to refine the query.

### 3.3 Malformed or Non‑ASCII Banners

- **Risk:** Banners may include null bytes or binary data; JSON decoding could fail.
- **Mitigation:** Use the `banner_hex` field when necessary to extract raw bytes; decode with error handling.  Store raw banners as binary or safely truncated strings.  Validate data before writing to logs or displays.

## 4. Security Considerations

### 4.1 Credential Handling

- **Risk:** Exposure of Censys API tokens could compromise the account or credit balance.
- **Mitigation:** Store tokens in the configuration file outside of version control.  Avoid printing tokens to logs.  Provide instructions for rotating tokens.

### 4.2 Sensitive Data Exposure

- **Risk:** Censys data may include hostnames or service banners containing sensitive information.
- **Mitigation:** Treat all Censys data as potentially sensitive.  Only display fields necessary for discovery.  Document privacy considerations in user guides.

## 5. Operational Considerations

### 5.1 Testing Environment

- **Risk:** Running the Censys module in development could accidentally consume credits.
- **Mitigation:** Provide a mock or sandbox mode in the CensysProvider for offline testing.  Encourage developers to use their own Censys credentials or a shared testing account.

### 5.2 Dependency Updates

- **Risk:** Upgrading `censys-sdk` may introduce breaking changes or deprecations.
- **Mitigation:** Adopt semantic version pinning.  Periodically check for updates and evaluate them in isolation.  Document the current tested version in the design.

### 5.3 Documentation Drift

- **Risk:** Users may be unaware of credit limits, new configuration keys, or required dependencies.
- **Mitigation:** Update `TECHNICAL_REFERENCE.md` with integration instructions.  Provide examples of configuration and common queries.  Include a section on credit usage and how to purchase more credits if needed.

## Summary

Integrating Censys into Dirracuda introduces new capabilities but also new risks around API use, query correctness, data handling and operational constraints.  By incorporating defensive coding practices, clear configuration, and careful credit management, the impact of these risks can be minimized.  Future extensions (e.g., IPv6 support and SMB/HTTP queries) should revisit this analysis to account for additional edge cases.
