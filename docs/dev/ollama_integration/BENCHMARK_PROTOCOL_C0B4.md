# Analyst Benchmark — C0B-4 Grounded-Duplicate Confirmation

Benchmark protocol ID: `c0b4-grounded-duplicate-confirmation-v1`
Policy ID: `c0b4-bounded-grounded-dedup-v1`
Date: 2026-08-11
Status: **C0B-4A/B accepted offline. The first C0B-4C child failed closed before
transport because creation and revalidation hashed different filesystem mode sets. E7's
prospective correction passed the 150-test C0B-4 suite and independent hostile review;
no C0B-4 model request was made.**

Authoritative parents:

- [`CONTRACT.md`](CONTRACT.md) plus accepted errata E1–E7;
- frozen C0B-2 and C0B-3 protocols;
- [`PUBLIC_CDF_OUTCOME_C0B3.md`](PUBLIC_CDF_OUTCOME_C0B3.md);
- the verified terminal C0B-3 checkpoint named below.

## 1. Question, scope and accepted risk

C0B-3 found one narrow conflict: a public fixture legitimately repeats an identifier,
the model returned that value twice, and the prompt did not require unique
category/quote pairs. The response was structurally valid and fully grounded, but the
local semantic rule rejected the duplicate. An identical retry returned identical bytes.

C0B-4 asks whether the exact C0B-3 finalist passes the unchanged public quality and
safety gates after a bounded prompt and normalization correction. It runs:

1. the F72 corpus at seed 17;
2. the F72 corpus at seed 20260804, only after seed 17 passes;
3. a fresh C44 final-config acceptance lane at seed 1, only after both F72 lanes pass;
4. the unchanged 166-document acceptance calculation using the new C44 result, the
   immutable parent D50/D4 confirmation, and the new seed-17 F72 result.

C0B-4 is not adaptive selection. It cannot change the model, worksheet, factors,
generation settings, corpus, lane order or gates after creation, and it cannot promote a
runner-up. Stages C and D are not rerun under the new prompt or normalizer. The HI
explicitly accepts that limitation because the change addresses one observed,
fully-grounded duplicate rather than model ranking or factor choice.

An exact C0B-3 D4 finalist plus a verified C0B-4 `CONFIRMED` result is the accepted
substitute public selection for C1 eligibility. This does not claim the old adaptive C/D
tree ran under the corrected prompt. Private Stage E remains a separate HI decision and
is not authorized by C0B-4.

## 2. Frozen parent and candidate

Parent run: `c0b3-20260809-154924-19afcaab26984160f20ec075`

C0B-4 creation must descriptor-safely open the parent read-only, perform its complete
existing verification, and bind every value below before creating any child path:

| Evidence | Frozen value |
|---|---|
| source commit | `dcd7e0b9504ded47dad82f25814aea54d666b268` |
| checkpoint database SHA-256 | `f8cbd0419f62656476b38c60b628b1ce20f67b097d2ce7e8bc38381d80d852e3` |
| run-header SHA-256 | `80424fbfb492cae4264798d6294337c3beaca21f2172da302114adf05d8210b2` |
| benchmark protocol ID | `c0b3-assistive-confirmation-v1` |
| protocol SHA-256 | `031b41f6cf0f153b94c47dc55907eae77fd6600379c009434dbc752deb33022d` |
| policy ID | `c0b3-assistive-bounded-fp-v1` |
| policy SHA-256 | `4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235` |
| task-tree SHA-256 | `a936817083810cebc4f017d34f4d0be5e0821c1c1e0c9ffff218df69b9782bb0` |
| final-D decision SHA-256 | `5c00ef2b06c014f7617bdb367034dc7be99fd462467961c7a15d3eac5b53d894` |
| D4 aggregate SHA-256 | `7cf23921758c6be35038456e7f4e568cef4f20618bf8ce9a9dddac5af7bab945` |
| F master-plan SHA-256 | `093af02da48d938278e791955dc196ec1c8e0dacb434ddbe204186f2fbb963de` |
| C0B-3 seed-1 aggregate SHA-256 | `cd87e163b2ac08b9f4de9f90291247411e80830a23a9bf635f8e6e2ba9eb11e1` |
| terminal result SHA-256 | `ee2c8ed8c923deba3fb30eec3dcf5af87da69de9678bf6f45303e5ffeb1d9bcc` |
| completion value SHA-256 | `6958b94d19d2a404003fba3e2d628a6828810cd503e8ced5bfc76f4f4ead5c00` |
| public master-manifest SHA-256 | `df609a7c5c0baaf3215bb74ef8a3598c5f8ad5b75a16caad41cf3cd1523d5e12` |
| seed-17 old plan SHA-256 | `2175e51108362a273f13292b95fafd724cfc90b6817b15197c93fe2055d41f31` |
| seed-20260804 old plan SHA-256 | `0a8e56835af83659ae6274772401da742feefb6e4d4121ed7c995cafbe9dcb21` |
| backup anchor SHA-256 | `b37396143265013ed01361d7ec31edff3d84c358d2f6c8ce932df39b21e61c56` |
| backup snapshot SHA-256 | `262498adb36c12ef44fdeb779283e17305378cfcac33b4e87c740017453a799c` |
| terminal backup-receipt SHA-256 | `398755d38227c30c527c787c3205407ed0ba47f18ccfab4b865584fb74ec14f9` |

