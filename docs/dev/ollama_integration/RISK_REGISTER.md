# Ollama Integration — Risk Register

Date: 2026-08-11
Status: **C0A controls frozen; C0B-2A offline controls implemented and reviewed; public
C/D/F envelope executed.** C0B-3 reached Stage F and ended terminal
`INCONCLUSIVE/no_seed1_qualifier` after one of 92 seed-1 chunks repeated an otherwise
grounded finding. The HI accepted E6's narrow prospective correction; C0B-4 repair and
stability confirmation passed its offline implementation and hostile-review gates. Its
first live child failed closed before transport because of the E7 filesystem-probe
preimage mismatch; no model request occurred. C1 and private Stage E remain held. Controls are
authoritative in [`CONTRACT.md`](CONTRACT.md), accepted errata, and the reviewed
benchmark protocols; this register is the
risk-indexed view.

| ID | Risk | Likelihood | Impact | Mitigation / Control |
|----|------|------------|--------|----------------------|
| R1 | Document content egresses to Ollama Cloud | Medium | Critical | Client rejects known `:cloud` and `-cloud` tag forms, permits only the benchmarked local tag+digest, validates a literal-loopback endpoint, disables redirects, and ignores proxies. Tag checks are defense-in-depth. Server-level egress (Ollama cloud off) is an operator prerequisite Analyst cannot prove — stated honestly, not claimed as a guarantee. |
| R2 | Crafted PDF achieves code execution or hangs/OOMs the parser | **High** | **Critical** | Every parser runs in **bubblewrap** (net off, caps dropped, read-only fd-bound input, private HOME/tmp) — a containment boundary against MuPDF RCE (CVE-2026-3308), not just resource limits. Plus wall-clock + `RLIMIT_*` for liveness and process-group kill. PyMuPDF pinned to bundle MuPDF ≥ 1.28.0. Sandbox unavailable → preflight fails. |
| R3 | XXE in DOCX/XLSX/PPTX reads local files or triggers SSRF | Medium | Critical | `defusedxml` (no external entities, no network); member-count/size/ratio/path gates before parse. Guardrail test bans raw `xml.etree`/unsafe lxml in the analysis path. Runs inside the sandbox regardless. |
| R4 | Zip bomb exhausts disk or memory during OOXML unpack | Medium | Medium | Cap uncompressed size and decompression ratio before extraction; reject and record. |
| R5 | Prompt injection from document content corrupts findings | **High** | Medium | Schema-constrained output only; no tools bound; model output never drives an action. Mandatory evidence citations — uncited findings dropped. All rendered values as text nodes, never dynamic HTML. |
| R6 | A grounded model finding is treated as semantically correct fact | Medium | High | Identifier counts come from deterministic detectors, never the model. Exact-substring grounding proves source support, not correct classification. Model rows stay visibly `suggested/unreviewed`, show quote + provenance, and require explicit human accept/reject before findings export. |
| R7 | Report implies full coverage it did not achieve | Medium | High | Every file carries a terminal state. Report header separates detector-scanned and model-reviewed percentages and gives terminal outcomes by reason. No report renders without the coverage table. |
| R8 | Schema-invalid model responses silently drop findings | Medium | Medium | Pydantic-validate every response, retry once, then record `model_invalid` as a counted coverage state. |
| R9 | Long run lost to crash, cancel, or reboot | High | Medium | Per-file checkpointing; resume skips completed files. Cancellation via the existing Running Tasks path. |
| R10 | GPU contention between analysis and any other stack workload | Medium | Medium | CPU-only text extraction (PyMuPDF). No ML document converter. Serial model calls, one job at a time. |
| R11 | Ollama unreachable, model missing, or stack reconfigured mid-run | Medium | Medium | Preflight checks endpoint, version, and model presence before starting. Mid-run failure pauses and checkpoints rather than discarding the run. |
| R12 | Feature bloats a near-limit GUI module | Medium | Medium | New logic in new modules under `experimental/`. GUI gets thin wiring only, per the Sherlock C21 precedent. |
| R13 | Ollama listening on `0.0.0.0:11434` with no auth | Low | High | HI to confirm ufw blocks 11434 from non-loopback. Out of scope for this repo but in scope for the stack it depends on. |
| R14 | Model deprecation breaks the pipeline on an Ollama upgrade | Medium | Low | No hardcoded model name. Model is config, preflight verifies presence, benchmark harness is re-runnable against replacements. |

