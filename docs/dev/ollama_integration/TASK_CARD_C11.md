# C11 — Phase 2 model orchestration

Date: 2026-08-16
Status: **offline implementation and acceptance PASS; C12 reporting/finalization next**

## Issue

C10 returns a live fenced Phase-1 handoff containing selected files and immutable chunk
identities, but no production worker regenerates those chunks, charges each Ollama
contact, validates and grounds worksheet responses, or closes the selected files.

## Frozen boundary

C11 owns serial Phase 2 only. It consumes the C10 handoff in the same process, uses the
C9A loopback-only client through the C9B durable contact ledger, and returns a
content-free `Phase2Handoff` with the latest live fence to C12. It does not write a
report, finalize the run, enable the standalone worker CLI, read private acceptance
data, or make an unapproved live model call.

Every actual HTTP request is inserted as one `dispatching` contact before the network.
Version and tags are two separately charged controls. The compound C9A `preflight()` is
not a legal C11 dispatch path. The exact tag and digest are checked before the first
scored chat on every worker start or resume; the response stream cannot itself prove a
digest and that remains an explicit residual boundary.

## Locked decisions

- Empty Phase-1 handoff makes zero Ollama contacts and proceeds directly to C12.
- Selected files are reopened descriptor-safely, re-extracted, and compared against the
  exact durable format/parser/count/provenance/detector/chunk evidence before any prompt.
  Source or regenerated-evidence drift fails closed without rewriting history.
- The main worker thread alone owns SQLite and the successor `LeaseFence`. The Ollama
  client performs transport in its bounded helper thread while a caller-thread poll hook
  pulses the lease at most every two seconds and observes durable cancellation. The
  transport's cancel probe reads only a thread-safe Event.
- Phase 2 is globally serial. It never assumes exclusive GPU/VRAM ownership, unloads a
  model, sends `keep_alive=0`, or treats CPU offload as failure.
- A canonical chunk request is reconstructible after crash. Attempt 1 uses the frozen
  worksheet-v2 prompt. One fully grounded duplicate row is normalized locally and does
  not consume a repair.
- Attempt-1 `model_invalid`, including findings-present with every quote ungrounded,
  permits one attempt-2 `model-invalid-repair-v1` request. The repair has a distinct,
  frozen error-specific prompt/request identity; an identical or seed-only retry is
  forbidden.
- Attempt-1 timeout, transport failure, orphan or cancellation-unknown permits one
  delivery retry of the base semantic request only after a successful public
  cancellation-health generation. Identity mismatch, protocol violation and response
  limit are nonretryable and fail closed as transport/provenance coverage failures.
- Explicit `resource_busy` consumes no semantic attempt. Failures one through five wait
  15/30/60/120/240 seconds outside transactions with successor heartbeats and
  cancellation polling. Failure six writes `paused_resource`, returns the active file to
  pending, interrupts the run and clears the exact lease; C11 returns that closed outcome
  and performs no later write.
- A health obligation is derived from ordered contact history, not a mutable boolean.
  An ambiguous scored chat with no later successful/model-invalid
  `cancellation_health` contact blocks the next scored dispatch. Resume rechecks version
  and tags, waits at least two monotonic seconds, then charges the public health contact.
- Grounding owns exact substring location. A zero-finding result is valid only for
  `no_findings` or `insufficient_evidence`. `findings_present` with no retained grounded
  evidence is model-invalid.
- Findings duplicated across overlapping chunks are de-duplicated deterministically by
  category, exact quote and absolute source span, retaining the earliest chunk. Raw
  chunk text, prompt, response and reasoning are never persisted or logged.
- All chunks close before the file leaves `selected_for_model`. All-valid advances
  atomically through `model_reviewed` and `model_response_valid` to
  `complete_model_reviewed`. Otherwise the file advances to `model_reviewed` and uses
  exact failure precedence `model_transport_error > model_timeout > model_invalid`.
- C11 success never releases the lease. The CLI remains `activation_held` until C12 can
  consume the returned fence and publish/finalize the report in the same process.

## Implementation split

### C11A — client and pure request contracts

- [x] Split version and tags into public one-contact client methods while preserving the
  compound C9A compatibility API.
- [x] Add the caller-thread heartbeat poll hook and shape-only worksheet transport gate.
- [x] Freeze deterministic base/repair/health request identities and content-free Phase 2
  outcomes/handoff contracts.

### C11B — durable Phase 2 state

- [x] Add selected/model-stage claim and resume snapshots.
- [x] Verify regenerated extraction, detector and chunks at the selected stage.
- [x] Derive health obligations from ordered contact history and block scored dispatch
  until satisfied.
- [x] Atomically derive final per-file coverage and cross-chunk finding de-duplication.

### C11C — serial orchestration and acceptance

- [x] Charge version, tags, health and chat before each exact request.
- [x] Implement two-attempt repair/delivery policy, resource backoff/pause, cancellation,
  fence-loss and crash recovery.
- [x] Return a live content-free C12 handoff; keep the executable activation-held.
- [x] Pass offline fake-transport, real file-backed SQLite, subprocess crash, privacy and
  public synthetic fixture tests. No private input or live Ollama contact is authorized.

## Acceptance

- Exact request/contact order is `version`, `tags`, then serial scored/health contacts;
  a failed precharge produces zero HTTP and a crash never resends a dispatching contact.
- One non-resource outcome consumes one of exactly two semantic slots. Resource refusals
  consume none; no path creates attempt three.
- Duplicate-only normalization, grounding, grounded-zero classification and overlap
  de-duplication preserve exact counters and canonical offsets.
- Crash/reconcile tests cover every contact/checkpoint boundary and resume only from
  durable evidence. Mixed chunk failures use the frozen precedence.
- Long fake transport proves strictly advancing heartbeats. Durable cancellation wins
  over simultaneous network/schema outcomes and leaves no stale checkpoint write.
- DB/log/repr scans contain no chunk text, prompt, raw response, thinking or exception
  text beyond deliberately retained grounded quotes.
- C11 production files remain below 1,200 lines where practical and below the 1,700-line
  pause threshold. Tests remain exempt from the production size rubric.

## Offline outcome

PASS on 2026-08-16. The focused C11 contract/state/engine plus C9 client boundary passed
165 tests; the full shared Analyst regression passed 1,346 tests with 1,074 deselected.
Real `os._exit()` cases cover a crash during chat, after the response but before contact
finish, after contact success but before the valid chunk checkpoint, and after the valid
chunk checkpoint but before file closure. Resume never resends attempt one, requires the
public health barrier for ambiguous/orphaned delivery, and completes an already-valid
chunk with zero HTTP. Slow regeneration and long transport both retain strictly advancing
successor heartbeats; cancellation and lease loss stop private work without misclassifying
source drift. Privacy scans retain no prompt, raw response, thinking or exception detail.

This was offline acceptance with synthetic public fixtures and fake transport. It made no
live Ollama request and read no private corpus. The standalone worker remains deliberately
activation-held until C12 consumes the live Phase-2 fence and finalizes reporting in the
same process.

## Primary sources

- Ollama chat and structured outputs: https://docs.ollama.com/api/chat
- Ollama model tags/digests: https://docs.ollama.com/api/tags
- Ollama version control: https://docs.ollama.com/api-reference/get-version
- Ollama streaming/errors: https://docs.ollama.com/api/streaming
- SQLite transaction semantics: https://www.sqlite.org/lang_transaction.html
