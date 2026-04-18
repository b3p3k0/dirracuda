# C0 — Contract Freeze + Baseline

**Context:** Before any code is written for the SearXNG Dork Module, C0 confirms all three runtime seams are viable and documents the exact touch list and validation commands that will govern C1–C6. No code is edited; this is a read-only audit pass.

---

## 1. Card Summary

C0 verifies three seams and freezes the validation contract:

| Seam | Status |
|------|--------|
| Experimental registry placeholder-replacement path | Confirmed — single list edit in `_get_features()` |
| SearXNG preflight contract (`/config` + `/search?format=json`) | Confirmed — live instance at `http://192.168.1.20:8090` |
| HTTP verifier/probe reuse path | Confirmed — `try_http_request` + `validate_index_page`; URL parse step required |

---

## 1a. Naming Lock

**Internal package name: `se_dork` (keep as-is through C1–C6).**

Rationale: matches the existing SPEC.md architecture diagram (`experimental/se_dork/client.py`), mirrors the `redseek` brevity pattern (short internal identifiers, canonical long-form in docs/UI only), and avoids a rename that would introduce churn in every C2–C6 touch. The canonical module name in docs remains "SearXNG Dork Module"; the tab label remains "SearXNG Dorking". This decision is locked for v1.

---

## 2. Concrete File Touch List for C1–C6

### C1 — Tab Scaffold

| File | Action |
|------|--------|
| `gui/components/experimental_features/registry.py` | Edit: swap placeholder entry for se_dork entry in `_get_features()` (lines 40–44) |
| `gui/components/experimental_features/se_dork_tab.py` | New: non-functional UI shell |
| `gui/components/experimental_features/placeholder_tab.py` | Delete — but only after registry removal is verified clean (see C1 sequence note) |
| `experimental/placeholder/__init__.py` | Delete — same gate as above |
| `gui/tests/test_experimental_features_dialog.py` | Edit: update tab-label assertions |

### C2 — Preflight Client

| File | Action |
|------|--------|
| `experimental/se_dork/__init__.py` | New: package marker |
| `experimental/se_dork/client.py` | New: `test_instance()` → calls `/config` then `/search?format=json` |
| `experimental/se_dork/models.py` | New: `PreflightResult`, `DorkResult` dataclasses |
| `gui/components/experimental_features/se_dork_tab.py` | Edit: wire Test button to client |
| `shared/tests/test_se_dork_client.py` | New |
| `gui/tests/test_se_dork_tab.py` | New |

### C3 — Service + Sidecar Store

| File | Action |
|------|--------|
| `experimental/se_dork/service.py` | New: orchestrates search → dedupe → persist |
| `experimental/se_dork/store.py` | New: SQLite sidecar at `~/.dirracuda/se_dork.db` |
| `experimental/se_dork/models.py` | Edit: add `DorkRun`, `DorkResult` persistence models |
| `gui/components/experimental_features/se_dork_tab.py` | Edit: wire Run button, show summary |
| `shared/tests/test_se_dork_store.py` | New |
| `shared/tests/test_se_dork_service.py` | New |

### C4 — Classifier / Verification

| File | Action |
|------|--------|
| `experimental/se_dork/classifier.py` | New: URL parse → `try_http_request` + `validate_index_page` → verdict |
| `experimental/se_dork/service.py` | Edit: call classifier per candidate |
| `commands/http/verifier.py` | Read-only (edit only if bug found) |
| `shared/tests/test_se_dork_classifier.py` | New |
| `shared/tests/test_se_dork_service.py` | Edit: extend |

### C5 — Results Browser

| File | Action |
|------|--------|
| `gui/components/se_dork_browser_window.py` | New: Toplevel results table with Open/Copy/Promote |
| `gui/components/experimental_features/se_dork_tab.py` | Edit: wire "Open Dork Results DB" button |
| `experimental/se_dork/store.py` | Edit: add query/fetch methods for browser |
| `gui/tests/test_se_dork_browser_window.py` | New |

### C6 — Docs + Regression

