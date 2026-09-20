# Analyst — Remote Backends Contract

- Date: 2026-09-20
- Branch: `feature/ollama-analyst`
- Status: **frozen at N0.** Changes require an erratum, not an edit.
- Evidence base: [`PROBE_RESULTS.md`](PROBE_RESULTS.md), measured against `mimir`,
  llama.cpp build `b1-f280b26`, 2026-09-20.

Every number in this contract traces to a measurement in `PROBE_RESULTS.md` or to an
existing frozen document. Nothing here is taken from vendor documentation alone.

---

## 1. What this supersedes

### From `CONTRACT.md` (frozen, never edited)

| Section | Status |
| --- | --- |
| §8 "Exact loopback URL validation (no DNS-derived hosts)" | **Superseded.** See §4. Recorded as erratum E17. |
| §8 local-only wording, lines 218-224 | **Superseded.** Replacement wording in §4.6, recorded as E17. |
| §8 redirects disabled, ambient proxies ignored, cloud tags rejected | **Kept, unchanged.** |
| §8 bounded response bytes, parsed-object size, deadlines | **Kept, unchanged.** |
| §8 cancellation wording "cancel requested; server completion unverified" | **Narrowed.** Still required for Ollama. Not required for llama.cpp — see §7.3. |
| §18 "publish 11434 on host loopback only" | **Superseded** for a user who opts into a remote profile. The rule survives as a default, not a constraint. |

### From `CONTRACT_READ_FIRST.md`

| Section | Status |
| --- | --- |
| §7.2 model pin relaxed to a per-run recorded selection | **Kept and extended.** See §6. |
| Everything else | **Kept, unchanged.** This contract adds transport and backend selection. It does not touch prompts, chunking, the risk rubric, grounding, or `report.json` content. |

### Existing errata that already cover ground here

**E1** (GPT-OSS thinking) already rules that streamed reasoning output is sensitive model
content, bounded in bytes, excluded from operational logs and every committed artifact.
§7.4 applies that same rule to llama.cpp's `reasoning_content` field. This contract adds
no new principle there; it names a new field the existing rule governs.

---

## 2. Scope

**In scope.** Choosing a model server: which host, which backend, which model, over what
transport, with what credentials.

**Out of scope.** Prompts, chunking, detectors, the risk rubric, grounding rules,
`report.json` content, and the sandbox. A remote run and a local run must produce the same
report for the same input and model.

**Explicitly excluded.** Cloud model providers. The `:cloud` / `-cloud` rejection in
`ollama_contract.is_cloud_model_tag` stays in force for the Ollama backend, and the
OpenAI-compatible backend must not be used to reach a hosted API. Enforcement is by
address policy (§4.2), not by tag matching.

---

## 3. Backends

Analyst supports exactly two transports.

| Kind | Endpoint | Used for |
| --- | --- | --- |
| `OLLAMA` | `/api/chat`, `/api/tags`, `/api/version`, `/api/ps` | Ollama, local or remote |
| `OPENAI_COMPAT` | `/v1/chat/completions`, `/v1/models`, `/props`, `/health`, `/slots` | llama.cpp, and any server that speaks the same dialect |

**The Ollama path is not rewritten.** It keeps `/api/chat` with per-request `num_ctx`,
`top_k`, `min_p`, `repeat_penalty`, `keep_alive`, and tag+digest identity, exactly as
benchmarked through C0B-7. Ollama's own `/v1` shim is never used: it silently ignores
`num_ctx` and returns no digest.

Backend kind is detected at Test time and stored on the profile. Detection order:

1. `GET /props` answers with a JSON object → `OPENAI_COMPAT`.
2. `GET /api/version` answers with a JSON object → `OLLAMA`.
3. Neither → unreachable. Refuse.
4. Both → honour the profile's manual override; if none is set, prefer `OLLAMA`.

The operator may override the detected kind. An override is recorded
(`ServerProfile.backend_detected = False`) and shown in the profile editor.

---

## 4. Transport policy

### 4.1 Defaults

A profile whose host is loopback needs no TLS and no token. That is the shipped default
and the behaviour of every existing install.

A profile whose host is not loopback requires **`https` and a bearer token**, unless the
operator sets the plaintext acknowledgement in §4.2.

### 4.2 The plaintext acknowledgement

`http` to a non-loopback host is permitted only when **all** of the following hold:

1. `ServerProfile.plaintext_ack` is `True`, set by an explicit operator action in the
   profile editor, never a default and never inferred.
