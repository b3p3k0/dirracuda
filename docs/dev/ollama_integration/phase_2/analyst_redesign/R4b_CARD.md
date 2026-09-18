# R4b — Model discovery backend (charged /api/tags list + persist)

- Date: 2026-09-18
- Type: backend code card. codex implements, Claude validates.
- Depends on: R4a (analyst_discovery_contact + analyst_discovered_model, schema v6).
- Contract: Amendment A2. SAFE + ADDITIVE: does NOT relax the per-run model pin (that is R4c).
  The worker keeps running the pinned model; this card only adds a discovery capability + a
  persisted list the GUI (R4d) will show.

## Goal

Add an explicit, charged, loopback-only model-discovery call that lists the server's non-cloud
models and persists them, plus a service API the GUI will use. No per-run behavior changes.

## Deliverables

### 1. ollama contract/client — list all non-cloud models (additive; do NOT change the strict
single-model verify path used by the worker)
- `ollama_contract.build_discovery_request() -> DiscoveryRequest` (a GET /api/tags request; fixed
  loopback endpoint, redirects off, proxies ignored; content-free; its own request_sha256).
- A parser `list_local_models(body) -> tuple[DiscoveredModel, ...]` where DiscoveredModel is
  (model_tag, model_digest). It returns ALL non-cloud models (reuse the cloud-tag rejection
  `_is_cloud_model`; a cloud tag is skipped, not fatal). It does NOT narrow to the expected pinned
  model. Bounded response size/node caps as elsewhere. Keep `parse_tags_response` (the strict
  single-model verify) unchanged for the worker.
- `OllamaClient.list_models() -> tuple[DiscoveredModel, ...]`: performs the discovery GET on the
  hardened loopback session (trust_env False, max_redirects 0), returns the parsed list. No
  inference.

### 2. ollama_state — charge on the pre-run ledger (no fence/run)
- `precharge_discovery_contact(endpoint, request_sha256, *, now_utc=None, path=None) ->
  DiscoveryCharge` and `finish_discovery_contact(contact_id, state, models_found, *, now_utc=None,
  path=None)` writing `analyst_discovery_contact`. These do NOT take a LeaseFence (there is no run);
  they use short BEGIN IMMEDIATE writes with a monotonic contact_no. Content-free.

### 3. service — the API the GUI will call
- `discover_models(*, path=None) -> tuple[DiscoveredModel, ...]`: precharge a discovery contact,
  call `OllamaClient().list_models()`, finish the contact (models_found = len or a failure state),
  upsert each into `analyst_discovered_model` (endpoint+tag, digest, first/last_seen), and return
  the list. On transport/cloud/other failure: finish the contact with the right state and raise a
  typed `AnalystServiceError` (content-free) so the GUI can show "could not reach the server".
- `list_discovered_models(*, path=None) -> tuple[DiscoveredModel, ...]`: read-only from
  `analyst_discovered_model`.

### 4. Tests — `shared/tests/test_analyst_r4b.py` (fake client, no socket)
- list_local_models returns all non-cloud models and skips a `:cloud`/`-cloud` tag.
- discover_models charges then finishes a discovery contact, persists the returned models
  (idempotent upsert on re-run updates last_seen), and returns them.
- a transport failure finishes the contact as failed, persists nothing new, raises content-free.
- list_discovered_models returns the persisted rows.
- the strict worker verify path (parse_tags_response) is unchanged (a quick assertion).

## Constraints
- Does NOT touch the per-run model pin, the chat request builder, the stream parser, the worker
  identity preflight, or MODEL_TAG usage. worksheet flow untouched.
- Loopback-only, redirects off, proxies ignored, cloud rejection kept, bounded + content-free.
- Only ollama_contract.py, ollama_client.py, ollama_state.py, service.py, and the new test.
  Files < 1700 lines.

## Acceptance (Claude validates, real venv, fake client)
1. `./venv/bin/python -m pytest shared/tests/test_analyst_r4b.py -q` passes.
2. `./venv/bin/python -m pytest shared/tests -k analyst -q` => 0 failed (worker/C9/C11 unchanged).
3. `git diff` shows the strict verify path + MODEL_TAG usages unchanged (pin not relaxed here).
