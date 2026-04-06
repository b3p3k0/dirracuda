# Card 6: Validation, Docs, and POC Exit Criteria

## Context

Cards 1–5 implemented the full Reddit OD module (redseek): sidecar DB, client, parser, service, GUI dialog, browser window, and explorer bridge. Card 6 closes the loop with a complete, auditable validation package and docs that capture real limits.

Hard constraints from LOCKED_DECISIONS.md:
- No commits
- No main DB writes (sidecar only)
- No scan_manager/backend pipeline coupling for Reddit
- No CLI path creation for Reddit
- Mark manual gates PENDING unless physically executed in this session

---

## Test Gap Analysis

After reviewing all 7 redseek/reddit test files (totalling ~2,700 lines) against the risk register, the automated coverage is comprehensive. No new test files are required. Coverage summary:

| Risk | Coverage | Location |
|------|----------|----------|
| R1: Endpoint drift / malformed JSON | ✓ | test_redseek_client.py |
| R2: HTTP 429 abort | ✓ | test_redseek_client.py + test_redseek_service.py |
| R3: Dedupe / cursor drift | ✓ | test_redseek_store.py + test_redseek_service.py |
| R4: Legacy DB isolation | ✓ (existing regression suite) | test_ftp_browser_window.py, test_http_browser_window.py, test_smb_browser_window.py, test_dashboard_scan_dialog_wiring.py |
| R5: False positives / confidence | ✓ | test_redseek_parser.py |
| R6: UI freeze | Manual only | — |
| R7: Ambiguous protocol | ✓ | test_redseek_explorer_bridge.py |

---

## Execution Plan

### Step 1: Static check (py_compile)

```bash
cd /home/kevin/DEV/dirracuda

./venv/bin/python -m py_compile \
  redseek/__init__.py \
  redseek/client.py \
  redseek/parser.py \
  redseek/models.py \
  redseek/store.py \
  redseek/service.py \
  redseek/explorer_bridge.py \
  gui/components/reddit_grab_dialog.py \
  gui/components/reddit_browser_window.py
```

Expected: silent (no output = pass).

### Step 2: Redseek unit tests (no Tk required)

```bash
./venv/bin/python -m pytest -v \
  shared/tests/test_redseek_store.py \
  shared/tests/test_redseek_client.py \
  shared/tests/test_redseek_parser.py \
  shared/tests/test_redseek_service.py \
  shared/tests/test_redseek_explorer_bridge.py
```

### Step 3: GUI reddit tests (headless)

```bash
xvfb-run -a ./venv/bin/python -m pytest -v \
  gui/tests/test_dashboard_reddit_wiring.py \
  gui/tests/test_reddit_browser_window.py
```

### Step 4: Browser + dashboard regressions

```bash
xvfb-run -a ./venv/bin/python -m pytest -v \
  gui/tests/test_ftp_browser_window.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_smb_browser_window.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_dashboard_api_key_gate.py
```

### Step 5: Combined filter sweep

```bash
xvfb-run -a ./venv/bin/python -m pytest -v -k "reddit or redseek"
```

### Step 6: Create docs artifacts

**Create** `docs/dev/reddit_od_module/07-CARD6_VALIDATION_REPORT.md`
- Context + date
- **Gap analysis section**: explain why no new tests were added — existing 7 test files (2,700+ lines) cover all risk register items; Card 6 "add/expand" scope was evaluated and found complete; this is a deliberate conclusion, not skipped scope
- py_compile results (exact command + exit status)
- pytest results for each group: exact command + final summary line only (e.g. `47 passed in 3.21s`), plus any warnings with blocking/non-blocking note
- AUTOMATED / MANUAL / OVERALL status block
- If a run produces notable warnings or failures, include the relevant failure lines — not the full stdout

**Create** `docs/dev/reddit_od_module/08-MANUAL_HI_CHECKLIST.md`
- Flow A: Ingestion `new` (open dialog → run → re-run for dedupe)
- Flow B: Ingestion `top` (bounded pages, repeat-run dedupe)
- Flow C: Reddit browser actions (3 row types for Open in Explorer)
- Flow D: Isolation regression (SMB/FTP/HTTP dialog launch)
- Each gate: PENDING with explicit "what to verify" and "pass criteria"

### Step 7: Update README.md

Read README.md first to confirm line 339 context ("Experimental Features"). Insert a **Reddit Ingestion (redseek)** subsection under that heading. Include:
1. One-sentence summary: what it is and what it is not (feed ingestion, not a scan source)
2. How to access: Dashboard → Reddit Grab / Reddit Post DB
3. Disclaimer block from SPEC.md (verbatim, short)
4. Known limitations bullet list (5 items from SPEC.md Known Limitations)

Do **not** rewrite existing README sections. Surgical insert only.

---

## Critical Files

| File | Action |
|------|--------|
| `redseek/__init__.py` | read only (verify import) |
| `redseek/client.py` | read only |
| `redseek/parser.py` | read only |
| `redseek/models.py` | read only |
| `redseek/store.py` | read only |
| `redseek/service.py` | read only |
| `redseek/explorer_bridge.py` | read only |
| `gui/components/reddit_grab_dialog.py` | read only |
| `gui/components/reddit_browser_window.py` | read only |
| `shared/tests/test_redseek_*.py` (5 files) | run only |
| `gui/tests/test_dashboard_reddit_wiring.py` | run only |
| `gui/tests/test_reddit_browser_window.py` | run only |
| `gui/tests/test_ftp_browser_window.py` | run only (regression) |
| `gui/tests/test_http_browser_window.py` | run only (regression) |
| `gui/tests/test_smb_browser_window.py` | run only (regression) |
| `gui/tests/test_dashboard_scan_dialog_wiring.py` | run only (regression) |
| `gui/tests/test_dashboard_api_key_gate.py` | run only (regression) |
| `docs/dev/reddit_od_module/07-CARD6_VALIDATION_REPORT.md` | **create** |
| `docs/dev/reddit_od_module/08-MANUAL_HI_CHECKLIST.md` | **create** |
| `README.md` | **edit** (surgical insert) |

---

## Reused Utilities

- `redseek/store.py::init_db`, `wipe_all` — already in service and browser
- `redseek/service.py::run_ingest` — already in dashboard worker
- `redseek/explorer_bridge.py::open_target` — already in browser window

---

## Verification

End-to-end check:
1. All py_compile targets exit 0
2. `pytest` groups show 0 failures, 0 errors
3. Combined `-k "reddit or redseek"` shows same set with 0 failures
4. `07-CARD6_VALIDATION_REPORT.md` contains exact command outputs, clear AUTOMATED/MANUAL/OVERALL verdict
5. `08-MANUAL_HI_CHECKLIST.md` has all 4 flows (A–D) with PENDING gates and explicit pass criteria
6. README.md diff is a clean insert with no deletions to existing content

If any test fails during execution: stop, diagnose, fix the root cause in the test or source, re-run that group only before continuing.

---

## Final Status Model

```
AUTOMATED: PASS (pending execution — set after test run)
MANUAL:    PENDING (Flows A–D require live GUI session)
OVERALL:   PENDING → PASS once AUTOMATED=PASS confirmed
```
