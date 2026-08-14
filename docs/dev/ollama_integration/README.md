# Ollama Integration Workspace

Date: 2026-08-11
Status: **C0A contract frozen** (committed `91bb2aa`). **C0B-1 accepted and committed
(`47e946b`).** The complete public C/D/F executor passed its offline and hostile-review
gates and was committed through B5 (`bde8f92`), with the current Ollama show-envelope
corrections committed as E3 (`8d772c4`) and E4 (`5c154b5`). The canonical public run at
that exact E4 source identity ended terminal **`INCONCLUSIVE`** after 530 charged calls.
Stage C carried both Qwen models on worksheet v2. D1 continued both at
`num_predict=1024`; D2 continued only `qwen3.6:27b` at `chunk_chars=8000` and
`overlap=256`; D3 then eliminated it because one negative-control document produced a
false positive. The 16,384-context allocation probe passed: context capacity was not the
failure. No final D selection exists, so Stage F never activated and private Stage E was
structurally ineligible and untouched. The terminal receipt and checkpoint verification
both pass. See [`PUBLIC_CDF_OUTCOME_C0B2.md`](PUBLIC_CDF_OUTCOME_C0B2.md).

Two earlier canonical preflights remain as receipted `FAILED_SAFETY` history with zero
scored calls; E3 and E4 corrected their bounded `/api/show` compatibility findings
prospectively. Work remains local on `feature/ollama-analyst`; no push or promotion is
authorized.

On 2026-08-09 the HI accepted the observed false-positive review cost for Analyst's
assistive, no-action role. E5 records that risk decision prospectively: D3/D4 and each
Stage-F seed may continue with at most one false-positive document, while final acceptance
remains capped at one across all 40 negatives. Every other gate is unchanged. C0B-2 stays
`INCONCLUSIVE`. C0B-3A's prospective contract passed three independent reviews. C0B-3B
implements the versioned policy split and exact legacy/current verification. Its offline
suite, full fake-transport terminal flows, leak audit, legacy-checkpoint compatibility
checks and three independent hostile reviews pass. The resulting C0B-3C run is complete
and terminal
`INCONCLUSIVE/no_seed1_qualifier`: 91 of 92 Stage-F seed-1 chunks were valid, and one
fully grounded answer repeated a category/quote row. The identical retry returned
identical bytes. See [`PUBLIC_CDF_OUTCOME_C0B3.md`](PUBLIC_CDF_OUTCOME_C0B3.md).

The HI accepted E6's narrow prospective correction. C0B-4 will preserve C0B-3, add an
explicit unique-evidence prompt rule, normalize at most one fully grounded redundant row
under an independent per-lane bound, confirm the fixed finalist with fresh F72 requests
at seeds 17 and 20260804, then restore complete-corpus acceptance with a new corrected
C44 lane. This is repair/stability confirmation, not a new untouched holdout. Private
Stage E and C1 remain held.

C0B-4A is accepted. C0B-4B is implemented and independently accepted offline: 141
C0B-4 tests and a separate 37-test high-risk holdout pass. The real-plan fake flow made
exactly 228 scored requests and 12 controls across three resumable stage invocations,
then independently replayed the source checkpoint and immutable snapshot. No live
model call occurred in that phase. The first C0B-4C child then failed closed with zero
invocations and attempts because creation hashed one filesystem mode while revalidation
hashed two. E7 preserves that verified terminal and corrects exact-mode revalidation
prospectively. The corrected 150-test C0B-4 suite and independent hostile re-review pass.
The replacement completed F72 seed 17 and ended verified
`INCONCLUSIVE/seed17_no_qualifier`: two negative near-miss documents each produced one
grounded financial suggestion, while every other measured gate passed. Later lanes were
never activated. The HI accepted E8 prospectively: C0B-5 caps both affected negative
documents and retained false-positive rows at 2 per F lane and 4 in the final aggregate,
using two never-contacted seeds. Private Stage E and C1 remain held.

C0B-5B's isolated offline implementation passed its focused, Analyst-wide, provenance,
leak, compile/diff, file-size and independent hostile-review gates. It adds independent
read-only replay of the frozen C0B-3/C0B-4 parents and the C0B-5 child, owner-only atomic
checkpoint/backup handling, explicit boundary resumes and quality-neutral shared-GPU
resource pauses. Its sole live child then ended `BLOCKED_PROVENANCE` after completing all
92 first-lane scored chunks plus context and cancellation controls, but before the health
control or lane aggregate. This is not a quality result. A deterministic SQLite
`query_only` state leak prevented receipt publication; the source and two owner-only
snapshots still replay semantically without a receipt. Later lanes were untouched.

C0B-6 is now authorized as a narrow, prospective harness repair. It preserves the
candidate, prompts, quality thresholds and human-review budgets; uses fresh F72 seeds
20260811 and 20260818; isolates semantic replay from writable SQLite state; adds closed
content-free failure origins; and requires a real 97-call cancellation-to-health resume
regression before one replacement child may contact Ollama. C0B-2 through C0B-5 remain
immutable. C0B-6B implements those controls and has reproduced the C0B-5 pre-health
failure as an exact method-signature mismatch hidden by its in-memory test double. The
focused, compatibility, provenance, leak and 1,420-test frozen-workspace gates pass. The
reviewed source is ready for its implementation commit before live child creation.

