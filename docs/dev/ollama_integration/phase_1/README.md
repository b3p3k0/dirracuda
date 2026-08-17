# Analyst Phase 1 Archive

Date archived: 2026-08-17
Status: Complete through C15; historical task cards are frozen

This directory contains the reviewed C1–C15 implementation cards for Analyst's
initial public V1 release boundary. They are retained as design and acceptance
history, not as an active backlog.

The active continuation documents remain one level up:

- [`../README.md`](../README.md) — current orientation and workspace map
- [`../CONTRACT.md`](../CONTRACT.md) — frozen V1 contract
- [`../CONTRACT_ERRATA.md`](../CONTRACT_ERRATA.md) — accepted corrections through E16
- [`../RISK_REGISTER.md`](../RISK_REGISTER.md) — standing risks and controls
- [`../LESSONS_LEARNED.md`](../LESSONS_LEARNED.md) — carry-forward engineering lessons
- [`../RESEARCH_NOTES.md`](../RESEARCH_NOTES.md) — sourced environment and format research

The benchmark protocols and outcomes intentionally remain at the workspace root.
Frozen benchmark code, source seals, and leak/provenance tests refer to their exact
repository paths. Moving them would alter historical evidence rather than merely tidy
documentation.

## Card Index

- C1–C7: worksheet, inventory, sandbox, and authenticated parser lanes
- C8: durable SQLite state, fencing, cancellation, and crash recovery
- C9/C9B: local Ollama protocol plus durable contact/resource scheduling
- C10: Phase 1 worker, safe reopening, and detector/model handoff
- C11: Phase 2 charged model orchestration and grounding
- C12: streaming reports, manifest publication, and finalization
- C13: desktop launch, detached worker control, hydration, and report browser
- C14: extraction-manifest and post-extract handoff
- C15: packaging, release matrix, and public synthetic end-to-end acceptance

Post-release corrections E16 and the live-status UI refinements are recorded in the
parent errata/history and their implementation commits; they do not reopen these cards.
