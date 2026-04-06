# Card 6: Validation Report

Date: 2026-04-05
Branch: development

---

## Test Gap Analysis

Card 6 scope includes "add/expand tests where gaps exist." After reviewing all 7 experimental/redseek/reddit test
files (~2,700 lines total) against the risk register, no additional tests were added. This is a
deliberate conclusion, not skipped scope.

| Risk | Verdict | Test file(s) |
|------|---------|-------------|
| R1: Endpoint drift / malformed JSON | Covered | test_redseek_client.py |
| R2: HTTP 429 abort | Covered | test_redseek_client.py, test_redseek_service.py |
| R3: Dedupe / cursor drift | Covered | test_redseek_store.py, test_redseek_service.py |
| R4: Legacy DB isolation | Covered (regression suite) | test_ftp_browser_window.py, test_http_browser_window.py, test_smb_browser_window.py, test_dashboard_scan_dialog_wiring.py |
| R5: False positives / confidence levels | Covered | test_redseek_parser.py |
| R6: UI freeze during ingestion | Not automatable | Manual gate (see 08-MANUAL_HI_CHECKLIST.md) |
| R7: Ambiguous protocol on open action | Covered | test_redseek_explorer_bridge.py |

---

## Step 1: Static Check (py_compile)

```
./venv/bin/python -m py_compile \
  experimental/redseek/__init__.py \
  experimental/redseek/client.py \
  experimental/redseek/parser.py \
  experimental/redseek/models.py \
  experimental/redseek/store.py \
  experimental/redseek/service.py \
  experimental/redseek/explorer_bridge.py \
  gui/components/reddit_grab_dialog.py \
  gui/components/reddit_browser_window.py
```

Exit status: 0 (silent — no syntax or import errors)

**AUTOMATED: PASS**

---

## Step 2: Redseek Unit Tests

```
./venv/bin/python -m pytest -v \
  shared/tests/test_redseek_store.py \
  shared/tests/test_redseek_client.py \
  shared/tests/test_redseek_parser.py \
  shared/tests/test_redseek_service.py \
  shared/tests/test_redseek_explorer_bridge.py
```

Result: `134 passed, 27 warnings in 1.01s`

Warnings: `DeprecationWarning: datetime.datetime.utcnow()` in test fixtures and `experimental/redseek/service.py:416`.
Non-blocking — Python 3.12 deprecation notice; stdlib-level, no behavior impact in current Python
versions on this host.

**AUTOMATED: PASS**

---

## Step 3: GUI Reddit Tests (headless)

```
xvfb-run -a ./venv/bin/python -m pytest -v \
  gui/tests/test_dashboard_reddit_wiring.py \
  gui/tests/test_reddit_browser_window.py
```

Result: `37 passed in 0.14s`

**AUTOMATED: PASS**

---

## Step 4: Browser + Dashboard Regressions

```
xvfb-run -a ./venv/bin/python -m pytest -v \
  gui/tests/test_ftp_browser_window.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_smb_browser_window.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_dashboard_api_key_gate.py
```

Result: `48 passed in 0.18s`

**AUTOMATED: PASS**

---

## Step 5: Combined Filter Sweep

```
xvfb-run -a ./venv/bin/python -m pytest -v -k "reddit or redseek"
```

Result: `171 passed, 746 deselected, 27 warnings in 1.49s`

Same deprecation warnings as Step 2. Non-blocking.

**AUTOMATED: PASS**

---

## Remaining Risks and Caveats

1. **`datetime.utcnow()` deprecation** (non-blocking): `experimental/redseek/service.py:416` and several test
   fixtures use `datetime.datetime.utcnow()`. This is deprecated in Python 3.12 and will be removed
   in a future version. No immediate impact on supported Python versions, but should be migrated to
   `datetime.datetime.now(datetime.UTC)` before the module sees production use.

2. **Reddit JSON endpoint stability** (by design): The client uses `reddit.com/r/opendirectories/
   {sort}.json`. This is an unofficial access path with no stability guarantee. Any Reddit-side
   structural change to the JSON response will break ingestion silently or with a schema error.

3. **Rate limiting unpredictability** (by design): HTTP 429 aborts the current run. Retry behavior
   is intentionally absent. Operators must re-trigger manually.

4. **Manual UI flows not yet executed** (PENDING): R6 (UI responsiveness) and explorer protocol-
   prompt flows require a live GUI session. See 08-MANUAL_HI_CHECKLIST.md.

5. **No historical archive access** (by design): The module caps at 3 pages per run (~75 posts max
   with 25 per page). Full subreddit history is outside scope.

---

## Status Summary

| Layer | Status |
|-------|--------|
| AUTOMATED | **PASS** |
| MANUAL | **PENDING** (Flows A–D not yet executed in this session) |
| OVERALL | **PENDING** → becomes PASS once MANUAL gates cleared |
