# Web UI

An optional browser-based companion to the desktop GUI. Run scans, browse results, and export the database from any machine on your network without opening Tkinter. **Experimental — v1 scope only.**

---

## Prerequisites

Web UI dependencies are separate from the main install to avoid bloating the default runtime:

```bash
pip install -r experimental/webui/requirements-web.txt
```

Packages: `fastapi`, `uvicorn`, `jinja2`, `httpx`.

---

## First-Time Credential Setup

There's no setup wizard yet. Run this once from your venv to create a login:

```bash
./venv/bin/python -c "
from experimental.webui.auth import set_password
set_password('admin', 'your_password_here')
"
```

Credentials are hashed (PBKDF2-HMAC-SHA256, 600k iterations) and stored in `~/.dirracuda/conf/webui_creds.json` with permissions `0600`. Run the same command again to change a password or add more users.

---

## Starting the Server

```bash
./venv/bin/python -m experimental.webui.server
```

Default: binds to `127.0.0.1:5480`. Open `http://127.0.0.1:5480` and log in.

Optional flags:

| Flag | Default | Notes |
|------|---------|-------|
| `--host` | `127.0.0.1` | Override bind address |
| `--port` | `5480` | Override port |
| `--config` | auto | Path to a specific `webui.json` |

The desktop GUI also controls the service: `⚗ Experimental → Web UI` exposes `Manage Credentials`, `WebUI Config` (with `Save` and `Save & Restart`), and Start/Stop/Open Browser controls. No terminal required.

---

## What You Can Do

**Dashboard** — shows service mode/URL, queue state, and Shodan query-credit status. Balance lookup is server-side only (the API key is never exposed to the browser). Dashboard balance uses manual refresh plus a short 150-second server cache; failures are shown with sanitized reason labels only.

**Scans** — submit and cancel SMB, FTP, or HTTP discovery runs. One scan runs at a time (same FIFO queue as the desktop GUI); the web UI uses the same CLI subprocess boundary. Web UI SMB runs default to legacy mode (`--legacy`) so SMB1-capable targets are included. The scan form’s max-results value is passed through and enforced via per-task query-limit overrides. The optional probe toggle runs a protocol-aware post-scan probe pass for SMB/FTP/HTTP verified hosts. With explicit user opt-in, non-sensitive scan form toggles are remembered in this browser via `localStorage`.

**Results** — paginated host summaries per protocol with desktop-style search (case-insensitive substring match on IP address and accessible-shares text) plus row filters. Click any host row to expand an inline details accordion directly under that row: a compact overview appears first, and a nested **Show full details** toggle reveals a read-only scroll box (10 lines) with captured protocol-specific detail text and notes. Share names, accessible directory counts, and HTTP access details are shown when the relevant tables exist in the DB — older databases degrade cleanly without errors. The page updates on demand using **Refresh** (no automatic background refresh). With explicit user opt-in, non-sensitive results toggles and selected protocol are remembered in this browser via `localStorage`.

**Export** — creates a clean, defragmented SQLite copy (`VACUUM INTO`) and downloads it. Exports land in `~/.dirracuda/exports/` with a timestamped filename. The download endpoint only serves files matching that naming pattern.

**Configuration** — view and save web UI settings at `/config` without restarting. CSRF-protected.

**Not in v1:** file browser, DB import/merge, file manifests, API token auth. See [Known Limitations](#known-limitations).

---

## Localhost vs Remote Mode

### Localhost (default)

Binds to `127.0.0.1`. TLS is optional — the server starts plain HTTP when no cert is configured. This is the only mode that permits TLS disabled without an additional flag.

### Remote mode

Requires all three conditions in `~/.dirracuda/conf/webui.json`:

```json
{
  "bind_address": "0.0.0.0",
  "remote_enabled": true,
  "allowed_cidrs": ["10.0.0.0/8"],
  "tls": {
    "enabled": true,
    "cert_file": "/path/to/cert.pem",
    "key_file": "/path/to/key.pem"
  }
}
```

To run remote without TLS (not recommended), add `"allow_insecure_remote": true` inside the `tls` block. You'll get a startup warning.

**Startup hard-fails** on unsafe combinations: non-loopback bind without `remote_enabled`, empty `allowed_cidrs`, or TLS enabled without cert/key files. No silent fallback — if the config is wrong, the server exits with an explicit error.

---

## Configuration Reference

Config file: `~/.dirracuda/conf/webui.json`. Not created until you save from `/config` — safe defaults are used in the meantime.

| Key | Type | Default | Valid range |
|-----|------|---------|-------------|
| `bind_address` | string | `"127.0.0.1"` | Any valid IP |
| `port` | int | `5480` | 1–65535 |
| `remote_enabled` | bool | `false` | Required `true` for non-loopback bind |
| `allowed_cidrs` | list | `["127.0.0.1/32", "::1/128"]` | Required when `remote_enabled=true` |
| `session_timeout_idle` | int (sec) | `1800` | 300–14400 (5 min–4 hr) |
| `session_timeout_absolute` | int (sec) | `28800` | 3600–86400 (1–24 hr) |
| `tls.enabled` | bool | `true` | `false` allowed for localhost only |
| `tls.cert_file` | string | `""` | Required if TLS enabled |
| `tls.key_file` | string | `""` | Required if TLS enabled |
| `tls.allow_insecure_remote` | bool | `false` | Permits non-loopback HTTP |

---

## Security Considerations

**Sessions are in-memory.** Restarting the server logs everyone out. There's no persistent session store in v1.

**Preference persistence is browser-local only.** Optional UI preference memory uses `localStorage` keys (`dirracuda_pref_consent_v1`, `dirracuda_pref_data_v1`), not cookies. Only allowlisted non-sensitive toggles/selectors are stored, never credentials, CSRF/session tokens, or free-text filter fields. Users can enable/disable/clear from `/config`.

**TLS cert rotation requires a restart.** Cert and key files are read once at startup.

**The allowlist is IP-based.** No DNS resolution. On networks where source IPs can be spoofed, this is not a strong control on its own.

**Export files persist on disk.** `~/.dirracuda/exports/` accumulates full database copies until you clean them up manually.

**Password hashing uses PBKDF2-HMAC-SHA256** (stdlib, 600k iterations). Argon2id would be stronger but adds a dependency — tracked as a future option if the dep is acceptable.

**Run as a normal user, not root.** Config and credential files are `0600`; running as root negates that.

This is experimental software. Don't expose it to untrusted networks without reviewing the setup and understanding what you're opening up.

---

## Known Limitations

These are intentional v1 scope cuts, not bugs awaiting fixes:

- No in-browser file explorer or target file downloads
- No database import or merge from the web UI
- No API token authentication — session cookies only
- No systemd unit — process management is on you
- Single-task scan queue — one scan runs at a time
- Sessions lost on server restart — no persistent session store
- TLS cert rotation requires a restart
