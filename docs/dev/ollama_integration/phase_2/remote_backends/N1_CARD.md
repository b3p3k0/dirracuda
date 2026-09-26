# N1 — Endpoint and Profile Plumbing

- Branch: `feature/ollama-analyst`
- Type: **code.** Implemented by codex (DA) under Claude orchestration.
- Status: **HELD** until its own senior review + HI PASS.
- Contract: [`CONTRACT_REMOTE_BACKENDS.md`](CONTRACT_REMOTE_BACKENDS.md) §3, §4, §6.4

## Goal

Make the endpoint a supplied value instead of a module constant, and give Analyst named
server profiles. **Shipped behaviour does not change.** A default loopback Ollama profile
reproduces today's run exactly.

This card adds no new backend and no remote security. It makes both possible.

## Why it is first

`ollama_contract.OLLAMA_ENDPOINT` is a `Final` constant baked into five URLs, a request
identity hash, and a hard assertion in `OllamaIdentity.__post_init__`. Nothing else can
proceed until that is parameterised, and doing it in isolation keeps the diff reviewable
against a known-good baseline.

## Work

### 1. Extract endpoint handling

New `experimental/analyst/endpoint.py`:

- Parse and validate `scheme://host:port`.
- Classify an address: loopback, RFC1918, RFC6598 (`100.64/10`), `fd00::/8`, or public.
- Build the Ollama URL set from a supplied base.
- Pure. No network, no DB, no Tk.

Move `OLLAMA_ENDPOINT`, `OLLAMA_VERSION_URL`, `OLLAMA_TAGS_URL`, `OLLAMA_PS_URL`,
`OLLAMA_CHAT_URL` out of `ollama_contract.py`. Keep a module-level loopback default so
`scripts/analyst_benchmark/*` resolves unchanged.

### 2. Relax the identity assertion

There are **five** frozen-endpoint checks, not one. All five move together or the diff
does not compile. Corrected 2026-09-20 — `ARCHITECTURE.md` §2.2 named only the first.

| Symbol | Line | Check |
| --- | --- | --- |
| `OllamaIdentity.__post_init__` | 286-292 | `self.endpoint != OLLAMA_ENDPOINT` |
| `DiscoveryRequest.__post_init__` | 313-330 | `self.endpoint != OLLAMA_ENDPOINT` |
| `DiscoveryRequest.__post_init__` | 318 | `self.url != OLLAMA_TAGS_URL` |
| `DiscoveryRequest.__post_init__` | 322 | `self.request_sha256 != DISCOVERY_REQUEST_SHA256` |
| `validate_chat_request` | 759-760 | `request.endpoint != OLLAMA_ENDPOINT` |

Each must accept any endpoint that `endpoint.py` validates.

`_discovery_identity_bytes()` (line 106) keeps the URL as an input, so
`DISCOVERY_REQUEST_SHA256` (line 125) and `EXPECTED_IDENTITY` (line 296) become functions
of the endpoint rather than module-level `Final`s. This is correct — the endpoint is part
of the request shape.

Call sites, excluding tests. Counts measured 2026-09-20, not carried over from
`ARCHITECTURE.md` §2.2, which was wrong on three rows:

| File | Occurrences | Note |
| --- | --- | --- |
| `experimental/analyst/ollama_contract.py` | 23 | the definitions themselves |
| `experimental/analyst/ollama_client.py` | 6 | import `:24-26`, use `:196`, `:231`, `:294` |
| `experimental/analyst/contact_contract.py` | 6 | import `:12`, bindings `:77`, `:80`, `:83` |
| `experimental/analyst/service.py` | 2 | import `:27`, bind `:268` |
| `experimental/analyst/ollama_state.py` | 2 | import `:31`, validator `:1253` |
| `scripts/analyst_c9_live_acceptance.py` | 7 | **not** frozen; tested by `scripts/tests/test_analyst_c9_live_acceptance.py:100-103` |

