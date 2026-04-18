# C5 Gap Audit — SearXNG Dork Module: Results Browser + Promotion Hooks

## Context

Card C5 (Results Browser + Promotion Hooks) was reportedly already implemented as part of C4
delivery. This plan performs the required gap audit and reports findings per the TASK_CARDS
report format. The task is to apply only missing deltas — no gratuitous changes.

---

## Gap Audit Findings

### Validation commands run

**Compile check:**
```
python3 -m py_compile \
  gui/components/se_dork_browser_window.py \
  gui/components/experimental_features/se_dork_tab.py \
  gui/components/dashboard_experimental.py
→ COMPILE OK
```

**Test suite:**
```
./venv/bin/python -m pytest \
  gui/tests/test_se_dork_browser_window.py \
  gui/tests/test_se_dork_tab.py \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py \
  -q
→ 75 passed in 0.20s
```

### Acceptance check audit (line-by-line)

| Check | Requirement | Location | Status |
|---|---|---|---|
| 1 | Browser opens from SearXNG Dorking tab | `se_dork_tab.py::_open_results_browser` L256-266; tested in `test_experimental_features_dialog.py` L484-511 | ✓ |
| 2 | Copy URL action | `se_dork_browser_window.py::_on_copy_url` L219-229 | ✓ |
| 2 | Open URL action | `se_dork_browser_window.py::_on_open_system_browser` L231-241 | ✓ |
| 2 | Add to dirracuda DB action | `se_dork_browser_window.py::_on_add_to_db` L314-355 | ✓ |
| 3 | Prefill `host_type="H"` | `_build_prefill` L273; tested `test_se_dork_browser_window.py` L68 | ✓ |
| 3 | Prefill `_promotion_source="se_dork_browser"` | `_build_prefill` L281; tested L73, L178 | ✓ |
| 4 | "Not available" message when callback=None | `_on_add_to_db` L317-323; tested L126-136 | ✓ |
| 4 | No silent no-op | Explicit `messagebox.showinfo("Not available", ...)` confirmed | ✓ |
| 5 | No automatic promotion | Callback only fired by explicit user menu action | ✓ |
| 5 | No Reddit regression | `test_dashboard_reddit_wiring.py` still passes (part of 75-test run) | ✓ |

### Promotion chain audit

```
handle_experimental_button_click (dashboard_experimental.py L26-36)
  └─ context["open_se_dork_results_db"] = lambda: open_se_dork_results_db(widget)  L33
       └─ open_se_dork_results_db (L65-82)
            └─ _resolve_server_window → show_se_dork_browser_window(
                   parent=server_window.window,
                   add_record_callback=server_window.open_add_record_dialog  ← manual only
               )
               OR fallback: add_record_callback=None → "Not available" in browser
```

All three path cases (live window, fallback, dead/destroyed window) are tested in
`test_experimental_features_dialog.py` L399-476.

### Line count rubric

| File | Lines | Rating |
|---|---|---|
| `gui/components/se_dork_browser_window.py` | 364 | ≤1200 excellent |
| `gui/components/experimental_features/se_dork_tab.py` | 283 | ≤1200 excellent |
| `gui/components/dashboard_experimental.py` | 119 | ≤1200 excellent |
| `gui/tests/test_se_dork_browser_window.py` | 204 | ≤1200 excellent |
| `gui/tests/test_se_dork_tab.py` | 402 | ≤1200 excellent |
| `gui/tests/test_experimental_features_dialog.py` | 511 | ≤1200 excellent |

---

## Report

- **Issue:** Gap audit finds no missing deltas. All C5 requirements are fully implemented.
- **Root cause:** C5 was already completed during C4 delivery.
- **Fix:** None required.
- **Files changed:** None.
- **Validation run:** `py_compile` → COMPILE OK; pytest → 75 passed.
- **Result:** All acceptance checks satisfied. Promotion chain intact. No regression.
- **HI test needed?** Yes — browser UI actions cannot be exercised headlessly.
  - Steps:
    1. Open the Experimental dialog → SearXNG Dorking tab.
    2. Click "Open Results DB" — confirm browser window opens.
    3. Right-click a row → "Copy URL" — confirm clipboard contents.
    4. Right-click a row → "Open in system browser" — confirm browser launch.
    5. Right-click a row → "Add to dirracuda DB" — if Servers window is open,
       confirm Add Record dialog prefills with the URL's host/port/scheme.
    6. Close Servers window; repeat step 5 — confirm "Not available" message appears.

---

## Line count rubric before/after per touched file

No files were changed. Before = After for all files (listed above).

---

## Completion

```
AUTOMATED: PASS
MANUAL:    PENDING  (HI test steps above)
OVERALL:   PENDING
```
