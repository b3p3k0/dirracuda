# N2 — OpenAI-Compatible Adapter

- Branch: `feature/ollama-analyst`
- Type: **code.** Implemented by codex (DA) under Claude orchestration.
- Status: **reviewed 2026-09-22; revised.** Split into two gated stages, N2a and N2b.
- Contract: [`CONTRACT_REMOTE_BACKENDS.md`](CONTRACT_REMOTE_BACKENDS.md) §3, §4.1-4.3, §5, §6, §7, §8
- Errata: **E18** (model identity rendering is in scope)
- Decisions: **D18** (identity kind + nullable digest), **D19** (§4.1-4.3 moves here from N3)

## Goal

Talk to llama.cpp. Local and private-LAN, including router mode. TLS, credentials and
egress consent stay in N3.

## Senior review outcome (2026-09-22)

The card as originally written **could not pass its own acceptance**. Four findings:

| # | Finding | Resolution |
| --- | --- | --- |
| B1 | `analyst_runs.model_digest` is `NOT NULL CHECK(64 hex)` (`db_schema.py:136`) and `report_json.RunMeta` enforces the same. A `reported` identity (§6.1) has no digest, so a llama.cpp run could not be stored **or** reported. Neither file was named in the card. | **D18** — schema v8: nullable digest plus `identity_kind`. Erratum **E18** resolves the §2/§6.1 contradiction that made this invisible. |
| B2 | D13 justified N2 covering LAN by the §4.2 plaintext opt-out, but that opt-out was assigned to N3. N2 could not legally reach `mimir`. D17's guard is bound to `ollama_client.py:151`, so a new adapter would inherit **no** address guard at all. | **D19** — §4.1-4.3 moves into N2. |
| M1 | §7 said to set `IDLE_READ_TIMEOUT_SECONDS` / `TOTAL_REQUEST_SECONDS` from measurements. Both are module-level `Final`s in `ollama_contract.py:73-74` shared with the Ollama client; changing them breaks §12.1 (byte-identical loopback Ollama). | Per-backend timeouts. The shared constants keep their values. |
| M2 | `_GLOBAL_REQUEST_SLOT` is defined and acquired only inside `ollama_client.py:78`. A second client would not take it, so two requests could be in flight per process, contradicting §11. | The slot moves to a shared module; every backend acquires it. |

### Why two stages

With B1 and B2 folded in, one card would carry the schema, the report payload, the
address policy, and the whole adapter. N2a is pure and data-only with no network at all,
so the risky network work in N2b lands on an already-validated identity foundation — the
same shape that worked for N1, where `endpoint.py` landed before anything used it.

This is **not** a local-only stage: N2b still does local and private-LAN together, as D13
requires.

---

# N2a — Identity, schema and pure backend types

- Status: **IMPLEMENTED 2026-09-22.** Awaiting HI review.
- Contract: §6.1, §6.2, §6.3 (types only), §3 (types only). Erratum E18, decision D18.

No network. No adapter. Nothing in this stage opens a socket.

## Work

### 1. Schema v8

Thanks to card S1 this is three edits in `db_schema.py` plus a guardrail digest.

- `analyst_runs.model_digest` becomes **nullable**; its `CHECK` becomes
  `model_digest IS NULL OR (64 lowercase hex)`.
- `analyst_runs` gains `identity_kind TEXT CHECK(identity_kind IN ('digest','reported'))`
  and the reported-identity columns from §6.1 (`model_path`, `n_params`, `size`, `ftype`,
  `n_vocab`, `n_ctx`, `n_ctx_train`, `system_fingerprint`).
- Append one `_SchemaStep`; add the v8 digest to
  `shared/tests/test_analyst_schema_ladder_guardrail.py`.
- **`store.py` needs no edit.** If it does, S1 regressed.

Existing rows keep `identity_kind = NULL`, read as `digest` for compatibility.

### 2. `report_json`

- `RunMeta` accepts a `reported` identity: digest optional, kind required, reported fields
  carried.
- The existing 64-hex rule still applies **when the kind is `digest`**.
- `REPORT_SCHEMA_VERSION` bumps.

### 3. Rendering, per E18

A `reported` identity must never display as verified.

