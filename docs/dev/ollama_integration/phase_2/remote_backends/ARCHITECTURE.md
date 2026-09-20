# Remote Backends — Proposed Architecture

- Date: 2026-09-20
- Status: draft for review. Nothing here is frozen. N0 freezes it.
- Audience: the PA/RA who writes N0, and the DA who implements N1-N3.

Every decision reference (D1-D9) points at the ledger in `README.md`.

---

## 1. Scope

In scope:

- Talk to Ollama or llama.cpp `llama-server`.
- Talk to either one on `localhost` or on another host.
- Named server profiles with stored credentials.
- Transport security for the remote case.

Out of scope:

- Cloud model providers. The cloud-tag rejection in `ollama_contract.is_cloud_model_tag`
  stays.
- Changing prompts, chunking, the risk rubric, or `report.json` content.
- Re-running the C0B benchmark. The read-first contract already relaxed the model pin to
  a per-run recorded selection (`CONTRACT_READ_FIRST.md:337`).

---

## 2. Current state

### 2.1 The remote UI exists and does nothing

`gui/components/experimental_features/analyst_tab.py:156-158` defines:

```python
self._server_kind_var = tk.StringVar(value="local")
self._server_host_var = tk.StringVar(value="127.0.0.1")
self._server_port_var = tk.StringVar(value="11434")
```

Those variables are read by nothing outside the widget construction at lines 396-427.
`_discover_models()` calls `experimental.analyst.service.discover_models()`, which takes
no host argument.

This is a live instance of the "UI contract must be reachable" lesson: an affordance
with no path to the backend. N1 either wires it or removes it.

### 2.2 The endpoint is frozen into a constant and a hash

`experimental/analyst/ollama_contract.py:21-25`:

```python
OLLAMA_ENDPOINT: Final = "http://127.0.0.1:11434"
OLLAMA_VERSION_URL: Final = f"{OLLAMA_ENDPOINT}/api/version"
OLLAMA_TAGS_URL: Final = f"{OLLAMA_ENDPOINT}/api/tags"
OLLAMA_PS_URL: Final = f"{OLLAMA_ENDPOINT}/api/ps"
OLLAMA_CHAT_URL: Final = f"{OLLAMA_ENDPOINT}/api/chat"
```

The URL is also an input to `_discovery_identity_bytes()`, alongside
`trust_env: False`, `proxies_ignored: True`, `allow_redirects: False`. That hash seals
the request shape.

`OllamaIdentity.__post_init__` (line 286) hard-asserts `self.endpoint == OLLAMA_ENDPOINT`.
That single assertion is the blocking one.

Usage count, excluding tests:

| File | References |
| --- | --- |
| `experimental/analyst/ollama_contract.py` | 21 |
| `experimental/analyst/ollama_client.py` | 11 |
| `scripts/analyst_c9_live_acceptance.py` | 9 |
| `experimental/analyst/contact_contract.py` | 4 |
| `scripts/analyst_benchmark/c0b{2,4,5,6}_runtime.py` | 2 each |
| `experimental/analyst/service.py` | 2 |
| `experimental/analyst/ollama_state.py` | 2 |

The benchmark scripts under `scripts/analyst_benchmark/` are frozen evidence. They must
keep resolving to loopback. Give them a module-level default rather than editing them.

### 2.3 The database is already multi-endpoint

`experimental/analyst/db_schema.py`:

- `analyst_discovered_model` has `PRIMARY KEY(endpoint, model_tag)` (line 534).
- `analyst_discovery_contact` carries `endpoint TEXT NOT NULL` (line 518).
- `analyst_runs` is indexed on `(ip_address, port, created_at_utc, run_id)` (line 331).

No schema fight for the endpoint itself. Profiles and identity kind are new columns.

### 2.4 Reusable parts already in the tree

| Need | Existing component |
| --- | --- |
| Encrypted credential store | `experimental/keymaster/store.py` — AES-GCM, PBKDF2 root key, provider-keyed, multi-key, `last_used` |
| Session mute flags | `gui/utils/session_flags.py` — in-process dict, resets on restart |
| Mute dialog pattern | `gui/components/dorkbook_window.py:251-312` (`_DeleteConfirmDialog`) |
| Charged contact ledger | `experimental/analyst/ollama_state.py`, `contact_contract.py` |

