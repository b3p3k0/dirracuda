# Ollama Integration — Lessons Learned

Only what a card actually exercised. Design decisions that have not yet been run
against anything do not appear here until they have.

---

## C0B-1 (gold set, instrument, Stage A, Stage B pilot)

### 1. PyMuPDF 1.28.0 embeds MuPDF 1.29.0, not 1.28.0

The upstream release note for the 1.28.0 tag says verbatim *"Use MuPDF-1.28.0."* The
measured value from `pymupdf.mupdf_version` is **1.29.0**.

The package version and the embedded library version are genuinely independent, and the
changelog is not a reliable proxy for either. CONTRACT.md §10 already required asserting
both; this is the concrete case that justifies it. Preflight must keep checking both,
and no card should ever infer the embedded version from the package number.

### 2. `prlimit` must run inside bubblewrap, not around it

`prlimit ... -- bwrap ...` fails with `Creating new namespace failed: Resource
temporarily unavailable`. The limits land on namespace setup rather than on the parser.

Correct shape: `bwrap <policy> -- /usr/bin/prlimit <limits> -- python <parser>`.

### 3. `RLIMIT_NPROC` cannot bound a parser on a desktop UID

It is a **per-UID** limit counting every process the user already owns — 227 at
measurement. `--nproc=64` therefore makes `clone()` fail before the sandbox exists, and
any value high enough to let the sandbox start is not a bound on anything.

CONTRACT.md §5 asks for a process-count limit. On this platform the mechanism that
actually works is a cgroup `pids.max`. **C3 owns that.** Until then the fork-bomb
controls in force are the PID namespace and the process-group kill — stated plainly
rather than left implied by a limit that is not doing its job.

### 4. A non-zero exit code is not evidence a limit fired

The first Stage A run recorded `rlimit_as_enforced: PASS` on `rc=127`. The child had
never started — the interpreter path did not exist inside the sandbox — so the test was
measuring an exec failure and calling it a resource limit.

Any check whose success condition is "the child died" needs a sentinel proving the child
first came alive. Applied to `check_rlimit_as`, which now prints `ALIVE` before
allocating.

### 5. Do not pass the repository's venv interpreter into a sandbox that excludes the repository

Related to the above and worth stating separately: the sandbox deliberately does not
bind the repo, so `sys.executable` is unreachable inside it. Probes use
`/usr/bin/python3`, which lives under the bound `/usr`. The failure mode is silent —
exec errors masquerading as probe results.

### 6. `resolve()` on a venv interpreter walks out of the venv

`Path("$SCRATCH/probe/bin/python").resolve()` follows the symlink to the system
interpreter, so `parents[1]` becomes `/usr`. A cleanup routine that validates its target
before `rm -rf` correctly refused to delete `/usr` — but only because it validated.
Cleanup targets must be recorded before launching fallible child work, use the
unresolved path, and be validated before recursive deletion. The C0B-1 repair also puts
cleanup in a `finally` path so dependency failures, timeouts and sandbox exceptions
cannot accumulate scratch trees.

### 7. gpt-oss cannot be benchmarked on `/api/generate` with structured output

Measured: `/api/generate` + `format` + `gpt-oss:20b` returns `done_reason=stop` with an
**empty** response and no `thinking` field. The evaluated tokens are unreachable. The
identical request on `/api/chat` returns both `message.thinking` and `message.content`.

Qwen behaves the same on either endpoint, so `/api/chat` is the only endpoint that
serves every candidate. Ported into the C9 client contract.

### 8. A reasoning trace consumes the output budget

`gpt-oss:20b` at `num_predict=1024` never reaches its answer: it exhausts the budget
inside the trace and returns `done_reason=length` with empty content. At 2048 it emits a
~3.4 KB trace plus a ~273-byte answer and stops cleanly.

Erratum E1 required this to be measured rather than assumed. Two consequences:

- output budgets for a thinking model must cover trace **plus** answer;
- `eval_count` under-reports gpt-oss — it counts answer tokens (80) and excludes the
  trace. Token accounting must not treat `eval_count` and `thinking_bytes` as the same
  unit;
- sensitive reasoning text need not be retained to measure that pressure. Count its
  bytes while streaming, enforce the combined output cap, and discard the text.

### 9. The fastest candidate is the biggest one

Warm, on a contended card: `qwen3.6:35b` **9.7 s/call**, `gpt-oss:20b` 38.7 s,
`qwen3.6:27b` 52.5 s. The 35b is a sparse MoE (~3B active parameters); the 27b is
dense. Size on disk predicts neither latency nor residency.

RESEARCH_NOTES had recorded an expectation that gpt-oss would lead on prompt processing.
It does not, on this workload — its prompt is also ~2.8× larger in tokens for the same
document because of the harmony template. Expectations from vendor notes are hypotheses,
not measurements.

### 10. Characters are not tokens, and the guard has to be exact

`prompt_tokens + num_predict <= floor(0.85 * num_ctx)`, evaluated from Ollama's
`prompt_eval_count`. The same 4000-character document produced 662 prompt tokens on Qwen
and 1858 on gpt-oss. A chars-based headroom estimate would have been wrong by ~2.8× for
one candidate.

### 11. A scoring rule can manufacture the result it measures

Two near-misses caught before any live call:

- **Grounding** scored *after* the aggregator drops ungrounded findings would be 100 %
  by construction. It must be measured on raw emitted findings.
- An evidence-span cap of 25 % of the chunk rejected a legitimate
  `Social Security Number: 900-12-3456` quote inside a 137-character fixture. A bound
  meant to stop whole-chunk quoting was instead manufacturing failures on short
  documents. Now 240 characters absolute, plus a 60 % whole-chunk guard floored at
  64-character sources.

Both were found by running the offline self-check against real fixtures rather than by
reading the rule.

### 12. Pre-registration has to be re-pinned when the instrument teaches you something

The protocol was frozen and hash-pinned three times before the first scored call, as
instrument validation corrected `num_predict`, the endpoint, and the envelope sampling
rate. That is legitimate — the freeze binds from the first *scored* request — but it
only stays legitimate because each change is recorded in the document with its measured
justification, and the pin is recomputed.

### 13. A leakage allowlist must be baseline-aware

The worktree already held unrelated untracked work (`docs/dev/kbd_ctrl_improve/`). A
repository-global "every changed path must be allowlisted" rule would either false-fail
on it or tempt the implementation to tidy someone else's files. The scanner records an
immutable baseline first and inspects only task deltas, hashing task-owned paths only.

### 14. Test the code, not the prose

Three guardrail tests failed on their first run by matching docstrings and, worse, by
flagging the leak scanner's own detection pattern for the private corpus root — a
scanner has to name what it searches for. Guardrails that grep raw file text produce
false positives that erode trust in the guardrail; these now walk the AST and exclude
docstrings, with the scanner explicitly exempted and separately tested.

### 15. A model can quote reliably and still count characters badly

Stage B failed every cell on grounding, two of them at exactly 0.000, while schema
validity was 1.000. An exact zero across independent models is an instrument signal.

The scorer consumed 679 findings. The raw sink retained 675 of them and all 675 quoted a
bounded exact substring of their source. A successful four-finding retry was scored but
not retained, so the honest conservative result is 675/679 = 99.4%, not an unsupported
claim about all 679. Of the retained findings, just 5.9% of model-supplied character
offsets were correct.

Two separate lessons:

- **The gate was stricter than the contract.** CONTRACT §7 verifies that *the quote is
  an exact substring*; the offset is provenance. The protocol had made offset equality a
  grounding condition, so the gate measured character-counting and reported it as
  fabrication. A gate must be traceable to the contract clause it enforces.
- **Models cannot produce reliable character offsets.** This is a real design
  constraint, independent of how the gate is resolved: C1's aggregator must locate the
  quote itself and treat any model-supplied offset as an untrusted hint.

The result was reported as measured and the correction proposed for review rather than
silently applied. Loosening a gate after seeing the data it produced is precisely what
pre-registration exists to prevent. The contract-aligned rule and conservative lower
bound were accepted only after an independent audit, focused regression tests and HI
approval.

