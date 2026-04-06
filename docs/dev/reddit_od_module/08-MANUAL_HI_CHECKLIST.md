# Card 6: Manual HI Checklist

Date: 2026-04-05
Status: PENDING (not yet executed — requires live GUI session)

Mark each item PASS or FAIL when executed. Note any deviations.

---

## Flow A: Ingestion — `new` mode

**Setup:** App running, no prior Reddit data in sidecar DB (fresh or wiped).

| # | Step | Pass Criteria | Result |
|---|------|---------------|--------|
| A1 | Open dashboard. Click `▶ Start Scan`, then click `Reddit Grab (EXP)` at the lower-left of the scan dialog. | Reddit Grab dialog opens with Sort=new, Max posts=50 defaults. | PENDING |
| A2 | Set: Sort=new, Max posts=50, Parse body=on, Include NSFW=on, Replace cache=off. Click `Run Grab`. | Dialog closes. Dashboard does not freeze. Progress/result appears. | PENDING |
| A3 | Result message shows at least one of: posts ingested, targets stored, or "no new posts". No unhandled exception dialog. | Clean summary message. | PENDING |
| A4 | Click `📋 Servers`, then click `Reddit Post DB (EXP)` in the top-right of the Server List window. | Browser window opens. Table populated or shows "no rows" without error. | PENDING |
| A5 | Run `Reddit Grab (EXP)` again immediately with same settings (Replace cache=off). | Second run completes. Row count in browser does not increase significantly (dedupe working). | PENDING |

**Notes:**
_Record actual post/target counts from A3 result dialog._

---

## Flow B: Ingestion — `top` mode

**Setup:** Sidecar DB may contain data from Flow A.

| # | Step | Pass Criteria | Result |
|---|------|---------------|--------|
| B1 | Open `▶ Start Scan`, click `Reddit Grab (EXP)`. Set Sort=top. Click `Run Grab`. | Run completes without error. Summary shows bounded result (≤3 pages worth). | PENDING |
| B2 | Run top mode a second time immediately. | Row count in browser does not meaningfully increase (top-mode dedupe working via dedupe_key). | PENDING |
| B3 | Check `Reddit Post DB (EXP)` for newly ingested rows from top mode. | Rows reflect top-mode ingestion without duplicate explosion on repeat runs. | PENDING |

**Notes:**
_Record whether any 429 was hit during the run. If so, confirm abort message shown and partial counts reported._

---

## Flow C: Reddit browser actions

**Setup:** Browser open with at least one row of each type: full URL, host:port, bare host.

| # | Step | Pass Criteria | Result |
|---|------|---------------|--------|
| C1 | Select a row with a full URL (e.g. `http://...` or `https://...`). Click `Open in Explorer`. | System browser opens that URL directly. No protocol prompt shown. | PENDING |
| C2 | Select a row with host:port for a known port (80, 443, or 21). Click `Open in Explorer`. | System browser opens with correct inferred scheme. No prompt shown. | PENDING |
| C3 | Select a row with bare host only (no scheme, no known port). Click `Open in Explorer`. | Protocol-pick prompt appears. Choosing http/https/ftp opens browser. Cancelling does nothing. | PENDING |
| C4 | Select any row. Click `Open Reddit Post`. | System browser opens `https://www.reddit.com/r/opendirectories/comments/<post_id>/`. | PENDING |
| C5 | Click `Refresh`. | Table reloads. Filter field resets. Sort indicators clear. | PENDING |
| C6 | Click `Clear DB`. Confirm the confirmation dialog. | Table empties. Sidecar DB wiped. Status line reflects empty state. | PENDING |

**Notes:**
_If no bare-host rows exist in the DB after Flow A/B, note this and mark C3 as N/A._

---

## Flow D: Isolation regression

**Setup:** Reddit module loaded (dashboard open). No active scan.

| # | Step | Pass Criteria | Result |
|---|------|---------------|--------|
| D1 | Click `Start Scan`. Choose SMB. Open the scan dialog. | Dialog opens normally. No errors related to Reddit module. | PENDING |
| D2 | Cancel the SMB scan dialog. Click `Start Scan`. Choose FTP. | FTP scan dialog opens normally. | PENDING |
| D3 | Cancel. Click `Start Scan`. Choose HTTP. | HTTP scan dialog opens normally. | PENDING |
| D4 | If scan state changes while `Start Scan` dialog is open, click `Reddit Grab (EXP)` from that dialog. | Grab does not start unless scan state is idle; no crash or stuck dialog state. | PENDING |
| D5 | Confirm `reddit_od.db` path is `~/.dirracuda/reddit_od.db` (or configured path). Confirm `dirracuda.db` is unchanged. | Main DB schema unmodified. No `reddit_*` tables in main DB. | PENDING |

**Notes:**
_For D5, can use `sqlite3 dirracuda.db ".tables"` to verify no reddit tables present._

---

## Completion Criteria

All PENDING items must be executed and marked PASS or FAIL before OVERALL status can move from
PENDING to a final verdict.

If any item is marked FAIL, document the failure details and whether it is blocking or non-blocking
before closing the card.