| File | Action |
|------|--------|
| `README.md` | Edit: add SearXNG Dorking section |
| `docs/TECHNICAL_REFERENCE.md` | Edit: add module architecture + sidecar schema |
| `docs/dev/searxng_dork_module/` | Edit: finalise SPEC/ROADMAP/TASK_CARDS to match shipped state |
| All test files from C1–C5 | Read: regression pass |

---

## 3. Step-by-Step C0 Plan

**Step 1 — Confirm registry replacement seam.**

```bash
# Pattern presence
rg -n "placeholder|experimental_features|registry" gui/components -g '*.py'
```

Then do a source read to verify structure assumptions, not just pattern presence:

```bash
# Read the exact registry to confirm _get_features() shape and placeholder entry line numbers
sed -n '1,60p' gui/components/experimental_features/registry.py
```

Expected evidence:
- `registry.py` contains `_get_features()` returning a list with `feature_id="placeholder"` (confirm exact lines).
- `build_all_tabs()` iterates that list; the dialog calls `build_all_tabs()` — no dialog changes needed to swap tabs.
- Replacement seam: edit `_get_features()`, add `ExperimentalFeature(feature_id="se_dork", label="SearXNG Dorking", build_tab=build_se_dork_tab)`, remove placeholder entry.

**C1 placeholder deletion sequence (locked here):**
1. Remove placeholder entry from `_get_features()` in `registry.py`.
2. Run `rg -rn "placeholder_tab\|from.*placeholder\|import.*placeholder" gui/ experimental/ --include='*.py'` (includes `gui/tests/`) to confirm zero live references remain — catches test-only imports and fixture strings.
3. Only if grep is clean: delete `placeholder_tab.py` and `experimental/placeholder/__init__.py`.
4. Re-run py_compile and tests before reporting done.

**Step 2 — Confirm HTTP verifier/probe integration points.**

```bash
# Pattern presence
rg -n "run_http_probe|try_http_request|validate_index_page|dispatch_probe_run" gui commands -g '*.py'
```

Then read function signatures directly to lock the adapter contract:

```bash
# Confirm try_http_request and validate_index_page signatures
sed -n '44,60p' commands/http/verifier.py
sed -n '150,170p' commands/http/verifier.py
```

Expected evidence:
- `commands/http/verifier.py` — `try_http_request(ip, port, scheme, ...)` at line ~46; `validate_index_page(body, status_code)` at line ~153.
- `gui/utils/http_probe_runner.py` — `run_http_probe` at line ~55.
- `gui/utils/probe_cache_dispatch.py` — `dispatch_probe_run` at line ~74.

**Classifier constraint (locked):** `try_http_request(ip, port, scheme, ...)` takes decomposed host components, not a URL string. `classifier.py` must parse each SearXNG result URL with `urllib.parse.urlparse()` to extract `(scheme, hostname, port, path)` before calling into the existing path. This is the only adapter step needed.

**Step 3 — Confirm SearXNG preflight contract.**

Run (from Dirracuda workstation):
```bash
# /config endpoint — headers + top-level JSON keys
curl -sS -D - 'http://192.168.1.20:8090/config' -o /tmp/sx_config.json | head -n 20
./venv/bin/python -c "import json; j=json.load(open('/tmp/sx_config.json')); print('config_keys=', list(j.keys())[:10])"

# JSON search — headers + parsed result
curl -sS -D - 'http://192.168.1.20:8090/search?q=hello&format=json' -o /tmp/sx.json | head -n 20
./venv/bin/python - <<'PY'
import json
j = json.load(open('/tmp/sx.json'))
print('top_keys=', list(j.keys()))
print('results_len=', len(j.get('results', [])))
print('first_url=', (j.get('results') or [{}])[0].get('url'))
PY
```

**Live host fallback (if 192.168.1.20:8090 is unreachable from this machine):**
- Run the same two curl commands locally on the SearXNG host (SSH in, use `127.0.0.1:8090`).
- Paste the full header block + python3 output here as evidence.
- Blocked signal: `curl: (7) Failed to connect` or connection timeout on both endpoints → add to plan as known blocker, note that C3 live-run validation is deferred until LAN access is confirmed.

