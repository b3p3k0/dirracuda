# Remote Backends Workspace

- Date opened: 2026-09-20
- Branch: `feature/ollama-analyst`
- Status: **planning only.** No code card is authorized.

## Goal

Let Analyst send work to a model server on another machine, and support llama.cpp
`llama-server` next to Ollama. The point is to offload analysis to stronger hardware
than the machine running Dirracuda.

Built for a general user base, not for one operator's host.

## Why this directory still says "ollama"

`docs/dev/ollama_integration/` is referenced by exact path in 15 Python files. Several
are frozen provenance and leak-scan seal lists:

- `scripts/analyst_benchmark/leakscan.py:48-53`
- `scripts/analyst_benchmark/c0b3_policy.py:40-46`
- `scripts/analyst_benchmark/c0b4_policy.py:33-43`
- `scripts/analyst_benchmark/c0b2_runtime.py:62`

Renaming the directory breaks those seals. The name stays. The work inside it is no
longer Ollama-only.

## Documents

| File | What it is | State |
| --- | --- | --- |
| `ARCHITECTURE.md` | Proposed design. Read this first. | Draft, 2026-09-20 |
| `PROBE_PROTOCOL.md` | Step 0. Read-only probes the HI runs against a real llama.cpp host. | Ready to run |
| `N0_CARD.md` | Contract freeze card. | **Executed 2026-09-20** |
| `CONTRACT_REMOTE_BACKENDS.md` | The frozen contract. Changes need an erratum. | Frozen at N0 |
| `KICKOFF_PROMPT.md` | Paste-able starting prompt for the inheriting PA/RA. | Ready |
| `N1_CARD.md` / `N2_CARD.md` / `N3_CARD.md` | Implementation cards. | HELD, each needs its own PASS |
| `PROBE_RESULTS.md` | Step 0 output, run against `mimir`. Read this with ARCHITECTURE. | Complete |

## Sequence

| Step | Name | Type | Gate |
| --- | --- | --- | --- |
| 0 | Probe | ~~HI runs~~ **Done 2026-09-20** over SSH against `mimir`. See `PROBE_RESULTS.md`. | Complete |
| N0 | Contract freeze | ~~Docs.~~ **Done 2026-09-20.** Contract frozen, E17 accepted, R102-R114 registered, N1-N3 written. | Senior review + HI PASS |
| N1 | Endpoint + profile plumbing | Code. Un-freezes the endpoint, adds server profiles, wires the dead GUI controls. | Own review |
| N2 | OpenAI-compatible adapter | Code. llama.cpp support, local and LAN. | Own review |
| N3 | Remote security | Code. TLS, token in Keymaster, cert pinning, egress consent. | Own review |

A PASS on one card authorizes only that card.

## Decision ledger

Decided with the HI on 2026-09-20. Do not relitigate without a new decision here.

| # | Decision | Rationale |
| --- | --- | --- |
| D1 | Two transports: keep native Ollama `/api/chat`, add one generic OpenAI-compatible adapter. | Ollama's `/v1` silently drops `num_ctx`, `top_k`, `min_p`, `repeat_penalty`, `keep_alive`, and returns no model digest. A single-dialect design would downgrade the already-benchmarked local path. |
| D2 | Remote defaults to TLS + bearer token. An explicit opt-out allows plaintext, and only to private address ranges. | Makes the common home-lab case reachable without shipping an unauthenticated-by-default network client. |
| D3 | Model identity is recorded per backend and labeled. Ollama: tag + sha256 digest, marked verified. OpenAI-compatible: name + server properties, marked unverified. | A file path must not look like a cryptographic digest in `report.json`. |
| D4 | Required context size is a preflight gate. Read `n_ctx` from the server; refuse the run if it is too small and name the exact fix. | llama.cpp cannot set context per request. Silent truncation produces garbage reports with no detectable signal. |
| D5 | Server profiles are named and stored. The bearer token goes in Keymaster under a new provider kind. | Reuses reviewed AES-GCM + PBKDF2 crypto instead of writing a second secret store. |
| D6 | TLS verifies against the OS trust store, or against a pinned certificate fingerprint stored on the profile. No "ignore certificate errors" code path exists. | Pinning gives self-signed hosts the same convenience without a documented MITM hole. |
| D7 | Egress consent confirms on every remote run, with "mute this session" and "mute forever" options. Mute-forever is per profile. | Reuses the existing `gui/utils/session_flags.py` pattern. Muting one host must not mute a host added later. |
| D8 | Backend kind is auto-detected at Test time, with a manual override dropdown. | Two cheap probes remove a setup step; the override is the escape hatch for proxies and future backends. |
| D9 | A run is pinned to its profile, backend kind, and model. A mismatched resume is refused; the UI offers a new run instead. | One report must come from one model, or its grounding story is void. |
| ~~D10~~ | ~~Gate on the per-slot context.~~ **Superseded 2026-09-20 by probe P2.** | The division by `--parallel` is version-dependent and did not happen on build `b1-f280b26`. Replaced by D10a. |
| D10a | Detect router vs single server at `/props`. Read context per model from `/v1/models` when routed, from `/props` when single. Always honour the server's `exceed_context_size_error`. | A router reports `n_ctx: 0`. llama.cpp errors cleanly on overflow rather than truncating, so the server is the real guarantee and preflight is fail-fast UX. |
| D14 | Send `chat_template_kwargs.enable_thinking=false` on every OpenAI-compatible request. Never persist `reasoning_content`. | The field echoes sensitive input verbatim and consumes the token budget. Measured on `qwen3.8-27b`. |
| D15 | Filter the model dropdown to text-generation models. | A router catalogue also carries embeddings-only and vision models that cannot serve a chat run. |
| D16 | Always send explicit sampling values. Never inherit server defaults. | `mimir`'s presets ship `temperature 1.0`, `top_p 0.95`. |
| D11 | Analyst does not arbitrate access to a shared server. It warns when slots are busy, maps capacity errors to plain messages, and records contention in `report.json`. | Neither backend exposes a lock primitive. Arbitration would need a sidecar coordinator — more moving parts than the problem is worth. |
| D12 | Server-admin guidance is a new section in `docs/ANALYST_GUIDE.md`, not a new document. | The guide already carries hardening, troubleshooting, and privacy. One discoverable place beats a second file nobody opens. |
| D13 | N2 covers local and LAN together. There is no local-only stage. | The D2 plaintext opt-out already makes a remote private-range host reachable, so a local-first split buys nothing. |

## Test hosts

- **llama.cpp**: `mimir` at Tailscale `100.125.197.36`, router mode on port 9292, build
  `b1-f280b26`. Reachable from the dev box in 1.7 ms; LAN path blocked by firewalld.
  Unauthenticated, which is why D2's private-range opt-out exists.
- **Remote transport, automated**: this development machine has Tailscale at
  `100.91.126.66`. That address is inside `100.64.0.0/10`, so a self-connect over the
  Tailscale IP exercises the real private-range check rather than a loopback shortcut.
  This is the gate before manual testing.
- **Remote transport, manual**: the HI connects from a laptop over Tailscale.
- **Remote Ollama**: no dedicated host. Covered by the same self-connect path.

## Resolved at N0

The `CONTRACT.md` local-only wording (lines 218-224) is superseded by **erratum E17**,
following the established pattern. `CONTRACT.md` itself is unedited.

## Starting the next session

Paste [`KICKOFF_PROMPT.md`](KICKOFF_PROMPT.md). It front-loads the five failure modes the
inheriting agent is most likely to hit.
