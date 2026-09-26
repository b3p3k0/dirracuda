# Analyst Benchmark — Method and Instrument

Companion to the pre-registered [`BENCHMARK_PROTOCOL_C0B1.md`](BENCHMARK_PROTOCOL_C0B1.md),
which is the authority on gates, factors and budgets. This document explains how the
instrument is built, what each module is for, how to reproduce a run, and — most
importantly — exactly what is and is not committed.

Everything here is a **measurement instrument**, not production runtime. No module in
`scripts/analyst_benchmark/` is part of the shipped product.

---

## 1. What is and is not committed

**Committed:**

- the synthetic gold set (`shared/tests/fixtures/analyst_gold/`) and its generator;
- the instrument (`scripts/analyst_benchmark/`) and its tests;
- the protocol, this method document, the errata, and aggregate results.

**Never committed:**

- any private document, path, basename, content hash, host identifier, or pseudonym;
- any raw model output or reasoning trace;
- any per-document private result.

Raw answer artifacts go to a **0600 sink outside the repository**, under
`get_paths().experimental_dir / "analyst_bench" / "runs" / <run_id>/`. The path is
always resolved through `get_paths()`; no module hand-builds a `~/.dirracuda` string,
and a test enforces that. GPT-OSS reasoning text is not retained: the client counts its
bytes for output-budget and operational reporting, then discards the text.

`git status --porcelain` is not a leakage test. The real gate is
`--leak-scan`, which records an immutable task baseline at C0B-1 start, inspects only
task-created and task-modified deltas against an exact allowlist, and content-scans
them. Pre-existing untracked work is reported and never touched. It fails closed.

## 2. Coverage vocabulary

Preserved from CONTRACT.md §4 without exception: **detector-scanned is not
model-reviewed.** The two are always reported as separate percentages
(`report.coverage_line`), and no output describes detector coverage as analysis.

The private corpus is unlabelled. It cannot produce precision, recall, F1, or any
accuracy claim, and detector agreement is not ground truth. `report.assert_no_accuracy_words`
enforces that on the private results section.

## 3. The gold set

166 canonical-extracted-text documents. The model is benchmarked on the payload an
extractor would hand it; parser correctness belongs to C4–C7.

| Stratum | Count |
|---|---|
| Positive controls (20 per category × 4) | 80 |
| Negative controls — clean | 20 |
| Negative controls — near-miss lookalikes | 20 |
| Prompt injection | 8 |
| Matched clean twins | 8 |
| Boundary (6 templates × 4 offsets) | 24 |
| Output-truncation | 3 |
| Input/context-truncation | 3 |
| **Total** | **166** |

Stage B uses a balanced 44-document screening subset (6 per positive category, 6
clean, 6 near-miss, 4 injection + their 4 twins).

**Identifier provenance**, recorded in the manifest and enforced by test: card PANs and
ACH routing/account numbers are documented Stripe sandbox values
(<https://docs.stripe.com/testing>); SSNs use SSA never-issued areas (900–999, 000,
666); phones use 555-0100…555-0199; domains are `example.com`/`example.org` (RFC 2606);
IPs come from RFC 5737/3849; names, streets and employers are invented.

