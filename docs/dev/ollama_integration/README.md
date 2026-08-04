# Ollama Integration Workspace

Date: 2026-08-04
Status: **C0A contract frozen** (architecture approved across three senior reviews
+ final contract review = PASS). The authoritative spec is
[`CONTRACT.md`](CONTRACT.md). C0B and every coding card are HELD until these C0A
docs are reviewed. No code written yet.

## Objective

Turn a bulk extract run — potentially thousands of documents pulled from open
directories — into a standardized exposure report without a human reading every
file. Priority findings: PII, financial/tax data, contact and demographic data.

The known industry name for this is sensitive data discovery and classification.
The pipeline shape is:

```
inventory -> extract text (sandboxed) -> detect (all files) -> select
          -> model worksheet (flagged) -> aggregate -> report
```

Analyst is an **optional** experimental feature and **Linux-only for V1**. The
core GUI must start and run without any Analyst dependency installed.

## Working Model

- Claude role: PA/RA. Research, spec, task cards, review of returned work.
- Codex role: DA. Implementation, one approved card at a time.
- HI role: priority, acceptance, risk ownership, live validation.

Direction to DA agents is `codex` CLI, model `sol high` unless a card says
otherwise.

## Locked Decisions

Full detail in [`CONTRACT.md`](CONTRACT.md); summary here.

1. **Raw values stored, not masked.** HI accepted the aggregation risk
   (2026-08-04): the data is already collected, so discovery speed does not change
   the exposure.
2. **V1 file types:** RTF, plain text, PDF, legacy `.doc`/`.xls`, OOXML — in that
   priority order by corpus volume. RTF alone is about 61% of the candidate-document
   corpus. No OCR/images in V1; the measured 31% of PDFs with no text layer are a
   named coverage gap.
3. **The model never acts.** No tools, filesystem, or network beyond the single
   Ollama call. Output is report data, never a decision that drives code.
4. **Execution: dedicated detached worker subprocess** (`python -m
   experimental.analyst.worker`), not in-process and not the core GUI→CLI scan
   boundary. Survives GUI closure; SQLite owns durable state. (Resolves the old
   D4.)
5. **Detectors own identifiers; the model owns classification, unstructured
   findings, and prose.** Neither does the other's job.
6. **Every discovered file reaches exactly one terminal state** at report
   finalize. Coverage is the first report section.
7. **Local inference only, stated honestly:** Analyst connects only to a
   loopback Ollama endpoint, rejects known `:cloud` and `-cloud` tags, and permits
   only the benchmarked tag+digest; server-level egress control is an operator
   prerequisite Analyst cannot prove. No "zero cloud egress" claim.
8. **PDF parser: PyMuPDF, AGPL accepted** as a deliberate project-level decision
   (Dirracuda ships a web UI → AGPL §13 attaches to the combined work). Pin an
   approved version bundling MuPDF ≥ 1.28.0.
9. **Parser sandbox is mandatory (bubblewrap).** Sandbox unavailable → preflight
   fails. Not merely resource limits — a containment boundary against RCE-class
   parser bugs.
10. **Optional dependency lane.** Analyst's heavy/AGPL deps live in
   `experimental/analyst/requirements-analyst.txt`; core GUI runs without them.

## Resolved Decisions

- **D1 — primary model:** decided by the C0B benchmark
   (`gpt-oss:20b` vs `qwen3.6:35b`, think=false); pinned by name + digest.
- **D2 — chunk size / context:** decided by C0B (server currently caps at 16K).
- **D3 — report unit: per-host.** Corpus skew: one host = 46,724 docs, most a few
   hundred; same code path serves both.
- **D5 — sidecar SQLite first**, migration-ready for later `dirracuda.db`
   adoption (no cross-DB joins, host keyed as the primary tables key it, additive
   columns). Dorkbook/Keymaster precedent.
- **D6 — legacy `.doc`/`.xls`:** antiword for `.doc` (installed); xlrd or
   sandboxed LibreOffice for `.xls` (benchmarked in C7); catdoc/xls2csv never
   used.
- **D7 — OCR:** deferred; every report prints its actual no-text-layer count and
  rate. The research sample measured 31% across the sampled corpus.

## Document Map

1. `CONTRACT.md` — **the frozen, authoritative spec.** Cards implement against it.
2. `README.md` (this file) — status, decisions, orientation.
3. `RESEARCH_NOTES.md` — verified external findings with sources + corpus profile.
4. `RISK_REGISTER.md` — risk controls.
5. `UI_MOCKUPS.md` — surface layouts (draft).

## Next Step

Review of these C0A documents. On acceptance: C0B (gold set + benchmark) — the
first card, which selects the model and chunk size from measured numbers before
any pipeline is sized.