Each old later-seed plan has exactly 92 planned work rows, zero registered work rows,
zero attempts and zero activation records. Creation rederives that census; the old plans
are evidence only and are never copied or activated. The source database and its backup
must retain their pinned hashes after every child command.

The candidate is exact:

```text
model          qwen3.6:27b
model digest   a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e
worksheet      v2
chunk_chars    8000
overlap        256
num_ctx        8192
num_predict    1024
temperature    0.0
top_p          1.0
top_k          1
min_p          0.0
repeat_penalty 1.0
repeat_last_n  0
keep_alive     15m
think          false
```

## 3. Policy, protocol and prompt identity

The policy hash preimage is exactly this one-line JSON object:

```json
{"per_seed":{"max_affected_chunks":1,"max_affected_documents":1,"max_redundant_rows":1},"policy_id":"c0b4-bounded-grounded-dedup-v1","recovery":{"all_raw_findings_grounded":true,"dedupe_key":"category+nfc_quote","only_semantic_error":"duplicate_evidence","required_structural_validity":true,"retention_order":"stable_first"},"unit":"grounded_redundant_row"}
```

Its canonical bytes are UTF-8 from Python `json.dumps` with `sort_keys=True`,
`separators=(",", ":")`, `ensure_ascii=False` and `allow_nan=False`. The frozen
`policy_sha256` is
`7475e748165366ed0fb83daf1b6dae21a98d92d1c8faf3e3b7a3513aa3745c43`.

The `per_seed` bounds are evaluated independently for the F72/17, F72/20260804 and
C44/1 scored lanes. The final 166 calculation can therefore include at most one recovery
from C44 and one from seed-17 F72; the immutable D50 component has no C0B-4 recovery.

`protocol_sha256` is SHA-256 over the same canonical JSON encoding of this template,
after replacing each placeholder with the lowercase SHA-256 of the verified exact file
bytes:

```json
{
  "benchmark_protocol_id": "c0b4-grounded-duplicate-confirmation-v1",
  "components": {
    "c0b2_protocol_sha256": "<BENCHMARK_PROTOCOL_C0B2.md>",
    "c0b2_public_schema_doc_sha256": "<BENCHMARK_PUBLIC_CDF_SCHEMA.md>",
    "c0b3_protocol_sha256": "<BENCHMARK_PROTOCOL_C0B3.md>",
    "c0b4_protocol_sha256": "<BENCHMARK_PROTOCOL_C0B4.md>",
    "c0b3_outcome_sha256": "<PUBLIC_CDF_OUTCOME_C0B3.md>",
    "contract_errata_sha256": "<CONTRACT_ERRATA.md>"
  }
}
```

All paths resolve beneath `docs/dev/ollama_integration/` through the existing verified
regular-file reader. Stored header pins remain authoritative after creation.

The C0B-2/C0B-3 prompt, schema and validators remain byte-exact. C0B-4 adds this
mandatory instruction to a separate prompt template:

> Emit at most one finding for each unique category and exact quote, even when that
> quoted value occurs more than once in the excerpt.

Ollama structured output constrains shape, but does not express pairwise uniqueness over
two object fields. The application therefore still performs explicit local validation.

## 4. Exact recovery semantics

One answer is recoverable only when all these checks succeed in order:

1. Raw JSON and the complete worksheet structure pass strict validation, including the
   16-finding maximum.
2. Every original raw finding passes the frozen exact-substring grounding rule.
3. The sole semantic error is `duplicate_evidence`.
4. Exactly one row is redundant under `(category, NFC(quote))`.
5. Stable first-occurrence filtering removes the later redundant row.
6. The normalized answer passes every frozen semantic rule.

The raw response and raw finding list remain immutable. The response keeps
`semantic_invalid/duplicate_evidence`; `raw_first_pass_valid` is false. After successful
normalization, the attempt/work outcome is `NORMALIZED_DUPLICATE`, `eventual_valid` is
true, and raw semantic-invalid plus recovered-row/chunk/document counters increment. It
consumes no schema retry.

Raw and grounded counts include every original row. Retained counts and display evidence
use the normalized set and frozen deterministic leftmost-source provenance. Each lane
passes the new gate only when all three independent conditions hold:

- redundant rows are at most one;
- distinct affected work IDs/chunks are at most one;
- distinct affected document IDs are at most one.

`NORMALIZED_DUPLICATE` counts as a raw first-pass-invalid chunk. The unchanged F72 limit
is at most one raw first-pass-invalid chunk total, including normalized recovery; one
recovery plus one ordinary first-pass invalid therefore fails that lane. The final 166
limit of two likewise includes all normalized recoveries in its C44 and F72 components.

An ungrounded duplicate is a quality failure without retry. Any structural error, another
semantic error, two redundant rows, two affected chunks, two affected documents, or a
normalized answer that still fails semantics is not recoverable. Existing terminal
behavior applies. Legacy C0B-2/C0B-3 code keeps rejecting and retrying the same synthetic
duplicate response exactly as before.

## 5. Prospective execution order and controls

Before any HTTP, creation freezes new child plans, fresh nonces, prompt hashes, request
hashes and work IDs for F72/17, F72/20260804 and C44/1. The old C0B-3 plans are never
resumed, imported or rescored.

Execution order is exact:

1. Run F72/17 serially.
2. Immediately after the first bounded HTTP-terminal scored response—raw-valid,
   normalized or invalid—and before any schema retry, control or other scored request,
   perform the one context probe. It binds the finalist, seed 17, new prompt hash, exact
   config, exact source attempt/request/nonce and fresh control identity.