`scripts/analyst_benchmark/c0b{2,4,5,6}_runtime.py` declare their **own** literal
`OLLAMA_ENDPOINT = "http://127.0.0.1:11434"` and never import `ollama_contract`. They are
insulated from this change by construction. The module-level loopback default is
belt-and-braces there, not load-bearing. The seals genuinely at risk are the hardcoded
`docs/dev/ollama_integration/` paths in `c0b2_leakscan.py:21-141`, which endpoint work
does not touch.

### 3. Profiles

New `experimental/analyst/profiles.py` — CRUD on the sidecar DB.

Schema additions (additive, guarded, following the `db_schema.py` migration discipline):

```
analyst_llm_profile(
  profile_id, name, scheme, host, port,
  backend_kind, backend_detected,
  keymaster_key_id, cert_fingerprint,
  plaintext_ack, consent_muted,
  created_at_utc, last_used_utc
)
```

`analyst_runs` gains `profile_id` and `backend_kind`. Both nullable and additive; an
existing run row keeps working.

`analyst_discovered_model` and `analyst_discovery_contact` already carry `endpoint`
(`db_schema.py:518`, `:534`) and need no change.

### 4. Wire the dead GUI controls

`analyst_tab.py:156-158` defines `_server_kind_var`, `_server_host_var`,
`_server_port_var`. Nothing reads them. Connect them to real profiles, or delete them.

**Put profile management in a satellite module from the start.** `analyst_tab.py` is
1323 lines; adding a profile editor inline pushes it toward the 1700 pause threshold.
Follow the `_mb()` / `_d()` dispatch discipline in `CLAUDE.md` so test monkeypatches keep
intercepting.

`discover_models()` and `list_discovered_models()` in `service.py` take an endpoint.

## Remote is saved but not reachable (D17)

N1 parameterises the endpoint. N3 writes the transport policy. Between them the door
exists with no lock, so N1 supplies a temporary one.

- The profile editor **saves** a non-loopback profile normally. Full CRUD, no special case.
- The network client **refuses to open a connection** to any non-loopback endpoint, with
  an explicit "remote servers are not enabled yet" result. Not a transport error.
- The guard lives in `ollama_client.py`, the last step before the socket. Not in the UI —
  a script or a direct DB write must not be able to bypass it.
- The profile editor states plainly that a remote profile cannot run yet. Silent failure
  is not acceptable.
- N3 removes the guard. One place to unwind.

## Out of scope

No OpenAI adapter. No TLS. No Keymaster. No consent dialog. No backend detection. Those
are N2 and N3.

## File size

| File | Before | Limit |
| --- | --- | --- |
| `ollama_contract.py` | 977 | should **shrink** — constants move out |
| `analyst_tab.py` | 1323 | must not exceed 1500; use a satellite module |
| `service.py` | 1000 | ≤1200 |
| `ollama_state.py` | 1288 | ≤1400 |
| `db_schema.py` | 1272 | ≤1500 |

## Acceptance

1. A default loopback Ollama run is byte-identical to the pre-change build.
2. `scripts/analyst_benchmark/*` is unedited and its path seals still resolve. Re-run the
   leak scan and provenance checks.
3. The endpoint appears in the discovery identity hash, and two different endpoints
   produce two different hashes.
4. `endpoint.py` classifies loopback, RFC1918, CGNAT, and public correctly, with tests
   per class.
5. Profile CRUD round-trips. Migration on an existing DB is additive and reversible in
   the sense that old rows still read.
6. The GUI host/port controls drive a real profile, or are gone.
7. A non-loopback profile saves and round-trips, and a connection attempt against it is
   refused with the "remote servers are not enabled yet" result. A test asserts the
   refusal happens in the client, not only in the UI.
8. `./venv/bin/python -m pytest` for `shared`/`experimental` and `gui` separately, both
   green. Xvfb screenshot of the profile editor.
