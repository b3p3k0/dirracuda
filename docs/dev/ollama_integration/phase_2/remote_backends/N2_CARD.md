# N2 — OpenAI-Compatible Adapter

- Branch: `feature/ollama-analyst`
- Type: **code.** Implemented by codex (DA) under Claude orchestration.
- Status: **HELD.** Requires N1 merged.
- Contract: [`CONTRACT_REMOTE_BACKENDS.md`](CONTRACT_REMOTE_BACKENDS.md) §3, §5, §6, §7, §8

## Goal

Talk to llama.cpp. Local and private-LAN, including router mode. Remote security
hardening is N3.

## Work

### 1. Backend package

```
experimental/analyst/backends/__init__.py   registry + dispatch by BackendKind
experimental/analyst/backends/base.py       pure types, no I/O
experimental/analyst/backends/ollama.py     thin wrapper over the existing client
experimental/analyst/backends/openai_api.py the new adapter
```

`base.py` carries `BackendKind`, `BackendCapabilities`, `ModelIdentity`, and the shared
result and error types. **Mirror `shared/tests/test_sherlock_purity.py`** — a guardrail
test that blocks DB, filesystem, network, and Tk imports from `base.py`.

### 2. Detection

Per contract §3: `/props` → `OPENAI_COMPAT`; `/api/version` → `OLLAMA`; neither →
unreachable; both → manual override, else prefer `OLLAMA`.

### 3. Context discovery — the part most likely to be got wrong

Implement contract §5.3 exactly:

```
if /props has role == "router":
    context = /v1/models[id].meta.n_ctx
           or --ctx-size parsed from /v1/models[id].status.args
else:
    context = /props.default_generation_settings.n_ctx
```

`0`, absent, or non-integer is **unknown**, never unlimited. An unknown context skips the
preflight refusal and says so in the UI.

Do **not** divide by `total_slots`. That behaviour is version-dependent and did not occur
on the probed build. See `PROBE_RESULTS.md` §P2.

### 4. Error taxonomy

Map llama.cpp errors onto the shared result types:

| Server response | Analyst result |
| --- | --- |
| `400 exceed_context_size_error` | specific message with `n_prompt_tokens` and `n_ctx`; never generic transport failure, never `model_invalid` |
| `401` / `403` | "server requires a token" |
| no free slot / capacity | "server at capacity"; back off without consuming a semantic attempt, per the C9A resource policy |
| empty `content` + populated `reasoning_content` | **configuration failure**, not `model_invalid` |

### 5. Request construction

- `response_format: {"type":"json_schema","strict":true}`. No GBNF fallback (§7.5).
- `chat_template_kwargs: {"enable_thinking": false}` on **every** request (§7.4).
- Explicit sampling values on every request; inherit nothing (§7.1, §7.2).
- `max_tokens` carries `num_predict`.

### 6. Model list

Filter to text-generation models (§6.3). Exclude `--embeddings` servers and `--mmproj`
vision models. Capture the full `reported` identity (§6.1) including `system_fingerprint`
from the completion response.

### 7. Timeouts and loading state

Measured: 11,058 prompt tokens took 33 s on a warm 27B. A cold 120B load is longer.

Set `IDLE_READ_TIMEOUT_SECONDS` and `TOTAL_REQUEST_SECONDS` against measured values and
**record the measurement in the card closeout**. Surface `status.value` of `unloaded` or
`sleeping` as "loading the model" so a healthy run is not cancelled (§8).

### 8. Cancellation

`stream: true` plus a cancel-checked read loop, as Ollama. Report "cancelled" without
qualification on this backend (§7.3) — measured slot release within 4 s.

## Reference server

`mimir` at Tailscale `100.125.197.36:9292`, router mode, build `b1-f280b26`,
unauthenticated, reachable from the dev box. SSH as `claude@mimir` is HI-authorized.

Use `qwen3.8-27b` — it is the closest analogue to the C0B-7 benchmark model and was
already warm during probing.

## Acceptance

1. A run completes end-to-end against `mimir` and produces a valid `report.json`.
2. The same run against a loopback Ollama produces the same report shape.
3. Router `n_ctx: 0` produces neither a false approval nor a false refusal.
4. An oversized prompt produces the specific message with both token counts.
5. `reasoning_content` appears in no log, no DB row, and no `report.json`. A leak-scan
   test proves it.
6. An embeddings-only model is not offered in the dropdown.
7. A cancelled run reports "cancelled" and the server slot frees.
8. The purity guardrail on `base.py` passes.
9. Both test suites green; Xvfb screenshot of a completed remote run.
