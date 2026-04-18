# C6 Plan: SearXNG Dork Module — Docs + Regression Closeout

## Context

Cards C1–C5 shipped a complete SearXNG Dorking experimental module:
- Tab scaffold replaced the placeholder tab (C1)
- Preflight client with actionable reason codes (C2)
- Dork search service + sidecar DB `~/.dirracuda/se_dork.db` (C3)
- URL classifier reusing `commands/http/verifier.py` primitives (C4)
- Results browser window with promotion hook (C5)

C6 is a docs-only + regression closeout: no feature expansion. Goal is to make README.md and TECHNICAL_REFERENCE.md match exactly what shipped, resolve open planning questions, and update the workspace docs to reflect done state.

---

## Files to Touch

| File | Lines now | Changes |
|------|-----------|---------|
| `README.md` | 458 | Add SearXNG Dorking section; update Experimental tabs list |
| `docs/TECHNICAL_REFERENCE.md` | 848 | 6 targeted hunks (dir table, settings keys, sidecar §5.6, component tree, dashboard table, §6.9) |
| `docs/dev/searxng_dork_module/README.md` | 94 | Status → Complete (C1–C6 shipped) after step 4 |
| `docs/dev/searxng_dork_module/SPEC.md` | 159 | Status line → Approved and implemented (C1–C6 complete) after step 4 |
| `docs/dev/searxng_dork_module/ROADMAP.md` | 83 | Mark Objectives 0–6 all [DONE] after step 4 |
| `docs/dev/searxng_dork_module/TASK_CARDS.md` | 323 | Fix C6 validation commands to canonical form |
| `docs/dev/searxng_dork_module/OPEN_QUESTIONS.md` | 49 | Resolve 3 open questions as D8–D10 |

---

## Detailed Changes

### 1. `README.md` — Experimental Features section (lines 341–396)

**Current state:** lists `Reddit` + `placeholder (scaffold tab for future modules)`. No SearXNG content.

**Changes:**
- Replace the tab list bullet: `placeholder (scaffold tab for future modules)` → `SearXNG Dorking`
- Remove all placeholder-era wording from the Experimental section (the tab list sentence, any parenthetical, any mention of "scaffold tab for future modules")
- Add a new `### SearXNG Dorking` subsection immediately after the Reddit section, covering:
  - Entry point: `Dashboard → ⚗ Experimental → SearXNG Dorking tab`
  - Three actions: Test Instance, Run Dork Search, Open Results DB
  - Settings that persist: instance URL, query, max results
  - SearXNG setup requirement + `format=json → 403` troubleshooting block (verbatim from workspace README)
  - Promotion path: "Add to dirracuda DB" via context menu; "Not available" shown if Server List is not open — open Server List and reopen Results DB to enable it
  - Sidecar DB path: `~/.dirracuda/se_dork.db`
  - Verdicts: `OPEN_INDEX`, `MAYBE`, `NOISE`, `ERROR`

### 2. `docs/TECHNICAL_REFERENCE.md` — 6 targeted hunks

**Hunk A — §2 Directory Structure (line ~112):**
- `experimental/placeholder/` → `experimental/se_dork/`
- Description: `SearXNG dork search pipeline (client, service, store, classifier, models)`

**Hunk B — §3.3 Two-Config System (after Reddit settings block, line ~207):**
Add:
```
SearXNG Dorking experimental UI settings persisted in `gui_settings.json`:
- `se_dork.instance_url`
- `se_dork.query`
- `se_dork.max_results`
```

**Hunk C — §5 Database: add new §5.6 after existing §5.5 (after line ~593):**
```
### 5.6 SearXNG Dork Sidecar Database (`~/.dirracuda/se_dork.db`)

The SearXNG Dorking module (`experimental/se_dork`) writes to a separate SQLite database.
It does not share tables with `dirracuda.db`.

Tables:
- `dork_runs` — one row per dork search run (PK `run_id`), with `instance_url`, `query`,
  `max_results`, `fetched_count`, `deduped_count`, `verified_count`, `status`, `error_message`
- `dork_results` — one row per candidate URL per run (PK `result_id`), FK `run_id →
  dork_runs(run_id)`; deduped per run on `UNIQUE(run_id, url_normalized)`; stores `url`,
  `title`, `snippet`, `source_engine`, `source_engines_json`, `verdict`, `reason_code`,
  `http_status`, `checked_at`

Verdict values: `OPEN_INDEX`, `MAYBE`, `NOISE`, `ERROR`.

URL normalization: scheme and netloc lowercased; path case preserved; trailing slash stripped from path; query string and fragment dropped.
```