### 16. A paired metric must not depend on input order

Stage B listed injection documents before their clean twins, while the runner compared
an injection only if its twin was already cached. The result was a perfect-looking zero
because none of the 24 intended comparisons executed.

Pair by stable document identity after both responses exist, assert the expected pair
count, and make missing/unscorable pairs an explicit result. A zero without a coverage
count is not evidence of resistance. C0B-1's injection result is INVALID / UNMEASURED.

### 17. Persist the response that was actually scored

On a successful schema retry, the scorer used the second response but the raw sink wrote
the first invalid response. That broke reproducibility and created a mismatch between
679 scored findings and 675 retained findings.

The accepted response and attempt metadata must be written together, and a focused test
must prove that the artifact can reproduce every aggregate counter. Related reporting
discipline: **262/264** responses were valid on first pass; **263/264** were eventually
valid after one retry. Those are different metrics and must not be collapsed.

### 18. Count transport preflight and close the ledger state

The pilot charged 332 calls, but its two read-only preflight requests happened before the
ledger existed. The honest run total is therefore at least 334, still below the 400 cap.
The stored ledger also remained `running` after normal completion.

Construct the ledger before the first Ollama request and make terminal state transitions
explicit. A call cap cannot be audited if some requests live outside its accounting.

### 19. A resumable label requires a resumable implementation

The shared-GPU policy declared backoff, checkpoint and resume, but the C0B-1 pilot runner
could only mark a pause or skip an interrupted call. No interruption occurred during the
133-minute run, so the missing path did not alter its data; it still means C0B-1 did not
validate resumability. C0B-2 must implement and exercise it before a long or private run.

### 20. Generated data still obeys the file-size gate

The first deterministic manifest was 3311 lines, above the repository's explicit
1700-line pause threshold. Pretty-printing each field onto its own line made a data
artifact look like an oversized module. Keeping root metadata readable and one document
record per line reduced it to 176 lines without changing any of the 166 fixture payloads.
The generator test now enforces the limit so regeneration cannot reintroduce it.
The commit whitespace gate also exposed a trailing space produced by the boundary filler;
that was fixed in the generator and covered by a corpus-wide regression check.

### 21. Fixed names in shared temporary directories are artifacts, not conveniences

The original Stage A result used `/tmp/c0b1_stage_a.json`, inherited the process umask,
and overwrote the same pathname on every run. That permits collisions and symlink
redirection and can expose diagnostic metadata to other local users. Retained temporary
artifacts now use an exclusive random name and explicit 0600 permissions; tests assert
both properties. The old artifact and four test-only CLI captures were removed after
their exact paths, ownership and file types were verified.

## C0B-2A (offline worksheet, plan, checkpoint and executor foundation)

### 22. Green tests do not prove a persisted invariant

Successive adversarial reviews reproduced fail-open paths in decision ordering, class
budgets, restore provenance, finalization, preflight, cancellation, exact model binding,
response-state mapping and per-finalist health evidence while the focused suite was
green. Each fix moved the rule into the transactional boundary and added the failing
case as a regression. For durable systems, test the forbidden state transition directly;
do not infer it from a happy-path end-to-end result.

### 23. A capability result is not a policy choice

WAL and DELETE both passed the canonical mergerfs process-crash, lock, integrity and
resume probe. DELETE+FULL was still the safer choice for this serial workload because
the measured SQLite 3.46.1 falls within SQLite's current WAL-reset advisory. The probe
answers "can this mode work here?"; workload and current upstream risk decide which
passing mode to use. No power-loss claim was made.

### 24. Read-only SQLite inspection must remain part of the lock model

SQLite's `immutable=1` promise is unsafe when another actor can change the database:
SQLite may omit locking and change detection. C0B-2 inspection uses `mode=ro`, refuses
unexpected journal sidecars and keeps mutation, restore and recovery behind the same
global execution lock. "Read-only" describes intent, not isolation.

### 25. Restore identity records are necessary, not sufficient

A valid SQLite file is not enough. The existing helper creates a unique bounded storage
identity on the same canonical root and records the origin both beside and inside the
database, which detects some substitution and partial-copy cases. It does not yet define
disaster-recovery authority or carry every snapshot referenced by inherited receipt rows.
Production restore therefore remains unexposed and held pending a self-contained evidence
bundle, descriptor-pinned copy, atomic publication and crash/retry contract.

### 26. Adaptive acceptance is a plan, not a derived count

A provisional Stage-F winner does not become `SELECTED` from caller-supplied totals.
The exact D1/D2 configuration must be frozen into a separate C44 acceptance sub-plan,
its document identities must equal the original C44, and the final artifact must chain
to its completion. Persist exact identities first; derive counts only as a verification.

### 27. Python coercion can reopen a closed response matrix

The executor initially compared `accepted` with Boolean equality. Python makes
`1 == True` and `0 == False`, so integer values crossed a boundary described as strict.
The transport response boundary now requires exact Boolean and string types, exact
path-specific outcomes, and rejects retry/safety labels returned as ordinary responses.
Retry and safety states can enter only through the branches that atomically update their
backoff or terminal state.

## C0B-2B1 (bounded public Stage-C path)

### 28. Freeze the whole adaptive executor before collecting its first result

The first Stage-C design pinned Git HEAD and the task tree, then deliberately held D/F
implementation until the Stage-C boundary. Those rules cannot both hold: implementing D
changes the very identity a survivor must resume under.

The complete public C/D/F implementation must be reviewed and committed before live run
creation. Stage decisions and plans may remain conditional, but the code that interprets
them cannot be written after seeing earlier outcomes. This is both a resume requirement
and a stronger preregistration boundary.

### 29. Retry entitlement comes from answered history, not the latest call class

A schema retry can itself time out or become an orphan. Its replacement is correctly
charged to `transport_orphan`, but that class does not erase the first schema-invalid
answer. When the replacement returns the second HTTP-accepted invalid answer, the work is
complete-invalid; it must not receive a third schema attempt.

The terminal rule now counts bounded invalid answers for the work item. Transport class
still controls the ledger charge, while answered history controls schema-retry
entitlement.

### 30. A derived result needs an independent equality check

Exact hashes prove that stored bytes did not change; they do not prove that counters,
failure reasons or a selected worksheet agree with the underlying attempts. Adversarial
tests produced shape-valid but contradictory aggregates and selections.

Stage C now re-derives attempt summaries, cell facts and the model decision before either
the aggregate or boundary decision can freeze. `done_reason=length` is counted across all
HTTP-answered attempts, so an invalid length-truncated response cannot disappear behind a
later valid retry.

### 31. Durable completion can have more than one artifact

The database boundary and its verified backup cannot commit in one SQLite transaction.
Originally, a snapshot failure after the boundary left a correct final state but no way
to retry the required backup because boundary resumes were read-only.

Snapshot completion is now idempotent: under the global lock, a later boundary/terminal
call accepts an existing verified same-state snapshot or creates the missing one without
mutating checkpoint evidence, claiming an invocation or contacting Ollama. The signal
guard likewise starts before recovery, and a signal during the transactional invocation
claim rolls that claim back.

### 32. Freeze code early; activate evidence branches late

Reproducibility requires the complete C/D/F interpreter to share the run's original
source identity, but it does not require later-stage work to exist before the evidence
authorizes it. The durable model therefore separates immutable plan bytes, one-way plan
activation and active-stage state. Code is frozen before `create`; D, later F seeds and
acceptance become claimable only from their persisted parent decisions.

The same distinction applies operationally: a shared GPU may extend calendar time or
cause a resumable resource pause, but neither changes plan identity nor becomes model
quality evidence.

### 33. Exact keys are not an exact contract

A list of field names still leaves incompatible choices for types, nullability, ordering,
identity domains and derived arithmetic. The public C/D/F protocol now has a separate
normative schema catalog so the implementation cannot invent those choices after seeing
results. Every aggregate must be independently re-derived from attempts and immutable
fixtures; strict shape validation alone is insufficient.

## C0B-2B2 (shared public phase, checkpoint and evidence controls)

### 34. A durable control is a scheduler barrier

