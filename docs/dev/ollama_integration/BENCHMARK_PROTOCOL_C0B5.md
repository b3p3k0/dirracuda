# Analyst Benchmark — C0B-5 Assistive Review-Budget Confirmation

Benchmark protocol ID: `c0b5-assistive-fp-confirmation-v1`
Policy ID: `c0b5-assistive-bounded-fp-v1`
Policy SHA-256: `0af37d83b05e03e4cd336719587d6e98d49aeaec943edeadce2e3df35651b1f7`
Date: 2026-08-11
Status: **C0B-5A frozen after three independent reviews; no C0B-5 model request
authorized until C0B-5B passes its complete offline gates.**

## 1. Authority and scope

This protocol is prospective. It inherits the complete reviewed C0B-4 contract except
for the exact changes named here. Authority, in order:

1. [`CONTRACT.md`](CONTRACT.md) plus accepted errata E1–E8;
2. [`BENCHMARK_PROTOCOL_C0B4.md`](BENCHMARK_PROTOCOL_C0B4.md), frozen as history;
3. [`PUBLIC_CDF_OUTCOME_C0B4.md`](PUBLIC_CDF_OUTCOME_C0B4.md), descriptive parent;
4. this protocol for C0B-5-only identities, limits, seeds and card gates.

C0B-5 does not edit, resume or rescore C0B-2, C0B-3 or C0B-4. It binds the verified C0B-4
terminal and the original C0B-3 D4 parent, but imports neither result as a passing
selection. It asks only whether the exact finalist is acceptable under the HI-approved,
explicitly bounded human-review workload.

This is operational-policy confirmation on an observed synthetic corpus. It is not a
fresh holdout, population-accuracy estimate or “90% accurate” claim.

This framing follows NIST's current AI RMF guidance to document context-specific
test/evaluation/verification/validation measures, human-AI oversight and explicit go/no-go
decisions rather than presenting one benchmark as universal accuracy: see the
[AI RMF Core](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/) and
[Generative AI Profile](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence).

## 2. Frozen policy

The exact canonical JSON policy preimage is:

```json
{"false_positive_review_budget":{"final":{"max_affected_negative_documents":4,"max_retained_findings_on_negatives":4,"negative_documents":40},"per_f_lane":{"max_affected_negative_documents":2,"max_retained_findings_on_negatives":2,"negative_documents":16}},"inherits":{"duplicate_policy_id":"c0b4-bounded-grounded-dedup-v1","duplicate_policy_sha256":"7475e748165366ed0fb83daf1b6dae21a98d92d1c8faf3e3b7a3513aa3745c43"},"policy_id":"c0b5-assistive-bounded-fp-v1","units":{"affected_negative_document":"document","retained_model_suggestion":"row"}}
```

SHA-256 over those exact UTF-8 bytes is
`0af37d83b05e03e4cd336719587d6e98d49aeaec943edeadce2e3df35651b1f7`.

Two limits apply independently:

| Gate | Negative documents | Maximum affected documents | Maximum retained findings |
|---|---:|---:|---:|
| Each F lane | 16 | 2 | 2 |
| Final public aggregate | 40 | 4 | 4 |

The final document limit is a 10% review budget on this curated negative set. It does not
estimate production prevalence or precision. The finding-row limit makes the stated
human workload bound real: an affected document cannot hide extra suggestions. Neither
limit compensates for the other or for another failed metric.

For each negative document, retained rows are the inherited normalized union of
`(category, quote, absolute_source_offset)` across its chunks. This removes overlap copies
without merging distinct source occurrences. `negative_retained_findings` is the sum of
those union sizes for documents whose expected-category set is empty;
`negative_false_positive_documents` counts those documents with a non-empty union. The
invariant is `negative_false_positive_documents <= negative_retained_findings`.

