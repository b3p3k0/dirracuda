# Dorkbook Workspace

Date: 2026-09-27
Status: Unified library and Start Scan launch consolidation implemented; HI acceptance pending.

Dorkbook is the shared dork library for Shodan and Self-hosted Search
(SearXNG/DeGoog). Desktop and Web UI show provider groups, topic/text filters,
and a full query preview. **Apply to Search** saves one of four independent
defaults; selecting a row only previews it. The pack contains 52 built-ins,
including the three original broad Shodan dorks.

The canonical sidecar remains
`~/.dirracuda/data/experimental/dorkbook.db`. Provider schema upgrades preserve
legacy dorks and take a SQLite-consistent backup before rebuilding the table.
No primary results schema, verifier, crawling, or download behavior changed.

## Current references

- [Start Scan follow-up](START_SCAN_DORKBOOK.md): aligned buttons, one dialog, independent provider queries.

- [Implementation plan](IMPLEMENTATION_PLAN.md): approved behavior and U1–U6 cards.
- [Unified validation](UNIFIED_VALIDATION.md): commands, live limits, and HI checklist.
- [Migration validation/recovery](U1_VALIDATION.md): backup and rollback instructions.
- [Catalog research](CATALOG_RESEARCH.md): final dorks, source links, and adaptation notes.
- [Candidate menu](CANDIDATE_DORKS.md): HI selections and deferred 33C exploration.
- [Current mockup](ASCII_SKETCHES.md): grouped view; historical v1 sketches follow.
- [Lessons learned](LESSONS_LEARNED.md): guardrails for future changes.
- [Open questions](OPEN_QUESTIONS.md): remaining acceptance and follow-up work.

The v1 spec, roadmap, task cards, prompts, and original validation report are
retained as clearly marked historical records. The implementation plan and
unified validation describe the current application.