The inherited C0B-3 public implementation freezes the protected run nonce key only in the
backed-up 0600 checkpoint; derived
boundary work binds both the logical document hash and generated-view hash; and D3/D4
context probes are phase-specific planned controls triggered by the first normal HTTP
answer even when schema-invalid. Stage F extends that barrier to each seed-1 candidate,
activates later seeds atomically, and uses a durable cursor event to separate their
execution. Public creation now uses a durable internal state, atomic no-replace promotion
and exact crash recovery before `PREPARED`. These controls do not change benchmark
thresholds or budgets.

The authoritative spec is [`CONTRACT.md`](CONTRACT.md), plus accepted errata in
[`CONTRACT_ERRATA.md`](CONTRACT_ERRATA.md). The C0B-1 experiment is pre-registered in
[`BENCHMARK_PROTOCOL_C0B1.md`](BENCHMARK_PROTOCOL_C0B1.md); method and instrument
dispositions are in [`BENCHMARK.md`](BENCHMARK.md).

**C0B-1 delivered:** the 166-document synthetic gold set, the offline benchmark
instrument under `scripts/analyst_benchmark/`, Stage A (PyMuPDF pin measured, sandbox
smoke), and the Stage B screening pilot. The pilot's original injection result is
**INVALID / UNMEASURED**; its grounding rule also exceeded CONTRACT §7. No private
document was touched, and no model or worksheet was selected.
**C0B-2** (Stages C–F–E: elimination, factor
tuning, full-gold-set validation, private operational sample) has its own protocol
document, written after C0B-1 review — so revising Stage E cannot invalidate a frozen
artifact.

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

## Decision Status

- **D1 — primary model: UNRESOLVED.** The canonical public run produced no finalist.
  `gpt-oss:20b` stopped in Stage C; both Qwen candidates continued into D, but D3
  eliminated the remaining `qwen3.6:27b` candidate. No partial survivor is a product
  selection.
- **D2 — chunk size / context: UNRESOLVED.** D1 and D2 measured useful intermediate
  values, but the later D3 gate invalidated that path. `chunk_chars=8000`,
  `overlap=256`, `num_ctx=16384` and `num_predict=1024` are not a deployable bundle and
  must not be promoted outside a new preregistered run.
- **D8 — PyMuPDF pin: RESOLVED.** `PyMuPDF==1.28.0`, wheel
   `pymupdf-1.28.0-cp310-abi3-manylinux_2_28_x86_64.whl`, digest verified against PyPI.
   Measured embedded **MuPDF 1.29.0** — not the 1.28.0 its release note claims, which is
   why the contract requires asserting both. Selected on version/security grounds plus a
   sandboxed import-and-smoke test; extraction quality is C5, not C0B.
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
2. `CONTRACT_ERRATA.md` — accepted narrow corrections to the frozen contract (E1–E8).
3. `README.md` (this file) — status, decisions, orientation.
4. `BENCHMARK_PROTOCOL_C0B1.md` — **pre-registered** C0B-1 decision rule, gates, factors, budgets. Hash-pinned.
5. `BENCHMARK_PROTOCOL_C0B2.md` — reviewed C0B-2 public/private protocol; offline 2A is
   implemented and the public envelope is authorized. The B1R amendment requires the
   complete public C/D/F executor to pass offline review under one source pin before the
   first scored call.
6. `BENCHMARK_PUBLIC_CDF_SCHEMA.md` — normative strict artifact, identity, derivation and
   terminal schemas for the public C/D/F executor.
7. `BENCHMARK_PROTOCOL_C0B3.md` — prospective bounded-FP confirmation and card gates.
8. `PUBLIC_CDF_OUTCOME_C0B3.md` — immutable C0B-3 terminal result and postmortem facts.
9. `BENCHMARK_PROTOCOL_C0B4.md` — frozen grounded-duplicate confirmation protocol.
10. `PUBLIC_CDF_OUTCOME_C0B4.md` — immutable C0B-4 terminal result and measurements.
11. `BENCHMARK_PROTOCOL_C0B5.md` — prospective assistive review-budget confirmation.
12. `PUBLIC_CDF_OUTCOME_C0B5.md` — immutable C0B-5 harness-failure record.
13. `BENCHMARK_PROTOCOL_C0B6.md` — prospective repaired confirmation protocol.
14. `BENCHMARK.md` — instrument method, module dispositions, what is/is not committed.
15. `PUBLIC_CDF_OUTCOME_C0B2.md` — public-only terminal result and decision chain.
16. `LESSONS_LEARNED.md` — only what a card actually exercised.
17. `RESEARCH_NOTES.md` — verified external findings with sources + corpus profile.
18. `RISK_REGISTER.md` — risk controls.
19. `UI_MOCKUPS.md` — surface layouts (draft).

`BENCHMARK_RESULTS.md` remains absent. It is the private Stage-E aggregate, and Stage E
never became eligible.

## Next Step

C0B-6A is frozen and C0B-6B has passed its offline gate. Commit the reviewed source, then
create the single C0B-6 replacement child from a clean dedicated worktree. C1 and
private Stage E remain held.