An F lane fails with exact reason `negative_false_positive_above_2` when its document
count exceeds two and independently with `negative_retained_findings_above_2` when its
row count exceeds two. The final aggregate equivalents are
`negative_false_positive_above_4` and `negative_retained_findings_above_4`.

Stage C and D remain frozen at the C0B-3 E5 limit of one affected document among 12;
they are not rerun. Every grounding, recall/F1, duplicate, injection, schema, boundary,
length, context, cancellation, filesystem, provenance, privacy, budget, backup and
no-action rule remains unchanged.

## 3. Candidate and prospective requests

The candidate remains byte-exact:

- model: `qwen3.6:27b`;
- digest: `a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e`;
- worksheet: v2;
- `chunk_chars=8000`, `overlap=256`, `num_ctx=8192`, `num_predict=1024`;
- the exact C0B-4 prompt and bounded grounded-duplicate normalization.

The observed C0B-4 seed 17 is descriptive only. C0B-5 freezes these never-contacted
generation conditions before any request:

1. `F72_20260804` — seed 20260804; context and cancellation/health controls assigned;
2. `F72_20260811` — seed 20260811; stability confirmation;
3. `C44_1` — seed 1; restored complete-corpus component.

F72/20260804 must pass before F72/20260811 activates. Both must pass before C44 activates.
The final 166-document aggregate combines fresh C44/1, immutable C0B-3 D50/D4 and fresh
F72/20260804 evidence. F72/20260811 is an independent stability gate and is not counted
twice in the final 40-negative census.

The first F lane alone owns context evidence, which must prove allocation of at least
8192 tokens, and the cancellation/following-health control. Both hashes in the second F
lane are exactly null. Cancellation and health requests therefore run only at seed
20260804 and cannot be deferred to or repeated at seed 20260811.

## 4. Exact parent binding

Every C0B-5 header contains exact top-level `parent_binding` keys `execution_parent` and
`observed_c0b4`. `execution_parent` is the following complete C0B-3 binding already
frozen and independently verified by C0B-4:

```json
{
  "run_id": "c0b3-20260809-154924-19afcaab26984160f20ec075",
  "source_commit": "dcd7e0b9504ded47dad82f25814aea54d666b268",
  "checkpoint_sha256": "f8cbd0419f62656476b38c60b628b1ce20f67b097d2ce7e8bc38381d80d852e3",
  "run_header_sha256": "80424fbfb492cae4264798d6294337c3beaca21f2172da302114adf05d8210b2",
  "benchmark_protocol_id": "c0b3-assistive-confirmation-v1",
  "protocol_sha256": "031b41f6cf0f153b94c47dc55907eae77fd6600379c009434dbc752deb33022d",
  "policy_id": "c0b3-assistive-bounded-fp-v1",
  "policy_sha256": "4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235",
  "task_tree_sha256": "a936817083810cebc4f017d34f4d0be5e0821c1c1e0c9ffff218df69b9782bb0",
  "final_d_decision_sha256": "5c00ef2b06c014f7617bdb367034dc7be99fd462467961c7a15d3eac5b53d894",
  "d4_aggregate_sha256": "7cf23921758c6be35038456e7f4e568cef4f20618bf8ce9a9dddac5af7bab945",
  "f_master_plan_sha256": "093af02da48d938278e791955dc196ec1c8e0dacb434ddbe204186f2fbb963de",
  "seed1_aggregate_sha256": "cd87e163b2ac08b9f4de9f90291247411e80830a23a9bf635f8e6e2ba9eb11e1",
  "terminal_result_sha256": "ee2c8ed8c923deba3fb30eec3dcf5af87da69de9678bf6f45303e5ffeb1d9bcc",
  "completion_sha256": "6958b94d19d2a404003fba3e2d628a6828810cd503e8ced5bfc76f4f4ead5c00",
  "master_manifest_sha256": "df609a7c5c0baaf3215bb74ef8a3598c5f8ad5b75a16caad41cf3cd1523d5e12",
  "seed17_old_plan_sha256": "2175e51108362a273f13292b95fafd724cfc90b6817b15197c93fe2055d41f31",
  "seed17_old_plan_census": {"planned_work_rows": 92, "registered_work_rows": 0, "attempt_rows": 0, "activation_rows": 0},
  "seed20260804_old_plan_sha256": "0a8e56835af83659ae6274772401da742feefb6e4d4121ed7c995cafbe9dcb21",
  "seed20260804_old_plan_census": {"planned_work_rows": 92, "registered_work_rows": 0, "attempt_rows": 0, "activation_rows": 0},
  "backup_anchor_sha256": "b37396143265013ed01361d7ec31edff3d84c358d2f6c8ce932df39b21e61c56",
  "backup_snapshot_sha256": "262498adb36c12ef44fdeb779283e17305378cfcac33b4e87c740017453a799c",
  "backup_receipt_sha256": "398755d38227c30c527c787c3205407ed0ba47f18ccfab4b865584fb74ec14f9"
}
```

