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

No raw passwords in logs. No password echo in UI after setup.

## Remote Mode

Remote mode is explicit:

```json
{
  "enabled": false,
  "bind_address": "127.0.0.1",
  "port": 5480,
  "remote_enabled": false,
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

Reject unexpected fields. Keep coercion explicit; do not quietly turn weird text
into dangerous defaults.

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

Do not log:

- raw passwords
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
