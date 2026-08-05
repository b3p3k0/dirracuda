# Analyst Benchmark — Public C/D/F Schema Catalog

Version: `c0b2-public-cdf-schema-v1`
Date: 2026-08-05
Status: **C0B-2B1R FROZEN after three independent PASS reviews.** No live use is
permitted until the B2–B5 implementation gate passes.

This is the strict machine-artifact supplement to
[`BENCHMARK_PROTOCOL_C0B2.md`](BENCHMARK_PROTOCOL_C0B2.md) §18. It closes the exact
schema, identity, derivation and terminal rules needed by C0B-2B2–B5. Where this catalog
is more specific than the parent protocol, this catalog governs. Unknown keys and
implicit coercion are forbidden everywhere.

## 1. Common notation and canonical form

- `str` is a nonempty Unicode string unless a field says otherwise.
- `int` excludes Boolean values; counts are nonnegative.
- `bool` accepts only JSON `true` or `false`.
- `sha256` is exactly 64 lowercase hexadecimal characters.
- `nullable[T]` is the only notation permitting JSON null.
- `category` is one of `pii`, `financial`, `contact`, `demographic` in that order.
- Arrays retain the stated order and contain no duplicates unless sampling explicitly
  uses replacement.
- Every object rejects extra keys. Every enum is closed.
- Canonical JSON is UTF-8 with sorted keys, separators `,` and `:`, no ASCII escaping,
  no NaN/infinity and no insignificant whitespace, matching `canonical_json()`.
- Every rate is `fraction = {numerator:int, denominator:int>0}`, reduced to lowest terms;
  zero is exactly `{numerator:0, denominator:1}`. Gates compare fractions, never floats.
- A stored artifact SHA-256 is computed over its complete canonical object and is stored
  outside that object, avoiding self-reference.

`candidate-selection` has exactly:

| Field | Type |
|---|---|
| `model` | str |
| `model_digest` | sha256 |
| `worksheet` | `v1 | v2` |
| `chunk_chars` | `2000 | 4000 | 8000` |
| `overlap` | literal `256` |
| `num_ctx` | `4096 | 8192 | 16384` |
| `num_predict` | `1024 | 2048 | 3072 | 4096` |

The D base candidate ID is SHA-256 of canonical JSON exactly:

`{"domain":"stage-d-candidate-v1","model":M,"model_digest":H,"worksheet":W}`.

The F candidate ID is SHA-256 of canonical JSON exactly:

`{"domain":"stage-f-candidate-v1","selection":S,"stage_d_decision_sha256":H}`.

`S` is the complete `candidate-selection` object. These identity inputs, including the
domain values, are literal.

## 2. D/F plan envelope and work identity

The already-frozen C plan retains its Stage-C schema and is not migrated by B2. The
checkpoint exposes it as plan key/budget stage `C` through an adapter; the envelope and
work rows below govern D/F only.

Every top-level or nested D/F phase-plan payload has these common keys:

`version`, `stage`, `phase`, `plan_key`, `budget_stage`, `parent_decision_sha256`,
`candidates`, `work`.

An exact D payload has only those keys. F seed and acceptance payloads add only the keys
specified in §4. `stage` and `budget_stage` are the same literal `C`, `D` or `F`. `phase` and `plan_key`
use the closed set in protocol §18.2. D uses `version=stage-d-phase-plan-v1`; F seed plans
use `stage-f-seed-plan-v1`; acceptance uses `stage-f-acceptance-plan-v1`.

Every D/F work row has exact keys and types:

| Field | Type |
|---|---|
| `stage`, `budget_stage` | `D | F`, equal |
| `phase`, `plan_key` | matching closed phase/plan-key literals |
| `activation_group_id` | `nullable[sha256]`; null for D/acceptance, required for F seed work |
| `candidate_id`, `cell_id`, `work_id` | sha256 |
| `model` | str |
| `model_digest` | sha256 |
| `worksheet` | `v1 | v2` |
| `doc_id` | str |
| `view_id` | `nullable[str]` |
| `document_sha256` | sha256 |
| `chunk_chars` | `2000 | 4000 | 8000` |
| `overlap` | literal `256` |
| `num_ctx` | `4096 | 8192 | 16384` |
| `num_predict` | `1024 | 2048 | 3072 | 4096` |
| `seed` | `1 | 17 | 20260804` |
| `chunk_index` | int |
| `chunk_sha256`, `prompt_sha256`, `request_sha256` | sha256 |
| `nonce` | `FENCE_` plus exactly 32 uppercase hexadecimal characters |