| R15 | One host holds 46,724 documents; per-host reporting stalls or OOMs on it | High | High | Stream and checkpoint at file granularity — never hold a host's findings in memory. Report generation aggregates from the sidecar DB, not from a live object graph. Benchmark against the FMIC host specifically, not an average one. |
| R16 | Legacy `.doc`/`.xls` (8,077 files) silently skipped | High | Medium | antiword for `.doc`; xlrd or sandboxed LibreOffice for `.xls` (C7). catdoc/xls2csv never used (TALOS-2024-2132). No approved parser → explicit `unsupported_format`, never an unreported gap. |
| R17 | Mislabeled extension routes a file to the wrong parser | High | Medium | Sniff magic bytes before parser selection. Extension is a hint, never authority. |
| R18 | 31% of sampled PDFs have no text layer and could be silently reported as empty | Certain in sample | Medium | Distinct `no_text_layer` terminal, distinguished from a successful terminal with zero findings. Every report prints its actual no-text-layer count/rate. |
| R19 | 502 MB outlier or zero-byte file breaks a parse worker | High | Low | Pre-parse gates on size (upper and lower bound) before a worker is handed the file. |
| R20 | Two GUI instances / stale worker → duplicate runs or a lost run on the single GPU | Medium | High | Atomic GPU-global lease in SQLite; PID + process-start identity; heartbeat; GUI-startup reconciliation and Running Tasks hydration from the DB. Test: two GUIs, two run-ids, one lease. |
| R21 | Parser output floods the pipe past `RLIMIT_FSIZE` | Medium | Medium | Explicit caps on extracted text bytes/chars, IPC size, stdout/stderr, page/sheet/member counts, aggregate expanded size. Overflow → process-group kill + `parser_output_limit`. |
| R22 | Source file modified in place between fingerprint and parse (TOCTOU on the inode) | Low | Medium | `O_NOFOLLOW` open + `--ro-bind-fd`; fingerprint from the same fd; re-check or private-snapshot before parse; mismatch → `source_changed_since_inventory`. |
| R23 | Cancellation closes the HTTP stream but server keeps generating / holds the GPU, or a simultaneous safety/provenance exception overrides the operator cancellation | Medium | Medium | `stream:true` + cancel-checked read loop + socket deadlines; total-run deadline as backstop. The signal handler only publishes operator intent and never performs transport cleanup or takes transport locks; the normal caller loop observes that event and initiates one idempotent asynchronous close. Every dispatch exception path gives the already-set operator event precedence and persists the charged attempt as `CANCELLED_UNVERIFIED`. If server stop is unproven, report "cancel requested; server completion unverified" (never "cancelled"). |
| R24 | Manifest lookup by `(ip, host_type, created_at)` selects the wrong run | Medium | Medium | Structured persistence return with the exact row id; Analyst looks up by row id only. Dashboard flow wired to persist before offering Analyst. |
| R25 | Reduced-isolation mode misused on hostile input | Low | High | Per-run non-persistent ack; barred from the auto post-extract hook and from benchmark/acceptance; recorded in metadata; never default. |
| R26 | Analyst optional deps break core GUI startup | Low | High | Separate `requirements-analyst.txt`; no optional parser imports at package/GUI-module import time; preflight reports missing deps cleanly; parser imports only inside the sandbox child (guardrail test). |
| R27 | Benchmark checkpoint corrupts or loses exclusion on mergerfs, atomic publication is unsupported, or a database-only restore loses receipt-referenced snapshot evidence | Medium | High | The mergerfs database probe passed process-crash rollback, integrity, SQLite/`flock` exclusion and resume, but did not exercise `renameat2(RENAME_NOREPLACE)`; the first real promotion failed closed with `EINVAL`. Never substitute replacing rename. The canonical Analyst directory is now an owner-only persistent bind mount backed by ext4, and the exact atomic primitive is probed before create. DELETE+FULL remains selected for the serial workload. Continue verified Online Backup snapshots and out-of-band quarantine. Production restore remains held pending a complete-evidence bundle and atomic-publication contract. |
| R28 | Crash after Ollama accepts a request causes an uncharged or duplicate result | Medium | Medium | Atomically precharge `DISPATCHING`; recovery marks it `ORPHANED_UNKNOWN`, keeps it charged, and creates a new attempt. Accepted answer and work terminal commit together. |
| R29 | Resume recomputes an adaptive C/D/F branch under changed code | Low | High | Immutable stage-local plans chained by persisted parent aggregate/decision hashes; activation states are recorded once and resume never recomputes them. |
| R30 | Stage-F holdout leaks into tuning | Low | High | Split/view manifest frozen before C; F bytes unavailable to C/D selection; F plan built only after D is immutable; any post-F retune produces `INCONCLUSIVE`. |
| R31 | Private traversal or source mutation reads the wrong file | Medium | Critical | Descriptor-relative `openat2`/no-follow traversal, excluded-tree overlap checks, mount-crossing skip, same-fd keyed content fingerprint, sealed memfd snapshot, and pre-inference revalidation. |
| R32 | Private source/model content reaches Git or survives a crash on disk | Low | Critical | No plaintext staging; raw private answers/reasoning/prompts never persist; sealed worktree plus per-document in-memory leak scan; only schema-generated aggregate committable; private envelope breach blocks security. |
| R33 | Unlabelled private data silently changes the selected model/config | Medium | High | Stage F selects first; Stage E is a separately authorized operational child run that may pass/block only. Any change requires a new public F run and fresh authorization. |
| R34 | Shared GPU contention is misreported as model-quality failure | High | Medium | Serial requests, no unload/kill, persistent bounded backoff and pause, quality-neutral offload/timing, exact call ledgers and honest `BLOCKED_BUDGET` if evidence cannot complete. |
| R35 | A Stage-C survivor is stranded because later D/F implementation changes the run's immutable Git/tree pins | High | High | Freeze, review and commit the complete public C/D/F implementation before creating the live checkpoint. Preserve one source identity through the public terminal; any later code change requires a new run. |
| R36 | A stored aggregate or selection is shape-valid but contradicts its attempt/document evidence | Low | High | Recompute document/cell counters, failure reasons and selection from checkpoint attempts plus the selective public corpus; require exact equality before freezing or loading a decision. |
| R37 | Final state commits but its required recovery snapshot fails | Low | High | Treat the snapshot as an idempotent completion obligation. A later boundary/terminal invocation may create a missing verified same-state snapshot under the global lock, without claiming an invocation or contacting Ollama. Revalidate the frozen source pins before that mutation; `BLOCKED_PROVENANCE` is the sole repair exception because drift is already its terminal evidence. |
| R38 | Conditional D/F work is counted as missing before its predecessor authorizes it | Low | High | Freeze immutable phase plans and one-way activation separately. Plan-only inactive work is neither registered nor counted toward completeness; only a persisted parent decision can activate it. |
| R39 | Long shared-GPU run becomes non-resumable because results docs change the pinned worktree | Medium | High | Freeze the benchmark worktree from create through terminal backup. Write results/docs only after the public terminal; use another worktree for unrelated changes. |
| R40 | Shape-valid nested D/F artifacts encode incompatible meanings or derived counters | Low | High | The normative public schema catalog freezes strict types, nullability, identity domains, ordering and derivations. Recompute every aggregate from attempts plus immutable fixtures and require canonical equality before freeze/load. |
| R41 | Public terminal state commits without its required evidence artifacts, or the cap ledger commits without the attempt | Low | Critical | Public low-level terminal APIs fail closed. Terminal state, failure/completion artifacts and budget callback effects share one transaction; missing or throwing callbacks roll back, and denied work remains uncharged. |
| R42 | A backup path is replaced after validation but before its receipt commits | Low | High | Keep directory and file descriptors pinned through and after receipt commit; verify device/inode, bytes, SQLite integrity, lineage and the live anchor from those descriptors before returning success. Fsync a newly created backup directory through its pinned parent. A matching checksum alone is not path identity. |
| R43 | A caller satisfies a control endpoint but continues scored work before the control sequence is complete | Low | High | Context, owned cancellation and delayed health checks are scheduler barriers, not advisory events. The checkpoint rejects further scored work until each required barrier completes in order. |
| R44 | A coherent rehash hides semantically inconsistent parent decisions or failure identities | Low | High | Re-derive stage, plan, aggregate, active-plan and terminal-specific identity links from checkpoint records. Hash validity is necessary but not sufficient. |
| R45 | Stage-F later seeds become half-activated across a crash, or acceptance cites untyped evidence | Low | High | B2's generic activation API rejects both later seeds and acceptance without mutation. B4 must bind typed evidence and activate the seed-17/seed-20260804 pair and cursor change atomically, with crash, replay and half-state tests, before either path can open. |
| R46 | Crash/resume regenerates a different D/F nonce key or loses it outside the backup | Medium | High | Generate one 32-byte key at public create; durably create `INITIALIZING` in a unique 0700 directory; atomically promote without replacement; then commit key, manifest, C plan/work and `PREPARED` together. Persist the key only in the 0600 checkpoint, include it in backups, and re-derive the C plan plus master-parent link before use. Recovery deletes only an exact evidence-free internal run and never regenerates a prepared key. |
| R47 | Derived boundary bytes are confused with the logical source, changing work or nonce identity | Medium | High | `document_sha256` always names the logical gold document; `view_id` is the exact derived-view SHA-256 and drives `view:<view_id>` nonce identity. D2/D3/D4 re-derive and compare both. |
| R48 | D3/D4 quality runs before the required context allocation is proven, or recovery traffic changes the configuration being measured | Medium | High | Freeze one phase-specific `/api/ps` control per candidate with D3/D4 activation. The first normal HTTP-200 answer—valid or invalid—creates a scheduler barrier before retry/next work. Pending context has scheduler priority; recovery replays the exact trigger configuration and runs `/api/ps` immediately afterward. Mismatch or underallocation blocks provenance rather than eliminating a model. |
| R49 | Cleanup deletes a same-UID replacement introduced after inode validation | Low | High | Atomically quarantine the exact pinned directory under the pinned parent, fsync, and retain its owner-only contents. Recovery performs no automatic unlink/rmdir; destructive garbage collection requires a separate protocol. |
| R50 | A forged or reassigned D control charge passes because its ID and request hash are individually valid | Low | High | Rebuild every historical phase from parent evidence and activation, bind attempts to exact invocation time windows, and require one phase/ordinal owner across each retry group before contact or receipt. |
| R51 | The adaptive D/F implementation, contract errata or leak scanner falls outside the source identity claimed by the public checkpoint, or a path swap makes the scanner read an unintended file | Low | Critical | Exact protocol-scoped allowlists drive task-tree hashing, dirty-tree refusal and leak scanning: C0B-2 remains exactly 48 paths and C0B-3 exactly 58; legacy C0B-1 scope is separate. Selection uses the exact stored/requested protocol identity, never a run-name prefix. Source, baseline and raw-response reads walk descriptor-relative from an explicit lexical trusted root or protected parent, retain no-follow descriptors for every directory component, and validate regular-file, owner, mode, stability and captured-inventory identity as applicable. Semantic pins bind public schemas/scorers and the complete C/D/F generation-factor domain. Every mutating operator path, including abandon, revalidates pins under the global lock before mutation. Any drift fails closed before further HTTP. |
| R52 | A blocked or trickling HTTP peer exceeds the frozen 600-second request wall because socket timeouts measure gaps between reads rather than total elapsed time | Medium | High | Run the blocking request and bounded body parser in one globally permitted daemon worker while the caller owns the monotonic total deadline and checks operator cancellation first. Timeout/cancel abandons the result, asynchronously closes the exact active response, closes any late response before publication and returns without waiting on uncooperative I/O. The permit remains held until that worker reaches final teardown, so retries cannot accumulate orphan workers. Connect and idle-read timeouts remain defense in depth, not the total wall. |
| R53 | Legitimate nonverbose `/api/show` tensor metadata exceeds the generic JSON node cap, or a compatibility increase weakens scored-output safety | Medium | Medium | Keep the 4,096-node cap for chat, scored answers and ordinary controls. Permit 16,384 nodes only for show, above the measured complete-candidate maximum of 11,318, under unchanged 2 MiB raw, 256 KiB canonical and depth-16 caps; sanitize to frozen hashes/safe fields and never persist tensors. Above-cap show remains `FAILED_SAFETY`. |
| R54 | A partial D1/D2 survivor is promoted as a product default after a later gate eliminates every candidate | Medium | High | Product defaults and private Stage E require the activated final Stage-D selection and Stage-F selection chain. Intermediate factor decisions are evidence only. E5 cannot promote or rescore the old survivor; C0B-3 starts from a fresh checkpoint and must pass the complete public chain. |
| R55 | Automation bias or review fatigue turns “human reviewed” into rubber-stamping | Medium | High | Model rows are visibly suggestions, deterministic evidence stays separate, source context is adjacent, and accept/reject is explicit with no hidden auto-accept. Capture bounded override/rejection counts for monitoring and rebenchmark triggers; feedback never silently retunes behavior. Model output triggers no action. |
| R56 | A C0B-3 artifact is interpreted under C0B-2 rules, or mixed policy lineage passes through resume/backup verification | Low | High | The stored header is the sole protocol discriminator. Every policy-sensitive D/F plan, aggregate, decision, result and completion carries the exact current binding and a distinct version; legacy absence remains exact. Mutating namespaces check the header read-only before writable open and again after open. Before a Stage-D abandon mutates state, re-derive every completed adaptive attempt plus the final decision from durable evidence. Every backup of an active-D run, including a generic `ABANDONED` terminal, repeats that reconstruction. The frozen `BLOCKED_PROVENANCE` exception remains structural-only so a drifted nonce key cannot prevent its own mandatory failure receipt; the generic anchor still validates the current-family plan/decision lineage and exact failure artifact. Backup/status/verify dispatch from the stored header, reject mixed families, and are exercised against both fresh fake C0B-3 terminals and all three immutable C0B-2 checkpoints. |
| R57 | The accepted single false-positive budget is misrepresented as accuracy perfection or silently widened after launch | Medium | Medium | Document the HI's assistive-product rationale, test exact 0/1/2 document boundaries at D/F and final acceptance, label model output `suggested/unreviewed`, require explicit human adjudication and retain accept/reject monitoring. The budget does not weaken schema, injection, grounding, provenance, privacy or no-action gates; any threshold change requires a new policy identity and benchmark. |
| R58 | A legitimate repeated identifier is treated as a whole-chunk model failure, or broad deduplication hides malformed/unsupported output | Medium | Medium | C0B-4 permits only one strictly structured, fully grounded redundant `(category, NFC quote)` row in one chunk/document independently per scored lane. Preserve raw counts and response bytes, remove later duplicates deterministically, count the recovery explicitly and fail on 2 rows/chunks/documents, any second semantic error or any ungrounded row. Legacy protocols remain strict. |
| R59 | A deterministic schema retry repeats the same failure while consuming time and call budget | High | Low | Duplicate-only recovery is local and uses no model retry. C1 gives any other repair a distinct error-specific request identity and one-call bound; changing only the seed is forbidden. Historical C0B-2/C0B-3 identical-retry evidence remains unchanged. |
| R60 | Observed Stage-F documents or old seed plans are presented as a fresh holdout after the prompt/scorer changes | Medium | High | Call C0B-4 repair/stability confirmation, not new selection or population validation. Create fresh nonces/plans under a distinct protocol, run unexecuted F72 seeds 17 and 20260804 plus a new C44 acceptance lane, and never resume or copy old plans. Bind the exact verified C0B-3 final-D parent and receipt; state that C/D were not rerun under the correction. |
| R61 | Individually valid checkpoint artifacts are mixed across protocols, nonces, plans or terminal owners | Low | Critical | One shared run-lineage validator binds every artifact, attempt, event, aggregate, activation, cursor, terminal and receipt to the exact header/master tree. It runs before mutation/contact and during source/snapshot verification. Strict mixed-lineage and fabricated-owner tests fail closed. |
| R62 | A crash between attempt persistence and derived event/artifact storage causes a repeat call or an unverifiable run | Medium | High | Persist the charged attempt first, reconcile only missing events from that durable history, and rederive context/cancellation evidence locally. Existing altered events fail; missing crash-gap events repair idempotently before mutation or contact. |
| R63 | Canonical JSON sorting changes mapping order and makes a valid stored aggregate unreadable | Medium | Medium | Schema semantics validate exact mapping key sets, not insertion order. Real scorer output must pass store-read-finalize canonical round trips. |
| R64 | Backup verification trusts shape-valid aggregate hashes instead of the model-response evidence, or replay becomes quadratic | Low | Critical | Authoritative verification independently replays scoring for the live checkpoint and immutable snapshot. Cache the immutable attempt ledger once per replay; the 228-request proof asserts exactly two linear semantic replays and rejects a coherently changed raw response/history. |
| R65 | A control response is recorded valid before context/health evidence requirements are checked | Medium | High | Treat evidence-required response metadata and parsing as part of the attempt outcome. Malformed context/health responses persist as charged safety failures with terminal backup; a durable valid response is rederived after crash without another call. |
| R66 | Filesystem creation and revalidation hash different capability-probe mode sets | Low | High | C0B-4 creation and invocation revalidation both probe exactly the frozen header journal mode. Keep the historical C0B-2/C0B-3 helper unchanged. Real create-to-revalidate parity plus injected mismatch/exception tests run before a live child is created. A pre-contact failure remains immutable and backed up. |
| R67 | A corrective commit makes the true pre-task leak baseline appear stale, encouraging a post-task replacement, dirty overlay or Git replacement ref that hides committed content | Medium | High | Never synthesize a new historical inventory. C0B-4 alone may carry its genuine baseline across one direct non-merge task commit; scan every immutable `HEAD` blob and dirty overlay independently against the exact allowlist. Disable Git replacement objects for every provenance/object read and rehash blob bytes against the tree object ID. Reject symlinks/gitlinks/non-regular entries, a second commit, merge, unsafe path or unlisted path. Create a fresh baseline immediately after the correction commit and before later result edits. |