---

## 3. Protocol differences that drive the design

| Concern | Ollama native | llama.cpp `llama-server` |
| --- | --- | --- |
| List models | `GET /api/tags` — tag + sha256 digest | `GET /v1/models` — id defaults to the model file path unless `--alias` |
| Chat | `POST /api/chat` | `POST /v1/chat/completions` |
| JSON schema output | `format: {schema}` | `response_format: {"type": "json_schema", ...}` |
| Context size | `options.num_ctx`, per request | server launch flag `-c`; readable at `GET /props` |
| Max new tokens | `options.num_predict` | `max_tokens` |
| `top_k` / `min_p` / `repeat_penalty` | supported | supported as llama.cpp extensions |
| Model residency | `keep_alive` | always resident |
| Health | `GET /api/version` | `GET /health` |
| Auth | none, ever | `--api-key` → `Authorization: Bearer`; `/health` and `/v1/models` stay open |
| Concurrency | `OLLAMA_NUM_PARALLEL`, `OLLAMA_MAX_LOADED_MODELS` | `--parallel N` slots; busy state at `GET /slots` |
| Effective context under concurrency | unchanged per request | version-dependent; **measured as the full `--ctx-size` per request** on `b1-f280b26` (§6.1) |
| Multi-model serving | one server, models swap in and out | optional **router mode**: a catalogue, a child server per model, `--models-max` resident (§6.1) |
| Context overflow | — | clean `400 exceed_context_size_error` with `n_prompt_tokens` and `n_ctx`. No silent truncation |
| Reasoning traces | `think` option | separate `reasoning_content` field; disable with `chat_template_kwargs.enable_thinking=false` (§7.4) |

Ollama's own `/v1` shim does **not** close this gap. It maps a fixed allowlist of OpenAI
fields to model options; `num_ctx` is not on that list and is silently ignored, and
`/v1/models` returns only `id`, `object`, `created`, `owned_by`. That is why D1 keeps two
transports.

