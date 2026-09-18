# R3b — Host READ Reduce Engine

- Date: 2026-09-18
- Type: code card. codex implements, Claude validates.
- Depends on: R1 (read_worksheet.parse_read, report_json.HostRead), R3a (analyst_read_contact
  ledger; analyst_read/analyst_read_exposures from R2/v4).
- Boundary: new runtime engine. Reuse the existing safety primitives; do NOT rewrite phase2's
  per-chunk flow (worksheet-v2 stays, per Amendment A1).

## Goal

After phase2 finishes reviewing files, run ONE host-level "READ" model generation over the
per-chunk summaries the map already produced, and persist the host read. Best-effort: a failed
or exhausted read must NOT fail the run (assistive, no-action role). Cancellation MUST still win.

## Design

New module `experimental/analyst/read_reduce.py` with a public entry:
`run_read_reduce(context: WorkerRunContext, handoff: Phase2Handoff, stop_event, *, path=None,
dependencies: ReadReduceDependencies | None = None) -> None`.

Steps:
1. **Resume guard**: if an `analyst_read` row already exists for the run, return immediately
   (idempotent; the read is done). Also return early if the run has zero model-reviewed chunks
   (nothing to read) after writing a deterministic minimal read is NOT R3b's job — R3c owns the
   fallback. R3b only writes `analyst_read` when the MODEL read succeeds.
2. **Build reduce input** (new read-only reader, e.g. in `read_reduce.py` or `report_state.py`):
   read per-chunk `document_type`, `subject`, `assessment` for `model_response_valid` chunks,
   plus a compact grounded-facts digest (counts by category, and the top grounded quotes with
   file + category, bounded). Render to one bounded plain-text summary string, capped to
   `ollama_contract.MAX_SOURCE_CHARS`. Never include raw document bodies beyond the already-stored
   bounded quotes/subjects.
3. **Request**: add `ollama_contract.build_read_chat_request(summary_text, *, nonce) -> ChatRequest`
   mirroring `_build_chat_request` but using `read_worksheet.build_read_prompt` +
   `read_worksheet.read_schema()` (its own pinned identity), same payload envelope (stream=True,
   think=False, GENERATION_OPTIONS, KEEP_ALIVE), same bounds. Use a FRESH RANDOM FENCE nonce per attempt (a new `FENCE_<16 hex>` token, like
   the client generates elsewhere). The read needs no R92-style deterministic replay: it is
   idempotent via the step-1 analyst_read resume guard, and the ledger tracks attempts by
   attempt_no. request_sha256 is recorded for audit, not strict resume-replay.
4. **Charge/finish** (new, in `ollama_state.py`, on the R3a ledger): `precharge_read_contact(
   fence, attempt_no, request_sha256, *, path=...) -> ContactCharge`-like and
   `finish_read_contact(fence, contact_id, status, *, path=...) -> ContactFinish`-like, writing
   `analyst_read_contact`. Mirror the control-contact discipline (charge before HTTP; terminal
   status on finish; resource-busy advances the SAME per-run resource schedule without consuming
   an attempt).
5. **Loop** (at most 2 semantic attempts): precharge -> `client.chat(request, expected_sha256=...,
   cancel=..., poll=...)` -> finish. On SUCCESS: `parse_read(content)` -> persist. On MODEL_INVALID
   / REQUEST_TIMEOUT / TRANSPORT_UNAVAILABLE: consume an attempt, retry once (a single repair or
   the same base request; a repair prompt is optional for the read — one plain retry is
   acceptable). On RESOURCE_BUSY: back off via the resource schedule; if it pauses/releases,
   propagate the same pause behavior phase2 uses (do NOT strand the fence). After 2 attempts:
   record the read as unavailable (leave `analyst_read` absent) and return without error.
6. **Persist** (new store fn, e.g. `store.write_host_read(fence, HostRead, *, read_mode,
   files_read, files_total, flagged_files, now_utc=..., path=...)`): insert `analyst_read` +
   `analyst_read_exposures` in one short BEGIN IMMEDIATE, fenced. `contacts_json` is canonical
   JSON of the contacts list. Exposure rows numbered 1..N from HostRead.top_exposures.
7. **Fence/heartbeat/cancel**: pulse the fence during the call. Cancellation is COOPERATIVE via
   `stop_event` only: pass a cancel callback to `client.chat` that returns True when
   `stop_event.is_set()`, so the client closes the stream and returns CANCELLED_UNVERIFIED;
   then finish the contact and raise a new `ReadReduceCancelled` exception. Do NOT import
   phase2's private stop/fence internals; do NOT replicate its durable-cancel-vs-local-stop
   logic (durable cancel is handled by phase2 before the reduce). Do NOT release the fence
   (finalize owns release).

## Wiring

`worker.run_worker`: call `run_read_reduce(context, phase2_handoff, stop_event, path=...)` AFTER
`run_phase2` returns and BEFORE `finalize_report`. Catch `ReadReduceCancelled` and map it to `WorkerOutcome.CANCELLED` (the worker then leaves the
run resumable, same as a phase2 cancel path). Swallow every NON-cancel reduce failure
content-free so the run still proceeds to `finalize_report` (R3c writes the fallback read).
Never leak exception text. On cancel do NOT release the fence here.

## Constraints

- Do NOT modify the per-chunk worksheet flow, `_dispatch_chunk`, `finish_valid_attempt`, or the
  C11 request identity. worksheet-v2 stays (Amendment A1).
- Every Ollama contact precharged (R3a ledger). Loopback-only; cloud-tag rejection unchanged.
- No raw document content in logs/reprs/exceptions/tests/prompts beyond the bounded grounded
  quotes/subjects already stored. Content-free failures.
- New files < 1700 lines. Match package style. Reuse OllamaClient, the resource schedule, fence,
  read_worksheet, report_json, ANALYST_DEFAULTS.
- Allowed files: new `read_reduce.py`; additive functions in `ollama_state.py`, `ollama_contract.py`,
  `store.py`; wiring in `worker.py`; a reduce-input reader (in `read_reduce.py` or `report_state.py`);
  tests `shared/tests/test_analyst_r3b.py`.

## Acceptance (Claude validates, real venv, with fakes/monkeypatch — no live Ollama)

1. `test_analyst_r3b.py` covers: success persists analyst_read + exposures (rows numbered,
   contacts_json canonical); resume skips when analyst_read exists; two invalid answers ->
   read unavailable, run continues, no crash; resource-busy backs off without consuming an
   attempt; cancellation during the read wins and does not strand the fence; content-free on
   failure. Uses a fake OllamaClient (no socket), like the C11 engine tests.
2. `./venv/bin/python -m pytest shared/tests -k analyst -q` => 0 failed (all C11/C12 safety
   tests still green; worksheet flow untouched).
3. `git diff` shows the per-chunk worksheet flow and C11 request identity unchanged.
