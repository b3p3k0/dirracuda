# C9 — Strict Ollama client, cancellation and shared-GPU policy

Date: 2026-08-16
Status: **complete; offline implementation, durable scheduling and public-only live
acceptance passed**

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

## C9B outcome before C10

C8 v1 could not durably represent retryable resource contacts separately from its two
semantic attempts. C9B's reviewed additive v2 migration now provides the bounded
contact/resource ledger, `not_before` evidence, cancel-aware waits and explicit joined
`paused_resource` schedule required by C10. Process lifecycle still uses C8's
`interrupted` state when the resource-paused worker releases its lease.

## Operator prerequisite outcome

The operator prerequisite passed on 2026-08-16. The Ollama 0.32.5 container now uses the
immutable image digest `sha256:4dea9fb511947e24a84237bb636b0203abcb2ff0d3fbc7b4ff865deb91362131`,
sets `OLLAMA_HOST=127.0.0.1:11434` and `OLLAMA_NO_CLOUD=1`, and logs both the loopback
listener and `Ollama cloud disabled: true`. Loopback `/api/version` succeeded; requests
to the host's LAN, Tailscale, VPN and container-facing addresses all failed with no
listener. This clears the MVP live-test gate without claiming a general egress firewall.

The HI accepted loopback-only as the MVP boundary, not the permanent deployment model.
A later card may add LAN/Tailscale use through a separately reviewed authenticated TLS
gateway and explicit device/user access policy while Ollama itself remains loopback-only.
Direct unauthenticated port 11434 exposure and public-internet access remain out of scope.

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
  request completed after the operator prerequisite was satisfied. No private data.
- Focused Analyst regression, public privacy scan, file-size check, README review and
  independent hostile review.

## Offline outcome

**PASS.** The exact C9A suite passed 212 tests; the shared Analyst regression passed 858
tests. Compile, whitespace, public privacy and production file-size checks passed. An
independent hostile review found no remaining offline blocker. No live request, private
document read or Ollama configuration change occurred.

## Public live outcome

**PASS.** `scripts/analyst_c9_live_acceptance.py --confirm-live` used only fixed public
synthetic records and the production C9 client. Exact Ollama 0.32.5 and
`qwen3.6:27b@a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e`
preflight passed. The first strict worksheet-v2 chat completed with `done_reason=stop`.
A second chat was cancelled immediately after response headers; the caller returned
`cancelled_unverified` within one millisecond and retained no content or metrics. After
the frozen two-second delay, the following structured health chat succeeded. The runner
printed only request/content hashes, statuses and counts; it did not persist or print
source, prompt, response or reasoning text.

The final strengthened `analyst-c9-live-v1` evidence recorded exactly five ordered
contacts (`version`, `tags`, structured chat, cancellation chat, health chat), a
`2000 ms` health delay and these content-free identities:

| Evidence | Value |
|----------|-------|
| Structured request SHA-256 | `9003104802d53819740d6096fdc8a933cdd141406a69087bde013d398dc50d03` |
| Structured content SHA-256 / eval count | `80b9a62ee836a3fd69f614fe17c5db381aac019059a81bbec58838758394a855` / 125 |
| Cancellation request SHA-256 / outcome | `93622533f01711a51c6f1618d9720d6949be8f071526bc97f979ea010aeb28b8` / `cancelled_unverified` |
| Health request SHA-256 | `885c50b35648034caa1525cf0af22a80812328921ab35b93c715f9f66ff61904` |
| Health content SHA-256 / eval count | `2bd8a4bb17eff6f8b50cb15047277685f2da8daf5ed087dc09dd78249b89ce1b` / 85 |

An earlier exploratory timer cancelled before response headers. Its immediate health
call correctly received `transport_unavailable` because C9 retained the global permit
while the underlying request unwound. It is not acceptance evidence. Freezing the
post-header trigger made the final cancellation and health claims precise and repeatable.

## Primary sources

- Ollama chat API: https://docs.ollama.com/api/chat
- Ollama streaming: https://docs.ollama.com/api/streaming
- Ollama tags and digests: https://docs.ollama.com/api/tags
- Ollama loaded-model context: https://docs.ollama.com/api/ps
- Ollama queue, concurrency and cloud settings: https://docs.ollama.com/faq
- Ollama local API authentication: https://docs.ollama.com/api/authentication
- Requests redirects, proxies and timeouts: https://requests.readthedocs.io/en/latest/api/
- Current disconnect leak report: https://github.com/ollama/ollama/issues/17131
