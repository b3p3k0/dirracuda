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
   - CLI helper:

   ```bash
   ./venv/bin/python -c "
from experimental.webui.auth import set_password
set_password('admin', 'your_password_here')
"
   ```

2. Start server:

```bash
./venv/bin/python -m experimental.webui.server
```

3. Open `http://127.0.0.1:5480` and log in.

Default bind: `127.0.0.1:5480`.

### Optional startup flags

| Flag | Default | Notes |
|------|---------|-------|
| `--host` | from config (`127.0.0.1`) | Bind override, re-validated at startup |
| `--port` | from config (`5480`) | Port override, re-validated at startup |
| `--config` | default webui config path | Load/save a specific `webui.json` |

---

## Desktop Control Surface

From the desktop app: `Experimental -> Web UI`

Available controls:

- Service status (`Running` / `Stopped` / `Failed: ...`)
- Start / Stop / Open in Browser / Copy URL
- `Manage Credentials` dialog
- `WebUI Config` dialog with `Save` and `Save & Restart`

---

## Feature Overview

### Dashboard (`/dashboard`)

- Service mode and URL summary
- Queue snapshot (active + queued tasks)
- Shodan query-credit status

Shodan balance is fetched server-side only. The API key is never sent to the browser. Balance checks are manual-refresh with a 150-second server cache and sanitized failure reasons.

### Scans (`/scans`)

- Queue SMB / FTP / HTTP scan tasks (single active task, FIFO queue)
- Per-task max-results is enforced (`max_shodan_results`)
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
- Manual refresh model (`Refresh` button), no auto-refresh
- Pagination controls: First / Prev / Next / Last / Jump to
- Row click opens inline details accordion:
  - compact overview + read-only notes
  - nested full-details scrollbox (`Show full details + probe tree`)
  - includes stored probe snapshot tree when present
  - falls back gracefully on older DB schemas

### Export (`/api/export`)

- Creates a clean DB copy using `VACUUM INTO`
- Writes artifacts to `~/.dirracuda/exports/`
- Download endpoint serves only generated export filenames (allowlist + directory containment checks)

### Config (`/config`)

- Edit Web UI bind, remote mode, TLS, allowlist, and session timeouts
- Includes browser preference storage controls (enable / disable / clear)
- Config save is CSRF-protected
- **Changes take effect on restart**

---

## Configuration

Config file: `~/.dirracuda/conf/webui.json`

If the file is absent, safe in-memory defaults are used. The file is created when config is explicitly saved.

Key fields:

- `bind_address`, `port`
- `remote_enabled`
- `allowed_cidrs`
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

Startup fails fast on unsafe combinations (no silent downgrade).

### Allowlist behavior

Allowlist checks run as HTTP middleware only when `remote_enabled=true`.

The check uses `request.client.host` (TCP peer as seen by Uvicorn). If you run behind a reverse proxy, configure trusted forwarded headers correctly, or allowlist behavior will be wrong.

---

## Security Notes

- Credentials are hashed with PBKDF2-HMAC-SHA256 (600k iterations) and stored at `~/.dirracuda/conf/webui_creds.json` (`0600` permissions).
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
