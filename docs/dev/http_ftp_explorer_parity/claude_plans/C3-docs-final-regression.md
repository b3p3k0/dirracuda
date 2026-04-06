# C3 Plan: Documentation + Risk Notes + Final Targeted Regression

## Context

C1 (UI tuning strip) and C2 (runtime behavior parity) are complete. The runtime contract is now:
- **SMB**: worker count + large-file split active
- **FTP**: worker count + large-file split active
- **HTTP**: worker count active; large-file split intentionally NOT active (UI field shown but disabled with explanatory note)

C3 closes the loop: update operator-facing docs to match this contract, update workspace status artifacts, and record a clean regression result. No runtime code changes.

---

## Config-source precision (critical)

FTP/HTTP worker count and large-file threshold are **not read from `conf/config.json`**. They are loaded from GUI settings keys (`file_browser.download_worker_count` / `file_browser.download_large_file_mb`) persisted by the settings manager (see `unified_browser_window.py` ~line 625 for FTP, ~line 1221 for HTTP). All documentation must reflect this: these are UI-controlled, settings-manager-persisted values, not `conf/config.json` config file values.

---

## Files to Modify (5 total)

### 1. `README.md` — Two sections

**Section A: Browsing Shares (~line 160)**

After "Downloads are staged in quarantine…", append:

> Download concurrency is configurable in the browser UI via the worker-count control (1–3 workers, default 2); the value is persisted in GUI settings under `file_browser.download_worker_count`. For SMB and FTP, a large-file threshold (persisted under `file_browser.download_large_file_mb`) routes files above that size to a dedicated worker. HTTP downloads use worker concurrency only — large-file routing is not active for HTTP in the current release. The large-file control is visible in the HTTP browser but disabled with an explanatory note.

**Section B: Configuration key list (~line 312)**

Expand the `file_browser.*` bullet:

> `file_browser.*` — browse mode limits (depth, entries, chunk size, quarantine root); download tuning — `download_worker_count` (1–3) and `download_large_file_mb` — is user-controlled in the browser UI and persisted in GUI settings, not read from this config file

---

### 2. `docs/TECHNICAL_REFERENCE.md` — Two sections

**Section A: §3.1 Configuration table (~line 155) — `file_browser` row**

Expand the Notes cell:

> GUI browser limits; `download_worker_count` (range 1–3, default 2) and `download_large_file_mb` are persisted as GUI settings keys, not loaded from this config file — they appear in the browser tuning strip; large-file threshold routing active for SMB and FTP only

**Section B: §6.6 File Browser (~line 641)**

After the quarantine/tmpfs sentence, add:

> Download concurrency is controlled by the worker-count spinbox in the browser UI (range 1–3, default 2), persisted in GUI settings under `file_browser.download_worker_count`. For SMB and FTP, a large-file threshold (GUI settings key `file_browser.download_large_file_mb`) dispatches files above that size to a dedicated large-file worker; remaining files share a separate small-file pool. HTTP uses worker-count concurrency only — there is no large-file queue routing for HTTP in the current release. The HTTP browser renders the large-file threshold control but disables it with an explanatory note.

---

### 3. `docs/dev/http_ftp_explorer_parity/README.md`

- Line 4: `Status: Card 0 complete (planning artifacts ready)` → `Status: C0–C3 complete`
- Card Status block:
  - C1: `ready` → `complete`
  - C2: `pending` → `complete`
  - C3: `pending` → `complete`

---

### 4. `docs/dev/http_ftp_explorer_parity/TASK_CARDS.md`

Append C3 card report after the end of the C3 card definition. The report **must include exact command output** (full pytest summary line + py_compile exit status), not just labels:

```markdown
### C3 Report

Issue: Operator/dev docs did not state new tuning behavior or HTTP large-file limitation.
Root cause: C1/C2 added runtime behavior; docs deferred to C3 per scope discipline.
Fix: Added worker count and large-file routing documentation to README.md (Browsing Shares +
Configuration sections) and TECHNICAL_REFERENCE.md (§3.1 table + §6.6 prose). Wording reflects
that tuning is UI-controlled and persisted in GUI settings keys (not conf/config.json). HTTP
limitation is explicit and unambiguous in both locations.
Files changed:
  - README.md
  - docs/TECHNICAL_REFERENCE.md
  - docs/RISK_REGISTER.md (R3 closure note)
  - docs/dev/http_ftp_explorer_parity/README.md
  - docs/dev/http_ftp_explorer_parity/TASK_CARDS.md (this report)

Validation run:
  py_compile: [exact exit status line, e.g. "exit 0"]
  pytest: [exact summary line, e.g. "42 passed in 3.21s"]

Result: [PASS/FAIL]
HI test needed: Yes.
  1. Open live HTTP browser; confirm large-file spinbox is disabled and note text matches docs wording.
  2. Open live FTP browser; confirm both controls are enabled and values persist after close/reopen.

AUTOMATED: [PASS/FAIL]
MANUAL:    PENDING
OVERALL:   PENDING
```

---

### 5. `docs/dev/http_ftp_explorer_parity/RISK_REGISTER.md`

Add R3 closure note. Current R3 entry: "HTTP appears to support large-file split when it doesn't (HIGH)".

Append or annotate: **Mitigated** — HTTP large-file control is rendered but disabled with explanatory note in UI; README.md and TECHNICAL_REFERENCE.md now explicitly state that large-file routing is inactive for HTTP in the current release.

---

## Validation Commands

```bash
./venv/bin/python -m pytest gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py gui/tests/test_browser_clamav.py -q
python3 -m py_compile gui/components/unified_browser_window.py
echo "py_compile exit: $?"
```

Paste the complete pytest summary line and py_compile exit status verbatim into the C3 report.

---

## Constraints

- No runtime code changes.
- No new files.
- Do not commit.
- Exact command output required in C3 report — no placeholder labels.