**Step 4 — Document format-policy failure mode.**

The `instance_format_forbidden` failure mode is detected as follows:
- `/config` returns 200 → instance is reachable.
- `/search?q=hello&format=json` returns **403** → SearXNG is blocking non-HTML formats.
- Root cause: `settings.yml` does not include `json` in `search.formats`.
- Client must check: `status == 403` on the json search call → set reason code `instance_format_forbidden`, display hint: `"enable search.formats: [json] in settings.yml, then restart SearXNG"`.
- This is distinct from `instance_unreachable` (network failure) and `instance_non_json` (200 but body is HTML, not JSON — e.g. an unrelated proxy or splash page).

**Step 5 — Write plan file.** (This document.)

---

## 4. Risks / Assumptions / Likely Regressions

| Item | Type | Detail |
|------|------|--------|
| `try_http_request` takes decomposed host, not URL | Constraint | `classifier.py` must add a `urlparse` adapter; no changes to verifier |
| SearXNG `/config` endpoint may not exist on all versions | Risk | If `/config` returns 404, preflight must degrade gracefully — test `/config` first, fall back to json search check only |
| Removing placeholder tab may break an existing test | Likely regression | `gui/tests/test_experimental_features_dialog.py` likely asserts tab labels; must be updated in C1 |
| `run_http_probe` writes to `http_probe_cache` (main DB) | Risk | Must NOT call `run_http_probe` directly from the classifier; use `try_http_request` + `validate_index_page` only to avoid polluting main DB |
| `experimental/se_dork/` package doesn't exist yet | Assumption | C1 creates `experimental/se_dork/__init__.py`; until then no imports work |
| `gui/utils/safe_messagebox` required for any dialogs | Convention | Any Toplevel dialogs in C5 must use `gui.utils.safe_messagebox`, enforced by `test_messagebox_guardrail.py` |
| `ensure_dialog_focus` required for modal dialogs | Convention | Browser window in C5 must call `gui.utils.dialog_helpers.ensure_dialog_focus()` as final step |

---

## 5. Exact Validation Commands

### C0 (this card)

```bash
# Seam 1: registry + placeholder wiring
rg -n "placeholder|experimental_features|registry" gui/components -g '*.py'

# Seam 2: HTTP verifier/probe integration points
rg -n "run_http_probe|try_http_request|validate_index_page|dispatch_probe_run" gui commands -g '*.py'

# Seam 3: SearXNG preflight — config endpoint
curl -sS -D - 'http://192.168.1.20:8090/config' -o /tmp/sx_config.json | head -n 20

# Seam 3: SearXNG preflight — json search endpoint
curl -sS -D - 'http://192.168.1.20:8090/search?q=hello&format=json' -o /tmp/sx.json | head -n 20
python3 - <<'PY'
import json
j = json.load(open('/tmp/sx.json'))
print('results_len=', len(j.get('results', [])))
print('first_url=', (j.get('results') or [{}])[0].get('url'))
PY
```

### C1 (frozen here for reference)

```bash
# Line counts (report rubric classification per file)
wc -l \
  gui/components/experimental_features/registry.py \
  gui/components/experimental_features/se_dork_tab.py \
  gui/tests/test_experimental_features_dialog.py

python3 -m py_compile \
  gui/components/experimental_features/registry.py \
  gui/components/experimental_features/se_dork_tab.py
./venv/bin/python -m pytest \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py -q
```

### C2

```bash
# Line counts
wc -l \
  experimental/se_dork/client.py \
  experimental/se_dork/models.py \
  gui/components/experimental_features/se_dork_tab.py \
  shared/tests/test_se_dork_client.py \
  gui/tests/test_se_dork_tab.py

python3 -m py_compile \
  experimental/se_dork/client.py \
  experimental/se_dork/models.py \
  gui/components/experimental_features/se_dork_tab.py
./venv/bin/python -m pytest \
  shared/tests/test_se_dork_client.py \
  gui/tests/test_se_dork_tab.py -q
```

### C3

