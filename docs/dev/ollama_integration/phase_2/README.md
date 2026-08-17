# Analyst Phase 2 Workspace

Date opened: 2026-08-17
Status: Planning only; scope is not frozen

Phase 1 is shipped at its reviewed V1 boundary. This directory is intentionally small
until the human operator selects one Phase 2 objective. Candidate directions are not
authorization to implement them:

1. complete and document the first operator-run acceptance, then address evidence-backed
   operational issues;
2. design an authenticated TLS gateway for private LAN/Tailscale access while raw Ollama
   remains loopback-only;
3. add OCR/image coverage under a separately reviewed parser, licensing, sandbox, and
   resource contract;
4. pursue measured performance or report-workflow improvements from real operator use.

Start with [`HANDOFF_PROMPT.md`](HANDOFF_PROMPT.md). Read the parent contract, errata,
risks, lessons, and current README before proposing a card. Create new Phase 2 task cards
here only after the operator freezes the objective and acceptance boundary.