Recording a valid context probe or cancellation event does not enforce the protocol if
another scored item can run immediately afterward. The checkpoint now blocks scored
work until the required context, stream-owned cancellation and delayed health sequence
completes in order. Eligibility is checked before charge or transport.

### 35. Public terminal state and evidence are one obligation

A terminal label without its required artifacts is not a recoverable public result.
Public low-level terminal transitions now fail closed; the runtime commits the terminal,
artifacts and budget callback effects together. Callback denial, absence or exception
rolls the whole invocation back, while private primitive semantics remain unchanged.

### 36. Additive migration must validate before it mutates

Column presence did not prove that a pre-existing table had the required constraints,
and adding missing tables before rejecting a partial schema left a failed open with side
effects. B2 inspects the whole checkpoint schema first, rejects partial or incompatible
DDL, and only then performs the additive migration.

### 37. A checksum does not pin a path

A byte-identical file can replace a checked path and retain the same digest. Backup
validation now holds directory and file descriptors through the receipt commit and
rechecks device/inode, content, SQLite semantics, lineage and the live anchor. This
closes the check/use gap rather than merely detecting changed bytes.

### 38. Re-derive meaning across records

An attacker or bug can coherently alter a record and recompute its hash. Parent
decisions, completion decisions and terminal-specific failure identities are therefore
checked against the stage, active plan, predecessor aggregate and checkpoint state that
give them meaning.

### 39. Activate adaptive pairs atomically or keep them closed

Activating one later Stage-F seed before the other creates a durable half-state, while
acceptance without its typed final-F evidence can attest the wrong result. B2's generic
API rejects both later seeds and acceptance without mutation. B4 owns their evidence,
paired seed activation, cursor transition, replay behavior and crash/half-state tests;
until that complete API exists, those branches remain held.

## C0B-2B3 (public creation and Stage D)

### 40. Durable creation needs an internal state

Creating the directory directly as `PREPARED` made a crash indistinguishable from a
usable run. Public creation now durably writes `INITIALIZING` in a unique owner-only
directory, atomically promotes it without replacement, and only then commits the nonce
key, manifest, C plan/work and `PREPARED` in one transaction. Recovery may delete only
the exact evidence-free internal run; a prepared run is preserved and only its initial
snapshot may be retried.

### 41. Recovery must prove absence, not recognize a happy shape

A partial SQLite schema can contain evidence even when another expected table is absent.
Cleanup therefore checks every evidence table that actually exists and refuses any row,
while promotion and deletion bind the named directory/database to the inode opened by
the checkpoint. Owner identity alone is insufficient: key-bearing directories and files
must remain exactly 0700 and 0600.

### 42. Scan decoded answer values, not JSON spelling

A fence marker encoded as `\u0046...` is the same Unicode value as a literal `F...`, but
a raw serialized substring check misses it. Stage D decodes JSON, preserves duplicate-key
values for the scan, recursively visits value scalars and compares Unicode NFC. Serialized
bytes remain useful for canonical storage, not semantic injection detection.

### 43. A decision hash identifies its checkpoint record

The D3 payload hash is not its durable parent identity. D4 must hash the decision ID,
stage, owning plan, aggregate, activation and canonical value exactly as stored. Final D
validation rebuilds both the current phase and its D3 predecessor from attempts before
Stage F can activate; matching record hashes without matching evidence are insufficient.

### 44. Recovery traffic can change the condition being measured

A generic warm-up request may load the right model with the wrong context allocation.
When a D3/D4 context observation is pending, recovery now replays that candidate's exact
trigger configuration and runs `/api/ps` immediately afterward. The pending candidate
has priority over unrelated shared-GPU obligations; recovery output is charged control
evidence and never scored work.

### 45. Safe cleanup may end at quarantine

Validating an inode and then unlinking its name leaves a final same-UID swap window. The
creation path now atomically moves the exact pinned directory out of its public name,
fsyncs the parent, and retains the owner-only contents. Automatic destructive garbage
collection needs a separate protocol; benchmark recovery must not delete a replacement
introduced after validation.

### 46. Attempt identity includes its historical owner

Stable IDs and request hashes did not stop a valid-looking later-phase control from being
backdated, or one retry group from straddling a phase transition. Historical validation
now rebuilds every phase from its parent evidence and activation, assigns each attempt to
its real invocation window, and requires one phase/ordinal owner across a retry group.
Well-shaped records without reachable ancestry are provenance failures.

## C0B-2B4 (Stage F)

### 47. A frozen plan is not execution authority

Stage F freezes both later seed plans before deciding which candidate groups qualify.
Only the canonical activation record authorizes work or controls. Scheduling, backoff and
recovery therefore enumerate the activated groups, not every row present in a frozen
plan; an inactive model cannot create traffic or block the active phase.

### 48. Crash recovery needs guards on both sides of mutation

A pre-recovery census proves the starting namespace, but orphan conversion legitimately
changes it. The executor now validates structure before recovery, then re-runs the strict
owner census after recovery and before transition or invocation claim. A final census
after claim catches a poisoned transition without permitting transport.

### 49. Expected rows do not prove an exact namespace

Re-deriving every expected work and control still misses valid-looking aliases, foreign
attempts and premature terminal evidence. B4 treats plans, registries, controls, attempts,
events, decisions and artifacts as complete namespaces: every durable row must have one
reachable owner, and every owner must have exactly its derived history.

### 50. A durable delay is evidence, not a sleep suggestion

Cancellation health is valid only when its not-before value is derived from the exact
cancelled attempt. Crash windows may add empty invocations or ordered preflight prefixes,
but they cannot insert scored work, recovery or another planned control. Every intervening
timestamp must also satisfy the same durable lower bound. Health owns the first
non-preflight attempt; after that attempt exists, mandatory resource recovery may precede
a health retry so the scheduler cannot deadlock at the sixth transport failure.

### 51. Validate hot-path plans once per invocation

Stage F can contain more than a thousand request rows. Rebuilding and revalidating the
whole plan for every dispatch creates a quadratic runtime cost. B4 proves the namespace
once, builds an immutable request index once per invocation, and still checks each lookup
against both work ID and request hash.

## C0B-2B5 (complete public offline gate)

### 52. Every enforcement surface must share one allowlist

The public task-tree pin had grown through D and F, while the executable leak scanner
still recognized only C0B-1 files. Both controls were individually strict but disagreed
about the protected tree, so the required leak command rejected legitimate B5 changes.
The reviewed complete-public path set now drives dirty-tree refusal, task-tree hashing
and the legacy scanner; the contract test checks exact docs/code equality.

### 53. Name semantic source pins for the whole adaptive run

A Git tree hash detects bytes, but a field labelled generation or detector identity must
describe what it claims. The public header now separately binds the C/D/F schemas and
scorers plus the full model, worksheet, chunk, overlap, context, output-budget, seed,
thinking and keep-alive factor domain. Stage C can no longer freeze a C-only semantic
identity and later execute D/F under an unlabeled extension.

### 54. Canonical storage order is not domain order

Canonical JSON sorts object keys. Rehydrating stored `category_recall` or
`category_metrics` mappings with a plain JSON load therefore produced alphabetical
order, while D/F strict validation requires the frozen category order. D4 and the stored
F aggregate loaders now restore domain order at their persistence boundaries before
strict validation; canonical bytes remain unchanged.

### 55. Owned cancellation is a resumable boundary

Stage F deliberately returns after closing its cancellation-probe stream. The delayed
health request belongs to a later resume invocation so a crash cannot blur cancellation
and recovery evidence. End-to-end expectations must observe `CANCELLED_UNVERIFIED`, then
resume through the two-second health barrier before later seeds activate.

### 56. A terminal operator action still needs evidence

`abandon` was confirmation-gated but held, even though `ABANDONED` was part of the frozen
public state machine. It now takes the global lock, freezes failure evidence, artifact
and state atomically, writes a verified receipt, rejects unsafe states and is idempotent
without constructing transport.

### 57. A read-only facade must satisfy transitive validators

