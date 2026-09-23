# N3 — Remote Security

- Branch: `feature/ollama-analyst`
- Type: **code + docs.** Implemented by codex (DA) under Claude orchestration.
- Status: **IMPLEMENTED 2026-09-23.** Awaiting the HI security review.
- Contract: [`CONTRACT_REMOTE_BACKENDS.md`](CONTRACT_REMOTE_BACKENDS.md) §4.4, §9, §10, §11
- Decisions: **D19** — §4.1-4.3 moved to N2b
- **Security-critical. Flag the diff for HI review before merge.**

## Goal

Make a remote profile safe to use, and say honestly what it does and does not protect.

## Work

### 1. Address policy (§4.1-4.3) — **moved to N2b**

Decision **D19**, 2026-09-22. N2b could not legally reach `mimir` while this lived here,
and a new adapter would have inherited no address guard at all. N2b now owns address
classification, the plaintext acknowledgement, the public-plaintext refusal and run-start
re-resolution.

N3 adds only what remains: a non-loopback `https` profile, refused by N2b, becomes usable
once TLS verification and bearer tokens land below.

### 2. TLS (§4.4)

OS trust store first; then a pinned SHA-256 fingerprint compared with
`hmac.compare_digest`; otherwise refuse and offer to pin what was presented.

**Add a guardrail test banning `verify=False`** across the codebase, in the manner of
`test_messagebox_guardrail.py`.

### 3. Keymaster (§9)

Add an `LLM_SERVER` provider to `experimental/keymaster/store.py`. Reuse the existing
AES-GCM and PBKDF2 machinery; write no new crypto.

Cache session keys for the application lifetime. A remote run started while Keymaster is
locked fails with **"Keymaster is locked"**, never a transport error.

### 4. Egress consent (§10)

- Confirm on every remote run. Dialog names profile, host, model, and what is sent.
- "Mute this session" → `ANALYST_REMOTE_EGRESS_MUTE_KEY` in
  `gui/utils/session_flags.py`. Reuse the `_DeleteConfirmDialog` pattern from
  `dorkbook_window.py:251-312`.
- "Mute forever" → `consent_muted` on the profile row. A new profile brings the dialog
  back.
- Persistent `Remote: <profile name>` marker in the run view regardless of mute.
- Loopback profiles never show it.

Follow the dialog rules in `CLAUDE.md`: `gui.utils.safe_messagebox`,
`ensure_dialog_focus`, named theme styles, never destroy a dialog from a worker thread.

### 5. Run pinning (§6.4)

Refuse a resume whose profile, backend kind, or model identity differs from the recorded
one. Offer "start a new run".

### 6. Leak scanning

Extend the existing leak-scan patterns to bearer tokens and to `reasoning_content`.

### 7. Documentation

- `docs/ANALYST_GUIDE.md` — new section **"Serving Analyst from a shared box"** after
  *Dependency setup*: the context arithmetic, `OLLAMA_NUM_PARALLEL` /
  `OLLAMA_MAX_LOADED_MODELS`, and a plain statement that Analyst does not arbitrate
  access.
- `README.md:588-591` — "digest-pinned Ollama model on loopback" becomes wrong when this
  lands. Update it. Note it is **already stale** on the read-first changes; fix both.
- `CONTRACT_ERRATA.md` — E17 is authored at N0. Confirm it still matches what shipped.

## Closeout, 2026-09-23

| # | Item | Where |
| --- | --- | --- |
| 2 | TLS, trust store then pinned SHA-256 | `tls.py`, `test_analyst_n3_tls.py` |
| 3 | Keymaster `LLM_SERVER` provider | `credentials.py`, `keymaster/store.py` |
| 4 | Egress consent | `analyst_egress_consent.py` |
| 5 | Run pinning | `backend_select.py` |
| 6 | Leak scanning | `test_analyst_n3_leakscan.py` |
| 7 | Documentation | `ANALYST_GUIDE.md`, `README.md` |

Three deviations, each taken deliberately:

1. **The `CERT_NONE` guardrail is scoped to `experimental/analyst/`**, not the whole
   repository. The scanner (`shared/http_browser.py`,
   `gui/utils/protocol_extract_runner.py`) also uses it, behind an explicit
   `allow_insecure_tls` flag, because browsing an arbitrary open directory on an
   untrusted host is the product's purpose and validating certificates there would
   defeat it. Contract 4.4 governs what Analyst sends its own harvested text over,
   not what the scanner reads from strangers. The `verify=False` ban remains
   repository-wide.
2. **Leak scanning is a new test, not an edit to `scripts/analyst_benchmark/leakscan.py`.**
   That file is frozen provenance whose path seals the benchmark policy scripts check.
   The rule is enforced in `shared/tests/test_analyst_n3_leakscan.py` instead.
3. **Keymaster needed a schema rebuild.** Its `CHECK (provider IN ('SHODAN'))` could not
   be widened in place, so `init_db` rebuilds `keymaster_keys` once when it finds the
   narrow form. Verified against a hand-built pre-`LLM_SERVER` sidecar.

### Known gap, for the security review

**A worker subprocess cannot decrypt a Keymaster token.** Keymaster is passphrase-gated
and caches session keys in the GUI process; the worker runs as a separate process and has
no access to them. `resolve_bearer_token` therefore raises "Keymaster is locked" in a
worker rather than silently connecting unauthenticated -- which is the safe failure, but
it means an authenticated remote run cannot currently complete unattended.

Delivering the token to the subprocess safely is a design decision, not an implementation
detail: an environment variable is visible in `ps`, a file on disk defeats the point of
encrypting it, and a socket handshake is a new surface. **Deliberately left for the HI.**
An unauthenticated private-range server under 4.2 is unaffected and works today.

## Acceptance

1. A self-signed server works via a pinned fingerprint; a changed certificate is refused.
2. `verify=False` appears nowhere; the guardrail test proves it.
3. A token appears in no log, no error message, and no artifact.
4. Keymaster locked produces the explicit message.
5. Consent appears on every remote run until muted; session mute resets on restart;
   profile mute survives; a new profile re-prompts; the `Remote:` marker always shows.
6. A resume against a different model is refused.
7. A second client on the HI's laptop over Tailscale works alongside the dev box.
8. Both test suites green; Xvfb screenshots of the consent dialog, profile editor with
   pinning, and a remote run.
9. HI reviewed the security diff before merge.
