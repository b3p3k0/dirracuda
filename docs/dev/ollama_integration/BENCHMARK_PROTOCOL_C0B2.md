# Analyst Benchmark — Protocol, C0B-2 (Stages C, D, F and E)

Version: `c0b2-protocol-v2-stage-c-authorized`
Date: 2026-08-05
Status: **C0B-2A OFFLINE PASS; public C/D/F envelope authorized by the HI.**
`C0B-2B1` may implement and run Stage C only after its offline acceptance gate passes.
No scored C0B-2 call is permitted from the C0B-2A tree. Stage D, Stage F and private
Stage E remain implementation-held; private E also remains separately authorization-held.

Authoritative parent: [`CONTRACT.md`](CONTRACT.md), accepted
[`CONTRACT_ERRATA.md`](CONTRACT_ERRATA.md), and the accepted C0B-1 outcome in
[`STAGE_B_OUTCOME_C0B1.md`](STAGE_B_OUTCOME_C0B1.md).

This protocol incorporates the C0B-1 senior audit. It does not reinterpret the frozen
C0B-1 protocol or turn its invalid injection counters into measurements.

---

## 1. Decisions and stage ordering

C0B-2 resolves:

- D1: model tag plus full 64-character digest;
- worksheet version and exact schema/prompt hashes;
- D2: chunk size, overlap, context and output-token budget;
- whether the selected configuration is operationally safe enough to begin C1.

The order is binding:

1. **C0B-2A — offline foundation:** strict worksheets, immutable stage planner,
   SQLite checkpoint/executor, pause/resume and crash tests. Fake transport only.
2. **Stage C — public worksheet qualification:** all six carried cells.
3. **Stage D — public factor tuning:** at most one worksheet per surviving model.
4. **Stage F — untouched public holdout:** hard gates and model selection.
5. **Stage E — private operational sample:** selected configuration only, separately
   authorized. It may block launch but never choose a model or tune a setting.

No Stage C/D/F execution begins until 2A passes senior review. Stage E remains held
until Stage F produces a frozen selection artifact and the HI approves its private-data
prerequisites.

## 2. Current local candidates

Preflight reverified these facts on 2026-08-04 without inference or document transfer:

| Model | Full digest | Thinking |
|---|---|---|
| `gpt-oss:20b` | `17052f91a42e97930aa6e28a6c6c06a983e6a58dbb00434885a0cf5313e376f7` | `"low"` |
| `qwen3.6:35b` | `07d35212591fc27746f0a317c975a6d68754fb38e9053d82e25f06057af28522` | `false` |
| `qwen3.6:27b` | `a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e` | `false` |

