# Censys Integration Risk Register

Date: 2026-05-14
Scope: Experimental Censys sidecar integration

## Risk Summary

| ID | Risk | Severity | Trigger | Mitigation | Validation |
|---|---|---|---|---|---|
| R1 | Wrong API family (Legacy vs Platform v3) | High | Requests target `search.censys.io` or v1/v2 legacy auth patterns | Lock base URL to `https://api.platform.censys.io/v3/`; reject legacy config | Unit tests assert endpoint roots and auth style |
| R2 | Secret leakage (PAT exposed in logs/errors/UI) | High | Exceptions/status include raw token text | Redaction helper and strict no-token formatting | Tests check outputs never include PAT substrings |
| R3 | Credit overrun due to broad queries/paging | High | Unlimited page traversal or regex-heavy queries | Bounded `max_pages`, explicit profile estimates, live balance/usage reads | Service tests for hard cap behavior |
| R4 | Cross-service false positives from non-nested CenQL | High | Query uses `host.services.port` + `host.services.protocol` outside nested clause | Builder enforces nested `host.services:(...)` patterns | Query builder tests for required syntax |
| R5 | Schema drift in sidecar DB | High | Missing columns/indexes/FK silently ignored | Runtime schema checks at open with explicit errors | Store tests for guard failure paths |
| R6 | UI thread blocking from network calls | Medium | Tk dialog freezes during API calls | Worker-thread network calls + UI thread marshaling only | GUI tests and manual responsiveness check |
| R7 | Promotion path bypasses shared safety contract | Medium | Censys browser writes directly to main DB with custom logic | Reuse `gui/utils/sidecar_promotion.py` only | Promotion tests with invalid host rows |
| R8 | Config coercion drift causes unsafe defaults | Medium | Bad types accepted silently (e.g., huge page size) | Explicit bounded coercion and typed accessors | Config contract tests |
| R9 | Docs drift from actual behavior | Medium | README/technical reference outdated after card changes | C9 explicit docs sync gate and grep checks | Docs parity validation commands |
| R10 | Regressions to existing experimental modules | Medium | Registry or shared helpers changed broadly | Surgical edits and targeted regression tests | `test_experimental_features_dialog.py` + related suites |

## Security Escalation Triggers

Escalate for HI review before acceptance if any card touches:

1. Auth/token handling logic
2. Config write paths
3. DB schema migration or destructive DB operations
4. Promotion write behavior in main DB

## Assumption Risks

1. Credit profile estimator infers costs from separate Censys credit docs.
2. Some endpoint permissions vary by account tier and role.
3. Matched service field availability can vary with requested fields and entitlement.

Mitigation:

1. Make profile explicit in config/UI and mark it as estimate-only.
2. Show permission failures with actionable `role/tier` hints.
3. Keep requested fields list explicit and test for missing-field fallback behavior.

## Source Anchors

1. Platform API transition and PAT role requirements: https://docs.censys.com/docs/platform-api-transition-guide
2. Search endpoint and `matched_services`: https://docs.censys.com/reference/v3-globaldata-search-query
3. CenQL nested semantics: https://docs.censys.com/docs/censys-query-language
4. Free/Starter credit model: https://docs.censys.com/docs/platform-credits-free-starter
5. Search/Enterprise credit model: https://docs.censys.com/docs/platform-credits-enterprise
6. Credit balance/usage endpoints: https://docs.censys.com/reference/v3-accountmanagement-user-credits and https://docs.censys.com/reference/v3-accountmanagement-org-credits-usage