```bash
# Line counts
wc -l \
  experimental/se_dork/service.py \
  experimental/se_dork/store.py \
  experimental/se_dork/models.py \
  shared/tests/test_se_dork_store.py \
  shared/tests/test_se_dork_service.py

python3 -m py_compile \
  experimental/se_dork/service.py \
  experimental/se_dork/store.py
./venv/bin/python -m pytest \
  shared/tests/test_se_dork_store.py \
  shared/tests/test_se_dork_service.py \
  gui/tests/test_se_dork_tab.py -q
```

### C4

```bash
# Line counts
wc -l \
  experimental/se_dork/classifier.py \
  experimental/se_dork/service.py \
  shared/tests/test_se_dork_classifier.py

python3 -m py_compile \
  experimental/se_dork/service.py \
  experimental/se_dork/classifier.py
./venv/bin/python -m pytest \
  shared/tests/test_se_dork_classifier.py \
  shared/tests/test_se_dork_service.py -q
```

### C5

```bash
# Line counts
wc -l \
  gui/components/se_dork_browser_window.py \
  gui/components/experimental_features/se_dork_tab.py \
  gui/tests/test_se_dork_browser_window.py

python3 -m py_compile \
  gui/components/se_dork_browser_window.py \
  gui/components/experimental_features/se_dork_tab.py
./venv/bin/python -m pytest \
  gui/tests/test_se_dork_browser_window.py \
  gui/tests/test_se_dork_tab.py -q
```

### C6 (full regression)

```bash
./venv/bin/python -m pytest \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py \
  gui/tests/test_se_dork_tab.py \
  gui/tests/test_se_dork_browser_window.py \
  shared/tests/test_se_dork_client.py \
  shared/tests/test_se_dork_service.py \
  shared/tests/test_se_dork_store.py -q
rg -n "SearXNG Dorking|SearXNG|format=json|403" README.md docs/TECHNICAL_REFERENCE.md
```

---

## 6. PASS/FAIL Acceptance Gates

### AUTOMATED

| Check | PASS condition |
|-------|---------------|
| `rg` seam-1 | `registry.py` source read shows `feature_id="placeholder"` literally present in `_get_features()` return list |
| `rg` seam-2 | Output includes `try_http_request` in `commands/http/verifier.py` and `run_http_probe` in `gui/utils/http_probe_runner.py` |
| `curl /config` | HTTP `200` in headers |
| `curl /search?format=json` | HTTP `200` + `Content-Type: application/json` in headers |
| `python3` JSON parse | `results_len= N` where N > 0 |

**AUTOMATED: PASS** when all five rows pass against live instance.

### MANUAL

| Check | PASS condition |
|-------|---------------|
| Touch list completeness | All files in section 2 are accounted for with no gaps across C1–C6 |
| Naming lock confirmed | `se_dork` is accepted as internal package name for v1 |

Note: `format=json` → 403 path is **not** a C0 manual gate. It is proven in C2 via a mocked HTTP 403 response in `shared/tests/test_se_dork_client.py`, plus a UI hint assertion in `gui/tests/test_se_dork_tab.py`. No live SearXNG reconfiguration needed at C0.

**MANUAL: PASS** when both rows are confirmed.

### Evidence that C0 is complete

1. All five automated checks return PASS outputs with actual output pasted (headers block + `top_keys`, `results_len > 0`, `first_url`).
2. Source reads of `registry.py` lines 1–60 and `verifier.py` signature lines confirm structure assumptions match exploration results.
3. `instance_format_forbidden` 403 failure mode is fully documented with cause and UI hint text (Section 3, Step 4); proof deferred to C2 mocked tests.
4. Classifier URL-parse adapter constraint is locked (`urlparse` → decomposed host before calling `try_http_request`).
5. `run_http_probe` / main-DB pollution risk is documented; mitigation locked (verifier primitives only in classifier path).
6. Naming lock decision is recorded in Section 1a.
7. C1 placeholder deletion sequence is locked (registry-first, grep-gate, then delete).
8. This plan file has been reviewed by HI.

**OVERALL: PASS** when automated gates pass, manual rows confirmed, and evidence list is complete.
