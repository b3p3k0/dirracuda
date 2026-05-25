# Web UI Feature Parity Matrix (Desktop vs Web)

Last updated: 2026-05-22  
Branch snapshot: `development`

This is a practical parity map between the desktop app (`./dirracuda`) and the
current Web UI (`experimental/webui`). It is not a product vision doc; it is a
worklist for closing concrete behavior gaps.

## Legend

- `PARITY` = web behavior is functionally equivalent for normal operator use
- `PARTIAL` = web supports part of the workflow, but desktop still has important advantages
- `GAP` = desktop capability is missing in web today
- `N/A` = desktop-only by design or intentionally out of scope

---

## Gaps

Capabilities missing entirely from the web UI, highest-leverage first.

| Area | Desktop Capability | Web UI Current State | Status | Evidence |
|---|---|---|---|---|
| Browse shares/files | Read-only SMB/FTP/HTTP browser + file viewer | Not implemented in web | GAP | `README.md` Browsing Shares; `experimental/webui/README.md` |
| Browser downloads | Quarantine-routed downloads with safeguards | Not implemented in web | GAP | `README.md` Browsing Shares/ClamAV/tmpfs; `experimental/webui/README.md` |
| DB import/merge | Import DB, merge DB, CSV import | Not implemented in web | GAP | `README.md` DB Tools + CSV Host Import |
| DB stats/maintenance | Stats, vacuum/integrity/purge maintenance | Not implemented in web | GAP | `README.md` DB Tools |
| Manual host add/delete | Add record and delete selected rows | No web add/delete host operations | GAP | `README.md` Server List; `experimental/webui/app.py` |
| Post-scan extract hook | Optional post-scan extract from scan flow | No web extract action in scan flow | GAP | `README.md` Extracting Files; `experimental/webui/README.md` |
| Dork editing | Discovery Dorks editor + Dorkbook integration | No web dork editor or Dorkbook management | GAP | `README.md` Discovery + Dorkbook; `experimental/webui/README.md` |
| Experimental modules in UI | SearXNG, Reddit, Dorkbook, Keymaster tabs | No web pages for those modules | GAP | `README.md` Experimental Features; `experimental/webui/README.md` |
| ClamAV post-processing | Optional scan/routing for extracted/downloaded files | No web extraction/download path to apply ClamAV | GAP | `README.md` ClamAV section |
| Keyboard workflow parity | Desktop has broad keybindings and quickref | Web has basic browser-native navigation only | GAP | `README.md` Keyboard Shortcuts; `docs/KBD_QUICKREF.md` |

---

## Partials

Capabilities where the web UI has meaningful coverage but the desktop still has important advantages.

| Area | Desktop Capability | Web UI Current State | Status | Evidence |
|---|---|---|---|---|
| Scan progress/logs | Live monitor windows and Running Tasks reopen | Polled task status + rolling logs (last 100 lines); no streaming, no persistent reopen | PARTIAL | `README.md` Running Tasks; `experimental/webui/tasks.py` |
| Main app config editing | Desktop config editor for broad app settings | Web `/config` manages webui service/security config only; main app settings (Shodan key, concurrency, timeouts) not editable | PARTIAL | `README.md` Configuration; `experimental/webui/app.py` |
| Result filtering | Rich server-list filtering with sortable columns | Search + `shares_only` + `favorites_only` + `hide_avoid`; results fixed to `last_seen DESC` order, no column sort | PARTIAL | `experimental/webui/app.py`; `README.md` Server List |
| Row detail drill-down | Desktop details and probe context panels | Inline accordion + probe tree text + `Open with system` handoff (explicit non-root probe base path when known, else root `/`); less structured than desktop detail pane | PARTIAL | `experimental/webui/README.md`; `experimental/webui/app.py`; `experimental/webui/static/results.js` |
| Multi-protocol launch in one action | Queue multiple protocols from one submit | Protocol checkboxes + `_queueTasksForPlan()` auto-queues one task per protocol in a single user action; tasks are separate queue entries, not a unified session | PARTIAL | `README.md` Dashboard/Discovery; `experimental/webui/tasks.py`; `experimental/webui/static/scans.js` |

---

## Parity

Capabilities where the web UI is functionally equivalent for normal operator use.

