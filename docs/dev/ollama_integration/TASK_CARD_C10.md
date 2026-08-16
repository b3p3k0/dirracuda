# C10 — Phase 1 worker orchestration

Date: 2026-08-16
Status: **C10 complete at the reviewed Phase 1 boundary; C11 and C12 complete**

## Issue

C1–C9 provide pure detectors, hostile-document parsers, durable checkpoints, worker
fencing and the later model-contact boundary, but no worker currently connects inventory
files to Phase 1 extraction, deterministic detection, selection and chunk handoff.

## Root cause

The existing modules were deliberately built as independently testable boundaries. A
run-id-only detached worker still lacks a typed run/resume context, descriptor-safe file
reopen, phase-aware claims, exact regenerated-evidence verification and one atomic
handoff that prevents C10 from reclaiming C11-ready files.

## Frozen boundary

C10 remains the Phase 1 card from `CONTRACT.md` §17. It owns the worker shell, safe
source reopen, sandboxed extraction, deterministic detection, selection and immutable
chunk handoff. It imports or calls no Ollama client/contact code and creates no
`analyst_ollama_contacts` rows. C11 owns every version, tag, chat, retry and resource
contact.

Production activation was held for C11 and C12 until C12 consumed the Phase-2
handoff in the same worker process. A successful Phase 1 function returns the current lease plus content-free
handoff evidence; it does not release the lease or label the run complete.

## Locked decisions

- Strict sandbox mode only. Reduced isolation remains a later separately reviewed path.
- Fast mode selects a nonempty successfully extracted file if and only if the complete
  deterministic hit set is nonempty. Deep mode selects every nonempty successfully
  extracted file. “Could not classify” is not a hidden third selection rule.
- At most four parser tasks are in flight. Parser subprocesses perform the isolated
  work; the worker main thread alone owns the latest `LeaseFence`, SQLite writes,
  heartbeats and durable cancellation polling.
- Tasks return bounded in-memory extraction results only. At most four 8 MiB text bodies
  may be retained. Text references are dropped after their checkpoint/handoff.
- Heartbeat/poll cadence is at most two seconds. Every pulse returns the successor fence
  and the durable cancel state atomically; no background DB writer may stale a copied
  fence.
- Source paths are reopened component by component from an already-validated root fd.
  Absolute, empty, dot, dot-dot, backslash, NUL, symlink, special-file and mount-crossing
  paths fail closed. The final already-open fd is checked against the exact inventory
  fingerprint before sandbox handoff.
- `source_identity_json` for a runnable C10 worker must contain exact root device, inode
  and mount identity. Older synthetic/generic run identities may remain valid C8 test
  rows but cannot start the production worker.
- Magic sniff candidates `ooxml` and `legacy_office` are honest format-stage evidence
  when a sandbox-level failure occurs before subtype authentication. Successful
  extraction still requires the concrete `docx|xlsx|pptx|doc|xls` subtype. A resume may
  refine only the matching candidate to its authenticated subtype while committing
  exact text evidence.
- Selected files transition atomically from `detector_scanned` to
  `selected_for_model`, store or verify the complete chunk set, and return to `pending`
  with no active generation. C10 claims only pre-model stages, so it cannot reclaim its
  own C11-ready handoff.
- Missing sandbox capability fails before any private source is opened. The worker logs
  only run id, file id, stage and closed outcome codes—never relative paths, source text,
  detector values, exception text, prompt/model content or reasoning.

## Card split

### C10A — contracts and safe reopen

- [x] Add immutable worker/run/file-resume/handoff contracts and closed outcomes.
- [x] Load and revalidate the exact immutable run context from a run id.
- [x] Reopen an inventoried source through descriptor-relative component walking.
- [x] Preserve candidate format on every post-sniff extraction failure and admit it only as
  format-stage failure evidence.

### C10B — Phase 1 state engine

- [x] Add pre-model-only claims, typed resume snapshots, exact extraction verification,
  atomic selected-file chunk handoff and a successor-fence cancel/heartbeat pulse.
- [x] Implement the bounded four-slot engine with all durable writes on the main thread.
- [x] Resume from every Phase 1 boundary without rewriting immutable evidence.

### C10C — worker shell and acceptance

- [x] Add the thin `python -m experimental.analyst.worker --run-id ID` shell with a signal
  Event and closed exit codes. Invalid invocation performs zero DB, source, process and
  network actions.
- [x] Prove strict preflight, lease race, cancellation, crash recovery, privacy and public
  parser behavior. Keep desktop launch held for the same-process model/report continuation.

## C10A outcome