**Finding, 2026-09-22:** nothing displayed model identity anywhere before this card — the
report view showed risk, owner, contacts, counts and exposures, but never the model. So
E18's requirement needed a new line, not an edit to an existing one. It is driven by one
pure helper, `report_json.model_identity_label()`, so no surface can drift from another.

**The Web UI clause has no target.** `experimental/webui/` contains zero references to
Analyst across every module and template — it surfaces scan results and Sherlock, not
Analyst reports. Recorded rather than inventing a surface. N3 revisits the Web UI.

### 4. `backends/base.py`

Pure types only — `BackendKind`, `BackendCapabilities`, `ModelIdentity`, shared result and
error types. Mirror `shared/tests/test_sherlock_purity.py` with a guardrail blocking DB,
filesystem, network and Tk imports. `experimental/analyst/endpoint.py` is the precedent.

## N2a acceptance

1. Schema v8 migrates from v7; an existing run row still reads, with `identity_kind` NULL.
2. The ladder guardrail passes with the v8 digest added, and `store.py` is unedited.
3. A `reported` identity round-trips through `analyst_runs` and `report_json`.
4. A `digest` identity still enforces 64 lowercase hex.
5. No surface renders a `reported` identity as verified. A test asserts it.
6. The `base.py` purity guardrail passes.
7. `shared`/`experimental` and `gui` suites green. Xvfb screenshot of a report view
   showing a reported identity.

## N2a closeout

All seven met.

| Acceptance | Evidence |
| --- | --- |
| 1 | v1-v7 all migrate to v8; a populated v7 keeps every row across the FK web; a copy of the real sidecar (4 runs) migrated with labels intact |
| 2 | 45 ladder guardrail tests; **`store.py` unedited** — S1 paying for itself |
| 3 | `analyst_runs` stores a NULL digest with `identity_kind='reported'`; `RunMeta` round-trips it |
| 4 | digest runs still refuse a non-64-hex or NULL digest, at both the CHECK and `RunMeta` |
| 5 | `model_identity_label()` never emits "verified" for a reported run; asserted in `gui/tests/test_analyst_n2a_identity_display.py` |
| 6 | AST purity guardrail on `backends/base.py` |
| 7 | 3526 shared+experimental (1 pre-existing daemon tkinter failure), 2091 gui, screenshot taken |

Three things worth carrying into N2b:

1. **v8 is the first non-additive step.** `initialize_schema` now runs with
   `foreign_keys=OFF` when a rebuild is pending, and `VACUUM`s after, because the dropped
   table's pages otherwise broke a crash-recovery test asserting byte-exact rollback.
2. **`REPORT_SCHEMA_VERSION` is 2, and 1 is still read.** A naive bump would have
   orphaned every report already on disk.
3. **`BackendCapabilities.admits()` already encodes 5.3/5.4** — an unknown context admits
   the run. N2b supplies the number; it does not re-decide the rule.

---

# N2b — The OpenAI-compatible adapter

- Status: **reviewed 2026-09-22.** Requires N2a (done).
- Contract: §3, §4.1-4.3, §5, §7, §8, §11. Decisions D19, D20.

## Senior review outcome (2026-09-22)

| # | Finding | Resolution |
| --- | --- | --- |
| B1 | Contract §4 needs `exceed_context_size_error` and the empty-content/`reasoning_content` case to be distinguishable from `model_invalid` and from a transport failure. `ContactStatus` feeds `OLLAMA_CONTACT_STATES`, baked into three schema CHECKs (`db_schema.py:360`, `:501`, `:525`). Widening a CHECK needs a table rebuild. | **D20** — schema v9 adds both statuses and rebuilds the three contact tables. |
| B2 | `RunSpec.model_digest` is a required `str` (`store.py:76`) and `create_run` is the only way a run is made, so a reported-identity run still could not be **created**. N2a's acceptance 3 was validated by direct INSERT, which is how this survived. | `RunSpec` gains an optional digest, the identity kind and the reported fields. |
| M1 | `analyst_runs.profile_id` and `backend_kind` were added in v7 and nothing writes either. N3's run pinning (§6.4) would have nothing to pin against. | Run creation records both. |
| M2 | `worker_preflight._PARSER_FILES` feeds every run's `parser_bundle_sha256`. `backends/*.py` is absent, so a run's provenance would not cover the code that talked to the model. `endpoint.py` is absent too, and it decides which server is contacted. | Both added to the bundle. |