`observed_c0b4` is this exact object:

```json
{
  "run_id": "c0b4-20260811-210848-d2b52272f3aabb156f55d166",
  "source_commit": "377e4eb9e277d24d9ef1699d3a427253c052df75",
  "checkpoint_sha256": "c6d3e8e8dfeba129911ab034bb8301f028722227bf6c3e1d3817b1fa461d4285",
  "run_header_sha256": "301719b3a4d570bb87017f01bfb27d16db2d66c652ed251c56e71c423b2e7f0b",
  "benchmark_protocol_id": "c0b4-grounded-duplicate-confirmation-v1",
  "protocol_sha256": "71bde3bdd02f338216aa9a964a21207db3d1d4c80f0e676dab04776f7f833ae0",
  "policy_id": "c0b4-bounded-grounded-dedup-v1",
  "policy_sha256": "7475e748165366ed0fb83daf1b6dae21a98d92d1c8faf3e3b7a3513aa3745c43",
  "task_tree_sha256": "2e6c04acee48ce4b01f591239568b260b7dc6d5f4273c579c083513852f459fe",
  "master_plan_sha256": "7faea74d2d2d856658a3854af04576c83ba3f1cacb1fbbe939ad87db58e11832",
  "lane_plan_sha256s": {
    "F72_17": "945298c296a86dde850e2e8253aaebe1c99ee86886a8a93bf794261212929cd5",
    "F72_20260804": "ed9e9ac2ac9937a5460b9a6be63ea017a2a53d7a6630772d141910f2c3250169",
    "C44_1": "3333d49c849fb36eea7695be5338664cda60b9843d47648e279fd3bd191f6f6f"
  },
  "inactive_lane_census": {
    "F72_20260804": {"planned_work_rows": 92, "activation_rows": 0, "attempt_rows": 0, "aggregate_rows": 0},
    "C44_1": {"planned_work_rows": 44, "activation_rows": 0, "attempt_rows": 0, "aggregate_rows": 0}
  },
  "f72_seed17_aggregate_sha256": "4b86e1fc4a3e9ccf198247da8782a9be688c606f4a8a2dce7fd7b0a5c717215e",
  "terminal_result_sha256": "7c9a387e2b3b17bb028eb3c98156a54059ce23d316174b6ec81030ed0ac73497",
  "completion_sha256": "5b2144227b15a89e17a1ec235976cee4a26e193b5dee31c5b91d09ab7f0e051c",
  "backup_anchor_sha256": "60ac16a8962a5b87b16cc5bf7beeaae3d8009cf4d26a5441656ef125d2602358",
  "backup_snapshot_sha256": "f31a38d269a13c6df8b9e264f8d149d161504e3e3cdcae1ea0f1fd2a253fe94b",
  "backup_receipt_sha256": "e758b8f1bbe8f1a2d2c4edf048b64ff7f8be82392c26df18427e6a3e87546c75"
}
```

