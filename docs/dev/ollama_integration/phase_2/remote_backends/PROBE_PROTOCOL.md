# Probe Protocol — Step 0

- Date: 2026-09-20
- Runs against: a real llama.cpp `llama-server` host
- Type: **read-only.** No repo change. No writes to the server.
- Who runs it: the HI
- Output: paste the results back, or save the transcript and point at it

> **Executed 2026-09-20 against `mimir` (build `b1-f280b26`). Results:
> [`PROBE_RESULTS.md`](PROBE_RESULTS.md).** Kept as the re-run procedure for a different
> host or a newer build. Several answers here are build-specific — notably the context
> division question in P2 — so re-run rather than assume.

## Why

`ARCHITECTURE.md` §3 lists what llama.cpp does, taken from public docs. Ten probes below
verify it on a real host. N0 freezes the contract using these answers, not the docs.

Do not skip P2, P4, P5, or P8. Those four carry the design risk.

## Redact before you paste

Remove the API key from anything you send back. Everything else is safe to share.

---

## Setup

Set these once. Change the port if your server uses another one.

```bash
LHOST=mimir
LPORT=8080
LBASE="http://$LHOST:$LPORT"
LKEY=""          # your --api-key value. Leave empty if the server has none.

# One helper for every probe. It adds the token only when LKEY is set.
lc() {
  curl -sS -w '\n[http %{http_code}] %{time_total}s\n' \
    ${LKEY:+-H "Authorization: Bearer $LKEY"} "$@"
}
```

Save everything:

```bash
exec > >(tee ~/llamacpp_probe_$(date +%Y%m%d).txt) 2>&1
```

---

## P1 — Health and reachability

```bash
curl -sS -w '\n[http %{http_code}]\n' "$LBASE/health"
```

**Answers:** is the server up, and does `/health` need a key.

---

## P2 — Server properties and the context question (highest risk)

```bash
lc "$LBASE/props" | head -c 4000
```

Then, so we can compare the reported number against the launch flags:

```bash
# on mimir — how was the server actually started?
ps -o args= -C llama-server | tr ' ' '\n' | grep -A1 -E '^(-c|--ctx-size|-np|--parallel)$'
```

**Answers:** the exact JSON shape, and **where `n_ctx` lives in it**.

**The question that matters most in this whole protocol:** `llama-server` divides
`--ctx-size` by `--parallel`. A server launched with `-c 16384 --parallel 2` gives each
request 8192 tokens.

So: does `/props` report `n_ctx` as the **launch total**, or as the **per-slot** value?

Compare the `n_ctx` in the JSON against the `-c` value from `ps`:

- equal → `/props` reports the **total**, and Analyst must divide by `total_slots` itself
- `-c` ÷ `--parallel` → `/props` already reports **per slot**, and Analyst uses it directly

If you cannot read the process args, restart the server once with a known
`-c 4096 --parallel 2` and see whether `/props` says 4096 or 2048.

Getting this wrong reintroduces silent truncation (risk RB-9). Do not guess it.

Also capture: `total_slots`, the model path, and build info.

---

## P3 — Model list

```bash
lc "$LBASE/v1/models"
```

**Answers:** what the `id` field contains — a file path, or an alias. Whether any field
carries a hash. Whether more than one model is served.

---

## P4 — Structured output (design risk)

```bash
lc -H "Content-Type: application/json" \
  -X POST "$LBASE/v1/chat/completions" -d '{
  "messages": [
    {"role":"system","content":"You output only JSON matching the schema."},
    {"role":"user","content":"Host banner: \"FreeNAS-11 backup share, admin@northwind.example\". Summarise it."}
  ],
  "temperature": 0,
  "max_tokens": 300,
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "host_read",
      "strict": true,
      "schema": {
        "type": "object",
        "additionalProperties": false,
        "required": ["summary", "findings"],
        "properties": {
          "summary": {"type": "string"},
          "findings": {
            "type": "array",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "required": ["label", "quote", "severity"],
              "properties": {
                "label": {"type": "string"},
                "quote": {"type": "string"},
                "severity": {"type": "string", "enum": ["low","med","high"]}
              }
            }
          }
        }
      }
    }
  }
}'
```

**Answers:**

- Is `response_format.json_schema` accepted, or rejected as unknown?
- Does the content parse as strict JSON with no prose wrapper and no markdown fence?
- Is `additionalProperties: false` honoured?
- Is the `enum` honoured?

If this fails, the fallback is GBNF grammar and N2 gets larger.

---

## P5 — Cancellation (design risk)

Start a long stream, then kill it after about three seconds.

```bash
timeout 3 curl -N -sS ${LKEY:+-H "Authorization: Bearer $LKEY"} -H "Content-Type: application/json" \
  -X POST "$LBASE/v1/chat/completions" -d '{
  "messages":[{"role":"user","content":"Write a 3000 word essay about filesystem permissions."}],
  "max_tokens": 3000, "stream": true, "temperature": 0
}' | tail -c 400
echo "--- client stopped at $(date +%T) ---"
```

Then, immediately:

```bash
for i in 1 2 3 4 5 6; do
  curl -sS -o /dev/null -w "t+${i}0s slots: %{http_code} %{time_total}s\n" "$LBASE/health"
  sleep 10
done
```

Also watch the server side while this runs:

```bash
# on mimir, in another shell
nvidia-smi -l 2   # or: watch -n2 'ps -o pid,pcpu,comm -C llama-server'
```