**Already in place, no work needed:** the chat path is injectable. `phase2.Dependencies.client`
and `read_reduce` both accept any object exposing `.chat(...)`, so `backends/ollama.py`
works as a thin wrapper with no restructuring.

## Work

### 1. Backend package

```
experimental/analyst/backends/__init__.py   registry + dispatch by BackendKind
experimental/analyst/backends/base.py       pure types, no I/O
experimental/analyst/backends/ollama.py     thin wrapper over the existing client
experimental/analyst/backends/openai_api.py the new adapter
```

`base.py` and its purity guardrail land in **N2a**. N2b adds the registry, the Ollama
wrapper and the adapter on top of them.

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

Timeouts become **per backend** (review finding M1). `IDLE_READ_TIMEOUT_SECONDS` and
`TOTAL_REQUEST_SECONDS` in `ollama_contract.py:73-74` are shared with the Ollama client;
changing them would break §12.1, which requires a loopback Ollama run to stay
byte-identical. Leave those values alone and give `OPENAI_COMPAT` its own, set against
measured values and **recorded in the card closeout**. Surface `status.value` of `unloaded` or
`sleeping` as "loading the model" so a healthy run is not cancelled (§8).

### 8. Cancellation

`stream: true` plus a cancel-checked read loop, as Ollama. Report "cancelled" without
qualification on this backend (§7.3) — measured slot release within 4 s.

### 9. Address policy (§4.1-4.3, decision D19)

Moved here from N3 so N2b can legally reach `mimir`, and so no unguarded remote path
exists at any point.

- Loopback: no TLS, no token. Unchanged default.
- Non-loopback: `plaintext_ack` on the profile **and** the host inside RFC1918, loopback,
  `100.64/10` or `fd00::/8`. Reuse `endpoint.py`'s `AddressClass` and `PRIVATE_CLASSES`,
  shipped in N1 — do not re-derive the ranges.
- Public plaintext: refused. **No override path may exist.**
- Re-resolve and re-check at run start, not only at Test time.
- `https` without a token is out of scope here; TLS verification and bearer tokens stay
  in N3. Until then a non-loopback `https` profile is refused.

D17's guard in `ollama_client.py` is replaced by this policy, not simply deleted. The
Ollama transport gains the same address rules rather than losing its guard.

### 10. One in-flight request per process (§11, review finding M2)

`_GLOBAL_REQUEST_SLOT` currently lives inside `ollama_client.py:78` and is acquired only
there. Move it to a module both backends import, and have every backend acquire it.
Otherwise a second client makes two concurrent in-flight requests possible, which §11
says cannot happen.

## Reference server

`mimir` at Tailscale `100.125.197.36:9292`, router mode, build `b1-f280b26`,
unauthenticated, reachable from the dev box. SSH as `claude@mimir` is HI-authorized.

Use `qwen3.8-27b` — it is the closest analogue to the C0B-7 benchmark model and was
already warm during probing.

## N2b acceptance

1. A run completes end-to-end against `mimir` and produces a valid `report.json`.
2. The same run against a loopback Ollama produces the same report shape.
3. Router `n_ctx: 0` produces neither a false approval nor a false refusal.
4. An oversized prompt produces the specific message with both token counts.
5. `reasoning_content` appears in no log, no DB row, and no `report.json`. A leak-scan
   test proves it.
6. An embeddings-only model is not offered in the dropdown.
7. A cancelled run reports "cancelled" and the server slot frees.
8. A public plaintext endpoint is refused and no code path permits it.
9. A private-range endpoint without `plaintext_ack` is refused; with it, permitted.
10. A hostname that re-resolves outside the permitted ranges at run start fails closed.
11. A loopback Ollama run is still byte-identical: the shared timeout constants are
    unchanged and the request-identity hash is untouched.
12. Two backends cannot hold the in-flight slot at once. A test asserts it.
13. Both test suites green; Xvfb screenshot of a completed remote run.