Before creating a C0B-5 child path, mutating any C0B-5 state or contacting Ollama, the
implementation must descriptor-safely open both parents read-only. `c0b5_lineage.py`
owns safe opening, hash/literal comparison and the inactive-lane census;
`c0b5_replay.py` independently rederives C0B-4 lineage and terminal facts from its plan,
activation, attempt, aggregate, result, completion and receipt records. That replay may
not import a C0B-4 module outside the exact §5 import table. The frozen C0B-3 read-only
verifier may be called from its inherited 58-path surface. Both paths must compare every
literal above before any child mutation or contact. The source databases and their
backups remain hash-pinned and unchanged after every C0B-5 command.

## 5. Protocol and source identity

`protocol_sha256` is SHA-256 over canonical JSON encoded with Python `json.dumps` using
`sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False` and `allow_nan=False`.
The exact preimage template is below. Each placeholder is replaced with the lowercase
SHA-256 of the verified exact file bytes at the named path beneath
`docs/dev/ollama_integration/`:

```json
{
  "benchmark_protocol_id": "c0b5-assistive-fp-confirmation-v1",
  "components": {
    "c0b2_protocol_sha256": "<BENCHMARK_PROTOCOL_C0B2.md>",
    "c0b2_public_schema_doc_sha256": "<BENCHMARK_PUBLIC_CDF_SCHEMA.md>",
    "c0b3_protocol_sha256": "<BENCHMARK_PROTOCOL_C0B3.md>",
    "c0b4_protocol_sha256": "<BENCHMARK_PROTOCOL_C0B4.md>",
    "c0b5_protocol_sha256": "<BENCHMARK_PROTOCOL_C0B5.md>",
    "c0b3_outcome_sha256": "<PUBLIC_CDF_OUTCOME_C0B3.md>",
    "c0b4_outcome_sha256": "<PUBLIC_CDF_OUTCOME_C0B4.md>",
    "contract_errata_sha256": "<CONTRACT_ERRATA.md>"
  }
}
```

The source task tree is the exact 58-path frozen C0B-3 set plus an exact 29-path delta,
for 87 paths total.

The canonical allowlist digest is SHA-256 of the UTF-8 JSON array produced from the
sorted path strings with `separators=(",", ":")`. Historical meanings remain frozen:

| Set | Paths | Canonical allowlist SHA-256 |
|---|---:|---|
| C0B-2 | 48 | `15af54bc43fe4f0abcbe0d1cfeb507265cc7d857a585e19fc169e9d21870a651` |
| C0B-3 | 58 | `5999e19c566168e8a2235a4773e26e16cd957bebeee75a82e6bfde2d2bb424ec` |
| C0B-4 | 83 | `31822035daab54a84a163d20c0e35b8ee67eae31d5c1aa6a551ea3b78407e438` |
| C0B-5 | 87 | `20990717679793872957300eaba1b4a203ebe43078f8ef2971e25e86eecc91e0` |

The exact 29-path delta is:

```text
docs/dev/ollama_integration/BENCHMARK.md
docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B4.md
docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B5.md
docs/dev/ollama_integration/PUBLIC_CDF_OUTCOME_C0B3.md
docs/dev/ollama_integration/PUBLIC_CDF_OUTCOME_C0B4.md
scripts/analyst_benchmark/c0b4_answer.py
scripts/analyst_benchmark/c0b4_executor.py
scripts/analyst_benchmark/c0b4_filesystem.py
scripts/analyst_benchmark/c0b5_backup.py
scripts/analyst_benchmark/c0b5_checkpoint.py
scripts/analyst_benchmark/c0b5_cli.py
scripts/analyst_benchmark/c0b5_executor.py
scripts/analyst_benchmark/c0b5_lineage.py
scripts/analyst_benchmark/c0b5_plan.py
scripts/analyst_benchmark/c0b5_policy.py
scripts/analyst_benchmark/c0b5_replay.py
scripts/analyst_benchmark/c0b5_runtime.py
scripts/analyst_benchmark/c0b5_schema.py
scripts/analyst_benchmark/c0b5_scoring.py
scripts/tests/test_analyst_c0b5_backup.py
scripts/tests/test_analyst_c0b5_checkpoint.py
scripts/tests/test_analyst_c0b5_cli.py
scripts/tests/test_analyst_c0b5_executor.py
scripts/tests/test_analyst_c0b5_plan.py
scripts/tests/test_analyst_c0b5_policy.py
scripts/tests/test_analyst_c0b5_public_flow.py
scripts/tests/test_analyst_c0b5_runtime.py
scripts/tests/test_analyst_c0b5_schema.py
scripts/tests/test_analyst_c0b5_scoring.py
```

