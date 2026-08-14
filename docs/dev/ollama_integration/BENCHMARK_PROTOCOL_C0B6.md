# Analyst Benchmark — C0B-6 Harness-Repair Confirmation

Benchmark protocol ID: `c0b6-repaired-confirmation-v1`
Policy ID: `c0b6-assistive-bounded-fp-v1`
Policy SHA-256: `bf22aed2c077dec6e27ecd4121ca14c2e546c1a036855db7e3a58d6b1f2f55d3`
Date: 2026-08-14
Status: **C0B-6A frozen; model contact remains held until C0B-6B passes every offline
gate and the reviewed source is committed.**

## 1. Authority and scope

This protocol prospectively authorizes one narrow replacement for the C0B-5 harness
failure. Authority, in order:

1. [`CONTRACT.md`](CONTRACT.md) plus accepted errata E1–E8;
2. [`BENCHMARK_PROTOCOL_C0B5.md`](BENCHMARK_PROTOCOL_C0B5.md), frozen history;
3. [`PUBLIC_CDF_OUTCOME_C0B5.md`](PUBLIC_CDF_OUTCOME_C0B5.md), descriptive failure;
4. this protocol for the C0B-6 repair, identities, fresh requests and card gates.

C0B-2 through C0B-5 are immutable. C0B-6 neither resumes nor rescales their evidence.
The C0B-5 failure is not an execution parent because it has no valid receipt and made no
quality decision. C0B-6 retains the verified C0B-3 execution parent and C0B-4 descriptive
parent frozen in C0B-5.

This is still an operational-policy confirmation on a public synthetic corpus. It is not
a fresh holdout, production-accuracy estimate or private-data test.

## 2. Frozen candidate and policy

The candidate, prompts, request options, scoring rules and limits are byte-exact C0B-5:

- `qwen3.6:27b` at digest
  `a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e`;
- worksheet v2; `chunk_chars=8000`; `overlap=256`;
- `num_ctx=8192`; `num_predict=1024`;
- the C0B-4 unique-evidence prompt and bounded grounded-duplicate normalization;
- maximum 2 affected negative documents and 2 retained negative findings per F lane;
- maximum 4 affected negative documents and 4 retained negative findings across the
  final 40-negative aggregate;
- every other C0B-5 quality, safety, context, cancellation, provenance and privacy gate.

The exact canonical policy preimage is:

```json
{"false_positive_review_budget":{"final":{"max_affected_negative_documents":4,"max_retained_findings_on_negatives":4,"negative_documents":40},"per_f_lane":{"max_affected_negative_documents":2,"max_retained_findings_on_negatives":2,"negative_documents":16}},"inherits":{"duplicate_policy_id":"c0b4-bounded-grounded-dedup-v1","duplicate_policy_sha256":"7475e748165366ed0fb83daf1b6dae21a98d92d1c8faf3e3b7a3513aa3745c43"},"policy_id":"c0b6-assistive-bounded-fp-v1","units":{"affected_negative_document":"document","retained_model_suggestion":"row"}}
```

SHA-256 over those exact UTF-8 bytes is
`bf22aed2c077dec6e27ecd4121ca14c2e546c1a036855db7e3a58d6b1f2f55d3`.

## 3. Fresh generation conditions

The C0B-5 F72/20260804 lane was contacted completely and is descriptive only. C0B-6
freezes this serial schedule before contact:

1. `F72_20260811` — never contacted under C0B-5; owns context and cancellation/health;
2. `F72_20260818` — newly frozen stability seed;
3. `C44_1` — complete-corpus component, never activated by C0B-4 or C0B-5.

The first lane must pass before the second activates. Both F lanes must pass before C44
activates. Each completed F lane commits its aggregate and pauses at a stage boundary;
the next lane requires explicit verified resume. The final 166-document aggregate uses
fresh C44/1, immutable C0B-3 D50/D4 and fresh F72/20260811. F72/20260818 is a stability
gate and is not counted twice.

