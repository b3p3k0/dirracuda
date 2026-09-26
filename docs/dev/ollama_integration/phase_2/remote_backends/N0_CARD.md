# N0 — Remote Backends Contract Freeze

- Date drafted: 2026-09-20
- Branch: `feature/ollama-analyst`
- Type: **docs / contract freeze.** No product code.
- Status: **executed 2026-09-20.** Awaiting senior review + HI PASS.

## Why N0 exists

Remote network access plus a second model backend is a non-trivial feature touching
auth, credentials, and transport security. The senior-review gate requires a frozen
contract before any code.

A review PASS on N0 authorizes **only** N0. N1, N2, and N3 each stay HELD until their
own review.

## Step 0 is done

Probes ran against `mimir` on 2026-09-20 over SSH. Results in `PROBE_RESULTS.md`.

Outcome: two design assumptions were wrong, one risk closed, two new risks found.

- **RB-1 closed.** `response_format: json_schema` works strictly. No GBNF fallback.
- **RB-2 closed for llama.cpp.** A client disconnect frees the slot in ~4s.
- **D10 superseded.** `--ctx-size` is not divided by slots on this build. The real problem
  is router mode reporting `n_ctx: 0`. Replaced by D10a.
- **New: RB-11 to RB-13.** Cold-model load latency, `reasoning_content` leakage, and
  non-chat models in the catalogue.

N0 is written against these measurements, not against the public docs.

## What N0 delivers

New documents in this directory:

1. `N0_CARD.md` — this card.
2. `CONTRACT_REMOTE_BACKENDS.md` — the frozen contract, written against probe output.
   Names what it supersedes in `CONTRACT.md` and `CONTRACT_READ_FIRST.md`, and what it
   keeps.
3. `N1_CARD.md`, `N2_CARD.md`, `N3_CARD.md`.
4. `PROBE_RESULTS.md` — already written.

Pointer and append edits (no frozen file is changed):

- `phase_2/README.md` — links this workspace as active.
- `RISK_REGISTER.md` — appends RB-1 through RB-13 from `ARCHITECTURE.md` §11 as **R102-R114**. Frozen
  Phase 1 entries untouched.
- `CONTRACT_ERRATA.md` — appends one entry superseding the local-only wording at
  `CONTRACT.md:218-224`.

`CONTRACT.md` stays frozen and unedited. Supersession goes in errata, the established
pattern.

## Working model

- **Claude = PA/RA + orchestrator.** Writes specs and cards, directs codex, validates
  returned diffs, runs tests, reports to HI.
- **Codex = DA.** Implements one approved card at a time via the `codex` CLI.
- **Operator = HI.** Priority, acceptance, risk ownership, live validation, and Step 0.

N0 is documentation, so Claude authors it. Delegation to codex begins at N1.

## Frozen decisions (do not relitigate)

D1 through D9 in `README.md`. Summarised:

1. Two transports. Native Ollama `/api/chat` stays as benchmarked; one new generic
   OpenAI-compatible adapter covers llama.cpp.
2. Remote defaults to TLS plus a bearer token. Plaintext needs a per-profile
   acknowledgement and a private address. Public plaintext is refused with no override.
3. Model identity is per backend and labeled. Ollama reports a verified digest.
   OpenAI-compatible reports an unverified name plus server properties.
4. Required context size is a hard preflight gate. Never truncate silently.
5. Named server profiles. The bearer token lives in Keymaster under a new `LLM_SERVER`
   provider.
6. TLS verifies against the OS trust store or a pinned sha256 fingerprint. No
   `verify=False` path exists.
7. Egress consent confirms on every remote run, with session mute and per-profile
   permanent mute.
8. Backend kind auto-detects at Test time, with a manual override.
9. A run is pinned to its profile, backend kind, and model. Mismatched resume is refused.
10a. Detect router vs single server; read context per model when routed. Always honour
    the server's `exceed_context_size_error`.
11. Analyst does not arbitrate access to a shared server. It warns, fails clearly, and
    records contention. No lock, no coordinator.
12. Server-admin guidance is a section in `docs/ANALYST_GUIDE.md`, not a new document.
13. N2 covers local and LAN together. There is no local-only stage.
14. Send `enable_thinking: false`; never persist `reasoning_content`.
15. Filter the model list to text-generation models.
16. Always send explicit sampling values; never inherit server defaults.

## Card sequence (frozen here; each HELD until its own review)

- **N1 — endpoint and profile plumbing.** Move endpoint constants out of
  `ollama_contract.py` into a new `endpoint.py`. Make `OllamaIdentity` accept a supplied
  endpoint. Add `profiles.py` and the profile schema. Wire the dead GUI controls at
  `analyst_tab.py:156-158`, with profile management in a satellite module. Loopback
  remains the default and the shipped behaviour does not change.
- **N2 — OpenAI-compatible adapter.** `backends/` package, pure `base.py` with a purity
  guardrail test, `openai_api.py`, preflight and the context gate, capability probing,
  backend auto-detection.
- **N3 — remote security.** TLS policy and pinning, `LLM_SERVER` in Keymaster, private
  range enforcement with re-resolution at run start, egress consent dialog and mute
  flags, run pinning and resume refusal, bearer-token leak-scan patterns, and the
  "Serving Analyst from a shared box" section in `docs/ANALYST_GUIDE.md`.

Transport validation: this development machine has Tailscale at `100.91.126.66`, inside
`100.64.0.0/10`. A self-connect over that address exercises the real private-range check
and is the automated gate. The HI then tests from a laptop over Tailscale.

## N0 acceptance

1. `PROBE_RESULTS.md` is referenced as the evidence base, and every contract number
   traces to a measurement in it.
2. `CONTRACT_REMOTE_BACKENDS.md` names exactly which `CONTRACT.md` and
   `CONTRACT_READ_FIRST.md` sections it supersedes and which it keeps.
3. The errata entry replaces the local-only wording without making any claim Analyst
   cannot prove.
4. The contract states the context preflight rule as non-optional, with the exact
   `n_ctx` field path found by probe P2 **and** whether that value is per slot or a
   total that Analyst must divide.
5. The contract states that Analyst does not arbitrate shared-server access, and names
   the three things it does instead: warn, fail clearly, record contention.
6. The contract states the cancellation wording chosen by probe P5, honestly.
7. `CONTRACT.md`, `CONTRACT_READ_FIRST.md`, and all frozen benchmark evidence remain
   unedited.
8. No `scripts/analyst_benchmark/*` file is touched. The frozen path seals still resolve.
9. No runtime dependency file, CI config, or schema code changed in N0.