`BENCHMARK_PROTOCOL_C0B4.md` and `PUBLIC_CDF_OUTCOME_C0B3.md` are read-only.
`c0b4_answer.py`, `c0b4_executor.py` and `c0b4_filesystem.py` are import-only and
read-only. Every other C0B-4 implementation/test path is forbidden. The existing living
docs and leak/CLI routing files in the inherited 58-path set may change only as required
to record or route C0B-5. No C0B-2/C0B-3 behavior or historical allowlist may change.

The import-only surface is closed:

| Module | Symbols C0B-5 may import |
|---|---|
| `c0b4_answer.py` | `AnswerAssessment`, `build_prompt`, `prompt_template_hash`, `assess_answer` |
| `c0b4_executor.py` | `ScoredWork`, `AttemptFinish`, `ScoredExecutionResult`, `execute_scored` |
| `c0b4_filesystem.py` | `revalidate_frozen_filesystem` |

In particular, C0B-5 may not import or call C0B-4 `_runtime_event`,
`reconcile_runtime_events`, `persist_scored_finish` or any other event, reconciliation,
persistence or preflight-identity helper. C0B-5 owns those operations under its own
artifact family. Private/underscored functions not named in the table remain
implementation details even when an allowed public symbol calls them internally.

After C0B-5A commits, `BENCHMARK_PROTOCOL_C0B5.md`,
`PUBLIC_CDF_OUTCOME_C0B4.md`, `CONTRACT_ERRATA.md`, the two leak modules, the provenance
test, and every C0B-4/C0B-3 historical document become read-only C0B-5B inputs. C0B-5B
may modify only these inherited paths:

```text
docs/dev/ollama_integration/BENCHMARK.md
docs/dev/ollama_integration/LESSONS_LEARNED.md
docs/dev/ollama_integration/README.md
docs/dev/ollama_integration/RISK_REGISTER.md
scripts/analyst_benchmark/__main__.py
```

It may also create the 11 C0B-5 modules and ten C0B-5 tests already enumerated in the
29-path delta. Every other inherited path is read-only during B.

C0B-5B requires a fresh owner-only baseline created immediately after the reviewed
C0B-5A commit and before any B edit. The C0B-5A baseline cannot be reused. That baseline
may cross exactly one direct, non-merge C0B-5B commit. The final scan independently reads
every immutable task-delta blob from that commit and the dirty overlay. A second commit,
merge, Git replacement object, unsafe entry or unlisted path fails closed. The 87-path
set may not be widened or edited without a new reviewed protocol revision.

## 6. Frozen artifact and report delta

C0B-5 uses a closed `c0b5-*-v1` family. Every C0B-4 artifact is mirrored with the C0B-5
prefix and the same exact keys and semantics except for the changes frozen here. Mixed
C0B-2/C0B-3/C0B-4/C0B-5 artifacts fail closed.

- Lane IDs are exactly `F72_20260804`, `F72_20260811` and `C44_1`.
- `parent_binding` is the exact two-key wrapper in §4.
- `retained_metrics` in each lane and C44, and `totals` in the final aggregate, add
  required integer `negative_retained_findings`.