Every C0B-6 request uses new `c0b6-*` nonce, work, control and runtime-event domains.
No C0B-5 request ID, nonce or attempt may be reused.

## 4. Receipt repair

Semantic replay must never change the writable checkpoint connection. Before anchor
construction, the default verifier opens the committed terminal checkpoint through a
separate owner-only, no-follow, read-only descriptor and closes that connection after
replay. Snapshot replay uses its own pinned immutable read-only connection.

The publication order is exact:

1. structurally validate the committed terminal on the writable handle;
2. semantically replay the same named checkpoint through the isolated read-only handle;
3. create and pin the pre-receipt snapshot;
4. structurally and semantically replay that snapshot;
5. insert the sole receipt on the still-writable connection;
6. reverify the stored receipt and snapshot.

Tests must use the default semantic verifier through the actual receipt insert and assert
the writable connection's `PRAGMA query_only` remains unchanged on both pass and raised
exception. A no-op injected verifier is not acceptance evidence.

## 5. Closed failure origins

Content safety and diagnosis are both mandatory. Failure artifacts store one closed
`failure_origin` code; exception strings, paths, prompt/source text and response content
remain forbidden. The vocabulary is:

```text
safety_transport
budget_claim
filesystem_revalidation
parent_replay
source_revalidation
master_replay
resume_history
resume_control_replay
preflight
lane_activation
lane_execution
lane_derivation
cursor_transition
acceptance_derivation
terminal_recheck
backup_live_replay
backup_snapshot_replay
backup_publication
operator_abandon
```

Every broad runtime/backup exception boundary maps to exactly one code before a durable
terminal is written. A pre-terminal CLI failure prints only a command-specific closed
code (`create_failed`, `status_failed`, `verify_failed`, `run_failed`, `resume_failed`,
`abandon_failed` or `leak_scan_failed`). Unknown dynamic text never becomes output.

## 6. Cancellation-to-health proof

Before live contact, a real owner-only SQLite checkpoint must reproduce the C0B-5
boundary shape: three valid preflights, 92 valid first-lane scored attempts, one valid
context attempt/evidence and one `CANCELLED_UNVERIFIED` cancellation attempt, for exactly
97 charged calls and no health attempt or lane aggregate.

After close/reopen, verified resume must:

- independently rederive context and cancellation facts before contact;
- keep all 97 existing attempt IDs and payloads unchanged;
- precharge exactly one planned health attempt before transport;
- produce the health evidence and first-lane aggregate without repeating cancellation;
- either pause at the stage boundary or reach the frozen quality terminal;
- survive terminal receipt publication using the default verifier.

Injected failure tests cover the precharge and post-response boundaries. The test uses
fake transport only; it must forbid sockets and private-root discovery.

## 7. Artifact, storage and execution contract

C0B-6 owns a closed `c0b6-*-v1` artifact family. Mixed C0B-2 through C0B-6 artifacts
fail closed. C0B-6 mirrors the C0B-5 schema except for:

- protocol/policy/version literals and run IDs use C0B-6;
- lane IDs and component keys use seeds 20260811 and 20260818;
- `FailureEvidence` and `FailureResult` require the §5 `failure_origin` literal;
- context and cancellation controls belong only to F72/20260811.

The request ledger remains 228 scored, 4 schema retry, 33 preflight/control and 30
transport-orphan calls, with a cumulative cap of 265 and ten invocation claims. Dispatch
is serial with one durable `DISPATCHING` row. Shared-GPU contention pauses as
`PAUSED_RESOURCE`; it is not a quality miss. One fresh C0B-6 child is allowed. Any
terminal is immutable and requires one valid snapshot/receipt tuple.

## 8. Source identity and allowed implementation