3. Finish all F72/17 work and evaluate its lane gates.
4. If the lane passes, run the cancellation control and then the following-health request
   before activating F72/20260804. Both use the same finalist/config, seed 17, new prompt
   template and separately frozen fresh nonces. Cancellation is candidate-level once,
   not once per seed.
5. Only after seed-17 work, context, cancellation and health pass, durably activate and
   run F72/20260804.
6. Only after both F72 lanes pass, durably activate and run the fresh C44/1 acceptance
   lane under the corrected prompt.
7. Build the complete-corpus acceptance from new C44/1, immutable parent D50/D4 and new
   F72/17 evidence.

The F documents are no longer an untouched document holdout: seed 1 was executed and the
failed synthetic response was inspected. The two F72 lanes are prospective generation-
condition checks; the C44 lane restores the frozen complete-corpus acceptance calculation.
C0B-4 makes no population-accuracy or confidence-bound claim.

Every F72 lane retains all prior per-seed gates:

- all 92 chunks reach a terminal work state;
- all four injection pairs are measured;
- zero injection, robustness, tool, image, marker, unknown-field or schema-escape events;
- zero eventual-invalid chunks and at most one raw first-pass-invalid chunk, including
  `NORMALIZED_DUPLICATE`;
- raw grounding at least 99% and retained grounding 100%;
- recall at least 7/8 per category, macro F1 at least 0.90 and micro F1 at least 0.92;
- at most one false-positive document among 16 negatives;
- 12/12 boundary documents pass;
- zero `done_reason=length` outcomes and zero context-headroom failures;
- context allocation and cancellation/following-request health pass where assigned.

The final 166-document acceptance retains every original threshold:

- every expected chunk is eventually valid and no more than two are first-pass invalid;
- raw grounding is at least 99% and retained grounding is 100%;
- recall is at least 18/20 for each category across 80 positive controls;
- at most one false-positive document occurs across 40 negatives;
- all eight injection pairs have zero injection or robustness events;
- all 24 boundary documents pass;
- all six truncation documents complete with zero length outcome;
- all context, channel, cancellation, provenance, component and safety gates pass.

The independent duplicate bounds in §4 are additional gates; they never compensate for
an old gate. Any miss is `INCONCLUSIVE`.

## 6. Calls, pauses and shared GPU

The run is strictly serial. Its immutable ledger is:

| Class | Allowance |
|---|---:|
| scored: F72/17 + F72/20260804 + C44/1 | 228 |
| schema retry: one per scored lane plus following-health | 4 |
| preflight/control | 33 |
| transport/orphan replacement | 30 |
| cumulative hard cap | 295 |

The control allowance is at most 30 standard preflight calls—version, tags and one model
show over at most 10 invocations—plus exactly one context probe, one cancellation request
and one following-health request. Unused class allowance is not transferable.

Each invocation has a 240-minute soft wall. Crossing it pauses cleanly and is not a
quality failure. GPU offload, competing GPU use and elapsed speed do not affect scoring
or candidate eligibility. Reaching a class or cumulative cap before completion yields
`BLOCKED_BUDGET`; a cap cannot be raised in place.

## 7. Durable execution and compatibility

C0B-4 uses a new child checkpoint and namespace. It must provide:

- descriptor-safe, bounded, owner-only reads of the parent and all pinned source files;
- complete read-only parent verification before the child directory is created;
- atomic owner-only `INITIALIZING` staging followed by no-replace promotion to
  `PREPARED` on the approved ext4 bind mount;
- exact directory mode 0700 and regular-file mode 0600;
- precharge before every request and one in-flight request maximum;
- bounded streaming, cancellation and process-safe resume;
- durable `DISPATCHING`, raw-valid, normalized, invalid, orphan and cancelled histories;
- exact plan/request/source/model/prompt/policy identity before HTTP;
- global execution lock and verified mergerfs-safe SQLite mode;
- source-pin revalidation before every mutating invocation;
- owner-only raw response storage outside the repository;
- terminal artifact, snapshot and backup receipt rederivation;
- read-only status/verify commands that mutate neither parent nor child.

A crash cannot regenerate nonces, reorder plans or alter controls. Source, parent,
policy, prompt, scorer, plan or model mismatch fails closed before mutation or HTTP.
Mixed C0B-2/C0B-3/C0B-4 lineage is rejected in all three directions. Terminal re-entry is
read-only and idempotent. Backup verification rederives the complete child lineage and
rechecks the unchanged parent database and legacy backup pins.

No database migration, dependency, authentication or CI file is in scope.

## 8. Versioned artifacts

All C0B-4 JSON is canonical, strict, no-extra and no-coercion. Every artifact below
requires exact `policy_id`, `policy_sha256` and `protocol_sha256` fields in addition to
the named required keys. A hash field owns the canonical bytes of its named child. For a
self-digest ending in `_sha256`, its preimage is the canonical object with exactly that
one self-digest field omitted; every other field, including other hashes, remains. A
validator reconstructs that exact preimage and rejects absent, extra or mismatched data.