- Each F lane records required `negative_false_positive_documents` and
  `negative_retained_findings` and enforces both independent §2 reasons.
- The result's lane-hash map has exact keys `f72_seed20260804_sha256`,
  `f72_seed20260811_sha256` and `c44_scored_sha256`.
- The final aggregate's `component_hashes` has exact keys
  `c44_rerun_aggregate_sha256`, `d50_confirmation_aggregate_sha256` and
  `f72_seed20260804_aggregate_sha256`.
- Context and cancellation/health evidence follow the exact ownership/null rules in §3.

The changed control literals are exact:

| Artifact fields | C0B-5 literal |
|---|---|
| Context `version` | `c0b5-context-control-v1` |
| Context `kind` / `lane_id` | `context_probe` / `F72_20260804` |
| Context `purpose` | `c0b5_stage_f_candidate_context` |
| Context `minimum_context_length` | `8192` |
| Context `trigger_rule` | `first_bounded_http_terminal_seed20260804` |
| Context evidence `version` / `lane_id` / `purpose` | `c0b5-context-evidence-v1` / `F72_20260804` / `c0b5_stage_f_candidate_context` |
| Context evidence `expected_num_ctx` | `8192` |
| Cancellation `version` / `kind` / `lane_id` / `seed` | `c0b5-cancellation-control-v1` / `cancellation_probe` / `F72_20260804` / `20260804` |
| Cancellation source / chunk | `pos_pii_013` / `0` |
| Cancellation deadline / close / health wait | `600` seconds / `5000` ms / `2000` ms |
| Health `version` / `kind` / `lane_id` / `seed` | `c0b5-health-control-v1` / `cancellation_health` / `F72_20260804` / `20260804` |
| Health source / chunk / deadline | `pos_pii_013` / `0` / `600` seconds |
| Cancellation-health evidence `version` / `lane_id` | `c0b5-cancellation-health-evidence-v1` / `F72_20260804` |

All other control keys and semantics are byte-exact C0B-4. Control request generation
uses seed 20260804 and new `c0b5-*` nonce/control/work domains; it never reuses a C0B-4
control ID, request, nonce or runtime event.

Ordered F-lane failure reasons are exactly:

```text
incomplete_chunk_coverage
injection_pairs_incomplete
injection_event_present
injection_robustness_failure
eventual_invalid_chunk_present
first_pass_invalid_chunks_above_1
redundant_rows_above_1
affected_chunks_above_1
affected_documents_above_1
raw_grounding_below_0_99
retained_grounding_below_1_00
pii_recall_below_7_of_8
financial_recall_below_7_of_8
contact_recall_below_7_of_8
demographic_recall_below_7_of_8
macro_f1_below_0_90
micro_f1_below_0_92
negative_false_positive_above_2
negative_retained_findings_above_2
boundary_identifier_below_12_of_12
length_outcome_present
context_headroom_failure
channel_violation_present
cancellation_health_failure
```

C44 failure reasons remain the exact C0B-4 ordered vocabulary. Ordered final-aggregate
failure reasons are exactly:

```text
incomplete_166_coverage
first_pass_invalid_chunks_above_2
c44_redundant_rows_above_1
c44_affected_chunks_above_1
c44_affected_documents_above_1
f72_seed20260804_redundant_rows_above_1
f72_seed20260804_affected_chunks_above_1
f72_seed20260804_affected_documents_above_1
eventual_invalid_chunk_present
raw_grounding_below_0_99
retained_grounding_below_1_00
pii_recall_below_18_of_20
financial_recall_below_18_of_20
contact_recall_below_18_of_20
demographic_recall_below_18_of_20
negative_false_positive_above_4
negative_retained_findings_above_4
injection_pairs_incomplete
injection_event_present
injection_robustness_failure
boundary_identifier_below_24_of_24
truncation_below_6_of_6
length_outcome_present
context_gate_failure
channel_violation_present
cancellation_health_failure
component_gate_failure
```

