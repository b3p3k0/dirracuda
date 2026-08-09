# Analyst Benchmark — Protocol, C0B-3 Assistive Confirmation

Benchmark protocol ID: `c0b3-assistive-confirmation-v1`
Scoring policy ID: `c0b3-assistive-bounded-fp-v1`
Date: 2026-08-09
Status: **C0B-3A FROZEN / PASS. No implementation or live run yet.**

Authoritative parents:

- [`CONTRACT.md`](CONTRACT.md) plus accepted E5 in
  [`CONTRACT_ERRATA.md`](CONTRACT_ERRATA.md);
- frozen [`BENCHMARK_PROTOCOL_C0B2.md`](BENCHMARK_PROTOCOL_C0B2.md) and
  [`BENCHMARK_PUBLIC_CDF_SCHEMA.md`](BENCHMARK_PUBLIC_CDF_SCHEMA.md);
- the immutable [`PUBLIC_CDF_OUTCOME_C0B2.md`](PUBLIC_CDF_OUTCOME_C0B2.md).

This is a compact prospective delta. Everything not explicitly changed here remains
exactly as frozen in C0B-2. It does not rescore or reopen the old run.

## 1. Decision and scope

The HI accepts bounded false-positive review cost because Analyst supports a human and
never acts. C0B-3 tests that policy against the existing public 166-document gold set.
This is a fresh execution, not a fresh dataset: C and D are confirmation data already
observed during C0B-2, while F is the only prospective holdout not exposed to the
canonical live model or used in C/D selection evidence.

C0B-3 reruns the complete public C→D→F path from a new `PREPARED` checkpoint. It uses the
same three model tags/digests, worksheet variants, model-response/evidence schemas,
public fixtures, derived views, seeds, factor candidates, generation options, stage
ordering, budgets, transport limits and scoring algorithms as C0B-2. Only the exact
versioned control-artifact and false-positive policy deltas below change. It imports no
answer, attempt, aggregate, decision or plan row from any earlier checkpoint.

Private Stage E is out of scope. C1 and product defaults remain held until C0B-3 reaches a
verified public `SELECTED` terminal.

## 2. Human-review product contract

Model rows are suggestions, not facts or actions. Production cards must preserve all of
these conditions:

1. Label every model-derived row `suggested` and `unreviewed` until the human explicitly
   accepts or rejects it.
2. Show the exact verified quote and source provenance beside the suggestion.
3. Keep deterministic identifiers visibly separate from model classification/prose.
4. Export only explicitly selected rows. No default-select-all persistence.
5. Never infer “safe” from an empty model result or incomplete model coverage.
6. Never trigger a filesystem, network, notification, authentication, quarantine or tag
   action from model output.
7. Record bounded accept/reject counters for later monitoring without retaining new raw
   content or silently changing the model/prompt/gate.

These requirements implement E5; C0B-3 itself remains a measurement instrument and does
not build the UI.

## 3. Exact scoring delta

The unit is a negative **document**, not a finding or chunk. A negative document is a
false positive when its aggregated retained model output contains any category/finding
after the frozen grounding and deduplication rules.

| Gate | C0B-2 rule | C0B-3 rule |
|---|---:|---:|
| Stage C, 12 negatives | at most 1 | at most 1 — unchanged |
| D3/D4, 12 negatives | 0 | at most 1 |
| Stage F, 16 negatives, each seed | 0 | at most 1 |
| Final acceptance, 40 negatives | at most 1 | at most 1 — unchanged |

New D/F failure identity: `negative_false_positive_above_1`. The old
`negative_false_positive_present` identity remains valid only when verifying C0B-2's
strict policy.

No other scoring rule changes. In particular:

- C/D/F recall and macro/micro F1 floors remain unchanged;
- raw grounding remains at least 99% and retained grounding remains 100%;
- strict schema, retry and first-pass-validity limits remain unchanged;
- injection, tool, image, marker, unknown-field and schema-escape events remain zero;
- boundary, truncation, length and context-headroom gates remain unchanged;
- context allocation, cancellation health, transport, provenance, filesystem and backup
  controls remain unchanged;
- speed, offload and resource timing never break a tie or change quality.

The final acceptance gate may fail even after intermediate bounded-FP gates pass. That is
intentional. No runner-up promotion, rescore or threshold change follows a failure.

## 4. Fresh execution and holdout integrity