**Hunk D — §6.1 Component Hierarchy (line ~621):**
- Replace `└─ placeholder tab (gui/components/experimental_features/placeholder_tab.py)` with:
  `└─ SearXNG Dorking tab (gui/components/experimental_features/se_dork_tab.py)`
  plus note about the browser: `→ SeDorkBrowserWindow (gui/components/se_dork_browser_window.py)`

**Hunk E — §6.4 Dashboard Controls table (line ~656):**
- Experimental button description: change `Reddit + placeholder tabs` → `Reddit + SearXNG Dorking tabs`

**Hunk F — §6.9 Experimental Features section (lines ~709–741):**
- Update current tabs list: replace `placeholder` → `SearXNG Dorking`
- Add SearXNG Dorking entry/browser paths (parallel to Reddit paths already shown)
- Add note: promotion callback comes from Server List window; "Not available" shown when callback absent

### 3. `docs/dev/searxng_dork_module/README.md`

- Status line interim (during execution): `Shipped (C1–C5 complete; C6 in progress)`
- Final value set in step 4 after validation passes: `Complete (C1–C6 shipped)`

### 4. `docs/dev/searxng_dork_module/SPEC.md`

- Status line set in step 4: `Approved and implemented (C1–C6 complete)`

### 5. `docs/dev/searxng_dork_module/ROADMAP.md`

- Prepend `[DONE]` to Objectives 0–5 headings during execution
- Mark Objective 6 as `[IN PROGRESS]` during execution
- Step 4: flip Objective 6 to `[DONE]` once all validation passes

### 6. `docs/dev/searxng_dork_module/TASK_CARDS.md`

Update C6 validation commands to match canonical form from task instructions:

**Pytest block:** add `shared/tests/test_se_dork_classifier.py` (missing from current card).

**rg block:** expand pattern and paths, but do NOT include `Search Dork` in the embedded rg pattern text (doing so would permanently plant that string in the card, creating a known false-positive in the negative check and violating the "replace Search Dork throughout" rule).

Updated rg block in TASK_CARDS.md C6:
```
rg -n "SearXNG Dorking|SearXNG Dork Module|format=json|403|Not available|se_dork" \
  README.md docs/TECHNICAL_REFERENCE.md docs/dev/searxng_dork_module/
```

The negative check for stale "Search Dork" wording is a separate command run during C6 execution only, scoped to the final-doc targets (see Validation section below). It is not embedded in the task card.

### 7. `docs/dev/searxng_dork_module/OPEN_QUESTIONS.md`

Move three open questions into Resolved Decisions:

- **D8** — Promotion UI in v1: shipped as "Add to dirracuda DB" via context menu when callback present; shows "Not available" when absent (open Server List first to enable callback)
- **D9** — Classification depth: shipped with verifier primitives only (`try_http_request` + `validate_index_page`); `run_http_probe` path deferred to v2
- **D10** — Export formats: deferred to v2

---

## Validation to Run After Changes

**Step 1 — regression suite:**
```bash
./venv/bin/python -m pytest \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py \
  gui/tests/test_se_dork_tab.py \
  gui/tests/test_se_dork_browser_window.py \
  shared/tests/test_se_dork_client.py \
  shared/tests/test_se_dork_classifier.py \
  shared/tests/test_se_dork_service.py \
  shared/tests/test_se_dork_store.py -q
```

**Step 2 — positive presence check (canonical terms exist in final docs):**
```bash
rg -n "SearXNG Dorking|SearXNG Dork Module|format=json|403|Not available|se_dork" \
  README.md docs/TECHNICAL_REFERENCE.md docs/dev/searxng_dork_module/
```
Expected: hits for `SearXNG Dorking`, `format=json`, `403`, `Not available`, `se_dork` in README.md and docs/TECHNICAL_REFERENCE.md.

**Step 3 — negative check (no stale "Search Dork" in final-doc targets only):**
```bash
rg -n "Search Dork" README.md docs/TECHNICAL_REFERENCE.md
```
Expected: zero hits. Any hit is a defect.

**Step 4 (final step, after all checks pass) — flip closeout docs to DONE:**
- `docs/dev/searxng_dork_module/ROADMAP.md`: Objective 6 heading → `[DONE]`
- `docs/dev/searxng_dork_module/README.md`: status → `Complete (C1–C6 shipped)`
- `docs/dev/searxng_dork_module/SPEC.md`: status → `Approved and implemented (C1–C6 complete)`

---

## Report Format (to produce after execution)

- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? yes — steps: fresh-start setup with documented steps; validate success path and intentional 403-misconfig path
- Line count rubric before/after per touched file
- AUTOMATED: PASS/FAIL
- MANUAL: PASS/FAIL/PENDING
- OVERALL: PASS/FAIL/PENDING