Failure lists are unique ordered subsets of these tuples. The document-limit reason
always precedes the retained-row-limit reason when both apply. C0B-4's
`f72_seed17_*` final reasons are invalid in C0B-5; the three exact replacements above
bind the qualifying seed-20260804 component.

Quality terminal/reason pairs are closed:

| Terminal | Exact reason |
|---|---|
| `CONFIRMED` | `complete_public_acceptance_passed` |
| `INCONCLUSIVE` | `seed20260804_no_qualifier` |
| `INCONCLUSIVE` | `seed20260804_control_gate_failed` |
| `INCONCLUSIVE` | `seed20260811_no_qualifier` |
| `INCONCLUSIVE` | `complete_corpus_acceptance_failed` |

Failure terminals, failure reasons, resumable states and pause reasons remain byte-exact
from C0B-4; only their artifact versions use `c0b5-*-v1`. Completion facts use the same
closed reasons above. No old quality reason is valid under a C0B-5 header.

The public result report is a derived-only view named `c0b5-public-summary-v1`; it is not
a durable checkpoint artifact and cannot be supplied as verification evidence. Read-only
`status`/`verify` regenerates it only after complete lineage replay from a terminal live
checkpoint or its verified immutable snapshot. Its exact source chain is the stored
`c0b5-result-v1`, its `c0b5-completion-v1`, every lane aggregate referenced by the
result, the referenced final acceptance aggregate when present, and the underlying
canonical attempt/document evidence. Backup verification replays that chain before the
view may be emitted.

The derived view contains no raw model text and has exact top-level keys `version`,
`run_id`, `policy_id`, `policy_sha256`, `protocol_sha256`, `terminal`, `reason`,
`result_sha256`, `completion_sha256`, `lane_aggregate_sha256s`,
`acceptance_aggregate_sha256`, `false_positive_documents`,
`fresh_f_union_document_ids`, `fresh_f_intersection_document_ids`, `component_counts`,
`total_human_rejection_rows` and `summary_sha256`. `summary_sha256` is the canonical
SHA-256 after removing only itself.

A `false_positive_documents` row has exact keys `component`, `document_id`,
`categories`, `public_template_family` and `negative_retained_findings`. `component` is
exactly `C44_RERUN`, `D50_CONFIRMATION`, `F72_SEED20260804` or `F72_SEED20260811`.
`categories` is the sorted unique category list from that document's normalized retained
union, never a singular or free-form value. Rows are sorted by `(component, document_id)`.
Each `component_counts` value has exact keys `negative_false_positive_documents` and
`negative_retained_findings` or is exactly null when that component never activated; the
map always has the four component keys just listed. Counts must sum exactly to the
associated lane/final totals. The fresh-seed union and intersection are both null until
both F aggregates exist; afterward they are sorted unique document-ID lists derived only
from those two components. `total_human_rejection_rows` equals the applicable final
aggregate's `negative_retained_findings`, or null before a final aggregate exists.

Public template family is derived only from a negative document's frozen ID. The exact
canonical mapping preimage is:

```json
{"doc_id_rules":{"neg_clean_":{"0":"clean_sprint_retrospective","1":"clean_boiler_maintenance_log","2":"clean_library_acquisition_notes","3":"clean_cafeteria_menu_cycle","4":"clean_parking_structure_survey"},"neg_nearmiss_":{"0":"near_miss_checksum_failed_barcode","1":"near_miss_ssn_shaped_part_number","2":"near_miss_phone_shaped_chassis_serial","3":"near_miss_invalid_routing_cost_centre","4":"near_miss_invalid_iban_template_placeholder"}},"numeric_suffix_range":[1,20]}
```

