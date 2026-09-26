# C12 — Streaming report export and atomic finalization

Date: 2026-08-16
Status: **implementation and offline acceptance PASS; C13 desktop integration next**

## Issue

C11 returns a content-free `Phase2Handoff` while retaining the exact live worker lease.
Every discovered file is terminal, but no production boundary yet renders the durable
coverage/evidence, publishes owner-only artifacts, or commits the run as complete.

## Frozen boundary

C12 consumes the live C11 fence, enters `finalizing`, streams a coverage-first report
from immutable terminal rows, atomically replaces four owner-only artifacts under the
persisted absolute `output_root`, and commits the artifact-manifest SHA while clearing
the lease. It does not enable the desktop launcher, hydrate Running Tasks, change the
primary database, copy original documents, or read source-document content.

Once that same-process continuation is present, C12 removes the temporary C10/C11
`activation_held` worker gate. The module CLI runs Phase 1, Phase 2, and report
finalization without releasing either handoff lease. This activates only the worker
entrypoint; desktop launch, task hydration, and user-facing controls remain C13.

The four fixed artifacts are:

- `run.json`: compact canonical run identity, versions, and coverage counts;
- `findings.jsonl`: canonical unmodified detector and retained model evidence;
- `findings.csv`: a spreadsheet-safe derived view of the canonical evidence; and
- `report.html`: static coverage-first HTML with partitioned local-only pages when
  inventory or findings exceed one page.

The publication commit is the SQLite transition to `complete`. Files written while the
run is still `finalizing` are not a complete report. A crash may leave a complete set of
deterministic artifacts on disk, but reconciliation clears the finalization token and a
resume replaces them before committing a new manifest.

## Locked decisions

- Reporting reads only the Analyst sidecar. It never reopens source files and never
  imports or contacts Ollama.
- Coverage is the first HTML section and distinguishes discovered, detector-scanned,
  selected, model-reviewed, successful terminals, and every stable failure terminal.
  Inventory exclusions are reported separately and never counted as discovered files.
- Model findings are reportable only through files terminal at
  `complete_model_reviewed`; partial chunk rows never masquerade as complete coverage.
  Detector and model evidence remain separate through an explicit `evidence_kind`.
- JSONL is the canonical raw evidence view. CSV and HTML escape or sanitize their own
  display copies without changing JSONL. CSV guards formula/control/separator prefixes,
  including full-width Unicode equivalents, in every textual cell.
- HTML is static, uses no JavaScript, remote asset, image, form, link target, or data
  URL, and emits the exact frozen CSP. Every value is context-escaped.
- Reads are paginated and connections/cursors are short-lived. No transaction spans a
  page render, filesystem operation, heartbeat, sleep, or rename.
- The finalizing worker may pulse its exact successor fence. Finalization itself is not
  cancellable; cancellation remains a pre-finalization control. Lease loss aborts
  publication and no completion row is written.
- The output path is the exact persisted absolute `output_root`. Path components are
  walked without following symlinks. The final directory and artifacts must be owned by
  the current user; the report directory is 0700 and files are 0600. Fixed artifact
  names only are accepted.
- Each artifact is written to a same-directory exclusive 0600 temporary file, flushed,
  fsynced, and atomically renamed. Existing symlinks, special files, foreign owners, or
  unsafe modes fail closed. The directory is fsynced after publication.
- The persisted manifest SHA is the SHA-256 of canonical JSON describing the report
  schema and the sorted final artifact `(name, size, sha256)` identities. The manifest
  does not include itself and is not embedded recursively in `run.json`.
- Routine errors/results/reprs are content-free. The only raw values exported are the
  accepted evidence fields in the owner-only report artifacts.

## Implementation split

### C12A — pure report contracts and paginated snapshot

- [x] Freeze typed run/coverage/inventory/finding/artifact contracts and canonical
  serialization.
- [x] Add bounded, ordered, read-only summary and page queries with exact lifecycle and
  fence validation.
- [x] Freeze spreadsheet guards and report pagination limits.

### C12B — secure writer and renderers

- [x] Walk/create the exact output directory without symlinks or mount/path escapes.
- [x] Stream canonical JSONL/CSV and static partitioned HTML without retaining the full
  graph.
- [x] Atomically replace owner-only artifacts and derive the canonical manifest SHA.

### C12C — finalization orchestration and acceptance

- [x] Enter finalizing, retain a fresh successor heartbeat between pages/artifacts, and
  atomically complete/clear the lease only after durable publication.
- [x] Replace the held worker CLI with the same-process Phase 1 → Phase 2 → C12
  continuation and fixed content-free exit outcomes.
- [x] Recover from crashes before/after each write/rename and before the DB publication
  commit without ever exposing an incomplete run as complete.
- [x] Pass hostile path/symlink, HTML/CSV injection, privacy, deterministic-output,
  pagination, scale, cancellation/fence, and zero-source/zero-network tests.

## Acceptance

- A run cannot begin finalization with one nonterminal file, dispatching attempt/contact,
  unavailable resource schedule, contradictory terminal/stage evidence, or stale fence.
- Empty and no-supported-content runs still produce all four artifacts with honest zero
  counts and exact completion code.
- JSONL raw values round-trip unchanged; CSV injection strings are guarded; HTML markers
  cannot create markup, scripts, requests, images, links, or event handlers.
- A 46,724-file synthetic run is read and written in bounded pages; tests prove no full
  finding/inventory fetch and strictly advancing finalization heartbeats.
- Any crash before `finish_finalization` leaves the DB non-complete and recoverable. A
  crash after it leaves all final artifacts durable and their recomputed manifest SHA
  equal to the DB value.
- Report/log/result representations do not contain source text, prompts, raw model
  responses, reasoning, exception text, or output paths.
- Production files remain below the 1,700-line pause threshold; tests remain exempt from
  the production size rubric.

## Primary sources

- SQLite atomic commit: https://sqlite.org/atomiccommit.html
- Python descriptor-relative filesystem operations: https://docs.python.org/3/library/os.html
- Python CSV format: https://docs.python.org/3/library/csv.html
- OWASP CSV injection: https://owasp.org/www-community/attacks/CSV_Injection
- Content Security Policy: https://developer.mozilla.org/docs/Web/HTTP/CSP

## Offline outcome

PASS on 2026-08-16. The C12 suite publishes and independently re-verifies the exact
owner-only artifact set, proves raw JSONL round-trip versus CSV/HTML display safety,
rejects output-directory and artifact symlinks, and keeps coverage ahead of findings.
A real process exit after all artifacts are fsynced leaves the run `finalizing` with no
manifest publication; reconciliation and resume deterministically replace the artifacts
before the single SQLite completion/lease-clear commit. The exact 46,724-file design
target streams through 94 inventory partitions with successor heartbeats and no full
inventory graph.

The full Phase 1 → Phase 2 → C12 worker continuation is active and returns one fixed
content-free outcome. The focused C8–C12 matrix passed 700 tests and the complete shared
Analyst regression passed 1,365 tests with 1,074 deselected. No private corpus or live
network/model contact was used. Desktop launch and task/report-browser surfaces remain
C13.
