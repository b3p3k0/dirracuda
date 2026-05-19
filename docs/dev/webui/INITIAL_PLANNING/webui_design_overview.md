# Web UI Design Overview for Dirracuda

## Purpose

Dirracuda currently offers a command‑line interface and a Tkinter GUI to discover and audit open directories over SMB, FTP and HTTP【485255009257124†L418-L427】.  This document proposes an **optional** web‑based interface that can be enabled by users who prefer browser access or remote dashboards.  The primary objectives are:

* **Focus on essential features first** – the initial web UI will support launching scans, monitoring progress, exporting the database and adjusting a small subset of configuration options.  Browsing shares/files or importing databases are intentionally deferred to keep the first release scoped and safe.  The desktop GUI remains the authoritative interface for advanced operations.
* **Reuse existing code paths** – rather than re‑implementing scanning logic, the web service will call into the same `shared` workflows and CLI commands used by the GUI.  This leverages Dirracuda’s modular architecture where the CLI layer invokes a workflow layer, which in turn orchestrates command modules and persists to SQLite【479495022112005†L29-L67】.
* **Minimal footprint** – the daemon should run as a separate process with no GUI dependencies.  It will be packaged alongside the main application but disabled by default.  Systemd (or an equivalent init system) will manage its lifecycle so that the GUI can start/stop the service.
* **Security** – authentication and network hardening are mandatory.  The server must default to listening only on `localhost` and require explicit credentials.  See the companion security specification for details.

## User Flows and Features

### Launching Scans

* **Discovery and access** – Users can select protocol(s), specify Shodan filters and concurrency settings, then launch a scan.  The web UI will invoke the appropriate workflow (e.g., `UnifiedWorkflow` for SMB) and stream progress.  The core pipeline remains: Shodan query → host filtering → port check → authentication → share/dir enumeration → persistence【479495022112005†L94-L110】.
* **Rescan controls** – Options for `rescan_all`, `rescan_failed` and `rescan_after_days` will be exposed to match existing CLI flags.
* **Scan budget and rate limits** – Expose the same dork editing and Shodan credit controls available in the GUI.  Users should see an estimated credit cost before launching a scan【485255009257124†L455-L462】.

### Monitoring and Results

* **Task dashboard** – Display active and queued scans with protocol and progress indicators.  Tasks run asynchronously in background threads or processes.  Since only one scan can write to the database at a time, additional scans are queued.
* **Results summary** – For completed scans, present a concise, read‑only summary of discovered hosts.  Each entry should include key metadata such as the protocol, IP and port, authentication method used, whether the probe succeeded or failed, the date/time of the scan and counts of enumerated shares or directories.  Offer a *Copy URL* button that copies the raw SMB/FTP/HTTP endpoint to the clipboard for use in other tools.  Detailed browsing of shares and files, as well as in‑browser downloads, remains deferred.
* **Database tools** – Allow users to export the SQLite database for offline analysis or backup.  Importing or merging databases is deferred; the installation script continues to support initial imports as needed.
* **Configuration editor** – Present only a limited set of settings in the first release: network binding (address and port), allowed/denied IP ranges, authentication credentials, concurrency limits and default Shodan query limits.  More advanced configuration remains in the desktop GUI.
* **Experimental features** – Keep experimental modules hidden until the core web UI is stable.  They can be added under an “Experimental” section in later versions.

### System Integration

* **Service management** – Add a control under the GUI’s *Experimental* tab to start, stop or check the status of the web service.  This control will call systemd (or `subprocess`) to manage the daemon.  When started, the button could change to “Open Web UI,” launching the browser at `http://localhost:<port>`.
* **Shared database access** – The web service and GUI will operate on the same SQLite database (`dirracuda.db`)【479495022112005†L64-L67】.  To avoid write‑conflicts, scanning tasks should be serialized or coordinated via a shared lock.  See the concurrency section.
* **Packaging** – Bundle the daemon script (e.g., `webserver.py`) and a systemd unit file.  Installation will copy these into appropriate directories and register but not enable the service.