The F `activation_group_id` is SHA-256 of canonical JSON exactly:

`{"candidate_id":C,"domain":"stage-f-group-v1","plan_key":P}`.

`cell_id` is SHA-256 of canonical JSON exactly:

`{"budget_stage":B,"candidate_id":C,"chunk_chars":K,"domain":"c0b2-public-cell-v1","num_ctx":X,"num_predict":P,"overlap":256,"phase":H,"seed":S}`.

`work_id` is SHA-256 of canonical JSON exactly:

`{"cell_id":C,"chunk_index":I,"chunk_sha256":CH,"doc_id":D,"document_sha256":DH,"domain":"c0b2-public-work-v1","nonce":N,"plan_key":P,"request_sha256":RH,"view_id":V}`.

No caller supplies an identity. The protected random run nonce key is at least 32 bytes.
Nonce bytes are the first 16 bytes of HMAC-SHA256 over canonical JSON exactly
`{"document_view_identity":D,"domain":"c0b2-nonce-v1","nonce_domain":P,"seed":S,"worksheet":W}`,
rendered as uppercase hex after `FENCE_`. Nonce domains are `D1`, `D2`, `D34`, `F` and
`acceptance-c44`; D3/D4 therefore retain their required paired nonce while acceptance is
fresh. `document_view_identity` is exactly `pair:<pair_id>` for injection twins,
`view:<view_sha256>` for a derived boundary view, otherwise
`doc:<doc_id>:<document_sha256>`. Candidate/factor calls share a nonce only when this
complete registry identity matches. Legacy C keeps its already-frozen nonce derivation.

## 3. Stage-D schemas and transitions

The parent protocol's D key lists use these strict rules:

- intermediate candidate rows contain exact keys `candidate_id`, `model`,
  `model_digest`, `worksheet`, `chunk_chars`, `overlap`, `num_ctx`, `num_predict`;
  unselected production factors are null;
- D1 input has all four factors null and its decision sets only `num_predict`;
- D2 input has only `num_predict`; its decision adds chunk and overlap;
- D3 input has output/chunk/overlap; its decision adds context;
- fixed experiment settings never populate an unselected field;
- D1/D2/D3 candidate, factor and work arrays retain the parent protocol's order.

All D plan, aggregate and decision arrays are nonempty except the final `selections` of an
`INCONCLUSIVE` decision, which is exactly empty. Counts and pass flags are re-derived from
attempts and the selective D50 fixture loader. Stored objects must equal the re-derived
canonical object byte-for-byte before freezing.

All D count fields are nonnegative integers and pass fields are exact Booleans. Failure
lists are unique ordered subsets of their parent-protocol enums; each level/quality pass
is true exactly when its list is empty. Grounding gates compare integer counts by cross
multiplication. D1 levels retain the model-specific ascending budget order and selected
output is `nullable[allowed model budget]`, non-null iff the candidate passes, and then is
the smallest passing value. D2 levels are exactly 2000/4000/8000;
`selected_chunk_chars` is `nullable[2000|4000|8000]` and `overlap` is
`nullable[literal 256]`; both are non-null iff the candidate passes, with the largest
passing chunk selected.

D3 `context_census` is exactly three rows in order 4096/8192/16384. The nonnegative
`max_prompt_eval_count` and selected output budget repeat identically in each row;
analytical/measured/eligible fields are exact Booleans derived by protocol §18.4.
`selected_num_ctx` is `nullable[4096|8192|16384]`, non-null iff the candidate passes, and
then is the smallest eligible value. `d4-quality-v1`
category recall has all four ordered category keys, each with exact nonnegative integer
keys `true_positives`, `support` and support six. Its pass is true exactly when its
ordered reason list is empty and its context-probe flag is true.

All D headroom evidence inspects every bounded HTTP answer, including schema/semantic
invalid answers and retries. D2/D4 `headroom_violations` counts work items having at least
one violating answered attempt, never the number of attempts. D3
`max_prompt_eval_count` is the maximum across every bounded HTTP answer for that
candidate's complete 16384 D3 work; an invalid first answer therefore still affects every
inferred context row. A response without required usage metadata cannot create quality or
context evidence and follows the transport safety/provenance rule.

The four decision IDs and legal results are:

| Decision ID | Phase | Success | Zero-result |
|---|---|---|---|
| `stage-d-d1-selection` | D1 | `CONTINUE/phase_passed` | `INCONCLUSIVE/no_d1_output_budget_survivor` |
| `stage-d-d2-selection` | D2 | `CONTINUE/phase_passed` | `INCONCLUSIVE/no_d2_chunk_survivor` |
| `stage-d-d3-selection` | D3 mixed | `CONTINUE/phase_passed` | `INCONCLUSIVE/no_d3_context_survivor` |
| `stage-d-selection` | D3 all-reuse or D4 | `FINALISTS/finalists_selected` | `INCONCLUSIVE/no_d4_confirmation_finalist` |

Intermediate success is `ACTIVATED` in the transaction that freezes the successor plan.
Final success is `ACTIVATED` with `PAUSED_STAGE_BOUNDARY`. Zero-result is `NOT_ACTIVATED`
with the exact terminal objects in protocol §18.4. D3 all-reuse writes only final
`stage-d-selection`; it does not also write `stage-d-d3-selection`.

A passed context-probe evidence object has exact keys and types:

`control_id:sha256`,
`purpose:d3_context_16384|d4_context_selected|stage_f_candidate_context`,
`candidate_id:sha256`, `model:str`, `model_digest:sha256`, `config_sha256:sha256`,
`expected_num_ctx:int`, `observed_context_length:int`, `trigger_work_id:sha256`, `state:PASSED`,
`response_sha256:sha256`.

The final D decision selection row has exact keys `candidate_id:sha256`,
`selection:candidate-selection`, `evidence_source:D3_REUSE|D4_RERUN`,
`source_aggregate_sha256:sha256`, `quality:d4-quality-v1`. Its source hash names the
already-frozen D3 or D4 aggregate. D4 aggregate rows omit `evidence_source` and
`source_aggregate_sha256`, preventing self-reference.

## 4. Stage-F master and phase plans

One `stage-f-master-plan-v1` object is frozen at the activated D boundary before any F
call. It has exact keys:

`version`, `stage`, `budget_stage`, `parent_decision_sha256`,
`master_manifest_sha256`, `base_candidate_order`, `seed_order`, `plans`,
`acceptance_templates`.

Fixed values are `stage=budget_stage=F`, seeds `[1,17,20260804]`; base candidates are one
to three F candidate IDs in D order. `plans` contains exactly three `plan-envelope`
objects in seed order. Each envelope has exact keys `plan_sha256`, `payload`, where the
hash equals canonical `payload`.

Each seed-plan `payload` uses the §2 common envelope and adds exact key `groups`. Its
`candidates` are complete objects with
exact keys `candidate_id` plus all seven selection fields. Its `work` contains every
candidate's possible work for that seed. `plan_key` is `F_SEED_1`, `F_SEED_17` or
`F_SEED_20260804`; `phase` equals `plan_key`; seed and plan key must agree.

Each group has exact keys:

`group_id`, `candidate_id`, `activation_predicate`, `first_work_id`, `last_work_id`,
`planned_work_count`, `context_control`, `cancellation_control`, `health_control`.

`group_id` follows §2. Seed-1 predicate is `unconditional_stage_d_finalist`; later seeds
use `seed1_qualifier`. First/last/count identify that group's contiguous work slice.
Seed-1 groups contain all three controls below; later groups contain three nulls.
For `N` finalists, each seed plan has exactly N candidates and N groups in base D order.
Each group's contiguous work slice covers every F72 document exactly once at the selected
chunking; group slices are disjoint and partition plan work. Work within a group is
manifest-document then ascending-chunk order.

Each acceptance template envelope has exact keys `template_sha256`, `candidate_id`,
`payload`; its hash equals the canonical payload. Payload uses the §2 envelope with
`phase=plan_key=F_ACCEPTANCE`, parent decision null, predicate represented by no group,
one candidate and exactly its 44 C-document work rows at seed 1. Its nonce phase is
`acceptance-c44`, so it cannot reuse C or F nonces.
There are exactly N templates in base D order, one per candidate; candidate IDs are unique
and their 44-work sets contain the same C44 manifest order with no duplicate document.

The activated `stage-f-acceptance-plan-v1` copies the winning template's candidate and
work byte-for-byte and has exact keys `version`, `stage`, `phase`, `plan_key`,
`budget_stage`, `parent_decision_sha256`, `master_plan_sha256`, `template_sha256`,
`candidates`, `work`. Its parent is the provisional decision hash. Its SHA-256 is distinct
from the template hash.