2. The host resolves to an address inside one of: RFC1918 (`10/8`, `172.16/12`,
   `192.168/16`), loopback, RFC6598 CGNAT (`100.64/10`, which covers Tailscale), or
   `fd00::/8`.

`http` to any address outside those ranges is refused. **There is no override.** This is
the rule that preserves the Phase 1 constraint that raw port 11434 must never become a
public interface.

### 4.3 Re-resolution

The address check runs at Test time **and again at run start**. A hostname that resolves
inside a private range today can resolve to a public address tomorrow. A run whose host
re-resolves outside the permitted ranges fails closed before the first request.

### 4.4 TLS verification

1. Verify against the operating system trust store.
2. If that fails and `ServerProfile.cert_fingerprint` is set, compare the presented
   certificate's SHA-256 fingerprint against it with `hmac.compare_digest`.
3. Otherwise refuse, and offer to pin the presented fingerprint.

**`verify=False` must not appear anywhere in the codebase.** A guardrail test enforces
this, in the manner of `test_messagebox_guardrail.py`.

### 4.5 Inherited request identity

Unchanged from `CONTRACT.md` §8 and not relaxed by remote access:

- `allow_redirects: False`
- `trust_env: False`
- ambient proxies ignored
- bounded response bytes, bounded parsed-object size, per-read / per-request / total-run
  deadlines

The request identity hash keeps the endpoint as an input. The endpoint is now a supplied
value rather than a module constant, so the hash becomes per-endpoint. That is correct:
the endpoint is part of the request shape.

### 4.6 Replacement local-only wording (normative)

`CONTRACT.md` lines 218-224 are superseded by:

> Analyst connects only to an endpoint the operator configured. It disables redirects,
> ignores ambient proxies, and rejects known cloud tag forms (`:cloud` and `-cloud`) on
> the Ollama backend. A non-loopback endpoint requires TLS and a bearer token, or an
> explicit per-profile acknowledgement that is accepted only for private address ranges;
> a public plaintext endpoint is refused with no override. Analyst records the endpoint,
> backend, and model identity with every run. Analyst cannot prove where a server sends
> data after receiving it. Server-level egress control remains an operator prerequisite.

No claim of "local only" or "zero cloud egress" appears anywhere.

---

## 5. Context discovery and the preflight gate

### 5.1 What was measured

On build `b1-f280b26`, a server launched `--ctx-size 32768` with `total_slots: 4`
processed an 11,058-token prompt in full and rejected a 42,058-token prompt. `--ctx-size`
was **not** divided by slot count.

That behaviour is version-dependent. **Analyst must not hardcode either rule.**

### 5.2 Router mode

llama.cpp may run as a router: one front door owning a model catalogue, spawning a child
server per model on demand. A router's `/props` returns `role: "router"`,
`model_path: "none"`, and `default_generation_settings.n_ctx: 0`.

Reading `n_ctx` from a router and comparing it to a requirement is a defect. It either
refuses every run or approves every run.

### 5.3 The rule (normative)

```
if /props contains role == "router":
    context = /v1/models[id].meta.n_ctx                      # model already loaded
           or --ctx-size parsed from /v1/models[id].status.args
else:
    context = /props.default_generation_settings.n_ctx
```

A context value of `0`, absent, or non-integer is **unknown**, never "unlimited".
An unknown context skips the preflight refusal and relies on §5.4 alone, and the UI says
the limit could not be determined.

### 5.4 The server is the guarantee

llama.cpp refuses an oversized prompt with a machine-readable error:

```json
{"error":{"code":400,"type":"exceed_context_size_error",
  "n_prompt_tokens":42058,"n_ctx":32768}}
```

It does not silently truncate. The error is proxied unchanged through a router.

Analyst must map `exceed_context_size_error` to a specific operator-facing message
carrying both numbers, and must never treat it as a generic transport failure or a
`model_invalid` result.

### 5.5 The preflight gate

The gate remains **required**, but its role is now fail-fast, not correctness:

- It refuses before a long run rather than after the first chunk.
- It names the model's limit and the run's requirement.
- Its absence would not permit silent truncation, because §5.4 prevents that.

Required context comes from the run kind: `NUM_CTX` for chunk generation, `READ_NUM_CTX`
for the host read (`ollama_contract.py:40-46`).

---

## 6. Model identity

### 6.1 Two kinds, always labelled