## Architecture

### Server Framework

* **FastAPI** is recommended due to its async support, automatic validation and OpenAPI documentation.  It can efficiently handle multiple concurrent requests and WebSocket streams.  Flask is a viable alternative but would require extra work for async tasks and docs.
* **Uvicorn/Gunicorn** as the ASGI server.  For a simple local deployment, a single Uvicorn worker is sufficient.  Gunicorn with Uvicorn workers can scale if the UI is exposed on an internal network.
* **Templates and static files** – Use a minimal templating engine (e.g., Jinja2) and a small bundle of static HTML/CSS/JS.  Avoid flashy effects; prefer tables and forms with simple styling.  Iframes should only be used for isolating untrusted content (e.g., file previews); otherwise, route navigation through standard endpoints.

### Application Layer

* **API layer** – Define REST endpoints and, where appropriate, WebSocket channels:
  * `POST /scans` – launch a new scan with parameters (protocols, filters, concurrency, rescan options).  Returns a task ID.
  * `GET /scans/{id}` – return status and progress for a scan.  When the scan completes, provide a summary of host counts and a list of endpoints for copy/paste.
  * `GET /hosts/{protocol}` – list discovered hosts by protocol in a condensed form (IP, port, timestamp).  This endpoint does not reveal share or file details.
  * `POST /export` – trigger a database export.  Returns a download token or direct link for retrieving the SQLite file.  Import endpoints are deferred.
  * `POST /config` and `GET /config` – update or fetch configuration values.  Only a limited subset of settings is editable in the web UI.
  * Additional endpoints for editing default dorks and, later, experimental modules.
* **Task manager** – Use Python’s `concurrent.futures` or `asyncio` to run scanning workflows in the background.  Each task uses the same functions called by the CLI; output is captured and written to the database.  Task state is stored in an in‑memory registry and persisted to the database when complete.  If a task fails, the error is recorded and returned by the API.
* **Database layer** – Implement a wrapper around SQLite with connection pooling.  Enable Write‑Ahead Logging (WAL) and set a high timeout to mitigate `database is locked` errors【672418071378949†L57-L63】.  Use one connection per task; avoid sharing connections across threads.  For safety, only one scan should write to the database at a time; concurrent scans can be queued.
* **Configuration handler** – Read `config.json` at startup and watch for changes.  Provide an API to update config values with validation (type checking, range enforcement).  Ensure that sensitive fields (e.g., API keys) are masked in the UI.

### Front‑End

* **Navigation** – Provide a compact navigation bar that adapts to the device.  On desktops it can appear vertically; on phones it collapses into a hamburger or bottom bar.  Pages include *Home*, *Scans*, *Results* and *Config*.  Experimental modules remain hidden until implemented.
* **Tables and forms** – Use responsive tables that reflow into card lists on small screens.  Provide touch‑friendly controls and validate inputs client‑side before submission.
* **Streaming updates** – Use WebSockets or Server‑Sent Events for real‑time progress; fall back to lightweight polling on devices or browsers that do not support these features.
* **Responsive design** – Apply CSS media queries or a lightweight framework such as Tailwind with responsive utilities to ensure the UI adapts gracefully to mobile devices.  Avoid heavy animations or large JavaScript bundles.

## Concurrency and Synchronisation

SQLite allows multiple readers but only one writer at a time【672418071378949†L57-L63】.  To prevent lock contention:

* **Serialise scan writes** – Maintain a mutex around database write operations or queue scans so that only one run writes at a time.  Reads (e.g., listing host summaries) can proceed concurrently.
* **Use WAL mode** – Enable Write‑Ahead Logging to allow readers during writes.  Set `timeout` to a sensible value (e.g., 30s) to minimise `database is locked` errors.
* **Connection management** – Open new connections per thread/process and close them promptly.  Do not share cursors across threads.  Consider migrating to a client–server database (e.g., PostgreSQL) if heavy concurrency becomes a requirement.

