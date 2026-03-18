# FTP Module Beta Test Script (Round 1)

Audience: first-time testers with no project background.

Estimated time: 20-35 minutes.

## Before You Start

1. Open a terminal in the project root.
2. Start the app in mock mode:
   ```bash
   ./xsmbseek --mock
   ```
3. Wait for the main window to fully load.
4. Keep this checklist open while testing.

If the app does not launch, stop and report that as a blocker.

## How to Record Results

For each test, mark one:
- `PASS` = behavior matches expected result
- `FAIL` = behavior is wrong or confusing
- `BLOCKED` = you could not complete the test (missing data, crash, etc.)

---

## Test 1: Launch + Main Window

Steps:
1. Launch with `./xsmbseek --mock`.
2. Wait 10 seconds.

Expected:
1. App window appears.
2. No crash or immediate error popup.

---

## Test 2: Open Server List

Steps:
1. Open the Server List Browser window from the main UI.
2. Look at the title bar.

Expected:
1. Window title is `Server List Browser`.
2. Header text includes `Server List`.

---

## Test 3: Verify Table Columns

Steps:
1. In the server list table, read headers left to right.
2. Find the `Type` column.

Expected:
1. `Type` exists.
2. `Type` appears between `Extracted` and `IP Address`.

---

## Test 4: Verify Type Values

Steps:
1. Scan 10-20 rows in the table.
2. Check values under `Type`.

Expected:
1. Type values are `S` and/or `F`.
2. No blank `Type` cells.

---

## Test 5: Open Details for SMB Row

Steps:
1. Select a row with `Type = S`.
2. Open details (double-click row or details button).

Expected:
1. Details window opens.
2. It shows `Protocol: SMB`.
3. Share access section is visible.

---

## Test 6: Open Details for FTP Row

Steps:
1. Select a row with `Type = F`.
2. Open details.

Expected:
1. Details window opens.
2. It shows `Protocol: FTP`.
3. FTP access info appears (anonymous/port/banner).

---

## Test 7: Favorite Toggle Is Per Row

Steps:
1. Find two rows with same IP but different type (`S` and `F`) if available.
2. Toggle favorite on only one row.

Expected:
1. Only the clicked row changes favorite icon.
2. Sibling row (same IP, other type) does not change.
3. If no same-IP pair exists, mark `BLOCKED`.

---

## Test 8: Avoid Toggle Is Per Row

Steps:
1. Use a same-IP S/F pair if available.
2. Toggle avoid on only one row.

Expected:
1. Only clicked row changes avoid icon.
2. Sibling row does not change.
3. If no same-IP pair exists, mark `BLOCKED`.

---

## Test 9: Favorites Filter

Steps:
1. Mark exactly one row as favorite.
2. Enable `Favorites only` filter.

Expected:
1. Favorited row remains visible.
2. Non-favorited rows disappear.
3. If same-IP S/F pair is present and only one is favorited, only that one remains.

---

## Test 10: Browse SMB Row

Steps:
1. Select a row with `Type = S`.
2. Trigger browse/file-browser action.

Expected:
1. SMB browser opens.
2. No FTP browser window opens instead.

---

## Test 11: Browse FTP Row

Steps:
1. Select a row with `Type = F`.
2. Trigger browse action.

Expected:
1. FTP browser window opens.
2. No crash.

---

## Test 12: Probe FTP Row (Currently Unsupported)

Steps:
1. Select a row with `Type = F`.
2. Start probe.

Expected:
1. Operation is skipped cleanly (not hard-failed/crashed).
2. You see a clear message/status indicating FTP probe is not supported yet.

---

## Test 13: Extract FTP Row (Currently Unsupported)

Steps:
1. Select a row with `Type = F`.
2. Start extract.

Expected:
1. Operation is skipped cleanly.
2. You see a clear message/status indicating FTP extract is not supported yet.

---

## Test 14: Delete One Protocol Row Only

Steps:
1. Find same-IP S/F pair if available.
2. Delete only one row (for example `S` row).
3. Refresh/reload list if needed.

Expected:
1. Deleted row is gone.
2. Sibling row (other protocol) is still present.
3. If no same-IP pair exists, mark `BLOCKED`.

---

## Test 15: Pry on FTP Row Is Blocked

Steps:
1. Select a row with `Type = F`.
2. Trigger Pry action.

Expected:
1. Warning appears that Pry is SMB-only.
2. Pry workflow does not start.

---

## Final Quick Score

Rate each 1-5:
1. Overall stability
2. Clarity of UI labels/messages
3. Ease of understanding what happened after each action
4. Confidence using this without developer help

