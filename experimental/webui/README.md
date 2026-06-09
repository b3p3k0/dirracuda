# Web UI

Optional browser companion to the desktop app for scan control, results review, export, and Web UI configuration.

Status: active experimental feature (current implementation under `experimental/webui`).

---

## Prerequisites

Install Web UI dependencies separately from the main desktop install:

```bash
pip install -r experimental/webui/requirements-web.txt
```

Packages: `fastapi`, `uvicorn`, `jinja2`, `httpx`.

---

## Quick Start

1. Create credentials (choose one path):

   - Desktop: `Experimental -> Web UI -> Manage Credentials`
     - Existing single-account credentials can be reset without the old
       password because the desktop workflow trusts access to the unlocked
       workstation.
     - New passwords must be entered twice.
   - Headless CLI:

   ```bash
   ./dirracuda-d credentials set admin
   ```

2. Start server:

```bash
./dirracuda-d start
```

3. Open `http://127.0.0.1:2600` and log in.

Default bind: `127.0.0.1:2600`.
Legacy explicit `port: 5480` entries in `webui.json` are auto-migrated to `2600` on load.

### Optional startup flags

| Flag | Default | Notes |
|------|---------|-------|
| `--host` | from config (`127.0.0.1`) | Bind override, re-validated at startup |
| `--port` | from config (`2600`) | Port override, re-validated at startup |
| `--config` | default webui config path | Load/save a specific `webui.json` |

Direct `python -m experimental.webui.server` remains available for debugging,
but it now requires a usable credential store and exits before binding when
credentials or runtime security configuration are invalid.

---

## Headless Daemon CLI

`./dirracuda-d` is a display-independent wrapper around the Web UI runtime. It
re-executes through `./venv/bin/python`; activating the virtualenv is not
required.

```bash
./dirracuda-d start
./dirracuda-d stop
./dirracuda-d restart
./dirracuda-d status
./dirracuda-d logs -n 100
./dirracuda-d logs --follow
./dirracuda-d doctor
./dirracuda-d config path
./dirracuda-d config check
```

Add `--json` to lifecycle, status, doctor, config, credential, or non-following
log commands for automation. Status exit codes are `0` for healthy, `3` for
cleanly stopped, and `1` for unhealthy or ambiguous ownership.

Direct background mode stores:

- PID metadata: `~/.dirracuda/state/webui.pid` (`0600`)
- Log: `~/.dirracuda/logs/app/webui.log` (`0600`, 5 MiB rotation, three backups)

### Optional per-user systemd service

```bash
./dirracuda-d systemd install
./dirracuda-d systemd status
./dirracuda-d systemd uninstall
```

Installation writes `dirracuda-d.service` under the current user's systemd unit
directory, enables it for the user manager, and starts it immediately. It does
not install a system-wide unit or enable user lingering, so automatic startup
normally begins when the user manager/login starts.

When the unit is installed, daemon and desktop lifecycle controls automatically
delegate to `systemctl --user`; otherwise they use direct background mode.
Dirracuda refuses to overwrite or remove a unit that lacks its managed marker.

---

## Desktop Control Surface

From the desktop app: `Experimental -> Web UI`

Available controls:

- Service status and active backend (`direct` / `systemd`)
- Start / Stop / Open in Browser / Copy URL
- `Manage Credentials` dialog for first-time setup and trusted local reset
- `WebUI Config` dialog with `Save` and `Save & Restart`

After a trusted desktop reset, account-specific pair lockouts are cleared. A
running managed service is restarted to revoke in-memory browser sessions; a
stopped service remains stopped. Restart or lockout-cleanup problems are shown
as warnings after the credential save rather than hiding the successful reset.

---

## Feature Overview

### Dashboard (`/dashboard`)

- Service mode and URL summary
- Queue snapshot (active + queued tasks)
- Shodan query-credit status

Shodan balance is fetched server-side only. The API key is never sent to the browser. Balance checks are manual-refresh with a 150-second server cache and sanitized failure reasons.

### Scans (`/scans`)

- Queue SMB / FTP / HTTP scan tasks (single active task, FIFO queue)
- Queue table hydrates from server-side queue state (`GET /api/scans`), so active/queued entries persist across page refresh/navigation
- Scan writes and `/results` reads share one resolved DB source (main `config.json` database path by default; explicit `db_path` override still wins)
- Per-task max-results is enforced (`max_shodan_results`)
- Mandatory preflight confirmation before queueing:
  - estimated query-credit cost (`ceil(max_shodan_results / 100)` per selected protocol)
  - live Shodan balance when available
  - estimated post-scan balance when live balance is available
  - fallback dashboard link when balance is unavailable/no_key
- SMB scans run in legacy mode by default (includes SMB1-capable targets)
- Optional protocol-aware post-scan probe pass (SMB / FTP / HTTP)
- Task progress, status polling, and cancel support

### Results (`/results`)

- Default protocol view is `ALL` and loads on page open
- Search is desktop-style: case-insensitive substring match on IP and accessible-share text
- Row filters:
  - `Show Only Shares > 0`
  - `Favorites Only`
  - `Hide Avoid`
- Inline row actions:
  - Click `Favorite`, `Avoid`, or `Probed` cells to toggle state on that row
  - `Probed` toggle uses desktop compromised semantics (`issue/clean` with `indicator_matches` `1/0`)
