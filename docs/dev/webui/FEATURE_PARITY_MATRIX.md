# Web UI Feature Parity Matrix (Desktop vs Web)

Last updated: 2026-05-20  
Branch snapshot: `development`

This is a practical parity map between the desktop app (`./dirracuda`) and the
current Web UI (`experimental/webui`). It is not a product vision doc; it is a
worklist for closing concrete behavior gaps.

## Legend

- `PARITY` = web behavior is functionally equivalent for normal operator use
- `PARTIAL` = web supports part of the workflow, but desktop still has important advantages
- `GAP` = desktop capability is missing in web today
- `N/A` = desktop-only by design or intentionally out of scope

## Matrix

| Area | Desktop Capability | Web UI Current State | Status | Evidence |
|---|---|---|---|---|
| Scan launch | Start SMB/FTP/HTTP scans from one dialog | `POST /api/scans` supports `protocol=smb|ftp|http` | PARITY | `README.md` Discovery; `experimental/webui/app.py` |
| Multi-protocol launch in one action | Queue multiple protocols from one submit | One protocol per request; can queue multiple tasks manually | PARTIAL | `README.md` Dashboard/Discovery; `experimental/webui/tasks.py` |
| Scan queueing | Queued task model with active task visibility | FIFO queue with one active scan, queued list, polling status, and `/scans` hydration from server queue snapshot after refresh/navigation | PARITY | `experimental/webui/tasks.py`; `experimental/webui/app.py`; `experimental/webui/static/scans.js` |
| Scan cancel | Cancel queued/running scan | `POST /api/scans/{id}/cancel` with queued/running handling | PARITY | `experimental/webui/app.py`; `experimental/webui/tasks.py` |
| Scan progress/logs | Live monitor windows and Running Tasks reopen | Polled task status + rolling logs (last 100 lines) | PARTIAL | `README.md` Running Tasks; `experimental/webui/tasks.py` |
| Shodan balance awareness | Preflight includes balance and estimated post-scan balance | `/scans` now uses mandatory preflight confirmation with credit estimate, live balance payload, post-scan estimate (when available), and fallback dashboard link | PARITY | `experimental/webui/app.py`; `experimental/webui/static/scans.js`; `README.md` |
| Query cap controls | Per-protocol max results in scan dialog | `max_shodan_results` per task (1..100000) | PARITY | `README.md` Shodan Credits; `experimental/webui/tasks.py` |
| Dork editing | Discovery Dorks editor + Dorkbook integration | No web dork editor / Dorkbook management | GAP | `README.md` Discovery + Dorkbook; `experimental/webui/README.md` |
| Post-scan probe hook | Optional post-scan probe from scan flow | `run_probe_after_scan` for SMB/FTP/HTTP | PARITY | `README.md` Web UI section; `experimental/webui/tasks.py` |
| Post-scan extract hook | Optional post-scan extract from scan flow | No web extract action in scan flow | GAP | `README.md` Extracting Files; `experimental/webui/README.md` |
| Unified host results | SMB/FTP/HTTP host views | `ALL/SMB/FTP/HTTP` results with pagination/search | PARITY | `experimental/webui/README.md`; `experimental/webui/app.py` |
| Result filtering | Rich server-list filtering | Search + `shares_only` + `favorites_only` + `hide_avoid` | PARTIAL | `experimental/webui/app.py`; `README.md` Server List |
| Row detail drill-down | Desktop details and probe context | Inline accordion details + probe tree text when present | PARTIAL | `experimental/webui/README.md`; `experimental/webui/app.py` |
| Host flag mutation | Toggle favorite/avoid/compromised from server list | Inline row actions + current-page bulk toggles (`favorite`/`avoid`/`compromised`) with desktop-compromised semantics and partial-success API outcomes | PARITY | `experimental/webui/app.py`; `experimental/webui/db_actions.py`; `experimental/webui/static/results.js` |
| Manual host add/delete | Add record and delete selected rows | No web add/delete host operations | GAP | `README.md` Server List; `experimental/webui/app.py` |
| Probe selected host | Probe from server list row action | Inline row `Probe` action + current-page bulk `Probe Selected` with async polling and single-active-job guard | PARITY | `experimental/webui/app.py`; `experimental/webui/results_probe_actions.py`; `experimental/webui/static/results.js` |
| Browse shares/files | Read-only SMB/FTP/HTTP browser + file viewer | Not implemented in web | GAP | `README.md` Browsing Shares; `experimental/webui/README.md` |
| Browser downloads | Quarantine-routed downloads with safeguards | Not implemented in web | GAP | `README.md` Browsing Shares/ClamAV/tmpfs; `experimental/webui/README.md` |
| ClamAV post-processing | Optional scan/routing for extracted/downloaded files | No web extraction/download path to apply ClamAV | GAP | `README.md` ClamAV section |
| DB export | Export clean DB copy | `POST /api/export` + download endpoint | PARITY | `experimental/webui/app.py`; `experimental/webui/db.py` |
| DB import/merge | Import DB, merge DB, CSV import | Not implemented in web | GAP | `README.md` DB Tools + CSV Host Import |
| DB stats/maintenance | Stats, vacuum/integrity/purge maintenance | Not implemented in web | GAP | `README.md` DB Tools |
| Main app config editing | Desktop config editor for broad app settings | Web `/config` manages webui service/security config only | PARTIAL | `README.md` Configuration; `experimental/webui/app.py` |
| Web auth/session hardening | Desktop has no web login surface | Session auth, CSRF, lockout, password policy, security headers | N/A | `docs/TECHNICAL_REFERENCE.md` web security sections; `experimental/webui/app.py` |
| Credential rotation UX | Desktop Web UI tab supports credential management | Web `/account` supports authenticated password change | PARITY | `README.md` Web UI; `experimental/webui/app.py` |
| Experimental modules in UI | SearXNG, Reddit, Dorkbook, Keymaster tabs | No web pages for those modules | GAP | `README.md` Experimental Features; `experimental/webui/README.md` |
| Keyboard workflow parity | Desktop has broad keybindings and quickref | Web has basic browser-native navigation only | GAP | `README.md` Keyboard Shortcuts; `docs/KBD_QUICKREF.md` |

## Early Target Shortlist

These are the highest-leverage parity targets with low architectural risk:

1. **Host state actions in Results (`favorite` / `avoid` / `compromised`)**  
   Status: shipped in Target 1 (inline + current-page bulk toggles).

2. **Scan preflight parity on `/scans` (credit estimate + balance + confirmation)**  
   Status: shipped in Target 2 (mandatory preflight + explicit start confirmation).

3. **Row-level probe action from Results (protocol-aware, no browse/download)**  
   Status: shipped in Target 3 (row + bulk probe actions with async polling).

## Deferred For Later Waves

- In-browser SMB/FTP/HTTP browsing
- Target file downloads + quarantine/tmpfs/ClamAV flows
- DB import/merge/maintenance
- Full experimental-module parity (SearXNG/Reddit/Dorkbook/Keymaster)

## Sources Used

- `README.md`
- `docs/TECHNICAL_REFERENCE.md`
- `experimental/webui/README.md`
- `experimental/webui/app.py`
- `experimental/webui/tasks.py`
- `docs/KBD_QUICKREF.md`
- `docs/dev/webui/LESSONS_LEARNED.md`