The protocol digest is canonical JSON over this ID and exact-byte SHA-256 values for
`BENCHMARK_PROTOCOL_C0B2.md`, `BENCHMARK_PUBLIC_CDF_SCHEMA.md`,
`BENCHMARK_PROTOCOL_C0B3.md`, `BENCHMARK_PROTOCOL_C0B4.md`,
`BENCHMARK_PROTOCOL_C0B5.md`, `BENCHMARK_PROTOCOL_C0B6.md`,
`PUBLIC_CDF_OUTCOME_C0B3.md`, `PUBLIC_CDF_OUTCOME_C0B4.md`,
`PUBLIC_CDF_OUTCOME_C0B5.md` and `CONTRACT_ERRATA.md`.

The source task tree is the exact 87-path C0B-5 set plus the following 23 paths. Its
110-path canonical allowlist SHA-256 is
`3f0825476cb6ceb1c937d3d5e651eb868e9480852f666f2c5eff173153fbfe4c`.

```text
docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B6.md
docs/dev/ollama_integration/PUBLIC_CDF_OUTCOME_C0B5.md
scripts/analyst_benchmark/c0b6_backup.py
scripts/analyst_benchmark/c0b6_checkpoint.py
scripts/analyst_benchmark/c0b6_cli.py
scripts/analyst_benchmark/c0b6_executor.py
scripts/analyst_benchmark/c0b6_lineage.py
scripts/analyst_benchmark/c0b6_plan.py
scripts/analyst_benchmark/c0b6_policy.py
scripts/analyst_benchmark/c0b6_replay.py
scripts/analyst_benchmark/c0b6_runtime.py
scripts/analyst_benchmark/c0b6_schema.py
scripts/analyst_benchmark/c0b6_scoring.py
scripts/tests/test_analyst_c0b6_backup.py
scripts/tests/test_analyst_c0b6_checkpoint.py
scripts/tests/test_analyst_c0b6_cli.py
scripts/tests/test_analyst_c0b6_executor.py
scripts/tests/test_analyst_c0b6_plan.py
scripts/tests/test_analyst_c0b6_policy.py
scripts/tests/test_analyst_c0b6_public_flow.py
scripts/tests/test_analyst_c0b6_runtime.py
scripts/tests/test_analyst_c0b6_schema.py
scripts/tests/test_analyst_c0b6_scoring.py
```

C0B-6B may create those modules/tests and modify only the inherited living docs,
`scripts/analyst_benchmark/__main__.py`, `c0b2_leakscan.py` and `leakscan.py` as required
for routing and exact provenance. C0B-5 implementation, tests and protocol are read-only.
The only allowed predecessor implementation imports are the policy-neutral C0B-4
answer/executor/filesystem symbols already frozen by C0B-5; the new family owns all
storage, schema, replay and mutation logic.

C0B-6B requires a fresh owner-only leak baseline created immediately after the C0B-6A
commit and before any B edit. It may cross exactly one direct non-merge B commit under
the inherited committed-blob plus dirty-overlay scan rules.

## 9. Card gates

### C0B-6A — protocol freeze

- record the C0B-5 terminal without treating it as quality evidence;
- freeze the repair, failure-origin vocabulary, seeds, source scope and live budget;
- commit documentation only; no model contact.

### C0B-6B — isolated offline implementation

- implement the closed C0B-6 family without editing C0B-5;
- pass focused C0B-6 tests, the 97-call handoff regression, receipt pass/failure tests,
  mixed-family/tamper tests and all existing C0B-5 tests;
- pass Analyst provenance/security, leak, compile/diff and file-size gates;
- run wider Analyst regression with a frozen workspace;
- commit the reviewed implementation before live child creation.

### C0B-6C — one replacement confirmation

- create one child from a dedicated clean worktree at the C0B-6B commit;
- run F72/20260811, verify its boundary, then resume F72/20260818;
- verify that boundary, resume C44/1 and verify the terminal receipt;
- publish aggregate public evidence only; raw model output remains owner-only.

C0B-6 reaches `CONFIRMED` only if both F lanes and the final aggregate pass every frozen
gate and source/receipt/leak verification is clean. A quality miss is `INCONCLUSIVE`; a
harness failure remains a closed failure. There is no threshold change, prompt retune or
second C0B-6 child. C1 and private Stage E remain held until the verified result is
reviewed.