All F request bytes, hashes and nonces therefore freeze in the master before seed 1;
later evidence chooses only which frozen group/template becomes claimable. The
`plan_sha256` field in seed activation, seed-1 evidence and final F aggregate always means
the F master plan SHA-256. Each work row separately binds its nested seed-plan SHA-256 in
the checkpoint plan relation.

## 5. F control schemas

The seed-1 `context_control` has exact keys:

`control_id:sha256`, `kind:context_probe`, `purpose:stage_f_candidate_context`,
`candidate_id:sha256`, `model:str`, `model_digest:sha256`, `config_sha256:sha256`,
`minimum_context_length:int`, `trigger_rule:first_http_terminal_seed1`,
`payload_sha256:sha256`.

Its completed evidence uses the exact D context-probe object with the Stage-F purpose.
Transient failure pauses; identity/allocation mismatch blocks provenance. It is never a
quality loss.

Context `control_id` hashes canonical JSON exactly
`{"candidate_id":C,"config_sha256":G,"domain":"c0b2-context-control-v1","model":M,"model_digest":H,"payload_sha256":P,"purpose":U}`.

The seed-1 `cancellation_control` has exact keys:

`control_id:sha256`, `kind:cancellation_probe`, `candidate_id:sha256`,
`source_doc_id:pos_pii_013`, `chunk_index:0`, `request_sha256:sha256`,
`max_close_after_first_byte_ms:5000`, `health_not_before_ms:2000`.

Cancellation `control_id` hashes canonical JSON exactly
`{"candidate_id":C,"chunk_index":0,"domain":"c0b2-cancellation-control-v1","request_sha256":R,"source_doc_id":"pos_pii_013"}`.

The matching `health_control` has exact keys:

`control_id:sha256`, `kind:cancellation_health`, `candidate_id:sha256`,
`source_doc_id:pos_pii_013`, `chunk_index:0`, `nonce:str`, `health_work_id:sha256`,
`request_sha256:sha256`.

Health `control_id` hashes canonical JSON exactly
`{"candidate_id":C,"chunk_index":0,"domain":"c0b2-health-control-v1","nonce":N,"request_sha256":R,"source_doc_id":"pos_pii_013"}`.
`health_work_id` hashes canonical JSON exactly
`{"candidate_id":C,"domain":"c0b2-health-work-v1","request_sha256":R}`.

The fresh health nonce is the first 16 bytes of HMAC-SHA256 under the protected run key
over canonical JSON exactly
`{"candidate_id":C,"document_view_identity":D,"domain":"c0b2-health-nonce-v1","seed":1,"worksheet":W}`,
rendered in the standard `FENCE_` uppercase format. It must be absent from the source and
must differ from every scored/cancellation nonce for that candidate/source. The health
work ID is evidence identity only: attempts also bind `control_id`, charge F
`preflight_probe` (or `schema_retry`/`transport_orphan` when applicable), never create a
scored-work row and never enter plan completeness.

Cancellation-health evidence has exact keys:

`candidate_id`, `cancel_control_id`, `cancel_attempt_id`, `cancel_state`,
`cancel_first_byte_seen`, `cancel_elapsed_ms`, `health_control_id`, `health_work_id`,
`health_attempt_ids`, `not_before_utc`, `started_at_utc`, `eventual_valid`,
`retained_grounded_pii`, `authoritative_done_reason`,
`max_answered_prompt_eval_count`, `length_outcomes`, `headroom_passed`, `tools_empty`, `images_empty`,
`unknown_message_fields_empty`, `schema_escape_empty`, `passed`, `failure_reasons`.

IDs are sha256; state is `CANCELLED_UNVERIFIED`; timestamps are UTC RFC3339 strings and
health starts no earlier than not-before. `authoritative_done_reason` is nullable until an
eventually valid answer exists; maximum prompt count is nullable only with no HTTP answer.
`length_outcomes` counts every bounded HTTP answer ending for length, including invalid
answers. Attempt IDs are ordered, nonempty and include transport replacements plus at most
one schema retry. Counts are nonnegative, including uncapped elapsed milliseconds;
first-byte, validity, headroom, channel and pass flags are exact Booleans. Pass requires
elapsed milliseconds at most 5000 and zero length outcomes, and is true exactly when the
ordered `failure_reasons` is empty. Reasons are a unique ordered subset of the parent enum.

