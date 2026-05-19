# Web UI Layout and Logic Flows for Dirracuda

This document proposes a first‑pass layout and the associated user flows for the optional Dirracuda web interface.  It builds upon the design and security plans and integrates feedback (single user, established frameworks, default binding to localhost, allow/deny list and one concurrent scan).

## High‑Level Layout

The UI should be clean, functional and familiar.  A vertical navigation bar on the left provides quick access to major sections.  The remainder of the page displays the active panel.  A simple monochrome wireframe of the concept is shown below:

![wireframe concept]({{file:file-FwiuBbtFawF2XAQS6rhpNg}})

### Navigation

* **Home / Dashboard** – an overview showing scan status, recent tasks and quick actions (start a new scan, view results, open config).  This page can also display a service monitor indicating whether the web daemon is running and links to documentation.
* **Scans** – a form to launch a new scan.  Users select protocol(s), configure Shodan filters, set concurrency, choose rescan options and submit.  A table below shows currently running and queued scans with progress bars.
* **Results** – a tabbed view listing discovered hosts by protocol.  Each entry shows the IP address, port, authentication method, the outcome of the probe (reachable or failed), the date/time of the scan and, where applicable, counts of enumerated shares or directories.  A *Copy URL* button allows users to copy the raw SMB/FTP/HTTP endpoint to the clipboard.  The first release intentionally omits share‑level browsing and offers no download functionality.
* **Config** – a small configuration panel for editing `config.json` fields relevant to the web service (e.g., bind address, port, allowed IPs, credentials), plus global Dirracuda settings (Shodan key, concurrency limits).  Sensitive fields should be masked in the UI.
* **Experimental (optional)** – placeholder for modules like dorkbook, SearXNG dorking or Reddit ingestion.  These can be hidden until implemented.

### Page Structure

Each page consists of:

1. **Header** – displays the page title and context‑specific actions.  For example, the scans page header could include a “Cancel Scan” button when viewing an active task.
2. **Main content** – forms, tables and detail panes appropriate to the section.  Tables are paginated and sortable; forms validate inputs before submission.
3. **Footer** – optionally displays status messages or version information.

### Status Monitor

A persistent status indicator (e.g., an icon in the nav bar) shows whether the web UI daemon is running.  When the GUI is open, it can poll `http://localhost:<port>/health` to update this indicator.  Users can start or stop the service from the desktop application’s experimental tab (as outlined in the design overview).  Within the web UI, the dashboard displays currently running scan tasks and their progress.

## Logic Flows

### Scan Launch Flow

1. **User opens Scans page** – the server sends the available default settings (protocol list, default filters, concurrency values) via `GET /scans/options`.
2. **User submits form** – the browser sends `POST /scans` with selected protocols and parameters.  Input is validated server‑side (types, ranges, dork syntax).  If invalid, the API returns a `400 Bad Request` with error details.
3. **Server initiates task** – the API delegates to the task manager, which queues the scan request.  Since only one scan runs at a time, if another scan is in progress, this task is queued.  The API responds with a 201 status and a task ID.
4. **Task execution** – when no other scan is writing to the database, the worker creates a new workflow instance (e.g., `UnifiedWorkflow`) and begins the discovery→authentication→enumeration process【479495022112005†L94-L110】.  Progress events are stored in memory and optionally persisted.
5. **Progress updates** – the UI subscribes to a WebSocket (`/ws/scans/{id}`) for real‑time updates.  Alternatively, it polls `GET /scans/{id}` at an interval.  The status bar and progress bars update accordingly.
6. **Task completion** – once enumeration is complete, the worker writes final results to the SQLite database and marks the task finished.  The UI reflects the state change and may offer a link to the newly discovered servers.
7. **Error handling** – if the scan fails (e.g., Shodan API error or network failure), the error is recorded and returned via the task status.  The UI displays an appropriate message.

### Results Summary Flow

1. **User navigates to Results page** – the client requests `GET /hosts/{protocol}` (e.g., `/hosts/smb` or `/hosts/ftp`).  The API returns a paginated list of discovered endpoints with columns such as IP, port, authentication method, probe outcome (success or failure), date/time of scan and counts of enumerated shares or directories.  The UI displays this list in a responsive table or card view, with options to filter or sort.
2. **Copying endpoints** – each entry includes a “Copy URL” button that copies the raw network path (e.g., `smb://ip/` or `ftp://ip/`) to the clipboard.  Users can paste these into other tools or scripts.  No further drill‑down is offered in this release.
3. **Aggregated metrics** – the results page can display summary statistics (e.g., total hosts, new vs. previously seen, number of reachable hosts) to help users gauge scanning impact.  Detailed browsing of shares or files and any download actions are deferred to a future version.

### Config Update Flow

1. **User opens Config page** – the client requests `GET /config` to fetch current settings.  Sensitive values are redacted.
2. **User edits fields** – the UI validates inputs (e.g., IP addresses, port ranges) client‑side.
3. **User saves** – the client sends `POST /config` with updated values.  The server validates again, writes the new settings atomically to `~/.dirracuda/conf/config.json` and, if necessary, restarts subsystems (e.g., rebinds the server).
4. **Feedback** – the UI displays a success message and reloads settings.  If the binding address or port changed, the user may need to reconnect.

## Additional Considerations

* **Framework choice** – Use FastAPI running under Uvicorn for stable, well‑maintained support.  It is widely adopted and provides built‑in request validation and security features.  Alternatives like Flask are also viable but may require extra boilerplate for async tasks and automatic API docs.
* **Library reuse** – Leverage existing Dirracuda modules for scanning and database interactions to avoid duplicating logic or compromising security.  Use `shared` workflow classes for SMB/FTP/HTTP scanning【479495022112005†L29-L67】.
* **Monitor integration** – Extend the desktop GUI’s experimental tab to include a *Web UI* section that shows a status indicator (running/stopped), a button to start/stop the service and a link to open the web interface.  The status indicator can query a `/health` endpoint that returns a heartbeat.
* **Extensibility** – The modular design allows new pages or panels to be added for future features (e.g., Keymaster, Dorkbook).  Keep navigation extensible by using a configurable list of routes.
* **Responsive mobile design** – Ensure that tables and navigation adapt gracefully to small screens.  For example, reflow result lists into card layouts, collapse the navigation bar into a hamburger menu and enlarge touch targets on phones.  Avoid complex layouts that could break on narrow devices.
* **Accessibility** – Target compliance with the Web Content Accessibility Guidelines (WCAG) 2.1 Level AA.  Use semantic HTML, ensure sufficient colour contrast, support keyboard navigation and provide appropriate ARIA labels so that users relying on assistive technologies can effectively navigate the web interface.

This layout and the associated flows provide a foundation for building the initial web interface.  They emphasise clarity, security and parity with existing Dirracuda functionality while remaining adaptable to future enhancements.