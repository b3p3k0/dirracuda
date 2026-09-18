# Analyst Phase 2 Workspace

Date opened: 2026-08-17
Objective frozen: 2026-09-18

Phase 1 is shipped at its reviewed V1 boundary. In an after-action session on 2026-09-18
the operator chose and froze one Phase 2 objective.

## Chosen objective: Analyst redesign (read-first host reports)

Re-orient Analyst from a per-excerpt PII classifier into a read-first tool. It reads a
folder of a host's extracted files and produces a senior-tech "read": what the host is,
likely owner and contacts, a risk rating, and the top exposures, over a grounded facts
layer. Reports become exportable (`report.json` source of truth, Markdown first).

- Design brief: [`analyst_redesign/DESIGN_BRIEF.md`](analyst_redesign/DESIGN_BRIEF.md)
- Planning handoff: [`analyst_redesign/HANDOFF_PROMPT.md`](analyst_redesign/HANDOFF_PROMPT.md)

This redesign supersedes the analytical core of the frozen `CONTRACT.md`. The Phase 1
infrastructure (detached worker, durable state, sandbox, grounding) stays.

## Implemented (R0–R4d) — awaiting merge review (2026-09-18)

The read-first redesign is implemented across R0–R4d on `feature/ollama-analyst` (not pushed),
each card codex-implemented and Claude-validated, R4c operator-reviewed. See
[`analyst_redesign/R8_CLOSEOUT.md`](analyst_redesign/R8_CLOSEOUT.md) for the ledger, evidence,
and screenshots. A run now produces `report.json` (read + ranked facts); the report view,
export/copy, batch export, warn-not-block seal, and a server-reported model dropdown are wired.

## Original R0 contract freeze (drafted 2026-09-18)

The PA session drafted the R0 contract-freeze card. It is docs-only and awaiting the
senior-review + operator (HI) PASS. A PASS authorizes only R0; every code card stays HELD.

- Card: [`analyst_redesign/R0_CARD.md`](analyst_redesign/R0_CARD.md)
- Revised contract: [`analyst_redesign/CONTRACT_READ_FIRST.md`](analyst_redesign/CONTRACT_READ_FIRST.md)
- report.json + sidecar v4: [`analyst_redesign/REPORT_JSON_SCHEMA.md`](analyst_redesign/REPORT_JSON_SCHEMA.md)
- UI contract: [`analyst_redesign/UI_CONTRACT.md`](analyst_redesign/UI_CONTRACT.md)

Working model: Claude is PA/RA + orchestrator; codex (DA) implements one approved card at a
time via the `codex` CLI; the operator is HI. R0 is authored by Claude; codex delegation begins
at R1.

## Candidate directions deferred until after the redesign

Not authorization to implement. Sequenced after the read-first redesign.

1. Remote model server (authenticated TLS gateway for a private AI box). Already scoped as
   a split card inside the redesign brief, section 11.
2. OCR / image coverage under a separately reviewed parser, licensing, sandbox, and
   resource contract.
3. Measured performance or report-workflow improvements from real operator use.

The earlier generic orientation is preserved in [`HANDOFF_PROMPT.md`](HANDOFF_PROMPT.md).
