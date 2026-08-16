# C9 — Strict Ollama client, cancellation and shared-GPU policy

Date: 2026-08-16
Status: **C9A offline implementation complete; public live acceptance and C9B
durable resource-pause migration held**

## Issue

Analyst has durable files, attempts and a global worker lease, but no production Ollama
client. Phase 2 therefore cannot yet prove local endpoint/model identity, stream bounded
responses, cancel a request promptly or classify shared-GPU contention without turning it
into a model-quality failure.

## Root cause

The accepted C0B benchmark contains a proven transport, but benchmark modules are not a
production dependency. C8 also deliberately allows only two semantic/model attempts and
has no durable `paused_resource` state. Charging temporary 429/503/OOM scheduling retries
against those two attempts would falsely terminalize healthy work.

## Frozen C9A scope

- Port the proven boundary into focused production modules; never import `scripts/`.
- Accept only `http://127.0.0.1:11434` in V1. Use fixed API paths, ignore ambient
  proxies, disable redirects and reject compression or unexpected content types.
- Require the approved tag and full digest at preflight. Reject known cloud tag forms.
- Build one exact `/api/chat` request: worksheet-v2 JSON schema, one user message,
  `stream:true`, `think:false`, `keep_alive:"15m"`, the frozen deterministic generation
  options and no tool/image/logprobs channels.
- Hash the canonical request before dispatch and recheck the exact detached payload at
  the network boundary.
- Enforce 10-second connect, 180-second idle-read and 600-second caller wall limits;
  bound frame/body/content/thinking and parsed JSON depth/node counts.
- Accept exactly one `done:true` terminal with `done_reason="stop"`. Treat `length` as
  schema/model invalid even if the partial content parses.
- Cancellation closes only the exact response, discards partial/late output and returns
  `cancelled_unverified`. It never unloads a model or claims the server stopped.
- Keep one global in-flight transport permit. A caller may return at its deadline, but
  the permit stays held until the real request/close worker exits.
- Treat explicit 429/503 and narrowly recognized memory/resource errors as
  `resource_busy`. Shared GPU telemetry is advisory only: no free-memory threshold,
  unload, kill or full-residency requirement. CPU offload and longer runtime are valid.
- Provide a pure bounded backoff decision for later orchestration. C9A does not mutate
  C8 or consume semantic attempts.

## C9B gate before C10

C8 v1 cannot durably represent retryable resource contacts separately from its two
semantic attempts. Before orchestration, review an additive v2 migration for a bounded
contact/resource ledger, `not_before` evidence and an honest `paused_resource` state.
Do not overload `interrupted` or silently edit the now-existing canonical v1 database.
The C9B/C10 orchestration gate, not C9A's pure policy, owns cancel-aware waiting and
durable retry scheduling.

## Current live-test blocker

The current Ollama 0.32.5 Docker service is host-networked, listens on all interfaces and
reports cloud support enabled. The unauthenticated API answered on LAN, Tailscale and VPN
addresses. Offline implementation may continue, but no live C9 acceptance or private
content is authorized until the operator makes Ollama loopback-only, sets
`OLLAMA_NO_CLOUD=1`, pins the image/version and verifies non-loopback requests fail.

## Validation gate

- Exact payload, type and hash snapshots; endpoint, tag and digest rejection.
- Fragmented/coalesced hostile NDJSON; duplicate keys; invalid UTF-8/nonfinite JSON;
  forbidden channels; terminal, count and all N/N+1 resource limits.
- Poisoned proxy environment, redirects, compression and content-type failures.
- Connect/read/total deadlines; cancellation before send/headers and during
  thinking/content; response close once; global permit retained until teardown.
- Pure resource classification/backoff boundaries and success reset. C9B/C10 must test
  cancellation during the actual scheduled wait.
- Public live preflight, one short structured chat, one cancellation and bounded health
  request only after the operator prerequisite is satisfied. No private data.
- Focused Analyst regression, public privacy scan, file-size check, README review and
  independent hostile review.

## Offline outcome

**PASS.** The exact C9A suite passed 212 tests; the shared Analyst regression passed 858
tests. Compile, whitespace, public privacy and production file-size checks passed. An
independent hostile review found no remaining offline blocker. No live request, private
document read or Ollama configuration change occurred.

## Primary sources

- Ollama chat API: https://docs.ollama.com/api/chat
- Ollama streaming: https://docs.ollama.com/api/streaming
- Ollama tags and digests: https://docs.ollama.com/api/tags
- Ollama loaded-model context: https://docs.ollama.com/api/ps
- Ollama queue, concurrency and cloud settings: https://docs.ollama.com/faq
- Ollama local API authentication: https://docs.ollama.com/api/authentication
- Requests redirects, proxies and timeouts: https://requests.readthedocs.io/en/latest/api/
- Current disconnect leak report: https://github.com/ollama/ollama/issues/17131