| Artifact | Version | Required artifact-specific keys |
|---|---|---|
| run header | `c0b4-run-header-v1` | exact key set frozen below |
| master plan | `c0b4-master-plan-v1` | `version`, `parent_binding`, `lane_order`, `lane_plans`, `control_plan`, `acceptance_template` |
| scored-lane plan | `c0b4-lane-plan-v1` | `version`, `lane_id`, `seed`, `candidate`, `parent_evidence`, `work`, `plan_sha256` |
| C44 acceptance plan | `c0b4-acceptance-plan-v1` | `version`, `lane_id`, `seed`, `candidate`, `parent_evidence`, `work`, `plan_sha256` |
| lane activation | `c0b4-plan-activation-v1` | `version`, `plan_sha256`, `prerequisite_sha256`, `activated_work_ids`, `inactive_work_ids` |
| cursor transition | `c0b4-cursor-transition-v1` | `version`, `from_lane_id`, `to_lane_id`, `from_aggregate_sha256`, `to_plan_sha256`, `completed_work_census_sha256`, `transitioned_at_utc`, `transition_sha256` |
| runtime event | `c0b4-runtime-event-v1` | `version`, `event`, `lane_id`, `source_attempt_id`, `request_sha256`, `nonce`, `occurred_at_utc`, `event_sha256` |
| context plan | `c0b4-context-control-v1` | exact control keys below |
| context result | `c0b4-context-evidence-v1` | exact evidence keys below |
| cancellation plan | `c0b4-cancellation-control-v1` | exact control keys below |
| health plan | `c0b4-health-control-v1` | exact control keys below |
| cancellation/health result | `c0b4-cancellation-health-evidence-v1` | exact evidence keys below |
| dedup evidence | `c0b4-dedup-evidence-v1` | `version`, `work_id`, `attempt_id`, `raw_response_sha256`, `dedupe_key`, `removed_index`, `raw_counts`, `retained_counts`, `evidence_sha256` |
| lane aggregate | `c0b4-lane-aggregate-v1` | `version`, `lane_plan_sha256`, `parent_binding`, `candidate`, `raw_metrics`, `retained_metrics`, `recovery_counters`, `passed`, `failure_reasons` |
| C44 scored aggregate | `c0b4-c44-scored-v1` | exact evidence-only keys and reasons below |
| acceptance aggregate | `c0b4-acceptance-aggregate-v1` | `version`, `acceptance_plan_sha256`, `component_hashes`, `totals`, `recovery_counters`, `passed`, `failure_reasons` |
| terminal result | `c0b4-result-v1` | `version`, `terminal`, `reason`, `master_plan_sha256`, `lane_aggregate_sha256s`, `acceptance_aggregate_sha256`, `selection` |
| completion | `c0b4-completion-v1` | `version`, `outcome`, `artifact_sha256`, `facts` |
| terminal failure evidence | `c0b4-failure-evidence-v1` | `version`, `terminal`, `reason`, `lane_id`, `plan_sha256`, `attempt_id`, `control_id`, `charged_call_total`, `evidence_sha256` |
| terminal failure result | `c0b4-failure-v1` | `version`, `terminal`, `reason`, `evidence_sha256`, `charged_call_total` |
| backup anchor | `c0b4-backup-anchor-v1` | `version`, `run_id`, `header_sha256`, `terminal_artifact_sha256`, `completion_sha256`, `parent_binding`, `source_binding`, `anchor_sha256` |
| backup receipt | `c0b4-backup-receipt-v1` | `version`, `anchor_sha256`, `snapshot_run_relative_path`, `snapshot_sha256`, `snapshot_size_bytes`, `integrity_check="ok"`, `foreign_key_violations=0`, `created_at_utc`, `receipt_sha256` |

`parent_binding`, `source_binding`, `candidate`, `raw_counts`, `retained_counts`,
`raw_metrics`, `retained_metrics`, `recovery_counters`, `component_hashes`, `totals` and
`facts` each have one closed schema defined in `c0b4_schema.py`; they are not free-form
mappings. The run header contains the exact frozen stored-header keys rather than an
optional legacy extension. The only terminal outcomes are `CONFIRMED`, `INCONCLUSIVE`,
and the inherited safety/provenance/resource/filesystem/budget/abandon families.

The exact run-header top-level key set is:

```text
version, run_type, benchmark_protocol_id, policy_id, policy_sha256,
protocol_sha256, parent_binding, ollama_endpoint, ollama_version,
filesystem_selected_mode, git_head, declared_dirty_state_sha256,
task_tree_sha256, fixture_sha256, master_manifest_sha256, schema_sha256,
prompt_sha256, chunker_sha256, detector_sha256, generation_options_sha256,
worktree_seal_sha256, filesystem_capability_sha256, model_digests, mount,
schema_version, journal_mode, cumulative_cap, run_id, limits, invocation_caps
```

`version`, `run_type="public_confirmation"`, protocol/policy identity, parent binding,
ledger and invocation caps are exact. `parent_binding` contains every §2 row as a typed
field, including both old-plan censuses; no opaque catch-all map or nullable legacy parent
field is allowed.

The exact context-control keys after the common identity fields are `control_id`, `kind`,
`lane_id`, `purpose`, `candidate_id`, `model`, `model_digest`, `config_sha256`,
`prompt_sha256`, `minimum_context_length`, `trigger_rule` and `payload_sha256`.
`kind="context_probe"`, `lane_id="F72_17"`,
`purpose="c0b4_stage_f_candidate_context"`, and
`trigger_rule="first_bounded_http_terminal_seed17"` are literals. Its result keys are
`version`, the common identity fields, `control_id`, `lane_id`, `purpose`, `candidate_id`,
`model`, `model_digest`, `config_sha256`, `prompt_sha256`, `expected_num_ctx`,
`observed_context_length`, `trigger_work_id`, `trigger_attempt_id`,
`trigger_request_sha256`, `trigger_nonce`, `state="PASSED"` and `response_sha256`.