**No malicious container is committed.** XXE documents, zip bombs, extreme member
counts, deep nesting, path-traversal members and a zip-mislabeled-`.pdf` are generated
at test time under `tmp_path` by `shared/tests/analyst_container_cases.py`, with builder
bounds (64 MiB / 2000 members) deliberately set above the supervisor gate thresholds
(16 MiB / 1000 members) so a case can be built safely *and* rejected.
That value remains the historical C0B fixture threshold; production OOXML separates its
package-inventory and parsed-XML budgets under
[`CONTRACT_ERRATA.md` E11](CONTRACT_ERRATA.md#e11--separate-ooxml-package-metadata-from-parsed-xml-limits).

The generator is committed alongside its output, and a test asserts regeneration is
byte-identical. Generated `manifest.json` keeps one document record per line (176 lines
total), remaining below the repository's 1700-line modularisation gate without changing
the 166 fixture payloads.

## 4. Module dispositions

Every module has a named owner or a removal card. Nothing is left as "superseded
later", which would just be known dead code.

| Module | Disposition |
|---|---|
| `worksheet.py` | **Ported to production in C1**; the losing variant is deleted there |
| `detectors.py` | **Ported to production in C1**, as a subset of the full detector set |
| `chunker.py` | **Ported to production in C1** |
| `preflight.py` | **Ported to production in C9** |
| `client.py` | **Ported to production in C9** |
| `sandbox_smoke.py` | Retained as immutable C0B Stage-A evidence; C3 production never imports it |
| `protocol.py` | Retained diagnostic |
| `resources.py` | Retained diagnostic |
| `goldset.py` | Retained diagnostic; the fixture corpus becomes the C1+ test corpus |
| `metrics.py` | Retained; reused by C15 acceptance |
| `report.py` | Retained diagnostic |
| `ledger.py` | Retained diagnostic |
| `leakscan.py` | Retained diagnostic |
| `runner.py` / `__main__.py` | Retained diagnostic |

`corpus.py` (private sampler, HMAC pseudonyms, staging) is **not built in C0B-1**.
C0B-1 touches no private data and the C0B-2 extraction protocol is deliberately
deferred, so shipping an unreachable, unexercised private sampler would be untested
code on speculation. It arrives in C0B-2 with its own tests.

## 5. Confirmation gates

| Invocation | Behaviour |
|---|---|
| no arguments | **does nothing** — usage to stderr, exit 2, zero side effects |
| `--self-test` | offline instrument check; no network, no Ollama |
| `--stage A --confirm-dependency-probe` | offline work **plus one external PyPI download**. "Zero calls" for Stage A means zero *Ollama* calls, not zero network |
| `--confirm-live --preflight-only` | transport + digest preflight, then stop |
| `--stage B --confirm-live` | the screening pilot |
| `--confirm-exclusive-ollama` | cold-load measurement only; never used by default |
| `--leak-scan --mode public` | task-delta allowlist and content scan |

No Ollama request of any kind — including `/api/tags` and `/api/show` — happens before
`--confirm-live` and a successful preflight.

## 6. Reproduction

```bash
# Offline
./venv/bin/python -m scripts.analyst_benchmark                       # must exit 2
./venv/bin/python -m scripts.analyst_benchmark --self-test
./venv/bin/python -m pytest -k analyst -v

# Stage A (one PyPI download, zero Ollama calls)
./venv/bin/python -m scripts.analyst_benchmark --stage A --confirm-dependency-probe

# First Ollama contact, then the pilot
./venv/bin/python -m scripts.analyst_benchmark --confirm-live --preflight-only
./venv/bin/python -m scripts.analyst_benchmark --stage B --confirm-live \
    --models gpt-oss:20b,qwen3.6:35b,qwen3.6:27b --worksheet v1,v2 \
    --seed 1 --soft-wall-minutes 240

# Leakage
./venv/bin/python -m scripts.analyst_benchmark --leak-scan --mode public \
    --baseline-file /secure/path/to/pre-task-baseline.json \
    --raw-artifact /secure/path/to/stage-b-raw.jsonl
```

Substitute the exact owner-only baseline and raw-artifact paths created for that run.
Both arguments are mandatory; repeat `--raw-artifact` when a run has more than one raw
JSONL file.

### C0B-1 shared-resource limitation

The frozen protocol declared backoff, checkpoint and resume behaviour. The executed
C0B-1 pilot did not encounter a resource interruption, and audit found that its runner
did not yet implement a durable resume path: it could mark a soft pause or skip a
resource-interrupted call, but could not safely resume the incomplete design. That
capability is therefore **held for C0B-2** and must be implemented and tested before any
long shared-GPU or private-corpus run. C0B-1 does not claim to have validated it.

## 7. PyMuPDF pin — measured, not assumed

| Field | Value |
|---|---|
| Pin | `PyMuPDF==1.28.0` |
| Wheel | `pymupdf-1.28.0-cp310-abi3-manylinux_2_28_x86_64.whl` |
| Local sha256 | `44f0973f5e5edbaec95bc34b64e71d1959d4ee90b1328de1b4f4f5b4fa78673f` |
| PyPI-published sha256 | identical — verified, fails closed on mismatch |
| `pymupdf.__version__` | `1.28.0` |
| **Embedded `mupdf_version`** | **`1.29.0`** |

The embedded MuPDF is **1.29.0**, not the 1.28.0 its release note advertises — which is
exactly why CONTRACT.md §10 requires asserting both versions rather than trusting the
package number. It clears the ≥ 1.28.0 floor (CVE-2026-3308) and is well past PyMuPDF
1.26.7 (CVE-2026-3029).

**Honest scope:** this pin is selected on **version/security grounds plus an
import-and-single-benign-PDF smoke test inside the sandbox**. C0B did **not** benchmark
PDF extraction quality — that is C5.

The probe downloads first, compares against PyPI's published digest for that exact
filename, asserts exactly one wheel, then installs that file with `--no-index`. The
probe creates an owner-only, task-prefixed scratch tree before launching the shell, so
the parent always knows the cleanup target. Every failure, timeout, sandbox exception
and normal return validates ownership, type and path before recursive deletion. The
Stage A diagnostic now uses a random 0600 file instead of a fixed, permissive `/tmp`
name. `requirements.txt`, CI, schema and auth are untouched.

## 8. Sandbox smoke

Bounded checks only, and explicitly **not** the Stage E extraction boundary.

Verified on this host: network unreachable, host HOME absent, repository not bound,
`RLIMIT_AS` enforced on the allocation itself, process-group kill, antiword sandboxed,
PyMuPDF imported and a benign PDF text layer read inside the sandbox.

Two findings worth carrying into C3:

1. **prlimit must run inside the sandbox.** Wrapping `bwrap` in `prlimit` applies the
   limits to namespace setup; `RLIMIT_NPROC` in particular makes `clone()` fail with
   EAGAIN before the sandbox exists.
2. **`RLIMIT_NPROC` is unusable here.** It is a per-UID limit counting every process the
   user already owns (227 at measurement). Any value low enough to bound a fork bomb
   also prevents the sandbox starting. The correct mechanism on this platform is a
   cgroup `pids.max`; **C3 owns it.** Until then the fork-bomb controls actually in
   force are the PID namespace and the process-group kill. Recorded, not papered over.

## 9. Card-close documentation and file sizes

The root `README.md` was reviewed after C0B-1. No edit is appropriate yet: Analyst is an
unreleased development workspace with no user-facing entrypoint, installation step, or
runtime behaviour. This method document and the workspace README are the correct scope
until an implementation card changes the shipped product.

Line counts before → after this remediation pass:

| File | Before | After |
|---|---:|---:|
| root `README.md` | 714 | 714 |
| `BENCHMARK_PROTOCOL_C0B1.md` | 257 | 257 (frozen run artifact; unchanged) |
| `BENCHMARK.md` | 187 | 224 |
| `STAGE_B_OUTCOME_C0B1.md` | 131 | 133 |
| `LESSONS_LEARNED.md` | 174 | 223 |
| workspace `README.md` | 122 | 126 |
| `CONTRACT_ERRATA.md` | 59 | 59 (unchanged) |
| gold-set `generate.py` | 505 | 534 |
| gold-set `manifest.json` | **3311** | **176** |
| `test_analyst_gold_set.py` | 223 | 227 |

Every source/document file is below 1200 lines. The generated manifest now also clears
the explicit 1700-line pause threshold.

## 10. C0B-4 offline confirmation instrument

C0B-4 is a new artifact family. It does not rewrite or resume C0B-2/C0B-3. Its child
checkpoint binds the verified C0B-3 finalist, fresh lane plans, a new prompt rule and the
bounded grounded-duplicate policy defined in `BENCHMARK_PROTOCOL_C0B4.md`.

The offline implementation passed 141 C0B-4 tests and a separate 37-test hostile
holdout. The production-shaped fake run used real fixtures, plans, scoring and SQLite:
228 scored requests, 12 controls and three stage-boundary invocations. It reached a
backed-up `CONFIRMED` result without network access. Read-only verification replays the
attempt evidence independently for both the live checkpoint and immutable snapshot; it
does not trust stored aggregate hashes alone.

The C0B-4 live command surface was intentionally gated:

```bash
./venv/bin/python -m scripts.analyst_benchmark c0b4 create
./venv/bin/python -m scripts.analyst_benchmark c0b4 status --run-id <run-id>
./venv/bin/python -m scripts.analyst_benchmark c0b4 run --run-id <run-id> --confirm-live
./venv/bin/python -m scripts.analyst_benchmark c0b4 resume --run-id <run-id> --confirm-live
./venv/bin/python -m scripts.analyst_benchmark c0b4 verify --run-id <run-id>
```

C0B-4C ran from a dedicated clean worktree and stayed unchanged through its terminal
receipt. The checkpoint sealed the complete worktree across execution. Shared GPU use
increased duration but did not change quality scoring.

The root `README.md` was reviewed again after C0B-4B and E7. No edit is appropriate:
Analyst still has no released user-facing entrypoint, dependency lane or runtime behavior.

The first C0B-4C child failed closed before transport with zero invocations and attempts.
Creation probed only frozen `DELETE` mode, while inherited revalidation included both
`DELETE` and `WAL` in the capability hash. E7 preserves that verified terminal and adds a
C0B-4-only exact-mode revalidator; legacy behavior and all benchmark gates are unchanged.
The corrected C0B-4 suite passed all 150 tests, and the final independent hostile review
accepted the replacement-ref defense. Its one authorized replacement is now terminal;
`PUBLIC_CDF_OUTCOME_C0B4.md` records the result.
The leak gate retains each genuine protocol-scoped baseline: C0B-4 and C0B-5 may each
cross one direct non-merge task commit, add that commit's exact net paths to the scan and
still reject a second commit or any path outside the matching frozen allowlist. It never
manufactures a post-task baseline. The scanner reads each immutable `HEAD` blob as well
as any dirty overlay and rejects committed or current symlink/gitlink/non-regular task
paths.
All Git provenance and object reads disable replacement refs, and every committed blob is
rehashed against its tree object ID before scanning.

## 11. C0B-4 terminal and C0B-5 policy handoff

The verified E7 replacement completed F72 seed 17 and ended
`INCONCLUSIVE/seed17_no_qualifier`: two of 16 negative near-miss documents each produced
one grounded financial suggestion, exceeding the frozen one-document allowance. Every
other measured gate passed, including 92/92 completed chunks, 168/168 grounding, 8/8
recall per category, four injection pairs and 12/12 boundaries. Cancellation/health and
the two later lanes did not run because the quality miss stopped the schedule. The
terminal checkpoint, snapshot and receipt verify. Exact public measurements are in
`PUBLIC_CDF_OUTCOME_C0B4.md`.

E8 prospectively caps both affected negative documents and retained findings on negatives
at 2 per 16-negative F lane and 4 across the final 40 negatives. Every other gate remains
unchanged. C0B-5 uses fresh F seeds 20260804 and 20260811; the observed C0B-4 seed 17 is
descriptive only. This is an operational review-cost limit on a curated synthetic set,
not a production accuracy claim.

No C0B-5 model call is allowed until its complete source passes offline and hostile
review, the leak/file-size gates pass and the source is committed in a clean worktree.
One quality miss stops `INCONCLUSIVE`; there is no automatic threshold widening or repeat
run.

## 12. C0B-5B offline implementation

The isolated C0B-5 module family passed its complete offline gate: 106 focused tests, 30
separate provenance/security tests, Analyst-wide regression, compile/diff, file-size and
public leak checks. An independent hostile re-review found three ordering defects in
resume control replay, semantic receipt publication and post-rename cleanup; the fixes
and exact regressions passed re-review. This is an offline result, not a model result. No
C0B-5 request occurred during C0B-5B.

The scorer independently caps two different review costs: affected negative documents
and retained suggestion rows on those documents. Each F lane must stay at or below 2/2;
the final 40-negative aggregate must stay at or below 4/4. Boundary tests cover both
units separately, and the derived public summary rebuilds document/category/template
rows from canonical attempt evidence.

Lineage verification opens the frozen C0B-3 and C0B-4 checkpoint/snapshot pairs
read-only before child creation, mutation or model contact. C0B-4 parent facts are
replayed independently rather than trusted from stored aggregate hashes. C0B-5 terminal
verification likewise replays the child checkpoint and immutable snapshot from attempt
evidence.

The child checkpoint keeps one durable precharged request at a time. Lane completion
commits its aggregate and pauses; only an explicit verified resume may activate the next
lane. Terminal evidence becomes immutable before the owner-only snapshot and receipt are
published. Crash-gap reconciliation may recreate missing derived events, but it cannot
repeat a completed call or rewrite an existing event.

Shared-GPU contention is operational, not a quality measurement. Dispatch stays serial;
a retryable resource failure yields `PAUSED_RESOURCE` with no second in-flight request.
The operator may resume later under the same frozen plan and source identity. Soft-wall
and stage-boundary pauses follow the same no-silent-advance rule.

## 13. C0B-5C live outcome

The sole child at source commit `a45c266` ended terminal `BLOCKED_PROVENANCE` after 97
charged calls: three preflights, 92 scored F72/20260804 chunks, one context control and
one planned cancellation control. The failure occurred before the cancellation health
call. No lane aggregate exists, so this is not a model-quality pass or miss. F72/20260811
and C44/1 were never activated.

The terminal checkpoint and two owner-only snapshots independently pass structural and
semantic replay without a receipt. Receipt publication deterministically fails because
the semantic verifier sets `PRAGMA query_only=ON` on the live SQLite connection and does
not restore it before receipt insertion. The earlier pre-health provenance trigger is
narrowed to the mutation boundary but its exact exception class was intentionally
discarded by the content-safe CLI, exposing a separate observability gap.

C0B-5 cannot be resumed or repeated: its terminal is immutable and its one-child
allowance is spent. C1 and private Stage E remain held. Any replacement must be a new,
prospectively frozen card with a fixed receipt path, content-free failure codes and fresh
generation conditions; it is not authorized by C0B-5.

## 14. C0B-6 prospective repair

C0B-6 is the authorized replacement card, not a reinterpretation of C0B-5. It keeps the
model, prompt, scoring and review budgets unchanged and freezes never-contacted F72 seeds
20260811 and 20260818. Before any live request, offline evidence must prove that semantic
receipt replay uses a separate pinned read-only connection, every terminal exception
boundary persists a closed content-free origin, and an exact 97-call C0B-5-shaped SQLite
state resumes from cancellation to the following health request without repetition.

The new artifact/checkpoint family is isolated under `c0b6-*-v1`; C0B-5 code and evidence
remain read-only. One C0B-6 child may run only from the committed, leak-clean C0B-6B
source. A quality miss stops `INCONCLUSIVE`; a harness fault fails closed and cannot
authorize another child.

C0B-6B reproduced the lost pre-health failure exactly: the runtime passed an attempt ID
to `C0B5Checkpoint.list_attempts()`, whose real API accepts no arguments. The in-memory
test double accepted the extra optional argument, so the mismatch reached the live
SQLite boundary only after the cancellation response. C0B-6 filters the no-argument
result, narrows the double to the real signature and proves the 97-call
cancellation-to-health handoff through the actual SQLite checkpoint. Receipt replay now
opens its own pinned read-only connection; the writable publisher remains writable on
both replay success and failure. Failure artifacts use a closed content-free origin
vocabulary, and the CLI exposes only those safe codes.

The final offline gate passed: 111 focused C0B-6 tests, 155 C0B-5 compatibility and
Analyst security/provenance tests, and the 1,420-test Analyst-wide regression. Six
marked skips were expected. Compile, diff and public leak checks pass. The two largest
C0B-6 instruments are 1700 and 1717 lines; no shipped production file changed, so the
HI's production-only size gate does not require benchmark modularisation here.

## 15. C0B-7 recovered decision

C0B-7 preserved the verified C0B-6 `BLOCKED_PROVENANCE` terminal and replayed its exact
checkpoint and snapshot independently. It corrected only three identity/serialization
mistakes in the offline projection: decision-row versus payload hash, canonical mapping
order versus domain order, and cursor self-hash versus full artifact hash. No source
artifact changed and no model call occurred.

Both sources produced `RECOVERED_CONFIRMED` with recovery SHA-256
`818516869ff91c0834cfa5d6526ce075516caace60cd1f8cb7dbcbbc3902e27f`. The unchanged
acceptance rule passes with 202/202 completed chunks, 408/408 grounded findings, full
20/20 recall in each category, two false-positive documents/findings and zero injection
events. Exact public measurements are in `PUBLIC_CDF_OUTCOME_C0B7.md`.

The HI explicitly deferred private Stage E until real-document testing is useful. This
satisfies the C0B closeout alternative without authorizing any private read. C1 may begin;
Stage E remains a named later validation gate.