## Deployment Considerations

* **Default binding** – The daemon should listen on `127.0.0.1:<port>` by default.  The port is configurable via `config.json`.  Exposing it to other interfaces requires explicit configuration and should trigger a strong warning.
* **Systemd unit** – Provide a unit file such as:
  ```ini
  [Unit]
  Description=Dirracuda Web UI
  After=network.target

  [Service]
  ExecStart=/usr/bin/python3 -m dirracuda.webserver --port=5480
  Restart=on-failure
  User=dirracuda
  Group=dirracuda
  ReadWritePaths=%h/.dirracuda

  [Install]
  WantedBy=multi-user.target
  ```
  Users can override the port and other options via a drop‑in file.
* **Packaging** – Distribute the web server code within the Dirracuda repository, perhaps under `web/`.  Update the installer to optionally register the systemd service and copy static assets.
* **Cross‑platform** – On systems without systemd (e.g., Windows or macOS), provide a simple CLI command (`dirracuda-webui --start`) that spawns the server and logs to stdout.  The GUI can still start/stop it via subprocess calls.
* **Dedicated user and group** – Run the web service under a dedicated system account (e.g., `dirracuda`) with its own group.  The `.dirracuda` directories should remain owned by the primary user (e.g., `kevin`) with group `dirracuda` and restrictive permissions (e.g., `770` or `750`).  The service user is a member of the `dirracuda` group, giving it access to these directories without granting it access to other files in the user’s home.  Do not add the service user to the primary user’s group or vice versa.

## Future Enhancements

* **Role‑based access control** – Introduce user accounts with roles (e.g., admin, read‑only) if multi‑user deployments emerge.
* **Remote dashboards** – Optionally expose the UI over SSH tunnels or VPN for remote monitoring.  This requires hardened network and authentication settings.
* **API integration** – Publish an official API schema (OpenAPI/Swagger) so that other tools (e.g., CLI scripts) can integrate.  Automated tests can be written against this API to ensure stability.
* **Protocol extensibility** – When new protocols are added to Dirracuda, update the web UI to reflect them.  The workflow architecture already supports adding new protocol handlers【479495022112005†L1423-L1453】.

This design should offer a solid foundation for developing a secure, user‑friendly web interface that complements the existing Dirracuda CLI and GUI.

## Upgrade and Migration Strategy

Implementing a long‑lived web service means handling changes to the database schema and configuration over time.  To ensure a smooth upgrade path:

* **Schema versioning** – Store a `schema_version` value in the SQLite database (e.g., via a dedicated table or SQLite’s `PRAGMA user_version`).  Each release that modifies the schema should increment this value and provide a migration script to bring older databases up to date.  Migrations can be managed with a tool such as Alembic or via custom SQL executed at startup.  Always back up the database before applying a migration.
* **Config versioning** – Include a `config_version` field in `config.json` and write parsers that gracefully handle unknown fields by ignoring them or using default values.  When adding new settings, ensure older versions of the UI and CLI can still parse the configuration.
* **Migration tool** – Provide a CLI command (e.g., `dirracuda migrate`) or integrate migrations into the service start‑up.  This tool should detect the current schema and config versions, summarise pending changes and request confirmation before altering data.  Minor migrations may run automatically, while major migrations could require a manual step so users can take backups.
* **Backward compatibility** – Favour additive changes to the schema and API; avoid removing columns or changing field types unless absolutely necessary.  Maintain compatibility views or layers for old clients and deprecate features gradually, documenting removals in release notes.
* **Testing and rollback** – Maintain unit and integration tests for each migration script.  If a migration fails, provide a rollback path by preserving the original database and configuration.  Do not modify data in place until the migration completes successfully.

By planning for migrations up front, the project can evolve without leaving users stranded on old versions or risking data loss during upgrades.