Stage F's backup validator reused the authoritative Stage-D owner re-derivation, but its
minimal read-only checkpoint facade omitted D's `work()` lookup and checkpoint `path`.
The gaps appeared only after later-seed activation asked for a new receipt. Read-only
adapters now expose the full interface required by every validator they call, and a
focused regression binds the work-state/path lookups without granting mutation methods.

### 58. A decision digest and its parent are different lineage edges

The F acceptance plan is owned by the provisional-selection digest, but that
provisional decision is owned by the frozen F master. Backup validation must verify both
edges explicitly. Substituting the preceding seed-plan hash for the decision parent
rejects an otherwise valid terminal checkpoint after all scoring has completed.

### 59. Global file-read telemetry includes runtime dependencies

Monkeypatching `Path.read_text` and `Path.read_bytes` observes interpreter and library
reads as well as benchmark inputs. A privacy assertion must reject unexpected reads in
the user's home while allowing system dependencies; otherwise a fully successful public
run fails on unrelated runtime files. Keep the separate fail-closed `get_paths()` guard
so any attempt to resolve canonical private Dirracuda data still aborts immediately.

### 60. End-to-end does not mean forcing pre-dispatch failures onto the wire

A single requirement combined a real bounded-transport happy path with "every terminal
branch," even though filesystem, uncharged-budget and abandon terminals must occur before
HTTP dispatch. Freeze a closed compositional matrix instead: one real end-to-end
transport spine, exact scorer/runtime proofs for every quality reason, real bounded-wire
proofs for transport-originated failures, and explicit zero-transport assertions for
pre-dispatch requests; abandon alone forbids client construction. This preserves the
boundary each test is meant to prove without creating slow duplicate benchmark prefixes
or weakening terminal coverage.

### 61. A scan mode must own one exact scope

Unioning a legacy allowlist with a newer feature tree made the current public leak command
accept paths outside the reviewed B5 boundary. Keep the C0B-1 scope available separately,
but make each protocol mode equal—not merely contain—its source-pinned set: 48 paths for
C0B-2 and 58 for C0B-3. Select the scope only from the exact protocol identity, retain the
legacy default, reject unknown paths, and test both equalities so a newer gate cannot
silently widen an older one.

### 62. Path metadata is not a safe read primitive

Checking `is_file()` and `is_symlink()` before reopening a path leaves a name-swap window
that can follow a replacement symlink. Sensitive source and leak scans now open with
`O_NOFOLLOW`, validate an owner-controlled regular file through `fstat`, read from that
descriptor, then bind the still-named inode to the captured inventory. Fail closed when
the platform or identity cannot provide that guarantee. Apply the same primitive to the
owner-only baseline and raw-response inputs, with explicit read caps, so the leakage gate
does not reintroduce the race while loading its own evidence. The caller must supply the
lexical authority boundary: repository reads are rooted at the repository, while external
artifacts are rooted at their protected parent. Walk and retain every directory descriptor
from `/` without following symlinks, then rebind each component name after the read; final-
component safety alone does not stop an intermediate-directory swap.

### 63. Every operator mutation revalidates source identity

An idempotent terminal command still changes durable evidence on its first invocation.
`abandon` therefore revalidates the frozen Git/task-tree pins while holding the global
lock and before any artifact or receipt write. The same rule applies when Stage C repairs
a missing boundary or terminal receipt; only `BLOCKED_PROVENANCE` may remain receiptable
after source drift because that drift is its frozen evidence. Drift regressions compare
checkpoint bytes and keep mutation, receipt and transport seams untouched.

### 64. Operator cancellation wins a simultaneous transport failure

A first signal may close a response at the same instant the bounded parser classifies a
safety or provenance failure. Every scored, recovery and control dispatch must check the
operator event before freezing the competing terminal so the charged attempt becomes
`CANCELLED_UNVERIFIED` and the run remains resumable. Test both failure types at every
dispatch seam; success and retry paths alone do not prove this race.

### 65. Safe CLI failures are content-free

Exception messages and type names are not safe UI data: filesystem, checkpoint and
transport errors can contain absolute paths or attacker-controlled detail. The public
benchmark CLI maps an unexpected failure to one stable `operation_failed` token and
tests with a sentinel private path. Detailed diagnosis belongs in explicit, separately
designed local evidence—not generic stderr interpolation.

### 66. A checkpoint backup is an evidence set, not one SQLite file

Restoring a post-receipt database alone preserves rows that refer to snapshot files in
the original physical run directory. Moving only the database therefore creates a
shape-valid but unverifiable history. B5 terminal branching uses a test-local Online
Backup rewind at the same immutable path, where inherited attachments remain available;
it does not claim production restore. A future restore card must freeze receipt/anchor
authority, package all referenced attachments, pin source descriptors through copy and
publish the complete verified set atomically.

### 67. Socket timeouts are not a total request deadline

