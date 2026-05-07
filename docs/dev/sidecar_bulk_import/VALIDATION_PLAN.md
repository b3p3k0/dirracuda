# Validation Plan

Automated:
- Compile touched modules with `py_compile`.
- Run targeted pytest suites for sidecar promotion, experimental wiring, and both browser windows.

HI checks:
1. Open SearXNG Results DB, multi-select rows, click `Add to dirracuda DB`.
2. Confirm progress dialog appears, cancel works, and summary totals are shown.
3. Repeat in Reddit Post DB with mixed valid/invalid protocol rows.
4. Verify single-row behavior still shows existing success/error dialogs.
