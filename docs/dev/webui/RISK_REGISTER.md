# Web UI Risk Register

| ID | Risk | Impact | Likelihood | Mitigation | Owner |
|---|---|---:|---:|---|---|
| W1 | Web service exposes scan controls on a network by mistake | High | Medium | Bind localhost by default; TLS on by default; require explicit remote flag and allowlist; TLS-off remote mode needs an explicit insecure override | DA |
| W2 | Auth implementation is too clever or underbuilt | High | Medium | Use server-side opaque sessions; PBKDF2 stdlib hashing; focused auth tests; HI review before merge | DA+RA |
| W3 | CSRF allows scan launch from another site | High | Medium | SameSite strict cookie plus CSRF token and Origin/Referer checks on mutating routes | DA |
| W4 | User scan input reaches shell | High | Low | Known CLI executable paths; argument lists only; test that `shell=True` is never used | DA |
| W5 | Direct workflow calls drift from desktop behavior | Medium | Medium | v1 uses CLI subprocess boundary; direct adapter deferred | RA |
| W6 | SQLite lock contention blocks desktop app | Medium | Medium | One active scan writer; one connection per operation; bounded timeouts; WAL where compatible | DA |
| W7 | Legacy DB shape breaks results page | Medium | High | Runtime schema inspection; minimal legacy DB tests; fail soft per optional column | DA |
| W8 | Experimental dialog gets redesigned by accident | Medium | Medium | C7 explicitly uses registry tab insertion only; focused tab-order tests | DA |
| W9 | File/export endpoint becomes path traversal | High | Low | Controlled export directory; generated filenames; no arbitrary path download | DA |
| W10 | Remote mode docs make unsafe setup sound normal | Medium | Medium | Warnings in UI and docs; remote setup framed as VPN/trusted-network only | RA |
| W11 | New dependency supply-chain risk | Medium | Low | Limit v1 deps to approved FastAPI/Uvicorn/Jinja2; no extra auth deps without HI approval | RA |
| W12 | UI grows into a full replacement app | Medium | Medium | v1 non-goals enforced in task cards; no file browsing/downloads/imports | HI+RA |
| W13 | Tests silently add web-only dependencies | Low | Medium | C1 stops before adding `httpx` or form parsers unless RA approves dependency expansion | RA |
| W14 | Mobile layout is technically responsive but useless on phones | Medium | High | Treat phone-width layouts as v1 acceptance; results/tasks reflow into cards; manual mobile viewport gate before closeout | DA+HI |
| W15 | Service control only works in the same desktop session | Medium | Medium | Use health checks plus pidfile/systemd discovery; validate pid command line before stopping; document ambiguous state | DA |