Its SHA-256 is
`9f47d270d66e904135f76927a66d4c5eb69b15626bd5a2d5d58f2c2053670169`.
The decimal three-digit suffix must be in 1–20; its integer value modulo five selects
the exact enum. Any other prefix, suffix or label fails closed. Implementations must
rederive this mapping against all 40 frozen negative manifest rows before child creation.

A different set of misses at each seed is not hidden by the numeric cap; it is surfaced
for HI review. Production C1 must later monitor affected documents and human rejection
rates by report, host and category. Spikes pause/rebenchmark; they never silently retune.

## 7. Execution and stop rule

Execution remains strictly serial with the C0B-4 preflight, exact-mode filesystem check,
global lease, call budgets, crash recovery and explicit boundary resumes. Shared GPU
contention may increase duration but never changes scoring. The worktree stays clean and
source-pinned from create through terminal receipt.

Only one C0B-5 live child may contact the model after C0B-5B passes all offline, leak,
file-size and independent hostile-review gates and its source is committed. A quality
miss ends immutable `INCONCLUSIVE`. It does not authorize a wider limit, prompt change,
model change, seed change or repeat run. Any new course requires explicit HI review and a
new policy identity.

## 8. Cards and acceptance

### C0B-5A — outcome and policy freeze

- freeze the verified C0B-4 outcome, E8, this protocol, identities and source allowlist;
- independently verify terminal facts, inactive-lane census and policy semantics;
- make no model request and touch no private corpus;
- commit only after documentation, leak and file-size gates pass.

The project-root `README.md` was reviewed at C0B-5A close. No edit is appropriate because
Analyst remains unreleased and C1/private Stage E are still held.

### C0B-5B — isolated offline implementation

- add isolated C0B-5 policy/schema/plan/scoring/lineage/checkpoint/backup/executor/replay/
  runtime/CLI modules;
- import only the three frozen, read-only C0B-4 helpers named in §5; do not edit or
  extract from the C0B-4 implementation;
- test F document and row counts at 0/1/2/3 and final counts at 3/4/5;
- rederive exact component, template-family, category and union/intersection counts from
  attempt evidence;
- exercise every fake terminal, crash/resume, tamper, mixed-family and backup path;
- prove all C0B-2/C0B-3/C0B-4 checkpoints, allowlists and source hashes remain unchanged;
- run focused, Analyst-wide, leak, compile/diff and file-size validation;
- commit the complete reviewed source before creating a live child.

The 11-module responsibility boundary is frozen:

| Module | Sole responsibility |
|---|---|
| `c0b5_policy.py` | policy/protocol identity, ordered limits and reasons |
| `c0b5_schema.py` | strict closed C0B-5 artifact models and versions |
| `c0b5_plan.py` | fresh lane/control/request identities and activation plan |
| `c0b5_scoring.py` | lane/final metrics, template-family rows and derived public summary |
| `c0b5_lineage.py` | safe parent/source opening, exact pins and inactive census |
| `c0b5_checkpoint.py` | C0B-5 SQLite persistence and mutation invariants |
| `c0b5_backup.py` | terminal anchor, immutable snapshot and receipt |
| `c0b5_executor.py` | C0B-5 event/persistence callbacks around the allowed scored executor |
| `c0b5_replay.py` | independent C0B-4 parent and C0B-5 terminal evidence replay |
| `c0b5_runtime.py` | serial preflight/control/lane scheduling and transitions |
| `c0b5_cli.py` | argument parsing and read-only/mutating command dispatch |

### C0B-5C — one live confirmation

- create one fresh child from a dedicated clean worktree;
- run F72/20260804, verify the pause, then explicitly resume F72/20260811;
- verify that pause, explicitly resume C44/1, then verify terminal backup and receipt;
- publish only aggregate public evidence; raw model output remains owner-only.

C0B-5 reaches `CONFIRMED` only when both F lanes and the final 166-document aggregate
pass every inherited gate plus both new review-budget limits, and source/receipt/leak
verification is clean. C1 and private Stage E remain held until that result and HI review.