Requests defines its read timeout as the wait between server bytes, and urllib3
explicitly warns that even its `total` timeout does not bound a complete streaming
response. A peer that blocks before headers or sends one byte periodically can therefore
outlive a nominal request wall. Keep connect and idle-read timeouts, but enforce the
frozen total wall from the caller around the complete blocking request/body operation.
One global worker permit prevents abandoned requests from accumulating; cancellation is
checked before the deadline, late results are discarded and closed, and the permit is
released only when the worker reaches final teardown. See the current
[Requests timeout guidance](https://requests.readthedocs.io/en/latest/user/advanced/#timeouts)
and [urllib3 timeout semantics](https://urllib3.readthedocs.io/en/stable/reference/urllib3.util.html#urllib3.util.Timeout).

### 68. A signal handler publishes intent; it does not perform cleanup

Calling `Response.close()` or even taking a transport lock from the first-signal handler
can freeze the main thread before the caller-owned loop observes operator intent. The
first signal now only records that intent. The normal caller loop observes it within its
poll interval and initiates one response-scoped asynchronous close. Repeated cleanup
requests cannot create duplicate close workers, and the request worker retains the
global permit until that close reaches final teardown. Keep the second-signal force
behavior independent so an operator still has an escape hatch. A `threading.Event` is
not the signal-context primitive: `Event.set()` takes its condition lock and can
self-deadlock if the signal interrupts that lock's owner. Publish first/second signal
intent with plain flag assignments; normal execution exposes that flag through the
event-compatible view and performs any condition notification outside signal context.

### 69. Every stage entrypoint normalizes internal cancellation

Recovery raises an internal `InvocationCancelled` after it durably records
`CANCELLED_PENDING_RESUME`. Catch that control-flow exception at every public stage
entrypoint and return the persisted resumable state. Missing the catch in only one stage
turns a correct checkpoint transition into a generic CLI `operation_failed`, confusing
the operator and breaking C/D/F parity even though no model call was charged.

### 70. Atomic publication is a runtime-filesystem capability

SQLite and lock probes do not prove `renameat2(RENAME_NOREPLACE)`. The canonical Analyst
path lived on mergerfs and passed the earlier database probe, but real checkpoint
promotion failed with `EINVAL`; the same primitive passed on ext4 and tmpfs. Never fall
back to ordinary rename, which may replace an existing target. Probe the exact runtime
path before creation. On mergerfs-based hosts, preserve the canonical application path
with an owner-only persistent bind mount backed by a filesystem that passes the atomic
primitive. See the current [Linux rename documentation](https://man7.org/linux/man-pages/man2/renameat2.2.html)
and [Python rename behavior](https://docs.python.org/3/library/os.html#os.rename).

### 71. Bound control responses by their documented payload class

A shared structural cap can reject legitimate metadata while appearing comfortably
inside the wire-size limit. Current Ollama returned 459 tensor rows from `/api/show`
despite `verbose:false`: 69,543 canonical bytes and depth 5, but 4,109 decoded nodes.
Keep model answers and ordinary controls at 4,096 nodes; give only show an 8,192-node cap
under the unchanged raw-byte, canonical-byte and depth limits, then discard tensor data
from durable evidence. A live preflight terminal is immutable: receipt it, correct the
contract and start a new source-pinned run rather than loosening or relabelling history.

### 72. Measure the complete compatibility domain before choosing headroom

The first show-only correction measured `gpt-oss:20b` at 4,109 nodes and selected 8,192,
but the next frozen candidate required 10,546 and the third required 11,318. That made a
technically correct bound operationally incomplete. Before freezing compatibility
headroom, inventory every member of the exact candidate/configuration domain and choose
one reviewed ceiling above the measured maximum. Preserve each failed preflight as a
receipted terminal; never revise its history. The complete set now supports a show-only
16,384-node cap while every scored/general path remains at 4,096.

### 73. Context allocation and context quality are separate gates

The canonical D3 probe proved that Ollama allocated 16,384 tokens for
`qwen3.6:27b`; all 66 chunks completed, every retained finding was grounded, every
category met 6/6 recall and all 12 boundary documents passed. One negative document still
produced a false positive. The context worked; the candidate did not clear the quality
gate. Keep runtime-allocation evidence separate from model-quality evidence so a reason
such as `no_d3_context_survivor` is not misread as a capacity failure.

### 74. An adaptive-stage survivor is not a product selection

D1 continued both Qwen candidates at a smaller output budget. D2 then continued
`qwen3.6:27b` at the largest passing chunk. D3 eliminated that same candidate, producing
the preregistered terminal `INCONCLUSIVE`. Intermediate tuning values are useful evidence,
not defaults to salvage after a later gate fails. Do not enter Stage F or private Stage E,
promote a runner-up, or loosen a threshold after seeing the result. A different candidate,
prompt, worksheet or gate belongs to a new reviewed plan and source-pinned run.

### 75. Grounded does not mean correctly classified

An exact source quote proves the model did not invent the cited text; it can still assign
the wrong category. That distinction produced C0B-2's single negative-document false
positive. For an assistive, no-action tool, risk tolerance belongs in a numeric
preregistered gate plus visible suggestion labels, source context and explicit human
accept/reject—not in a vague “human in the loop” claim. Changing that tolerance after a
terminal creates a new experiment and source identity; it never rewrites the old result.

### 76. A policy migration needs a closed compatibility namespace

Changing one numeric gate is not only a scorer edit. The run header, every
policy-sensitive D/F owner, terminal result, completion row, backup rederiver and
read-only verifier must dispatch from one exact stored discriminator. Run-ID prefixes
are display data, and absent legacy fields remain meaningful; optional policy defaults
would let mixed artifacts cross the boundary. Guard mutating commands with a read-only
header check before opening the checkpoint writable, then repeat the check after open.
Prove both mismatch directions and the legacy/current 0/1/2 boundaries without model or
network calls.

### 77. Exact-output tests are part of a compatibility contract

Making `status` and `verify` explicitly report the resolved protocol/policy is required
behavior, even for legacy checkpoints. Exact-dictionary assertions and mocked checkpoint
entry tests must change with that public shape. Keep those test paths in the reviewed edit
set and frozen task tree; otherwise an allowlist intended to prevent scope creep can block
the tests that prove the promised compatibility. Verify real legacy checkpoints before
and after the change and compare their database SHA-256 values, not only their reported
state.

### 78. A bounded miss is a product decision, not a hidden test exception

The canonical C0B-2 run exposed one grounded but incorrectly classified negative
document. For Analyst's recommendation-only role, the HI accepted that review cost: a
useful partner with explicit human adjudication is preferable to indefinitely waiting for
an unmeasured claim of perfection. Encode that judgement as prospective 0/1/2 numeric
boundaries, distinct policy/version identities and visible `suggested/unreviewed` output;
never special-case the observed document, rewrite the old terminal or relax unrelated
schema, injection, grounding, provenance, privacy or final-acceptance controls. Production
accept/reject counts are monitoring signals and rebenchmark triggers, not silent retuning.

### 79. Never manufacture a missing pre-task baseline after the task

A baseline created after implementation cannot prove what was present before the first
edit. If the genuine artifact is unavailable, say so and use a separately approved,
one-time substitute: compare the frozen parent commit with the complete worktree path set,
exclude only a previously sealed unrelated delta, then descriptor-read and scan every
changed task file against the complete owner-only raw-response inventory. Record exact
counts and require zero hits. Future cards still create their baseline before editing.

### 80. A generic terminal still inherits its stage history

Changing an active Stage-D run to `ABANDONED` does not erase the adaptive decisions that
led to its boundary. Validate and re-derive every completed attempt plus the final
selection before the state mutation, then repeat that reconstruction when verifying the
backup. Schema validity and internally consistent hashes are insufficient: a mixed-policy
history can be coherently rehashed. Apply this rule to every backup from an active-D
state, including generic failure terminals, rather than only D-specific outcomes. Keep
the frozen `BLOCKED_PROVENANCE` exception narrow: validate its stored current-family
lineage and exact failure artifact structurally, but do not demand the nonce key whose
drift may be the failure being receipted.

### 81. A structured schema does not express every semantic constraint

The worksheet JSON Schema bounded shape and types correctly, but pairwise uniqueness over
`category` plus normalized `quote` remained an application rule. The prompt never stated
that rule, while the public fixture legitimately repeated identifiers. Keep prompt,
validator and fixture semantics aligned. When a deterministic local transform can safely
remove redundant grounded data, count and bound it instead of calling the whole answer
unusable.

### 82. An identical deterministic retry is evidence collection, not repair

C0B-3 retried the same prompt, nonce, options, source and seed after semantic invalidity.
Both responses were byte-identical. A local normalization needs no model call; a real
model repair needs a new, frozen error-specific request identity. Do not spend a retry
budget hoping fixed deterministic inputs change.

### 83. After holdout inspection, say what the next run can prove

Stage-F seed 1 and its failing synthetic document were inspected during the postmortem.
The documents are no longer untouched. New responses at seeds 17 and 20260804 can confirm
the prospective prompt/normalizer under different frozen generation conditions, but they
cannot restore a pristine document holdout or support a population-accuracy claim. Freeze
that narrower question before contact. Preserve the original complete-corpus gate with a
fresh C44 acceptance lane instead of quietly reducing the acceptance surface.

### 84. Schema-valid pieces do not prove one run lineage

Strict artifact models can each pass while their protocol, nonce, plan or owner comes
from another run. Validate the complete graph from one header and master plan at every
publish, resume, terminal and backup boundary. Hash presence is not semantic ownership.

### 85. Canonical storage must round-trip through its schema

Canonical JSON sorts mapping keys. A validator that depends on insertion order can
accept an in-memory aggregate and reject the same bytes after storage. Validate exact key
sets and meaning, then test real constructor output through store, read and finalize.

### 86. Durable attempts and derived events have separate crash boundaries

Precharge and response persistence can commit before their runtime event or aggregate.
Resume must validate every event that exists, reconstruct only missing events from the
durable attempt history, then require completeness before contact. Never repeat a valid
context or cancellation observation just because its derived artifact was not stored.

### 87. Control validation belongs inside the attempt outcome

Do not record a context or health response `RAW_VALID` before checking every field needed
to build its evidence. A later parse failure otherwise contradicts the durable state and
can strand the run. Validate first, then persist one coherent outcome and terminalize a
bounded safety failure when required.

### 88. Backup verification must replay meaning, not only hashes

Corruption can coherently change a response and its history while leaving stored
aggregates untouched. Recompute controls, lane metrics and acceptance from attempts for
both the source checkpoint and snapshot. Cache the immutable attempt ledger once;
re-reading it per work item creates an avoidable quadratic hot path.

### 89. A stage pause owns the activation boundary

Completing a lane does not authorize the next lane. Persist the aggregate, pause, then let
the verified resume own the cursor transition and activation. Require the prerequisite
aggregate to pass semantically before either artifact exists or any later contact occurs.

### 90. Closed retry budgets need closed owner catalogs

A global call cap does not enforce one schema retry per lane or three ordered preflights
per invocation. Bind attempt IDs, owners, request hashes, classes and histories to the
exact plan. Check the per-lane retry partition and the version-tags-show barrier during
read-only verification, not only in the live scheduler.

### 91. Capability hashes require identical probe preimages

Hashing successful facts is not enough when creation and revalidation enumerate different
checks. C0B-4 created a one-mode filesystem capability but inherited a two-mode
revalidator, so an unchanged ext4 mount was guaranteed to fail. Freeze the probe mode set
as part of the contract, pass it explicitly at both boundaries and prove real
create-to-revalidate parity. Preserve any zero-call terminal; correct it prospectively
under a new source identity.

### 92. Preserve a real baseline across a commit; do not reconstruct one

A corrective card began after its parent implementation commit, so the genuine pre-task
baseline had an older `HEAD`. Creating a new file after editing would erase the evidence
the baseline exists to preserve. Permit only a directly proven, non-merge task commit,
add all of its net paths to the scan and keep exact allowlist enforcement. Anything more
complex needs a fresh baseline before the next edit. Scan immutable committed blobs and
dirty overlays separately; otherwise a safe overlay or deletion can hide content already
preserved in Git history. Git replacement refs can also substitute safe bytes for a named
object without appearing in worktree status. Disable replacements for every Git ancestry,
diff, tree and object read, then rehash returned blob bytes against the tree object ID.

### 93. A document cap is not a human-review workload cap

One affected negative document may contain several false-positive rows. If the product
rationale is a bounded number of human dismissals, freeze both limits independently:
affected documents and retained findings on negatives. Test the exact pass/fail
boundaries for each and never let one compensate for the other.

### 94. Post-observation policy changes need fresh generation conditions

C0B-4 measured two false-positive documents at seed 17 before the HI selected a broader
review budget. That seed remains useful descriptive evidence but cannot qualify the new
rule without circularity. Freeze new seeds before contact, keep the prior terminal
immutable and describe the next run as operational-policy confirmation rather than an
untouched holdout or population-accuracy estimate.

## C0B-5B (offline implementation accepted)

### 95. Review-cost limits need two independently derived units

Counting affected documents alone can hide several suggestions that a human must reject
inside one document. Rebuild both affected-document and retained-row counts from the
normalized finding union, enforce both limits independently and carry the same evidence
into the derived public summary.

### 96. Parent proof and child proof are separate jobs

A valid child header does not prove its frozen inputs are still valid. Verify both
C0B-3/C0B-4 checkpoint/snapshot pairs read-only before child creation or contact, replay
the observed C0B-4 terminal from its attempts, then separately replay the C0B-5 source
checkpoint and snapshot at terminal verification. Shared hashes are references, not a
substitute for either semantic replay.

### 97. Resume authority belongs in durable state

Persist the request charge before dispatch and allow only one in-flight request. Commit a
lane aggregate and its pause before returning; a later verified resume owns the next
activation. Crash recovery may fill a missing derived event from the durable attempt, but
must never repeat the call or overwrite an existing event.

### 98. Shared-GPU contention is a scheduling outcome

A busy GPU changes elapsed time, not model quality. Keep dispatch serial and turn a
retryable resource failure into `PAUSED_RESOURCE`; preserve the plan and resume later.
Do not score contention as a miss or silently start the next lane.

### 99. Size guardrails protect shipped code first

The HI clarified that the mandatory production-code size limit does not automatically
block test or benchmark instruments whose audit trail is inherently larger. Those files
still need explicit size reporting and focused review. C0B-5 needed no exception after
review: its largest instrument is 1699 lines, and no shipped production file changed.

### 100. Source-sealed regressions need a frozen workspace and a realistic window

The Analyst-wide run took 88 minutes, almost entirely in inherited fake-terminal replay
matrices. Editing while those tests run changes their declared source seal and creates a
false failure, so the workspace must stay frozen. The run found only two missing module
disposition notes; the exact guardrail and focused suites passed after that correction.
Optimize the inherited replay tests on a dedicated card rather than weakening this gate.

### 101. A read-only verifier must not make its caller read-only

C0B-5 semantic replay set `PRAGMA query_only=ON` on the writable checkpoint connection.
Replay passed, but the following receipt insert failed deterministically. Connection-local
state is still a side effect: restore it in `finally` or replay through a separate pinned
read-only connection. Tests must exercise the default verifier through the actual write.

### 102. Safe errors still need closed diagnostic codes

The sole C0B-5 child failed before its health call, but the CLI correctly discarded the
exception text and persisted only `provenance_identity_failure`. That protected content
while losing the exact internal boundary. Persist a predefined, content-free origin code
such as `health_precharge_guard`; never persist arbitrary exception strings.

### 103. Test doubles must reject calls the real boundary rejects

C0B-5's in-memory checkpoint allowed `list_attempts(attempt_id)` while the SQLite
checkpoint exposed only `list_attempts()`. That permissive signature hid a deterministic
failure until the sole live child crossed the cancellation-to-health boundary. Keep
double signatures exact and add at least one live-shaped test through the real durable
implementation for every resume or publication handoff.

### 104. A payload hash and a database-row hash are different identities

C0B-6 replay correctly verified the complete C0B-3 decision-row hash, but final scoring
later compared it with a hash of only the decoded JSON payload. Name identity hashes by
their exact preimage and carry verified identity forward instead of recomputing another
object at the consumer.

### 105. Canonical mapping order is not domain order

Canonical JSON sorts object keys. A legacy validator treated insertion order in a
category mapping as semantic order, so valid reloaded evidence failed after all live
work completed. Validate mapping key sets and use an explicit domain sequence for
iteration; never infer domain order from a serialized object.

### 106. Self-hashes exclude their self-hash field

A cursor transition stores both its preimage self-hash and the database artifact hash of
the complete object. Replay compared the former with the latter. Centralize each hash
preimage and test it after durable serialization; a shared `sha256` type does not make
different preimages interchangeable.

### 107. Equivalent generated schemas are not the same request contract

The first C1 port expressed the same worksheet fields but changed their generated order
and added a class-description field. Pydantic therefore produced a different canonical
schema and prompt hash. Production now reproduces the exact benchmarked schema and prompt
identities, asserts both at runtime, and fails closed on dependency-driven schema drift.
Semantic equivalence is insufficient when the selected model was measured against exact
request bytes.

### 108. Longest-match ownership prevents nested detector double counts

`not hispanic or latino` contains the shorter `hispanic or latino` detector phrase. A
simple independent term scan counted one source span twice. Production scans longer
phrases first and suppresses overlapping demographic terms before stable source-order
sorting. Deterministic detectors still need overlap rules even when every regex is
individually correct.

### 109. mergerfs inode values are race evidence, not document identity

mergerfs may report one inode for distinct logical files. C2 therefore hashes every
visible path independently and never caches/deduplicates by `(device, inode)`. Device,
inode and timestamps still help compare two observations of the same opened path, while
the content hash and later same-source revalidation own document identity. The measured
mergerfs smoke passed descriptor traversal, `_analyst` exclusion and symlink rejection.

### 110. A stale heartbeat does not prove a worker is dead

Clearing a lease for an exact still-live PID/start-time/boot identity could let a paused
worker resume beside a new GPU owner. C2 reattaches only when identity and heartbeat are
fresh, clears only a missing/reused/rebooted identity, and blocks stale-live or
unverifiable states. C8 must resolve the exact live process before an atomic clear.

### 111. Process limits belong to the parser cgroup, not the desktop user

`RLIMIT_NPROC` counts processes for the whole user and can collide with unrelated desktop
or service work. C3 uses a transient user-systemd scope with `TasksMax`, then kills the
exact unit on timeout, cancellation or output overflow. The live finite-fork probe must
prove that boundary; merely constructing the command is not enough.

### 112. Anonymous descriptors are not equivalent bind-mount sources

Bubblewrap 0.11.1 accepted `--ro-bind-fd` for an opened named file but rejected a Linux
`memfd` because its `/proc/self/fd` target is an anonymous deleted object. Named C2 files
therefore use `--ro-bind-fd`; bounded, fully sealed anonymous snapshots use bubblewrap's
`--ro-bind-data` read-only copy operation. Both pass one explicit descriptor, preserve
the original descriptor offset and fail closed if their required runtime state differs.

### 113. A parser runtime allowlist must include its launcher

The first narrow Python bind list contained Python, its standard library and shared
libraries but omitted `prlimit`, which is executed *inside* bubblewrap. The sandbox
correctly failed before the parser. Runtime closure tests now execute the exact
systemd → bubblewrap → prlimit → child chain; listing parser dependencies alone is not
proof that the launch graph is complete.

### 114. RTF code pages are state, not one document-wide decode

RTF can change encoding through the font table. C4 tracks scoped font selection,
Unicode-control fallback counts and multibyte hex runs rather than decoding each byte or
the whole document as Windows-1252. Unsupported symbol/code pages fail closed so
corrupted text cannot look like reliable source evidence.

### 115. Failed extraction output is not diagnostic output

Timeout, cancellation, malformed input, resource failure and output overflow may leave
valid-looking partial text in a pipe. C3/C4 retain only a closed reason/detail enum on
failure and discard both stdout and stderr. Bounded bytes are still sensitive bytes; a
cap is not permission to persist or log them.

### 116. A valid wheel hash does not prove matching corresponding source

The measured PyMuPDF 1.28.0 x86_64 wheel genuinely embeds MuPDF 1.29.0, but its build
metadata names only an unidentified local checkout while the published source builds
MuPDF 1.28.0. The PyMuPDF source build also downloads MuPDF itself without checking a
digest and enables unused OCR libraries by default. C5 therefore verifies both source
archives and every build wheel before an offline, no-OCR local build, then asserts both
resulting versions. Package integrity, nested build inputs and binary/source provenance
are separate checks.

### 117. Native-library diagnostics must never share the IPC stream

PyMuPDF writes diagnostics to stdout by default. A repaired or malformed PDF can
therefore prefix a valid frame with warnings and turn a successful parse into an IPC
failure. The child sets `PYMUPDF_MESSAGE=fd:2` before import; stderr stays bounded and is
never returned as document output. A repairable PDF proves the success frame survives.

### 118. Revalidate native output on the durable side

The child rejects unsafe control characters and records per-page character counts, but
the child is the compromise boundary. The durable decoder independently validates UTF-8,
control characters, exact page counts, separators, text-bearing page count, byte/character
totals, status/detail pairs and both parser versions. Strict IPC means distrusting even a
well-formed success frame.

### 119. Documented booleans may still arrive as integers

PyMuPDF documents `Document.needs_pass` as boolean but returned integer `1` for the
encrypted fixture. Identity comparison with `True` missed it and attempted page access.
C5 now accepts only explicit `bool`/`int` values exactly equal to zero or one, then
coerces deliberately. Validation should reflect measured runtime types without becoming
generally permissive.

### 120. ZIP magic identifies a container candidate, not an Office format

DOCX, XLSX and PPTX share ordinary ZIP signatures with unrelated archives. C6 routes ZIP
magic only to a sandbox candidate, then requires content types and one internal root
`officeDocument` relationship to agree on one non-macro main part. A filename extension
never authenticates the package, and ambiguous evidence fails closed.

### 121. Archive metadata gates must finish before the first XML parse

Defusing XML does not stop decompression bombs, duplicate names, path ambiguity or
special ZIP entries. C6 inventories every member and checks declared count, size,
aggregate expansion, ratio, path, type, encryption and compression method first. It then
streams only selected XML and independently counts actual decompressed bytes and CRC;
no member is extracted to disk.

### 122. Importing defusedxml is not the same as configuring it safely

`forbid_dtd` defaults to false. Every C6 `iterparse` call explicitly sets
`forbid_dtd=True`, `forbid_entities=True` and `forbid_external=True`, while separate
depth, element, attribute, byte, CPU and wall limits own denial-of-service bounds. A
valid-package DTD/XXE fixture proves the rejection reaches the XML layer after ZIP gates.

### 123. OOXML order and provenance come from allowlisted relationships

Lexical filenames do not define workbook sheet or presentation slide order. C6 follows
only the small relationship types needed from authenticated roots, resolves internal
package URIs without filesystem extraction, never fetches external targets and avoids a
recursive graph walk. The strict frame carries bounded paragraph/cell/slide/notes units
and the durable worker independently reconstructs every delimiter, identity and count.
Whole-story or whole-sheet labels are too coarse to map a later finding back to its
source paragraph or cell.

### 124. Container inventory and decompression are different budgets

A ZIP central directory describes every member even when the extractor never opens most
of them. C6 permits a bounded 256 MiB of declared OOXML package expansion for
compatibility, while independently allowing only 16 MiB of authenticated XML to be
decompressed and parsed. The distinction must be explicit: a metadata cap is not
permission to expand ignored media, and a historical hostile-fixture threshold is not
silently rewritten as a production limit.

### 125. A generic container signature is only a parser candidate

The CFB/OLE signature is shared by legacy Word, Excel, PowerPoint and arbitrary compound
files. C7A routes that signature to a candidate sandbox but calls content supported only
when exact Antiword successfully authenticates and parses it. A `.doc` extension is not
evidence, and an Excel workbook that reaches the Word parser stays unsupported rather
than being silently mislabelled.

### 126. A charset label does not prove standards-conforming output

Antiword 0.37's `UTF-8.txt` mapping emitted supplementary Unicode characters as paired
CESU-8 surrogates in a live public DOC. Replacing invalid bytes would silently corrupt
evidence. C7A first requires strict UTF-8, then permits only a complete high-plus-low
surrogate pair and converts it to one Unicode scalar; every other invalid form remains a
closed `text_decode` failure.

### 127. A small parser can still be an incompatible distribution dependency

`xlrd 2.0.2` is technically attractive for historical Excel because it is pure Python
and does not execute macros. Its exact licence still contains the original BSD
advertising clause, which GNU classifies as GPL-incompatible. Dependency selection must
review the shipped licence text, not only PyPI's short classifier or the parser's attack
surface. C7B therefore uses MIT-licensed python-calamine under accepted erratum E12.

### 128. Native package identity is both a host and child responsibility

An exact wheel hash is not enough after installation, and binding a whole site-packages
tree defeats a narrow parser runtime. C7B pins the exact ABI-specific wheel, initializer
and native-extension hashes; the host verifies their paths, identities, hashes and shared
library closure before launch, then the isolated child hashes the two package files again
before import. Unsupported Python/platform combinations fail closed instead of finding a
nearby wheel dynamically.

### 129. Spreadsheet provenance must preserve geometry, not only displayed values

A compact list of cell values loses sheet order and coordinates, while iterating the
entire nominal XLS grid creates a denial-of-service path. C7B precharges each worksheet's
bounded dense rectangle, then emits only nonblank typed values as canonical
`sheet-N!A1` units. The durable decoder independently enforces workbook order, XLS bounds,
scalar grammar and count relationships. Empty worksheets are recognized before calling
python-calamine 0.8.2's iterator because that path can panic.

### 130. A frozen database mode still needs a current-runtime safety check

The original contract selected WAL before SQLite disclosed its WAL-reset corruption
race. The active 3.46.1 runtime falls inside the affected range, C8 intentionally uses
multiple processes and the canonical sidecar lives on mergerfs. E13 therefore switches
only this sidecar to `DELETE` plus `synchronous=EXTRA`, short `BEGIN IMMEDIATE`
transactions and bounded contention. Runtime facts can invalidate an otherwise sound
design; verify them before implementing the first write path.

### 131. Python autocommit and explicit SQLite transactions are separate layers

On Python 3.14, `sqlite3.connect(..., autocommit=True)` leaves transaction ownership to
explicit SQL. After `BEGIN IMMEDIATE`, `Connection.commit()` and `rollback()` are no-ops;
an early C8 integration check appeared to succeed but closing the connection discarded
the entire new schema. Every owned transaction now executes literal `COMMIT` or
`ROLLBACK`, and close/reopen tests prove persistence and crash rollback.

### 132. Cancellation without a signal target must still reach a resumable state

Persisting `cancel_requested` is necessary but insufficient when a crashed worker has
already lost its lease. There is nobody to acknowledge the request, so leaving that state
would strand the run permanently. C8 atomically converts the no-lease case to
`cancelled_pending_resume`, marks only nonterminal files resumable and returns no signal
target. Operator intent survives without guessing a PID.

### 133. Resume skips work only after re-deriving its identity

C8 never stores extracted text. After a crash it may reuse complete detector evidence or
valid model results, but it re-extracts selected documents, regenerates chunk boundaries
and requires exact ordered start/end/hash equality before skipping a completed chunk.
This keeps the sidecar content-minimal without trusting stale runtime memory.

### 134. Content-free metadata still needs a typed contract

A generic JSON metadata field can store an entire document body just as easily as a page
count. C8 accepts only closed parser-identity strings, bounded scalar extraction counts
and canonical provenance kinds, labels and spans. Provenance needs durable storage—final
finding offsets alone cannot recover a page or cell after the source changes—but its text
does not. Value-level leak tests matter more than checking that no column is named
`raw_text`.

### 135. Deterministic output needs a cap even when input is bounded

An 8 MiB text limit still permits hundreds of thousands of repeated email matches. E14
caps detector hits before the first insert and writes a stable
`detector_output_limit` terminal at cap + 1. Bounding parser output while leaving derived
evidence unbounded merely moves the denial-of-service path into the database.

### 136. Typed evidence still needs range validation at the database boundary

A correctly typed checkpoint can still claim impossible offsets. C8 initially accepted
chunks beyond extracted text, detector hits beyond the file and grounded findings beyond
their chunk. The durable boundary now binds every range to its parent evidence and
rechecks finding counters and assessment semantics. Dataclass membership proves shape,
not truth.

### 137. A request hash must identify the exact bytes sent

C9 builds one canonical JSON body, hashes those bytes, re-derives the prompt, schema and
options immediately before dispatch, then sends the same bytes with `data=`. Rebuilding
an equivalent object through an HTTP library's `json=` path could change the wire body
after its durable charge. Semantic equality is not request identity.

### 138. A caller deadline does not stop a blocked network worker

Requests' connect/read timeouts do not provide a total wall clock, and closing a stream
can itself block. C9 runs each contact in one daemon worker, lets the caller return on
cancel or the 600-second wall, discards every late result and retains the global permit
until response teardown really finishes. Prompt return and proven server termination are
different claims.

### 139. Resource contention is scheduling evidence, not a failed model answer

The GPU is intentionally shared and the selected model may CPU-offload. Utilization and
free-memory snapshots therefore cannot decide admission or quality. C9 classifies only
explicit bounded 429/503/memory-resource responses, backs off, and never unloads or kills
another workload. C8's two attempts are semantic attempts, so a separate durable contact
ledger is required before orchestration can retry resource contacts honestly.

### 140. Network contact and semantic attempt are different durable facts

A request can reach a server and receive an explicit resource refusal without producing
a model answer. C9B therefore charges every HTTP intent in its own content-free row
before dispatch, but materializes one of the two semantic attempts only for a real or
execution-uncertain chat outcome. A crash is never rewritten as resource contention.

### 141. Effective scheduling state does not require rebuilding lifecycle tables

Adding `paused_resource` directly to C8's run-state CHECK would require rebuilding a
foreign-key parent table. An additive one-to-one schedule table preserves the tested C8
process lifecycle while carrying exact backoff, cooldown and resume authorization. The
smallest compatible migration is often safer than making every status native to one
table.

### 142. A successful contact must checkpoint before another contact starts

C9B first allowed a terminal-success contact to leave its linked semantic attempt
`dispatching` while another HTTP contact began. A later resource pause could then clear
the lease and strand that attempt. Precharge now rejects every new contact until the
successful answer is durably validated or recovery closes it; serial networking alone
does not provide durable ordering.

### 143. Clamped wait arithmetic needs a durable eligibility handoff

Computing `min(recorded_delay, wall_deadline - now)` bounds a monotonic sleep, but a
later wall-clock comparison can still reject forever after clock rollback. C9B binds wait
completion to the exact worker fence or lease-free schedule revision, then marks the
backoff due or the paused retry authorized. The calculation and the dispatch gate must
share evidence, not merely the same formula.

### 144. Cancellation acceptance must name the phase it exercises

A timer-based exploratory probe cancelled before response headers and the two-second
health call found C9's global permit still held while the request unwound. That was the
intended anti-accumulation guard, not evidence that a closed stream had recovered. The
final public protocol observes real response headers, then cancels, requires
`cancelled_unverified` within five seconds and performs exactly one health generation
after the frozen delay. “Cancellation passed” is too vague unless the trigger, retained
permit and following-health boundary are all explicit.

### 145. A stored relative path is not a safe reopen recipe

Joining a trusted root string to an inventoried relative path leaves every intermediate
name open to symlink and rebinding races. C10A reopens the absolute root and each source
component with no-follow directory descriptors, rechecks root and mount identity, then
verifies the already-open file's full inventory fingerprint and hash. Resume trusts the
descriptor and evidence, not a freshly resolved pathname.

### 146. Container magic is provenance, not subtype authentication

ZIP and CFB signatures identify container families, not DOCX/XLSX/PPTX or DOC/XLS by
themselves. A runtime-bind failure or malformed child frame must therefore retain
`ooxml` or `legacy_office`, not invent an exact subtype or drop all format evidence.
Only the strict sandbox child may refine a candidate while committing extraction
evidence, and the durable checkpoint revalidates the allowed family transition.

### 147. A run-id-only worker must revalidate persisted identity bytes

A detached worker cannot safely reconstruct defaults or trust a decoded JSON object
because the row may be stale, noncanonical or from an older experimental contract.
C10A loads the stored canonical bytes read-only, verifies their SHA-256 and exact shape,
and accepts only the frozen model, parser, chunk and strict-isolation contract. Legacy
rows remain valid history but fail closed before private source access.

### 148. A bounded worker queue also needs bounded completed-result lifetime

Limiting an executor to four tasks does not cap private text if completed `Future`
objects remain referenced while the next wave starts. C10B removes each submitted and
completed future, clears batch-local collections and drops result frames before another
file is admitted. Concurrency bounds must cover results awaiting collection as well as
threads currently running.

### 149. Durable cancellation requires cooperation inside deterministic work

Polling only around extraction is insufficient when an 8 MiB document can spend time in
detectors or chunk hashing. C10B passes one shared cancellation probe through extraction,
bounded scanning and chunk construction, discards partial derived evidence, drains every
task and only then acknowledges cancellation and releases the lease. Pure CPU work needs
the same bounded stop contract as subprocess and network work.

### 150. Resume verification errors are not generic state failures

A regenerated parser identity, provenance row or detector hit that differs from its
durable checkpoint is evidence drift, while a lost fence is an ownership failure. C10B
uses a narrow mismatch exception at the compare-only boundary so the worker returns the
closed `resume_mismatch` outcome without conflating cancellation, stale ownership or
database corruption.

### 151. A partial executable must not manufacture a lifecycle seam

Phase 1 returns a live fenced handoff that C11 must consume in the same process. Letting
a C10-only command exit would strand that lease; releasing it “temporarily” would weaken
the reviewed ownership contract. C10C therefore ships the internal worker boundary but
keeps the standalone CLI activation-held before even opening the database. A safe hold
is more honest than a disposable transition that the final pipeline will not use.

### 152. Version strings do not identify a parser bundle

Dependency versions alone miss local changes to routing, frames, detector rules,
checkpoint semantics and sandbox policy. C10C hashes the bounded production source files
that define those behaviors, stores one canonical bundle identity and compares both it
and the exact detector-rules identity before source access. Per-file runtime discovery
still revalidates artifacts to close the later TOCTOU window.

### 153. Native dependency preflight needs an execution proof

Locating a PyMuPDF distribution tagged 1.28.0 does not prove that its embedded MuPDF is
1.28.0—the published wheel discrepancy already demonstrated that. C10C runs one public
synthetic PDF through the exact sandbox child before claiming a worker lease and checks
both returned versions. Metadata inspection and native execution answer different
supply-chain questions.

---

## Not yet learned

Deliberately absent until the mechanisms exist and have been run:

- private staging, HMAC pseudonymisation, and raw-result retention (designed, but not
  exercised because no Stage-F selection made private Stage E eligible);
- extraction-manifest identity handoff (C14).
