# Web UI ASCII Sketches

These sketches are not pixel specs. They pin down layout intent so Claude does
not improvise a new product.

## Desktop Experimental Dialog

Existing dialog shell stays tabbed.

```text
Experimental Features
+------------------------------------------------------------+
| These features are experimental and may be unstable...     |
| [ ] Don't show this notice again                           |
+---------+--------+--------+----------+-----------+---------+
| SearXNG | Reddit | Web UI | Dorkbook | Keymaster |         |
+---------+--------+--------+----------+-----------+---------+
| Web UI                                                     |
|                                                            |
| Browser-based control panel for launching scans, viewing   |
| summaries, exporting the database, and managing the web    |
| service. Disabled by default. TLS is on by default; remote |
| access requires an allowlist.                              |
|                                                            |
| [Open Web UI Control]                                      |
|                                                            |
|                                                [Close]     |
+------------------------------------------------------------+
```

Tab order is deliberate: `SearXNG`, `Reddit`, `Web UI`, `Dorkbook`,
`Keymaster`.

## Web UI Control Dialog

Launched by the single button in the tab.

```text
Web UI Control
+------------------------------------------------------------+
| Status: stopped                                            |
| URL:    http://127.0.0.1:5480                              |
| Mode:   localhost only                                     |
|                                                            |
| [Start] [Stop] [Restart] [Open Browser] [Copy URL]         |
|                                                            |
| Remote access is disabled. TLS is on by default. Disable  |
| it only on purpose, and only on networks you trust.        |
|                                                            |
|                                                [Close]     |
+------------------------------------------------------------+
```

If the service is managed by systemd, the status line should say that clearly.
If the dialog only controls a subprocess started by the desktop app, say that
too. Ambiguous service state is not helpful.

## Web Login

```text
Dirracuda Web UI
+--------------------------------------+
| Username                             |
| [admin_____________________________] |
| Password                             |
| [__________________________________] |
|                                      |
| [Sign in]                            |
+--------------------------------------+
```

No marketing hero. No decorative dashboard before auth.

## Web Dashboard

```text
Dirracuda                                  admin | Logout
+------------+-----------------------------------------------+
| Dashboard  | Service                                       |
| Scans      |   Localhost only  http://127.0.0.1:5480       |
| Results    |                                               |
| Export     | Active Scan                                   |
| Config     |   none                                        |
|            |                                               |
|            | Recent Tasks                                  |
|            | +----------+----------+----------+----------+ |
|            | | Time     | Protocol | Status   | Result   | |
|            | +----------+----------+----------+----------+ |
+------------+-----------------------------------------------+
```

Left nav is fine inside the new web app. It is not fine as a replacement for the
desktop Experimental dialog.

## Scan Page

```text
Scans
+------------------------------------------------------------+
| Protocols  [x] SMB  [x] FTP  [x] HTTP                     |
| Country    [US________________]                            |
| Max results [100____]  Rescan [ ] all  [ ] failed          |
| [x] Run probe on verified hosts after scan                 |
|                                                            |
| Estimated Shodan cost: shown when available                |
|                                                            |
| [Queue Scan]                                               |
+------------------------------------------------------------+
| Queue                                                      |
| +------+----------+----------+--------------+------------+ |
| | ID   | Protocol | State    | Progress     | Action     | |
| | 42   | SMB      | running  | 35 / 100     | [Cancel]   | |
| +------+----------+----------+--------------+------------+ |
+------------------------------------------------------------+
```

## Results Page

```text
Results
+--------+--------+--------+--------------------------------+
| SMB    | FTP    | HTTP   |                                |
+--------+--------+--------+--------------------------------+
| Filter [____________________]                              |
| +-----------+------+-------+--------+---------+----------+ |
| | Host      | Port | Auth  | Shares | Probe   | Action   | |
| | 10.0.0.10 | 445  | anon  | 4      | current | [Copy]   | |
| |           |      |       |        |         | [Probe]  | |
| +-----------+------+-------+--------+---------+----------+ |
| Selected host share summary:                               |
|   public/  readable  128 files                             |
|   media/   readable  probe pending                         |
+------------------------------------------------------------+
```

Share/directory summaries are in scope. The browser file explorer and file
downloads are not.

## Config Page

```text
Config
+------------------------------------------------------------+
| Bind address [127.0.0.1_________]  Port [5480____]         |
| Remote access [ ] enabled                                  |
| TLS         [x] enabled   [ ] allow insecure remote override|
| TLS cert     [____________________]                        |
| TLS key      [____________________]                        |
| Allowlist    [127.0.0.1/32, ::1/128____________________]   |
| Idle timeout minutes [30____]  Absolute hours [8____]      |
|                                                            |
| [Save]                                                     |
+------------------------------------------------------------+
```

When remote access is enabled, show a warning near the control that changed it.
Do not bury the warning in docs only.

## Mobile Results Reflow

```text
Results
+------------------------------+
| SMB | FTP | HTTP             |
+------------------------------+
| 10.0.0.10:445                |
| Auth: anonymous              |
| Shares: 4                    |
| Probe: current               |
| Last seen: 2026-05-09        |
| [Copy Endpoint] [Probe]      |
+------------------------------+
| 10.0.0.11:21                 |
| Auth: anonymous              |
| Directories: probe pending   |
| Last seen: 2026-05-09        |
| [Copy Endpoint] [Probe]      |
+------------------------------+
```
