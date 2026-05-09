# Web UI v1 Spec

## Goal

Add an optional browser UI for common Dirracuda operations without weakening the
desktop app or widening the default attack surface.

The first release is deliberately narrow. It should feel like a control panel,
not a replacement for the Tkinter GUI.

## User-Facing Scope

v1 includes:

- Login/logout for one local admin user.
- Health/status page.
- Dashboard summary with recent scan/task state.
- Scan launch for SMB, FTP, HTTP, and multi-protocol scan requests.
- Scan queue with one active scan at a time.
- Progress view using polling first; Server-Sent Events or WebSockets can come
  later if the polling contract is solid.
- Read-only host summaries by protocol.
- Share/directory summary data in results where the existing database/probe
  paths have it.
- Copy endpoint action for SMB/FTP/HTTP host rows.
- Optional post-scan probe for verified hosts when selected during scan launch.
- Database export/download for the active SQLite database.
- Limited web UI config view/edit:
  - bind address
  - port
  - TLS enabled flag and certificate/key paths
  - explicit insecure-mode override for operators who disable TLS
  - remote access enabled flag
  - CIDR allowlist
  - session timeout
  - max Shodan results cap defaults
- Desktop Experimental tab entry:
  - new `Web UI` tab between `Reddit` and `Dorkbook`
  - short description
  - one button, `Open Web UI Control`
- Web UI control dialog launched from that button.

## Out Of Scope For v1

- Interactive file explorer surfaces.
- Downloading target files through the web UI.
- DB import/merge.
- Dorkbook, Keymaster, Reddit, or SearXNG web pages.
- Multi-user roles.
- Public API tokens.
- CORS for arbitrary external frontends.
- Running unauthenticated, even on localhost.
- Replacing the desktop GUI.

## Critical Behavior Decisions

### Scan Execution

The web server launches existing CLI entrypoints with `subprocess.Popen(...)`
using argument lists and `shell=False`.

Reason:

- The desktop GUI already treats CLI subprocesses as the runtime scan boundary.
- Crash and cancel behavior is easier to contain.
- The subprocess security model is clearer than passing user input into a shell.
- Direct workflow calls can be revisited after v1 has real usage.

### Auth Model

The web UI uses server-side sessions:

- Login returns an opaque session ID cookie.
- Session IDs are generated with `secrets`.
- Cookies are `HttpOnly`, `SameSite=Strict`, and `Path=/`.
- Cookies are `Secure` whenever TLS is enabled.
- Session expiry defaults to 30 minutes idle.
- Absolute session expiry defaults to 8 hours.
- Logout deletes server-side state and clears the cookie.

Bearer API tokens are deferred. They are useful for automation, but they add a
second auth path and storage lifecycle. That is not first-release work.

Login submission should use JSON so v1 does not need an extra form-parsing
dependency. If HI later wants no-JavaScript form login, approve that dependency
as a separate decision.

### Credential Storage

Credentials live in `~/.dirracuda/conf/webui_creds.json`, mode `0600`.

Store:

- username
- password hash
- salt
- algorithm metadata
- iteration count
- created/updated timestamp

Use PBKDF2-HMAC-SHA256 with at least 600,000 iterations and a unique random salt.
Cap accepted password length so login cannot become a CPU denial-of-service path.

### Remote Access

Default:

- `enabled=false`
- `bind_address=127.0.0.1`
- `port=5480`
- `tls.enabled=true`
- `tls.allow_insecure_remote=false`

Remote mode requires:

- explicit `remote_enabled=true`
- non-loopback bind address
- CIDR allowlist
- TLS enabled with readable cert/key paths unless the operator explicitly sets an
  insecure override
- visible warning in the control dialog and docs

No remote mode should work by accident. TLS-off remote mode is allowed only as a
deliberate operator choice with loud UI/docs warnings.

## Acceptance Criteria

- Existing desktop GUI still launches through `./dirracuda`.
- Existing Experimental dialog stays tabbed.
- Tab order becomes: `SearXNG`, `Reddit`, `Web UI`, `Dorkbook`, `Keymaster`.
- Web service does not start unless credentials exist.
- First credential setup is done through a CLI command or control dialog path
  that writes `webui_creds.json` with restrictive permissions.
- Every protected endpoint rejects anonymous requests.
- Mutating requests require CSRF protection.
- Scan requests validate types, ranges, protocol names, result caps, and
  post-scan probe options.
- Scan requests never use shell command strings.
- Only one scan subprocess runs at a time; later scans queue.
- Results endpoints are read-only and parameterized.
- Export endpoint writes/serves only expected database export artifacts.
- Remote bind without an allowlist fails closed.
- Remote bind without TLS fails unless the explicit insecure override is set.
- Mobile layouts are usable at phone widths, not just desktop browser widths.
- README and `docs/TECHNICAL_REFERENCE.md` are updated when runtime behavior
  actually lands.

## Definition Of Done

Each implementation card closes with:

- root cause / design reason stated
- smallest safe change applied
- line counts before/after for touched files
- targeted tests run
- broader regression run when risk warrants
- docs updated if behavior changed
- HI manual gate marked `PENDING` or `PASS`
