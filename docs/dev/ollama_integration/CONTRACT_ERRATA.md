# Analyst — Contract Errata

Errata against the frozen C0A contract ([`CONTRACT.md`](CONTRACT.md)). `CONTRACT.md`
itself is **not edited** — it stays frozen as reviewed and committed at `91bb2aa`.
Each entry here records a specific, narrow correction, who accepted it, and when.

---

## E1 — GPT-OSS cannot disable thinking

**Status:** ACCEPTED (HI, 2026-08-04)
**Affects:** §7 (model worksheet), §8 (Ollama request contract)
**Raised by:** C0B-1 planning, senior review revision 3

### The conflict

§7 states "Temperature 0. Thinking disabled." and §8 states "thinking disabled where
supported". These disagree, and the stricter reading is unachievable for one of the
two candidates named in §D1.

Ollama documents that GPT-OSS is an exception to the boolean `think` parameter:

> GPT-OSS requires `think` to be set to `"low"`, `"medium"`, or `"high"`. Passing
> true/false is ignored for that model.

The trace cannot be fully disabled for GPT-OSS. Source:
https://docs.ollama.com/capabilities/thinking

### The correction

§7 and §8 align to the §8 wording: **thinking is disabled where supported.**
Concretely, for the C0B candidate set:

| Model | `think` value |
|---|---|
| `gpt-oss:20b` | `"low"` — the shortest trace the model accepts |
| `qwen3.6:35b` | `false` |
| `qwen3.6:27b` | `false` |

Preflight confirms the installed Qwen models honour `false` before any scored request.

### Consequences accepted with this erratum

1. **The streamed `thinking` field is sensitive model output.** It is bounded in
   bytes, counted against cancellation and output-budget accounting, excluded from
   operational logs and from every committed artifact, and deleted alongside other
   ephemeral raw results under the same retention policy.
2. **Candidates do not share identical reasoning settings.** This is a stated
   limitation of any comparison involving GPT-OSS, not a controlled variable. It is
   reported wherever GPT-OSS results appear.
3. **The interaction between thinking tokens and `num_predict` is measured, not
   assumed.** C0B-1 Stage B records it.

### Reversal condition

If generated reasoning traces over private material are later judged unacceptable,
`gpt-oss:20b` is eliminated from the candidate set rather than accommodated. This
erratum does not authorize reasoning traces for any purpose beyond running the
approved benchmark and the resulting Analyst pipeline.

---

## E2 — B5 terminal proof is compositional

**Status:** ACCEPTED (HI, 2026-08-06)
**Affects:** `BENCHMARK_PUBLIC_CDF_SCHEMA.md` §12.4
**Raised by:** final B5 hostile review

### The conflict

The original B5 sentence required one fake-session flow to use the actual bounded
transport through the selected C→D→F path and "every terminal branch." That is not a
coherent requirement for terminals which occur before HTTP dispatch: filesystem refusal,
an uncharged budget refusal, and explicit abandon must not send a request; abandon must
not construct the client at all. Replaying the full expensive prefix for each quality reason would also
duplicate evidence already divided deliberately between deterministic scorer tests and
durable terminal/receipt tests.

### The correction

B5 terminal proof is compositional and closed:

1. One fake-session flow uses the actual `BoundedOllamaTransport` for the complete
   C→D1→D2→D3→conditional D4→F seeds→acceptance path, including crash, retry, pause,
   cancellation and resume.
2. Focused scorer and durable-runtime tests cover every enumerated C/D/F quality-terminal
   reason and exact artifact/decision ownership. Production-generated checkpoints prove
   receipt and no-transport re-entry for every structurally distinct terminal owner path:
   C, each D phase, F seed 1, F final-seed ranking and F acceptance.
3. Transport-originated safety and provenance terminals use the actual bounded adapter.
   Pre-dispatch budget and filesystem terminals prove that no HTTP request is made;
   abandon proves that the transport client is not constructed.
4. The complete proof suite forbids socket connection and private-root discovery/access.

