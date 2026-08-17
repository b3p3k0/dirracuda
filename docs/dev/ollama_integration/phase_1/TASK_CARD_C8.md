# C8 — Durable sidecar state, resume and global worker lease

Date: 2026-08-16
Status: **Complete — accepted 2026-08-16**

## Issue

Analyst can inventory and extract supported documents, but it does not yet have durable
run/file checkpoints or an atomic global worker lease. A GUI restart, worker crash or
second GUI instance could therefore lose progress, repeat work or create two GPU owners.

## Root cause

C2 supplies PID-reuse-resistant process identity and pure reattachment decisions, while
C3–C7 supply safe extraction. No production sidecar persists run state, immutable file
terminals, attempt precharge, cancellation intent, heartbeat or lease fencing.

## Frozen scope

- Add canonical `analyst.db` resolution through `get_paths()`; no hand-built user path,
  cross-database join or main-database migration.
- Create one exact, owner-only, versioned STRICT schema after auditing real runtime state.
  Empty version-zero databases may initialize; partial, unknown or newer schemas fail
  without mutation.
- Apply accepted erratum E13: `journal_mode=DELETE`, `synchronous=EXTRA`, foreign keys on,
  mmap off, fixed busy timeout and short explicit `BEGIN IMMEDIATE` transactions.
- Persist runs, discovered files and exclusions, chunks, model attempts, detector hits,
  content-free provenance spans, grounded findings and one retained global GPU-lease
  slot.
- Keep run identity/configuration, source fingerprints and successful file terminals
  immutable. Resume may reopen only nonterminal work.
- Let a newly launched worker claim the global lease atomically with its real PID, Linux
  start ticks and boot UUID before parser/model work. A concurrent loser exits without
  touching GPU work and its run remains ready.
- Fence every heartbeat, checkpoint and release with lease generation, run, owner token
  and full process identity. Never clear or signal an exact stale-live process
  automatically.
- Persist cancel intent before any signal. Normal cancellation stays resumable; explicit
  abandon converts every remaining file to the stable `cancelled_abandoned` terminal.
- Precharge a model attempt before transport. A crash leaves a charged orphan; only the
  bounded second attempt may proceed. No prompt, raw response, reasoning or exception text
  enters the sidecar.
- Bound deterministic evidence at 10,000 hits per file. Cap + 1 writes the stable
  `detector_output_limit` terminal; it never creates a long unbounded transaction or
  repeats forever on resume.
- Accept only typed, content-free parser identities, scalar extraction counts and
  canonical provenance labels/spans. Generic metadata, raw extracted text, paths and
  exception strings are not sidecar data.
- Finalize only when every discovered file has exactly one immutable terminal. A partial
  or normally cancelled checkpoint is never labelled complete.

## Validation gate

- Exact schema, permissions, PRAGMAs, migration audit and rollback tests.
- Real two-process/two-run lease race with exactly one winner.
- Heartbeat/reconciliation fencing for fresh, dead, PID-reused, rebooted, stale-live,
  future and unverifiable process evidence.
- Crash boundaries before/after claim, during parse, during inference and finalization.
- Cancellation, resume, abandon, identity-drift and terminal-immutability tests.
- Real mergerfs multi-process exclusion, rollback, integrity, foreign-key and resume
  smoke at the canonical product path.
- Focused Analyst regression, privacy audit, production file sizes and independent hostile
  review.

## Outcome

- The exact v1 STRICT sidecar, lifecycle/checkpoint APIs and generation-fenced singleton
  lease are implemented under `experimental/analyst/`.
- Successful checkpoints revalidate exact text, provenance, detector, chunk and grounded
  finding ranges before persistence; arbitrary metadata and terminal detail text fail
  closed.
- The canonical mergerfs-backed product path passed a four-process writer race and a real
  hot-journal `os._exit()` rollback/reopen test. `quick_check`, foreign keys,
  `DELETE + EXTRA`, `mmap_size=0`, permissions and synthetic-row cleanup all passed.
- Focused C8, shared Analyst, compile and diff checks passed. Independent hostile review
  found no remaining blocker.

## Sources

- SQLite WAL-reset advisory: https://sqlite.org/wal.html#the_wal_reset_bug
- SQLite transaction control: https://sqlite.org/lang_transaction.html
- SQLite rollback-journal locking/recovery: https://sqlite.org/lockingv3.html
- SQLite PRAGMA reference: https://sqlite.org/pragma.html