## High-Likelihood Risks — Detailed Controls

### R2: Parser code execution, hang, or memory exhaustion

The shipped parser (PyMuPDF/MuPDF) has RCE-class history, so the design is
containment-first and crash-only. The orchestrator never parses a file itself.

1. **Sandbox is the boundary.** Every parser runs in bubblewrap: read-only
   fd-bound input, runtime allowlist (not the repo), `--clearenv`, private
   HOME/tmp, `--unshare-net`, `--unshare-pid`, `--cap-drop ALL`,
   `--die-with-parent`. Sandbox unavailable → preflight fails. Residual: not a
   defense against a host-kernel exploit.
2. **Pre-parse gates.** Reject before a child is spawned: size above cap
   (default 100 MB — excludes the 502 MB outlier), size zero, magic bytes not
   matching a supported family, decompression ratio / member / expanded-size caps
   for container formats.
3. **One file per child**, explicit `spawn`/`forkserver`. A child parses exactly
   one file and returns bounded text or dies.
4. **Kill paths.** rlimits (`RLIMIT_AS`/`RLIMIT_CPU`/`RLIMIT_NPROC`/
   `RLIMIT_NOFILE`/`RLIMIT_CORE`) applied via a launcher/`prlimit` (never
   `preexec_fn`), plus a parent wall-clock `SIGKILL` of the whole **process
   group**. rlimits catch runaway allocation; the wall clock catches infinite
   loops that trip no soft limit.