| Area | Desktop Capability | Web UI Current State | Status | Evidence |
|---|---|---|---|---|
| Scan launch | Start SMB/FTP/HTTP scans from one dialog | `POST /api/scans` supports `protocol=smb\|ftp\|http` | PARITY | `README.md` Discovery; `experimental/webui/app.py` |
| Scan queueing | Queued task model with active task visibility | FIFO queue with one active scan, queued list, polling status, `/scans` hydration from server queue snapshot after refresh/navigation, and shared scan/results DB path resolution from main config | PARITY | `experimental/webui/tasks.py`; `experimental/webui/app.py`; `experimental/webui/static/scans.js` |
| Scan cancel | Cancel queued/running scan | `POST /api/scans/{id}/cancel` with queued/running handling | PARITY | `experimental/webui/app.py`; `experimental/webui/tasks.py` |
| Shodan balance awareness | Preflight includes balance and estimated post-scan balance | Mandatory preflight confirmation with credit estimate, live balance payload, post-scan estimate (when available), and fallback dashboard link | PARITY | `experimental/webui/app.py`; `experimental/webui/static/scans.js`; `README.md` |
| Query cap controls | Per-protocol max results in scan dialog | `max_shodan_results` per task (1..100000) | PARITY | `README.md` Shodan Credits; `experimental/webui/tasks.py` |
| Post-scan probe hook | Optional post-scan probe from scan flow | `run_probe_after_scan` for SMB/FTP/HTTP | PARITY | `README.md` Web UI section; `experimental/webui/tasks.py` |
| Unified host results | SMB/FTP/HTTP host views | `ALL/SMB/FTP/HTTP` results with pagination/search | PARITY | `experimental/webui/README.md`; `experimental/webui/app.py` |
| Host flag mutation | Toggle favorite/avoid/compromised from server list | Inline row actions + current-page bulk toggles (`favorite`/`avoid`/`compromised`) with desktop-compromised semantics and partial-success API outcomes | PARITY | `experimental/webui/app.py`; `experimental/webui/db_actions.py`; `experimental/webui/static/results.js` |
| Probe selected host | Probe from server list row action | Inline row `Probe` action + current-page bulk `Probe Selected` with async polling and single-active-job guard | PARITY | `experimental/webui/app.py`; `experimental/webui/results_probe_actions.py`; `experimental/webui/static/results.js` |
| DB export | Export clean DB copy | `POST /api/export` + download endpoint | PARITY | `experimental/webui/app.py`; `experimental/webui/db.py` |
| Credential rotation UX | Desktop Web UI tab supports credential management | Web `/account` supports authenticated password change | PARITY | `README.md` Web UI; `experimental/webui/app.py` |

---

## Out of Scope

| Area | Desktop Capability | Web UI Current State | Status | Evidence |
|---|---|---|---|---|
| Web auth/session hardening | Desktop has no web login surface | Session auth, CSRF, lockout, password policy, security headers | N/A | `docs/TECHNICAL_REFERENCE.md` web security sections; `experimental/webui/app.py` |

---

## Shipped Targets

1. **Host state actions in Results (`favorite` / `avoid` / `compromised`)**  
   Shipped: inline row + current-page bulk toggles.

2. **Scan preflight parity on `/scans` (credit estimate + balance + confirmation)**  
   Shipped: mandatory preflight + explicit start confirmation.

3. **Row-level probe action from Results (protocol-aware, no browse/download)**  
   Shipped: row + bulk probe actions with async polling.

4. **Results detail external-open path selection (root vs non-root intent)**  
   Shipped: explicit probe base-path precedence for HTTP/FTP, root fallback for ambiguous listings.

## Next Target Candidates

Ordered by leverage vs. architectural complexity, drawn from the Partials and upper Gaps:

1. **Scan progress/logs** — add log streaming or a scrollable live log panel; highest operator-visibility gap in the current Partials.
2. **Result column sorting** — server-side `ORDER BY` parameter closes the most visible filtering gap.
3. **DB stats/maintenance** — read-only views (stats, integrity check, vacuum trigger); no browse/download risk; self-contained.
4. **Manual host add/delete** — CRUD on results rows; moderate complexity, high daily-use value.
5. **Main app config editing** — expand `/config` to surface Shodan key, concurrency, and timeout settings from `SMBSeekConfig`.

## Deferred For Later Waves

- In-browser SMB/FTP/HTTP browsing
- Target file downloads + quarantine/tmpfs/ClamAV flows
- DB import/merge
- Full experimental-module parity (SearXNG/Reddit/Dorkbook/Keymaster)
- Dork editor / Dorkbook management

## Sources Used

- `README.md`
- `docs/TECHNICAL_REFERENCE.md`
- `experimental/webui/README.md`
- `experimental/webui/app.py`
- `experimental/webui/tasks.py`
- `experimental/webui/static/scans.js`
- `experimental/webui/static/results.js`
- `docs/KBD_QUICKREF.md`
- `docs/dev/webui/LESSONS_LEARNED.md`

---

## Active Wave Focus (C29–C35)

| Card | Objective | Status | Commit(s) | Date |
|------|-----------|--------|-----------|------|
| C29 | IA/nav cutover + `/export` page + root route hard-cut | SHIPPED | 23faba4, 5702957 | 2026-05-24 |
| C30 | Shared queue model for runs + probes | SHIPPED | 5702957 | 2026-05-24 |
| C31 | SearXNG web flow | SHIPPED | 8bbdebb | 2026-05-24 |
| C32 | Reddit web flow | SHIPPED | 3c6d1a4 | 2026-05-24 |
| C33 | Dorkbook immediate-persist parity | SHIPPED | 2caadd1 | 2026-05-24 |
| C34 | Keymaster unlock/manage/apply web MVP | SHIPPED | b890903 | 2026-05-24 |
| C35 | Docs/lessons/parity/regression closeout | IN PROGRESS | — | 2026-05-24 |

### Wave Constraints

- Shared queue includes runs and probes only (no promotions).
- Canonical Shodan route is `/scans/shodan` only.
- `Scans` and `Extras` are toggle-only parent entries in nav.
- Mutating endpoints keep same-origin + CSRF protections.
