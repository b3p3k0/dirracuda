# Web UI Architecture

## Shape

The web UI is a separate local web service plus a small desktop control surface.

```text
Tk Dashboard
  -> ExperimentalFeaturesDialog
     -> Web UI tab
        -> Web UI Control dialog
           -> start/stop/status/open browser

Browser
  -> FastAPI app
     -> auth/session middleware
     -> task manager
        -> CLI subprocess: cli/smbseek.py, cli/ftpseek.py, cli/httpseek.py
     -> DB reader/export helpers
        -> dirracuda.db
```

The desktop GUI remains the normal workstation interface. The web UI is for
lightweight control and monitoring.

## Proposed Code Layout

Implementation cards may adjust exact names after reading local patterns, but
the target shape is:

```text
experimental/webui/
  __init__.py
  app.py                  # FastAPI app factory
  auth.py                 # credential verification, sessions, CSRF
  config.py               # webui.json load/validate/write
  server.py               # python -m experimental.webui.server entrypoint
  service_control.py      # health/pidfile/systemd control helpers
  tasks.py                # scan queue + subprocess lifecycle
  db.py                   # read-only host summaries + export
  schemas.py              # request/response models
  templates/
    login.html
    dashboard.html
    scans.html
    results.html
    config.html
  static/
    webui.css
    webui.js

gui/components/
  webui_control_dialog.py

gui/components/experimental_features/
  webui_tab.py
```

Keep generated/static assets small. No bundler in v1.

## Entrypoints

- Desktop GUI: `./dirracuda`
- Web service module: `./venv/bin/python -m experimental.webui.server`
- Future convenience script, if wanted: `./dirracuda-webui`

Do not make `gui/main.py` runnable again. It is compatibility-only.

## Scan Task Model

The web server owns an in-memory queue:

- `queued`
- `running`
- `finished`
- `failed`
- `cancelled`

Only one scan subprocess may be `running`.

Each task records:

- task id
- protocol list
- request summary
- status
- progress counters when parsed from stdout
- start/end timestamps
- subprocess pid while running
- last safe user-facing message
- full internal error in restricted log only

Use a lock around task registry mutation. Do not share SQLite connections across
worker threads.

## CLI Boundary

Build commands as lists:

```text
./venv/bin/python cli/smbseek.py --country US --max-results 100
```

Never build shell strings. Never pass user text to `shell=True`.

The first implementation can parse the same progress lines the desktop GUI
already expects. A later card can add `--json-events` to the CLI if text parsing
becomes brittle.

## Database Access

The web UI reads from the same main SQLite DB as the desktop GUI.

Rules:

- Use one SQLite connection per operation.
- Use parameterized queries.
- Keep result endpoints read-only.
- Use WAL where safe and compatible with existing DB code.
- Queue scan writers so web-launched scans do not fight each other.
- Do not assume modern schema. Inspect tables/columns before optional reads.

The export path should use existing DB tools patterns where possible. If a new
helper is needed, keep it narrow: source DB path in, export artifact path out.

## Config Files

New config files:

```text
~/.dirracuda/conf.d/experimental/webui.json
~/.dirracuda/conf/webui_creds.json
```

`webui.json` stores web service configuration. It should not absorb unrelated
Dirracuda config fields.

`webui_creds.json` stores only web UI credential metadata. Keep it mode `0600`.

Writes must be atomic:

1. validate new payload
2. write temp file in same directory
3. fsync where practical
4. replace
5. chmod

## Desktop Web UI Tab Controls

Operational controls live inline in the Experimental -> Web UI tab.

Inline control responsibilities:

- show configured URL
- show health check result
- start service through the configured controller
- stop service across desktop app restarts when ownership is verifiable
- open browser
- link to docs/manual steps for systemd mode

The controller must not rely only on in-memory Tk state. A usable v1 needs to:

- check `/health` first
- support a persistent pid file for manual/local subprocess mode
- validate that a pid still belongs to the expected web UI command before
  stopping it
- support systemd user/service status when that mode is configured
- leave a clear "manual stop required" message if state is ambiguous

If systemd support lands in v1, keep it explicit and conservative. The control
surface should show what it will run before it runs it.

Launch contract: use module execution (`python -m experimental.webui.server`)
instead of script-path execution, so imports of `experimental.webui.*` resolve
correctly after package relocation.

## Frontend

Use server-rendered pages and light JavaScript:

- No SPA framework.
- No build step.
- Login can submit JSON with a small script to avoid adding a form parser
  dependency in v1.
- Tables remain dense and practical.
- Mobile support is a v1 requirement, not polish. Use a viewport meta tag,
  mobile-first CSS, and media queries. Results and task tables should reflow into
  compact cards on phone-width screens.
- Target WCAG 2.2 AA where practical: semantic forms, focus states, keyboard
  navigation, sufficient contrast, and status text that screen readers can read.
