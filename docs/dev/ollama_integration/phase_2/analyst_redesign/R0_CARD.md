# R0 — Read-First Redesign Contract Freeze

- Date: 2026-09-18
- Branch: `feature/ollama-analyst`
- Type: **docs / contract freeze.** No product code.
- Status: drafted by PA. Awaiting senior-review + operator (HI) PASS.

## Why R0 exists

The read-first redesign is a non-trivial feature. The senior-review gate requires a frozen
contract before any code. R0 is that freeze. A review PASS on R0 authorizes **only** R0. Every
code card (R1+) stays HELD until its own review.

## What R0 delivers

Four documents in this directory:

1. `R0_CARD.md` — this card.
2. `CONTRACT_READ_FIRST.md` — the revised analytical contract. Says what it supersedes in the
   frozen `CONTRACT.md` and what it keeps.
3. `REPORT_JSON_SCHEMA.md` — the versioned `report.json` schema and the sidecar schema v4
   read-table DDL.
4. `UI_CONTRACT.md` — the launch, Advanced, report, and batch-export layouts, made normative.

Pointer edits (no frozen file is changed):

- `phase_2/README.md` — links R0 as the active card.
- `RISK_REGISTER.md` — appends new read-first risk IDs. Frozen Phase 1 entries are untouched.

`CONTRACT.md` stays frozen and unedited. Supersession is recorded in `CONTRACT_READ_FIRST.md`,
the same way errata never edit `CONTRACT.md`.

## Working model

- **Claude = PA/RA + orchestrator.** Writes specs and cards, directs codex, validates returned
  diffs, runs tests, reports to HI.
- **Codex = DA.** Implements one approved card at a time via the `codex` CLI. This balances
  token use across platforms.
- **Operator = HI.** Priority, acceptance, risk ownership, live validation.

R0 is documentation, so Claude authors it. Delegation to codex begins at R1.

## Frozen decisions (do not relitigate)

Operator brief + handoff:

1. Two-layer report: the read (model judgment, labeled unverified) over grounded facts.
2. Read-first pipeline. Detectors stay, facts-layer only. They no longer gate the model.
3. `report.json` is the source of truth. Render Markdown (primary), plain text, HTML on demand.
   In-app display, Export, Copy, and batch export are required.
4. Integrity relaxes to warn-not-block. A moved report still opens with a "changed" badge.
5. Model pin relaxes. User picks a model from a server-reported dropdown. Record model + digest
   per run.
6. Remote model server is a separate later card. Local ships first.
7. UI: one folder input on launch, a separate Advanced dialog, the brief's layouts.

Decided in the R0 planning session:

8. The read layer persists in the existing `analyst.db` sidecar as schema v4 tables,
   D5-migration-ready, with a plan to promote into primary `dirracuda.db` if the feature proves
   out.
9. Re-run everything. The new report view requires `report.json`. Pre-redesign runs are legacy
   and re-run to get a read. No compatibility view.

## Card sequence (frozen here; each HELD until its own review)

Each R1+ card is coded by codex under Claude's orchestration.

- R1 — pure read + facts models, prompts (pinned SHAs), risk rubric, `report.json` builder
  (stdlib-only, like C1).
- R2 — sidecar schema v4 read tables + migration guardrails.
- R3 — worker read-first orchestration (facts gather -> read map-reduce -> finalize
  `report.json`).
- R4 — model dropdown, per-run model selection, charged refresh.
- R5 — report render layer (Markdown / text / HTML from `report.json`) + seal warn-not-block.
- R6 — GUI launch simplification + Advanced dialog.
- R7 — report view (two layers) + Export + Copy + batch export.
- R8 — closeout: docs, regression matrix, Xvfb QA.
- Later, separate gate — remote model server.

## R0 acceptance

1. `CONTRACT_READ_FIRST.md` names exactly which `CONTRACT.md` sections it supersedes vs keeps.
2. `report.json` schema is versioned (`report_schema_version`).
3. Sidecar v4 read-table DDL follows D5 (host-keyed as the primary tables key it, additive, no
   cross-DB join) and reuses the `db_schema.py` migration guardrails.
4. `CONTRACT.md` and all frozen benchmark evidence remain unedited.
5. No runtime dependency file, CI config, or schema code changed in R0.