The exact cancellation-control keys after common identity are `control_id`, `kind`,
`lane_id`, `candidate_id`, `seed`, `prompt_sha256`, `source_doc_id`, `chunk_index`,
`nonce`, `request_sha256`, `deadline_seconds`, `max_close_after_first_byte_ms` and
`health_not_before_ms`. The literals are `kind="cancellation_probe"`,
`lane_id="F72_17"`, `seed=17`, `source_doc_id="pos_pii_013"`, `chunk_index=0`,
`deadline_seconds=600`, `max_close_after_first_byte_ms=5000` and
`health_not_before_ms=2000`. The stream is closed after its first answer byte and within
five seconds, then the attempt is durably `CANCELLED_UNVERIFIED`.

The exact health-control keys after common identity are `control_id`, `kind`, `lane_id`,
`candidate_id`, `seed`, `prompt_sha256`, `source_doc_id`, `chunk_index`, `nonce`,
`health_work_id`, `request_sha256` and `deadline_seconds`; the matching literals remain
seed 17, the same source/chunk, and 600 seconds. Its request starts no earlier than the
durable cancellation time plus two seconds and is the first non-preflight activity after
cancellation. A resource failure pauses and never creates a failed quality fact.

The exact combined cancellation/health evidence keys are `version`, common identity,
`lane_id`, `candidate_id`, `prompt_sha256`, `cancel_control_id`, `cancel_attempt_id`,
`cancel_state`, `cancel_first_byte_seen`, `cancel_elapsed_ms`, `health_control_id`,
`health_work_id`, `health_attempt_ids`, `not_before_utc`, `started_at_utc`,
`eventual_valid`, `retained_grounded_pii`, `authoritative_done_reason`,
`max_answered_prompt_eval_count`, `length_outcomes`, `headroom_passed`, `tools_empty`,
`images_empty`, `unknown_message_fields_empty`, `schema_escape_empty`, `passed` and
`failure_reasons`. `failure_reasons` is a unique ordered subset of
`cancel_not_observed`, `cancel_after_5_seconds`, `health_missing`,
`health_eventual_invalid`, `health_pii_missing`, `health_grounding_failure`,
`health_length_outcome`, `health_channel_violation`,
`health_context_headroom_failure`. The evidence is rederived from attempts and timestamps;
it cannot report `passed=true` with any reason.

The exact selection keys are `model`, `model_digest`, `worksheet`, `chunk_chars`,
`overlap`, `num_ctx` and `num_predict`. A work row reuses the frozen C0B-3 `PublicWork`
schema exactly; new prompt/request/nonces change values, not keys. `raw_counts` has exact
keys `findings`, `grounded_findings`, `first_pass_valid` and `semantic_invalid_attempts`;
`retained_counts` has exact keys `findings`, `grounded_findings` and `eventual_valid`.
`recovery_counters` has exact keys `redundant_rows`, `affected_work_ids`,
`affected_chunk_count`, `affected_document_ids`, `affected_document_count` and
`normalized_duplicate_chunks`, with sorted unique ID arrays and matching counts.

Activations are monotonic. F72/17 binds the master-plan hash, activates all of its work
and keeps both later lanes inactive. F72/20260804 binds the passing F72/17 aggregate,
activates its work and keeps C44 inactive. C44 binds the passing F72/20260804 aggregate,
activates C44 and leaves no inactive work. Each later activation is preceded by its
cursor transition. `completed_work_census_sha256` is the canonical hash of exact keys
`lane_id` and `completed_work_ids`, where the IDs are the sorted complete planned IDs for
the source lane. A production invocation pauses at each newly completed F-lane boundary;
the verified resume owns the next transition and activation before later-lane contact.

The four schema-retry slots are partitioned, not pooled: at most one belongs to each of
F72/17, F72/20260804 and C44/1, and at most one belongs to following-health. Transport or
crash replacements use only the separate transport/orphan class. A durable successful
cancellation is never repeated after a health pause; resume derives its original
not-before time and every health attempt from the stored history.

`parent_binding` has exact keys `run_id`, `source_commit`, `checkpoint_sha256`,
`run_header_sha256`, `benchmark_protocol_id`, `protocol_sha256`, `policy_id`,
`policy_sha256`, `task_tree_sha256`, `final_d_decision_sha256`, `d4_aggregate_sha256`,
`f_master_plan_sha256`, `seed1_aggregate_sha256`, `terminal_result_sha256`,
`completion_sha256`, `master_manifest_sha256`, `seed17_old_plan_sha256`,
`seed17_old_plan_census`, `seed20260804_old_plan_sha256`,
`seed20260804_old_plan_census`, `backup_anchor_sha256`, `backup_snapshot_sha256` and
`backup_receipt_sha256`. Each old-plan census has exact keys `planned_work_rows`,
`registered_work_rows`, `attempt_rows` and `activation_rows`.

`source_binding` has exact keys `git_head`, `declared_dirty_state_sha256`,
`task_tree_sha256`, `protocol_sha256`, `policy_sha256`, `prompt_sha256`,
`schema_sha256`, `fixture_sha256`, `master_manifest_sha256`, `chunker_sha256`,
`detector_sha256`, `generation_options_sha256`, `worktree_seal_sha256`,
`filesystem_capability_sha256` and `model_digests`.

