# Probe Results — Step 0

- Date run: 2026-09-20
- Host: `mimir` (Fedora 44, kernel 7.1.9)
- Build: `b1-f280b26` (also returned as `system_fingerprint` on every completion)
- Run by: Claude over SSH, HI-authorized
- Contains no credentials. The server has none to leak.

**Verdict: the design was wrong in two places and under-specified in a third.** Details
below. Corrections are already applied to `ARCHITECTURE.md` and the `README.md` ledger.

---

## 0. mimir is not a single-model server

This was not anticipated anywhere in the plan.

```
llama-server --models-preset /opt/llm/models-preset.ini --models-max 2 \
             --sleep-idle-seconds 1800 --host 0.0.0.0 --port 9292
```

llama.cpp runs in **router mode**. One front-door server on `:9292` owns a catalogue of
11 preset models and spawns a child `llama-server` per model on a random loopback port,
on demand. At most 2 are resident (`--models-max 2`). Idle children sleep after 1800s.

Model states observed: `unloaded`, `sleeping`, and loaded.

This is the shape a capable shared box actually takes, so it is the shape Analyst must
handle — not the single-model server the public docs describe.

---

## P1 — Health

`GET /health` → `200 {"status":"ok"}`. No token needed.

## P2 — Properties and context (the decisive probe)

### The router lies about context

```json
GET http://mimir:9292/props
{"role":"router","max_instances":2,"models_autoload":true,
 "model_alias":"llama-server","model_path":"none",
 "default_generation_settings":{"params":null,"n_ctx":0},
 "build_info":"b1-f280b26"}
```

**`n_ctx` is `0`. `model_path` is `"none"`.**

The preflight design in `ARCHITECTURE.md` §6 said "read `n_ctx` from `/props`". Against a
router that returns `0`, which would either refuse every run or, if treated as "unknown",
approve every run. Both are wrong.

### A child reports honestly

```json
GET http://127.0.0.1:57887/props     # the qwen3.8-27b child
{"default_generation_settings":{"n_ctx":32768,...},
 "total_slots":4,
 "model_alias":"qwen3.8-27b",
 "model_ftype":"Q4_K - Medium",
 "model_path":"/opt/llm/models/Qwen3.8-27B-UD-Q4_K_XL.gguf",
 "is_sleeping":..., "build_info":"b1-f280b26"}
```

### `--ctx-size` is NOT divided by slots on this build

