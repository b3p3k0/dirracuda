# Reddit OD Module: V2 Validation Plan

Date: 2026-04-06

## V2 Implementation State (2026-04-06)

Cards V2-1 through V2-3: code-complete, test suites executed and passing.  
Card V2-4: docs-only (this card).  
HI manual gates: not yet executed this session.

Status labels:
- `AUTOMATED: PASS|FAIL`
- `MANUAL: PASS|FAIL|PENDING`
- `OVERALL: PASS|FAIL|PENDING`

## Automated Checks

### A) Redseek core (service + bridge)
Commands:
```bash
./venv/bin/python -m pytest -q \
  shared/tests/test_redseek_service.py \
  shared/tests/test_redseek_explorer_bridge.py
```
Expected:
1. Preview note generation behavior covered and passing.
2. Internal-open/fallback prompt branches covered and passing.

**AUTOMATED: PASS**

### B) Reddit browser + server list integration slices
Commands:
```bash
xvfb-run -a ./venv/bin/python -m pytest -q \
  gui/tests/test_reddit_browser_window.py \
  gui/tests/test_server_list_card4.py \
  gui/tests/test_dashboard_reddit_wiring.py
```
Expected:
1. Context-menu add flow tests pass.
2. Existing Reddit/Server List wiring remains stable.

**AUTOMATED: PASS**

### C) Browser/scan regression confidence
Commands:
```bash
xvfb-run -a ./venv/bin/python -m pytest -q \
  gui/tests/test_ftp_browser_window.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_smb_browser_window.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py
```
Expected:
1. No regressions in existing browser windows.
2. No regressions in scan dialog wiring.

**AUTOMATED: PASS**

### D) Fast umbrella check
Commands:
```bash
xvfb-run -a ./venv/bin/python -m pytest -q -k "reddit or redseek"
```
Expected:
1. No new failures.

**AUTOMATED: PASS**

## Manual HI Gate (V2)

### Flow A: Notes preview behavior
1. Run Reddit Grab with parse-body on.
2. Open Reddit Post DB and confirm `notes` column shows deterministic preview format.
3. Re-run with parse-body off and confirm body preview is omitted.

Pass criteria:
1. Notes are readable, capped, and deterministic.
2. No parser diagnostic residue appears in notes after V2 runs.

**MANUAL: PENDING**

### Flow B: Internal-first open + fallback choices
1. Select row with known HTTP/FTP target.
2. Click `Open in Explorer`.
3. Confirm internal browser launches first.
4. Force a fallback case (unsupported/unresolved target) and confirm prompt offers:
   - Open in system browser
   - Copy address
   - Cancel

Pass criteria:
1. All three fallback actions work as labeled.
2. No silent failure when internal open cannot proceed.

**MANUAL: PENDING**

### Flow C: Add to dirracuda DB from Reddit browser
1. Right-click a Reddit row -> `Add to dirracuda DB`.
2. Confirm Add Record dialog opens prefilled.
3. Save and verify row appears in Server List under correct protocol.
4. Try non-IP host target and confirm clear guidance path (no silent write).

Pass criteria:
1. Promotion uses existing Add Record semantics.
2. Writes are user-confirmed only.
3. Protocol/port mapping is correct for D1 behavior.

**MANUAL: PENDING**

### Flow D: Entry-point discoverability
1. From dashboard, open Start Scan dialog and confirm `Reddit Grab (EXP)` placement.
2. From Servers window, confirm `Reddit Post DB (EXP)` button placement.
3. Confirm README click-path text matches actual UI locations.

Pass criteria:
1. UI and README are consistent and obvious for new users.

**MANUAL: PENDING**

## Exit Criteria
1. `AUTOMATED: PASS` for all suites above.
2. `MANUAL: PASS` for Flows A–D.
3. `OVERALL: PASS`.
4. No commits unless HI explicitly requests commit.

**OVERALL: PENDING** — automated passed, manual HI gates not yet executed.