- Bulk actions (current page only):
  - Select rows with the leading checkbox column
  - Use `Toggle Favorite`, `Toggle Avoid`, `Toggle Compromised`, `Probe Selected`, or `Clear Selection`
  - Bulk actions are per-row toggles, not force set/unset
  - Probe actions are asynchronous; status is polled until completion
  - Only one probe job runs at a time; overlapping starts return a conflict and reuse the active job
  - Selection resets on results reloads (filter/page/protocol changes)
- Manual refresh model (`Refresh` button), no auto-refresh
- Pagination controls: First / Prev / Next / Last / Jump to
- Row click opens inline details accordion:
  - compact overview + read-only notes
  - nested full-details scrollbox (`Show Details` / `Hide Details`)
  - `Open with system` action in the details panel (new-tab/window handoff)
  - `Run Probe` beside `Open with system` starts a protocol-aware probe for that host
  - open-path selection uses explicit probe base path when present; otherwise `/`
  - external-app caution is always shown in details; private/incognito mode cannot be forced from browser JS
  - includes stored probe snapshot tree when present
  - falls back gracefully on older DB schemas

### Export (`/api/export`)

- Creates a clean DB copy using `VACUUM INTO`
- Writes artifacts to `~/.dirracuda/exports/`
- Download endpoint serves only generated export filenames (allowlist + directory containment checks)

### Config (`/config`)

- Edit Web UI bind, remote mode, TLS, allowlist, trusted DNS hosts, and session timeouts
- Includes browser preference storage controls (enable / disable / clear)
- Config save is CSRF-protected
- **Changes take effect on restart**

---

## Configuration

Config file: `~/.dirracuda/conf.d/experimental/webui.json`

If the file is absent, safe in-memory defaults are used. The file is created when config is explicitly saved.

Key fields:

- `bind_address`, `port`
- `remote_enabled`
- `allowed_cidrs`
- `trusted_hosts`
- `session_timeout_idle`, `session_timeout_absolute` (seconds)
- `tls.enabled`, `tls.cert_file`, `tls.key_file`, `tls.allow_insecure_remote`

---

## Localhost vs Remote Mode

### Localhost mode (default)

- Loopback bind (`127.0.0.1` or `::1`)
- TLS may be disabled

### Remote mode

Non-loopback bind requires:

- `remote_enabled=true`
- non-empty `allowed_cidrs`
- TLS configured with cert/key, **or** explicit insecure override (`tls.allow_insecure_remote=true`)

When remote mode is enabled with a loopback bind, config normalization changes
`127.0.0.1` to `0.0.0.0` or `::1` to `::`. These wildcard values are listener
addresses, not browser destinations. Local browser and health checks use
`127.0.0.1` or `::1`; LAN clients use the host's actual interface address, for
example `http://192.168.1.251:2600` on the system used to validate this behavior.

Startup fails fast on unsafe combinations (no silent downgrade).
Entering remote plaintext HTTP from either configuration UI requires explicit
confirmation. The login page, daemon status/checks, and desktop controls retain
a visible plaintext warning until TLS is enabled or remote mode is disabled.

### Allowlist behavior

Allowlist checks run as HTTP middleware only when `remote_enabled=true`.

The check uses `request.client.host` (the TCP peer). The bundled direct server
disables forwarded-header trust; reverse-proxy deployment is outside this
configuration surface.

### Host validation

- IPv4/IPv6 literal hosts and `localhost` are accepted automatically.
- Custom DNS names must appear in `trusted_hosts`.
- Names are stored as lowercase IDNA without trailing dots.
- Wildcards, schemes, ports, paths, malformed names, and duplicates are rejected.

---

## Security Notes

- Startup fails closed when no usable Web UI credential exists.
- Credentials are hashed with PBKDF2-HMAC-SHA256 (600k iterations) and stored at `~/.dirracuda/conf/webui_creds.json` (`0600` permissions).
- Browser password changes require the current password, an authenticated
  session, same-origin validation, and CSRF protection.
- Desktop password resets trust the unlocked workstation, require confirmation
  of the new password, and never ask for the old password.
- Known and unknown usernames perform equivalent PBKDF2 verification work.
- Login usernames/passwords are capped at 128/1,024 UTF-8 bytes.
- Rate-limit subjects are hashed, pair and IP-wide lockouts are enforced, and state is capped at 4,096 rows.
- Request limits are 4 KiB for login bodies, 1 MiB for other bodies, 8 KiB for targets, and 16 KiB/100 fields for headers.
- Direct logs rotate continuously at 5 MiB with three private backups.
- Session cookies are HttpOnly + SameSite=Strict; session store is in-memory.
- CSRF checks are enforced on mutating routes.
- Shodan key handling stays server-side only.
- Browser preference persistence uses `localStorage` (not cookies), opt-in only, and stores allowlisted non-sensitive toggles/selectors only.
- Export files are full DB copies and persist on disk until manually cleaned up.

---

## Known Limitations

- No in-browser file explorer or target file download workflows
- No web-based DB import/merge
- No token-based API auth (session-cookie auth only)
- Single active scan task at a time
- Sessions are lost on server restart
- No automatic results/dashboard refresh polling