The authoritative closed proof-node matrix is in
`BENCHMARK_PUBLIC_CDF_SCHEMA.md` §12.4 and is checked against the frozen reason/state
sets by `test_b5_terminal_proof_matrix_is_closed_and_names_existing_nodes`.

### Consequences accepted with this erratum

This changes proof composition, not a scoring threshold, terminal outcome, runtime
behavior or live-data rule. It was accepted before B5 approval and before any public
checkpoint or scored Ollama call. Any new terminal reason must update both the strict
schema and the closed proof matrix; an unlisted reason fails the offline gate.

---

## E3 — `/api/show` has a control-specific JSON node cap

**Status:** ACCEPTED FOR CORRECTION (standing HI authorization, 2026-08-08)
**Affects:** `BENCHMARK_PROTOCOL_C0B2.md` §8
**Raised by:** first canonical public preflight

### The conflict

The general 4,096-node decoded-JSON cap is sufficient for chat frames and ordinary
controls, but current Ollama may include a `tensors` collection in `/api/show` even when
the frozen request sends `verbose:false`. The first canonical public run received a
69,543-byte, depth-5 response with 4,109 decoded nodes and 459 tensor rows for
`gpt-oss:20b`. Version and tags passed; show then correctly froze `FAILED_SAFETY`. No
scored document request occurred. The failed run is retained with a verified receipt.

Ollama documents `verbose` as enabling large verbose fields, while its current API type
also exposes optional tensors and the observed false/omitted behavior has an open
upstream report:

- [Show model details](https://docs.ollama.com/api-reference/show-model-details)
- [Ollama API type](https://github.com/ollama/ollama/blob/main/api/types.go)
- [Ollama issue 10286](https://github.com/ollama/ollama/issues/10286)

### The correction

Only a `show` control may contain up to 8,192 decoded JSON nodes. The 2 MiB raw-body,
256 KiB canonical-JSON and depth-16 caps remain unchanged. The sanitizer still persists
only the frozen model identity, capabilities, safe detail fields and hashes of parameters,
template and model info; tensor names, shapes and values are discarded. Version, tags,
ps, chat frames and scored answer JSON retain the 4,096-node cap.

### Consequences accepted with this erratum

This is a control-envelope compatibility correction, not a scoring threshold or model-
quality change. The immutable failed run is not resumed or reclassified. After the exact
correction passes review and is committed, a new public run starts from `PREPARED` under
the new source identity. Any show response above 8,192 nodes remains `FAILED_SAFETY`.

---

## E4 — Size the show-control envelope from every frozen candidate

**Status:** ACCEPTED FOR CORRECTION (standing HI authorization, 2026-08-08)
**Affects:** E3 and `BENCHMARK_PROTOCOL_C0B2.md` §8
**Raised by:** replacement canonical public preflight

### The conflict

E3 measured only the first model before selecting 8,192 nodes. The replacement run then
passed version, tags and the `gpt-oss:20b` show control but froze `FAILED_SAFETY` on the
next candidate. No scored document request occurred, and the second failed run is also
retained with a verified receipt.

Measurement of the complete frozen candidate set produced:

| Model | Decoded nodes | Tensor rows | Canonical bytes | Depth |
|---|---:|---:|---:|---:|
| `gpt-oss:20b` | 4,109 | 459 | 69,543 | 5 |
| `qwen3.6:35b` | 10,546 | 1,194 | 105,965 | 5 |
| `qwen3.6:27b` | 11,318 | 1,307 | 110,924 | 5 |

### The correction

E4 supersedes only E3's 8,192-node value. The show-only limit is 16,384 decoded nodes,
above the measured complete-candidate maximum. All unchanged E3 constraints still apply:
2 MiB raw body, 256 KiB canonical JSON, depth 16, sanitized durable evidence and a 4,096-
node cap for chat, scored answers and every other control.

### Consequences accepted with this erratum

Compatibility limits derived from a candidate set must measure the entire frozen set
before selection. Tests pin all three observed counts, the exact 16,384/16,385 boundary
and the unchanged byte/depth/general-control caps. The second failed run is neither
resumed nor reclassified; a new committed source identity requires a third public run.

---

## E5 — Model findings are bounded, human-reviewed suggestions

**Status:** ACCEPTED FOR PROSPECTIVE CORRECTION (HI decision, 2026-08-09)
**Affects:** `CONTRACT.md` §§7 and 14; public benchmark false-positive gates
**Raised by:** canonical C0B-2 terminal review

The HI reconfirmed this decision after reviewing the measured result: one bounded false
positive across the public evidence is acceptable for a useful assistive partner at this
stage. Analyst supports—not replaces—the human analyst, so this observed review cost is
not a showstopper while the no-action, explicit-review and unchanged hard-safety controls
below hold. This acceptance does not describe the model as perfect or authorize threshold
drift.

### The conflict

Analyst is a digital intern: it recommends findings to a human analyst and cannot act on
them. The frozen public protocol nevertheless used inconsistent false-positive gates.
Stage C allowed one false-positive document among 12 negatives and final acceptance
allowed one among 40, while D3/D4 and each Stage-F seed required zero. The canonical run
hit exactly that mismatch: its remaining candidate cleared grounding, recall, boundary,
schema, context and safety checks, but one negative document produced a retained finding.

Exact-substring grounding proves that quoted evidence exists. It does not prove that the
model classified that evidence correctly. A human-review control therefore needs both a
numeric error budget and a UI/report contract; “human in the loop” is not a substitute
for either.

### The prospective correction

C0B-3 uses these exact document-level limits:

| Gate | Maximum false-positive documents |
|---|---:|
| Stage C, 12 negatives | 1 — unchanged |
| D3/D4, 12 negatives | 1 — replaces zero |
| Stage F, 16 negatives, independently at each seed | 1 — replaces zero |
| Final 166-document acceptance, 40 negatives | 1 — unchanged |

The final combined gate is deliberately tighter than the intermediate gates: a candidate
may continue after one bounded miss, but additional seed-1 misses can still prevent final
selection. Recall/F1, raw and retained grounding, schema, injection, boundary, length,
context, cancellation, provenance and safety gates do not change. No metric compensates
for failing another hard gate.

### Human-review contract

- Deterministic identifier counts remain a separate, authoritative evidence track.
- Every model-derived finding is labelled **suggested / unreviewed**, shows its verified
  source quote and provenance, and requires an explicit human accept/reject choice.
- No model finding triggers copy, delete, move, quarantine, notification, authentication,
  probing, tagging, upload or any other state change.
- Findings export includes only rows the human explicitly selects. The report never
  implies that unreviewed suggestions are adjudicated facts or that no finding means safe.
- Rejection/override counts become monitoring evidence; they never silently retune the
  prompt, model or threshold.

### Non-retroactivity

C0B-2 remains terminal `INCONCLUSIVE/no_d3_context_survivor`. E5 does not alter its
checkpoint, decisions, artifacts, receipt, protocol, schema or outcome document. The
bounded policy applies only to a fresh, preregistered C0B-3 run under a new source identity.
Private Stage E remains held until that run produces a valid public selection and the HI
separately authorizes or defers private execution.

This is an assistive-workflow risk decision, not a claim of population accuracy. It
follows NIST's context-specific treatment of human-AI roles, oversight and measured risk:
[AI RMF Core](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/) and
[trustworthiness characteristics](https://airc.nist.gov/airmf-resources/airmf/3-sec-characteristics/).

---

## E6 — Repeated grounded evidence is normalized, not retried blindly

**Status:** ACCEPTED FOR PROSPECTIVE CORRECTION (HI decision, 2026-08-11)
**Affects:** `CONTRACT.md` §§7–8; worksheet prompt, evidence normalization and retry
**Raised by:** canonical C0B-3 terminal review

### The conflict

The public output-truncation fixtures contain identifiers that legitimately occur more
than once. C0B-3's prompt required exact quoted evidence but did not tell the model to
emit each category/quote pair only once. One Stage-F answer therefore returned the same
grounded contact quote twice. The schema was strict-valid, every quote was grounded and
the source contained the repeated value twice, but the semantic validator rejected the
answer as duplicate evidence.

The frozen retry repeated the exact prompt, nonce, schema, options, source and seed. It
returned byte-identical content and could not repair a deterministic semantic error.

### The prospective correction

The production prompt says to emit at most one finding for each unique category and exact
quote, even when the value repeats in the source. After strict structural validation and
raw grounding, the aggregator removes later duplicates by `(category, Unicode-NFC quote)`
in stable first-seen order. Raw counts retain every original row; retained counts and
display evidence use the normalized set and deterministic source location.

The C0B-4 confirmation policy may recover at most one redundant row in at most one
chunk/document independently in each scored lane. Recovery requires strict-valid raw
structure, `duplicate_evidence` as the only semantic error, every raw row grounded, and a
fully valid normalized answer. Two redundant rows, two affected chunks/documents,
another semantic error or an ungrounded row fails the gate. The rule is global; it never
names the observed fixture.

All other schema, grounding, quality, injection, context, safety and provenance rules
remain unchanged. C0B-2 and C0B-3 retain their exact old prompt, validator, retry and
terminal meanings.

Production C1 does not blindly repeat an identical deterministic request. Duplicate-only
normalization is local and needs no second model call. Any future error-specific model
repair gets a distinct prompt/request identity, hard one-repair bound and explicit tests;
changing only the random seed is not an accepted repair design.

### Non-retroactivity

C0B-3 remains terminal `INCONCLUSIVE/no_seed1_qualifier`. Its response, attempts,
aggregate, artifact, receipt and checkpoint are not rescored or resumed. C0B-4 creates a
new source/policy identity, requests and checkpoint for F72 seeds 17 and 20260804 plus a
fresh C44 seed-1 acceptance lane. The new F responses are prospective stability evidence,
not a new untouched document holdout. Final acceptance combines corrected C44 and F72
evidence with the immutable parent D50/D4 result; Stages C and D are not rerun.

---

## E7 — C0B-4 revalidates the capability it created

**Status:** ACCEPTED FOR CORRECTION (standing HI authorization, 2026-08-11)
**Affects:** `BENCHMARK_PROTOCOL_C0B4.md` §§3, 7 and 9
**Raised by:** first C0B-4C invocation

### The conflict

C0B-4 creation intentionally probed only its frozen `DELETE` journal mode. Invocation
revalidation called the inherited C0B-2 helper, whose default probe covers both `DELETE`
and `WAL`. The capability digest includes the complete ordered mode list, so the two-mode
revalidation digest could never equal the stored one-mode creation digest.

The first child therefore ended verified `BLOCKED_FILESYSTEM` before invocation claim,
precharge, transport construction or model contact. It contains zero invocations and zero
attempts. Its terminal backup and receipt verify, and the frozen mount fingerprint did
not change.

### The correction

C0B-4 uses a local revalidator that probes exactly the journal mode frozen in its header,
then compares mount fingerprint, capability digest and selected mode. C0B-2 and C0B-3
retain their historical two-mode helper behavior. Offline tests cover real
create-to-revalidate parity, the exact one-mode call, every comparison mismatch, probe
failure and the zero-call backed-up filesystem terminal.

The corrected C0B-4 suite passes all 150 tests. Independent hostile review also confirms
that the associated leak gate scans the original committed bytes even when Git
replacement refs and a safe dirty overlay are present; Git object reads disable
replacements and rehash each blob against its tree object ID.

### Non-retroactivity and replacement

The failed child `c0b4-20260811-190217-ac970de2a2f6021965bcd948` remains immutable and is
not resumed or reclassified. After this correction passes review and is committed under
a new source/protocol identity, one fresh replacement child may start from `PREPARED`.
Because the failed child made no request, this replacement does not discard or select on
model evidence. All scoring, safety, budget, lane and acceptance rules remain unchanged.