| Backend | `ModelIdentity.kind` | Recorded |
| --- | --- | --- |
| `OLLAMA` | `digest` | tag + SHA-256 digest, as today |
| `OPENAI_COMPAT` | `reported` | model id, `model_path`, `n_params`, `size`, `ftype`, `n_vocab`, `n_ctx`, `n_ctx_train`, and the response `system_fingerprint` |

`report.json` and the report view must render which kind applies. A `reported` identity is
never presented as verified.

### 6.2 Reported identity is not thin

The probe showed a llama.cpp router exposes a rich per-model record. The composite above
is strong enough to detect a swapped model in practice. It is still not a cryptographic
digest, so the `reported` label stands — but the recorded evidence is substantial and must
be captured in full, not reduced to a name.

`system_fingerprint` (the server build, e.g. `b1-f280b26`) arrives on every completion
response and is recorded per run.

### 6.3 Model filtering

The offered model list contains only text-generation models. A router catalogue may also
carry embeddings-only models (launched `--embeddings`) and vision models (launched
`--mmproj`). Filter on `architecture.input_modalities` / `output_modalities` and on
`status.args`. Offering a model that cannot chat produces an undiagnosable runtime
failure.

### 6.4 Run pinning

`analyst_runs` records `profile_id`, `backend_kind`, and the full `ModelIdentity` at
creation. On resume:

| Condition | Action |
| --- | --- |
| Exact match | Resume |
| Profile unreachable | Hold the run resumable; do not retarget |
| Model id differs | Refuse; offer "start a new run" |
| Digest differs where both sides have one | Refuse; offer "start a new run" |
| Backend kind differs | Refuse |

One report comes from one model. Mixing two destroys the grounding claim the read-first
contract rests on.

---

## 7. Generation parameters

### 7.1 Never inherit server defaults

`mimir`'s presets ship `temperature 1.0`, `top_p 0.95`. Analyst sends explicit values on
every request and inherits nothing.

### 7.2 Parameter mapping

| Frozen profile | `OLLAMA` | `OPENAI_COMPAT` |
| --- | --- | --- |
| `temperature 0.0` | `options.temperature` | `temperature` |
| `top_p 1.0` | `options.top_p` | `top_p` |
| `top_k 1` | `options.top_k` | `top_k` |
| `min_p 0.0` | `options.min_p` | `min_p` |
| `repeat_penalty 1.0` | `options.repeat_penalty` | `repeat_penalty` |
| `repeat_last_n 0` | `options.repeat_last_n` | `repeat_last_n` |
| `seed 1` | `options.seed` | `seed` |
| `num_ctx` | `options.num_ctx` | not sendable; see §5 |
| `num_predict` | `options.num_predict` | `max_tokens` |
| `keep_alive` | `keep_alive` | not applicable |

The probe confirmed every sampler in the right-hand column is addressable on llama.cpp.

### 7.3 Cancellation

Measured: killing the client freed the llama.cpp slot within 4 seconds
(`1/4 → 0/4 processing`).

- `OPENAI_COMPAT`: Analyst reports **"cancelled"** without qualification.
- `OLLAMA`: the `CONTRACT.md` §8 wording stands — "cancel requested; server completion
  unverified" — because `/api/ps` is skewed by `keep_alive` and cannot prove a stop.

Both keep `stream: true` plus a cancel-checked read loop and socket deadlines. A
`threading.Event` still cannot interrupt a blocking read.

### 7.4 Reasoning traces

llama.cpp returns reasoning in a separate `reasoning_content` field. Measured on
`qwen3.8-27b`, that field contained a verbatim echo of the input and consumed the
`max_tokens` budget — a request with `max_tokens: 8` returned **empty** `content`.

Normative:

1. Send `"chat_template_kwargs": {"enable_thinking": false}` on every `OPENAI_COMPAT`
   request. Verified to produce `reasoning_content: null` and clean `content`.
2. `reasoning_content` is sensitive model output under **E1**. It is never logged,
   never persisted, and never placed in `report.json`. Leak-scan patterns extend to the
   field name.
3. A response with empty `content` and populated `reasoning_content` is a **configuration
   failure**, reported as such. It is not recorded as `model_invalid`, which would blame
   the model for an Analyst bug.

### 7.5 Structured output

Measured working with `response_format: {"type": "json_schema", "strict": true}`:
strict JSON, no markdown fence, `additionalProperties: false` honoured, enum honoured,
`finish_reason: "stop"`.

**No GBNF grammar fallback is contracted.** llama.cpp enforces the schema with a grammar
internally; the model never sees it. Analyst still describes the expected shape in the
prompt, as the worksheet prompts already do, for semantic quality.