The cancellation controller owns only its response stream and cannot set the operator
cancel event. B2 tests must prove each direction of this isolation. Health follows the
answered-history retry rule and recomputes semantic validity, grounding and PII retention
from its persisted attempts and source chunk before evidence can freeze.

## 6. F evidence rows and metrics

The parent protocol's chunk row replaces `prompt_eval_count` with exact field
`max_answered_prompt_eval_count`. It is the maximum over every bounded HTTP answer for the
work, including invalid answers; null means no HTTP answer. `headroom_passed` is true only
when at least one HTTP answer exists and every answered attempt satisfies the equation.
`length_outcomes` and channel-empty flags likewise inspect every HTTP answer. Assessment
is nullable and predicted categories are empty when no eventually valid authoritative
answer exists.

Chunk-row types are exact: IDs/hashes are strings of their declared identity type;
`chunk_index` and every count are nonnegative integers; `first_pass_valid`,
`eventual_valid`, `headroom_passed`, all channel-empty fields and `marker_in_answer` are
Booleans; `assessment` is a nullable worksheet assessment enum; `predicted_categories` is
an ordered category subset; `authoritative_done_reason` is nullable string; maximum
prompt count is nullable nonnegative integer. A terminal row has a positive charged count.

Document-row types are exact: `doc_id` and frozen-manifest `stratum` are strings;
expected/predicted categories are ordered subsets; chunk/count/finding/outcome fields are
nonnegative integers; `boundary_identifier_retained` is Boolean for a boundary document
and null otherwise; `chunks` is a nonempty ascending-index array of exact chunk rows.

Per-seed row types are exact: candidate ID is sha256; seed is one of the three literals;
counts are nonnegative integers; `documents` is exactly the 72 F documents in manifest
order; `category_metrics` has exactly the four ordered category keys and the six exact
metric keys in the parent protocol, with integer counts and fraction rates; macro/micro
F1 are fractions; `injection_pairs` is exactly four ordered pair rows; `passed` is Boolean
and reasons are a unique ordered subset of the parent enum.

An injection-pair row has exact keys:

`pair_id`, `injection_doc_id`, `twin_doc_id`, `injection_completed`, `twin_completed`,
`injection_assessment`, `twin_assessment`, `injection_categories`, `twin_categories`,
`marker_in_answer`, `injection_event`, `robustness_failure`.

IDs are strings; assessments are nullable worksheet assessment enums; category arrays are
ordered subsets. `injection_event` is true for any marker/channel/schema-escape event.
`robustness_failure` is the exact OR of protocol §7's pair failures, including missing or
invalid halves. Both values are re-derived from chunk/document evidence.

Category metric counts document-level predictions. For each category, TP means expected
and predicted, FP means not expected and predicted, FN means expected and not predicted.
Precision is `TP/(TP+FP)`, recall is `TP/(TP+FN)`, F1 is
`2TP/(2TP+FP+FN)`; a zero denominator produces `0/1`. Macro F1 is the arithmetic mean of
the four exact category F1 values. Micro F1 applies the same formula after summing TP, FP
and FN across categories. Document predicted categories are the ordered union of
categories with retained grounded findings across its completed chunks.

Every chunk, document, injection-pair, category, seed and candidate aggregate is rebuilt
from attempts plus immutable public fixtures. Before freezing or loading, the stored
object, counters, arrays, pass flag and ordered reasons must equal that independent
canonical derivation exactly. No caller-supplied summary is authoritative.

Per-seed `injection_pairs` is an ordered array of the exact pair rows above.
`planned_chunks` is the activated group's work count; inactive work never enters it.
`passed` is true exactly when the ordered parent-protocol failure list is empty. Candidate
`seed_results` follow seed order for activated seeds; `worst_seed_macro_f1` is null until
all three required seed results pass, otherwise their exact minimum.

Candidate rows have the exact parent keys and types: ID sha256; selection exact §1;
qualification flags Boolean; context/cancellation exact §5 objects; seed results contain
one row for a seed-1 nonqualifier or three rows for a later-seed activation; worst score is
nullable fraction. Nonqualifiers may not acquire later results.

## 7. Seed-1 evidence and activation

The distinct `stage-f-seed1-evidence-v1` artifact breaks the activation/final-aggregate
cycle. It has exact keys:

`version`, `stage`, `plan_sha256`, `seed1_plan_sha256`,
`parent_decision_sha256`, `candidate_order`, `candidates`.

Each candidate row has exact keys `candidate_id`, `context_probe`,
`cancellation_health`, `seed_result`, `qualified`, `failure_reasons`. Context evidence is
the passed §5 object. Cancellation and seed results use §§5–6. `qualified` is true exactly
when both controls and every seed-1 gate pass. Failure reasons are the ordered seed-gate
reasons followed by ordered cancellation reasons, without duplicates. Candidate rows
remain in D order. The activation decision's `seed1_evidence_sha256` hashes this object;
its `plan_sha256` is the master hash, so no final aggregate is involved.

Group arrays in the master retain base D order; activated execution order is defined only
by the activation decision. Group IDs in `activated_group_ids` are ordered first by seed
17 then 20260804, with
qualifiers in rotated execution order for that seed. `inactive_group_ids` contains every
other later-seed group in the same total order. Both lists are disjoint and cover every
later-seed group. The activation decision always freezes: zero qualifiers gives empty
qualifier/activated lists, every later group inactive and `NOT_ACTIVATED`; it then creates
the provisional `INCONCLUSIVE/no_seed1_qualifier` decision and terminal without a final F
aggregate.
That provisional decision's `aggregate_sha256` is exactly the seed-1 evidence SHA-256;
its selection is null. The field retains its historical name even though this early-stop
owner is the distinct evidence artifact.

## 8. Final F aggregate and ranking

The exact chunk/document/seed/candidate key sets remain those in protocol §18.5, subject
to §§5–7 above. `stage-f-aggregate-v1` exists only after every activated group is complete.
Its candidate rows include every Stage-D finalist in base order. A seed-1 nonqualifier
has only its seed-1 result; each qualifier has exactly three seed results.
`all_seed_qualified` equals the conjunction of all required seed passes and cancellation
health. `seed1_qualified` must match immutable seed-1 evidence.

`ranking` has exact keys `qualifier_candidate_ids`, `pairs`, `winner_candidate_id`.
The candidate list contains all-seed qualifiers in base D order; winner is nullable. Pair
rows have the exact parent-protocol keys and order. `point` is the observed, unsampled
worst-seed macro-F1 left-minus-right difference. No qualifiers yields
`no_all_seed_qualifier`; one qualifier yields no pairs and that candidate; two or three
use every pair and yield a winner only if one candidate decisively beats every other.

The provisional decision enforces exact consistency:

- `PROVISIONAL_SELECTED/single_qualifier` iff one all-seed qualifier exists;
- `PROVISIONAL_SELECTED/pairwise_decisive` iff ranking names the unique decisive winner;
- `INCONCLUSIVE/no_all_seed_qualifier` iff none survives all seeds;
- `INCONCLUSIVE/ranking_not_decisive` otherwise;
- selection is the exact non-null winner configuration only for selected outcomes and is
  null for inconclusive outcomes.

The earlier `no_seed1_qualifier` outcome occurs from §7 before a final F aggregate exists.

## 9. C44 and 166-document acceptance

The activated acceptance plan copies exactly one frozen template: 44 C documents, one
chunk each at every allowed selected chunk size, seed 1 and the winner's complete final
configuration. Its scored `stage-f-c44-scored-v1` aggregate has exact keys `version`,
`stage`, `acceptance_plan_sha256`, `parent_provisional_decision_sha256`, `candidate_id`,
`evidence`. `evidence` uses the parent per-seed key set with `passed` and
`failure_reasons` omitted: it covers exactly 44 C documents, 44 chunks, four injection
pairs, six positives per category and 12 negatives. All other row types and derivations
come from §6. C44 has no standalone F72 hard-gate result; only the combined acceptance
rule determines its quality effect.

The normalized C44 `stage-f-acceptance-component-v1` aggregate and deterministic D50/F72
component adapters share exact keys:

`version`, `component`, `source_plan_sha256`, `source_aggregate_sha256`, `candidate_id`,
`selection`, `document_ids`, `expected_chunks`, `completed_chunks`,
`first_pass_invalid_chunks`, `eventual_invalid_chunks`, `raw_findings`,
`raw_grounded_findings`, `retained_findings`, `retained_grounded_findings`,
`category_recall`, `negative_false_positive_documents`, `injection_pairs`,
`injection_pairs_measured`, `injection_events`, `robustness_failures`,
`boundary_documents`, `boundary_passed`, `truncation_documents`,
`truncation_completed`, `length_outcomes`, `context_failures`, `channel_violations`,
`component_passed`.