Sources: [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md),
[Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility),
[ollama/ollama#6137](https://github.com/ollama/ollama/pull/6137).

Every row in the llama.cpp column is an expectation from public docs. `PROBE_PROTOCOL.md`
verifies it against a real host before N0 freezes anything.

---

## 4. Module layout

New modules under `experimental/analyst/`:

```
endpoint.py            Endpoint parsing, validation, private-range check, TLS policy.
profiles.py            Server profile CRUD on the sidecar DB.
backends/__init__.py   Registry + dispatch by BackendKind.
backends/base.py       Pure types: BackendKind, ServerProfile, BackendCapabilities,
                       ModelIdentity, shared result and error types.
backends/ollama.py     Thin wrapper over the existing ollama_client.
backends/openai_api.py New adapter: llama.cpp, vLLM, LM Studio, TGI.
```

Kept as-is, parameterised not rewritten:

- `ollama_client.py`, `ollama_protocol.py`, `ollama_contract.py`, `ollama_state.py`

`backends/base.py` is pure: no network, no DB, no Tk. Mirror the
`shared/sherlock/test_sherlock_purity.py` guardrail to enforce it.

### 4.1 File size impact

Current sizes of files N1-N3 will touch:

| File | Lines | Rubric |
| --- | --- | --- |
| `experimental/analyst/ollama_contract.py` | 977 | excellent |
| `experimental/analyst/ollama_client.py` | 745 | excellent |
| `experimental/analyst/service.py` | 1000 | excellent |
| `experimental/analyst/db_schema.py` | 1272 | good |
| `gui/components/experimental_features/analyst_tab.py` | 1323 | good |

Two carry real risk:

- `analyst_tab.py` at 1323 gains profile management UI, a Test button flow, and consent
  dialogs. It will cross 1700 unless the profile editor lands in its own module. Plan a
  satellite module from the start, following the `_mb()` / `_d()` dispatch discipline in
  `CLAUDE.md`.
- `ollama_contract.py` at 977 should **shrink**: the endpoint constants move out to
  `endpoint.py`. If it grows instead, the change is going the wrong way.

---

## 5. Types

Shape only. N0 fixes the exact fields.

```python
class BackendKind(Enum):
    OLLAMA = "ollama"            # native /api/chat
    OPENAI_COMPAT = "openai"     # llama.cpp, vLLM, LM Studio, TGI

@dataclass(frozen=True, slots=True)
class ServerProfile:
    profile_id: int
    name: str                    # "mimir", "localhost"
    scheme: str                  # "http" | "https"
    host: str
    port: int
    backend_kind: BackendKind
    backend_detected: bool       # False when the operator overrode detection
    keymaster_key_id: int | None # bearer token, None for no auth
    cert_fingerprint: str | None # pinned sha256, None when the OS trust store verifies
    plaintext_ack: bool          # D2 private-network opt-out
    consent_muted: bool          # D7 mute forever, per profile

@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    max_context: int             # n_ctx the server will actually honour
    can_set_context_per_request: bool
    supports_json_schema: bool
    supports_seed: bool
    server_version: str

@dataclass(frozen=True, slots=True)
class ModelIdentity:
    kind: str                    # "digest" | "reported"
    model_name: str
    digest: str | None           # Ollama only
    properties_hash: str | None  # OpenAI-compatible: hash of the /props + /v1/models record
```

`ModelIdentity.kind` is what `report.json` and the report view render. "digest" means
verified. "reported" means the server named a model and nothing proves which weights
answered.

The probe showed "reported" is richer than assumed. A llama.cpp router gives, per model:
`model_path`, `n_params`, `size`, `ftype`, `n_vocab`, `n_ctx`, `n_ctx_train`, plus a
`system_fingerprint` (the server build) on **every completion response**. That composite
is a strong fingerprint — strong enough to detect a swapped model in practice, though
still not a cryptographic digest. The "reported, unverified" label stands; the evidence
behind it does not need to be thin.

---

## 6. Preflight — the Test button

One code path, run before any profile is usable and before any run starts.

| Step | Ollama | OpenAI-compatible | Failure |
| --- | --- | --- | --- |
| 1 Endpoint policy | `endpoint.py` validates scheme, host, port | same | Refuse. Name the rule that failed. |
| 2 TLS | OS trust store, or pinned fingerprint | same | Refuse. Offer to pin the presented fingerprint. |
| 3 Detect | `GET /api/version` answers | `GET /props` answers | Neither answers → unreachable. Both answer → honour the manual override. |
| 4 Auth | n/a | `GET /v1/chat/completions` probe; 401 means the token is wrong or missing | Refuse with "server requires a token". |
| 5 Capabilities | `num_ctx` is per request, so `max_context` is model-bound | detect router vs single at `/props`, then read context per §6.1 | — |
| 6 Context gate (D4, D10) | — | model context < required → refuse early and name the model's limit | Refuse. The server also refuses independently. |
| 7 Busy check (D11) | `GET /api/ps` | `GET /slots` | Warn only. Never block. |
| 8 Models | `GET /api/tags` → tag + digest | `GET /v1/models` → id | Empty list → refuse. |
| 9 Record | persist profile + capabilities + model list | same | — |

Required context comes from the run kind. Today that is `NUM_CTX = 8192` for chunk
generation and `READ_NUM_CTX = 16384` for the host read
(`ollama_contract.py:40-46`).

Step 6 is the single most important gate in this design. Without it llama.cpp silently
drops the front of the prompt and Analyst produces a confident report from a truncated
document.

### 6.1 Context discovery (D10, corrected 2026-09-20)

**The original D10 was wrong.** It said `llama-server` divides `--ctx-size` by
`--parallel`, so the gate must use the per-slot value. The probe disproved that on build
`b1-f280b26`: a server launched `--ctx-size 32768` with `total_slots: 4` processed an
11,058-token prompt in full and rejected a 42,058-token one with `n_ctx: 32768`.

The division behaviour is **version-dependent**, which is worse than either answer.
Analyst must not hardcode either rule.

Two further discoveries change this section more than the division question did.

#### Router mode

A capable shared box does not run one model. `mimir` runs llama.cpp as a **router**: one
front door owning a catalogue, spawning a child server per model on demand, capped by
`--models-max`. Querying the router:

```json
{"role":"router","max_instances":2,"models_autoload":true,
 "model_alias":"llama-server","model_path":"none",
 "default_generation_settings":{"params":null,"n_ctx":0}}
```

`n_ctx` is **0**. A preflight that reads `/props.default_generation_settings.n_ctx` and
compares it against a requirement either refuses everything or, if it treats 0 as
"unknown", approves everything.

#### The rule

```
if /props.role == "router":
    context comes from /v1/models[id].meta.n_ctx           (model loaded)
                    or --ctx-size parsed from status.args  (not yet loaded)
else:
    context comes from /props.default_generation_settings.n_ctx
```

Then, regardless of path, **always honour the server's own refusal**:

```json
{"error":{"code":400,"type":"exceed_context_size_error",
  "n_prompt_tokens":42058,"n_ctx":32768}}
```

llama.cpp errors cleanly on overflow and does not silently truncate. The error is
machine-readable, carries both numbers, and is proxied unchanged through the router.

That demotes the preflight gate from *the only protection* to *fail-fast UX*: refuse
before a long run rather than after the first chunk. Keep it — a run that dies on chunk 1
of 400 wastes the operator's time — but the correctness guarantee now comes from the
server, which is a better place for it.

`exceed_context_size_error` maps directly to a precise message:

```
Model qwen3.8-27b on mimir accepts 32768 tokens. This read needs 42058.
Pick a model with a larger context, or raise --ctx-size for this preset.
```

## 7. Security model

### 7.1 Transport (D2, D6)

Rules, in order:

1. `https` + bearer token is the default for any host that is not loopback.
2. `http` to a non-loopback host requires `plaintext_ack` on the profile, and the host
   must resolve inside a private range: RFC1918, loopback, RFC6598 CGNAT
   (`100.64.0.0/10`, which covers Tailscale), or `fd00::/8`.
3. `http` to a public address is refused. There is no override.
4. TLS verifies against the OS trust store. If that fails and the profile carries a
   pinned sha256 fingerprint, the pin is checked instead.
5. No `verify=False` exists anywhere in the codebase. A guardrail test should assert this,
   the way `test_messagebox_guardrail.py` bans direct messagebox calls.

Resolve the hostname once at Test time, check the resolved address against the range
rules, and store the profile. Re-resolve at run start and re-check. A hostname that
resolves inside RFC1918 today can resolve to a public address tomorrow.

The existing request identity keeps `allow_redirects: False`, `trust_env: False`,
`proxies_ignored: True`. Remote does not relax those.

### 7.2 Credentials (D5)

Add an `LLM_SERVER` provider to `experimental/keymaster/store.py`. The store already does
AES-GCM with a PBKDF2-derived root key and supports multiple labeled keys per provider.

Friction to design around: Keymaster is passphrase-gated
(`store.py:414 unlock_session_keys`). A run started after an app restart needs an unlock.
Cache the session keys for the app lifetime, the way `keymaster_window.py` already does,
and fail a remote run with a clear "Keymaster is locked" message rather than a transport
error.

A profile with `keymaster_key_id = None` sends no `Authorization` header. That is the
correct configuration for a loopback Ollama.

### 7.3 Egress consent (D7)

Remote runs send extracted file text off the machine. That text is harvested from open
directories and is frequently sensitive.

- Confirm on every remote run by default. The dialog names the profile, the host, the
  model, and what is sent.
- "Mute this session" writes to `gui/utils/session_flags.py`. Add
  `ANALYST_REMOTE_EGRESS_MUTE_KEY`. It resets on app restart, like every other flag there.
- "Mute forever" sets `consent_muted` on the profile row. Adding a new profile brings the
  dialog back. That is deliberate.
- The run view shows a persistent `Remote: <profile name>` marker regardless of mute
  state. Muting the dialog must not hide the fact.
- A loopback profile never shows the dialog.

---

### 7.4 Model content hygiene (probe F5)

Reasoning models return a separate `reasoning_content` field alongside `content`. Measured
on `qwen3.8-27b`, that field contained a **verbatim echo of the input**, including the
sensitive banner text the probe sent.

It also consumes the `max_tokens` budget. A probe with `max_tokens: 8` returned empty
`content` and a truncated reasoning trace — a silent quality failure that looks like a
model problem rather than a configuration one.

Both are fixed by one request field, verified working:

```json
"chat_template_kwargs": {"enable_thinking": false}
```

Rules:

- Send it on every OpenAI-compatible request.
- Never log, persist, or place `reasoning_content` in `report.json`. Extend the existing
  leak-scan patterns to cover the field name.
- If a response arrives with empty `content` and a populated `reasoning_content`, treat it
  as a configuration failure and say so, rather than recording a `model_invalid` result.

Server presets also set their own sampling defaults — `mimir` ships `temperature 1.0`,
`top_p 0.95`. Analyst must send explicit deterministic values on every request and never
inherit server defaults.

### 7.5 Model filtering (probe F4)

A router catalogue is not a list of chat models. `mimir` serves 11 presets including a
pure embeddings model (`nomic-embed-text`, launched `--embeddings --pooling mean`) and a
vision model (`qwen3.6-35b`, launched with `--mmproj`).

Filter the dropdown to text-generation models before offering them. `/v1/models` carries
`architecture.input_modalities` and `output_modalities`, and the embeddings case is
visible in `status.args`. Offering an embeddings model produces a confusing runtime
failure the operator cannot diagnose.

---

## 8. Run pinning and resume (D9)

`analyst_runs` records `profile_id`, `backend_kind`, and the full `ModelIdentity` at
creation.

On resume, the worker compares the recorded triple against the live profile:

| Condition | Action |
| --- | --- |
| Exact match | Resume. |
| Profile unreachable | Hold the run resumable. Do not silently retarget. |
| Model name differs | Refuse. Offer "start a new run". |
| Digest differs where both sides have one | Refuse. Offer "start a new run". |
| Backend kind differs | Refuse. |

Rationale: mixing two models inside one report destroys the grounding claim that the
whole read-first contract rests on.

---

## 9. Shared servers (D11, D12)

Two Dirracuda installs can now point at one server. Analyst does not arbitrate that.

What Analyst does:

| Behaviour | Where | Blocking |
| --- | --- | --- |
| Gate on per-slot context | preflight step 6 | Yes — refuses the run |
| Warn when slots are busy | preflight step 7, repeated at run start | No — informational |
| Map capacity errors to "server at capacity" | adapter error taxonomy | Fails the request, not the run |
| Record contention in `report.json` | run metadata | No |

What Analyst does not do, and why:

- **Arbitration.** Neither Ollama nor llama.cpp exposes a lock primitive. A real lock
  needs a sidecar coordinator service, which is more moving parts than the problem
  justifies for a tool whose users administer their own hardware.
- **Cross-client coordination through the sidecar DB.** The clients are on different
  machines by definition. There is no shared filesystem to coordinate through.
- **Model thrashing control.** Two clients requesting different models make Ollama load
  and evict repeatedly. That is `OLLAMA_MAX_LOADED_MODELS` and server RAM — server
  configuration, not something a client can fix.

The busy warning is advisory and must never block. A user who owns the box and knows the
other client is their own batch job should not be stopped by a dialog.

`_GLOBAL_REQUEST_SLOT` in `ollama_client.py` stays as it is. It bounds one process to one
in-flight request, which is still correct. It was never a cross-machine mechanism and must
not be presented as one.

### 9.1 Documentation (D12)

`docs/ANALYST_GUIDE.md` gains one section, **"Serving Analyst from a shared box"**, placed
after *Dependency setup*. It covers:

- the `-c` ÷ `--parallel` arithmetic with a worked example;
- `OLLAMA_NUM_PARALLEL` and `OLLAMA_MAX_LOADED_MODELS` as the Ollama equivalents;
- a plain statement that Analyst does not arbitrate access and assumes the operator
  administers the hardware;
- how to read the busy warning and the "server at capacity" message.

No new document. The guide already carries hardening, troubleshooting, and privacy
sections, so this is where a reader will look. If the section later outgrows the page,
split it then.

---

## 10. Changes to frozen contract wording

`CONTRACT.md:218-224` carries a verbatim local-only claim:

> "Analyst connects only to a literal-loopback Ollama endpoint, disables redirects,
> ignores ambient proxies, rejects known cloud tag forms (`:cloud` and `-cloud`), and
> runs only a locally installed model whose tag and digest match the approved benchmark."

That is now false for a remote profile. `CONTRACT.md` is never edited — the established
pattern is `CONTRACT_ERRATA.md`. N0 adds an errata entry with replacement wording that:

- keeps redirects disabled, ambient proxies ignored, and cloud tags rejected;
- states the operator chooses the endpoint, and names the policy that constrains it;
- makes no claim Analyst cannot prove, consistent with the existing note that
  "server-level egress control is an operator prerequisite Analyst cannot prove".

`DESIGN_BRIEF.md` §11 already pre-scoped this work. Its rule "raw port 11434 must never
become the LAN/Tailscale interface" is preserved by §7.1 rules 1-3 above: an unauthenticated
plaintext Ollama on a LAN is reachable only after an explicit per-profile acknowledgement,
and never from a public address.

---

## 11. Risks

| ID | Risk | Mitigation |
| --- | --- | --- |
| RB-1 | llama.cpp honours `response_format: json_schema` loosely. | **Closed by probe P4.** Strict JSON, enum and `additionalProperties:false` honoured, no fence. No GBNF fallback needed. |
| RB-2 | Client disconnect does not stop llama.cpp inference. | **Closed by probe P5.** The slot freed within 4s. Analyst may report "cancelled" honestly on llama.cpp; the hedged Ollama wording is not needed here. |
| RB-3 | `analyst_tab.py` crosses 1700 lines. | Put profile management in a satellite module in N1, before the UI grows. |
| RB-4 | A DNS name resolves private at Test time and public at run time. | Re-resolve and re-check at run start (§7.1). |
| RB-5 | Keymaster lock state blocks an unattended run. | Explicit "Keymaster is locked" failure, not a transport error. Cache session keys per app lifetime. |
| RB-6 | Endpoint parameterisation leaks into frozen benchmark scripts and invalidates a seal. | Module-level loopback default; do not edit `scripts/analyst_benchmark/*`. Re-run the leak scan and provenance checks in N1. |
| RB-7 | Two Dirracuda instances hit one remote server and interleave. | Accepted and documented (§9, D11). Warn, fail clearly, record contention. No arbitration attempted. |
| RB-9 | The context gate reads the wrong context number. | **Reshaped by probe P2.** Division by slots is version-dependent and did not occur on `b1-f280b26`. The real failure is a router reporting `n_ctx: 0`. Detect router vs single (§6.1); the server's own `exceed_context_size_error` is the backstop. |
| RB-11 | A cold or sleeping model makes the first request take minutes; Analyst times out and records a false failure. | Measured: 11k prompt tokens took 33s to process on a warm 27B. A cold 120B load is far longer. Read timeouts must tolerate it, and the UI must distinguish "loading" from "stuck". |
| RB-12 | `reasoning_content` echoes sensitive input into logs or `report.json`. | Send `enable_thinking: false`; never persist the field; extend leak-scan patterns (§7.4). |
| RB-13 | An embeddings or vision model is offered in the dropdown and fails at runtime. | Filter to text-generation models (§7.5). |
| RB-10 | Ollama model thrashing when two clients want different models. | Out of scope for the client. Documented in the ANALYST_GUIDE section (§9.1). |
| RB-8 | Token leaks into logs or `report.json`. | The existing leak-scan tooling covers this. Extend its patterns to bearer tokens and add a test. |

---

## 12. What N0 must produce

1. `CONTRACT_REMOTE_BACKENDS.md` — the frozen contract, written against real probe output.
2. An errata entry for `CONTRACT.md:218-224`.
3. `N1_CARD.md`, `N2_CARD.md`, `N3_CARD.md`.
4. Risk register entries RB-1 through RB-13 appended to `RISK_REGISTER.md`. Frozen Phase 1
   entries untouched.
5. A pointer line in `phase_2/README.md`.

N0 changes no runtime code, no dependency file, and no CI config.
