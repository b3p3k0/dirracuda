# R4c — Per-run model selection (relax the exact pin) [SECURITY-CRITICAL]

- Date: 2026-09-18
- Type: SECURITY-CRITICAL backend code card. codex implements; Claude validates HARD; the operator
  (HI) reviews the Ollama-contract diff before this card is considered done.
- Depends on: R4b (discovery + analyst_discovered_model). Contract 7.2 + Amendment A2.
- HI approval: full per-run relax approved 2026-09-18.

## Goal

Let a chosen model actually drive a run. The exact pinned tag+digest (R89, a Critical egress
control) becomes a PER-RUN recorded selection. What stays fail-closed: loopback-only endpoint,
redirects off, proxies ignored, cloud-tag rejection, bounded/content-free, and digest INTEGRITY
(the chosen tag must resolve to the run's recorded digest at run time, else fail closed).

## Exact changes (thread the run's model through; keep MODEL_TAG as the DEFAULT only)

1. **Run creation** (`service.create_directory_run`, `create_manifest_run`): accept optional
   `model_tag` and `model_digest`; default to `ANALYST_DEFAULTS` when not given (current behavior
   preserved). Reject a cloud tag at creation. Store them on the run row. (The GUI/R4d passes a
   (tag, digest) chosen from `analyst_discovered_model`.)
2. **Chat request** (`ollama_contract`): `_build_chat_request` / `build_chat_request` /
   `build_repair_chat_request` take the run's `model_tag`; the payload `"model"` uses it;
   `ChatRequest.model_tag` carries it; `validate_chat_request` and the request-keys check compare
   against the request's own `model_tag` (not the constant). `build_read_chat_request` (R3b)
   likewise takes the run's model_tag.
3. **Response** (`ollama_client`): `ChatStreamParser` is constructed with the run's model_tag so
   the streamed response's model field is validated against the chosen model, not MODEL_TAG.
4. **Worker identity preflight** (`phase2._run_identity_preflight` + `_require_runtime_contract`):
   `check_tags` verifies the run's `model_tag` resolves to a digest; assert that resolved digest
   equals the run's stored `model_digest` (INTEGRITY: the model behind the tag must not have
   changed) - mismatch fails closed (a distinct content-free reason). Remove the hard
   `== MODEL_TAG / == MODEL_DIGEST` equality; replace with well-formed + cloud-reject + the
   stored-digest integrity check. The request identity (R92) reconstruction uses the run's
   model_tag from the durable context.
5. **Threading**: the model_tag flows from `WorkerRunContext` (already has model_tag/model_digest)
   into the request builders and the parser. Do not read MODEL_TAG as the source of truth in the
   per-run path; MODEL_TAG remains only the default for run creation and the module default assert.

## Preserve (do NOT change)
- Loopback-only endpoint, redirects off, proxies ignored (R89 transport controls).
- Cloud-tag rejection (`:cloud` / `-cloud`) at list, creation, and request build (R1).
- Bounded response, content-free ledgers/logs, the charged contact discipline, two-attempt budget,
  fence, cancellation, resume.
- The worksheet prompt/schema SHA pins (only the MODEL changes, not the prompt).

## Tests
- Update the C9/C11 tests that pinned `== MODEL_TAG` to assert against the run's model_tag; the
  DEFAULT run still uses qwen3.6:27b so most stay green with the default. Do NOT weaken transport,
  cloud-reject, bounded, or charged-contact assertions.
- `shared/tests/test_analyst_r4c.py`: a run created with a non-default model builds requests whose
  payload "model" is that model and whose stream parser accepts that model; the worker preflight
  verifies tag->stored-digest and FAILS CLOSED on a digest mismatch (model changed behind the tag);
  a cloud tag is rejected at creation and at request build; the default (no model given) still uses
  the pinned model; request identity (request_sha256) reconstruction uses the run's model_tag.

## Constraints
- Only the model identity relaxes. Every other C9 control stays. Content-free failures with exact
  reasons. Files < 1700 lines. Allowed: ollama_contract.py, ollama_client.py, phase2.py,
  read_reduce.py (its read request builder), service.py, worker_contract/context if a field is
  needed, and the C9/C11 tests + the new test. Do NOT touch the worksheet prompt/schema.

## Acceptance (Claude validates, then HI reviews)
1. `./venv/bin/python -m pytest shared/tests/test_analyst_r4c.py -q` passes.
2. `./venv/bin/python -m pytest shared/tests -k analyst -q` => 0 failed (all C9/C11 safety tests
   green; default run still pinned).
3. Claude presents the full ollama_contract.py + phase2.py + ollama_client.py diff to the operator;
   the card is done only after HI review, because it relaxes a Critical control.