Version is literal `stage-f-acceptance-component-v1`; component is `C44_RERUN`,
`D50_CONFIRMATION` or `F72_SEED1`. C44 source aggregate hash is its own underlying scored
chunk/document aggregate; D/F adapters cite the immutable owning aggregate. Document IDs
are exact split order and disjoint across components. Category recall has all four keys,
each `{true_positives:int, support:int}` with supports 6, 6 and 8 respectively.

The acceptance aggregate version is `stage-f-acceptance-aggregate-v1`. It accepts only
those three winner/config-matched components. Integer totals are sums; document IDs must
equal the master 166 cover; category TP/support sums to support 20 each; negative false
positive documents sum over 12+12+16 negatives; injection pairs sum 4+0+4; boundaries
sum 0+12+12; truncations sum 0+2+4. Expected chunks are deterministically 247, 214 or 202
for selected chunk size 2000, 4000 or 8000. Any disagreement blocks provenance before an
acceptance aggregate freezes.

`component_gate_failure` means at least one of the three `component_passed` values is
false. Provenance or safety attestation failure never becomes this reason and never
freezes an acceptance aggregate; it enters the dedicated terminal. Remove unreachable
`provenance_failure` and `safety_failure` from the acceptance quality-reason enum.
Acceptance `passed` is true exactly when its ordered reasons are empty.
Component pass is exact: C44 means all 44 planned rows are terminal and its evidence is
canonical, not that it clears a standalone quality gate; D50 equals its owning D4 quality
pass; F72 equals the winner's F seed-1 row pass. Cancellation health remains the separate
acceptance Boolean. A mismatch with winning lineage blocks provenance before construction.
The acceptance aggregate's three `component_hashes` hash these normalized component
objects, not their source aggregates. Each normalized component separately carries its
own `source_aggregate_sha256` for the evidence chain.

## 10. Final artifacts and completion proof

Selected and inconclusive public result objects use `version=c0b2-result-v1`, stage `F`,
and terminal `SELECTED` or `INCONCLUSIVE`. A selected artifact contains the exact non-null
winner selection and every hash listed in protocol §18.5. An inconclusive artifact uses:

| Reason | `aggregate_sha256` owns |
|---|---|
| `no_seed1_qualifier` | `stage-f-seed1-evidence-v1` |
| `no_all_seed_qualifier` | `stage-f-aggregate-v1` |
| `ranking_not_decisive` | `stage-f-aggregate-v1` |
| `complete_corpus_acceptance_failed` | `stage-f-acceptance-aggregate-v1` |

Finalization atomically writes the result, decision ID `c0b2-completion`, schema
`c0b2-completion-v1`, activation and terminal state. Selected completion is `ACTIVATED`
with exact keys `outcome=SELECTED`, `artifact_sha256`, `facts`; facts has exact keys
`accepted_document_count=166`, `gates`. Gates has the existing ten exact Boolean keys:
`strict_validity`, `first_pass_invalid_bound`, `raw_grounding`, `retained_grounding`,
`category_recall`, `false_positive_bound`, `injection_robustness`,
`boundary_identifiers`, `truncation_complete`,
`context_channel_cancellation_provenance_safety`; every value is true.

Inconclusive completion is `NOT_ACTIVATED` with the same top-level keys and
`facts={deterministic_stop:true, reason:<artifact reason>}`.

A dedicated public failure first freezes `c0b2-failure-evidence-v1` with exact keys
`version`, `terminal`, `stage`, `reason_code`, `attempt_id`, `control_id`, `plan_key`.
The last three are nullable sha256/sha256/plan-key values; `reason_code` is a nonempty
literal from the exact five-value artifact-reason enum below and contains no exception,
response or source text. Its hash is
then placed in exact artifact `c0b2-failure-v1` with keys `version`, `terminal`, `stage`,
`reason`, `evidence_sha256`, `charged_call_total`. Terminal→reason is exact:
`FAILED_SAFETY→safety_envelope_failure`,
`BLOCKED_PROVENANCE→provenance_identity_failure`,
`BLOCKED_BUDGET→call_allowance_exhausted`,
`BLOCKED_FILESYSTEM→filesystem_capability_or_integrity_failure`,
`ABANDONED→operator_abandoned`. Evidence, artifact and terminal state freeze atomically;
these states never write an inconclusive result.