The complete run starts from a newly created `c0b3-*` identity under one clean committed
source tree. Creation may read every public fixture to freeze the manifest, but no F
document text may be sent to Ollama or used in scoring or decision evidence before the
new run's activated final-D decision. Looking at an F result and then changing a model,
prompt, worksheet, scorer, factor, gate or order terminates the new run
`INCONCLUSIVE`; it never becomes a retune.

The existing gold set is sufficient for this product-risk decision. This protocol makes
no population-accuracy or confidence-bound claim. Adding hundreds of new fixtures would
measure a different question and is not required to confirm this assistive workflow.

Calls remain serial and use the C0B-2 stage/class/cumulative caps and 240-minute soft
invocation wall. Shared-GPU offload and slow execution remain quality-neutral. Pause,
resume, cancellation and verified terminal receipts work exactly as before.

## 5. Policy and protocol identity

The policy hash preimage is exactly this JSON object:

```json
{"gates":{"final_acceptance":{"max_false_positive_documents":1,"negative_document_count":40},"stage_c":{"max_false_positive_documents":1,"negative_document_count":12},"stage_d3_d4":{"max_false_positive_documents":1,"negative_document_count":12},"stage_f_per_seed":{"max_false_positive_documents":1,"negative_document_count":16}},"policy_id":"c0b3-assistive-bounded-fp-v1","unit":"negative_document"}
```

Its canonical bytes are UTF-8 from Python `json.dumps` with `sort_keys=True`,
`separators=(",", ":")`, `ensure_ascii=False` and `allow_nan=False`. The frozen
`policy_sha256` is
`4b18b631daa61da7e22993777962b4822f892e03466236b1b6317da40c260235`.

For C0B-3, `protocol_sha256` is SHA-256 over the same canonical JSON encoding of:

Each component value is the lowercase 64-character hexadecimal SHA-256 digest string of
the exact file bytes, substituted into this valid JSON template before canonical
serialization:

```json
{
  "benchmark_protocol_id": "c0b3-assistive-confirmation-v1",
  "components": {
    "c0b2_protocol_sha256": "<lowercase hex digest>",
    "c0b2_public_schema_doc_sha256": "<lowercase hex digest>",
    "c0b3_protocol_sha256": "<lowercase hex digest>",
    "contract_errata_sha256": "<lowercase hex digest>"
  }
}
```

All paths resolve under `docs/dev/ollama_integration/` through the existing verified
regular-file reader. No current-file lookup substitutes for stored header pins after
creation.

The new header has three required exact fields:
`benchmark_protocol_id="c0b3-assistive-confirmation-v1"`,
`policy_id="c0b3-assistive-bounded-fp-v1"`, and the literal `policy_sha256` above.
Header discrimination is exhaustive:

| Header shape | Meaning |
|---|---|
| all three fields absent | legacy C0B-2 strict policy and validator only |
| all three fields present and exact | C0B-3 policy and validator only |
| partial, mixed, unknown or mismatched | reject before mutation or HTTP |

Run-ID prefixes are display identity only. Policy is never inferred from a run ID, Git
history, failure reason or current source file.

## 6. Versioned lineage and backward compatibility

Every C0B-3 D/F policy-sensitive payload requires the exact `policy_id` and
`policy_sha256`. D1 is the single parent-link exception: its parent is the unchanged
Stage-C v1 selection, whose ownership is bound by the current checkpoint header; D1 is
the first policy-bearing payload. Every later D/F parent hash must name an artifact
carrying the same policy binding. The following current versions are exclusive to C0B-3:

| Artifact | C0B-3 version |
|---|---|
| D phase plan | `stage-d-phase-plan-v2` |
| D phase aggregate | `stage-d-phase-aggregate-v2` |
| D intermediate/final decision | `stage-d-decision-v2` |
| F seed plan | `stage-f-seed-plan-v2` |
| F acceptance template/activated plan | `stage-f-acceptance-plan-v2` |
| F master plan | `stage-f-master-plan-v2` |
| F seed-1 evidence | `stage-f-seed1-evidence-v2` |
| F seed activation decision | `stage-f-seed-activation-v2` |
| F seed cursor transition | `c0b3-f-seed-cursor-transition-v1` |
| F aggregate | `stage-f-aggregate-v2` |
| F provisional decision | `stage-f-selection-v2` |
| F C44 scored aggregate | `stage-f-c44-scored-v2` |
| F acceptance component | `stage-f-acceptance-component-v2` |
| F acceptance aggregate | `stage-f-acceptance-aggregate-v2` |
| selected/inconclusive result | `c0b3-result-v1` |
| completion value | `c0b3-completion-v1` |
| completion decision ID | `c0b3-completion` |

