# Security Review Risk Register

Status: C1-C8 controls implemented; E0-E12 and final closeout remain open

| ID | Risk | Impact | Likelihood | Mitigation / gate | Owner |
|---|---|---:|---:|---|---|
| R-01 | Redirect helper still allows authority changes through normalization edge cases | High | Medium | Test default ports, case, IDNA, IPv6, userinfo, scheme-relative and malformed locations | C1 DA + RA |
| R-02 | Endpoint pinning breaks HTTPS virtual hosts | High | Medium | Require Host/SNI/certificate separation tests before migration | C2 DA + RA |
| R-03 | Standard library cannot express pinned IP plus verified hostname safely | High | Medium | C2 stop gate; no dependency or insecure workaround without HI approval | HI + RA |
| R-04 | Proxy settings continue routing target traffic elsewhere | High | Low | Explicit proxy-disabled opener/transport and environment-proxy tests | C1 DA |
| R-05 | SMB sanitizer causes local-name collisions | Medium | Medium | Deterministic suffix mapping and tests with colliding hostile names | C4 DA |
| R-06 | Containment check is performed after directory/file creation | High | Low | Acceptance requires check before `mkdir` and `open` | C4 RA |
| R-07 | ZIP metadata lies about expanded size | High | Medium | Enforce both declared and streamed-byte caps | C6 DA |
| R-08 | ZIP restrictions reject previously accepted nested exports | Medium | Medium | Root-level-only rule is explicit; provide actionable validation error | HI + docs |
| R-09 | Exception audit creates noisy or sensitive logs | Medium | High | Taxonomy, sanitized context rules, review batches <=40, no blanket logging | E-series RA |
| R-10 | Exception audit changes control flow in GUI teardown paths | Medium | Medium | Typed lifecycle catches default to intentional-silent unless evidence requires change | E-series RA |
| R-11 | TLS default migration produces conflicting old GUI values | Medium | High | One-time deterministic migration and tests; remove independent persistence | C3 DA |
| R-12 | Python 3.8 support continues after [2024-10-07 EOL](https://peps.python.org/pep-0569/) | High | Medium | Defer explicitly; review before next development-to-main promotion | HI |
| R-13 | Insecure TLS default permits MITM of audit traffic | Medium | Medium | Plain-language warnings, App Config control, strict mode tests, VM/VPN guidance | HI + C3/C9 |
| R-14 | FTP validation rejects unusual but valid server names | Low | Low | Reject only ASCII controls and DEL; retain other Unicode/bytes behavior | C7 DA |
| R-15 | Full wave becomes too large to review coherently | High | High | One plan and implementation session per card; execution tracker; commit reminders | RA |
| R-16 | Baseline exception line numbers drift during earlier cards | Low | High | Stable inventory IDs plus operation/context matching during E0 | E0 DA |
| R-17 | Security fixes bypass GUI-to-CLI boundary | High | Low | Architecture and review guardrail; no direct workflow calls from GUI | RA |
| R-18 | Docs claim controls before implementation is accepted | Medium | Medium | Update runtime claims only after the corresponding card is accepted; C9 performs the final reconciliation | RA |

R-01 through R-08, R-11, and R-14 are controlled implementation risks retained
as regression guardrails. R-09, R-10, and R-16 remain active until the E-series
exception audit is complete. R-12 and R-13 are accepted deferred risks. R-15,
R-17, and R-18 remain process controls through C9 closeout.

## Deferred Risk Review

R-12 must be reviewed before the next `development -> main` promotion or by
2026-08-15, whichever comes first. The review must inventory dependency support,
packaging impact, and supported operating systems before changing the minimum.
