# Dorkbook Open Questions

Date: 2026-09-27

No unresolved product decision blocks the implemented unified library.
[HI acceptance](UNIFIED_VALIDATION.md) remains pending for desktop/browser
layout, restart persistence, and the real profile's sidecar upgrade.

Live API checks reached both owned aggregators. Useful directory-search yield
remains unverified because upstream engines report HTTP 429/CAPTCHAs; DeGoog
returns some Wikipedia results while Brave is limited. Retry after the owned
instances' upstream engines recover; see the exact command in validation.

Deferred: 33C Caddy/custom listing recognition, Calibre/Calibre-Web/OPDS
application support, and Reddit saved dorks. Keep these separate from catalog
editing; 33C requires verifier/traversal research described in
[CANDIDATE_DORKS.md](CANDIDATE_DORKS.md).