Each listed v2 payload is the corresponding C0B-2 v1 exact-key schema plus only required
`policy_id`, required `policy_sha256`, the listed `version`, and the stated
`negative_false_positive_above_1` reason change. A `c0b3-result-v1` payload is likewise
the corresponding strict C0B-2 selected or stage-specific inconclusive result shape plus
required `policy_id` and `policy_sha256`, with only its version changed.

The exact `c0b3-completion-v1` value keys are `version`, `policy_id`, `policy_sha256`,
`outcome`, `artifact_sha256`, and `facts`. `version` and both policy fields are exact;
`outcome` is `SELECTED` or `INCONCLUSIVE`; `artifact_sha256` owns the exact
`c0b3-result-v1`; and `facts` retains the corresponding strict C0B-2 `SelectedFacts` or
`DeterministicStopFacts` shape. No other completion key is permitted.

Stage-C plan, aggregate and selection formats remain exactly C0B-2 v1 because their rule
does not change; the C0B-3 header and fresh hashes establish their run ownership. Generic
`c0b2-plan-activation-v1`, `c0b2-failure-evidence-v1`, `c0b2-failure-v1`,
`c0b2-backup-anchor-v1` and `c0b2-backup-receipt-v1` container formats also remain
unchanged because their meanings do not encode a scoring threshold. For C0B-3 they may
only name/rederive current-version plans, evidence, decisions and results under a C0B-3
header. Backup and verify dispatch by the stored header discriminator before validating
owned artifacts.

Legacy v1 models remain byte-exact and strict. No optional/defaulted policy field is added
to them. Current scorers and rederivers accept only the current versions above; legacy
scorers and rederivers accept only their existing v1 versions. Unknown cross-version
combinations fail closed before activation, backup acceptance, mutation or HTTP.

Backward compatibility is mandatory:

- C0B-2 D/F recomputation continues to use the zero-FP rule and old failure identity;
- C0B-3 uses the bounded rule and `negative_false_positive_above_1`;
- all three canonical C0B-2 checkpoints keep their exact terminal states and continue to
  pass read-only `status`/`verify`;
- no database schema or migration change is permitted for this policy split.

The `c0b3 create/run/resume/abandon` mutating namespace requires a current header; C0B-2
mutating commands reject current checkpoints and C0B-3 mutating commands reject legacy
checkpoints before HTTP. Existing C0B-2 and new C0B-3 `status`/`verify` remain read-only,
may inspect either recognized header, and report the resolved protocol/policy explicitly.

## 7. C0B-3B implementation card

`FROZEN_C0B2_PUBLIC_PATHS` remains byte-semantic authority for C0B-2. The separate
`FROZEN_C0B3_PUBLIC_PATHS` is exactly `FROZEN_C0B2_PUBLIC_PATHS` union these literal new
paths; it is selected by the exact protocol namespace and never by a prefix match:

- `scripts/analyst_benchmark/c0b3_cli.py`
- `scripts/analyst_benchmark/c0b3_policy.py`
- `scripts/analyst_benchmark/c0b3_schema.py`
- `scripts/tests/test_analyst_c0b3_cli.py`
- `scripts/tests/test_analyst_c0b3_policy.py`
- `scripts/tests/test_analyst_c0b3_schema.py`
- `scripts/tests/test_analyst_c0b3_runtime.py`
- `scripts/tests/test_analyst_c0b3_public_flow.py`
- `docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B3.md`
- `docs/dev/ollama_integration/UI_MOCKUPS.md`

C0B-3B may edit only these literal paths:

- `scripts/analyst_benchmark/__main__.py`
- `scripts/analyst_benchmark/c0b2_schema.py`
- `scripts/analyst_benchmark/c0b2_public_schema.py`
- `scripts/analyst_benchmark/c0b2_executor.py`
- `scripts/analyst_benchmark/c0b2_runtime.py`
- `scripts/analyst_benchmark/c0b2_runtime_common.py`
- `scripts/analyst_benchmark/c0b2_runtime_d.py`
- `scripts/analyst_benchmark/c0b2_runtime_f.py`
- `scripts/analyst_benchmark/c0b2_runtime_f_evidence.py`
- `scripts/analyst_benchmark/c0b2_runtime_f_namespace.py`
- `scripts/analyst_benchmark/c0b2_leakscan.py`
- `scripts/analyst_benchmark/c0b2_stage_d_plan.py`
- `scripts/analyst_benchmark/c0b2_stage_d.py`
- `scripts/analyst_benchmark/c0b2_stage_f_plan.py`
- `scripts/analyst_benchmark/c0b2_stage_f.py`
- `scripts/analyst_benchmark/c0b3_cli.py`
- `scripts/analyst_benchmark/c0b3_policy.py`
- `scripts/analyst_benchmark/c0b3_schema.py`
- `scripts/tests/test_analyst_c0b3_cli.py`
- `scripts/tests/test_analyst_c0b3_policy.py`
- `scripts/tests/test_analyst_c0b3_schema.py`
- `scripts/tests/test_analyst_c0b3_runtime.py`
- `scripts/tests/test_analyst_c0b3_public_flow.py`
- `docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B2.md`
- `docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B3.md`
- `docs/dev/ollama_integration/CONTRACT_ERRATA.md`
- `docs/dev/ollama_integration/README.md`
- `docs/dev/ollama_integration/RISK_REGISTER.md`
- `docs/dev/ollama_integration/LESSONS_LEARNED.md`
- `docs/dev/ollama_integration/UI_MOCKUPS.md`

The three new source modules and five new focused tests absorb the policy/version logic;
near-limit legacy files receive only small dispatch delegates. In particular,
`c0b2_runtime.py` (1,670 lines), `c0b2_runtime_d.py` (1,656),
`c0b2_checkpoint.py` (1,673), `test_analyst_c0b2_checkpoint.py` (1,688), and
`test_analyst_c0b2_runtime_common.py` (1,692) must not exceed 1,700 lines. The checkpoint
and two near-limit existing test files are outside the edit set. If any touched file would
cross 1,700, stop and propose extraction before editing it. If another path proves
necessary, stop and revise this protocol before touching it.

Required tests include:

- exact D and per-seed F boundaries: 0/1 false-positive documents pass; 2 fail;
- final acceptance remains 0/1 pass and 2 fail across 40 negatives;
- legacy strict policy still rejects one D/F false-positive document;
- old header/plan/artifact verification stays byte- and decision-consistent;
- policy mismatch/unknown/mixed lineage fails before transport;
- fresh create pins the new protocol, policy and exact task tree;
- fake-transport C→D→F reaches both `SELECTED` and `INCONCLUSIVE` branches;
- crash, retry, pause/resume, cancellation, backup and leak gates remain green;
- exact Analyst regression plus wider/full regression when risk warrants.

Before and after implementation, read-only `status` and `verify` must return the same
terminal/call facts for the three canonical C0B-2 checkpoints and their database file
SHA-256 values must not change: the 13:53:58 run is `FAILED_SAFETY`/3 calls, the 14:11:11
run is `FAILED_SAFETY`/4 calls, and the 14:23:58 run is `INCONCLUSIVE`/530 calls.

No requirements, database schema/migration, auth or CI file is in scope.

## 8. C0B-3C live card

After C0B-3B passes tests and independent hostile review, commit the exact clean source
tree before creating a live checkpoint. Execute C, then D/F only when durable decisions
authorize them. Verify every boundary/terminal receipt before source changes.

Possible outcomes:

- `SELECTED`: one model/config passes every unchanged gate plus E5's bounded-FP policy;
- `INCONCLUSIVE`: no unique fully accepted model/config;
- existing safety/provenance/resource/cancellation terminals retain their frozen meaning.

No result authorizes private Stage E automatically. After a verified `SELECTED`, the HI
may separately authorize E, explicitly defer it and begin eligible C1 work, or stop.

The frozen maximum is 2,750 calls and 27–44 active GPU-hours. The prior one-survivor path
suggests roughly 940 calls and 10–13 active hours, but that is planning evidence, not a
timeout or quality gate. Shared-GPU calendar time may be longer; pause/resource controls
remain authoritative.

## 9. C0B-3A acceptance

C0B-3A passes when:

1. E5 and this protocol state the same numeric policy and human-review contract.
2. C0B-2's terminal record and frozen execution rules remain unchanged.
3. The exact C0B-3B path/version/compatibility gates are independently reviewed.
4. Root `README.md` is reviewed; no pre-launch product claim is added.
5. File-size, stale-term, link, content-leak and diff checks pass.

Only then may C0B-3B code work begin.