C10A passed independent hostile review. The focused suite covers exact worker-context
loading, descriptor ownership and cleanup, path/symlink/mount/binding drift,
cancellation before and during reopen, malformed parser frames, and atomic candidate
format refinement. The full shared Analyst suite passed with no private data, network or
model contact. All touched production files remain below the 1,700-line pause threshold.

## C10B outcome

C10B passed independent hostile review. The engine keeps at most four private extraction
results alive, drops completed futures before admitting the next wave, and cooperatively
cancels extraction, deterministic detection and chunking before acknowledging durable
ownership release. Exact extraction and detector drift return the closed
`resume_mismatch` outcome without rewriting evidence. Fast/Deep selection, detector and
chunk caps, atomic selected-file handoff, crash recovery, successor-fence pulses and zero
Ollama contacts are regression-covered. The full shared Analyst suite passed 1,200 tests;
all touched production files remain below the 1,700-line pause threshold.

## C10C outcome

C10C passed its focused acceptance and hostile review. A canonical parser-bundle v1
binds the exact parser, detector, checkpoint, sandbox and Phase 1 source identities plus
frozen dependency versions; drift fails before dependency probes, lease claim or source
access. Preflight verifies every runtime closure, exercises the exact PDF child on a
public synthetic document and requires PyMuPDF/MuPDF 1.28.0/1.28.0, then proves the
strict networkless sandbox. Generated public PDF, DOCX, DOC and XLS files traverse the
complete worker path; a parse longer than ten seconds preserves strictly advancing
heartbeats. Signal handling is Event-only and distinguishes a durable cancel from a raw
local interruption. The focused C10C suite passed 53 tests and the full shared Analyst
suite passed 1,253 tests. All touched production files remain below 1,200 lines.

At the C10 boundary the standalone module remained deliberately activation-held: valid CLI syntax returned a
fixed content-free outcome before opening the database or starting preflight. C11 then
consumed the returned Phase 1 fence, and C12 now consumes C11's live Phase-2 fence and
atomically finalizes the report. That temporary hold is resolved; no disposable
exit/release path was added.

## Exact Phase 1 flow

1. Validate the typed run context and strict public sandbox preflight, then claim the
   singleton worker lease. A lease loser exits without claiming a file.
2. Claim only `discovered|format_identified|text_extracted|detector_scanned` pending
   files. Securely reopen and re-extract whenever later-stage raw text must be rebuilt.
3. `discovered`: sniff/extract; terminalize direct pre-parser outcomes or checkpoint
   format then exact extraction evidence.
4. `format_identified`: re-extract; require the same exact format or an allowed candidate
   refinement; checkpoint extraction or a legal parser terminal.
5. `text_extracted`: re-extract and compare exact parser/count/provenance evidence before
   scanning. Zero text becomes `complete_no_supported_content`; otherwise checkpoint the
   entire bounded detector set and selection atomically.
6. `detector_scanned`: unselected becomes `complete_detector_only`; selected regenerates
   chunks and commits the atomic C11 handoff.
7. Return the latest fence and ordered content-free handoff. Never touch
   `model_reviewed` or later stages.

## Acceptance

- Public TXT/RTF plus generated PDF/OOXML/legacy fixtures cover exact stage order,
  provenance, detector order, Fast/Deep selection and 8000/256 chunk identities.
- Every extraction terminal/detail lands at its legal stage. Generic container failures
  never claim an authenticated subtype; successes never retain a generic candidate.
- Real `os._exit` crashes after claim, parse, format, extraction, detector and chunk
  boundaries resume without duplicate provenance/hits/chunks or reopened terminals.
- Regenerated extraction/chunk mismatch fails closed. Source mutation becomes
  `source_changed_since_inventory`; parser/config drift requires a fork.
- Cancellation before claim, during reopen/hash/parser/detector and before handoff sends
  no new work, discards in-memory output and reaches `cancelled_pending_resume`.
- Two worker processes race to one lease; out-of-order parser completions still produce
  deterministic durable ordinals. A stale generation cannot checkpoint.
- A parse longer than ten seconds keeps a fresh lease without fence races.
- DB/log/repr scans find no source body, prompt, response, reasoning or exception text.
  C10 imports no Ollama modules and creates exactly zero Ollama contacts.
- C10 production files remain below 1,200 lines where practical and below the 1,700-line
  pause threshold. Tests may exceed the production rubric under the HI's test exemption.

## Primary sources

- Python subprocess isolation: https://docs.python.org/3/library/subprocess.html
- Python bounded futures and shutdown: https://docs.python.org/3.14/library/concurrent.futures.html
- Python descriptor-relative filesystem APIs: https://docs.python.org/3.14/library/os.html
- Linux `openat2` path-resolution controls: https://man7.org/linux/man-pages/man2/openat2.2.html
- SQLite transaction semantics: https://www.sqlite.org/lang_transaction.html
