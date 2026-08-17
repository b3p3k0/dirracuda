# C9B — Durable Ollama contacts and resource pause

Date: 2026-08-16
Status: **offline implementation complete; live orchestration remains C10**

## Issue

C8 permits exactly two semantic model attempts per chunk. C9A correctly classifies an
explicit Ollama 429/503/memory response as shared-resource scheduling evidence, but C8
cannot persist that outcome without either consuming a semantic attempt or disguising it
as a generic interruption.

## Root cause

The v1 sidecar records model attempts, not HTTP contacts. A crash after dispatch is
execution-uncertain and must consume an attempt; an explicit resource refusal must not.
That distinction requires a durable precontact charge and an honest run pause state.

## Frozen correction

### Schema v2

- Preserve the exact v1 schema signature as the only migration source. Do not rebuild
  `analyst_runs`: process lifecycle stays in C8, while the joined schedule owns the
  explicit effective status `paused_resource`.
- Add STRICT `analyst_ollama_contacts`, a content-free ledger whose rows are inserted
  once, close in one direction and are immutable after terminal. Every
  `version`, `tags`, `ps`, `chat` and `cancellation_health` request is charged before
  network contact.
- Add STRICT `analyst_ollama_schedule`, exactly one row created with every run. Its closed
  state is `available|backoff|paused_resource`; it records the 0–6 consecutive-failure
  count, exact delay, bounded `not_before_utc`, explicit resume authorization, revision
  and update time.
- Keep `analyst_model_attempts` at exactly attempts 1–2. Existing v1 rows retain their
  identities and consume their existing slots.

Contact rows contain only run/chunk identity, ordinal, kind, semantic slot, request
SHA-256, positive lease generation, closed status, timestamps, optional unique attempt
link, `resource_failures_before` (0–6) and nullable `resource_failures_after` (0–6).
`after` is null only while dispatching; resource busy records `min(before+1, 6)`;
generation success/model-invalid records zero; every other outcome preserves `before`.
They never contain a URL, prompt, source text, response, thinking, error text or exception
detail. Cross-row audit proves a contact's chunk belongs to its run and a linked attempt
matches both the chunk and reserved semantic number.

### Contact and attempt rules

- The contact row is the authoritative pre-dispatch charge. One partial unique index
  permits only one global `dispatching` contact, matching the singleton worker lease.
- Controls and cancellation health have no chunk or semantic attempt number.
- Chat reserves the next semantic number. An explicit `resource_busy` closes the
  contact, advances backoff and creates no semantic attempt, so that number may be used
  again.
- Every other chat terminal status creates/links exactly one semantic attempt in the
  same transaction. This includes success, model-invalid, timeout, transport,
  protocol/limit/identity failure and `cancelled_unverified`.
- A dispatching chat orphaned by crash is conservatively `orphaned_unknown` and consumes
  its reserved semantic attempt. A control orphan never consumes a semantic attempt.
- Before precharge, the adapter revalidates the exact C9A request and fence. After
  precharge, ambiguity is never rewritten as resource contention.
- Only generation `success` or `model_invalid` resets resource backoff; both carry a
  terminal stream. Timeout, transport, protocol/limit/identity failure, cancel and orphan
  outcomes preserve it because C9A exposes no separate server-answered proof. Cheap
  version/tags/ps controls do not prove resource recovery.

### Pause and resume

- Resource failures use the frozen 15/30/60/120/240-second waits. Waits 1–5 keep the run
  `running`, retain the exact lease, heartbeat and poll cancellation outside SQLite.
- Failure 6 atomically closes the contact, records a 300-second cooldown, sets the
  schedule `paused_resource`, returns any active file to pending, moves the C8 run to
  `interrupted` and clears the exact lease.
- A resource pause may be explicitly resumed only at/after `not_before_utc`; authorization
  and the later claim are fenced, and claim rejects an early or unauthorized pause.
  Resume does not reset the failure count. Another resource result pauses again;
  successful generation resets it.
- Wall-clock rollback cannot create an unbounded wait: remaining time is clamped to
  `[0, recorded_delay_seconds]`; live waiting uses a monotonic deadline. Completion is
  handed back through an exact fence/schedule-revision transaction, which moves an
  active backoff due or authorizes a lease-free paused resume even if wall UTC remains
  behind. The arithmetic helper alone never authorizes dispatch.
- Cancellation of a paused no-lease run becomes `cancelled_pending_resume`; abandon is
  terminal. Finalization rejects a non-available schedule or dispatching contact.
- Bound retained evidence at 64 control contacts per run and 16 chat contacts per chunk.
  A cap refuses another dispatch with zero HTTP and no mutation; C10 must surface the
  operator action rather than loop, prune or overwrite history.

### Migration

- Fresh databases initialize directly as exact v2. Exact v2 reopen is idempotent.
- The only v1 migration candidate is an exact empty development sidecar: zero domain
  rows and the unowned generation-zero lease. Any populated/active v1 is rejected without
  mutation and needs an explicit later migration card. The measured canonical v1 meets
  this narrow gate.
- Under `BEGIN IMMEDIATE`, re-audit exact v1 and the empty gate, create only the two new
  tables and indexes, then write `user_version=2` last. No existing table is rebuilt or
  copied and foreign keys stay enabled.
- Run exact-v2 schema validation, cross-row checks, `foreign_key_check` and `quick_check`
  before literal `COMMIT`; literal rollback on every failure; re-enable and verify foreign
  keys after the transaction.
- Hot-journal recovery admits only owner-validated DANA headers at known versions 1 or
  2, then performs the exact schema audit. Foreign/partial state remains nonmutating.

## Production split

- `db_schema.py`: exact v1/v2 signatures and transactional migration.
- `store.py`: known-version audit/hot-journal handling.
- `lease.py`: schedule-aware claim/cancel/reconcile fencing.
- New `contact_contract.py`: pure enums, exact status mapping, hashes and limits.
- New `ollama_state.py`: typed contact charge/finish/backoff/recovery API. Do not grow
  the already-good 1,341-line `checkpoint.py`.

## Acceptance gate

- Fresh v2, exact-empty-v1 migration, populated-v1 nonmutation refusal, idempotence,
  two-process migration race and injected failure/crash at every DDL/version boundary.
- Hot DELETE-journal recovery for known v1/v2; foreign/partial DB and journal hashes
  unchanged on refusal; canonical mergerfs migration smoke.
- Exact contact precharge/fence/idempotence/outcome matrix, crash orphaning, cancellation
  precedence and one-contact concurrency.
- Six resource contacts leave semantic attempt count zero; later real outcomes consume
  exactly attempts 1 and 2; no third attempt or call.
- Exact backoff/cooldown boundary, clock rollback clamp, paused claim/resume/cancel/
  abandon, heartbeat-safe waits and finalization refusal.
- N/N+1 ledger bounds, STRICT coercion hostility, privacy value scans, FK/quick/integrity
  checks, C8/C9 regression, file sizes, README and lessons review.

## Sources

- SQLite ALTER TABLE: https://sqlite.org/lang_altertable.html
- SQLite foreign-key PRAGMA: https://sqlite.org/pragma.html#pragma_foreign_keys
- SQLite transactions: https://sqlite.org/lang_transaction.html
- Ollama errors: https://docs.ollama.com/api/errors
- Ollama streaming: https://docs.ollama.com/api/streaming