---

## 8. Latency and model loading

A router may hold a model `unloaded` or `sleeping`. The first request triggers a load.
`--models-max` bounds how many stay resident; `--sleep-idle-seconds` bounds how long.

Measured: 11,058 prompt tokens took **33 seconds** to process on an already-warm 27B
model. A cold 120B load is substantially longer.

Consequences:

1. Connect timeout stays short. `CONNECT_TIMEOUT_SECONDS = 10.0` is adequate — the TCP
   connect is fast even when the model is cold.
2. `IDLE_READ_TIMEOUT_SECONDS` and `TOTAL_REQUEST_SECONDS` must accommodate a cold load
   plus prompt processing. N2 sets these against measured values and records the
   measurement.
3. The UI distinguishes **loading** from **stuck**. A model in `status.value` of
   `unloaded` or `sleeping` at run start is surfaced as "loading the model" so the
   operator does not cancel a healthy run.

---

## 9. Credentials

The bearer token lives in Keymaster under a new `LLM_SERVER` provider, reusing the
existing AES-GCM encryption and PBKDF2-derived root key
(`experimental/keymaster/store.py`).

- A profile with `keymaster_key_id = None` sends no `Authorization` header. This is the
  correct configuration for loopback Ollama and for an unauthenticated private-range
  server reached under §4.2.
- Keymaster is passphrase-gated. Session keys are cached for the application lifetime.
- A remote run started while Keymaster is locked fails with an explicit **"Keymaster is
  locked"** message, never a transport error.
- The token is never logged, never written to `report.json`, and never included in an
  error message. Leak-scan patterns extend to bearer tokens.

---

## 10. Egress consent

A remote run sends extracted file text off the machine. That text is harvested from open
directories and is frequently sensitive.

1. Confirm on **every** remote run by default. The dialog names the profile, host, model,
   and what is sent.
2. "Mute this session" writes `ANALYST_REMOTE_EGRESS_MUTE_KEY` to
   `gui/utils/session_flags.py`. It resets on application restart, as every flag there
   does.
3. "Mute forever" sets `consent_muted` on the profile row. A newly added profile brings
   the dialog back. This is deliberate.
4. The run view shows a persistent `Remote: <profile name>` marker regardless of mute
   state. Muting the dialog must never hide the fact.
5. A loopback profile never shows the dialog.

---

## 11. Shared servers

Analyst does **not** arbitrate access to a server it does not own.

What it does:

| Behaviour | Blocking |
| --- | --- |
| Gate on model context at preflight | Yes |
| Warn when slots are busy (`/slots`, `/api/ps`) | No — advisory only |
| Map capacity errors to "server at capacity" | Fails the request, not the run |
| Record contention in run metadata | No |

What it does not do, and why: neither backend exposes a lock primitive; a real lock needs
a sidecar coordinator service; the clients are on different machines so there is no shared
filesystem to coordinate through; and Ollama model thrashing is `OLLAMA_MAX_LOADED_MODELS`
and server RAM, which a client cannot fix.

Measured: with `total_slots: 4`, a short request issued during a 1200-token generation
returned in 0.5 s. Contention is not a practical problem until slots saturate.

`_GLOBAL_REQUEST_SLOT` in `ollama_client.py` is unchanged. It bounds one process to one
in-flight request. It was never a cross-machine mechanism and must not be described as
one.

`docs/ANALYST_GUIDE.md` gains a section, "Serving Analyst from a shared box", covering
the context arithmetic, the Ollama equivalents, and a plain statement that Analyst assumes
the operator administers the hardware.

---

## 12. Acceptance

A card implementing this contract is accepted only if:

1. A loopback Ollama run produces byte-identical behaviour to the pre-change build.
2. `verify=False` appears nowhere; the guardrail test proves it.
3. A public plaintext endpoint is refused, with no code path that permits it.
4. A router's `n_ctx: 0` does not produce a false approval or a false refusal.
5. `exceed_context_size_error` produces a specific message carrying both token counts.
6. `reasoning_content` appears in no log, no database row, and no `report.json`.
7. A bearer token appears in no log, no error message, and no artifact.
8. A resume against a different model is refused.
9. No `scripts/analyst_benchmark/*` file is edited, and the frozen path seals resolve.
10. `CONTRACT.md`, `CONTRACT_READ_FIRST.md`, and all frozen benchmark evidence remain
    unedited.