This contradicts the sources cited in the plan
([#11681](https://github.com/ggml-org/llama.cpp/issues/11681),
[#4130](https://github.com/ggml-org/llama.cpp/discussions/4130)). Those describe older
behaviour. Measured, not assumed:

| Test | Prompt tokens | Result |
| --- | --- | --- |
| Under the limit | 11,058 | `200`, `usage.prompt_tokens: 11058`, full prompt processed |
| Over the limit | 42,058 | `400 exceed_context_size_error`, `n_ctx: 32768` |

`--ctx-size 32768` with `total_slots: 4` gave a single request the **full 32768**, not
8192. So on build `b1-f280b26`, `/props` `n_ctx` is the usable per-request context.

**D10 as written is wrong and has been corrected.** The division behaviour is
version-dependent, which is worse than either answer — Analyst cannot hardcode either.

## P3 — Model list is far richer than OpenAI's shape

`GET /v1/models` returns per model:

| Field | Value seen |
| --- | --- |
| `id` | a clean alias (`qwen3.8-27b`), **not** a file path |
| `status.value` | `unloaded` / `sleeping` / loaded |
| `status.args` | the child's full argv, including its own `--ctx-size` |
| `status.preset` | the raw ini section |
| `architecture.input_modalities` | `["text"]` or `["text","image"]` |
| `meta` | present only once loaded: `n_ctx`, `n_ctx_train`, `n_params`, `size`, `ftype`, `n_vocab` |
| `source`, `can_remove` | `preset`, `false` |

Two consequences:

1. **Per-model context is discoverable without loading the model** — parse `--ctx-size`
   out of `status.args`. Once loaded, `meta.n_ctx` confirms it.
2. **Identity is much stronger than "name only."** `model_path` + `n_params` + `size` +
   `ftype` + `n_vocab` + `system_fingerprint` is a solid fingerprint. Still not a
   cryptographic digest, so D3's "reported, unverified" label stands — but the recorded
   evidence is richer than the plan assumed.

The catalogue also contains a **pure embeddings model** (`nomic-embed-text`, launched
`--embeddings --pooling mean`) and a **vision model** (`qwen3.6-35b` with `--mmproj`).
Analyst must filter the list to text-generation models or it will offer a model that
cannot chat.

## P4 — Structured output: PASS

`response_format: {"type":"json_schema", "strict": true}` worked exactly as needed:

- content was strict JSON, no markdown fence, no prose wrapper
- `additionalProperties: false` honoured
- the `severity` enum honoured (`"low"`)
- `finish_reason: "stop"`

**No GBNF fallback needed.** Risk RB-1 is closed.

Worth noting from the model's own reasoning trace: it never saw the schema. llama.cpp
enforces it with a grammar, not by prompting. Analyst should still describe the expected
shape in the prompt for semantic quality, as the worksheet prompts already do.

## P5 — Cancellation: PASS, better than Ollama

Streaming request killed client-side at 4s:

```
t+2s  slots processing: 1 / 4
t+4s  slots processing: 0 / 4
t+6s  slots processing: 0 / 4
```

llama.cpp detects the dropped connection and frees the slot. **Analyst can honestly
report "cancelled" on this backend** and does not need the hedged "server completion
unverified" wording Phase 1 adopted for Ollama. Risk RB-2 is closed for llama.cpp.

## P6 — Sampling

The child's `default_generation_settings.params` enumerates every sampler llama.cpp
supports, including `top_k`, `min_p`, `repeat_penalty`, `repeat_last_n`, `seed`,
`dry_*`, `xtc_*`, `mirostat`. All are addressable. No loss versus the Ollama native path.

Note the preset ships `temperature 1.0 / top-p 0.95` as server defaults. Analyst must send
its own deterministic values explicitly and never inherit server defaults.

## P7 — Authentication: none

No `--api-key` on the router. `/v1/models` returns `200` from a remote host with no token.

Not a misconfiguration in context — see P9.

## P8 — Context overflow: errors cleanly, does NOT truncate

```json
{"error":{"code":400,
  "message":"request (42058 tokens) exceeds the available context size (32768 tokens), try increasing it",
  "type":"exceed_context_size_error",
  "n_prompt_tokens":42058, "n_ctx":32768}}
```

Machine-readable, with both numbers. **This is the single best result in the probe.** The
silent-truncation failure D4 was built to prevent does not occur on this build. The
preflight gate becomes fail-fast UX rather than the only line of defence, and the error
type maps straight to a precise message.

The error is proxied unchanged through the router.

## P9 — Network exposure

| Path | Result |
| --- | --- |
| Tailscale `100.125.197.36:9292` from the dev box | `200`, 1.7 ms |
| LAN `192.168.1.242:9292` | connection refused |

firewalld: `tailscale0` is in the **trusted** zone; `enp191s0` is in `FedoraServer` with
`ports: []` and only `ssh`/`cockpit` services. So `--host 0.0.0.0` is bound broadly but
the firewall admits only Tailscale.

Both endpoints sit in `100.64.0.0/10`, so this exercises the real D2 private-range check
rather than a loopback shortcut. The unauthenticated-plus-Tailscale posture is exactly
the case D2's opt-out was written for.

## P10 — Build

`b1-f280b26`, returned both at `/props.build_info` and as `system_fingerprint` on every
completion. Free per-response build provenance.

## P11 — Concurrency: not a problem here

4 slots. A short request issued while a 1200-token generation was in flight returned
`200` in **0.5 s**.

`/slots` exists, needs no token, and reports `is_processing` per slot.

## P12 — Reasoning models (not in the original protocol)

`qwen3.8-27b` emits a separate `reasoning_content` field by default. Two problems:

1. It consumes the `max_tokens` budget. An early probe with `max_tokens: 8` returned
   **empty content** and a truncated reasoning trace — a silent quality failure.
2. It contains a **verbatim echo of the input**, including whatever sensitive text
   Analyst sent.

Both are fixed by one request field:

```json
"chat_template_kwargs": {"enable_thinking": false}
```

Verified: `content: "OK."`, `reasoning_content: None`, `finish_reason: "stop"`.

Analyst must send this and must never log or persist `reasoning_content`.

---

## Verdict per design risk

| Risk | Before | After |
| --- | --- | --- |
| RB-1 json_schema unreliable | open | **closed** — works, no GBNF needed |
| RB-2 cancellation unverifiable | open | **closed for llama.cpp** — slot frees in ~4s |
| RB-9 silent truncation | highest severity | **downgraded** — server errors cleanly; but `/props` on a router reports `n_ctx: 0`, which is a new failure mode |
| RB-7 shared-server contention | accepted | still accepted; 4 slots make it a non-issue until saturation |

## New findings requiring design changes

| # | Finding | Impact |
| --- | --- | --- |
| F1 | Router mode: `/props` returns `role: "router"`, `n_ctx: 0`, `model_path: "none"` | Preflight must detect router vs single and read context per model from `/v1/models` |
| F2 | `--ctx-size` ÷ `--parallel` is version-dependent | Never hardcode either rule. Trust `/props` when single, `status.args` when routed, and always honour `exceed_context_size_error` |
| F3 | Models can be `unloaded` or `sleeping`; first call triggers a load | Read timeouts must tolerate a cold 120B load. 11k prompt tokens alone took 33s to process |
| F4 | Catalogue contains embeddings-only and vision models | Filter to text-generation before offering a model |
| F5 | `reasoning_content` leaks input and eats the token budget | Send `enable_thinking: false`; never persist the field |
| F6 | Server presets set `temperature 1.0`, `top_p 0.95` | Always send explicit deterministic values; never inherit |
| F7 | `system_fingerprint` gives free per-response build identity | Record it alongside model identity |