5. **Worker death is a normal outcome.** Record the specific terminal
   (`parse_timeout`/`parse_oom`/`parse_signal`/`parse_error`/`parser_output_limit`),
   respawn, continue. No single file can end a run.
6. **Version pin with a floor.** Pin an approved PyMuPDF bundling MuPDF ≥ 1.28.0;
   preflight asserts both the package and embedded MuPDF versions; re-check at
   implementation time.

Net effect: a crafted document costs one timeout interval and one line in the
coverage table, contained.

### R5: Prompt injection from document content

The controlling insight is that **an injection can lie to the model but it
cannot lie to a regex.** All structured identifier counts come from the
deterministic track, so the highest-value findings are outside the model's reach
entirely. What remains at risk is classification and prose, and those are
constrained:

1. **No capabilities.** No tools, no function calling, no filesystem, no network
   beyond the single Ollama call. The model's entire output surface is one
   schema-validated JSON object.
2. **Per-chunk isolation.** Every chunk is a fresh request with no conversation
   history. An injection in chunk 40 cannot influence chunk 41, and cannot
   influence any other file or host.
3. **Delimited untrusted content.** Document text is fenced inside a
   random per-request nonce delimiter the document author cannot predict, per
   OWASP's "separate and clearly denote untrusted content."
4. **Quote verification.** Every model finding must cite a span that the
   aggregator confirms is an exact substring of the source chunk. Fabricated
   findings are dropped. A finding that survives by quoting the injected text
   itself is still correct behavior — it points at real content in a real file,
   which is exactly what the analyst needs to see.
5. **Output is untrusted display data.** The static HTML report uses
   context-appropriate escaping, no JavaScript, no remote assets, and a strict
   CSP (`default-src 'none'`); extracted URLs render as text. Where a future JS
   surface builds the DOM (Web UI), values go through text nodes — the rule
   Sherlock already follows. Canonical unmodified evidence lives only in the 0600
   JSONL; HTML/CSV are derived sanitized copies.
6. **Downstream warning.** The generated report contains attacker-controlled
   strings. If a report is ever fed to another LLM, it is an injection vector.
   Document this; do not build that path in V1.

Residual risk accepted: a sufficiently clever document can cause a wrong
classification or a misleading summary sentence. It cannot cause an action, and
it cannot suppress a deterministic identifier match.

## Deferred (Not V1)

1. OCR for scanned PDFs and images.
2. Cross-run corpus-level reporting and clustering.
3. Any automated action taken on a finding (notification, tagging, disclosure).
