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

`OllamaIdentity.__post_init__` currently requires `self.endpoint == OLLAMA_ENDPOINT`.
It must accept any endpoint that `endpoint.py` validates.

`_discovery_identity_bytes()` keeps the URL as an input, so `EXPECTED_IDENTITY` becomes a
function of the endpoint rather than a module-level `Final`. This is correct — the
endpoint is part of the request shape.

Call sites, excluding tests: `ollama_client.py` (11), `contact_contract.py` (4),
`service.py` (2), `ollama_state.py` (2).

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

## Out of scope

No OpenAI adapter. No TLS. No Keymaster. No consent dialog. No backend detection. Those
are N2 and N3.

## File size

| File | Before | Limit |
| --- | --- | --- |
| `ollama_contract.py` | 977 | should **shrink** — constants move out |
| `analyst_tab.py` | 1323 | must not exceed 1500; use a satellite module |
| `service.py` | 1000 | ≤1200 |
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
7. `./venv/bin/python -m pytest` for `shared`/`experimental` and `gui` separately, both
   green. Xvfb screenshot of the profile editor.