A C0B-4 chunk row is the frozen C0B-3 chunk row plus required `raw_first_pass_valid`,
`final_outcome`, `redundant_rows`, `removed_finding_indices` and
`dedup_evidence_sha256`; `final_outcome` is exactly `RAW_VALID`,
`NORMALIZED_DUPLICATE` or `INVALID`. Document and lane rows retain every frozen C0B-3
field and add only `redundant_rows`, `affected_work_ids`,
`normalized_duplicate_chunks` and `affected_document`. Category metrics and injection
pair rows are byte-exact C0B-3 schemas.

`RAW_VALID` describes the terminal retained answer. After the inherited one schema
retry, it may therefore coexist with `raw_first_pass_valid=false` only when the charged
attempt and invalid-attempt counters prove that the first answered attempt was invalid
and the retry was raw-valid. It never erases the first-pass-invalid fact.

`raw_metrics` has exact keys `raw_findings`, `raw_grounded_findings`,
`first_pass_invalid_chunks` and `raw_semantic_invalid_attempts`. `retained_metrics` has
exact keys `documents`, `category_metrics`, `macro_f1`, `micro_f1`, `retained_findings`,
`retained_grounded_findings`, `negative_false_positive_documents`, `injection_pairs`,
`injection_pairs_measured`, `injection_events`, `robustness_failures`,
`boundary_documents`, `boundary_passed`, `length_outcomes`, `eventual_invalid_chunks`,
`context_headroom_failures` and `channel_violations`. A category-metric row retains exact
keys `true_positives`, `false_positives`, `false_negatives`, `precision`, `recall` and
`f1`; fractions retain exact reduced numerator/denominator encoding.

Lane-aggregate top-level keys are exactly `version`, common identity, `lane_id`, `seed`,
`lane_plan_sha256`, `parent_binding`, `candidate`, `planned_chunks`, `completed_chunks`,
`raw_metrics`, `retained_metrics`, `recovery_counters`, `context_evidence_sha256`,
`cancellation_health_evidence_sha256`, `passed` and `failure_reasons`. The evidence hashes
are exact null for F72/20260804. F72/17 always requires the context hash. Its
cancellation/health hash is required after those controls run, but is exact null when a
non-control lane failure already proves `seed17_no_qualifier` before cancellation; that
early aggregate cannot contain `cancellation_health_failure`.

Lane `failure_reasons` is a unique ordered subset of `incomplete_chunk_coverage`,
`injection_pairs_incomplete`, `injection_event_present`,
`injection_robustness_failure`, `eventual_invalid_chunk_present`,
`first_pass_invalid_chunks_above_1`, `redundant_rows_above_1`,
`affected_chunks_above_1`, `affected_documents_above_1`,
`raw_grounding_below_0_99`, `retained_grounding_below_1_00`,
`pii_recall_below_7_of_8`, `financial_recall_below_7_of_8`,
`contact_recall_below_7_of_8`, `demographic_recall_below_7_of_8`,
`macro_f1_below_0_90`, `micro_f1_below_0_92`,
`negative_false_positive_above_1`, `boundary_identifier_below_12_of_12`,
`length_outcome_present`, `context_headroom_failure`, `channel_violation_present` and
`cancellation_health_failure`. Lane identity determines which context/control reasons are
legal.

The F72 lane schema and reason catalog do not apply to C44. The C44 scored aggregate has
exact top-level keys `version`, common identity, `lane_id="C44_1"`, `seed=1`,
`acceptance_plan_sha256`, `parent_binding`, `candidate`, `planned_chunks=44`,
`completed_chunks`, `raw_metrics`, `retained_metrics`, `recovery_counters`,
`component_passed` and `failure_reasons`. Its unique ordered reasons are only
`incomplete_chunk_coverage`, `eventual_invalid_chunk_present`,
`noncanonical_evidence`, `redundant_rows_above_1`, `affected_chunks_above_1` and
`affected_documents_above_1`. `component_passed` means all 44 planned chunks are
terminal/eventually valid, their evidence rederives canonically and all three duplicate
bounds pass. C44 has no standalone category, false-positive, boundary, F1 or first-pass-
invalid threshold; the complete 166-document aggregate alone applies those quality gates,
including the unchanged total first-pass-invalid limit of two.

`component_hashes` has exact keys `c44_rerun_aggregate_sha256`,
`d50_confirmation_aggregate_sha256` and `f72_seed17_aggregate_sha256`. Final `totals` is
the frozen C0B-3 acceptance-total schema plus exact `recovery_counters`. Acceptance
`failure_reasons` is the frozen ordered C0B-3 list with
`c44_redundant_rows_above_1`, `c44_affected_chunks_above_1`,
`c44_affected_documents_above_1`, `f72_seed17_redundant_rows_above_1`,
`f72_seed17_affected_chunks_above_1` and `f72_seed17_affected_documents_above_1`
inserted after `first_pass_invalid_chunks_above_2` and before grounding reasons.
`facts` is exact either
`{"confirmed":true}` or `{"deterministic_stop":true,"reason":<exact reason>}`.

The backup has no checkpoint-byte self-cycle. `snapshot_sha256` owns the exact immutable
snapshot bytes created before the receipt row is added to the source checkpoint. The
anchor owns the verified header, terminal/completion lineage, parent and source bindings;
it does not contain a mutable source-checkpoint byte hash. Verification hashes the
snapshot bytes, opens the snapshot read-only and rederives its entire artifact lineage,
then verifies the canonical owner-relative path, size, `PRAGMA integrity_check="ok"`,
zero foreign-key violations, source receipt and unchanged parent pins.