**Answers:** does killing the client stop generation, or does the GPU stay busy for the
full 3000 tokens? This decides whether Analyst can honestly report "cancelled" or must
say "cancel requested; server completion unverified" (RB-2).

---

## P6 — Sampling parameters and determinism

Run this **twice** and diff the two outputs.

```bash
for run in 1 2; do
curl -sS ${LKEY:+-H "Authorization: Bearer $LKEY"} -H "Content-Type: application/json" \
  -X POST "$LBASE/v1/chat/completions" -d '{
  "messages":[{"role":"user","content":"List three risks of an open SMB share. Be terse."}],
  "temperature": 0, "top_p": 1.0, "top_k": 1, "min_p": 0.0,
  "repeat_penalty": 1.0, "seed": 1, "max_tokens": 200
}' > /tmp/probe_p6_$run.json
done
diff /tmp/probe_p6_1.json /tmp/probe_p6_2.json && echo "IDENTICAL" || echo "DIFFERENT"
cat /tmp/probe_p6_1.json
```

**Answers:** are `top_k`, `min_p`, `repeat_penalty`, `seed` accepted or rejected as
unknown fields? Is the same request reproducible byte-for-byte? The frozen Analyst profile
is deterministic; if llama.cpp is not, N0 must say so in the contract.

---

## P7 — Authentication behaviour

Only if the server was started with `--api-key`.

```bash
echo "--- no token ---"
curl -sS -w '\n[http %{http_code}]\n' -X POST "$LBASE/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"hi"}],"max_tokens":5}'

echo "--- wrong token ---"
curl -sS -w '\n[http %{http_code}]\n' -H "Authorization: Bearer wrong-key-for-probe" -X POST "$LBASE/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"hi"}],"max_tokens":5}'

echo "--- open endpoints without a token ---"
curl -sS -w '\n[http %{http_code}]\n' "$LBASE/health"
curl -sS -w '\n[http %{http_code}]\n' "$LBASE/v1/models"
curl -sS -w '\n[http %{http_code}]\n' "$LBASE/props"
```

**Answers:** the exact status code and body for a missing and a wrong token. Whether
`/props` is open like `/health` and `/v1/models`. N2 maps these to a clear "server requires
a token" message instead of a generic transport failure.

---

## P8 — Context overflow (design risk)

Build a prompt larger than the server's `n_ctx` from P2.

```bash
python3 - <<'PY' > /tmp/probe_p8.json
import json
filler = ("The quick brown fox jumps over the lazy dog. " * 4000)
print(json.dumps({
    "messages": [{"role": "user", "content": filler + "\n\nWhat was the FIRST sentence?"}],
    "max_tokens": 80,
    "temperature": 0,
}))
PY
lc -H "Content-Type: application/json" \
  -X POST "$LBASE/v1/chat/completions" -d @/tmp/probe_p8.json
```

**Answers:** does the server return an error, or does it silently drop the front of the
prompt and answer anyway? Capture `usage.prompt_tokens` from the response.

This is the failure D4 exists to prevent. If the server answers silently, the preflight
gate is the only protection and it must be non-optional.

---

## P9 — Is TLS available

```bash
lc -k "https://$LHOST:$LPORT/health" || echo "no TLS on this port"
```

If the server does run TLS, capture the certificate fingerprint. That is what cert pinning
(D6) would store.

```bash
echo | openssl s_client -connect "$LHOST:$LPORT" 2>/dev/null \
  | openssl x509 -noout -fingerprint -sha256
```

**Answers:** whether this host is reachable over TLS today, and whether the cert is
self-signed. Drives how N3 is tested.

---

## P10 — Version and build

```bash
lc "$LBASE/props" | grep -oiE '"(build|version|model_path)[^,]*' || true
llama-server --version   # on mimir
```

**Answers:** the exact build under test. N0 records it. Anything probed here is only true
for this build.

---

## P11 — Concurrency behaviour

Only if the server reports more than one slot in P2. Skip with a note if `total_slots` is 1.

Start a long request in the background, then immediately fire a second one.

```bash
curl -sS ${LKEY:+-H "Authorization: Bearer $LKEY"} -H "Content-Type: application/json" \
  -X POST "$LBASE/v1/chat/completions" -d '{
  "messages":[{"role":"user","content":"Write a 2000 word essay about RAID levels."}],
  "max_tokens": 2000, "temperature": 0
}' > /tmp/probe_p11_long.json &

sleep 2
echo "--- slots while busy ---"
lc "$LBASE/slots"

echo "--- second request while busy ---"
time lc -H "Content-Type: application/json" \
  -X POST "$LBASE/v1/chat/completions" -d '{
  "messages":[{"role":"user","content":"Say OK."}],"max_tokens":5,"temperature":0
}'
wait
```

**Answers:**

- Does `/slots` exist, and does it need a token? What does a busy slot look like?
- Does a second concurrent request succeed, queue, or return an error?
- If it errors, what is the status code and body?

This drives the busy warning (D11) and the "server at capacity" error mapping.

---

## What to send back

1. The saved transcript, key removed.
2. For P2: the `n_ctx` from `/props`, the `-c` and `--parallel` the server was started
   with, and `total_slots`. This is the one I most need.
3. For P5: what `nvidia-smi` showed after the client stopped.
4. For P6: `IDENTICAL` or `DIFFERENT`.
5. For P8: whether it errored or answered, and the `prompt_tokens` value.
6. For P11: whether the second request succeeded, queued, or errored.

Once those land, N0 gets written.
