# N3 — Remote Security

- Branch: `feature/ollama-analyst`
- Type: **code + docs.** Implemented by codex (DA) under Claude orchestration.
- Status: **HELD.** Requires N2 merged.
- Contract: [`CONTRACT_REMOTE_BACKENDS.md`](CONTRACT_REMOTE_BACKENDS.md) §4, §9, §10, §11
- **Security-critical. Flag the diff for HI review before merge.**

## Goal

Make a remote profile safe to use, and say honestly what it does and does not protect.

## Work

### 1. Address policy (§4.1–4.3)

- Loopback: no TLS, no token required. Unchanged default.
- Non-loopback: `https` + bearer token, **or** `plaintext_ack` limited to RFC1918,
  loopback, `100.64/10`, `fd00::/8`.
- Public plaintext: refused. **No override path may exist.**
- Re-resolve and re-check the address at run start, not only at Test time.

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

## Acceptance

1. A public plaintext endpoint is refused and no code path permits it.
2. A self-signed server works via a pinned fingerprint; a changed certificate is refused.
3. `verify=False` appears nowhere; the guardrail test proves it.
4. A token appears in no log, no error message, and no artifact.
5. Keymaster locked produces the explicit message.
6. Consent appears on every remote run until muted; session mute resets on restart;
   profile mute survives; a new profile re-prompts; the `Remote:` marker always shows.
7. A resume against a different model is refused.
8. A second client on the HI's laptop over Tailscale works alongside the dev box.
9. Both test suites green; Xvfb screenshots of the consent dialog, profile editor with
   pinning, and a remote run.
10. HI reviewed the security diff before merge.