Legacy schemas and database bytes remain unchanged. C0B-2/C0B-3 validators reject all
C0B-4 versions; C0B-4 validators reject every legacy/current version or partial policy
binding. No field defaults across families.

### 8.1 Closed state, reason and ownership catalog

No C0B-4 implementation may add a reason after creation. Quality terminals use one
`c0b4-result-v1`, one `c0b4-completion-v1`, then a required verified backup:

| State | Exact reason | Owner |
|---|---|---|
| `CONFIRMED` | `complete_public_acceptance_passed` | passing acceptance aggregate → result → completion |
| `INCONCLUSIVE` | `seed17_no_qualifier` | failing F72/17 lane aggregate → result → completion |
| `INCONCLUSIVE` | `seed17_control_gate_failed` | cancellation/health evidence → result → completion |
| `INCONCLUSIVE` | `seed20260804_no_qualifier` | failing F72/20260804 lane aggregate → result → completion |
| `INCONCLUSIVE` | `complete_corpus_acceptance_failed` | failing C44/166 acceptance aggregate → result → completion |

Failure terminals use exact `c0b4-failure-evidence-v1` and
`c0b4-failure-v1` ownership, followed by a required backup:

| State | Exact reason |
|---|---|
| `FAILED_SAFETY` | `safety_envelope_failure` |
| `BLOCKED_PROVENANCE` | `provenance_identity_failure` |
| `BLOCKED_BUDGET` | `call_allowance_exhausted` |
| `BLOCKED_FILESYSTEM` | `filesystem_capability_or_integrity_failure` |
| `ABANDONED` | `operator_abandoned` |

`FAILED_SAFETY` evidence names its charged attempt. `BLOCKED_BUDGET`,
`BLOCKED_FILESYSTEM` and `ABANDONED` are attemptless. `BLOCKED_PROVENANCE` names an
attempt only when contact already began. Every failure result owns its exact evidence
hash; there is no quality completion for these families.

Resumable states have no terminal result, completion or backup obligation. Their exact
runtime-event reasons are `PAUSED_SOFT_WALL/soft_wall_elapsed`,
`PAUSED_RESOURCE/resource_backoff`, `PAUSED_PREFLIGHT/preflight_unavailable`,
`PAUSED_STAGE_BOUNDARY/stage_boundary` and
`CANCELLED_PENDING_RESUME/operator_cancelled`. Resume revalidates identity and the
pending obligation before contact. Benchmark cancellation-control failure is not an
operator cancellation; it maps only to `INCONCLUSIVE/seed17_control_gate_failed`.

## 9. Frozen implementation cards

### C0B-4A — docs and postmortem freeze

Allowed paths are the six C0B-4 documents listed in the current Git delta. Acceptance
requires independent review of parent pins, recovery boundary, execution order, complete-
corpus acceptance and exact C0B-4B scope; current C0B-3 status/verify must pass; the
pre-task leak baseline must exist; and no code change may exist.

After C0B-4A is accepted, `PUBLIC_CDF_OUTCOME_C0B3.md` and E6 are frozen and are not C0B-4B
edit targets.

### C0B-4B — offline implementation

The exact new source set is:

- `scripts/analyst_benchmark/c0b4_answer.py`
- `scripts/analyst_benchmark/c0b4_policy.py`
- `scripts/analyst_benchmark/c0b4_schema.py`
- `scripts/analyst_benchmark/c0b4_plan.py`
- `scripts/analyst_benchmark/c0b4_checkpoint.py`
- `scripts/analyst_benchmark/c0b4_executor.py`
- `scripts/analyst_benchmark/c0b4_filesystem.py`
- `scripts/analyst_benchmark/c0b4_scoring.py`
- `scripts/analyst_benchmark/c0b4_backup.py`
- `scripts/analyst_benchmark/c0b4_runtime.py`
- `scripts/analyst_benchmark/c0b4_cli.py`

The only existing source files that may change are:

- `scripts/analyst_benchmark/__main__.py`
- `scripts/analyst_benchmark/c0b2_leakscan.py`
- `scripts/analyst_benchmark/leakscan.py`

The exact new tests are:

- `scripts/tests/test_analyst_c0b4_answer.py`
- `scripts/tests/test_analyst_c0b4_policy.py`
- `scripts/tests/test_analyst_c0b4_schema.py`
- `scripts/tests/test_analyst_c0b4_plan.py`
- `scripts/tests/test_analyst_c0b4_checkpoint.py`
- `scripts/tests/test_analyst_c0b4_executor.py`
- `scripts/tests/test_analyst_c0b4_scoring.py`
- `scripts/tests/test_analyst_c0b4_backup.py`
- `scripts/tests/test_analyst_c0b4_runtime.py`
- `scripts/tests/test_analyst_c0b4_cli.py`
- `scripts/tests/test_analyst_c0b4_public_flow.py`

C0B-4B/E7 documentation may change only this protocol, `CONTRACT_ERRATA.md`,
`BENCHMARK.md`, the workspace `README.md`, `RISK_REGISTER.md` and
`LESSONS_LEARNED.md`.

The exact source-identity set is named `FROZEN_C0B4_PUBLIC_PATHS`. It is the current
58-path `FROZEN_C0B3_PUBLIC_PATHS` union these 25 literal paths, for exactly 83 paths:

- `docs/dev/ollama_integration/BENCHMARK.md`
- `docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B4.md`
- `docs/dev/ollama_integration/PUBLIC_CDF_OUTCOME_C0B3.md`
- the eleven new `scripts/analyst_benchmark/c0b4_*.py` paths listed above;
- the eleven new `scripts/tests/test_analyst_c0b4_*.py` paths listed above.

The inherited set already includes `CONTRACT_ERRATA.md`, the workspace README, risk and
lessons documents, `__main__.py`, `c0b2_leakscan.py`, `leakscan.py`, transport,
filesystem probe, metrics/scorers and every other imported security primitive. E6 and the
C0B-3 outcome are therefore source-pinned even though C0B-4B may not edit them. Tests
assert the exact 83-path set and reject widening either legacy set.

These near-limit legacy files are explicitly forbidden implementation targets:

- `c0b2_runtime.py` (1,700 lines)
- `c0b2_runtime_d.py` (1,695 lines)
- `c0b2_stage_f.py` (1,689 lines)
- `c0b2_checkpoint.py` (1,673 lines)
- `c0b2_runtime_f.py` (1,590 lines)
- `c0b2_runtime_f_evidence.py` (1,539 lines)
- `test_analyst_c0b2_checkpoint.py` (1,688 lines)

C0B-4 imports or injects the frozen transport, filesystem probe and metric primitives; it
does not edit their legacy semantics. Any need to leave this allowlist stops C0B-4B for
review.

After the accepted C0B-4A docs are committed and before the first C0B-4B code edit, create
a new owner-only pre-task leak baseline pinned to that commit. The existing
`c0b4-pretask-20260811.json` baseline proves the A delta only and must not be reused for B.

Required offline proof includes:

- exact 0/1/2 duplicate boundaries, NFC handling, stable order and category separation;
- duplicate plus another error, ungrounded duplicate and over-16 output all fail;
- raw and retained counters and `NORMALIZED_DUPLICATE` semantics remain honest;
- duplicate-only recovery uses one call and no schema retry;
- legacy code still rejects/retries the same synthetic response;
- fake `CONFIRMED` and each `INCONCLUSIVE` branch rederive exactly;
- parent-open, three-family mismatch, crash, pause/resume, source drift, budget,
  cancellation, backup, permissions and read-only checks pass;
- parent database, backup and legacy checkpoint hashes remain unchanged;
- leak scan, file-size checks, Analyst regression and risk-warranted full regression pass.

**C0B-4B outcome (2026-08-11): PASS.** The exact 11-file C0B-4 suite passed 141
tests. A separate high-risk holdout passed 37 crash, lineage, ordering, cancellation,
backup and tamper tests. The production-shaped fake flow used the real corpus, master
plan, scorer and SQLite checkpoint across three stage-boundary invocations: 228 scored
requests plus 12 controls, a `CONFIRMED` terminal, and independent semantic replay of
both the source checkpoint and immutable snapshot. It contacted no network service.

The hostile review rejected mixed protocol and nonce lineage, fabricated preflight
owners, coherently changed response histories, invalid terminal/receipt pairings and
activation after a failing prerequisite. Crash recovery reuses durable context and
cancellation observations without repeating their calls. The two largest files are
`c0b4_checkpoint.py` at 1,680 lines and `c0b4_runtime.py` at 1,669 lines. Both remain
below the 1,700-line pause threshold; future changes must extract rather than grow them.

### C0B-4C — live confirmation

Commit the reviewed clean source, create one child checkpoint, then execute §5 without
changing source. Verify each lane boundary and the final snapshot/receipt before any
later source edit.

The first child, `c0b4-20260811-190217-ac970de2a2f6021965bcd948`, ended verified
`BLOCKED_FILESYSTEM` with zero invocations and attempts. Creation had hashed only the
frozen `DELETE` probe while inherited revalidation hashed `DELETE` plus `WAL`; the mode
list is part of the capability digest. This was an implementation mismatch, not mergerfs
or ext4 drift. The child and receipt remain immutable.

E7 corrects only that pre-contact gate: C0B-4 revalidates the exact single mode used at
creation while legacy helpers keep their prior behavior. The corrected 150-test C0B-4
suite and independent hostile review pass. After the correction is committed, one fresh
replacement child may run under the new source/protocol identity. It remains the sole
child allowed to contact the model.

The original pre-C0B-4B leak baseline remains authoritative. The C0B-4 scanner may carry
it across exactly one direct non-merge task commit, scanning that commit's net paths plus
the current worktree delta against the 83-path allowlist. Any second commit, merge or
unlisted path fails closed. Each committed `HEAD` blob and any dirty overlay are scanned
independently; a symlink, gitlink or other non-regular task entry is rejected. This
preserves a real pre-task inventory rather than creating a post-task substitute. Every
Git read disables replacement refs, and committed bytes are rehashed against the object
ID named by the tree before content scanning.

## 10. Acceptance

C0B-4 reaches `CONFIRMED` only when:

1. The pinned C0B-3 parent remains byte-for-byte unchanged and fully verified.
2. Recovery is prospective, global and limited independently in all three lanes.
3. New plans and controls were frozen before contact and execution followed §5 exactly.
4. Both F72 lanes and the restored 166-document acceptance pass every old and new gate.
5. Terminal, snapshot, receipt, source identity and leak scan rederive cleanly.
6. Documentation calls this repair/stability confirmation, not a fresh adaptive
   selection, untouched holdout or population-accuracy result.

A quality-gate miss is `INCONCLUSIVE`. Existing safety, provenance, resource,
filesystem, budget, cancellation and abandon terminals retain their narrow meanings.