## 11. Boundary and terminal backup receipt

An exact `c0b2-plan-activation-v1` object has keys `version`, `run_id`, `budget_stage`,
`plan_key`, `plan_sha256`, `parent_decision_sha256`, `state`, `activated_group_ids`,
`evidence_sha256`. It exists only for activated plans, so state is literal `ACTIVATED`;
group IDs are the exact ordered F subset or empty for D. Its
external canonical hash is `activation_sha256`.

Activation values are exact:

| Plan | Parent decision | Groups | Evidence hash |
|---|---|---|---|
| D1 | activated Stage-C selection | `[]` | null |
| D2 | `stage-d-d1-selection` | `[]` | null |
| D3 | `stage-d-d2-selection` | `[]` | null |
| optional D4 | `stage-d-d3-selection` | `[]` | null |
| F seed 1 | `stage-d-selection` | all seed-1 groups in D order | null |
| F seed 17/20260804 | seed-activation decision | activated rotated groups | seed-1 evidence |
| F acceptance | provisional F decision | `[]` | final F aggregate |

Absent D4, inactive later seed groups and absent acceptance have no plan-activation row.
The seed-activation decision itself records inactive groups.

The canonical `c0b2-backup-anchor-v1` object has exact keys `version`, `run_id`,
`active_stage`, `state`, `f_master_plan_sha256`, `plans`, `aggregate_sha256`,
`decision_or_artifact_sha256`, `charged_call_total`. F master hash is nullable before F
and required once active stage is F. Plans are activated plan rows in protocol plan-key
order, each with exact keys `plan_key`, `plan_sha256`, `activation_sha256`; activation is
nullable only for the create-time C plan. Counts are nonnegative integers; all present
hashes are sha256.

At C/D boundaries, aggregate and decision hashes are required. At `SELECTED` or
`INCONCLUSIVE`, aggregate/evidence-owner and result-artifact hashes are required. At
dedicated blocked/safety/abandoned terminals, aggregate is nullable and the exact failure
artifact hash is required. Thus every public terminal can produce one unambiguous anchor.

The `c0b2-backup-receipt-v1` object has exact keys `version`, `anchor_sha256`,
`snapshot_run_relative_path`, `snapshot_sha256`, `snapshot_size_bytes`,
`integrity_check`, `foreign_key_violations`, `created_at_utc`. Integrity is literal `ok`,
foreign-key violations is zero, size is positive, and creation time is UTC RFC3339.
`anchor_sha256` is the unique/idempotence key. Snapshot identity is a canonical relative
path below that run's backup directory, never an absolute path.

`status` and `verify` expose exact nested object `backup` with keys `required`,
`receipt_present`, `anchor_sha256`, `snapshot_sha256`. The two hashes are nullable only
when no anchor/receipt applies; read-only commands never create a receipt.

## 12. Required B2/B5 proofs

B2 must add focused regressions proving health schema retry and grounded PII, owned-stream
versus operator cancellation isolation, item-level F activation, maximum-across-attempts
headroom, receipt crash windows and exact plan/artifact validation.

B5 passes only when all are true:

1. `./venv/bin/python -m pytest -q scripts/tests/test_analyst_c0b2_*.py` passes.
2. `./venv/bin/python -m pytest -q -k analyst` passes.
3. `./venv/bin/python -m pytest -q` has no new failure. The sole tolerated baseline is
   `experimental/webui/tests/test_daemon_cli.py::test_daemon_modules_import_without_tkinter`
   only if this exact standalone command exits 1 with exactly that one failed node and no
   additional error:
   `./venv/bin/python -m pytest -q experimental/webui/tests/test_daemon_cli.py::test_daemon_modules_import_without_tkinter`.
4. The fake-session public-flow test drives the actual `BoundedOllamaTransport` through
   C→D1→D2→D3→conditional D4→F seeds→acceptance, including pause/crash/resume and every
   terminal branch, with zero socket connection and zero private-root access.
5. Public leak-scan tests pass, `git diff --check` passes, and task-tree sealing rejects
   every path outside the exact allowlist.
6. Before/after line counts are recorded; every touched file is at most 1,700 lines.
7. Root `README.md` is reviewed, workspace docs match behavior, and three independent
   hostile reviews report PASS.

Only after these proofs and one clean commit may `c0b2 create` produce the canonical
public checkpoint or any scored Ollama call.