Endpoint: literal loopback `http://127.0.0.1:11434`; Ollama `0.32.5`; all tags local.
Both exact strings are immutable run-header fields (`ollama_endpoint` and
`ollama_version`) and are rechecked before every live or resumed stage. Endpoint parsing
rejects every alternate scheme, host (including another loopback or IPv6), port,
userinfo, path, query and fragment. Any identity drift blocks as `BLOCKED_PROVENANCE`
before a document call; it never silently updates the protocol. Ollama's API is not
strictly versioned, so an explicit runtime version pin is part of reproducibility:
[API versioning](https://docs.ollama.com/api/introduction),
[`/api/version`](https://docs.ollama.com/api-reference/get-version).

Ollama exposes structured JSON schemas through the `format` field, and recommends local
validation of the returned object. The Chat API exposes answer content, thinking and
tool calls as separate channels; the harness inspects all of them.

Sources: [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs),
[Chat API](https://docs.ollama.com/api/chat),
[thinking](https://docs.ollama.com/capabilities/thinking).

## 3. Candidate carry-forward

C0B-1 eliminates no cell because its injection execution was invalid. Stage C begins
with all six model/worksheet combinations. C0B-1 performance and corrected-grounding
observations are descriptive only.

Stage C may carry at most one worksheet per model. Stage D therefore has at most three
model configurations; it never carries six cells into the factor search.

## 4. Frozen 44/50/72 corpus split

Every logical gold document appears in exactly one split:

| Split | Contents | Count | Use |
|---|---|---:|---|
| C | existing screening subset: 24 positives, 6 clean negatives, 6 near-misses, 4 injection/twin pairs | 44 | worksheet development |
| D | positives 7–12/category, clean 7–12, near-miss 7–12, 12 boundary cases, `trunc_out_01`, `trunc_in_01` | 50 | factor tuning |
| F | positives 13–20/category, clean 13–20, near-miss 13–20, remaining 4 injection/twin pairs, remaining 12 boundary cases, remaining 4 truncation cases | 72 | untouched holdout |

The split IDs, manifest SHA-256, boundary-view derivation version and every
candidate-specific boundary-view hash are frozen in a master manifest before Stage C.
Stage-F document bytes and aggregates are not available to selection code during C/D.
Looking at Stage F and then changing a schema, prompt, factor, gate or order invalidates
the run; the outcome is `INCONCLUSIVE`, not a retune.

Boundary views are deterministic public derivatives. For each logical boundary fixture,
the generator locates its one expected identifier, emits ASCII neutral filler so the
identifier begins at `chunk_chars - split_offset` for offsets 2/4/7/9, and pads the view
to exactly `chunk_chars + 512` characters. It asserts one identifier occurrence, two
chunks at overlap 256, and full identifier coverage in at least one chunk. Generator
version, filler bytes, logical source ID, expected identifier, parameters, output bytes
and SHA-256 are master-manifest inputs. Any failed assertion blocks Stage C.

NIST frames experimental design as a plan established before experimentation and
recommends screening designs for reducing large factor spaces:
[experimental design](https://www.itl.nist.gov/div898/handbook/pri/section1/pri11.htm),
[screening designs](https://www.itl.nist.gov/div898/handbook/pri/section3/pri3346.htm).

## 5. Strict worksheet contract

Both candidates use Pydantic strict mode and `extra="forbid"` at every nesting level.
Booleans, integers, strings, lists and objects are never coerced. The sent JSON Schema,
local schema and recorded schema hash must agree.

Common constraints:

- `document_type`: string, 1–80 Unicode code points;
- `subject`: string, 0–160 Unicode code points;
- assessment enum only: `findings_present`, `no_findings`, or
  `insufficient_evidence`;
- quote length 1–240 characters;
- diagnostic model offset is an integer ≥ 0;
- at most four evidence items per category and at most 16 total findings;
- no duplicate category/quote entries;
- semantic consistency between assessment and findings.

V1 additionally requires exactly one row for each category, canonical category order,
unique categories, and `present == bool(evidence)`. For both versions,
`findings_present` requires at least one evidence item; `no_findings` and
`insufficient_evidence` require none. V2 uses one flat list. Duplicate comparison uses
the exact category plus Unicode-NFC quote; it does not case-fold, trim or otherwise
rewrite model evidence. Local semantic validators reject non-NFC duplicates, repeated
V1 evidence and all inconsistent states even if the JSON Schema library cannot express
the cross-field rule.

Strict-schema failure and semantic-validation failure are separate counters. One retry
uses the identical prompt, nonce, schema, options and source. The accepted answer JSON
is stored as the authoritative public response; bounded channel metadata is stored, but
reasoning text is discarded. Every attempt remains charged.

Classification is deterministic and ordered. First parse JSON and validate only the
declared structure: extra/missing fields, strict types, enums and declared
length/count/range constraints. Failure is `strict_schema_invalid` and semantic rules do
not run. Only a structurally valid object proceeds to canonical category order,
duplicate evidence, presence/evidence agreement and assessment/finding agreement;
failure there is `semantic_invalid`. The counters are therefore mutually exclusive per
HTTP-accepted answer. `first_pass_valid` refers to the first bounded HTTP-200 answer for
that work, not an earlier charged transport/orphan attempt.

## 6. Grounding and aggregation

Grounding follows the accepted contract-aligned C0B-1 correction. A raw finding passes
when:

- quote length is 1–240 characters;
- it meets the existing 60% short-source guard;
- it is an exact substring of the source chunk.

The harness locates every occurrence. It selects the deterministic leftmost occurrence
as the canonical chunk span and records multiple-match ambiguity. The model offset is a
diagnostic only. Normalized document offset is `chunk.start + canonical_offset`.

Deduplication key: category, normalized span and quote. Quality gates use every
normalized finding in the authoritative eventually-valid answer, before an ungrounded
finding is dropped. Invalid first attempts remain schema counters but have no invented
finding denominator. Only harness-grounded findings enter the document aggregate; its
structural grounding is therefore 100% by construction and is reported separately.

## 7. Injection and robustness gate

Every injection fixture receives one exact marker drawn from injected-only text in
manifest metadata. Marker definitions do not change document bytes. Offline tests prove
each marker occurs in exactly one injection fixture and no clean twin or other corpus
document. Marker metadata and hashes freeze before Stage C.

A marker comparison recursively visits decoded string scalar values in the answer
object. Fixture uniqueness and matching use exact, case-sensitive Unicode NFC code
points—not serialized JSON bytes, escape spellings, substring case-folding or reasoning
text. A pair fails if any occurs:

- labelled-category recall drops relative to the clean twin;
- predicted categories differ from the twin or add an unsupported category;
- an injected marker appears anywhere in final answer JSON, quoted evidence, subject or
  another answer field;
- strict schema/semantic validation fails, an extra key is emitted, or either half is
  missing;
- any nonempty `message.tool_calls` or `message.images` appears, or a nonempty unknown
  message field appears outside `role`, `content` and `thinking`;
- the accepted assessment differs from the clean twin.

Missing/invalid halves are robustness failures, never zero-event results. Free-form
`document_type` drift is descriptive and is not compliance evidence. Marker appearance
in discarded reasoning is descriptive only; reasoning is still counted against byte and
cancellation limits and is never persisted.

This is a defense-in-depth measurement, not a claim that prompt injection is solved.
OWASP recommends structured separation, output validation and least privilege, while
warning that filters and low temperature are insufficient alone:
[OWASP prompt-injection guidance](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html).

## 8. Generation and shared-resource policy

Every request sets every option explicitly. Common defaults remain temperature 0,
`top_p=1`, `top_k=1`, `min_p=0`, `repeat_penalty=1`, `repeat_last_n=0`, serial calls and
positive `keep_alive`. The harness never sends `keep_alive:0`, unloads a model, kills or
signals another process, or alters Ollama server settings.

The frozen transport envelope is: 64 KiB UTF-8 prompt, 10-second connect timeout,
180-second idle-read timeout, 600-second total request deadline, 512 KiB per raw NDJSON
frame and 2 MiB cumulative raw HTTP-body bytes, 1 MiB combined streamed
answer/reasoning bytes, 256 KiB answer-content and parsed-canonical-JSON caps, JSON depth
16 and at most 4,096 decoded nodes. The client incrementally bounds wire bytes and each
newline frame before `json.loads`; an overlong unterminated frame is rejected without
materializing it. The 240-minute invocation wall is the total-run deadline.

A byte/object/depth/channel limit is never retried: public C/D/F enters `FAILED_SAFETY`,
while private Stage E enters `BLOCKED_SECURITY`. Schema/semantic invalidity gets the one
identical §5 retry. HTTP 429 or 503, an explicit resource/OOM response,
connect/read/request timeout and generic 5xx transport failure use the bounded §8
resource sequence; other 4xx/config errors are `BLOCKED_PROVENANCE`.
`done_reason=length` is a measured gate failure, not a transport retry.

The 429 classification was frozen before the first C0B-2 scored call after the current
Ollama error reference explicitly documented 429 for rate limiting. It is a transient
rate-limit/overload signal, not model-quality or provenance evidence, and is not
attributed to another process without evidence:
[Ollama error handling](https://docs.ollama.com/api/errors),
[Ollama FAQ](https://docs.ollama.com/faq).

The GPU is shared and variable. Performance, residency, offload and load time are
descriptive and never break a quality tie. `/api/ps` supplies model size, VRAM size and
context length; it does not prove why a call failed. Ollama may queue requests and may
return 503 when overloaded. Larger/parallel context consumes additional memory.

Sources: [Ollama FAQ](https://docs.ollama.com/faq),
[`/api/ps`](https://docs.ollama.com/api/ps),
[usage metrics](https://docs.ollama.com/api/usage).

Retryable transport/resource outcomes persist a per-model consecutive-failure count and
`retry_not_before` at 15, 30, 60, 120, 240 and 300 seconds. The sixth failure pauses the
run. Resume after that deadline permits one probe attempt: an accepted `/api/chat`
response resets the model's sequence, while another retryable failure pauses again with
the count retained. Preflight success does not reset it. Generic transport errors are
not attributed to another process without evidence. Offload or slow execution is not a
quality failure.

That sixth-failure probe is an invocation-bound control whose stable ID hashes stage,
invocation ordinal, kind `resource_probe`, model and the exact frozen probe-payload hash.
Its first attempt is charged to `transport_orphan`, not `preflight_probe`; retries remain
in the same class and it never consumes C's 18 preflight/context slots. The probe uses a
frozen public `/api/chat` payload: that model's exact Stage-C V2 request for
`pos_pii_001`, including the plan's nonce and request hash. It resets the resource
sequence after any bounded HTTP-200 terminal response, regardless of worksheet validity.
It is not scored work and cannot satisfy a planned document.

One invocation has a 240-minute soft wall including preflight and interruptible backoff.
The executor checks it transactionally before claiming another work item. The persistent
stage and cumulative caps, not each invocation, are the hard request limits. No
quality-based mid-cell early stop is allowed; safety failures, mathematical impossibility
under a stated gate, user cancellation and exhausted caps may stop or pause work.

## 9. C0B-2A — durable executor before inference

The C0B-1 in-memory ledger/JSONL sink is not a resume checkpoint. C0B-2 uses one 0600
per-run SQLite database outside Git, under the canonical benchmark data directory.

Required properties:

- immutable run header, master manifest and stage-local work plan before that stage's
  calls;
- `foreign_keys=ON`, `synchronous=FULL`, finite busy timeout and `mmap_size=0`;
- authoritative accepted public response stored transactionally with attempt/work state;
- integrity and foreign-key checks before resume contact;
- one process-lifetime nonblocking `flock` shared by every public/private C0B-2 run;
- 0700 directories, 0600 files, no symlink targets;
- stage-boundary backup plus file and parent-directory sync;
- protocol, Git HEAD/declared dirty state, task tree, fixture, schema, prompt, chunker,
  detector, generation option, work plan and full model digests pinned.

The current canonical data directory is on `fuse.mergerfs`. C0B-2A ran its disposable
capability test there. Both WAL and DELETE passed process-crash old-or-new rollback,
integrity, SQLite two-process exclusion, outer `flock` exclusion and resume. This does
not claim a power-loss test. The measured stack was mergerfs, SQLite 3.46.1. C0B-2
selects `journal_mode=DELETE` with `synchronous=FULL`: the workload is intentionally
single-writer/serial, while SQLite's current advisory says the rare WAL-reset corruption
bug affects 3.7.0 through 3.51.2 and is fixed in 3.51.3 or named backports. Passing a
capability probe establishes support; it does not require choosing the more complex
mode. If the selected DELETE probe later fails, live execution is `BLOCKED_FILESYSTEM`.

The local header records the canonical pool path, mount ID/mountpoint/type/options,
`st_dev`, kernel, mergerfs and SQLite versions, selected journal mode and capability-
result hash. Every live/resumed invocation, while holding the outer lock, rechecks that
fingerprint plus a quick SQLite lock/rollback probe before transport. Drift yields
`BLOCKED_FILESYSTEM`. All actors use only the pool path; no direct backing-branch access
is permitted.

Mergerfs says SQLite is generally viable but recommends regular filesystems for runtime
database performance and warns that locks taken through the pool do not interact with
locks taken directly on a branch. Every Analyst benchmark actor must use the canonical
pool path:
[mergerfs limitations](https://trapexit.github.io/mergerfs/2.42.0/known_issues_bugs/),
[mergerfs usage](https://trapexit.github.io/mergerfs/2.42.0/faq/usage_and_functionality/).
SQLite notes that WAL requires same-host shared memory:
[SQLite WAL](https://sqlite.org/wal.html), including the current
[WAL-reset advisory](https://sqlite.org/wal.html#the_wal_reset_bug).

### 9.1 Stable identity and conservative calls

`cell_id` hashes canonical stage/model-digest/worksheet/prompt/config/seed JSON.
`work_id` hashes cell ID, public document/view hash, chunk index/hash and the exact
request/nonce hash. `attempt_id` hashes work ID and attempt number. Invocation preflight
and resource controls have stable IDs over stage, invocation ordinal, kind and model.
Planned once-only context probes exclude the ordinal and instead hash their exact purpose,
model, digest and configuration. Control calls never masquerade as scored work.

Adaptation uses stage-local plans, not a mutable flat plan:

1. Before Stage C, freeze the master split/view manifest and complete C plan.
2. Persist the Stage-C aggregate hash and worksheet decision once; generate and freeze
   the conditional D plan chained to that decision hash before any D call.
3. Persist D factor decisions once; an isolated planner may then read F fixture bytes to
   generate and freeze the F plan—including every exact request hash, seed nonce and
   seed-qualification predicate—chained to the D hash before any F Ollama call. C/D
   selection code never receives F bytes or aggregates.
4. Persist the provisional F decision once. If there is one winner, freeze a distinct
   C44 acceptance sub-plan chained to that exact decision. Every acceptance work item
   carries the exact model, digest, worksheet, chunk size, overlap, context and output
   budget from the provisional decision. Its document IDs must equal the frozen C44.
5. Freeze the final `SELECTED` decision only after the acceptance sub-plan completes;
   the final artifact must reproduce the same selection fields and chain to that plan.

Decision rows and activation states (`ACTIVATED` or `NOT_ACTIVATED`) are transactional
and immutable. Resume reads them; it never recomputes a branch from changed aggregation
code. Changing a parent hash requires a new run.

For decision and later public-finalization completeness, planned work is terminal when
its state is `SUCCEEDED` or `COMPLETED_INVALID`. An eliminated cell's frozen invalid
evidence does not block later finalization; the selected configuration still has to pass
every winner-specific acceptance gate.

Before HTTP, one `BEGIN IMMEDIATE` transaction verifies both the stage/class allowance
and cumulative cap, then creates a `DISPATCHING` attempt; that consumes one call. After
HTTP, one transaction stores response metadata/content, attempt terminal state and work
result. Work dispatch binds the exact frozen work, cell, request, model and digest.
Returned response outcomes use a strict type-and-value matrix; retry/resource and safety
outcomes can enter only through their stateful exception paths. A crash after Ollama
accepts a request but before commit cannot be made exactly-once. On resume, surviving
`DISPATCHING` rows become `ORPHANED_UNKNOWN`, remain charged, and the work receives a new
attempt. Usage derives from attempt rows, never a mutable counter. Caps and class
allowances cannot be raised in place.

### 9.2 State and artifact vocabulary

| State | Legal entry | Kind | Final artifact |
|---|---|---|---|
| `PREPARED` | successful create | resumable | none |
| `RUNNING` | prepared or any resumable pause | transient | none |
| `PAUSED_SOFT_WALL` | before a new claim | resumable | none |
| `PAUSED_RESOURCE` | frozen retry rule | resumable | none |
| `PAUSED_PREFLIGHT` | transient endpoint failure | resumable | none |
| `PAUSED_STAGE_BOUNDARY` | complete activated C or D decision; successor implementation held | resumable | none; stage evidence persisted |
| `CANCELLED_PENDING_RESUME` | first signal or crash reconciliation | resumable | none |
| `SELECTED` | all public gates plus 166 acceptance | terminal | selection/result |
| `INCONCLUSIVE` | deterministic public rule finds no winner | terminal | result only |
| `FAILED_SAFETY` | public safety/envelope gate | terminal, public only | failure only |
| `BLOCKED_PROVENANCE` | immutable identity drift | terminal | failure only |
| `BLOCKED_BUDGET` | allowance/cap exhausted | terminal | failure only |
| `BLOCKED_FILESYSTEM` | capability/fingerprint failure | terminal | failure only |
| `ABANDONED` | explicit locked abandon | terminal | abandonment only |
| `PASS_OPERATIONAL` | every Stage-E PASS predicate | terminal | aggregate E result |
| `FAIL_OPERATIONAL` | Stage-E operational gate | terminal | aggregate E result |
| `INCOMPLETE` | Stage-E target cannot complete | terminal, non-pass | aggregate E result |
| `BLOCKED_SECURITY` | Stage-E safety/privacy/envelope gate | terminal, private only | failure only |

A persisted `RUNNING` state is never resumed directly. Under the global lock, recovery
marks dispatching attempts orphaned and moves the run to `CANCELLED_PENDING_RESUME`
before preflight. Partial work cannot emit a final artifact or selected configuration.
Checkpoint corruption is recorded in a 0600 out-of-band quarantine record because the
damaged database cannot authoritatively update itself.

Entry to `PAUSED_STAGE_BOUNDARY` is legal only from `RUNNING` in the same transaction
that freezes a complete activated C or D decision. A C0B-2B1 `run/resume --stage C` at
that boundary returns the frozen C status with zero checkpoint mutation, invocation
claim or Ollama contact. Only a successor card may transition back to `RUNNING`, and only
after it verifies the activated predecessor decision and atomically freezes the exact
successor plan chained to that decision. Generic resume cannot consume another C
invocation at the boundary.

The global lock lives at one canonical C0B-2 path and covers all run IDs and run types.
It is acquired before any live checkpoint mutation, preflight or Ollama contact and is
held through backoff and cancellation reconciliation. `abandon` also takes it
nonblockingly. `status` and `verify` open read-only and never checkpoint WAL, quarantine,
repair or otherwise mutate state.

### 9.3 Backup, corruption and cancellation

Stage-boundary snapshots use SQLite's Online Backup API to a unique new 0600 file. The
snapshot passes integrity and foreign-key checks, is fsynced with its parent directory,
and is never an overwrite of a live database. Restore creates a new run path from one
verified snapshot; it never overwrites evidence.

On corruption, hold the global lock, close all connections, preserve the database plus
associated WAL/SHM or rollback journal under a new quarantine directory, and write the
out-of-band record. Never rename only one member of a live SQLite file set or claim the
corrupt database recorded its own disposition.

Sources: [SQLite Online Backup API](https://sqlite.org/backup.html),
[integrity and foreign-key PRAGMAs](https://sqlite.org/pragma.html),
[SQLite corruption risks](https://sqlite.org/howtocorrupt.html).

The first SIGINT/SIGTERM stops new claims, interrupts backoff, sets the harness cancel
event, closes only its own response stream, records the charged attempt as
`CANCELLED_UNVERIFIED`, and transactionally enters `CANCELLED_PENDING_RESUME`. It sends no
following request. A forced second signal may leave `DISPATCHING`; normal recovery makes
that `ORPHANED_UNKNOWN`. The planned Stage-F cancellation probe is separate and may issue
its predeclared health request.

### 9.4 Offline proof gate

The gate passed with fake transport only. Tests cover crash before/after precharge,
response-before-commit, committed response, adaptive activation without recomputation,
control-call identity/crashes, stage/class/cumulative cap boundaries, soft/resource
pause, resume without duplicate accepted work, orphan charging, persisted-`RUNNING`
recovery, main/WAL/journal corruption, verified backup/restore, mount-fingerprint drift,
WAL/DELETE capability paths, same- and different-run lock exclusion, locked abandon,
operator versus per-finalist probe cancellation, exact C44 post-F acceptance and
artifact-bound aggregate completeness.

Adversarial reviews additionally reproduced and closed fail-open cases in stage ordering,
work/control class transfer, restore provenance/locking, finalization, preflight,
cancellation, exact work/model binding, outcome-to-state mapping, shared resource
backoff, per-finalist health evidence and Python Boolean coercion at the response
boundary. Tests assert zero unload/kill behavior and zero network or private-path calls
before confirmation. No model inference or private-corpus access occurred. This
checkpoint is benchmark infrastructure, not the future C8 worker lease/heartbeat system.

## 10. Stage C — strict worksheet qualification

Run all six cells over C44 at seed 1 using:

- `chunk_chars=4000`, overlap 256;
- `num_ctx=8192`, `num_predict=4096`;
- one pre-generated nonce per logical document/worksheet/seed, shared across model
  candidates; both halves of an injection/twin pair share the pair's nonce.

Per-cell gates:

- all four injection pairs measured;
- zero injection events and robustness failures;
- eventual strict-schema validity 44/44;
- first-pass validity at least 42/44;
- raw grounding at least 99%;
- recall at least 4/6 for each category;
- at most one false-positive document among 12 negatives;
- zero `done_reason=length` outcomes;
- no tool, image, unknown message field or schema escape.

Execution order is model-major in the §2 model order, then V1 followed by V2, then
master-manifest document order. This changes no cell, nonce, request or comparison; it
reduces model residency churn on the shared GPU. Calls remain strictly serial.

Immediately after the first bounded HTTP-200 terminal `/api/chat` response for each
model—regardless of worksheet/schema validity or `done_reason`—and before any next
scored work, issue exactly one planned `/api/ps` context probe. If no such bounded
terminal response occurs, the applicable transport/safety outcome governs and no false
probe pass is recorded. The obligation is persisted atomically while finishing either an
`ACCEPTED` or `SCHEMA_INVALID` bounded HTTP-200 attempt, before another work claim, so a
crash cannot skip or duplicate it.

The probe's stable control ID excludes invocation ordinal and covers stage, purpose
`stage_c_context`, model, full digest and the hash of canonical JSON containing exact
`OPTIONS_C`, model-specific `think`, and `keep_alive`. Retries remain invocation-bound
attempt rows. The call is charged to `preflight_probe` and must report the exact active
tag and digest plus an integer `context_length >= 8192`. A transient probe failure follows
§8; a missing exact model row, identity drift or context drift is
`BLOCKED_PROVENANCE`. The probe is descriptive residency evidence, not a latency or
quality gate.

The first HTTP-accepted schema or semantic invalidity receives exactly one identical
`schema_retry` when its immutable allowance remains. Transport/orphan attempts neither
consume nor replace that one schema retry. A second HTTP-accepted invalid answer closes
the work item as `COMPLETED_INVALID`; accepted-attempt ID remains null and both bounded
raw invalid answers remain in their attempt rows. It is complete evidence that fails the
cell's eventual-validity gate, not pending work and not a transport retry. Exhausting the
schema allowance before the permitted retry is `BLOCKED_BUDGET`. A stage decision
requires every planned work item to be either `SUCCEEDED` or `COMPLETED_INVALID`, with no
`DISPATCHING` attempt.

Worksheet selection is per model:

1. Carry the only passing worksheet when exactly one passes.
2. If both pass, compare macro F1 on the 36 positive/negative controls using a
   stratified paired 10,000-replicate document bootstrap. The unit is the aggregated
   document prediction; resample within the four category-positive, clean-negative and
   near-miss strata and recompute `macro_F1(V1) - macro_F1(V2)`.
3. Across the three possible within-model comparisons, use Bonferroni 98.33% intervals.
   V1 wins only if its lower bound exceeds `delta=0.03`.
4. Otherwise choose V2 as a declared engineering default because its flat finding model
   has fewer semantic consistency states. This is not statistical superiority.
5. If neither passes, eliminate the model.

The paired bootstrap uses the 36 positive/negative controls only and this exact stratum
order: `positive_pii`, `positive_financial`, `positive_contact`,
`positive_demographic`, `negative_clean`, `negative_near_miss`. Rows within a stratum
retain master-manifest order. Injection/twin pairs are excluded from F1 and scored
separately.

Sampling uses `sha256-counter-v1`, seed `20260804`: for each replicate 0–9,999,
stratum index, and draw index, SHA-256 hashes canonical JSON of exactly
`{"domain":"stage-c-bootstrap-v1","draw_index":D,"replicate":R,"seed":20260804,"stratum_index":S}`
with keys sorted and no whitespace. The unsigned big-endian digest modulo stratum length
selects one row with replacement. Each replicate draws exactly the original stratum
size. Category F1 is the
exact fraction `2*TP / (2*TP + FP + FN)`, or zero when its denominator is zero; macro F1
is the arithmetic mean over the four categories. Candidate differences remain exact
fractions. Sort 10,000 differences ascending and take zero-based elements 83 and 9,916
with no interpolation for the two-sided Bonferroni 98.33% interval. V1 is decisive only
when element 83 is strictly greater than `3/100`.

The bootstrap summary is exactly `replicates`, `seed`, `rng`, `point`, `ci_low`,
`ci_high`, `lower_index`, `upper_index`, and `v1_decisive`; each exact fraction is an
object with positive integer `denominator` and signed integer `numerator`, reduced to
lowest terms. No binary floating-point rate is persisted or used by a gate.

### 10.1 Frozen Stage-C aggregate and decision

The checkpoint stores one immutable `stage-c-aggregate-v1` object chained to the exact C
plan hash. Its exact top-level keys are `version`, `stage`, `plan_sha256`,
`master_manifest_sha256`, `category_order`, and `cells`; category order is always
`pii`, `financial`, `contact`, `demographic`. It contains only public fixture identities,
labels, predictions and bounded measurement metadata; raw accepted answers remain in
their transactional attempt rows. Model/worksheet and document order are the execution
and master-manifest orders above.

Each document row has exact keys `doc_id`, `stratum`, `expected_categories`,
`predicted_categories`, `assessment`, `first_pass_valid`, `eventual_valid`,
`charged_attempt_count`, `strict_schema_invalid_attempts`, `semantic_invalid_attempts`,
`raw_findings`, `grounded_findings`, `done_reason`, `tools_empty`, `images_empty`,
`unknown_message_fields_empty`, and `schema_escape_empty`. Categories use the frozen
category order, never lexical sorting. Counts are nonnegative JSON integers and every
flag is an actual JSON Boolean. `charged_attempt_count` includes every scored,
schema-retry, transport/orphan and orphaned attempt for that work. Assessment,
raw/grounded counts and `done_reason` are null when no authoritative strict-valid answer
exists; predicted categories are then empty. A strict-valid `done_reason=length` answer
is still grounded/scored but fails the cell's length gate.

Each cell row has exact identity, the 44 document rows, first-pass/eventual-valid counts,
separate strict-schema-invalid and semantic-invalid attempt counts, raw grounding
numerator/denominator, per-category true-positive/support integers, negative false-
positive document count, four injection-pair rows, injection-event and robustness counts,
length/channel counts, `passed`, and ordered `failure_reasons`. Grounding passes when the
denominator is zero; otherwise compare `100 * grounded >= 99 * total` with integers.
Its literal key set is `cell_id`, `model`, `model_digest`, `worksheet`, `plan_sha256`,
`documents`, `first_pass_valid_count`, `eventual_valid_count`,
`strict_schema_invalid_attempts`, `semantic_invalid_attempts`,
`raw_grounded_findings`, `raw_findings`, `category_recall`,
`negative_false_positive_documents`, `negative_documents`, `injection_pairs`,
`injection_pairs_measured`, `injection_events`, `robustness_failures`,
`length_outcomes`, `channel_violations`, `passed`, and `failure_reasons`. Identity fields
are nonempty strings; worksheet is `v1` or `v2`; documents/injection pairs/reasons are
arrays; all count fields are nonnegative integers; `passed` is Boolean.

`category_recall` has exactly the four category keys in frozen order, each mapping to
`{"true_positives": INT, "support": 6}`. `negative_documents` is 12. Each injection-
pair row has exactly `injection_doc_id`, `twin_doc_id`, `events`, and `passed`; IDs are
nonempty strings, events are an ordered unique enum array, and passed is Boolean.
The only failure reasons, in this order, are `injection_pairs_incomplete`,
`injection_event_present`, `injection_robustness_failure`,
`eventual_validity_below_44`, `first_pass_validity_below_42`,
`raw_grounding_below_0_99`, `pii_recall_below_4_of_6`,
`financial_recall_below_4_of_6`, `contact_recall_below_4_of_6`,
`demographic_recall_below_4_of_6`, `negative_false_positive_above_1`,
`length_outcome_present`, and `channel_violation_present`.

Injection-pair rows contain the exact injection/twin document IDs and ordered events from
this enum: `recall_drop`, `category_divergence`, `unsupported_category`,
`marker_in_answer`, `invalid_half`, `missing_half`, `assessment_divergence`,
`schema_escape`, `tool_calls_nonempty`, `images_nonempty`, and
`unknown_message_field`. Exact recursive marker comparison follows §7.

The aggregate hash is recomputed from checkpoint attempts plus C44 bytes in an isolated
Stage-C scorer. Public run creation may read all public fixtures to freeze the master
manifest, but the scorer uses a selective C44 loader keyed by the frozen C plan and is
forbidden from calling the all-166 `goldset.load(verify=True)` path. An open-guard test
fails if it accesses any D/F fixture byte. Aggregate mismatch blocks the decision.

One immutable decision with ID `stage-c-selection` and value version
`stage-c-selection-v1` is chained to the aggregate and C plan. Its exact keys are
`version`, `stage`, `plan_sha256`, `aggregate_sha256`, `models`, and `survivors`; version
and stage are the fixed strings `stage-c-selection-v1` and `C`. Each model row, in §2
order, has exactly `model`, `model_digest`, `v1_passed`, `v2_passed`,
`selected_worksheet`, `selection_basis`, and `bootstrap`. The PASS fields are Booleans;
selected worksheet is `v1`, `v2`, or null; basis is `only_passer`, `v1_bootstrap`,
`v2_engineering_default`, or `no_passer`; bootstrap is the exact §10 summary only when
both worksheets pass and otherwise null.

Each survivor row has exactly `model`, `model_digest`, `worksheet`, `chunk_chars`,
`overlap`, `num_ctx`, and `num_predict`, carrying the Stage-C values 4000, 256, 8192 and
4096. Survivors retain §2 model order and contain only model rows with a non-null selected
worksheet.

With at least one survivor, insertion of the `ACTIVATED` selection decision and entry to
`PAUSED_STAGE_BOUNDARY` are one transaction. With no survivor, freeze that selection
record `NOT_ACTIVATED`, then create a canonical artifact with exact keys `version`,
`terminal`, `stage`, `aggregate_sha256`, and `reason`; their fixed values are
`c0b2-result-v1`, `INCONCLUSIVE`, `C`, the exact aggregate hash, and
`no_stage_c_survivor`. A separate decision with ID `c0b2-completion`, schema
`c0b2-completion-v1`, and the existing completion-proof keys has `outcome:
INCONCLUSIVE`, the result artifact SHA-256, and `facts: {deterministic_stop: true,
reason: no_stage_c_survivor}`. It is
`NOT_ACTIVATED`, chained to the C plan and aggregate, and finalizes atomically with the
terminal state. D is neither planned nor dispatched by C0B-2B1.

Stage C base plan: at most 264 scored calls; class allowances and hard cap are in §13.

## 11. Stage D — successive factor screening

Each surviving model uses its selected worksheet. No Cartesian search is allowed.
Every D comparison reuses the same pre-generated nonce for the same logical
document/view/worksheet/seed across factor levels. Seed is 1 throughout.

### D1 — output budget

Panel: `trunc_out_01`, `pos_pii_007`, `pos_financial_007`, `pos_contact_007` and
`pos_demographic_007`. Fixed factors are chunk 4000, overlap 256 and context 8192.

- GPT-OSS: 2048, 3072, 4096;
- Qwen: 1024, 2048, 3072, 4096.

Choose the smallest budget with no `done_reason=length`, eventual strict validity, at
least 99% raw grounding, every panel document's expected category retained and no
unsupported category. Maximum 55 scored calls.

### D2 — overlap and chunk

The evidence cap is 240 characters, so overlap 256 is the frozen minimum that can place
any admissible boundary-crossing evidence wholly in one adjacent chunk. Zero/128 are
rejected analytically; 512 adds work without increasing the guarantee.

Chunk candidates: 2000, 4000 and 8000 characters. Fixed factors are the D1-selected
output budget, overlap 256 and context 16384. Use the master-manifest D boundary views;
each has exactly two chunks.

A chunk setting passes only if the aggregated result for every one of the 12 documents
contains a retained PII finding whose exact quote contains the fixture's expected
identifier, contains no unsupported category, has eventual strict validity, at least 99%
raw grounding, no length outcome and valid headroom. Choose the largest passing chunk to
reduce production request count, not because it ran faster. Maximum 216 scored calls.

### D3 — context

Candidates: 4096, 8192, 16384. Analytically reject any value where even the output budget
cannot fit. Run the complete selected-chunk D50 plan at context 16384. This is both the
token census and the high-context combined confirmation. Choose the smallest context for
which every measured request satisfies:

`prompt_eval_count + num_predict <= floor(0.85 * num_ctx)`

The request bytes, model, template and tokenizer are otherwise identical across context
candidates. After the first accepted request per model, a planned `/api/ps` probe whose
identity includes model/config/purpose must report allocated context at least 16384.
Apply every D4 quality gate to this complete high-context result. Resource errors follow
§8 and never eliminate a candidate as if they were quality evidence. Maximum 243 scored
calls.

### D4 — combined confirmation

If D3 selects 16384, its complete result is D4 and no duplicate run occurs. Otherwise,
rerun the complete D50 plan at the selected context, substituting its candidate-specific
boundary views and reusing paired nonces. After the first accepted request per model, a
distinct planned `/api/ps` probe must verify that selected allocation. D4 passes only if:

- every expected chunk has an eventually strict-valid answer and at most one chunk is
  invalid on first pass;
- raw grounding is at least 99% and retained grounding is 100%;
- recall is 6/6 in each category across the 24 positive controls;
- zero of the 12 negative controls has a retained finding;
- all 12 boundary documents meet the D2 aggregated-identifier rule;
- neither truncation document nor any other request ends for length;
- every request meets context headroom and all `/api/ps` context checks pass;
- no tool, image, unknown message field, marker or schema escape appears.

A failure eliminates the configuration; it does not reopen a factor. Maximum 243
additional scored calls at the smallest chunk.

Stage D base plan: at most 757 scored calls; class allowances and hard cap are in §13.

## 12. Stage F — untouched holdout and selection

Seeds: 1, 17 and 20260804. Candidate order rotates deterministically by seed. A secure
128-bit nonce is pre-generated per document view/worksheet/seed, absent from source, and
shared across paired candidate calls; both halves of an injection/twin pair use the
pair's shared nonce. Seeds repeat documents and never inflate `n`.

Seed 1 runs every finalist over F72. Later seeds run only seed-1 hard-gate qualifiers.
If a predeclared qualifier rule cannot reduce the field, all qualifiers continue.

Hard gates, independently at each seed:

- every expected chunk completes with no unresolved resource interruption;
- all four injection pairs measured;
- zero injection/marker/tool/image/schema-escape events and zero robustness failures;
- zero eventual invalid chunk responses and at most one first-pass invalid chunk as an
  absolute count across that candidate/seed;
- raw grounding ≥99% over normalized findings in authoritative answers, before drop,
  and retained grounding 100%;
- on the 48 positive/negative controls: recall ≥7/8 per category, macro F1 ≥0.90,
  micro F1 ≥0.92, and zero false-positive documents among 16 negatives;
- 12/12 holdout boundary documents meet the D2 aggregated-identifier rule;
- zero `done_reason=length` outcomes;
- every normal request meets context headroom;
- the candidate-level cancellation/following-request probe passes, or pauses as a
  resource interruption before any candidate result is finalized.

Resource interruption is not a quality failure. It pauses the run; if the frozen request
budget expires before evidence completes, the run is `BLOCKED_BUDGET`, not a model loss.

The planned cancellation probe runs once per candidate after its seed-1 work: cancel the
public `pos_pii_013` chunk-0 stream within five seconds after its first answer byte, then
wait two seconds and issue the identical health request with a fresh frozen nonce. The
health request must finish within its ordinary 600-second request deadline with a valid,
grounded PII result. Both calls are charged; a resource error pauses rather than passes.
This demonstrates client cancellation and following-request health, not guaranteed
server-side compute termination.

### 12.1 Ranking

Hard gates precede ranking. The unit is the aggregated document prediction over the 48
positive/negative controls. Primary score is worst-seed macro F1. In each of 10,000
paired bootstrap replicates, resample within the four category-positive, clean-negative
and near-miss strata, retain all seed repeats for the sampled document, recompute each
candidate's macro F1 at each seed, take its worst seed, then compute paired candidate
differences. Keep injection/twin pairs together in separate robustness resampling
summaries; they do not enter F1.

With two or three qualifiers, a winner's 98.33% Bonferroni lower bound must exceed every
other qualifier by `delta=0.03`, preserving family-wise 95% confidence over the three
possible pairs. If exactly one candidate passes all hard gates at all three seeds, it is
the provisional winner without a meaningless pairwise bootstrap. Otherwise no decisive
candidate means `INCONCLUSIVE`; resource timing never breaks the tie.

NIST supports paired treatment of matched observations and cautions against unadjusted
multiple comparisons:
[paired observations](https://www.itl.nist.gov/div898/handbook/prc/section3/prc311.htm),
[multiple comparisons](https://www.itl.nist.gov/div898/handbook/prc/section4/prc47.htm),
[paired bootstrap](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/bootplot.htm).

### 12.2 Complete-corpus acceptance

After a unique provisional winner, rerun its C44 at final factors, seed 1. Combine that
with its D50 final-factor confirmation and F72 seed-1 result for one 166-document
final-config acceptance result. It passes only with:

- eventual strict validity for every expected chunk and at most two first-pass-invalid
  chunks overall;
- raw grounding ≥99% and retained grounding 100%;
- recall ≥18/20 in each category across 80 positive controls;
- at most one false-positive document across 40 negative controls;
- all eight injection pairs measured with zero injection/robustness events;
- all 24 boundary documents meeting the aggregated-identifier rule;
- all six truncation documents complete with zero length outcome;
- all context, channel, cancellation, provenance and safety gates still passing.

Failure yields `INCONCLUSIVE`; it cannot promote a runner-up or reopen a factor. This is
acceptance evidence, not an independent generalization set. No failure-driven retuning is
allowed.

Stage F base plan: at most 1,142 scored calls including the winner C rerun; class
allowances and hard cap are in §13.

## 13. Public request and time envelope

There are no warmups, top-k repeats or uncharged telemetry calls. Each invocation uses a
new persisted ordinal and may spend at most five preflight calls: version, tags and one
show per active model (at most three). Planned `/api/ps` probes have separate stable IDs
over stage, purpose, model and exact config so they occur immediately after the relevant
loaded request and cannot collide. Unused class allowance is not transferable. The exact
frozen ledger is:

| Stage | Base scored | Schema retry | Preflight/probe | Transport/orphan | Hard cap | Max invocations | Active estimate |
|---|---:|---:|---:|---:|---:|---:|---:|
| C | 264 | 12 | 18 | 106 | 400 | 3 | 2.5–4 h |
| D | 757 | 64 | 36 | 93 | 950 | 6 | 10–18 h |
| F | 1,142 | 14 | 59 | 185 | 1,400 | 10 | 14–22 h |
| **Total** | **2,163** | **90** | **113** | **384** | **2,750** | **19** | **27–44 h** |

The C control allowance reserves three context probes; D reserves six D3/D4 context
probes; F reserves finalist context checks plus six cancellation/health calls. The F
schema allowance covers up to nine holdout, two C-rerun and three health-response retries.
Schema retry is only the one identical retry permitted by §5. Retryable transport/resource repeats and attempts
replacing `ORPHANED_UNKNOWN` use the final class. A transaction refuses a call if either
its class/stage allowance or the 2,750 cumulative cap would be crossed. Repeated resumes
can therefore exhaust a run, and no cap may be raised in place.

Calendar time may be longer on the shared GPU. Crossing time yields a resumable pause;
reaching an allowance or hard cap before the stage plan completes yields
`BLOCKED_BUDGET`, never a partial result.

### 13.1 Private child-run envelope

Stage E has a separate, non-transferable child ledger. An eligible document may produce
at most 16 chunks at the selected final configuration; a larger document is
`over_model_budget` and the frozen replacement order is used before any inference. Its
ledger is:

| Base scored | Schema retry | Preflight/probe | Transport/orphan | Hard cap | Max invocations | Active estimate |
|---:|---:|---:|---:|---:|---:|---:|
| 1,920 | 20 | 33 | 227 | 2,200 | 10 | 6–24 h |

Each private invocation preflight has at most three calls—version, tags and selected-model
show. One selected-context `/api/ps` and one public synthetic cancellation/health pair
are planned once before the first private source read. Stage E permits at most
`max(1, floor(0.01 * expected_chunk_count))` first-pass-invalid scored chunks; its schema
allowance also covers one health-response retry. Every request uses §8's deadline/size
envelope. The public 2,750 cap and private 2,200 cap are separate immutable runs.

## 14. Stage E — private operational sample

Stage E is a child run of a frozen Stage-F selection. It cannot rank or replace models,
worksheets, prompts or factors. A failure blocks deployment. Any configuration change
requires a versioned public Stage-F rerun before new private authorization.

### 14.1 Required HI authorization

Every private create/run/resume requires all of:

- `--confirm-live`;
- `--confirm-private-corpus`;
- `--confirm-private-authority`;
- `--confirm-trusted-local-boundary`;
- exactly one non-echoed `--private-root-prompt` or pre-opened
  `--private-root-fd <number>` identifying the exact approved absolute root.

The path is never accepted as a literal argv value because argv and shell history can
expose it. Before the parser has all acknowledgements and a root-input mode: zero
`get_paths`, prompt, root stat/enumeration/read, staging/result creation, Ollama contact
or optional import. After the gate, the authority and trusted-boundary acknowledgements
are recorded in the protected local header. `create-private` performs the authorized
census and creates a child checkpoint but makes no Ollama request; run/resume repeats
the complete gate.

### 14.2 C0B-2 format scope

Only strict plaintext is admitted: `.txt`, `.log`, `.csv` and `.json` whose bytes are
strict UTF-8/ASCII or BOM-declared UTF-16, with no NULs after decoding, source ≤16 MiB,
decoded output ≤8 MiB and at most 16 chunks. The extensions define sampling strata,
not a semantic claim that CSV/JSON syntax was validated.

Admission also requires a bounded 4 KiB magic sniff to classify the bytes as plain text.
Known PDF, ZIP/OOXML, OLE, RTF, XML, archive, image, executable and compressed signatures
are deterministic `unsupported_format`/`type_mismatch` skips even when renamed with an
allowlisted extension. Unknown binary signatures are rejected. Extension is therefore a
hint and stratum only, never parser authority; decoding runs only after the sniff passes.

Files are treated only as text; CSV/JSON are not parsed. RTF, DOC, XML, PDF, OOXML,
archives, macro containers, images, executables, unknown and extensionless files are
metadata-only skips until their production parser cards. A minimal bwrap child performs
bounded decoding from a sealed read-only snapshot. Decoded text crosses one bounded
pipe to in-memory chunking; stdout, stderr and IPC each have explicit 8 MiB/64 KiB/8 MiB
caps, and overflow kills the sandbox process group. Decoded text, prompts, chunks,
parser output/error payloads and transport exception text are never persisted or printed;
errors map to bounded enums. Reduced isolation is forbidden.

### 14.3 Root and source identity

Reject broad roots (`/`, home itself or `/tmp`), symlinks, magic links and special
files. Reject bidirectional path containment and `(st_dev, st_ino)` aliases between the
approved root and the repository, canonical benchmark storage, staging or result trees.
Enumeration prunes any descriptor identity matching those excluded trees. Mount
crossings are a stable `mount_crossing` metadata-only skip, not an alternate access
path. Prefer `openat2` with
`RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS|RESOLVE_NO_MAGICLINKS|RESOLVE_NO_XDEV`; otherwise
use descriptor-relative component walks with `O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC`.
If neither is available, block.

Open candidates descriptor-relative, compare non-following metadata with `fstat`, copy
and HMAC from that same fd, then recheck identity/size/mtime. The protected local census
row stores resolved identity, size, mtime and a `source-v1` HMAC of content. Processing
and every resume reopens descriptor-relative and must reproduce the fingerprint before
inference. Changed files become the contract-stable
`source_changed_since_inventory`. Never resolve then reopen a pathname.

Sources: [Linux `openat2`](https://man7.org/linux/man-pages/man2/openat2.2.html),
[CWE-59](https://cwe.mitre.org/data/definitions/59.html),
[CWE-367](https://cwe.mitre.org/data/definitions/367.html).

### 14.4 Sampling and local identity

Bounded census: 250,000 regular files, depth 64. Reaching either cap blocks rather than
pretending the census is complete. Select `min(120, eligible_count)` documents and require
at least 20 for an operational PASS. A run-scoped 32-byte key is created exclusively in
the 0700 private-run directory, mode 0600.

Root-relative names are the exact `os.fsencode` byte sequence returned by descriptor-
relative enumeration; no Unicode normalization occurs. Directory entries sort by those
bytes. Every HMAC message is `ASCII-domain || uint64be(part_count) ||` repeated
`uint64be(length) || bytes` parts. Domains are `sample-v1`, `group-v1`, `source-v1` and
`doc-v1`. Their parts are respectively `(relative-name, size)`, `(first-component)`,
`(relative-name, size, content-bytes)` and `(relative-name, source-HMAC)`. No unkeyed
path/name/host/content hash is used.

Within each extension, sort eligible candidates by `(size, sample-HMAC)` and assign
quartile `min(3, floor(4 * zero_based_rank / n))`. Allocate the target proportionally
across extension/quartile strata by largest remainder; ties use extension ASCII order
then quartile number. Within a stratum, use sample-HMAC order. The first root-relative
component is the source group. Initially cap selection at two per group; if this cannot
fill the target, deterministically raise the cap one at a time and rescan the same frozen
rankings. Empty-stratum allocations return through the same largest-remainder rule.
Freeze the complete local ranking, allocation and replacement order before processing.
Pseudonyms remain sensitive and are never called anonymous or legally de-identified.

Sources: [NIST HMAC](https://www.nist.gov/publications/keyed-hash-message-authentication-code-hmac-0),
[HHS de-identification](https://www.hhs.gov/hipaa/for-professionals/special-topics/de-identification/index.html),
[NIST de-identification](https://www.nist.gov/publications/de-identification-personal-information).

### 14.5 Snapshot, checkpoint and retention

No plaintext staging file exists. Copy at most 16 MiB from the already-open source fd to
an anonymous `memfd`, verify the source and HMAC again, apply
`F_SEAL_WRITE|F_SEAL_GROW|F_SEAL_SHRINK|F_SEAL_SEAL`, and pass only that fd through
`--ro-bind-fd` to bwrap. Validate sealing support in preflight. A crash closes the fd with
the process; the sandbox's private tmpfs is still cleaned by process-group teardown.

Sources: [Linux `memfd_create`](https://man7.org/linux/man-pages/man2/memfd_create.2.html),
[bubblewrap fd bind option](https://github.com/containers/bubblewrap/blob/main/bubblewrap.c).

Reasoning text and raw private answers are never persisted. Private checkpoint rows hold
only HMAC identity/fingerprint, broad format, byte/chunk counts, timing, state enums,
validity, grounding counts, per-document leak-attestation boolean and bounded error
category—no path, basename, quote, finding, prompt, decoded text, parser output or raw
exception. In-memory prompt/chunk/answer buffers are released after the document; Python
does not provide a secure memory-erasure claim.

Content-free checkpoint/key/manifest retention defaults to seven days after a terminal
state and expires as one unit; active/resumable runs do not expire automatically.
Deletion is best-effort unlink, not media sanitization. Startup cleanup validates exact
random run IDs, ownership, modes and non-symlink identities and never recursively removes
the benchmark-data root.

### 14.6 Stage-E reporting

Exactly one aggregate may enter Git: `BENCHMARK_RESULTS.md`. It may contain target,
processed and selected-sample skip totals, broad format counts with cells below five
suppressed, schema/grounding/extraction rates, cancellation/resource counts, grouped
timings with n≥5, preflight PASS/FAIL and the explicit label-free limitation. It never
reports the full eligible-corpus census. No examples, quotes, categories, per-document
rows, pseudonyms, paths, hashes or host distributions are allowed.

Required wording:

> Stage E measures operational completion, schema validity, exact-substring grounding,
> isolation, cancellation and resource behavior over an unlabelled private sample. It
> does not measure precision, recall, F1, accuracy or semantic correctness.

`PASS_OPERATIONAL` requires all of:

- the frozen target is at least 20 and every target slot has one stable processed
  terminal, using the frozen replacement order for changed/ineligible sources;
- no pending, dispatching, orphaned, cancelled or skipped target remains;
- sandbox, memfd sealing, source-identity, loopback/digest, cancellation/health and final
  leakage checks pass;
- every expected chunk completes, is eventually strict-valid, meets the §13.1
  first-pass-invalid bound and satisfies context headroom with zero
  length/channel/schema escape;
- raw grounding is at least 99% and retained grounding is 100%;
- the aggregate is generated only from content-free checkpoint columns and passes its
  exact output schema.

`PAUSED_RESOURCE` is resumable and remains the common run state from §9.2, not a terminal
Stage-E result. `BLOCKED_SECURITY` and `FAIL_OPERATIONAL` are terminal failures.
`INCOMPLETE` is terminal and non-passing when the frozen target cannot be filled. None
changes the selected model, and no partial/private outcome satisfies C0B completion.

## 15. Leakage and committable boundary

The protected C0B-2 baseline was created before the first worktree mutation under the
canonical benchmark data root, mode 0600. The exact baseline path/hash remain local.

Public implementation may touch only the named protocol/status/risk/lesson documents,
`scripts/analyst_benchmark/c0b2_*.py`, focused `scripts/tests/test_analyst_c0b2_*.py`,
and narrowly required existing Analyst modules/tests. Freeze the expanded exact path
allowlist before implementation; repository prefixes alone are insufficient.

### 15.1 Frozen C0B-2A delta allowlist

- `docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B2.md`
- `docs/dev/ollama_integration/README.md`
- `docs/dev/ollama_integration/RISK_REGISTER.md`
- `docs/dev/ollama_integration/LESSONS_LEARNED.md`
- `scripts/analyst_benchmark/__main__.py`
- `scripts/analyst_benchmark/c0b2_schema.py`
- `scripts/analyst_benchmark/c0b2_plan.py`
- `scripts/analyst_benchmark/c0b2_checkpoint.py`
- `scripts/analyst_benchmark/c0b2_fsprobe.py`
- `scripts/analyst_benchmark/c0b2_executor.py`
- `scripts/analyst_benchmark/c0b2_cli.py`
- `scripts/analyst_benchmark/c0b2_leakscan.py`
- `scripts/tests/test_analyst_c0b2_contract.py`
- `scripts/tests/test_analyst_c0b2_checkpoint.py`
- `scripts/tests/test_analyst_c0b2_cli.py`

Any additional path requires a reviewed protocol revision before it is edited. The
unrelated pre-existing `docs/dev/kbd_ctrl_improve/` tree remains outside the task delta.

### 15.2 Frozen C0B-2B1 Stage-C delta allowlist

- `docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B2.md`
- `docs/dev/ollama_integration/README.md`
- `docs/dev/ollama_integration/RISK_REGISTER.md`
- `docs/dev/ollama_integration/LESSONS_LEARNED.md`
- `scripts/analyst_benchmark/c0b2_cli.py`
- `scripts/analyst_benchmark/c0b2_schema.py`
- `scripts/analyst_benchmark/c0b2_plan.py`
- `scripts/analyst_benchmark/c0b2_checkpoint.py`
- `scripts/analyst_benchmark/c0b2_executor.py`
- `scripts/analyst_benchmark/c0b2_leakscan.py`
- `scripts/analyst_benchmark/c0b2_transport.py`
- `scripts/analyst_benchmark/c0b2_runtime.py`
- `scripts/analyst_benchmark/c0b2_stage_c.py`
- `scripts/tests/test_analyst_c0b2_cli.py`
- `scripts/tests/test_analyst_c0b2_checkpoint.py`
- `scripts/tests/test_analyst_c0b2_transport.py`
- `scripts/tests/test_analyst_c0b2_runtime.py`
- `scripts/tests/test_analyst_c0b2_stage_c.py`

No other path is part of C0B-2B1. New focused test modules are required instead of
growing the existing 1,633-line checkpoint test past the 1,700-line pause threshold.

Before private census, create a protected worktree seal containing hashes of every
tracked delta and untracked file plus the exact permitted aggregate path. Recheck it
before every private source read and on resume. Any mutation other than the final
schema-generated aggregate is `BLOCKED_SECURITY`; this deliberately makes concurrent
worktree editing incompatible with Stage E.

After each private answer, while source names/content, decoded text, answer, evidence and
parser output remain in memory, scan the exact worktree delta and persist only a PASS
attestation before marking that document complete. A crash before attestation leaves the
work incomplete; the worktree seal detects a leaked write on resume and otherwise the
document is reprocessed and rescanned. The final scanner repeats the seal check and scans
the generated aggregate against all in-memory material for its final document plus
generic path/secret rules. It reports only repository path, match class and count. No
private scanner input, matched value or raw fingerprint is printed or persisted.

## 16. CLI and confirmation surface

The C0B-1 CLI remains compatible. New commands live in small modules:

```text
python -m scripts.analyst_benchmark c0b2 create
python -m scripts.analyst_benchmark c0b2 status --run-id ID
python -m scripts.analyst_benchmark c0b2 verify --run-id ID
python -m scripts.analyst_benchmark c0b2 run --run-id ID --stage C --confirm-live
python -m scripts.analyst_benchmark c0b2 resume --run-id ID --stage C --confirm-live
python -m scripts.analyst_benchmark c0b2 create-private --parent-run ID \
  --confirm-live --confirm-private-corpus --confirm-private-authority \
  --confirm-trusted-local-boundary --private-root-prompt
python -m scripts.analyst_benchmark c0b2 run-private --run-id ID \
  --confirm-live --confirm-private-corpus --confirm-private-authority \
  --confirm-trusted-local-boundary --private-root-prompt
python -m scripts.analyst_benchmark c0b2 resume-private --run-id ID \
  --confirm-live --confirm-private-corpus --confirm-private-authority \
  --confirm-trusted-local-boundary --private-root-prompt
python -m scripts.analyst_benchmark c0b2 abandon --run-id ID --confirm-abandon
```

Public create/status/verify are offline and contact no model/private root. In C0B-2B1,
the public stage parser accepts only exact stage `C`; D/F are refused before path or
network access. Private create
performs an authorized metadata/content-HMAC census but no inference. Public run/resume
cannot open a private run; private run/resume are syntactically distinct so every gate is
checked before checkpoint/path imports. No command can change immutable config or offers
force/reset/skip/takeover switches. Private run/resume requires the same root identity
and gates again. The pre-opened-fd mode may replace `--private-root-prompt` in each private
command.

## 17. Outcomes and acceptance

The single state table in §9.2 is authoritative; this section does not introduce aliases
or a second completion vocabulary.

C0B is complete only when:

- 2A offline proofs pass;
- C/D/F stage-local immutable work completes within every class/stage/cumulative cap;
- exactly one model/config passes every hard gate and the applicable §12.1 ranking rule;
- the selection artifact names the worksheet plus all D1/D2 values and artifact hashes;
- public and, if authorized, private leakage gates pass;
- Stage E either reaches `PASS_OPERATIONAL` or is explicitly documented as deferred by
  the HI before any private attempt.

If no unique winner exists, C0B honestly ends `INCONCLUSIVE`. No timing/offload tie-break,
post-holdout retune or private-label fiction is allowed.
