# Web UI Security Model

The web UI is security-sensitive because it can launch scans, read local
research data, and expose a browser surface. Default-deny is the baseline.

## Threats We Care About

- Someone on the network finds the web UI and launches scans.
- A browser attack triggers a scan through an authenticated operator session.
- User-supplied scan text becomes shell input.
- User input reaches SQL unsafely.
- Remote mode accidentally starts without TLS or an allowlist.
- TLS is disabled casually because localhost development made it annoying.
- Logs leak Shodan keys, passwords, session IDs, local paths, or target details
  beyond what the operator expects.
- SQLite lock contention corrupts confidence or blocks the desktop app.
- Web export serves files outside the intended export directory.

## Authentication

All endpoints require auth except:

- static assets required by the login page
- login page
- login submission
- health endpoint if explicitly configured as public-local only

Session details:

- opaque random session id
- server-side session store
- idle timeout default: 30 minutes
- idle timeout bounds: 5 minutes minimum, 240 minutes maximum
- absolute timeout default: 8 hours
- absolute timeout bounds: 1 hour minimum, 24 hours maximum
- cookie name: `__Host-dirracuda-session` when TLS is enabled
- cookie flags: `HttpOnly`, `SameSite=Strict`, `Path=/`
- `Secure` when TLS is enabled

For localhost HTTP, `Secure` cannot be used. Remote mode uses TLS by default, so
remote sessions should use `Secure`. If an operator explicitly disables TLS, the
UI and logs must say the session cookie cannot get full transport protection.

OWASP's current session guidance gives 15-30 minute idle timeouts as common for
lower-risk apps and 4-8 hour absolute windows for full-day use. Dirracuda is not
a banking app, but it can launch scans and expose local research data. The v1
defaults split the difference: 30 minute idle, 8 hour absolute, with bounded
operator overrides.

## CSRF

Every mutating request must include CSRF protection.

Acceptable v1 pattern:

- server issues a per-session CSRF token
- templates include the token in a meta tag or hidden input
- JavaScript sends it as `X-CSRF-Token`
- server rejects missing/mismatched token
- server also checks Origin/Referer for mutating browser requests when present

Do not enable credentialed wildcard CORS.

## Password Storage

Use PBKDF2-HMAC-SHA256 with:

- unique random salt
- at least 600,000 iterations
- constant-time comparison
- metadata in the stored record
- max accepted password length to avoid CPU abuse

Credential file:

```text
~/.dirracuda/conf/webui_creds.json
mode 0600
```

The file is written atomically via `_atomic_write_json` (`config.py`), which sets mode
`0600` before the final rename. On every read, `auth._load_creds()` calls
`_check_creds_permissions()` and raises `CredentialError` if the mode is not exactly
`0600`. Caller behaviour:

- `verify_password()`: absorbs `CredentialError` and returns `False` — "never raises"
  contract preserved.
- `set_password()`, `credential_exists()`, `get_credential_usernames()`: propagate
  `CredentialError` — a misconfigured store blocks all mutations until repaired.
- Web `_change_password` route: calls `check_credential_store()` as a preflight before
  `verify_password()`, so bad permissions surface as HTTP 503 (not 401).
- Desktop credential dialog: catches `CredentialError` at open time and shows a
  repair message. The trusted-workstation reset path preflights permissions,
  requires the new password twice, and writes through `set_password()` without
  checking the old password.

Permission enforcement is POSIX-only. On Windows the check is a no-op.

Operator repair:

```bash
chmod 0600 ~/.dirracuda/conf/webui_creds.json
```

No raw passwords in logs. No password echo in UI after setup.

The browser and desktop use different authorization boundaries for password
changes. The browser requires an authenticated session, current password,
same-origin request, and CSRF token. The desktop assumes access to the unlocked
workstation authorizes recovery. After a desktop reset, account pair lockouts
are removed while IP-wide spray state is retained. A running managed service is
restarted to revoke all in-memory browser sessions; stopped services remain
stopped.

## Remote Mode

Remote mode is explicit:

```json
{
  "enabled": false,
  "bind_address": "127.0.0.1",
  "port": 2600,
  "remote_enabled": false,
  "trusted_hosts": [],
  "tls": {
    "enabled": true,
    "allow_insecure_remote": false,
    "cert_file": "",
    "key_file": ""
  },
  "allowed_cidrs": ["127.0.0.1/32", "::1/128"]
}
```

Rules:

- TLS is enabled by default.
- Loopback bind may explicitly disable TLS for local-only development.
- Non-loopback bind requires `remote_enabled=true`.
- Non-loopback bind requires at least one non-empty CIDR allowlist entry.
- Non-loopback bind uses TLS by default.
- Non-loopback bind without TLS requires `tls.allow_insecure_remote=true` and
  must emit a high-visibility warning.
- Transitioning into remote plaintext mode requires confirmation in both
  configuration UIs.
- IP-literal Host values and `localhost` are accepted; DNS names require an
  exact canonical `trusted_hosts` entry.
- If remote config is invalid, startup fails with a safe error.

## Input Validation

Use Pydantic request models for API inputs.

Validate:

- protocol names
- country codes
- result caps
- concurrency bounds
- timeout bounds
- rescan flags
- dork/filter length
- bind address
- port
- CIDR values
- filesystem paths for cert/key/export
- login username/password byte lengths
- request body, target, and header sizes
- trusted DNS Host names

Reject unexpected fields. Keep coercion explicit; do not quietly turn weird text
into dangerous defaults.

Transport-level limits:

- login bodies: 4 KiB
- other request bodies: 1 MiB
- request target: 8 KiB
- total headers: 16 KiB and 100 fields
- Uvicorn concurrency/backlog: 128 each

Authentication limiter subjects are SHA-256 identifiers, not submitted
usernames or raw source addresses. Pair and source-IP aggregate limits share a
bounded 4,096-row SQLite store.

## Subprocess Safety

All CLI launches use argument lists and `shell=False`.

The web UI must control the executable path. User input may only become validated
arguments to known CLI commands.

Cancel behavior:

- send terminate
- wait bounded time
- kill only the child process group if termination fails
- mark task `cancelled` or `failed` honestly

## Database Safety

- One scan writer at a time.
- One SQLite connection per operation/thread.
- Parameterized queries only.
- Read paths tolerate missing legacy columns/tables.
- Export uses a generated safe filename in a controlled export directory.
- No destructive DB maintenance from v1 web UI.

## Logging

Log:

- login success/failure
- lockout events
- scan launch/cancel/finish
- config changes
- export creation/download
- remote bind startup
- insecure TLS override use
- short account hashes for authentication events

Do not log:

- raw passwords
- submitted usernames
- password hashes unless debugging credential setup locally
- session IDs
- CSRF tokens
- Shodan API keys
- full untrusted file contents

User-facing errors should be boring and safe. Restricted logs can carry detail.

## Service Hardening

Systemd unit hardening target:

```text
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=%h/.dirracuda
RestrictSUIDSGID=yes
```

Exact values may need adjustment for user-service vs system-service mode. Claude
must verify systemd behavior against the local target before claiming it works.

## Trust Boundaries

| Boundary | Mechanism |
|---|---|
| Browser ↔ server | Session cookie (`HttpOnly`, `SameSite=Strict`, `Secure` when TLS); per-session CSRF token validated on every mutating request |
| Server ↔ credential file | Atomic write at mode `0600`; `_check_creds_permissions()` read check; `CredentialError` on any deviation |
| Server ↔ subprocess (scans) | `shell=False`; argument lists only; user input becomes validated argv, never shell syntax |
| Localhost vs remote | Loopback bind: no CIDR enforcement, TLS optional. Non-loopback: `remote_enabled=true` required, CIDR allowlist enforced per-request, TLS on by default |
| Server behind proxy | Unsupported by the bundled trust model. Forwarded headers are ignored and the proxy socket peer is evaluated by the allowlist. |

## Operator Caveats

- **Credential file must be mode `0600`.** All credential operations fail with
  `CredentialError` until the file is repaired. Repair: `chmod 0600 ~/.dirracuda/conf/webui_creds.json`
- **Session store is in-memory.** Restarting the server logs out all active users.
  There is no cross-restart session persistence — this is intentional (see W-004).
- **Rate-limit DB must be writable in remote mode.** If `~/.dirracuda/state/webui_ratelimit.db`
  is unwritable at startup, remote mode refuses to start. Localhost mode degrades
  gracefully (logins unthrottled, health reports `"rate_limiter": "error"`).
- **TLS required for non-loopback by default.** Disabling TLS for remote requires
  `tls.allow_insecure_remote=true` and emits a high-visibility warning.
- **Reverse proxy deployment is outside the supported topology.** The bundled
  server ignores forwarded client and host headers.
